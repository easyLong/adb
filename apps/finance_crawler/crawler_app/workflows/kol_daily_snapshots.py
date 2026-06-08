"""Daily KOL snapshot import from Tencent Docs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.documents.column_resolver import normalize_title
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.storage.db import get_conn
from apps.finance_crawler.crawler_app.storage.profile_metrics import (
    get_profile_action_profile,
    mark_profile_writeback,
    profile_key_for_url,
    upsert_profile_source,
)
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.integrations.tencent_docs.write_requests import row_cells_request
from apps.finance_crawler.utils.link_source import detect_link_source
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.workflows.profile_metrics import crawl_pending_profile_metrics
from apps.finance_crawler.workflows.profile_post_reads import crawl_profile_post_reads

logger = get_logger("kol_daily_snapshots")

OTHER_TYPE = "\u5176\u5b83"
KOL_DAILY_CRAWL_SOURCE_NAME = "kol_daily_crawl"
KOL_DAILY_CRAWL_FIELDS = ("fans_count", "growth_count")
KOL_DAILY_WRITEBACK_RANGE = "A1:I5000"
KOL_DAILY_COL_DATE = 0
KOL_DAILY_COL_ACCOUNT = 1
KOL_DAILY_COL_PLATFORM = 2
KOL_DAILY_COL_HOMEPAGE = 3
KOL_DAILY_COL_FANS = 6
KOL_DAILY_COL_GROWTH = 7
KOL_DAILY_COL_READ = 8
KOL_DAILY_WRITEBACK_HEADER = [
    "\u65e5\u671f",
    "\u5927V\u540d\u79f0",
    "\u5e73\u53f0",
    "\u4e3b\u9875\u94fe\u63a5",
    "\u7b2c\u51e0\u7fa4",
    "\u7c7b\u578b",
    "\u7c89\u4e1d\u6570",
    "\u589e\u7c89\u6570",
    "\u9605\u8bfb\u6570",
]


@dataclass(frozen=True, slots=True)
class KolSnapshotRow:
    snapshot_date: date
    kol_name: str
    platform: str
    homepage_url: str
    group_name: str
    fans_count: int | None
    growth_count: int | None
    read_count: int | None


@dataclass(frozen=True, slots=True)
class KolCrawlSourceRow:
    metric_date: date
    account_name: str
    platform: str
    homepage_url: str
    existing_fans_count: int | None
    source_locator: dict[str, Any]


KOL_DAILY_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "snapshot_date": ("\u65e5\u671f", "date"),
    "kol_name": ("\u5927V\u540d\u79f0", "\u5927v\u540d\u79f0", "kol", "name"),
    "platform": ("\u5e73\u53f0", "platform"),
    "homepage_url": ("\u4e3b\u9875\u94fe\u63a5", "\u9996\u9875\u94fe\u63a5", "\u94fe\u63a5", "url"),
    "fans_count": ("\u7c89\u4e1d\u6570", "\u7c89\u4e1d\u91cf", "fans_count"),
    "growth_count": ("\u589e\u7c89\u6570", "\u589e\u7c89\u91cf", "growth_count"),
    "read_count": ("\u9605\u8bfb\u6570", "\u9605\u8bfb\u91cf", "read_count"),
    "group_name": ("\u7b2c\u51e0\u7fa4", "\u7fa4", "group"),
}
REQUIRED_FIELDS = ("snapshot_date", "kol_name", "platform")


def sync_kol_daily_snapshots_from_tencent_docs(
    *,
    snapshot_date: date | None = None,
    doc_url: str | None = None,
    range_a1: str | None = None,
) -> dict[str, Any]:
    target_date = snapshot_date or date.today()
    resolved_doc_url = (doc_url or Config.KOL_DAILY_SNAPSHOT_DOC_URL or "").strip()
    if not resolved_doc_url:
        raise ValueError("KOL_DAILY_SNAPSHOT_DOC_URL is not configured")

    doc = client.parse_doc_url(resolved_doc_url)
    rows, start_row = client.fetch_grid(range_a1 or Config.KOL_DAILY_SNAPSHOT_READ_RANGE, doc=doc)
    if not rows:
        return _summary(
            target_date=target_date,
            doc=doc,
            sheet_title=client.fetch_sheet_title(doc),
            source_rows=0,
            imported=0,
            skipped=0,
            problems=["empty sheet"],
        )

    mapping = resolve_kol_daily_header(rows[0])
    problems = list(mapping.get("problems") or [])
    if problems:
        return _summary(
            target_date=target_date,
            doc=doc,
            sheet_title=client.fetch_sheet_title(doc),
            source_rows=max(len(rows) - 1, 0),
            imported=0,
            skipped=max(len(rows) - 1, 0),
            problems=problems,
        )

    parsed_rows: list[KolSnapshotRow] = []
    skipped = 0
    for row in rows[1:]:
        parsed = parse_kol_daily_row(row, mapping["columns"])
        if not parsed or parsed.snapshot_date != target_date:
            skipped += 1
            continue
        parsed_rows.append(parsed)

    imported = 0
    conn = get_conn()
    try:
        for item in parsed_rows:
            existing = repository.get_kol_base_profile(conn, kol_name=item.kol_name, platform=item.platform)
            kol_type = str((existing or {}).get("kol_type") or OTHER_TYPE)
            profile_id = repository.upsert_kol_base_profile(
                conn,
                kol_name=item.kol_name,
                platform=item.platform,
                homepage_url=item.homepage_url,
                group_name=item.group_name,
                kol_type=kol_type,
            )
            repository.upsert_kol_daily_snapshot(
                conn,
                kol_profile_id=profile_id,
                snapshot_date=item.snapshot_date,
                kol_name=item.kol_name,
                platform=item.platform,
                homepage_url=item.homepage_url,
                group_name=item.group_name,
                kol_type=kol_type,
                fans_count=item.fans_count,
                growth_count=item.growth_count,
                read_count=item.read_count,
            )
            imported += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    summary = _summary(
        target_date=target_date,
        doc=doc,
        sheet_title=client.fetch_sheet_title(doc),
        source_rows=max(len(rows) - 1, 0),
        imported=imported,
        skipped=skipped,
        problems=[],
    )
    logger.info("KOL daily snapshots synced: %s", summary)
    return summary


def run_kol_daily_snapshot_pipeline(
    *,
    snapshot_date: date | None = None,
    source_doc_url: str | None = None,
    writeback_doc_url: str | None = None,
) -> dict[str, Any]:
    target_date = snapshot_date or date.today()
    sync_summary = sync_kol_daily_snapshots_from_tencent_docs(
        snapshot_date=target_date,
        doc_url=source_doc_url,
    )
    ensured = ensure_kol_daily_snapshots_from_base_profiles(snapshot_date=target_date)
    writeback_summary = None
    resolved_writeback_url = (writeback_doc_url or Config.KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL or "").strip()
    if resolved_writeback_url:
        writeback_summary = writeback_kol_daily_snapshots_to_tencent_docs(
            snapshot_date=target_date,
            doc_url=resolved_writeback_url,
        )
    return {
        "date": target_date.isoformat(),
        "sync": sync_summary,
        "ensured_from_base": ensured,
        "writeback": writeback_summary,
    }


def run_kol_daily_crawl_pipeline(
    *,
    target_date: date | None = None,
    doc_url: str | None = None,
    limit: int | None = None,
    source_name: str | None = None,
    requested_fields: tuple[str, ...] | list[str] | None = None,
    action_profile_key: str | None = None,
    trigger_config_id: int | None = None,
    trigger_run_id: int | None = None,
) -> dict[str, Any]:
    resolved_date = target_date or date.today()
    resolved_source_name = source_name or KOL_DAILY_CRAWL_SOURCE_NAME
    resolved_fields = tuple(requested_fields or KOL_DAILY_CRAWL_FIELDS)
    sync_summary = sync_kol_crawl_sources_from_writeback_doc(
        target_date=resolved_date,
        doc_url=doc_url,
        source_name=resolved_source_name,
        requested_fields=resolved_fields,
        action_profile_key=action_profile_key,
        trigger_config_id=trigger_config_id,
        trigger_run_id=trigger_run_id,
    )
    resolved_limit = limit if limit is not None else Config.KOL_DAILY_CRAWL_LIMIT
    fans_results = crawl_pending_profile_metrics(
        limit=resolved_limit or None,
        target_date=resolved_date,
        source_name=resolved_source_name,
    )
    if "read_count" in resolved_fields:
        read_results = crawl_profile_post_reads(
            limit=resolved_limit or None,
            target_date=resolved_date,
            source_name=resolved_source_name,
        )
    else:
        read_results = []
    writeback_summary = writeback_kol_daily_crawl_results_to_tencent_docs(
        target_date=resolved_date,
        doc_url=doc_url,
        source_name=resolved_source_name,
    )
    return {
        "date": resolved_date.isoformat(),
        "source_name": resolved_source_name,
        "action_profile_key": action_profile_key,
        "trigger_config_id": trigger_config_id,
        "trigger_run_id": trigger_run_id,
        "sync": sync_summary,
        "fans_crawled": len(fans_results),
        "read_crawled": len(read_results),
        "writeback": writeback_summary,
    }


def sync_kol_crawl_sources_from_writeback_doc(
    *,
    target_date: date | None = None,
    doc_url: str | None = None,
    source_name: str | None = None,
    requested_fields: tuple[str, ...] | list[str] | None = None,
    action_profile_key: str | None = None,
    trigger_config_id: int | None = None,
    trigger_run_id: int | None = None,
) -> dict[str, Any]:
    resolved_date = target_date or date.today()
    resolved_source_name = source_name or KOL_DAILY_CRAWL_SOURCE_NAME
    resolved_fields = tuple(requested_fields or KOL_DAILY_CRAWL_FIELDS)
    resolved_doc_url = (doc_url or Config.KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL or "").strip()
    if not resolved_doc_url:
        raise ValueError("KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL is not configured")

    doc = client.parse_doc_url(resolved_doc_url)
    rows, start_row = client.fetch_grid(KOL_DAILY_WRITEBACK_RANGE, doc=doc)
    imported = 0
    skipped = 0
    for offset, row in enumerate(rows[1:], start=1):
        sheet_row_index = start_row + offset + 1
        parsed = parse_kol_crawl_source_row(row, sheet_row_index=sheet_row_index, doc=doc)
        if not parsed or parsed.metric_date != resolved_date:
            skipped += 1
            continue
        app_type = detect_link_source(parsed.homepage_url)
        row_action_profile_key = action_profile_key or _resolve_profile_action_profile_key(
            app_type=app_type,
            requested_fields=resolved_fields,
        )
        source_locator = dict(parsed.source_locator)
        source_locator.update(
            {
                "source_name": resolved_source_name,
                "requested_fields": list(resolved_fields),
                "action_profile_key": row_action_profile_key,
                "trigger_config_id": trigger_config_id,
                "trigger_run_id": trigger_run_id,
            }
        )
        profile_key = profile_key_for_url(parsed.homepage_url)
        upsert_profile_source(
            {
                "profile_key": profile_key,
                "account_name": parsed.account_name,
                "platform": parsed.platform,
                "app_type": app_type,
                "homepage_url": parsed.homepage_url,
                "metric_date": parsed.metric_date,
                "source_type": "tencent_docs",
                "source_name": resolved_source_name,
                "source_key": profile_key_for_url(
                    "%s:%s:%s:%s:%s"
                    % (
                        resolved_source_name,
                        doc.file_id,
                        doc.sheet_id,
                        parsed.metric_date.isoformat(),
                        parsed.homepage_url,
                    )
                ),
                "source_locator": source_locator,
                "requested_fields": list(resolved_fields),
                "source": {
                    "doc_url": resolved_doc_url,
                    "workflow": resolved_source_name,
                    "action_profile_key": row_action_profile_key,
                    "trigger_config_id": trigger_config_id,
                    "trigger_run_id": trigger_run_id,
                },
                "existing_fans_count": parsed.existing_fans_count,
            }
        )
        imported += 1

    summary = {
        "date": resolved_date.isoformat(),
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "sheet_title": client.fetch_sheet_title(doc),
        "source_rows": max(len(rows) - 1, 0),
        "imported": imported,
        "skipped": skipped,
        "source_name": resolved_source_name,
        "action_profile_key": action_profile_key or "auto_by_app",
        "trigger_config_id": trigger_config_id,
        "trigger_run_id": trigger_run_id,
    }
    logger.info("KOL daily crawl sources synced: %s", summary)
    return summary


def parse_kol_crawl_source_row(
    row: list[object] | tuple[object, ...],
    *,
    sheet_row_index: int,
    doc: client.DocInfo,
) -> KolCrawlSourceRow | None:
    metric_date = _parse_date(_cell(row, KOL_DAILY_COL_DATE))
    account_name = _cell(row, KOL_DAILY_COL_ACCOUNT)
    platform = _cell(row, KOL_DAILY_COL_PLATFORM)
    homepage_url = _cell(row, KOL_DAILY_COL_HOMEPAGE)
    if not metric_date or not homepage_url or homepage_url == "/":
        return None
    return KolCrawlSourceRow(
        metric_date=metric_date,
        account_name=account_name,
        platform=platform,
        homepage_url=homepage_url,
        existing_fans_count=_parse_count(_cell(row, KOL_DAILY_COL_FANS)),
        source_locator={
            "file_id": doc.file_id,
            "sheet_id": doc.sheet_id,
            "row_index": sheet_row_index,
            "date_col_index": KOL_DAILY_COL_DATE,
            "url_col_index": KOL_DAILY_COL_HOMEPAGE,
            "fans_col_index": KOL_DAILY_COL_FANS,
            "growth_col_index": KOL_DAILY_COL_GROWTH,
            "read_col_index": KOL_DAILY_COL_READ,
        },
    )


def writeback_kol_daily_crawl_results_to_tencent_docs(
    *,
    target_date: date | None = None,
    doc_url: str | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    resolved_date = target_date or date.today()
    resolved_source_name = source_name or KOL_DAILY_CRAWL_SOURCE_NAME
    resolved_doc_url = (doc_url or Config.KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL or "").strip()
    if not resolved_doc_url:
        raise ValueError("KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL is not configured")

    doc = client.parse_doc_url(resolved_doc_url)
    rows, start_row = client.fetch_grid(KOL_DAILY_WRITEBACK_RANGE, doc=doc)
    current_rows_by_url = locate_kol_daily_rows_by_date_url(rows, start_row, resolved_date)
    metric_rows = _kol_daily_crawl_metric_rows(resolved_date, source_name=resolved_source_name)

    requests: list[dict[str, Any]] = []
    successes: list[dict[str, Any]] = []
    failures: list[tuple[dict[str, Any], str]] = []
    for item in metric_rows:
        homepage_url = str(item.get("homepage_url") or "").strip()
        if item.get("metric_id") is None or str(item.get("metric_status") or "") != "success":
            failures.append((item, "metric result is not successful"))
            continue
        matches = current_rows_by_url.get(homepage_url, [])
        if not matches:
            failures.append((item, "row not found by date and homepage_url"))
            continue
        if len(matches) > 1:
            failures.append((item, "duplicate rows by date and homepage_url"))
            continue
        writeback_requests = _kol_daily_metric_writeback_requests(item)
        if not writeback_requests:
            failures.append((item, "no requested metric fields to write back"))
            continue
        for writeback in writeback_requests:
            requests.append(
                row_cells_request(
                    matches[0],
                    writeback["start_col"],
                    writeback["values"],
                    text_format={"fontSize": max(int(Config.KOL_DAILY_SNAPSHOT_WRITEBACK_FONT_SIZE or 10), 1)},
                    doc=doc,
                )
            )
        successes.append(item)

    if requests:
        client.post_batch_update(requests, "kol_daily_crawl_writeback", doc=doc)

    for item in successes:
        mark_profile_writeback(
            metric_source_id=int(item["metric_source_id"]),
            metric_id=int(item["metric_id"]) if item.get("metric_id") is not None else None,
            locator=item.get("source_locator") or {},
            status="success",
        )
    for item, error in failures:
        mark_profile_writeback(
            metric_source_id=int(item["metric_source_id"]),
            metric_id=int(item["metric_id"]) if item.get("metric_id") is not None else None,
            locator=item.get("source_locator") or {},
            status="error",
            error=error,
        )

    summary = {
        "date": resolved_date.isoformat(),
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "sheet_title": client.fetch_sheet_title(doc),
        "source_name": resolved_source_name,
        "metric_rows": len(metric_rows),
        "written": len(successes),
        "failed": len(failures),
        "failures": [
            {
                "metric_source_id": int(item["metric_source_id"]),
                "homepage_url": item.get("homepage_url"),
                "error": error,
            }
            for item, error in failures[:20]
        ],
    }
    logger.info("KOL daily crawl results written back: %s", summary)
    return summary


def _kol_daily_metric_writeback_requests(item: dict[str, Any]) -> list[dict[str, Any]]:
    locator = item.get("source_locator") or {}
    requested_fields = tuple(locator.get("requested_fields") or KOL_DAILY_CRAWL_FIELDS)
    columns = [
        ("fans_count", KOL_DAILY_COL_FANS, item.get("fans_count")),
        ("growth_count", KOL_DAILY_COL_GROWTH, item.get("growth_count")),
        ("read_count", KOL_DAILY_COL_READ, item.get("read_count")),
    ]
    selected = [
        (field_name, column_index, "" if value is None else value)
        for field_name, column_index, value in columns
        if field_name in requested_fields
    ]
    if not selected:
        return []

    requests: list[dict[str, Any]] = []
    run_start = selected[0][1]
    run_values: list[Any] = []
    previous_col = run_start - 1
    for _, column_index, value in selected:
        if column_index != previous_col + 1 and run_values:
            requests.append({"start_col": run_start, "values": run_values})
            run_start = column_index
            run_values = []
        run_values.append(value)
        previous_col = column_index
    if run_values:
        requests.append({"start_col": run_start, "values": run_values})
    return requests


def ensure_kol_daily_snapshots_from_base_profiles(*, snapshot_date: date | None = None) -> dict[str, Any]:
    target_date = snapshot_date or date.today()
    conn = get_conn()
    try:
        existing = repository.list_kol_daily_snapshots(conn, snapshot_date=target_date)
        existing_keys = {(str(row["kol_name"]), str(row["platform"])) for row in existing}
        profiles = repository.list_kol_base_profiles(conn)
        created = 0
        for profile in profiles:
            key = (str(profile["kol_name"]), str(profile["platform"]))
            if key in existing_keys:
                continue
            repository.upsert_kol_daily_snapshot(
                conn,
                kol_profile_id=int(profile["id"]),
                snapshot_date=target_date,
                kol_name=key[0],
                platform=key[1],
                homepage_url=str(profile.get("homepage_url") or ""),
                group_name=str(profile.get("group_name") or ""),
                kol_type=str(profile.get("kol_type") or OTHER_TYPE),
                fans_count=None,
                growth_count=None,
                read_count=None,
            )
            created += 1
        conn.commit()
        return {
            "date": target_date.isoformat(),
            "base_profiles": len(profiles),
            "existing": len(existing),
            "created": created,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def writeback_kol_daily_snapshots_to_tencent_docs(
    *,
    snapshot_date: date | None = None,
    doc_url: str | None = None,
    include_days: int | None = None,
) -> dict[str, Any]:
    target_date = snapshot_date or date.today()
    resolved_include_days = max(int(include_days if include_days is not None else Config.KOL_DAILY_SNAPSHOT_WRITEBACK_DAYS), 1)
    writeback_dates = [
        target_date - timedelta(days=offset)
        for offset in range(resolved_include_days - 1, -1, -1)
    ]
    resolved_doc_url = (doc_url or Config.KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL or "").strip()
    if not resolved_doc_url:
        raise ValueError("KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL is not configured")

    doc = client.parse_doc_url(resolved_doc_url)
    conn = get_conn()
    try:
        rows_by_date = {
            item: repository.list_kol_daily_snapshots(conn, snapshot_date=item)
            for item in writeback_dates
        }
    finally:
        conn.close()

    existing_rows, existing_start = client.fetch_grid("A1:I5000", doc=doc)
    existing_by_date = _existing_writeback_rows_by_date(existing_rows, existing_start)
    append_row = _next_append_row(existing_rows, existing_start)
    requests = [_kol_row_request(1, KOL_DAILY_WRITEBACK_HEADER, doc=doc)]
    written_data_rows = 0
    cleared_rows = 0
    row_starts: dict[str, int] = {}
    for item in writeback_dates:
        row_values = [_snapshot_writeback_row(row) for row in rows_by_date[item]]
        existing_indexes = existing_by_date.get(item, [])
        if existing_indexes:
            start_row = min(existing_indexes)
        else:
            start_row = append_row
            append_row += len(row_values)
        row_starts[item.isoformat()] = start_row if row_values else 0
        for offset, values in enumerate(row_values):
            requests.append(_kol_row_request(start_row + offset, values, doc=doc))
            written_data_rows += 1
        for row_index in existing_indexes[len(row_values):]:
            requests.append(_kol_row_request(row_index, [""] * len(KOL_DAILY_WRITEBACK_HEADER), doc=doc))
            cleared_rows += 1
    client.post_batch_update(requests, "kol_daily_snapshot_writeback", doc=doc)
    summary = {
        "date": target_date.isoformat(),
        "dates": [item.isoformat() for item in writeback_dates],
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "sheet_title": client.fetch_sheet_title(doc),
        "rows": sum(len(rows) for rows in rows_by_date.values()),
        "rows_by_date": {item.isoformat(): len(rows_by_date[item]) for item in writeback_dates},
        "row_starts": row_starts,
        "written_rows": written_data_rows + 1,
        "cleared_rows": cleared_rows,
    }
    logger.info("KOL daily snapshots written back: %s", summary)
    return summary


def _kol_row_request(row_index: int, values: list[Any], *, doc: client.DocInfo) -> dict[str, Any]:
    return row_cells_request(
        row_index,
        0,
        values,
        text_format={"fontSize": max(int(Config.KOL_DAILY_SNAPSHOT_WRITEBACK_FONT_SIZE or 10), 1)},
        doc=doc,
    )


def locate_kol_daily_rows_by_date_url(
    rows: list[list[object]],
    start_row: int,
    target_date: date,
) -> dict[str, list[int]]:
    output: dict[str, list[int]] = {}
    for offset, row in enumerate(rows[1:], start=1):
        row_index = start_row + offset + 1
        row_date = _parse_date(_cell(row, KOL_DAILY_COL_DATE))
        homepage_url = _cell(row, KOL_DAILY_COL_HOMEPAGE)
        if row_date == target_date and homepage_url:
            output.setdefault(homepage_url, []).append(row_index)
    return output


def _kol_daily_crawl_metric_rows(target_date: date, *, source_name: str | None = None) -> list[dict[str, Any]]:
    resolved_source_name = source_name or KOL_DAILY_CRAWL_SOURCE_NAME
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    s.id AS metric_source_id,
                    s.metric_date,
                    s.source_locator_json,
                    s.latest_metric_id AS latest_metric_id,
                    t.id AS target_id,
                    t.account_name,
                    t.platform,
                    t.app_type,
                    t.homepage_url,
                    m.id AS metric_id,
                    m.status AS metric_status,
                    m.fans_count,
                    m.growth_count,
                    m.read_count
                FROM profile_metric_sources s
                JOIN profile_targets t ON t.id = s.target_id
                LEFT JOIN profile_metric_runs m
                  ON m.target_id = s.target_id
                 AND m.metric_date = s.metric_date
                WHERE s.status = 'active'
                  AND s.source_name = %s
                  AND s.metric_date = %s
                ORDER BY s.id ASC
                """,
                (resolved_source_name, target_date),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    output = []
    for row in rows:
        item = dict(row)
        item["source_locator"] = _json_loads(str(item.pop("source_locator_json") or "")) or {}
        output.append(item)
    return output


