"""Read-count extraction and terminal page-state detection."""

from __future__ import annotations

import re
from typing import Any

from apps.finance_crawler.mobile.page_status import detect_page_status_from_texts, records_to_texts
from apps.finance_crawler.mobile.parsers import normalize_count_text, parse_count_token


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
        value = parse_standalone_count(cleaned[index - 1])
        if value is None:
            continue
        found = True
        best = max(best, value)

    return best if found else None


def not_found_reason_from_records(records: list[dict[str, Any]]) -> str | None:
    status, reason = detect_page_status_from_texts(records_to_texts(records, min_length=1))
    if status == "not_found":
        return reason or "not_found"
    return None


def looks_blank(records: list[dict[str, Any]]) -> bool:
    useful_texts = []
    ignored_texts = {"\u9875?", "\u8fd4\u56de", "\u6924?", "\u6769\u65bf\u6d16"}
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
        if text and text not in ignored_texts:
            useful_texts.append(text)
        if desc and desc not in ignored_texts:
            useful_texts.append(desc)
    return len(useful_texts) <= 1


def looks_retryable_error(records: list[dict[str, Any]]) -> bool:
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


def parse_standalone_count(text: str) -> int | None:
    cleaned = re.sub(r"\s+", "", text.replace(",", ""))
    if not re.fullmatch(r"\d+(?:\.\d+)?[\u4e07wWkK\u5343]?", cleaned):
        return None
    return parse_count_token(cleaned)
