"""Classify share links by target app/source."""

from __future__ import annotations

from apps.finance_crawler.crawlers.constants import (
    SOURCE_ALIPAY,
    SOURCE_ANTFORTUNE,
    SOURCE_TENPAY,
    SOURCE_UNKNOWN,
)


def detect_link_source(url: str) -> str:
    from apps.finance_crawler.crawlers.registry import detect_source_app

    return detect_source_app(url)


def resolve_source_app(stored_source_app: str | None, url: str) -> str:
    detected = detect_link_source(url)
    if detected != SOURCE_UNKNOWN:
        return detected

    source_app = (stored_source_app or "").strip()
    if source_app and source_app != SOURCE_UNKNOWN:
        return source_app
    return SOURCE_UNKNOWN