def _resolve_profile_action_profile_key(
    *,
    app_type: str,
    requested_fields: tuple[str, ...] | list[str],
) -> str:
    profile = get_profile_action_profile(
        app_type=app_type or "unknown",
        task_type="profile_daily_metrics",
        field_names=tuple(requested_fields),
    )
    return str((profile or {}).get("action_profile_key") or "unknown_profile_daily_metrics_v1")


def _existing_writeback_rows_by_date(
    rows: list[list[object]],
    start_row: int,
) -> dict[date, list[int]]:
    output: dict[date, list[int]] = {}
    for offset, row in enumerate(rows[1:], start=1):
        row_index = start_row + offset + 1
        row_date = _parse_date(_cell(row, 0))
        if row_date:
            output.setdefault(row_date, []).append(row_index)
    return output


def _next_append_row(rows: list[list[object]], start_row: int) -> int:
    last_row = 1
    for offset, row in enumerate(rows, start=1):
        if any(str(value or "").strip() for value in row):
            last_row = start_row + offset
    return max(last_row + 1, 2)


def resolve_kol_daily_header(header: list[object] | tuple[object, ...]) -> dict[str, Any]:
    normalized_headers = [normalize_title(value) for value in header]
    columns: dict[str, int] = {}
    problems: list[str] = []
    for field_name, aliases in KOL_DAILY_FIELD_ALIASES.items():
        matches = _matching_columns(normalized_headers, aliases)
        if not matches:
            if field_name in REQUIRED_FIELDS:
                problems.append(f"missing required KOL daily field: {field_name}")
            continue
        if len(matches) > 1:
            problems.append(f"ambiguous KOL daily field: {field_name} candidates={matches}")
            continue
        columns[field_name] = matches[0]
    return {"columns": columns, "problems": problems}


