"""Common record page capture loop driven by an app-specific CapturePlan."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apps.finance_crawler.crawlers import AppCrawlerAdapter, CapturePlan
from apps.finance_crawler.mobile.capture_engine import (
    append_jsonl,
    collect_ui_records,
    current_screen_signature,
    save_screenshot,
    save_text,
    scroll_forward,
    stable_key,
    try_ocr,
)
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("record_capture")

CountParser = Callable[[list[str]], tuple[int, int, bool, bool]]


def _record_texts(records: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for record in records:
        if record.get("package") == "com.android.systemui":
            continue
        for key in ("text", "content_desc"):
            value = (record.get(key) or "").strip()
            if value:
                texts.append(value)
    return texts


def capture_record_pages(
    *,
    record_id: int,
    output_dir: Path,
    app_adapter: AppCrawlerAdapter,
    capture_plan: CapturePlan,
    device: Any,
    serial: str | None,
    parse_counts: CountParser,
) -> dict[str, Any]:
    """Capture screenshots/XML/OCR according to the app capture plan."""

    output_dir.mkdir(parents=True, exist_ok=True)
    ui_jsonl = output_dir / "ui_records.jsonl"
    ocr_jsonl = output_dir / "ocr_records.jsonl"
    seen_record_keys: set[str] = set()
    seen_screen_signatures: set[str] = set()
    all_texts: list[str] = []
    total_ui_records = 0
    total_ocr_records = 0
    pages_captured = 0
    read_count = 0
    comment_count = 0
    read_found = False
    comment_found = False
    ocr_attempted = False
    ocr_available = None

    max_pages = max(1, capture_plan.max_pages)

    for page_index in range(max_pages):
        xml_text = device.dump_hierarchy(compressed=False, pretty=True)
        signature = current_screen_signature(xml_text)

        xml_path = output_dir / f"page_{page_index:03d}.xml"
        screenshot_path = output_dir / f"page_{page_index:03d}.png"
        save_text(xml_path, xml_text)
        save_screenshot(device, screenshot_path, serial=serial)
        pages_captured += 1

        records = collect_ui_records(xml_text, page_index)
        new_records = []
        for record in records:
            key = stable_key(record)
            if key in seen_record_keys:
                continue
            seen_record_keys.add(key)
            new_records.append(record)

        append_jsonl(ui_jsonl, new_records)
        total_ui_records += len(new_records)
        all_texts.extend(_record_texts(new_records))

        read_count, comment_count, read_found, comment_found = parse_counts(all_texts)
        should_try_ocr = capture_plan.enable_ocr and ocr_available is not False
        if capture_plan.stop_when_counts_found and read_found and comment_found:
            should_try_ocr = False
        if should_try_ocr:
            ocr_attempted = True
            ocr_records = try_ocr(screenshot_path)
            if ocr_records is None:
                ocr_available = False
            else:
                ocr_available = True
                filtered_ocr = []
                for row in ocr_records:
                    if float(row.get("confidence", -1)) < capture_plan.ocr_min_confidence:
                        continue
                    bounds = row.get("bounds") or {}
                    if int(bounds.get("top") or 0) < capture_plan.ocr_min_top:
                        continue
                    row["page_index"] = page_index
                    row["screenshot"] = screenshot_path.name
                    filtered_ocr.append(row)
                append_jsonl(ocr_jsonl, filtered_ocr)
                total_ocr_records += len(filtered_ocr)
                all_texts.extend(row["text"] for row in filtered_ocr)
                read_count, comment_count, read_found, comment_found = parse_counts(all_texts)

        logger.info(
            "record capture source=%s record=%s page=%s/%s ui_new=%s ocr_total=%s read_found=%s comment_found=%s",
            app_adapter.source_app,
            record_id,
            page_index + 1,
            max_pages,
            len(new_records),
            total_ocr_records,
            read_found,
            comment_found,
        )
        if capture_plan.stop_when_counts_found and read_found and comment_found:
            break
        if page_index >= max_pages - 1:
            break
        if capture_plan.stop_on_repeated_screen and signature in seen_screen_signatures:
            logger.info("record capture stopped: repeated screen record=%s", record_id)
            break
        seen_screen_signatures.add(signature)
        if not scroll_forward(device, serial=serial):
            logger.info("record capture stopped: no more scrollable content record=%s", record_id)
            break
        time.sleep(capture_plan.scroll_wait)

    return {
        "output_dir": str(output_dir),
        "ui_records": total_ui_records,
        "ocr_records": total_ocr_records,
        "ui_jsonl": str(ui_jsonl),
        "ocr_jsonl": str(ocr_jsonl) if ocr_jsonl.exists() else None,
        "texts": all_texts,
        "read_count": read_count,
        "comment_count": comment_count,
        "read_found": read_found,
        "comment_found": comment_found,
        "pages_captured": pages_captured,
        "ocr_attempted": ocr_attempted,
        "ocr_available": ocr_available,
    }
