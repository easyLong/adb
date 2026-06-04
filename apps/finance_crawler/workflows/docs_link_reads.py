"""Write read counts from Tencent Docs K-column links back to M-column cells."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.integrations.tencent_docs.write_requests import cell_request
from apps.finance_crawler.mobile.capture_engine import capture_pages
from apps.finance_crawler.mobile.capture_engine import run_adb
from apps.finance_crawler.mobile.crawler import open_url, resolve_short_url
from apps.finance_crawler.mobile.device_session import current_serial, device as session_device
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.mobile.parsers import normalize_count_text, parse_count_token
from apps.finance_crawler.storage.db import log_task
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("docs_link_reads")


@dataclass(frozen=True)
class DocLinkReadTarget:
    row_index: int
    link: str
    title: str
    account_name: str
    existing_read: str


def run_docs_link_reads(
    *,
    doc_url: str | None = None,
    target_date: date | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    started = time.time()
    doc = _select_doc(doc_url=doc_url, target_date=target_date)
    targets = _read_targets(doc, limit=limit)
    results: list[dict[str, Any]] = []
    requests_payload: list[dict[str, Any]] = []
    written_count = 0

    if not targets:
        summary = {"targets": 0, "success": 0, "failed": 0, "written": 0}
        log_task("docs_link_reads", "success", json.dumps(summary), time.time() - started)
        return summary

    try:
        assert_device_ready()
    except DeviceUnavailable:
        reset_device_session()
        raise

    for index, target in enumerate(targets, start=1):
        logger.info(
            "doc link read crawl %s/%s row=%s account=%s",
            index,
            len(targets),
            target.row_index,
            target.account_name,
        )
        result = _crawl_target(target)
        results.append(result)
        if result.get("status") == "success":
            requests_payload.append(
                cell_request(
                    target.row_index,
                    Config.DOC_LINK_READS_READ_COL,
                    result["read_count"],
                    doc=doc,
                )
            )
            if len(requests_payload) >= Config.QQ_BATCH_UPDATE_SIZE:
                client.post_batch_update(requests_payload, "docs_link_reads_partial", doc=doc)
                written_count += len(requests_payload)
                requests_payload.clear()

    if requests_payload:
        client.post_batch_update(requests_payload, "docs_link_reads", doc=doc)
        written_count += len(requests_payload)

    summary = {
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "targets": len(targets),
        "success": sum(1 for item in results if item.get("status") == "success"),
        "failed": sum(1 for item in results if item.get("status") != "success"),
        "written": written_count,
        "results": results,
    }
    log_task("docs_link_reads", "success", json.dumps(_log_safe(summary), ensure_ascii=False), time.time() - started)
    return summary


def extract_read_count_from_records(records: list[dict[str, Any]]) -> int | None:
    ordered = sorted(
        records,
        key=lambda item: (
            int(item.get("page_index") or 0),
            int((item.get("bounds") or {}).get("top") or 0),
            int((item.get("bounds") or {}).get("left") or 0),
        ),
    )
    texts = [str(item.get("text") or "").strip() for item in ordered if str(item.get("text") or "").strip()]
    return extract_read_count_from_texts(texts)


def extract_read_count_from_texts(texts: list[str]) -> int | None:
    best = 0
    found = False
    number = r"(?P<num>\d+(?:[,.]\d+)*(?:\.\d+)?\s*[\u4e07wWkK\u5343]?)"
    labels = r"\u9605\u8bfb|\u6d4f\u89c8|\u67e5\u770b"

    cleaned = [re.sub(r"\s+", "", text or "") for text in texts if str(text or "").strip()]
    for text in cleaned:
        compact = normalize_count_text(text)
        for pattern in (
            rf"{number}(?:\u6b21)?(?:{labels})",
            rf"(?:{labels})(?:\u91cf|\u6570)?{number}",
        ):
            match = re.search(pattern, compact)
            if not match:
                continue
            found = True
            best = max(best, parse_count_token(match.group("num")))

    for index, text in enumerate(cleaned):
        if text not in {"\u9605\u8bfb", "\u6d4f\u89c8", "\u67e5\u770b"} or index <= 0:
            continue
        value = _parse_standalone_count(cleaned[index - 1])
        if value is None:
            continue
        found = True
        best = max(best, value)

    return best if found else None


def _crawl_target(target: DocLinkReadTarget) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir = Config.CAPTURE_DIR / f"doc_link_reads_row_{target.row_index}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    opened_url = ""
    last_error = ""
    try:
        opened_url = resolve_short_url(target.link)
        for attempt in range(Config.DOC_LINK_READS_OPEN_RETRIES + 1):
            if attempt:
                _force_stop_link_app(opened_url)
            attempt_dir = output_dir if attempt == 0 else output_dir / f"retry_{attempt}"
            open_url(opened_url)
            device = session_device()
            summary = capture_pages(
                device=device,
                output_dir=attempt_dir,
                max_scrolls=0,
                wait_after_open=max(Config.PAGE_LOAD_WAIT, 3.0),
                wait_after_scroll=0.0,
                enable_ocr=Config.DOC_LINK_READS_ENABLE_OCR,
                dynamic_wait=False,
                ready_timeout=0.0,
                ready_check_interval=0.5,
                serial=current_serial(),
            )
            records = _read_capture_records(summary)
            read_count = extract_read_count_from_records(records)
            screenshot = next(attempt_dir.glob("page_000.png"), None)
            if read_count is not None:
                return {
                    "row_index": target.row_index,
                    "status": "success",
                    "read_count": read_count,
                    "screenshot_path": str(screenshot) if screenshot else None,
                    "opened_url": opened_url,
                    "attempts": attempt + 1,
                    "duration": round(time.perf_counter() - started, 3),
                }
            if _looks_retryable_error(records) and _tap_retry_button(records):
                time.sleep(max(Config.PAGE_LOAD_WAIT, 4.0))
                retry_dir = attempt_dir / "tap_retry"
                retry_summary = capture_pages(
                    device=device,
                    output_dir=retry_dir,
                    max_scrolls=0,
                    wait_after_open=max(Config.PAGE_LOAD_WAIT, 3.0),
                    wait_after_scroll=0.0,
                    enable_ocr=Config.DOC_LINK_READS_ENABLE_OCR,
                    dynamic_wait=False,
                    ready_timeout=0.0,
                    ready_check_interval=0.5,
                    serial=current_serial(),
                )
                retry_records = _read_capture_records(retry_summary)
                read_count = extract_read_count_from_records(retry_records)
                retry_screenshot = next(retry_dir.glob("page_000.png"), None)
                if read_count is not None:
                    return {
                        "row_index": target.row_index,
                        "status": "success",
                        "read_count": read_count,
                        "screenshot_path": str(retry_screenshot) if retry_screenshot else None,
                        "opened_url": opened_url,
                        "attempts": attempt + 1,
                        "used_page_retry": True,
                        "duration": round(time.perf_counter() - started, 3),
                    }
                records = retry_records
                screenshot = retry_screenshot or screenshot
            if _looks_retryable_error(records):
                last_error = "retryable_error_page"
            elif _looks_blank(records):
                last_error = "blank_page"
            else:
                last_error = "read_count_not_found"
            logger.warning(
                "doc link read not found row=%s attempt=%s error=%s",
                target.row_index,
                attempt + 1,
                last_error,
            )
        return {
            "row_index": target.row_index,
            "status": "error",
            "error": last_error or "read_count_not_found",
            "screenshot_path": _latest_screenshot(output_dir),
            "opened_url": opened_url,
            "duration": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        logger.warning("doc link read crawl failed row=%s: %s", target.row_index, exc)
        return {
            "row_index": target.row_index,
            "status": "error",
            "error": str(exc),
            "duration": round(time.perf_counter() - started, 3),
        }


def _force_stop_link_app(opened_url: str) -> None:
    package_name = Config.AFWEALTH_PACKAGE if opened_url.startswith("afwealth://") else Config.ALIPAY_PACKAGE
    try:
        run_adb(["shell", "am", "force-stop", package_name], serial=current_serial(), timeout=10)
        reset_device_session()
        time.sleep(Config.APP_RESTART_WAIT)
    except Exception as exc:
        logger.warning("force-stop before doc link retry skipped: %s", exc)


def _looks_blank(records: list[dict[str, Any]]) -> bool:
    useful_texts = []
    for record in records:
        package_name = str(record.get("package") or "")
        text = str(record.get("text") or "").strip()
        desc = str(record.get("content_desc") or "").strip()
        bounds = record.get("bounds") or {}
        top = int(bounds.get("top") or 0)
        if package_name.startswith("com.android.systemui"):
            continue
        if not package_name and top < 260:
            continue
        if text and text not in {"顧?", "返回"}:
            useful_texts.append(text)
        if desc and desc not in {"返回"}:
            useful_texts.append(desc)
    return len(useful_texts) <= 1


def _looks_retryable_error(records: list[dict[str, Any]]) -> bool:
    texts = [
        str(record.get("text") or record.get("content_desc") or "").strip()
        for record in records
    ]
    joined = "\n".join(texts)
    return any(
        keyword in joined
        for keyword in (
            "\u7f51\u7edc\u4e0d\u7ed9\u529b",
            "\u8bf7\u7a0d\u540e\u91cd\u8bd5",
            "\u91cd\u8bd5",
            "\u52a0\u8f7d\u5931\u8d25",
            "\u8bf7\u6c42\u8d85\u65f6",
        )
    )


def _tap_retry_button(records: list[dict[str, Any]]) -> bool:
    for record in records:
        text = str(record.get("text") or record.get("content_desc") or "").strip()
        if "\u91cd\u8bd5" not in text:
            continue
        bounds = record.get("bounds") or {}
        left = int(bounds.get("left") or 0)
        right = int(bounds.get("right") or 0)
        top = int(bounds.get("top") or 0)
        bottom = int(bounds.get("bottom") or 0)
        if right <= left or bottom <= top:
            continue
        try:
            run_adb(
                ["shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2)],
                serial=current_serial(),
                timeout=10,
            )
            return True
        except Exception as exc:
            logger.warning("tap retry button failed: %s", exc)
            return False
    return False


def _latest_screenshot(output_dir: Path) -> str | None:
    screenshots = sorted(output_dir.rglob("page_000.png"), key=lambda item: item.stat().st_mtime)
    return str(screenshots[-1]) if screenshots else None


def _read_targets(doc: client.DocInfo, *, limit: int | None = None) -> list[DocLinkReadTarget]:
    rows, start_row = client.fetch_grid(Config.DOC_LINK_READS_READ_RANGE, doc=doc)
    targets: list[DocLinkReadTarget] = []
    resolved_limit = Config.DOC_LINK_READS_CRAWL_LIMIT if limit is None else limit
    for offset, row in enumerate(rows):
        row_index = start_row + offset + 1
        if row_index == 1:
            continue
        link = _cell(row, Config.DOC_LINK_READS_LINK_COL)
        if not link or not _looks_like_link(link):
            continue
        existing_read = _cell(row, Config.DOC_LINK_READS_READ_COL)
        if Config.DOC_LINK_READS_ONLY_EMPTY and existing_read:
            continue
        targets.append(
            DocLinkReadTarget(
                row_index=row_index,
                link=link,
                title=_cell(row, 0),
                account_name=_cell(row, 9),
                existing_read=existing_read,
            )
        )
        if resolved_limit and resolved_limit > 0 and len(targets) >= resolved_limit:
            break
    logger.info("doc link read targets=%s sheet=%s", len(targets), doc.sheet_id)
    return targets


def _select_doc(*, doc_url: str | None = None, target_date: date | None = None) -> client.DocInfo:
    base = client.parse_doc_url(doc_url) if doc_url else client.configured_doc()
    sheet_title = Config.DOC_LINK_READS_SHEET_TITLE.strip()
    if target_date is not None:
        sheet_title = target_date.strftime("%m%d")
    if not sheet_title:
        return base

    sheets = client.fetch_file_sheets(base.file_id)
    for sheet in sheets:
        if sheet.title == sheet_title:
            return sheet.doc
    for sheet in sheets:
        if sheet_title in sheet.title:
            return sheet.doc
    available = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in sheets)
    raise RuntimeError(f"sheet title not found: {sheet_title}; available: {available}")


def _read_capture_records(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("ui_jsonl", "ocr_jsonl"):
        path_text = summary.get(key)
        if not path_text:
            continue
        rows.extend(_read_jsonl(Path(str(path_text))))
    return [_normalize_bounds(row) for row in rows]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    output = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            output.append(json.loads(line))
    return output


def _normalize_bounds(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    bounds = dict(item.get("bounds") or {})
    if "right" not in bounds:
        bounds["right"] = int(bounds.get("left") or 0) + int(bounds.get("width") or 0)
    if "bottom" not in bounds:
        bounds["bottom"] = int(bounds.get("top") or 0) + int(bounds.get("height") or 0)
    item["bounds"] = bounds
    return item


def _parse_standalone_count(text: str) -> int | None:
    cleaned = re.sub(r"\s+", "", text.replace(",", ""))
    if not re.fullmatch(r"\d+(?:\.\d+)?[\u4e07wWkK\u5343]?", cleaned):
        return None
    return parse_count_token(cleaned)


def _cell(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _looks_like_link(text: str) -> bool:
    return text.startswith(("http://", "https://", "alipays://", "alipay://"))


def _log_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _log_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_log_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value
