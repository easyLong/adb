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
from apps.finance_crawler.utils.device_health import assert_device_ready

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
    warmup_result: dict[str, Any] | None = None
    try:
        opened_url = resolve_short_url(target.link)
        for attempt in range(capture_plan.open_retries + 1):
            used_page_retry = False
            if attempt:
                _force_stop_link_app(opened_url)
            attempt_dir = output_dir if attempt == 0 else output_dir / f"retry_{attempt}"
            if attempt == 0 and _should_warm_up_antfortune_before_open(capture_plan):
                warmup_result = _warm_up_antfortune_read_count()
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
                    "warmup": warmup_result,
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
                    "warmup": warmup_result,
                    "capture_plan": capture_plan.to_json_dict(),
                    "duration": round(time.perf_counter() - started, 3),
                }
            app_recovery: dict[str, Any] | None = None
            if looks_retryable_error(records) and _should_recover_antfortune_retryable(capture_plan):
                app_recovery = _recover_antfortune_retryable_page(
                    opened_url,
                    capture_plan=capture_plan,
                    output_dir=attempt_dir / "antfortune_retryable_recovery",
                )
                if app_recovery.get("warmup"):
                    warmup_result = app_recovery.get("warmup")
                if app_recovery.get("status") == "success":
                    records = app_recovery["records"]
                    screenshot = app_recovery.get("screenshot")
                    read_count = extract_read_count_from_records(records)
                    if read_count is not None:
                        return {
                            "row_index": target.row_index,
                            "status": "success",
                            "read_count": read_count,
                            "screenshot_path": str(screenshot) if screenshot else None,
                            "opened_url": opened_url,
                            "attempts": attempt + 1,
                            "used_app_recovery": True,
                            "warmup": warmup_result,
                            "capture_plan": capture_plan.to_json_dict(),
                            "duration": round(time.perf_counter() - started, 3),
                        }
                    not_found_reason = not_found_reason_from_records(records)
                    if not_found_reason:
                        logger.warning(
                            "read count target not found after recovery row=%s attempt=%s reason=%s",
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
                            "used_app_recovery": True,
                            "warmup": warmup_result,
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
                        "warmup": warmup_result,
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
                        "warmup": warmup_result,
                        "capture_plan": capture_plan.to_json_dict(),
                        "duration": round(time.perf_counter() - started, 3),
                    }
                records = retry_records
                screenshot = retry_screenshot or screenshot
                used_page_retry = True
            if looks_retryable_error(records):
                last_error = "retryable_error_page"
                cooldown_seconds = _cool_down_retryable_page(target.row_index, attempt + 1)
                return {
                    "row_index": target.row_index,
                    "status": "error",
                    "error": last_error,
                    "screenshot_path": str(screenshot) if screenshot else latest_screenshot(output_dir),
                    "opened_url": opened_url,
                    "attempts": attempt + 1,
                    "used_page_retry": used_page_retry,
                    "cooldown_seconds": cooldown_seconds,
                    "app_recovery": app_recovery,
                    "warmup": warmup_result,
                    "capture_plan": capture_plan.to_json_dict(),
                    "duration": round(time.perf_counter() - started, 3),
                }
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
            "warmup": warmup_result,
            "capture_plan": capture_plan.to_json_dict(),
            "duration": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        logger.warning("read count crawl failed row=%s: %s", target.row_index, exc)
        return {
            "row_index": target.row_index,
            "status": "error",
            "error": str(exc),
            "warmup": warmup_result,
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


def _should_warm_up_antfortune(capture_plan: FieldCapturePlan) -> bool:
    return bool(
        Config.ANTFORTUNE_READ_COUNT_WARMUP_ENABLED
        and capture_plan.task_type == "read_count"
        and str(capture_plan.app_type or "").lower() == "antfortune"
    )


def _should_warm_up_antfortune_before_open(capture_plan: FieldCapturePlan) -> bool:
    return bool(_should_warm_up_antfortune(capture_plan) and Config.ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN)


def _should_recover_antfortune_retryable(capture_plan: FieldCapturePlan) -> bool:
    return bool(_should_warm_up_antfortune(capture_plan) and Config.ANTFORTUNE_READ_COUNT_RECOVER_ON_RETRYABLE)


def _recover_antfortune_retryable_page(
    opened_url: str,
    *,
    capture_plan: FieldCapturePlan,
    output_dir: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        serial = assert_device_ready()
        logger.warning("Ant Fortune retryable page detected; restarting app and reopening post")
        run_adb(["shell", "am", "force-stop", Config.AFWEALTH_PACKAGE], serial=serial, timeout=10)
        reset_device_session()
        time.sleep(Config.APP_RESTART_WAIT)
        warmup = _warm_up_antfortune_read_count()
        open_url(opened_url)
        device = session_device()
        summary = capture_pages(
            device=device,
            output_dir=output_dir,
            max_scrolls=capture_plan.max_scrolls,
            wait_after_open=capture_plan.wait_after_open,
            wait_after_scroll=capture_plan.wait_after_scroll,
            enable_ocr=capture_plan.enable_ocr,
            dynamic_wait=False,
            ready_timeout=capture_plan.ready_timeout,
            ready_check_interval=capture_plan.ready_check_interval,
            serial=current_serial(),
        )
        return {
            "status": "success",
            "warmup": warmup,
            "records": read_capture_records(summary),
            "screenshot": next(output_dir.glob("page_000.png"), None),
            "duration": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        logger.warning("Ant Fortune retryable recovery failed: %s", exc)
        return {
            "status": "error",
            "error": str(exc),
            "duration": round(time.perf_counter() - started, 3),
        }


def _warm_up_antfortune_read_count() -> dict[str, Any]:
    started = time.perf_counter()
    if Config.ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT <= 0:
        return {"status": "skipped", "reason": "swipe_count_disabled"}
    try:
        serial = assert_device_ready()
        component = _resolve_launcher_component(Config.AFWEALTH_PACKAGE, serial=serial)
        if component:
            run_adb(["shell", "am", "start", "-n", component], serial=serial, timeout=20)
        else:
            run_adb(
                [
                    "shell",
                    "monkey",
                    "-p",
                    Config.AFWEALTH_PACKAGE,
                    "-c",
                    "android.intent.category.LAUNCHER",
                    "1",
                ],
                serial=serial,
                timeout=20,
            )
        time.sleep(max(float(Config.ANTFORTUNE_READ_COUNT_WARMUP_WAIT_SECONDS or 0), 0.0))
        swipes = max(int(Config.ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT or 0), 0)
        for _ in range(swipes):
            run_adb(
                ["shell", "input", "swipe", "540", "1700", "540", "700", "650"],
                serial=serial,
                timeout=10,
            )
            time.sleep(max(float(Config.ANTFORTUNE_READ_COUNT_WARMUP_AFTER_SWIPE_SECONDS or 0), 0.0))
        return {
            "status": "success",
            "component": component,
            "swipes": swipes,
            "duration": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        logger.warning("Ant Fortune read-count warmup skipped: %s", exc)
        return {
            "status": "error",
            "error": str(exc),
            "duration": round(time.perf_counter() - started, 3),
        }


def _resolve_launcher_component(package_name: str, *, serial: str | None) -> str | None:
    try:
        output = run_adb(["shell", "cmd", "package", "resolve-activity", "--brief", package_name], serial=serial, timeout=10)
    except Exception as exc:
        logger.warning("resolve launcher component failed package=%s: %s", package_name, exc)
        return None
    for line in reversed([item.strip() for item in output.splitlines() if item.strip()]):
        if "/" in line and not line.startswith("priority="):
            return line
    return None


def _cool_down_retryable_page(row_index: int, attempt: int) -> float:
    cooldown_seconds = max(float(Config.DOC_LINK_READS_RETRYABLE_COOLDOWN_SECONDS or 0), 0.0)
    if cooldown_seconds <= 0:
        return 0.0
    logger.warning(
        "read count retryable page row=%s attempt=%s; cooling down %.1fs",
        row_index,
        attempt,
        cooldown_seconds,
    )
    time.sleep(cooldown_seconds)
    return cooldown_seconds


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
