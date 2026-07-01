"""Map task fields and target apps to ADB capture action plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.documents.fields import (
    ACCOUNT_NAME,
    ARTICLE_TITLE,
    CHECK_RESULT,
    COMMENT_COUNT,
    LIKE_COUNT,
    READ_COUNT,
    REMARK,
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
class FieldEvidenceRequirement:
    """Evidence actions required to extract one metric from a shared capture."""

    actions: tuple[str, ...]
    min_scrolls: int = 0
    open_retries: int = 0


@dataclass(frozen=True, slots=True)
class MinimalCaptureActions:
    """Merged action set that can satisfy all requested fields once."""

    actions: tuple[str, ...]
    max_scrolls: int = 0
    open_retries: int = 0


FIELD_EVIDENCE_REQUIREMENTS: dict[str, FieldEvidenceRequirement] = {
    ACCOUNT_NAME: FieldEvidenceRequirement((ACTION_UI_CONTROLS,)),
    READ_COUNT: FieldEvidenceRequirement(
        (ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_TAP_RETRY),
    ),
    COMMENT_COUNT: FieldEvidenceRequirement((ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_SCROLL), min_scrolls=1),
    LIKE_COUNT: FieldEvidenceRequirement((ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_SCROLL), min_scrolls=1),
    ARTICLE_TITLE: FieldEvidenceRequirement((ACTION_UI_CONTROLS, ACTION_SCREENSHOT)),
    SCREENSHOT: FieldEvidenceRequirement((ACTION_SCREENSHOT,)),
    REMARK: FieldEvidenceRequirement(()),
    CHECK_RESULT: FieldEvidenceRequirement((ACTION_UI_CONTROLS,)),
}

APP_METRIC_EVIDENCE_REQUIREMENTS: dict[tuple[str, str], FieldEvidenceRequirement] = {
    (SOURCE_TENPAY, READ_COUNT): FieldEvidenceRequirement(
        (ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_OCR, ACTION_TAP_RETRY),
    ),
    (SOURCE_TENPAY, COMMENT_COUNT): FieldEvidenceRequirement(
        (ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_OCR),
    ),
    (SOURCE_TENPAY, LIKE_COUNT): FieldEvidenceRequirement(
        (ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_OCR),
    ),
    (SOURCE_TENPAY, ARTICLE_TITLE): FieldEvidenceRequirement(
        (ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_OCR),
    ),
    (SOURCE_TENPAY, "trade_details"): FieldEvidenceRequirement(
        (ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_OCR, ACTION_SCROLL, ACTION_CLICK_DETAIL),
        min_scrolls=2,
    ),
}

INTERACTIVE_DETAIL_FIELDS = {
    "trade_details",
    "fund_details",
}

ACTION_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    ACTION_OCR: (ACTION_SCREENSHOT,),
    ACTION_CLICK_DETAIL: (ACTION_SCREENSHOT,),
}


def plan_capture_for_task(
    *,
    task_type: str,
    app_type: str,
    fields: tuple[str, ...],
) -> FieldCapturePlan:
    merged = plan_minimal_capture_actions(app_type=app_type, fields=fields)

    return FieldCapturePlan(
        task_type=task_type,
        app_type=app_type or "unknown",
        fields=tuple(dict.fromkeys(fields)),
        actions=merged.actions,
        max_scrolls=merged.max_scrolls,
        wait_after_open=max(Config.PAGE_LOAD_WAIT, 3.0),
        wait_after_scroll=Config.DETAIL_SCROLL_WAIT if merged.max_scrolls > 0 else 0.0,
        open_retries=merged.open_retries,
        ready_timeout=0.0,
        ready_check_interval=0.5,
    )


def plan_minimal_capture_actions(*, app_type: str, fields: tuple[str, ...]) -> MinimalCaptureActions:
    """Merge app+metric evidence needs into one smallest reusable capture action set."""

    actions = {ACTION_OPEN_LINK}
    max_scrolls = 0
    open_retries = 0
    for field_name in tuple(dict.fromkeys(fields)):
        requirement = metric_evidence_requirement(app_type=app_type, metric_name=field_name)
        actions.update(requirement.actions)
        max_scrolls = max(max_scrolls, requirement.min_scrolls)
        open_retries = max(open_retries, requirement.open_retries)
        if field_name == READ_COUNT and Config.DOC_LINK_READS_ENABLE_OCR:
            actions.add(ACTION_OCR)

    actions = _with_dependencies(actions)
    if ACTION_SCROLL in actions:
        max_scrolls = max(max_scrolls, 1)
    if READ_COUNT in fields:
        open_retries = max(open_retries, Config.DOC_LINK_READS_OPEN_RETRIES)
    return MinimalCaptureActions(
        actions=_ordered_actions(actions),
        max_scrolls=max_scrolls,
        open_retries=open_retries,
    )


def metric_evidence_requirement(*, app_type: str, metric_name: str) -> FieldEvidenceRequirement:
    app_metric = APP_METRIC_EVIDENCE_REQUIREMENTS.get((app_type, metric_name))
    if app_metric:
        return app_metric
    generic = FIELD_EVIDENCE_REQUIREMENTS.get(metric_name)
    if generic:
        return generic
    if metric_name in INTERACTIVE_DETAIL_FIELDS:
        return FieldEvidenceRequirement((ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_OCR, ACTION_CLICK_DETAIL))
    return FieldEvidenceRequirement((ACTION_UI_CONTROLS,))


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


def _with_dependencies(actions: set[str]) -> set[str]:
    expanded = set(actions)
    pending = list(actions)
    while pending:
        action = pending.pop()
        for dependency in ACTION_DEPENDENCIES.get(action, ()):
            if dependency not in expanded:
                expanded.add(dependency)
                pending.append(dependency)
    return expanded


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