def parse_kol_daily_row(row: list[object] | tuple[object, ...], columns: dict[str, int]) -> KolSnapshotRow | None:
    snapshot_date = _parse_date(_cell(row, columns.get("snapshot_date")))
    kol_name = _cell(row, columns.get("kol_name"))
    platform = _cell(row, columns.get("platform"))
    if not snapshot_date or not kol_name or not platform:
        return None
    return KolSnapshotRow(
        snapshot_date=snapshot_date,
        kol_name=kol_name,
        platform=platform,
        homepage_url=_cell(row, columns.get("homepage_url")),
        group_name=_cell(row, columns.get("group_name")),
        fans_count=_parse_count(_cell(row, columns.get("fans_count"))),
        growth_count=_parse_count(_cell(row, columns.get("growth_count"))),
        read_count=_parse_count(_cell(row, columns.get("read_count"))),
    )


def _matching_columns(normalized_headers: list[str], aliases: tuple[str, ...]) -> list[int]:
    normalized_aliases = [normalize_title(alias) for alias in aliases]
    exact = [
        index
        for index, header in enumerate(normalized_headers)
        if header and header in normalized_aliases
    ]
    if exact:
        return exact
    return [
        index
        for index, header in enumerate(normalized_headers)
        if header and any(alias and alias in header for alias in normalized_aliases)
    ]


