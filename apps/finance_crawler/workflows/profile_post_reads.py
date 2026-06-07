"""Crawl dated post read counts from profile homepages."""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.mobile.capture_engine import (
    collect_ui_records,
    run_adb,
    save_screenshot,
    scroll_forward,
    try_ocr,
)
from apps.finance_crawler.mobile.device_session import device as session_device
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.mobile.read_count_parser import extract_read_count_from_texts
from apps.finance_crawler.crawler_app.storage.profile_metrics import (
    get_profile_targets_for_post_reads,
    update_profile_post_read_metric,
)
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.workflows.profile_metrics import (
    _is_device_unavailable_error,
    _max_consecutive_device_errors,
    _open_profile_url,
)

logger = get_logger("profile_post_reads")


def crawl_profile_post_reads(
    limit: int | None = None,
    target_date: date | None = None,
    *,
    source_name: str | None = None,
) -> list[dict[str, Any]]:
    metric_date = target_date or _configured_metric_date() or date.today()
    resolved_limit = limit if limit is not None else Config.PROFILE_POST_READ_CRAWL_LIMIT
    records = get_profile_targets_for_post_reads(
        limit=resolved_limit or None,
        metric_date=metric_date,
        source_name=source_name,
    )
    if not records:
        logger.info("profile post read crawl skipped: no profile targets date=%s", metric_date)
        return []

    try:
        serial = assert_device_ready()
    except DeviceUnavailable:
        reset_device_session()
        raise

    results = []
    consecutive_device_errors = 0
    max_device_errors = _max_consecutive_device_errors()
    for index, record in enumerate(records, start=1):
        logger.info(
            "profile post read crawl %s/%s row=%s account=%s date=%s",
            index,
            len(records),
            record.get("source_locator", {}).get("row_index"),
            record.get("account_name"),
            metric_date,
        )
        result = _crawl_one_profile(record, metric_date=metric_date, serial=serial)
        results.append(result)
        if _is_device_unavailable_error(result.get("error")):
            consecutive_device_errors += 1
            if consecutive_device_errors >= max_device_errors:
                reset_device_session()
                raise DeviceUnavailable(
                    "profile post read crawl stopped after %s consecutive device errors: %s"
                    % (consecutive_device_errors, result.get("error"))
                )
        else:
            consecutive_device_errors = 0
    return results


