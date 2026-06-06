"""Map task fields and target apps to ADB capture action plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.documents.fields import (
    CHECK_RESULT,
    COMMENT_COUNT,
    READ_COUNT,
    SCREENSHOT,
)
from apps.finance_crawler.crawlers.constants import SOURCE_TENPAY
from apps.finance_crawler.mobile.action_plan import (
    ACTION_CLICK_DETAIL,
    ACTION_OCR,
    ACTION_OPEN_LINK,
    ACTION_SCREENSHOT,
    ACTION_SCROLL,
    ACTION_TAP_RETRY,
    ACTION_UI_CONTROLS,
    FieldCapturePlan,
)


@dataclass(frozen=True, slots=True)
class AppCapturePolicy:
    extra_actions: tuple[str, ...] = ()
    min_scrolls: int = 0
    requires_ocr: bool = False


FIELD_ACTIONS: dict[str, tuple[str, ...]] = {
    READ_COUNT: (ACTION_UI_CONTROLS, ACTION_SCREENSHOT),
    COMMENT_COUNT: (ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_SCROLL),
    SCREENSHOT: (ACTION_SCREENSHOT,),
    CHECK_RESULT: (ACTION_UI_CONTROLS,),
}

APP_POLICIES: dict[str, AppCapturePolicy] = {
    SOURCE_TENPAY: AppCapturePolicy(
        extra_actions=(ACTION_OCR,),
        requires_ocr=True,
    ),
}

INTERACTIVE_DETAIL_FIELDS = {
    "trade_details",
    "fund_details",
}


def plan_capture_for_task(
    *,
    task_type: str,
    app_type: str,
    fields: tuple[str, ...],
) -> FieldCapturePlan:
    actions = {ACTION_OPEN_LINK}
    for field_name in fields:
        actions.update(FIELD_ACTIONS.get(field_name, (ACTION_UI_CONTROLS,)))
        if field_name in INTERACTIVE_DETAIL_FIELDS:
            actions.add(ACTION_CLICK_DETAIL)
            actions.add(ACTION_OCR)

    if READ_COUNT in fields:
        actions.add(ACTION_TAP_RETRY)
        if Config.DOC_LINK_READS_ENABLE_OCR:
            actions.add(ACTION_OCR)

    policy = APP_POLICIES.get(app_type)
    max_scrolls = 0
    if ACTION_SCROLL in actions:
        max_scrolls = 1
    if policy:
        actions.update(policy.extra_actions)
        max_scrolls = max(max_scrolls, policy.min_scrolls)
        if policy.requires_ocr:
            actions.add(ACTION_OCR)

    return FieldCapturePlan(
        task_type=task_type,
        app_type=app_type or "unknown",
        fields=tuple(dict.fromkeys(fields)),
        actions=_ordered_actions(actions),
        max_scrolls=max_scrolls,
        wait_after_open=max(Config.PAGE_LOAD_WAIT, 3.0),
        wait_after_scroll=Config.DETAIL_SCROLL_WAIT if max_scrolls > 0 else 0.0,
        open_retries=Config.DOC_LINK_READS_OPEN_RETRIES if READ_COUNT in fields else 0,
        ready_timeout=0.0,
        ready_check_interval=0.5,
    )


def resolve_capture_plan_for_task(
    *,
    task_type: str,
    app_type: str,
    fields: tuple[str, ...],
    profile: dict[str, Any] | None = None,
) -> FieldCapturePlan:
    if profile:
        return plan_capture_from_profile(profile, task_type=task_type, app_type=app_type, fields=fields)
    return plan_capture_for_task(task_type=task_type, app_type=app_type, fields=fields)


def plan_capture_from_profile(
    profile: dict[str, Any],
    *,
    task_type: str,
    app_type: str,
    fields: tuple[str, ...],
) -> FieldCapturePlan:
    actions = tuple(str(item) for item in profile.get("action_names") or [])
    config = profile.get("capture_config") or {}
    max_scrolls = _config_int(config.get("max_scrolls"), 0)
    open_retries = _config_int(config.get("open_retries"), 0)
    return FieldCapturePlan(
        task_type=task_type,
        app_type=app_type or str(profile.get("app_type") or "unknown"),
        fields=tuple(dict.fromkeys(fields)),
        actions=_ordered_actions(set(actions)),
        max_scrolls=max_scrolls,
        wait_after_open=_config_float(config.get("wait_after_open"), max(Config.PAGE_LOAD_WAIT, 3.0)),
        wait_after_scroll=_config_float(
            config.get("wait_after_scroll"),
            Config.DETAIL_SCROLL_WAIT if max_scrolls > 0 else 0.0,
        ),
        open_retries=open_retries,
        ready_timeout=_config_float(config.get("ready_timeout"), 0.0),
        ready_check_interval=_config_float(config.get("ready_check_interval"), 0.5),
    )


def _ordered_actions(actions: set[str]) -> tuple[str, ...]:
    order = (
        ACTION_OPEN_LINK,
        ACTION_UI_CONTROLS,
        ACTION_SCREENSHOT,
        ACTION_OCR,
        ACTION_TAP_RETRY,
        ACTION_SCROLL,
        ACTION_CLICK_DETAIL,
    )
    return tuple(action for action in order if action in actions)


def _config_int(value: Any, default: int) -> int:
    if isinstance(value, str) and hasattr(Config, value):
        return int(getattr(Config, value) or default)
    if value in (None, ""):
        return default
    return int(value)


def _config_float(value: Any, default: float) -> float:
    if isinstance(value, str) and hasattr(Config, value):
        return float(getattr(Config, value) or default)
    if value in (None, ""):
        return default
    return float(value)
