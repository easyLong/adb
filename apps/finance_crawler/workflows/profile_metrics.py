"""Homepage profile metric workflow.

The Tencent Docs sheet is treated as an adapter: rows are imported as dated
metric requests, the crawler stores observations in MySQL, and writeback only
updates the configured sheet cell.
"""

from __future__ import annotations

import json
import shlex
import time
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.integrations.tencent_docs.write_requests import cell_request, row_cells_request
from apps.finance_crawler.mobile.capture_engine import capture_pages, open_app_link, run_adb
from apps.finance_crawler.mobile.capture_records import read_capture_records as _read_capture_records
from apps.finance_crawler.mobile.device_session import device as session_device
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.mobile.parsers import extract_profile_fans_count
from apps.finance_crawler.storage.db import log_task
from apps.finance_crawler.storage.profile_metrics import (
    create_daily_profile_metric_sources,
    get_pending_profile_metric_sources,
    get_pending_profile_writebacks,
    mark_profile_writeback,
    profile_key_for_url,
    profile_summary,
    record_profile_metric,
    upsert_profile_source,
)
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.link_source import detect_link_source
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("profile_metrics")

SOURCE_TYPE = "tencent_docs"
FANS_COL_INDEX = 4
SUPPORTED_PROFILE_PLATFORMS = {
    "\u8682\u8681",
    "alipay",
    "Alipay",
    "\u7406\u8d22\u901a",
    "\u817e\u8baf\u7406\u8d22\u901a",
    "tenpay",
    "Tenpay",
    "TENPAY",
    "tencentwm",
}


def create_daily_profile_metric_tasks(metric_date: date | None = None) -> int:
    resolved_date = metric_date or _configured_metric_date() or date.today()
    count = create_daily_profile_metric_sources(resolved_date)
    logger.info("daily profile metric tasks created date=%s count=%s", resolved_date, count)
    return count


def ensure_daily_profile_metric_rows(
    metric_date: date | None = None,
    *,
    doc_url: str | None = None,
    template_range: str | None = None,
) -> dict[str, Any]:
    """Append missing profile rows for a date from the configured template range."""

    resolved_date = metric_date or date.today()
    doc = _profile_doc(doc_url)
    template_rows, _ = client.fetch_grid(template_range or Config.PROFILE_METRICS_TEMPLATE_RANGE, doc=doc)
    existing_rows, existing_start = client.fetch_grid(Config.PROFILE_METRICS_READ_RANGE, doc=doc)

    existing_links = {
        _normalize_homepage_url(_cell(row, 3))
        for row in existing_rows
        if _parse_date(_cell(row, 0)) == resolved_date and _normalize_homepage_url(_cell(row, 3))
    }
    template_items = [_daily_template_values(row, resolved_date) for row in template_rows]
    template_items = [item for item in template_items if item and _normalize_homepage_url(str(item[3])) not in existing_links]
    if not template_items:
        summary = {
            "date": resolved_date.isoformat(),
            "template_rows": len(template_rows),
            "written": 0,
            "row_start": None,
            "row_end": None,
        }
        logger.info("profile daily rows already complete: %s", summary)
        return summary

    next_row = _next_append_row(existing_rows, existing_start)
    requests = [
        row_cells_request(next_row + offset, 0, values, doc=doc)
        for offset, values in enumerate(template_items)
    ]
    client.post_batch_update(requests, "profile_daily_rows", doc=doc)
    summary = {
        "date": resolved_date.isoformat(),
        "template_rows": len(template_rows),
        "written": len(template_items),
        "row_start": next_row,
        "row_end": next_row + len(template_items) - 1,
    }
    logger.info("profile daily rows prepared: %s", summary)
    return summary


