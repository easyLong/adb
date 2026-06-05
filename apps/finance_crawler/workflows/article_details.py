"""Demand-1 article detail workflow.

Tencent Docs is treated as an adapter. The source rows define which article
URLs to crawl, MySQL stores observations, and writeback updates only the
article detail fields in the sheet.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.integrations.tencent_docs import columns as tencent_docs_columns
from apps.finance_crawler.integrations.tencent_docs.write_requests import cell_request, screenshot_cell_value
from apps.finance_crawler.mobile.crawler import open_url, resolve_short_url, scrape_record_content
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.mobile.parsers import parse_count_token
from apps.finance_crawler.storage.article_details import (
    article_detail_summary,
    article_key_for_url,
    get_pending_article_sources,
    get_pending_article_writebacks,
    mark_article_writeback,
    record_article_detail_run,
    upsert_article_source,
)
from apps.finance_crawler.storage.db import log_task
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.link_source import detect_link_source
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("article_details")

SOURCE_TYPE = "tencent_docs"
DATE_COL = 0
IP_COL = 1
PRODUCT_COL = 2
URL_COL = 8
TITLE_COL = 9
SCREENSHOT_COL = 10
READ_COL = 11
COMMENT_COL = 12
LIKE_COL = 13


def run_article_details() -> dict[str, Any]:
    started = time.time()
    try:
        imported = sync_article_sources_from_tencent_docs()
        crawled = crawl_pending_article_details()
        written = writeback_article_details() if Config.ARTICLE_DETAILS_WRITEBACK_ENABLED else 0
        summary = {
            "imported": imported,
            "crawled": len(crawled),
            "written": written,
            **article_detail_summary(),
        }
        log_task("article_details", "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        log_task("article_details", "error", str(exc), time.time() - started)
        raise


def sync_article_sources_from_tencent_docs(doc_url: str | None = None) -> int:
    doc = _article_doc(doc_url)
    rows, start_row = client.fetch_grid(Config.ARTICLE_DETAILS_READ_RANGE, doc=doc)
    columns = tencent_docs_columns.resolve_columns(
        rows,
        start_row,
        tencent_docs_columns.ARTICLE_DETAIL_ALIASES,
        _article_column_fallbacks(),
        strict_fallback_title=True,
    )
    imported = 0
    for offset, row in enumerate(rows):
        sheet_row_index = start_row + offset + 1
        if sheet_row_index == 1:
            continue
        parsed = _parse_article_source_row(row, sheet_row_index=sheet_row_index, doc=doc, columns=columns)
        if not parsed:
            continue
        upsert_article_source(parsed)
        imported += 1
    logger.info("article sources synced from Tencent Docs: imported=%s", imported)
    return imported


def crawl_pending_article_details(limit: int | None = None) -> list[dict[str, Any]]:
    resolved_limit = limit if limit is not None else Config.ARTICLE_DETAILS_CRAWL_LIMIT
    records = get_pending_article_sources(limit=resolved_limit or None)
    if not records:
        logger.info("article detail crawl skipped: no pending records")
        return []

    try:
        assert_device_ready()
    except DeviceUnavailable:
        reset_device_session()
        raise

    results: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        logger.info(
            "article detail crawl %s/%s row=%s ip=%s",
            index,
            len(records),
            record.get("source_locator", {}).get("row_index"),
            record.get("ip_name"),
        )
        result = _crawl_article(record)
        results.append(result)
    return results


def writeback_article_details(limit: int | None = None) -> int:
    rows = get_pending_article_writebacks(limit=limit)
    if not rows:
        logger.info("article detail writeback skipped: no pending rows")
        return 0

    requests_by_doc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    columns_by_doc: dict[tuple[str, str], dict[str, int]] = {}
    successes: list[dict[str, Any]] = []
    failures: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        locator = row.get("source_locator") or {}
        try:
            doc = client.DocInfo(file_id=str(locator["file_id"]), sheet_id=str(locator["sheet_id"]))
            row_index = int(locator["row_index"])
            key = (doc.file_id, doc.sheet_id)
            if key not in columns_by_doc:
                columns_by_doc[key] = _article_writeback_columns(doc, locator)
            columns = columns_by_doc[key]
            requests_by_doc.setdefault(key, []).extend(
                [
                    cell_request(row_index, columns["title"], row.get("article_title") or "", doc=doc),
                    cell_request(row_index, columns["screenshot"], screenshot_cell_value(row.get("screenshot_path")), doc=doc),
                    cell_request(
                        row_index,
                        columns["comment_count"],
                        "" if row.get("comment_count") is None else row["comment_count"],
                        doc=doc,
                    ),
                    cell_request(
                        row_index,
                        columns["like_count"],
                        "" if row.get("like_count") is None else row["like_count"],
                        doc=doc,
                    ),
                ]
            )
            successes.append(row)
        except Exception as exc:
            failures.append((row, str(exc)))

    for (file_id, sheet_id), requests in requests_by_doc.items():
        client.post_batch_update(
            requests,
            "article_detail_writeback",
            doc=client.DocInfo(file_id=file_id, sheet_id=sheet_id),
        )

    for row in successes:
        mark_article_writeback(
            source_id=int(row["source_id"]),
            run_id=int(row["run_id"]) if row.get("run_id") is not None else None,
            locator=row.get("source_locator") or {},
            status="success",
        )
    for row, error in failures:
        mark_article_writeback(
            source_id=int(row["source_id"]),
            run_id=int(row["run_id"]) if row.get("run_id") is not None else None,
            locator=row.get("source_locator") or {},
            status="error",
            error=error,
        )
    logger.info("article detail writeback finished: success=%s failed=%s", len(successes), len(failures))
    return len(successes)


def _crawl_article(record: dict[str, Any]) -> dict[str, Any]:
    url = str(record["article_url"])
    started = time.perf_counter()
    source_id = int(record["source_id"])
    target_id = int(record["target_id"])
    app_type = str(record.get("app_type") or "unknown")
    try:
        opened_url = resolve_short_url(url)
        open_url(opened_url)
        result = scrape_record_content(source_id, source_app=None)
        parsed = _parse_article_capture(result)
        status = "success" if _is_article_success(result, parsed) else "error"
        error = None if status == "success" else _article_error(result, parsed)
        run_id = record_article_detail_run(
            target_id=target_id,
            source_id=source_id,
            app_type=app_type,
            article_url=url,
            status=status,
            article_title=parsed.get("article_title"),
            comment_count=parsed.get("comment_count"),
            like_count=parsed.get("like_count"),
            metrics={
                "workflow": "article_details",
                "duration": round(time.perf_counter() - started, 3),
                "capture_pages": result.get("capture_pages"),
                "ocr_records": parsed.get("ocr_record_count"),
                "opened_url": opened_url,
            },
            screenshot_path=result.get("screenshot_path"),
            error=error,
        )
        return {
            "run_id": run_id,
            "source_id": source_id,
            "row_index": (record.get("source_locator") or {}).get("row_index"),
            "status": status,
            "article_title": parsed.get("article_title"),
            "comment_count": parsed.get("comment_count"),
            "like_count": parsed.get("like_count"),
            "error": error,
        }
    except Exception as exc:
        run_id = record_article_detail_run(
            target_id=target_id,
            source_id=source_id,
            app_type=app_type,
            article_url=url,
            status="error",
            article_title=None,
            comment_count=None,
            like_count=None,
            metrics={"workflow": "article_details", "duration": round(time.perf_counter() - started, 3)},
            error=str(exc),
        )
        logger.warning("article detail crawl failed source=%s url=%s: %s", source_id, url, exc)
        return {
            "run_id": run_id,
            "source_id": source_id,
            "row_index": (record.get("source_locator") or {}).get("row_index"),
            "status": "error",
            "error": str(exc),
        }


def _parse_article_capture(result: dict[str, Any]) -> dict[str, Any]:
    ocr_rows = _read_ocr_rows(_ocr_records_path(result))
    title = extract_tenpay_title_from_ocr(ocr_rows) or str(result.get("article_title") or "")
    bottom_counts = extract_tenpay_bottom_counts_from_ocr(ocr_rows)
    comment_count = bottom_counts.get("comment_count")
    if comment_count is None and result.get("comment_count") is not None:
        comment_count = int(result.get("comment_count") or 0)
    like_count = bottom_counts.get("like_count")
    if like_count is None and result.get("like_found"):
        like_count = int(result.get("like_count") or 0)
    return {
        "article_title": title,
        "comment_count": comment_count,
        "like_count": like_count,
        "ocr_record_count": len(ocr_rows),
    }


def extract_tenpay_title_from_ocr(rows: list[dict[str, Any]]) -> str:
    candidates = []
    for row in rows:
        text = _ocr_text(row)
        bounds = row.get("bounds") or {}
        top = int(bounds.get("top") or 0)
        left = int(bounds.get("left") or 0)
        if 380 <= top <= 650 and left <= 120 and _looks_like_title_line(text):
            candidates.append((top, left, text))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return "".join(item[2] for item in candidates).strip()


def extract_tenpay_bottom_counts_from_ocr(rows: list[dict[str, Any]]) -> dict[str, int]:
    numeric = []
    for row in rows:
        text = _ocr_text(row).replace(",", "")
        bounds = row.get("bounds") or {}
        top = int(bounds.get("top") or 0)
        left = int(bounds.get("left") or 0)
        if top < 2150:
            continue
        if not re.fullmatch(r"\d+(?:\.\d+)?(?:[wWkK\u4e07\u5343])?", text):
            continue
        numeric.append((left, parse_count_token(text)))
    numeric.sort(key=lambda item: item[0])
    if len(numeric) < 2:
        return {}
    return {
        "comment_count": numeric[0][1],
        "like_count": numeric[1][1],
    }


def _is_article_success(result: dict[str, Any], parsed: dict[str, Any]) -> bool:
    if result.get("status") != "success":
        return False
    if not parsed.get("article_title"):
        return False
    if parsed.get("comment_count") is None:
        return False
    if parsed.get("like_count") is None:
        return False
    return True


def _article_error(result: dict[str, Any], parsed: dict[str, Any]) -> str:
    if result.get("error"):
        return str(result["error"])
    missing = []
    if not parsed.get("article_title"):
        missing.append("article_title")
    if parsed.get("comment_count") is None:
        missing.append("comment_count")
    if parsed.get("like_count") is None:
        missing.append("like_count")
    return "article fields were not detected: " + ", ".join(missing)


def _parse_article_source_row(
    row: list[str],
    *,
    sheet_row_index: int,
    doc: client.DocInfo,
    columns: dict[str, int],
) -> dict[str, Any] | None:
    article_url = _cell(row, columns["url"])
    if not article_url or article_url == "/":
        return None
    source_date = _parse_date(_cell(row, columns["date"]))
    article_key = article_key_for_url(article_url)
    locator = {
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "row_index": sheet_row_index,
        "url_col_index": columns["url"],
        "title_col_index": columns["title"],
        "screenshot_col_index": columns["screenshot"],
        "read_col_index": columns["read_count"],
        "comment_col_index": columns["comment_count"],
        "like_col_index": columns["like_count"],
    }
    return {
        "article_key": article_key,
        "ip_name": _cell(row, columns["ip"]),
        "product_name": _cell(row, columns["product"]),
        "app_type": detect_link_source(article_url),
        "article_url": article_url,
        "source_date": source_date,
        "source_type": SOURCE_TYPE,
        "source_name": "article_details",
        "source_key": article_key_for_url(f"{doc.file_id}:{doc.sheet_id}:{sheet_row_index}:{article_url}"),
        "source_locator": locator,
        "requested_fields": ["article_title", "screenshot", "comment_count", "like_count"],
        "source": {"doc_url": Config.ARTICLE_DETAILS_DOC_URL or Config.QQ_DOC_URL},
    }


def _article_doc(doc_url: str | None = None) -> client.DocInfo:
    url = (doc_url or Config.ARTICLE_DETAILS_DOC_URL or Config.QQ_DOC_URL).strip()
    if not url:
        raise RuntimeError("ARTICLE_DETAILS_DOC_URL is not configured")
    return client.parse_doc_url(url)


def _article_column_fallbacks() -> dict[str, int]:
    return {
        "date": DATE_COL,
        "ip": IP_COL,
        "product": PRODUCT_COL,
        "url": URL_COL,
        "title": TITLE_COL,
        "screenshot": SCREENSHOT_COL,
        "read_count": READ_COL,
        "comment_count": COMMENT_COL,
        "like_count": LIKE_COL,
    }


def _article_writeback_columns(doc: client.DocInfo, locator: dict[str, Any]) -> dict[str, int]:
    fallbacks = _article_column_fallbacks()
    fallbacks.update(
        {
            "title": _locator_col(locator, "title_col_index", TITLE_COL),
            "screenshot": _locator_col(locator, "screenshot_col_index", SCREENSHOT_COL),
            "comment_count": _locator_col(locator, "comment_col_index", COMMENT_COL),
            "like_count": _locator_col(locator, "like_col_index", LIKE_COL),
        }
    )
    return tencent_docs_columns.fetch_header_columns(
        doc,
        aliases_by_field={
            "title": tencent_docs_columns.ARTICLE_DETAIL_ALIASES["title"],
            "screenshot": tencent_docs_columns.ARTICLE_DETAIL_ALIASES["screenshot"],
            "comment_count": tencent_docs_columns.ARTICLE_DETAIL_ALIASES["comment_count"],
            "like_count": tencent_docs_columns.ARTICLE_DETAIL_ALIASES["like_count"],
        },
        fallbacks=fallbacks,
        strict_fallback_title=True,
    )


def _locator_col(locator: dict[str, Any], key: str, fallback: int) -> int:
    value = locator.get(key)
    return int(value) if value is not None else fallback


def _read_ocr_rows(path_text: str | None) -> list[dict[str, Any]]:
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ocr_records_path(result: dict[str, Any]) -> str | None:
    raw_path = result.get("ocr_records")
    if isinstance(raw_path, str):
        return raw_path
    screenshot_path = result.get("screenshot_path")
    if not screenshot_path:
        return None
    return str(Path(str(screenshot_path)).parent / "ocr_records.jsonl")


def _ocr_text(row: dict[str, Any]) -> str:
    return str(row.get("text") or "").strip()


def _looks_like_title_line(text: str) -> bool:
    if not text or len(text) < 4:
        return False
    if text in {"讨论区", "听一听", "关注"}:
        return False
    if re.search(r"\d{4}-\d{2}-\d{2}", text):
        return False
    return True


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