def _cell(row: list[object] | tuple[object, ...], index: int | None) -> str:
    if index is None or index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def _parse_count(value: str) -> int | None:
    text = value.strip().replace(",", "")
    if not text or text in {"-", "--", "N", "n"}:
        return None
    multiplier = Decimal(1)
    if text.endswith("\u4e07"):
        multiplier = Decimal(10000)
        text = text[:-1]
    elif text.endswith("\u4ebf"):
        multiplier = Decimal(100000000)
        text = text[:-1]
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return int(Decimal(match.group(0)) * multiplier)
    except (InvalidOperation, ValueError):
        return None


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _snapshot_writeback_row(row: dict[str, Any]) -> list[Any]:
    snapshot_date = row.get("snapshot_date")
    if hasattr(snapshot_date, "isoformat"):
        snapshot_date_text = snapshot_date.isoformat()
    else:
        snapshot_date_text = str(snapshot_date or "")
    return [
        snapshot_date_text,
        row.get("kol_name") or "",
        row.get("platform") or "",
        row.get("homepage_url") or "",
        row.get("group_name") or "",
        row.get("kol_type") or "",
        "" if row.get("fans_count") is None else row.get("fans_count"),
        "" if row.get("growth_count") is None else row.get("growth_count"),
        "" if row.get("read_count") is None else row.get("read_count"),
    ]


def _summary(
    *,
    target_date: date,
    doc: client.DocInfo,
    sheet_title: str,
    source_rows: int,
    imported: int,
    skipped: int,
    problems: list[str],
) -> dict[str, Any]:
    return {
        "date": target_date.isoformat(),
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "sheet_title": sheet_title,
        "source_rows": source_rows,
        "imported": imported,
        "skipped": skipped,
        "problems": problems,
    }
