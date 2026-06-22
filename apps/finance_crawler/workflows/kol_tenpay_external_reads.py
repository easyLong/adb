"""Backfill KOL Tenpay read counts from external Tencent Docs sheets."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.storage.db import get_conn as get_crawler_app_conn
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.integrations.tencent_docs.write_requests import row_cells_request
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("kol_tenpay_external_reads")

TENPAY_PLATFORM = "理财通"

DEFAULT_SOURCE_URLS = (
    "https://docs.qq.com/sheet/DYkFjS0x5ZnN3REZt?tab=t94mxd&nlc=1",
    "https://docs.qq.com/sheet/DYktyUWhvcXBkQ2Vk?tab=xgedy6&nlc=1",
    "https://docs.qq.com/sheet/DYmZHV2RFTm1RYW5a?tab=eewy5i&nlc=1",
    "https://docs.qq.com/sheet/DYkpCeXZ1WHJwR0N4?tab=bmc2o7&nlc=1",
    "https://docs.qq.com/sheet/DYmFzempwUWJad2xa?tab=j2p51n&nlc=1",
    "https://docs.qq.com/sheet/DYmhYUE1WTUdhQmxF?tab=04jlfd&nlc=1",
    "https://docs.qq.com/sheet/DYmtBdmNiTnpHaWFJ?tab=x52om7&nlc=1",
)
DEFAULT_TARGET_URL = "https://docs.qq.com/sheet/DYnhxS2VHZHBqR0V5?tab=wpvy0d"

SOURCE_HEADER_ALIASES = {
    "date": ("日期",),
    "account_name": ("账号名称", "账号名", "账户名称", "大V名称"),
    "read_count": ("T-1日文章阅读数", "T-1日文章阅读数（填单篇最高的即可）", "阅读数"),
}
TARGET_HEADER_ALIASES = {
    "date": ("日期",),
    "kol_name": ("大V名称", "账号名称", "账号名"),
    "platform": ("平台",),
    "read_count": ("阅读数",),
}
SOURCE_FALLBACKS = {"date": 0, "account_name": 1, "read_count": 4}
TARGET_FALLBACKS = {
    "date": 0,
    "kol_name": 1,
    "platform": 2,
    "read_count": 8,
}


@dataclass(frozen=True, slots=True)
class SourceRead:
    read_count: int
    source_url: str
    source_row_index: int
    source_name: str


def run_kol_tenpay_external_reads(
    target_date: date | None = None,
    *,
    source_urls: list[str] | None = None,
    target_doc_url: str | None = None,
) -> dict[str, Any]:
    """Copy Tenpay T-1 article read counts into the KOL daily target sheet.

    Matching is intentionally strict on the target side:
    date + KOL name + platform == 理财通.
    """

    resolved_source_urls = source_urls or _configured_source_urls()
    resolved_target_url = target_doc_url or Config.KOL_TENPAY_EXTERNAL_READS_TARGET_DOC_URL or DEFAULT_TARGET_URL
    if not resolved_source_urls:
        raise ValueError("no KOL Tenpay external read source docs configured")

    source_reads, source_duplicates, source_summary, source_errors = _read_sources(resolved_source_urls, target_date)
    updates, skipped_conflicts, target_summary = _build_updates(
        resolved_target_url,
        source_reads,
        source_duplicates,
        target_date,
    )

    target_doc = client.parse_doc_url(resolved_target_url)
    metrics_upserted = _upsert_daily_metrics(resolved_target_url, target_doc.sheet_id, updates)
    requests = [
        row_cells_request(
            item["row_index"],
            item["read_col"],
            [str(item["new_read_count"])],
            text_format={"fontSize": Config.KOL_TENPAY_EXTERNAL_READS_WRITEBACK_FONT_SIZE},
            doc=target_doc,
        )
        for item in updates
    ]
    if requests:
        client.post_batch_update(requests, "kol_tenpay_external_reads", doc=target_doc)
    writeback_failures = _verify_writeback(resolved_target_url, updates)
    retry_rows = 0
    if writeback_failures:
        retry_rows = _retry_plain_value_writebacks(target_doc, updates, writeback_failures)
        writeback_failures = _verify_writeback(resolved_target_url, updates)
    metric_status_updates = _mark_metric_writebacks(updates, writeback_failures)

    summary = {
        "target_date": target_date.isoformat() if target_date else "all",
        "source_docs": len(resolved_source_urls),
        "source_rows": source_summary["rows"],
        "source_nonempty_reads": source_summary["nonempty_reads"],
        "source_errors": len(source_errors),
        "unique_source_reads": len(source_reads),
        "target_rows": target_summary["rows"],
        "matched_updates": len(updates),
        "skipped_conflicts": len(skipped_conflicts),
        "missing_source": target_summary["missing_source"],
        "metrics_upserted": metrics_upserted,
        "written_rows": len(requests),
        "retry_plain_value_rows": retry_rows,
        "metric_status_updates": metric_status_updates,
        "verified_rows": len(updates) - len(writeback_failures),
        "writeback_failed_rows": len(writeback_failures),
        "updated_by_date": dict(sorted(_count_by(updates, "date").items())),
    }
    if source_errors:
        summary["source_error_details"] = source_errors[:20]
    if skipped_conflicts:
        summary["conflicts"] = skipped_conflicts[:20]
    if writeback_failures:
        summary["writeback_failures"] = writeback_failures[:20]

    logger.info("KOL Tenpay external reads summary: %s", summary)
    if writeback_failures:
        raise RuntimeError(
            "KOL Tenpay external reads writeback verification failed: "
            f"{len(writeback_failures)} rows did not land; sample={writeback_failures[:5]}"
        )
    return summary


def run_kol_tenpay_external_reads_lookback(
    *,
    end_date: date | None = None,
    days: int | None = None,
    source_urls: list[str] | None = None,
    target_doc_url: str | None = None,
) -> dict[str, Any]:
    """Run Tenpay external read backfill for T-1 through T-N dates."""

    resolved_end = end_date or date.today() - timedelta(days=1)
    resolved_days = max(int(days if days is not None else Config.KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS), 1)
    target_dates = [resolved_end - timedelta(days=offset) for offset in range(resolved_days)]
    summaries: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for item in target_dates:
        try:
            summaries.append(
                run_kol_tenpay_external_reads(
                    target_date=item,
                    source_urls=source_urls,
                    target_doc_url=target_doc_url,
                )
            )
        except Exception as exc:
            failed.append({"date": item.isoformat(), "error": str(exc)})
            logger.exception("KOL Tenpay external reads failed for date=%s", item.isoformat())

    summary = {
        "mode": "lookback",
        "days": resolved_days,
        "dates": [item.isoformat() for item in target_dates],
        "succeeded": len(summaries),
        "failed": len(failed),
        "matched_updates": sum(int(item.get("matched_updates") or 0) for item in summaries),
        "metrics_upserted": sum(int(item.get("metrics_upserted") or 0) for item in summaries),
        "written_rows": sum(int(item.get("written_rows") or 0) for item in summaries),
        "verified_rows": sum(int(item.get("verified_rows") or 0) for item in summaries),
        "writeback_failed_rows": sum(int(item.get("writeback_failed_rows") or 0) for item in summaries),
        "by_date": summaries,
        "failures": failed,
    }
    if failed:
        raise RuntimeError(f"KOL Tenpay external reads lookback failed for {len(failed)} date(s): {failed[:5]}")
    logger.info("KOL Tenpay external reads lookback summary: %s", summary)
    return summary


def run_kol_tenpay_external_reads_db_only(
    target_date: date,
    *,
    source_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Update kol_daily_metrics.read_count from external Tenpay docs without Tencent Docs writeback."""

    resolved_source_urls = source_urls or _configured_source_urls()
    if not resolved_source_urls:
        raise ValueError("no KOL Tenpay external read source docs configured")

    source_reads, source_duplicates, source_summary, source_errors = _read_sources(resolved_source_urls, target_date)
    updates, skipped_conflicts, target_summary = _build_db_updates(source_reads, source_duplicates, target_date)
    metrics_upserted = _upsert_daily_metrics("", "", updates)
    summary = {
        "target_date": target_date.isoformat(),
        "mode": "database",
        "source_docs": len(resolved_source_urls),
        "source_rows": source_summary["rows"],
        "source_nonempty_reads": source_summary["nonempty_reads"],
        "source_errors": len(source_errors),
        "unique_source_reads": len(source_reads),
        "target_rows": target_summary["rows"],
        "matched_updates": len(updates),
        "skipped_conflicts": len(skipped_conflicts),
        "missing_source": target_summary["missing_source"],
        "metrics_upserted": metrics_upserted,
        "updated_by_date": dict(sorted(_count_by(updates, "date").items())),
    }
    if source_errors:
        summary["source_error_details"] = source_errors[:20]
    if skipped_conflicts:
        summary["conflicts"] = skipped_conflicts[:20]
    logger.info("KOL Tenpay external reads DB summary: %s", summary)
    return summary


