"""Shared error classification for crawler_app workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.utils.device_health import DeviceUnavailable


DEVICE_UNAVAILABLE = "device_unavailable"
LOGIN_REQUIRED = "login_required"
PAGE_NOT_FOUND = "page_not_found"
RENDER_NOT_READY = "render_not_ready"
FIELD_NOT_DETECTED = "field_not_detected"
EVIDENCE_REJECTED = "evidence_rejected"
WRITEBACK_LOCATE_FAILED = "writeback_locate_failed"
UNKNOWN_ERROR = "unknown_error"


@dataclass(frozen=True, slots=True)
class ClassifiedError:
    kind: str
    retryable: bool
    terminal: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "retryable": self.retryable,
            "terminal": self.terminal,
        }


def classify_crawl_error(error: Any, *, status: str | None = None, page_state: str | None = None) -> ClassifiedError:
    text = str(error or status or "").strip().lower()
    state = str(page_state or "").strip().lower()
    normalized_status = str(status or "").strip().lower()

    if isinstance(error, DeviceUnavailable) or _contains_any(
        text,
        (
            "adb device",
            "no adb device",
            "configured device not found",
            "multiple adb devices",
            "device unauthorized",
            "device not ready",
            "device offline",
            "uiautomator2 device session is unavailable",
            "adb shell unavailable",
            "device check timed out",
        ),
    ):
        return ClassifiedError(DEVICE_UNAVAILABLE, retryable=True)

    if state == "login_required" or _contains_any(text, ("login", "\u767b\u5f55", "password")):
        return ClassifiedError(LOGIN_REQUIRED, retryable=False, terminal=False)

    if normalized_status in {"not_found", "deleted"} or _contains_any(
        text,
        (
            "not_found",
            "content deleted",
            "content missing",
            "\u5185\u5bb9\u4e0d\u89c1",
            "\u5185\u5bb9\u4e0d\u5b58\u5728",
        ),
    ):
        return ClassifiedError(PAGE_NOT_FOUND, retryable=False, terminal=True)

    if state == "loading" or _contains_any(text, ("blank_page", "render", "not ready", "loading", "title only")):
        return ClassifiedError(RENDER_NOT_READY, retryable=True)

    if _contains_any(
        text,
        (
            "not detected",
            "not found",
            "read_count_not_found",
            "account name was not detected",
            "profile fans count was not detected",
        ),
    ):
        return ClassifiedError(FIELD_NOT_DETECTED, retryable=True)

    if _contains_any(
        text,
        (
            "evidence",
            "not tied to expected profile",
            "abbreviated fans count requires exact detail page",
            "adjacent",
        ),
    ):
        return ClassifiedError(EVIDENCE_REJECTED, retryable=False)

    return ClassifiedError(UNKNOWN_ERROR, retryable=True)


def classify_writeback_error(error: Any) -> ClassifiedError:
    text = str(error or "").strip().lower()
    if _contains_any(text, ("not found in current sheet", "duplicate", "not mapped", "missing post_url", "locate")):
        return ClassifiedError(WRITEBACK_LOCATE_FAILED, retryable=False)
    return ClassifiedError(UNKNOWN_ERROR, retryable=True)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker and marker in text for marker in markers)