def infer_post_date(text: str, *, now: datetime) -> date | None:
    cleaned = re.sub(r"\s+", "", text or "")
    if not cleaned:
        return None
    if "\u521a\u521a" in cleaned or re.search(r"\d+\u5206\u949f\u524d", cleaned):
        return now.date()
    hour_match = re.search(r"(?P<hours>\d+)\u5c0f\u65f6\u524d", cleaned)
    if hour_match:
        return (now - timedelta(hours=int(hour_match.group("hours")))).date()
    if "\u6628\u5929" in cleaned:
        return now.date() - timedelta(days=1)
    if "\u524d\u5929" in cleaned:
        return now.date() - timedelta(days=2)
    full_match = re.search(r"(?P<year>20\d{2})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})", cleaned)
    if full_match:
        return _safe_date(
            int(full_match.group("year")),
            int(full_match.group("month")),
            int(full_match.group("day")),
        )
    short_match = re.search(r"(?<!\d)(?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)", cleaned)
    if short_match:
        return _safe_date(now.year, int(short_match.group("month")), int(short_match.group("day")))
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _crawl_one_profile(record: dict[str, Any], *, metric_date: date, serial: str) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir = Config.CAPTURE_DIR / (
        "profile_post_reads_%s_%s" % (record["target_id"], datetime.now().strftime("%Y%m%d_%H%M%S"))
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    posts: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    error = None

    try:
        _dismiss_system_overlays(serial)
        _force_stop_profile_app(record.get("app_type"), serial=serial)
        _open_profile_url(str(record["homepage_url"]), source_app=record.get("app_type"))
        time.sleep(max(Config.PAGE_LOAD_WAIT, 4.0))
        _dismiss_system_overlays(serial)
        device = session_device()
        now = datetime.now()
        stop_after_page = False

        for page_index in range(Config.PROFILE_POST_READ_MAX_SCROLLS + 1):
            page = _capture_screen(device, output_dir, f"profile_{page_index:03d}", serial=serial)
            candidates = _extract_post_candidates(page["records"], now=now)
            logger.info(
                "profile post list page=%s account=%s candidates=%s",
                page_index,
                record.get("account_name"),
                len(candidates),
            )
            for candidate in candidates:
                post_date = candidate.get("post_date")
                if post_date is None:
                    continue
                if post_date < metric_date:
                    stop_after_page = True
                    continue
                if post_date != metric_date:
                    continue
                key = _candidate_key(candidate)
                if key in seen_keys:
                    continue
                if len(posts) >= Config.PROFILE_POST_READ_MAX_POSTS:
                    stop_after_page = True
                    break
                seen_keys.add(key)
                post = _open_candidate_and_read(
                    candidate,
                    metric_date=metric_date,
                    output_dir=output_dir,
                    serial=serial,
                    device=device,
                    now=now,
                )
                if post:
                    posts.append(post)

            if stop_after_page:
                break
            moved = scroll_forward(device, serial=serial)
            if not moved:
                break
            time.sleep(Config.DETAIL_SCROLL_WAIT)

        total_read_count = sum(int(post.get("read_count") or 0) for post in posts)
        screenshot_path = posts[0].get("screenshot_path") if posts else str(output_dir / "profile_000.png")
        metric_id = update_profile_post_read_metric(
            target_id=int(record["target_id"]),
            metric_date=metric_date,
            app_type=str(record.get("app_type") or "unknown"),
            homepage_url=str(record["homepage_url"]),
            read_count=total_read_count,
            posts=posts,
            screenshot_path=screenshot_path,
        )
        return {
            "metric_id": metric_id,
            "target_id": record["target_id"],
            "account_name": record.get("account_name"),
            "metric_date": metric_date.isoformat(),
            "status": "success",
            "post_count": len(posts),
            "read_count": total_read_count,
            "duration": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        error = str(exc)
        logger.warning("profile post read crawl failed target=%s: %s", record.get("target_id"), exc)
        metric_id = update_profile_post_read_metric(
            target_id=int(record["target_id"]),
            metric_date=metric_date,
            app_type=str(record.get("app_type") or "unknown"),
            homepage_url=str(record["homepage_url"]),
            read_count=None,
            posts=posts,
            error=error,
        )
        return {
            "metric_id": metric_id,
            "target_id": record["target_id"],
            "account_name": record.get("account_name"),
            "metric_date": metric_date.isoformat(),
            "status": "error",
            "post_count": len(posts),
            "read_count": None,
            "error": error,
            "duration": round(time.perf_counter() - started, 3),
        }


def _open_candidate_and_read(
    candidate: dict[str, Any],
    *,
    metric_date: date,
    output_dir: Path,
    serial: str,
    device: Any,
    now: datetime,
) -> dict[str, Any] | None:
    bounds = candidate.get("tap_bounds") or {}
    x = int((bounds.get("left", 0) + bounds.get("right", 0)) / 2)
    y = int((bounds.get("top", 0) + bounds.get("bottom", 0)) / 2)
    if x <= 0 or y <= 0:
        return None

    run_adb(["shell", "input", "tap", str(x), str(y)], serial=serial, timeout=10)
    time.sleep(max(Config.PAGE_LOAD_WAIT, 3.0))
    detail_index = len(list(output_dir.glob("detail_*.png")))
    detail = _capture_screen(device, output_dir, f"detail_{detail_index:03d}", serial=serial)
    texts = [str(row.get("text") or "").strip() for row in detail["records"] if str(row.get("text") or "").strip()]
    detail_date = _first_post_date(texts, now=now) or candidate.get("post_date")
    read_count = extract_read_count_from_texts(texts)
    run_adb(["shell", "input", "keyevent", "BACK"], serial=serial, timeout=10)
    time.sleep(1.5)

    if detail_date != metric_date or read_count is None:
        return None
    return {
        "title": candidate.get("title") or _extract_title_from_detail(texts),
        "list_time_text": candidate.get("time_text"),
        "detail_date": detail_date.isoformat(),
        "read_count": read_count,
        "screenshot_path": str(detail["screenshot_path"]),
    }


def _capture_screen(device: Any, output_dir: Path, stem: str, *, serial: str) -> dict[str, Any]:
    xml_text = device.dump_hierarchy(compressed=False, pretty=True)
    xml_path = output_dir / f"{stem}.xml"
    screenshot_path = output_dir / f"{stem}.png"
    xml_path.write_text(xml_text, encoding="utf-8")
    save_screenshot(device, screenshot_path, serial=serial)

    records = collect_ui_records(xml_text, 0)
    ocr_records = try_ocr(screenshot_path) or []
    for row in ocr_records:
        row["source"] = "ocr"
    for row in records:
        row["source"] = "ui"
    all_records = [*records, *_normalize_ocr_records(ocr_records)]
    (output_dir / f"{stem}.json").write_text(
        json.dumps(all_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"records": all_records, "screenshot_path": screenshot_path}


def _normalize_ocr_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for record in records:
        item = dict(record)
        bounds = dict(item.get("bounds") or {})
        if "right" not in bounds:
            bounds["right"] = int(bounds.get("left", 0)) + int(bounds.get("width", 0))
        if "bottom" not in bounds:
            bounds["bottom"] = int(bounds.get("top", 0)) + int(bounds.get("height", 0))
        item["bounds"] = bounds
        normalized.append(item)
    return normalized


def _extract_post_candidates(records: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    text_records = _visible_text_records(records, app_only=True)
    candidates = []
    for record in text_records:
        text = str(record.get("text") or "").strip()
        post_date = infer_post_date(text, now=now)
        if post_date is None:
            continue
        bounds = record.get("bounds") or {}
        if int(bounds.get("top", 0)) < 900:
            continue
        title_record = _find_title_after(text_records, bounds)
        tap_record = title_record or record
        candidates.append(
            {
                "time_text": text,
                "post_date": post_date,
                "title": str((title_record or {}).get("text") or "").strip(),
                "tap_bounds": tap_record.get("bounds") or bounds,
                "top": int(bounds.get("top", 0)),
            }
        )
    candidates.sort(key=lambda item: int(item.get("top") or 0))
    return _dedupe_candidates(candidates)


def _visible_text_records(records: list[dict[str, Any]], *, app_only: bool = False) -> list[dict[str, Any]]:
    output = []
    for record in records:
        text = str(record.get("text") or "").strip()
        bounds = record.get("bounds") or {}
        if not text or not isinstance(bounds, dict):
            continue
        if app_only and record.get("source") != "ui":
            continue
        if app_only and str(record.get("package") or "") not in {Config.ALIPAY_PACKAGE, Config.AFWEALTH_PACKAGE}:
            continue
        if int(bounds.get("width", 1)) <= 0 or int(bounds.get("height", 1)) <= 0:
            continue
        output.append(record)
    output.sort(key=lambda item: (int((item.get("bounds") or {}).get("top", 0)), int((item.get("bounds") or {}).get("left", 0))))
    return output


def _find_title_after(records: list[dict[str, Any]], time_bounds: dict[str, Any]) -> dict[str, Any] | None:
    time_top = int(time_bounds.get("top", 0))
    time_bottom = int(time_bounds.get("bottom", 0))
    candidates: list[dict[str, Any]] = []
    for record in records:
        bounds = record.get("bounds") or {}
        top = int(bounds.get("top", 0))
        if top <= time_bottom or top > time_top + 420:
            continue
        text = str(record.get("text") or "").strip()
        if _looks_like_post_title(text):
            candidates.append(record)
    if not candidates:
        return None
    first_top = min(int((item.get("bounds") or {}).get("top", 0)) for item in candidates)
    first_title_group = [
        item for item in candidates if int((item.get("bounds") or {}).get("top", 0)) <= first_top + 90
    ]
    return max(first_title_group, key=lambda item: len(str(item.get("text") or "").strip()))


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        top = int(candidate.get("top") or 0)
        title = _normalize_title(str(candidate.get("title") or ""))
        duplicate_index = None
        for index, existing in enumerate(deduped):
            existing_top = int(existing.get("top") or 0)
            existing_title = _normalize_title(str(existing.get("title") or ""))
            if candidate.get("post_date") != existing.get("post_date"):
                continue
            if abs(top - existing_top) <= 90:
                duplicate_index = index
                break
            if title and existing_title and (title in existing_title or existing_title in title):
                duplicate_index = index
                break
        if duplicate_index is None:
            deduped.append(candidate)
            continue
        existing = deduped[duplicate_index]
        if len(str(candidate.get("title") or "")) > len(str(existing.get("title") or "")):
            deduped[duplicate_index] = candidate
    return deduped


def _normalize_title(text: str) -> str:
    return re.sub(r"\W+", "", text or "")


def _looks_like_post_title(text: str) -> bool:
    if len(text) < 6 or len(text) > 120:
        return False
    noise = (
        "\u7c89\u4e1d",
        "\u83b7\u8d5e",
        "\u5206\u4eab",
        "\u8bc4\u8bba",
        "\u9605\u8bfb",
        "\u5b9e\u76d8\u7b14\u8bb0",
        "\u64cd\u4f5c\u7b14\u8bb0",
    )
    return not any(word in text for word in noise)


def _candidate_key(candidate: dict[str, Any]) -> str:
    return "%s|%s|%s" % (
        candidate.get("post_date"),
        candidate.get("time_text") or "",
        candidate.get("title") or "",
    )


def _first_post_date(texts: list[str], *, now: datetime) -> date | None:
    for text in texts:
        inferred = infer_post_date(text, now=now)
        if inferred is not None:
            return inferred
    return None


def _extract_title_from_detail(texts: list[str]) -> str:
    for text in texts:
        cleaned = text.strip()
        if _looks_like_post_title(cleaned):
            return cleaned
    return ""


def _configured_metric_date() -> date | None:
    if not Config.PROFILE_METRICS_TARGET_DATE:
        return None
    return date.fromisoformat(Config.PROFILE_METRICS_TARGET_DATE)


def _force_stop_profile_app(app_type: Any, *, serial: str) -> None:
    source_app = str(app_type or "").lower()
    if source_app == "antfortune":
        package_name = Config.AFWEALTH_PACKAGE
    elif source_app == "tenpay":
        package_name = Config.TENPAY_PACKAGE
    else:
        package_name = Config.ALIPAY_PACKAGE
    run_adb(["shell", "am", "force-stop", package_name], serial=serial, timeout=10)
    reset_device_session()
    time.sleep(Config.APP_RESTART_WAIT)


def _dismiss_system_overlays(serial: str) -> None:
    for args in (
        ["shell", "cmd", "statusbar", "collapse"],
        ["shell", "input", "keyevent", "BACK"],
    ):
        try:
            run_adb(args, serial=serial, timeout=5)
        except Exception:
            continue
    time.sleep(0.5)
