"""Mobile read-count crawler shared by legacy workflows and crawler_app v2."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.mobile.action_plan import (
    ACTION_OCR,
    ACTION_OPEN_LINK,
    ACTION_SCREENSHOT,
    ACTION_TAP_RETRY,
    ACTION_UI_CONTROLS,
    FieldCapturePlan,
)
from apps.finance_crawler.mobile.capture_records import normalize_bounds, read_capture_records, read_jsonl
from apps.finance_crawler.mobile.capture_engine import capture_pages, run_adb
from apps.finance_crawler.mobile.crawler import open_url, resolve_short_url
from apps.finance_crawler.mobile.device_session import current_serial, device as session_device
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.mobile.read_count_parser import (
    extract_read_count_from_records,
    extract_read_count_from_texts,
    looks_blank,
    looks_retryable_error,
    not_found_reason_from_records,
    parse_standalone_count,
)
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("read_count_crawler")


@dataclass(frozen=True)
class ReadCountTarget:
    row_index: int
    link: str
    title: str = ""
    account_name: str = ""
    existing_read: str = ""
    output_prefix: str = "doc_link_reads"
    capture_plan: FieldCapturePlan | None = None


def crawl_read_count_target(target: ReadCountTarget) -> dict[str, Any]:
    started = time.perf_counter()
    capture_plan = target.capture_plan or _default_read_count_capture_plan()
    output_dir = Config.CAPTURE_DIR / (
        f"{target.output_prefix}_row_{target.row_index}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    opened_url = ""
    last_error = ""
    try:
        opened_url = resolve_short_url(target.link)
        for attempt in range(capture_plan.open_retries + 1):
            if attempt:
                _force_stop_link_app(opened_url)
            attempt_dir = output_dir if attempt == 0 else output_dir / f"retry_{attempt}"
            open_url(opened_url)
            device = session_device()
            summary = capture_pages(
                device=device,
                output_dir=attempt_dir,
                max_scrolls=capture_plan.max_scrolls,
                wait_after_open=capture_plan.wait_after_open,
                wait_after_scroll=capture_plan.wait_after_scroll,
                enable_ocr=capture_plan.enable_ocr,
                dynamic_wait=False,
                ready_timeout=capture_plan.ready_timeout,
                ready_check_interval=capture_plan.ready_check_interval,
                serial=current_serial(),
            )
            records = read_capture_records(summary)
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
                    "capture_plan": capture_plan.to_json_dict(),
                    "duration": round(time.perf_counter() - started, 3),
                }
            not_found_reason = not_found_reason_from_records(records)
            if not_found_reason:
                logger.warning(
                    "read count target not found row=%s attempt=%s reason=%s",
                    target.row_index,
                    attempt + 1,
                    not_found_reason,
                )
                return {
                    "row_index": target.row_index,
                    "status": "not_found",
                    "error": "not_found",
                    "not_found_reason": not_found_reason,
                    "screenshot_path": str(screenshot) if screenshot else None,
                    "opened_url": opened_url,
                    "attempts": attempt + 1,
                    "capture_plan": capture_plan.to_json_dict(),
                    "duration": round(time.perf_counter() - started, 3),
                }
            if capture_plan.allow_tap_retry and looks_retryable_error(records) and tap_retry_button(records):
                time.sleep(max(Config.PAGE_LOAD_WAIT, 4.0))
                retry_dir = attempt_dir / "tap_retry"
                retry_summary = capture_pages(
                    device=device,
                    output_dir=retry_dir,
                    max_scrolls=capture_plan.max_scrolls,
                    wait_after_open=capture_plan.wait_after_open,
                    wait_after_scroll=capture_plan.wait_after_scroll,
                    enable_ocr=capture_plan.enable_ocr,
                    dynamic_wait=False,
                    ready_timeout=capture_plan.ready_timeout,
                    ready_check_interval=capture_plan.ready_check_interval,
                    serial=current_serial(),
                )
                retry_records = read_capture_records(retry_summary)
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
                        "capture_plan": capture_plan.to_json_dict(),
                        "duration": round(time.perf_counter() - started, 3),
                    }
                not_found_reason = not_found_reason_from_records(retry_records)
                if not_found_reason:
                    logger.warning(
                        "read count target not found row=%s attempt=%s reason=%s",
                        target.row_index,
                        attempt + 1,
                        not_found_reason,
                    )
                    return {
                        "row_index": target.row_index,
                        "status": "not_found",
                        "error": "not_found",
                        "not_found_reason": not_found_reason,
                        "screenshot_path": str(retry_screenshot) if retry_screenshot else None,
                        "opened_url": opened_url,
                        "attempts": attempt + 1,
                        "used_page_retry": True,
                        "capture_plan": capture_plan.to_json_dict(),
                        "duration": round(time.perf_counter() - started, 3),
                    }
                records = retry_records
                screenshot = retry_screenshot or screenshot
            if looks_retryable_error(records):
                last_error = "retryable_error_page"
            elif looks_blank(records):
                last_error = "blank_page"
            else:
                last_error = "read_count_not_found"
            logger.warning(
                "read count not found row=%s attempt=%s error=%s",
                target.row_index,
                attempt + 1,
                last_error,
            )
        return {
            "row_index": target.row_index,
            "status": "error",
            "error": last_error or "read_count_not_found",
            "screenshot_path": latest_screenshot(output_dir),
            "opened_url": opened_url,
            "capture_plan": capture_plan.to_json_dict(),
            "duration": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        logger.warning("read count crawl failed row=%s: %s", target.row_index, exc)
        return {
            "row_index": target.row_index,
            "status": "error",
            "error": str(exc),
            "capture_plan": capture_plan.to_json_dict(),
            "duration": round(time.perf_counter() - started, 3),
        }


def _default_read_count_capture_plan() -> FieldCapturePlan:
    actions = [ACTION_OPEN_LINK, ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_TAP_RETRY]
    if Config.DOC_LINK_READS_ENABLE_OCR:
        actions.append(ACTION_OCR)
    return FieldCapturePlan(
        task_type="read_count",
        app_type="unknown",
        fields=("read_count",),
        actions=tuple(actions),
        max_scrolls=0,
        wait_after_open=max(Config.PAGE_LOAD_WAIT, 3.0),
        wait_after_scroll=0.0,
        open_retries=Config.DOC_LINK_READS_OPEN_RETRIES,
        ready_timeout=0.0,
        ready_check_interval=0.5,
    )


def tap_retry_button(records: list[dict[str, Any]]) -> bool:
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


def latest_screenshot(output_dir: Path) -> str | None:
    screenshots = sorted(output_dir.rglob("page_000.png"), key=lambda item: item.stat().st_mtime)
    return str(screenshots[-1]) if screenshots else None


def _force_stop_link_app(opened_url: str) -> None:
    package_name = Config.AFWEALTH_PACKAGE if opened_url.startswith("afwealth://") else Config.ALIPAY_PACKAGE
    try:
        run_adb(["shell", "am", "force-stop", package_name], serial=current_serial(), timeout=10)
        reset_device_session()
        time.sleep(Config.APP_RESTART_WAIT)
    except Exception as exc:
        logger.warning("force-stop before read count retry skipped: %s", exc)