def run_profile_metrics() -> dict[str, Any]:
    started = time.time()
    summary: dict[str, Any] = {}
    try:
        imported = sync_profile_sources_from_tencent_docs()
        crawled = crawl_pending_profile_metrics()
        written = writeback_profile_metrics() if Config.PROFILE_METRICS_WRITEBACK_ENABLED else 0
        summary = {
            "imported": imported,
            "crawled": len(crawled),
            "written": written,
            **profile_summary(),
        }
        log_task("profile_metrics", "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        log_task("profile_metrics", "error", str(exc), time.time() - started)
        raise


def sync_profile_sources_from_tencent_docs(doc_url: str | None = None) -> int:
    doc = _profile_doc(doc_url)
    rows, start_row = client.fetch_grid(Config.PROFILE_METRICS_READ_RANGE, doc=doc)
    imported = 0
    for offset, row in enumerate(rows):
        sheet_row_index = start_row + offset + 1
        if sheet_row_index == 1:
            continue
        parsed = _parse_profile_source_row(row, sheet_row_index=sheet_row_index, doc=doc)
        if not parsed:
            continue
        target_id, source_id = upsert_profile_source(parsed)
        fans = parsed.get("existing_fans_count")
        if fans is not None:
            metric_id = record_profile_metric(
                target_id=target_id,
                metric_date=parsed["metric_date"],
                app_type=parsed["app_type"],
                homepage_url=parsed["homepage_url"],
                status="success",
                fans_count=fans,
                metrics={"source": "tencent_docs_existing"},
                crawled_at=datetime.now(),
            )
            mark_profile_writeback(
                metric_source_id=source_id,
                metric_id=metric_id,
                locator=parsed["source_locator"],
                status="success",
            )
        imported += 1
    logger.info("profile sources synced from Tencent Docs: imported=%s", imported)
    return imported


def crawl_pending_profile_metrics(
    limit: int | None = None,
    *,
    target_date: date | None = None,
    source_name: str | None = None,
) -> list[dict[str, Any]]:
    resolved_limit = limit if limit is not None else Config.PROFILE_METRICS_CRAWL_LIMIT
    metric_date = target_date or _configured_metric_date()
    records = get_pending_profile_metric_sources(
        limit=resolved_limit or None,
        metric_date=metric_date,
        source_name=source_name,
    )
    if not records:
        logger.info("profile metric crawl skipped: no pending records")
        return []

    try:
        assert_device_ready()
    except DeviceUnavailable:
        reset_device_session()
        raise

    results: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        logger.info(
            "profile metric crawl %s/%s row=%s account=%s",
            index,
            len(records),
            record.get("source_locator", {}).get("row_index"),
            record.get("account_name"),
        )
        result = _crawl_profile(record)
        results.append(result)
    return results


def writeback_profile_metrics(limit: int | None = None) -> int:
    rows = get_pending_profile_writebacks(limit=limit)
    if not rows:
        logger.info("profile metric writeback skipped: no pending rows")
        return 0

    requests_by_doc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    successes: list[dict[str, Any]] = []
    failures: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        locator = row.get("source_locator") or {}
        try:
            doc = client.DocInfo(file_id=str(locator["file_id"]), sheet_id=str(locator["sheet_id"]))
            row_index = int(locator["row_index"])
            fans_col = int(locator.get("fans_col_index", FANS_COL_INDEX))
            key = (doc.file_id, doc.sheet_id)
            requests_by_doc.setdefault(key, []).append(
                row_cells_request(
                    row_index,
                    fans_col,
                    [row["fans_count"], "" if row.get("growth_count") is None else row["growth_count"]],
                    doc=doc,
                )
            )
            successes.append(row)
        except Exception as exc:
            failures.append((row, str(exc)))

    for (file_id, sheet_id), requests in requests_by_doc.items():
        client.post_batch_update(
            requests,
            "profile_metric_writeback",
            doc=client.DocInfo(file_id=file_id, sheet_id=sheet_id),
        )

    for row in successes:
        mark_profile_writeback(
            metric_source_id=int(row["metric_source_id"]),
            metric_id=int(row["metric_id"]) if row.get("metric_id") is not None else None,
            locator=row.get("source_locator") or {},
            status="success",
        )
    for row, error in failures:
        mark_profile_writeback(
            metric_source_id=int(row["metric_source_id"]),
            metric_id=int(row["metric_id"]) if row.get("metric_id") is not None else None,
            locator=row.get("source_locator") or {},
            status="error",
            error=error,
        )
    logger.info("profile metric writeback finished: success=%s failed=%s", len(successes), len(failures))
    return len(successes)


def _crawl_profile(record: dict[str, Any]) -> dict[str, Any]:
    url = str(record["homepage_url"])
    output_dir = Config.CAPTURE_DIR / ("profile_%s_%s" % (record["target_id"], datetime.now().strftime("%Y%m%d_%H%M%S")))
    started = time.perf_counter()
    try:
        known_error = _known_unavailable_profile_url(url)
        if known_error:
            metric_id = record_profile_metric(
                target_id=int(record["target_id"]),
                metric_date=record["metric_date"],
                app_type=str(record.get("app_type") or "unknown"),
                homepage_url=url,
                status="blocked",
                fans_count=None,
                metrics={"workflow": "profile_metrics", "duration": 0},
                error=known_error,
            )
            return {
                "metric_id": metric_id,
                "target_id": record["target_id"],
                "account_name": record.get("account_name"),
                "metric_date": record["metric_date"],
                "status": "blocked",
                "fans_count": None,
                "error": known_error,
            }
        _open_profile_url(url, source_app=record.get("app_type"))
        summary = capture_pages(
            session_device(),
            output_dir,
            max_scrolls=0,
            wait_after_open=max(Config.PAGE_LOAD_WAIT, 4.0),
            wait_after_scroll=Config.DETAIL_SCROLL_WAIT,
            enable_ocr=str(record.get("app_type") or "").lower() == "tenpay",
            dynamic_wait=False,
            ready_timeout=8,
            ready_check_interval=0.5,
            serial=None,
        )
        records = _read_capture_records(summary)
        fans_count = extract_profile_fans_count(records)
        texts = [str(item.get("text") or "").strip() for item in records if str(item.get("text") or "").strip()]
        blocked_error = None if fans_count is not None else _blocked_profile_page_error(texts)
        status = "success" if fans_count is not None else ("blocked" if blocked_error else "error")
        error = None if fans_count is not None else (blocked_error or "profile fans count was not detected")
        screenshot_path = str(output_dir / "page_000.png") if (output_dir / "page_000.png").exists() else None
        metric_id = record_profile_metric(
            target_id=int(record["target_id"]),
            metric_date=record["metric_date"],
            app_type=str(record.get("app_type") or "unknown"),
            homepage_url=url,
            status=status,
            fans_count=fans_count,
            metrics={
                "workflow": "profile_metrics",
                "duration": round(time.perf_counter() - started, 3),
                "capture_pages": 1,
            },
            screenshot_path=screenshot_path,
            error=error,
        )
        return {
            "metric_id": metric_id,
            "target_id": record["target_id"],
            "account_name": record.get("account_name"),
            "metric_date": record["metric_date"],
            "status": status,
            "fans_count": fans_count,
            "error": error,
        }
    except Exception as exc:
        record_profile_metric(
            target_id=int(record["target_id"]),
            metric_date=record["metric_date"],
            app_type=str(record.get("app_type") or "unknown"),
            homepage_url=url,
            status="error",
            fans_count=None,
            metrics={"workflow": "profile_metrics", "duration": round(time.perf_counter() - started, 3)},
            error=str(exc),
        )
        logger.warning("profile metric crawl failed target=%s url=%s: %s", record.get("target_id"), url, exc)
        return {
            "target_id": record["target_id"],
            "account_name": record.get("account_name"),
            "metric_date": record["metric_date"],
            "status": "error",
            "fans_count": None,
            "error": str(exc),
        }


def _parse_profile_source_row(
    row: list[str],
    *,
    sheet_row_index: int,
    doc: client.DocInfo,
) -> dict[str, Any] | None:
    metric_date = _parse_date(_cell(row, 0))
    account_name = _cell(row, 1)
    platform = _cell(row, 2)
    homepage_url = _cell(row, 3)
    existing_fans_count = _parse_int(_cell(row, 4))
    if not metric_date or not homepage_url or homepage_url == "/":
        return None
    if platform and platform not in SUPPORTED_PROFILE_PLATFORMS:
        return None
    app_type = detect_link_source(homepage_url)
    profile_key = profile_key_for_url(homepage_url)
    locator = {
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "row_index": sheet_row_index,
        "fans_col_index": FANS_COL_INDEX,
        "url_col_index": 3,
    }
    return {
        "profile_key": profile_key,
        "account_name": account_name,
        "platform": platform,
        "app_type": app_type,
        "homepage_url": homepage_url,
        "metric_date": metric_date,
        "source_type": SOURCE_TYPE,
        "source_name": "profile_metrics",
        "source_key": profile_key_for_url(f"{doc.file_id}:{doc.sheet_id}:{sheet_row_index}:{homepage_url}"),
        "source_locator": locator,
        "requested_fields": ["fans_count"],
        "source": {"doc_url": Config.PROFILE_METRICS_DOC_URL or Config.QQ_DOC_URL},
        "existing_fans_count": existing_fans_count,
    }


def _daily_template_values(row: list[str], metric_date: date) -> list[Any] | None:
    homepage_url = _cell(row, 3)
    if not homepage_url or homepage_url == "/":
        return None
    return [
        profile_sheet_date_value(metric_date),
        _cell(row, 1),
        _cell(row, 2),
        homepage_url,
        "",
        "",
        "",
        _cell(row, 7),
    ]


def _normalize_homepage_url(value: str) -> str:
    return value.strip()


def _next_append_row(rows: list[list[str]], start_row: int) -> int:
    last_used = start_row
    for offset, row in enumerate(rows):
        if any(str(cell or "").strip() for cell in row):
            last_used = start_row + offset + 1
    return max(2, last_used + 1)


def profile_sheet_date_value(metric_date: date) -> str:
    return metric_date.isoformat()


def _open_profile_url(url: str, *, source_app: str | None = None) -> None:
    if "think.klv5qu.com" in url:
        deep_link = _antfortune_deep_link(url)
        if deep_link:
            serial = assert_device_ready()
            run_adb(
                [
                    "shell",
                    "am",
                    "start",
                    "-a",
                    "android.intent.action.VIEW",
                    "-d",
                    shlex.quote(deep_link),
                    "-p",
                    Config.AFWEALTH_PACKAGE,
                ],
                serial=serial,
                timeout=20,
            )
            return
    try:
        serial = assert_device_ready()
        open_app_link(url, serial=serial)
    except RuntimeError as exc:
        if "target app package is not installed" not in str(exc) and "unable to resolve Intent" not in str(exc):
            raise
        serial = assert_device_ready()
        run_adb(
            ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", shlex.quote(url)],
            serial=serial,
            timeout=20,
        )


def _antfortune_deep_link(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    app_id = (query.get("appId") or [""])[0]
    page = (query.get("page") or [""])[0]
    if not app_id or not page:
        return None
    return "afwealth://platformapi/startapp?" + urlencode({"appId": app_id, "page": page})


def _known_unavailable_profile_url(url: str) -> str | None:
    if "ur.alipay.com" not in url:
        return None
    try:
        response = requests.get(url, allow_redirects=False, timeout=12)
    except Exception:
        return None
    location = response.headers.get("location") or ""
    if response.status_code in {301, 302, 303, 307, 308} and "/404" in location:
        return "profile short link redirects to Alipay 404"
    return None


def _blocked_profile_page_error(texts: list[str]) -> str | None:
    combined = "\n".join(texts)
    if "\u5b8c\u5584\u8eab\u4efd\u4fe1\u606f" in combined or "\u7f51\u7edc\u5b89\u5168\u6cd5" in combined:
        return "profile page is blocked by identity verification"
    if "404" in combined or "\u51fa\u9519\u4e86" in combined:
        return "profile page is unavailable"
    return None


def _profile_doc(doc_url: str | None = None) -> client.DocInfo:
    url = (doc_url or Config.PROFILE_METRICS_DOC_URL or Config.QQ_DOC_URL).strip()
    if not url:
        raise RuntimeError("PROFILE_METRICS_DOC_URL is not configured")
    return client.parse_doc_url(url)


def _configured_metric_date() -> date | None:
    if not Config.PROFILE_METRICS_TARGET_DATE:
        return None
    return date.fromisoformat(Config.PROFILE_METRICS_TARGET_DATE)


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) and row[index] is not None else ""


def _parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def _parse_int(value: str) -> int | None:
    text = value.strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


