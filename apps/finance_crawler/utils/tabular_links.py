"""Parse tabular link rows into crawl candidates."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawlers.registry import supported_schemes
from apps.finance_crawler.utils.link_source import detect_link_source


def _normalize_date_text(value: str) -> str:
    text = (value or "").strip()
    replacements = {
        "年": "-",
        "月": "-",
        "日": " ",
        # Keep support for a few historically mojibaked date tokens.
        "Δκ": "-",
        "ΤΒ": "-",
        "ΘΥ": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def is_supported_crawl_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.netloc)
    return parsed.scheme in supported_schemes()


def parse_sheet_date(sheet_title: str) -> tuple[int, int, int] | None:
    text = _normalize_date_text(sheet_title)

    full_date = re.search(
        r"(?P<year>20\d{2})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})",
        text,
    )
    if full_date:
        return (
            int(full_date.group("year")),
            int(full_date.group("month")),
            int(full_date.group("day")),
        )

    match = re.search(
        r"(?<!\d)(?P<month>0[1-9]|1[0-2])(?P<day>3[01]|[12]\d|0[1-9])(?!\d)",
        text,
    )
    if not match:
        match = re.search(
            r"(?P<month>0?[1-9]|1[0-2])[-/.](?P<day>3[01]|[12]\d|0?[1-9])",
            text,
        )
    if not match:
        return None

    year_match = re.search(r"(?P<year>20\d{2})", text)
    year = int(year_match.group("year")) if year_match else datetime.now().year
    return year, int(match.group("month")), int(match.group("day"))


def parse_time_from_cell(value: str) -> tuple[int, int, int] | None:
    text = _normalize_date_text(value)
    if not text:
        return None

    range_match = re.search(
        r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*(?:[-~－—–]|至|到)\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?)",
        text,
    )
    if range_match:
        parts = [int(part) for part in range_match.group("start").split(":")]
        return parts[0], parts[1], parts[2] if len(parts) == 3 else 0

    time_only_match = re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text)
    if time_only_match:
        parts = [int(part) for part in text.split(":")]
        return parts[0], parts[1], parts[2] if len(parts) == 3 else 0

    embedded_time = re.search(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?", text)
    if embedded_time:
        return (
            int(embedded_time.group("hour")),
            int(embedded_time.group("minute")),
            int(embedded_time.group("second") or 0),
        )

    return None


def is_post_market_marker(value: str) -> bool:
    text = _normalize_date_text(value)
    return any(marker in text for marker in ("盘后", "盤後"))


def _post_market_time() -> tuple[int, int, int]:
    parts = [int(part) for part in Config.QQ_POST_MARKET_TIME.split(":")]
    if len(parts) == 2:
        return parts[0], parts[1], 0
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    raise ValueError(f"invalid TENCENT_DOC_POST_MARKET_TIME: {Config.QQ_POST_MARKET_TIME}")


def parse_source_time(value: str, sheet_title: str = "") -> datetime | None:
    sheet_date = parse_sheet_date(sheet_title)
    cell_time = parse_time_from_cell(value)
    if sheet_date and cell_time:
        return datetime(*sheet_date, *cell_time)

    text = _normalize_date_text(value)
    if not text:
        return None
    if sheet_date and is_post_market_marker(text):
        return datetime(*sheet_date, *_post_market_time())
    if sheet_date and re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        parts = [int(part) for part in text.split(":")]
        return datetime(*sheet_date, parts[0], parts[1], parts[2] if len(parts) == 3 else 0)

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    match = re.search(r"(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})", text)
    if match:
        month, day, hour, minute = map(int, match.groups())
        now = datetime.now()
        return datetime(now.year, month, day, hour, minute)

    return None


def eligible_candidates(
    rows: list[list[str]],
    start_row: int,
    sheet_title: str = "",
    *,
    source_time_col: int = Config.QQ_COL_POST_TIME,
    url_col: int = Config.QQ_COL_URL,
) -> list[dict[str, Any]]:
    now = datetime.now()
    cutoff = now - timedelta(hours=Config.INITIAL_CHECK_DELAY_HOURS)
    candidates: list[dict[str, Any]] = []

    for offset, row in enumerate(rows[1:], start=1):
        row_index = start_row + offset + 1
        if len(row) <= max(url_col, source_time_col):
            continue

        url = row[url_col].strip()
        source_time_text = row[source_time_col]
        detail_only = is_post_market_marker(source_time_text)
        source_time = parse_source_time(source_time_text, sheet_title)
        if not url or not source_time:
            continue
        if not is_supported_crawl_url(url):
            continue
        if Config.FETCH_ONLY_ELIGIBLE and source_time > cutoff:
            continue

        candidates.append(
            {
                "url": url,
                "source_app": detect_link_source(url),
                "source_time": source_time,
                "source_time_text": source_time_text,
                "detail_only": detail_only,
                "row_index": row_index,
                "age_hours": round((now - source_time).total_seconds() / 3600, 2),
            }
        )

    return candidates


def save_latest_candidates(candidates: list[dict[str, Any]]) -> None:
    serializable = []
    for item in candidates:
        copied = dict(item)
        source_time = copied.get("source_time")
        if hasattr(source_time, "isoformat"):
            copied["source_time"] = source_time.isoformat(sep=" ")
        serializable.append(copied)

    Config.LATEST_CANDIDATES_FILE.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
