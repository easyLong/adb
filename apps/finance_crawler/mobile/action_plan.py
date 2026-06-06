"""ADB capture action plans shared by mobile crawlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ACTION_OPEN_LINK = "open_link"
ACTION_UI_CONTROLS = "ui_controls"
ACTION_SCREENSHOT = "screenshot"
ACTION_OCR = "ocr"
ACTION_TAP_RETRY = "tap_retry"
ACTION_SCROLL = "scroll"
ACTION_CLICK_DETAIL = "click_detail"


@dataclass(frozen=True, slots=True)
class FieldCapturePlan:
    task_type: str
    app_type: str
    fields: tuple[str, ...]
    actions: tuple[str, ...]
    max_scrolls: int = 0
    wait_after_open: float = 3.0
    wait_after_scroll: float = 0.0
    open_retries: int = 0
    ready_timeout: float = 0.0
    ready_check_interval: float = 0.5
    stop_when_fields_found: bool = True

    @property
    def enable_ocr(self) -> bool:
        return ACTION_OCR in self.actions

    @property
    def allow_tap_retry(self) -> bool:
        return ACTION_TAP_RETRY in self.actions

    @property
    def allow_scroll(self) -> bool:
        return ACTION_SCROLL in self.actions and self.max_scrolls > 0

    @property
    def complexity(self) -> str:
        return capture_complexity(self.actions)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "app_type": self.app_type,
            "fields": list(self.fields),
            "actions": list(self.actions),
            "max_scrolls": self.max_scrolls,
            "wait_after_open": self.wait_after_open,
            "wait_after_scroll": self.wait_after_scroll,
            "open_retries": self.open_retries,
            "ready_timeout": self.ready_timeout,
            "ready_check_interval": self.ready_check_interval,
            "stop_when_fields_found": self.stop_when_fields_found,
            "complexity": self.complexity,
        }


def capture_complexity(actions: tuple[str, ...]) -> str:
    action_set = set(actions)
    if ACTION_CLICK_DETAIL in action_set:
        return "click_detail"
    if ACTION_TAP_RETRY in action_set:
        return "interactive_retry"
    if ACTION_SCROLL in action_set:
        return "scroll_capture"
    if ACTION_OCR in action_set:
        return "ui_ocr_capture"
    return "ui_capture"
