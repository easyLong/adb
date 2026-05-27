"""ADB crawler wrapper built on the validated capture flow."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from apps.finance_crawler.crawlers import AppCrawlerAdapter, CapturePlan, CrawlAdapterContext, get_app_adapter
from apps.finance_crawler.mobile.capture_engine import (
    append_jsonl,
    collect_ui_records,
    save_screenshot,
    scroll_forward,
    try_ocr,
)
from apps.finance_crawler.mobile import parsers as community_parsers
from apps.finance_crawler.mobile.device_session import (
    current_serial,
    device,
    open_url,
    reset_device_session,
    resolve_short_url,
)
from apps.finance_crawler.mobile.page_status import detect_page_status_from_texts, records_to_texts
from apps.finance_crawler.mobile.post_capture import capture_post_pages
from apps.finance_crawler.config import Config
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("crawler")


def _dump_records() -> list[dict[str, Any]]:
    xml_text = device().dump_hierarchy(compressed=False)
    return collect_ui_records(xml_text, 0)


def read_texts_from_screen() -> list[str]:
    return records_to_texts(_dump_records(), min_length=2)


def detect_page_status() -> tuple[str, str | None]:
    return detect_page_status_from_texts(read_texts_from_screen())


def extract_account_name(texts: list[str]) -> str:
    return community_parsers.extract_account_name(texts)


def check_post_exists_and_account(post_id: int) -> dict[str, Any]:
    time.sleep(1.0)
    status, error_msg = detect_page_status()
    if status == "not_found":
        return {"status": "not_found", "exists": False, "account_name": None, "error": error_msg}
    if status == "error":
        return {"status": "error", "exists": False, "account_name": None, "error": error_msg}

    texts = read_texts_from_screen()
    account_name = extract_account_name(texts)
    return {"status": "success", "exists": True, "account_name": account_name, "error": None}


def take_screenshot(post_id: int) -> str | None:
    path = Config.SCREENSHOT_DIR / f"post_{post_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    try:
        current_device = device()
        save_screenshot(current_device, path, serial=current_serial())
        return str(path)
    except Exception as exc:
        logger.warning("screenshot failed: %s", exc)
        return None


def extract_post_content(texts: list[str]) -> str:
    return community_parsers.extract_post_content(texts)


def parse_numbers_with_presence(texts: list[str]) -> tuple[int, int, bool, bool]:
    return community_parsers.parse_numbers_with_presence(texts)


def parse_numbers(texts: list[str]) -> tuple[int, int]:
    read_count, comment_count, _, _ = parse_numbers_with_presence(texts)
    return read_count, comment_count


def _capture_ocr_snapshot(output_dir: Path, name: str) -> list[dict[str, Any]]:
    screenshot_path = output_dir / f"{name}.png"
    current_device = device()
    save_screenshot(current_device, screenshot_path, serial=current_serial())
    rows = try_ocr(screenshot_path) or []
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        if float(row.get("confidence", -1)) < Config.OCR_MIN_CONFIDENCE:
            continue
        row["screenshot"] = screenshot_path.name
        filtered_rows.append(row)
    if filtered_rows:
        append_jsonl(output_dir / "adapter_ocr_records.jsonl", filtered_rows)
    return filtered_rows


def _parse_counts_with_adapter(
    app_adapter: AppCrawlerAdapter,
    texts: list[str],
) -> tuple[int, int, bool, bool]:
    try:
        parsed = app_adapter.parse_counts(texts)
    except Exception as exc:
        logger.warning("app adapter count parser failed source=%s: %s", app_adapter.source_app, exc)
        parsed = None
    return parsed or parse_numbers_with_presence(texts)


def _adapter_before_main_capture(
    app_adapter: AppCrawlerAdapter,
    context: CrawlAdapterContext,
) -> dict[str, Any]:
    try:
        return app_adapter.before_main_capture(context)
    except Exception as exc:
        logger.exception("app adapter before-main hook failed source=%s", app_adapter.source_app)
        return {"error": str(exc), "adapter_error": str(exc)}


def _adapter_extract_content(app_adapter: AppCrawlerAdapter, texts: list[str]) -> str | None:
    try:
        return app_adapter.extract_content(texts)
    except Exception as exc:
        logger.warning("app adapter content parser failed source=%s: %s", app_adapter.source_app, exc)
        return None


def _adapter_extract_account_name(app_adapter: AppCrawlerAdapter, texts: list[str]) -> str | None:
    try:
        return app_adapter.extract_account_name(texts)
    except Exception as exc:
        logger.warning("app adapter account parser failed source=%s: %s", app_adapter.source_app, exc)
        return None


def _adapter_result_fields(
    app_adapter: AppCrawlerAdapter,
    *,
    account_name: str,
    comment_count: int,
    adapter_data: dict[str, Any],
) -> dict[str, Any]:
    try:
        result_fields = app_adapter.result_fields(
            account_name=account_name,
            comment_count=comment_count,
            adapter_data=adapter_data,
        )
    except Exception as exc:
        logger.exception("app adapter result builder failed source=%s", app_adapter.source_app)
        return {"app_metrics": {"adapter_error": str(exc)}}

    if adapter_data.get("adapter_error"):
        app_metrics = dict(result_fields.get("app_metrics") or {})
        app_metrics["adapter_error"] = adapter_data["adapter_error"]
        result_fields["app_metrics"] = app_metrics
    return result_fields


def _adapter_capture_plan(app_adapter: AppCrawlerAdapter) -> CapturePlan:
    try:
        return app_adapter.capture_plan()
    except Exception as exc:
        logger.warning("app adapter capture plan failed source=%s: %s", app_adapter.source_app, exc)
        from apps.finance_crawler.crawlers.base import DefaultCrawlerAdapter

        return DefaultCrawlerAdapter().capture_plan()


def scrape_post_content(post_id: int, source_app: str | None = None) -> dict[str, Any]:
    output_dir = Config.CAPTURE_DIR / f"post_{post_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    result: dict[str, Any] = {
        "status": "error",
        "content": None,
        "read_count": 0,
        "comment_count": 0,
        "screenshot_path": None,
        "error": None,
    }

    status, error_msg = detect_page_status()
    if status == "not_found":
        result.update({"status": "deleted", "error": error_msg})
        return result
    if status == "error":
        result.update({"status": "error", "error": error_msg})
        return result

    app_adapter = get_app_adapter(source_app)
    capture_plan = _adapter_capture_plan(app_adapter)
    adapter_data = _adapter_before_main_capture(
        app_adapter,
        CrawlAdapterContext(
            source_app=source_app,
            output_dir=output_dir,
            capture_ocr_snapshot=_capture_ocr_snapshot,
            device=device,
            scroll_forward=scroll_forward,
            scroll_wait=capture_plan.scroll_wait,
            max_detail_scrolls=capture_plan.max_detail_scrolls,
        )
    )
    current_device = device()
    summary = capture_post_pages(
        post_id=post_id,
        output_dir=output_dir,
        app_adapter=app_adapter,
        capture_plan=capture_plan,
        device=current_device,
        serial=current_serial(),
        parse_counts=lambda texts: _parse_counts_with_adapter(app_adapter, texts),
    )
    texts = summary["texts"]
    read_count = summary["read_count"]
    comment_count = summary["comment_count"]
    content = _adapter_extract_content(app_adapter, texts) or extract_post_content(texts)
    account_name = _adapter_extract_account_name(app_adapter, texts) or extract_account_name(texts)
    app_result_fields = _adapter_result_fields(
        app_adapter,
        account_name=account_name,
        comment_count=comment_count,
        adapter_data=adapter_data,
    )
    if not content and not summary["read_found"] and not summary["comment_found"]:
        result.update(
            {
                "status": "error",
                "error": "post content was not detected; page may be blank or not the target post",
                "capture_pages": summary["pages_captured"],
                "read_found": summary["read_found"],
                "comment_found": summary["comment_found"],
                "ocr_attempted": summary["ocr_attempted"],
                "ocr_available": summary["ocr_available"],
                "ocr_records": summary["ocr_records"],
            }
        )
        return result
    screenshot = next(output_dir.glob("page_000.png"), None)
    result.update(
        {
            "status": "success",
            "account_name": account_name,
            "content": content,
            "read_count": read_count,
            "comment_count": comment_count,
            "screenshot_path": str(screenshot) if screenshot else None,
            "capture_pages": summary["pages_captured"],
            "read_found": summary["read_found"],
            "comment_found": summary["comment_found"],
            "ocr_attempted": summary["ocr_attempted"],
            "ocr_available": summary["ocr_available"],
            "ocr_records": summary["ocr_records"],
            **app_result_fields,
        }
    )
    return result