def run_kol_tenpay_external_reads_db_lookback(
    *,
    end_date: date | None = None,
    days: int | None = None,
    source_urls: list[str] | None = None,
) -> dict[str, Any]:
    resolved_end = end_date or date.today() - timedelta(days=1)
    resolved_days = max(int(days if days is not None else Config.KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS), 1)
    target_dates = [resolved_end - timedelta(days=offset) for offset in range(resolved_days)]
    summaries: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for item in target_dates:
        try:
            summaries.append(run_kol_tenpay_external_reads_db_only(item, source_urls=source_urls))
        except Exception as exc:
            failed.append({"date": item.isoformat(), "error": str(exc)})
            logger.exception("KOL Tenpay external reads DB update failed for date=%s", item.isoformat())
    summary = {
        "mode": "database_lookback",
        "days": resolved_days,
        "dates": [item.isoformat() for item in target_dates],
        "succeeded": len(summaries),
        "failed": len(failed),
        "matched_updates": sum(int(item.get("matched_updates") or 0) for item in summaries),
        "metrics_upserted": sum(int(item.get("metrics_upserted") or 0) for item in summaries),
        "by_date": summaries,
        "failures": failed,
    }
    if failed:
        raise RuntimeError(f"KOL Tenpay external reads DB lookback failed for {len(failed)} date(s): {failed[:5]}")
    logger.info("KOL Tenpay external reads DB lookback summary: %s", summary)
    return summary


