"""Field extraction from one post-link capture bundle."""

from __future__ import annotations

from typing import Any

from apps.finance_crawler.crawler_app.capture.core import CaptureBundle, FieldExtractionResult
from apps.finance_crawler.crawler_app.documents.fields import (
    ACCOUNT_NAME,
    CHECK_RESULT,
    COMMENT_COUNT,
    READ_COUNT,
    REMARK,
    SCREENSHOT,
)


POST_FIELD_EXTRACTORS = {
    ACCOUNT_NAME,
    CHECK_RESULT,
    COMMENT_COUNT,
    READ_COUNT,
    REMARK,
    SCREENSHOT,
}


def build_post_capture_bundle(
    *,
    task_type: str,
    app_type: str,
    requested_fields: tuple[str, ...],
    result: dict[str, Any],
) -> CaptureBundle:
    capture_plan = result.get("capture_plan") if isinstance(result.get("capture_plan"), dict) else {}
    actions = tuple(str(item) for item in capture_plan.get("actions") or ())
    action_template_key = str(capture_plan.get("action_template_key") or "") or None
    page_state = _infer_post_page_state(result)
    return CaptureBundle(
        task_type=task_type,
        app_type=app_type or str(result.get("app_type") or "unknown"),
        requested_fields=tuple(dict.fromkeys(requested_fields)),
        action_template_key=action_template_key,
        actions=actions,
        opened_url=str(result.get("opened_url") or "") or None,
        status=str(result.get("status") or "unknown"),
        page_state=page_state,
        screenshot_path=str(result.get("screenshot_path") or "") or None,
        raw_result=result,
        metadata={"capture_plan": capture_plan},
        error=str(result.get("error") or "") or None,
    )


def extract_post_field_results(bundle: CaptureBundle) -> list[FieldExtractionResult]:
    return [
        result
        for field_name in bundle.requested_fields
        if field_name in POST_FIELD_EXTRACTORS
        for result in [_extract_post_field(bundle, field_name)]
    ]


def post_field_results_by_name(bundle: CaptureBundle) -> dict[str, FieldExtractionResult]:
    return {result.field_name: result for result in extract_post_field_results(bundle)}


def writeback_values_from_field_results(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("field_results")
    if not isinstance(payload, list):
        return {}
    values: dict[str, Any] = {}
    for item in payload:
        if not isinstance(item, dict) or not item.get("accepted"):
            continue
        field_name = str(item.get("field_name") or "")
        if not field_name:
            continue
        value = item.get("value")
        if value is None:
            continue
        values[field_name] = value
    return values


def _extract_post_field(bundle: CaptureBundle, field_name: str) -> FieldExtractionResult:
    raw = bundle.raw_result
    status = bundle.status
    if field_name == ACCOUNT_NAME:
        value = raw.get("account_name")
        if status in {"not_found", "deleted"}:
            return _result(bundle, field_name, "N", source="page_status", accepted=True)
        return _result(bundle, field_name, value, source="ui_controls", accepted=bool(value))
    if field_name == READ_COUNT:
        value = raw.get("read_count")
        if status in {"not_found", "deleted"}:
            return _result(bundle, field_name, "N", source="page_status", accepted=True)
        return _result(bundle, field_name, value, source="ui_controls", accepted=value is not None)
    if field_name == COMMENT_COUNT:
        value = raw.get("comment_count")
        return _result(bundle, field_name, value, source="ui_controls", accepted=value is not None)
    if field_name == SCREENSHOT:
        value = raw.get("screenshot_path")
        return _result(bundle, field_name, value, source="screenshot", accepted=bool(value))
    if field_name == CHECK_RESULT:
        if status == "success":
            return _result(bundle, field_name, "Y", source="page_status", accepted=True)
        if status in {"not_found", "deleted"}:
            return _result(bundle, field_name, "N", source="page_status", accepted=True)
        return _result(bundle, field_name, None, source="page_status", accepted=False)
    if field_name == REMARK:
        return _result(bundle, field_name, _remark_value(bundle), source="runtime", accepted=True)
    return _result(bundle, field_name, None, source=None, accepted=False)


def _result(
    bundle: CaptureBundle,
    field_name: str,
    value: Any,
    *,
    source: str | None,
    accepted: bool,
) -> FieldExtractionResult:
    return FieldExtractionResult(
        field_name=field_name,
        value=value,
        source=source,
        accepted=accepted,
        page_state=bundle.page_state,
        confidence=0.8 if accepted else 0.0,
        evidence={
            "status": bundle.status,
            "opened_url": bundle.opened_url,
            "actions": list(bundle.actions),
            "capture_plan": bundle.metadata.get("capture_plan"),
        },
        quality_error=None if accepted else bundle.error or f"{field_name} was not detected",
    )


def _infer_post_page_state(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "")
    if status == "success":
        return "post_detail"
    if status in {"not_found", "deleted"}:
        return "not_found"
    if "blank" in str(result.get("error") or "").lower():
        return "blank"
    return "error" if status else "unknown"


def _remark_value(bundle: CaptureBundle) -> str:
    if bundle.status == "success":
        return "\u6210\u529f"
    reason = bundle.raw_result.get("not_found_reason") or bundle.error or bundle.status or "failed"
    if bundle.status in {"not_found", "deleted"}:
        return str(reason)
    if str(bundle.raw_result.get("final_submission_status") or "") == "failed":
        return f"\u5931\u8d25\uff1a{reason}"
    return str(reason)
