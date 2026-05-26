"""Classify share links by target app/source."""

from __future__ import annotations

from urllib.parse import urlparse


SOURCE_ALIPAY = "alipay"
SOURCE_ANTFORTUNE = "antfortune"
SOURCE_TENPAY = "tenpay"
SOURCE_UNKNOWN = "unknown"


def detect_link_source(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()

    if scheme == "afwealth" or host.endswith("think.klv5qu.com"):
        return SOURCE_ANTFORTUNE

    if scheme in {"tenpay", "tencentwm"} or host.endswith("tencentwm.com"):
        return SOURCE_TENPAY

    if scheme in {"alipay", "alipays", "alipaylite", "alipaytoken"}:
        return SOURCE_ALIPAY

    if "alipay" in host:
        return SOURCE_ALIPAY

    return SOURCE_UNKNOWN