def _upsert_daily_metrics(target_doc_url: str, target_sheet_id: str, updates: list[dict[str, Any]]) -> int:
    if not updates:
        return 0

    conn = get_crawler_app_conn()
    try:
        count = 0
        for item in updates:
            repository.upsert_kol_daily_metric(
                conn,
                metric_date=date.fromisoformat(str(item["date"])),
                kol_name=str(item["kol_name"]),
                platform=str(item["platform"]),
                read_count=int(item["new_read_count"]),
                read_source="external_tenpay",
                source_doc_url=str(item.get("source_url") or ""),
                source_row_index=int(item["source_row_index"]) if item.get("source_row_index") else None,
                source_payload={
                    "source_name": item.get("source_name"),
                    "old_read_count": item.get("old_read_count"),
                    "target_row_index": item.get("row_index"),
                    "mode": item.get("mode") or "tencent_docs_writeback",
                },
                target_doc_url=target_doc_url,
                target_sheet_id=target_sheet_id,
                target_row_index=int(item["row_index"]) if item.get("row_index") else None,
                writeback_status=str(item.get("writeback_status") or "pending"),
            )
            count += 1
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _mark_metric_writebacks(updates: list[dict[str, Any]], failures: list[dict[str, Any]]) -> int:
    if not updates:
        return 0

    failures_by_row = {int(item["row_index"]): item for item in failures}
    conn = get_crawler_app_conn()
    try:
        count = 0
        for item in updates:
            failure = failures_by_row.get(int(item["row_index"]))
            if failure:
                status = "error"
                error = f"expected={failure.get('expected')} actual={failure.get('actual')}"
            else:
                status = "success"
                error = None
            count += repository.mark_kol_daily_metric_writeback(
                conn,
                metric_date=date.fromisoformat(str(item["date"])),
                kol_name=str(item["kol_name"]),
                platform=str(item["platform"]),
                status=status,
                error=error,
            )
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _retry_plain_value_writebacks(
    target_doc: client.DocInfo,
    updates: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> int:
    """Retry failed cells without formatting.

    Tencent Docs can occasionally accept a formatted write request while the
    value does not land. Retrying the same cell as a plain value has proven more
    reliable for affected read-count cells.
    """

    updates_by_row = {int(item["row_index"]): item for item in updates}
    retry_count = 0
    for failure in failures:
        item = updates_by_row.get(int(failure["row_index"]))
        if item is None:
            continue
        request = row_cells_request(
            int(item["row_index"]),
            int(item["read_col"]),
            [str(item["new_read_count"])],
            doc=target_doc,
        )
        client.post_batch_update(
            [request],
            f"kol_tenpay_external_reads_plain_retry_{item['row_index']}",
            doc=target_doc,
        )
        retry_count += 1
        time.sleep(1)
    return retry_count


def _configured_source_urls() -> list[str]:
    raw = Config.KOL_TENPAY_EXTERNAL_READS_SOURCE_DOC_URLS.strip()
    if raw:
        return [item.strip() for item in re.split(r"[\n,;]+", raw) if item.strip()]
    return list(DEFAULT_SOURCE_URLS)


def _read_sources(
    source_urls: list[str],
    target_date: date | None,
) -> tuple[
    dict[tuple[str, str], SourceRead],
    dict[tuple[str, str], list[SourceRead]],
    dict[str, int],
    list[dict[str, str]],
]:
    source_reads: dict[tuple[str, str], SourceRead] = {}
    duplicates: dict[tuple[str, str], list[SourceRead]] = defaultdict(list)
    summary = {"rows": 0, "nonempty_reads": 0}
    errors: list[dict[str, str]] = []

    for url in source_urls:
        doc = client.parse_doc_url(url)
        try:
            rows, start_row = client.fetch_grid(Config.KOL_TENPAY_EXTERNAL_READS_SOURCE_RANGE, doc=doc)
        except Exception as exc:
            logger.warning("failed to read KOL Tenpay source doc: %s; error=%s", url, exc)
            errors.append({"source_url": url, "error": str(exc)})
            continue
        columns = _resolve_columns(_first_row(rows), SOURCE_HEADER_ALIASES, SOURCE_FALLBACKS)

        for offset, row in enumerate(rows[1:], start=1):
            row_index = start_row + offset + 1
            source_date = _parse_date(_cell(row, columns["date"]))
            if source_date is None or (target_date and source_date != target_date):
                continue
            account_name = _normalize_name(_cell(row, columns["account_name"]))
            if not account_name:
                continue
            summary["rows"] += 1
            read_count = _parse_count(_cell(row, columns["read_count"]))
            if read_count is None:
                continue
            summary["nonempty_reads"] += 1

            item = SourceRead(
                read_count=read_count,
                source_url=url,
                source_row_index=row_index,
                source_name=str(_cell(row, columns["account_name"])).strip(),
            )
            key = (source_date.isoformat(), account_name)
            duplicates[key].append(item)
            current = source_reads.get(key)
            if current is None or item.read_count > current.read_count:
                source_reads[key] = item

    return source_reads, duplicates, summary, errors


def _build_updates(
    target_doc_url: str,
    source_reads: dict[tuple[str, str], SourceRead],
    source_duplicates: dict[tuple[str, str], list[SourceRead]],
    target_date: date | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    doc = client.parse_doc_url(target_doc_url)
    rows, start_row = client.fetch_grid(Config.KOL_TENPAY_EXTERNAL_READS_TARGET_RANGE, doc=doc)
    columns = _resolve_columns(_first_row(rows), TARGET_HEADER_ALIASES, TARGET_FALLBACKS)
    target_platform = Config.KOL_TENPAY_EXTERNAL_READS_TARGET_PLATFORM or TENPAY_PLATFORM
    updates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    summary = {"rows": 0, "missing_source": 0}

    for offset, row in enumerate(rows[1:], start=1):
        row_index = start_row + offset + 1
        row_date = _parse_date(_cell(row, columns["date"]))
        if row_date is None or (target_date and row_date != target_date):
            continue
        platform = str(_cell(row, columns["platform"])).strip()
        if platform != target_platform:
            continue
        kol_name = _normalize_name(_cell(row, columns["kol_name"]))
        if not kol_name:
            continue

        summary["rows"] += 1
        key = (row_date.isoformat(), kol_name)
        source = source_reads.get(key)
        if source is None:
            summary["missing_source"] += 1
            continue

        values = sorted({item.read_count for item in source_duplicates.get(key, [])})
        if len(values) > 1:
            conflicts.append({"row_index": row_index, "key": key, "values": values})
            continue

        updates.append(
            {
                "row_index": row_index,
                "date": row_date.isoformat(),
                "kol_name": str(_cell(row, columns["kol_name"])).strip(),
                "platform": platform,
                "old_read_count": _cell(row, columns["read_count"]),
                "new_read_count": source.read_count,
                "read_col": columns["read_count"],
                "source_row_index": source.source_row_index,
                "source_url": source.source_url,
                "source_name": source.source_name,
            }
        )

    return updates, conflicts, summary


def _build_db_updates(
    source_reads: dict[tuple[str, str], SourceRead],
    source_duplicates: dict[tuple[str, str], list[SourceRead]],
    target_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    target_platform = Config.KOL_TENPAY_EXTERNAL_READS_TARGET_PLATFORM or TENPAY_PLATFORM
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT metric_date, kol_name, platform, read_count
                FROM kol_daily_metrics
                WHERE metric_date = %s
                  AND platform = %s
                ORDER BY id ASC
                """,
                (target_date, target_platform),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    updates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    summary = {"rows": 0, "missing_source": 0}
    for row in rows:
        row_date = row["metric_date"]
        kol_name = _normalize_name(row.get("kol_name"))
        if not kol_name:
            continue
        summary["rows"] += 1
        key = (row_date.isoformat(), kol_name)
        source = source_reads.get(key)
        if source is None:
            summary["missing_source"] += 1
            continue
        values = sorted({item.read_count for item in source_duplicates.get(key, [])})
        if len(values) > 1:
            conflicts.append({"key": key, "values": values})
            continue
        updates.append(
            {
                "row_index": 0,
                "date": row_date.isoformat(),
                "kol_name": str(row.get("kol_name") or "").strip(),
                "platform": str(row.get("platform") or "").strip(),
                "old_read_count": "" if row.get("read_count") is None else row.get("read_count"),
                "new_read_count": source.read_count,
                "read_col": 0,
                "source_row_index": source.source_row_index,
                "source_url": source.source_url,
                "source_name": source.source_name,
                "mode": "database",
                "writeback_status": "synced_to_db",
            }
        )
    return updates, conflicts, summary


def _verify_writeback(target_doc_url: str, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not updates:
        return []

    doc = client.parse_doc_url(target_doc_url)
    rows, start_row = client.fetch_grid(Config.KOL_TENPAY_EXTERNAL_READS_TARGET_RANGE, doc=doc)
    failures: list[dict[str, Any]] = []
    for item in updates:
        row_index = int(item["row_index"])
        read_col = int(item["read_col"])
        row_offset = row_index - start_row - 1
        actual = _cell(rows[row_offset], read_col) if 0 <= row_offset < len(rows) else ""
        expected = str(item["new_read_count"])
        if str(actual).strip() != expected:
            failures.append(
                {
                    "row_index": row_index,
                    "date": item["date"],
                    "kol_name": item["kol_name"],
                    "expected": expected,
                    "actual": actual,
                }
            )
    if failures:
        logger.warning("KOL Tenpay external reads writeback verification failed: %s", failures[:20])
    return failures


def _resolve_columns(
    header: list[str],
    aliases: dict[str, tuple[str, ...]],
    fallbacks: dict[str, int],
) -> dict[str, int]:
    normalized_header = [_normalize_title(item) for item in header]
    resolved: dict[str, int] = {}
    for field_name, titles in aliases.items():
        for title in titles:
            normalized_title = _normalize_title(title)
            for index, header_title in enumerate(normalized_header):
                if normalized_title and normalized_title in header_title:
                    resolved[field_name] = index
                    break
            if field_name in resolved:
                break
        if field_name not in resolved:
            resolved[field_name] = fallbacks[field_name]
    return resolved


def _first_row(rows: list[list[str]]) -> list[str]:
    return rows[0] if rows else []


def _cell(row: list[str], index: int) -> str:
    return row[index] if isinstance(row, list) and len(row) > index else ""


def _normalize_title(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _normalize_name(value: object) -> str:
    return _normalize_title(value)


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def _parse_count(value: object) -> int | None:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = Decimal(match.group(0))
    if "万" in text:
        number *= Decimal(10000)
    return int(number)


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[str(item[key])] += 1
    return counts
