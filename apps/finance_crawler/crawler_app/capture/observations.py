"""Field-level capture observations shared by capture flows and storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from apps.finance_crawler.crawler_app.capture.core import CaptureBundle, FieldExtractionResult


@dataclass(frozen=True, slots=True)
class FieldCaptureObservation:
    """One field extraction decision and the evidence behind it."""

    subject_type: str
    subject_id: int | None
    target_type: str | None
    target_id: int | None
    task_type: str
    app_type: str
    field_name: str
    accepted: bool
    value: Any = None
    action_template_key: str | None = None
    action_names: tuple[str, ...] = ()
    page_state: str | None = None
    extraction_source: str | None = None
    confidence: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    quality_error: str | None = None
    screenshot_path: str | None = None
    observed_at: datetime | None = None

    @property
    def value_text(self) -> str | None:
        return None if self.value is None else str(self.value)

    @property
    def value_number(self) -> int | None:
        if isinstance(self.value, int) and not isinstance(self.value, bool):
            return int(self.value)
        return None


def build_profile_metric_observations(
    *,
    metric_id: int,
    target_id: int,
    task_type: str,
    app_type: str,
    status: str,
    fans_count: int | None,
    read_count: int | None,
    metrics: dict[str, Any],
    screenshot_path: str | None,
    error: str | None,
    observed_at: datetime,
) -> list[FieldCaptureObservation]:
    """Build field observations from a profile metric run payload."""

    observations: list[FieldCaptureObservation] = []
    observations.extend(
        _standard_metric_observations(
            subject_type="profile_metric_run",
            subject_id=metric_id,
            target_type="profile_target",
            target_id=target_id,
            metrics=metrics,
            observed_at=observed_at,
        )
    )
    observed_fields = {item.field_name for item in observations}
    fans = metrics.get("fans") if isinstance(metrics, dict) else None
    if "fans_count" not in observed_fields and isinstance(fans, dict) and fans:
        accepted = bool(fans.get("fans_count") is not None and not fans.get("quality_error"))
        observations.append(
            FieldCaptureObservation(
                subject_type="profile_metric_run",
                subject_id=metric_id,
                target_type="profile_target",
                target_id=target_id,
                task_type=task_type,
                app_type=app_type or "unknown",
                field_name="fans_count",
                value=fans.get("fans_count", fans_count),
                accepted=accepted,
                action_template_key=str(fans.get("action_template") or "") or None,
                action_names=tuple(str(item) for item in (fans.get("actions") or [])),
                page_state=str(fans.get("page_state") or "") or None,
                extraction_source=str(fans.get("source") or "") or None,
                confidence=_confidence_value(fans.get("confidence", fans.get("page_state_confidence"))),
                evidence=fans,
                quality_error=fans.get("quality_error") or error,
                screenshot_path=screenshot_path,
                observed_at=observed_at,
            )
        )

    if "read_count" not in observed_fields and (
        read_count is not None or (isinstance(metrics, dict) and "posts" in metrics)
    ):
        posts = metrics.get("posts") if isinstance(metrics, dict) else None
        accepted = bool(status == "success" and read_count is not None)
        evidence = {
            "workflow": metrics.get("workflow") if isinstance(metrics, dict) else None,
            "post_count": metrics.get("post_count") if isinstance(metrics, dict) else None,
            "posts": posts if isinstance(posts, list) else [],
        }
        observations.append(
            FieldCaptureObservation(
                subject_type="profile_metric_run",
                subject_id=metric_id,
                target_type="profile_target",
                target_id=target_id,
                task_type=task_type,
                app_type=app_type or "unknown",
                field_name="read_count",
                value=read_count,
                accepted=accepted,
                action_template_key=f"{app_type or 'unknown'}_profile_daily_metrics_v1:read_count",
                action_names=(
                    "open_profile",
                    "scan_recent_posts",
                    "tap_post",
                    "capture_read_count",
                    "aggregate_max_recent_posts",
                ),
                page_state="profile_posts",
                extraction_source="recent_posts",
                confidence=0.8 if accepted else 0.0,
                evidence=evidence,
                quality_error=None if accepted else error or "profile post read count was not detected",
                screenshot_path=screenshot_path,
                observed_at=observed_at,
            )
        )
    return observations


def _standard_metric_observations(
    *,
    subject_type: str,
    subject_id: int | None,
    target_type: str | None,
    target_id: int | None,
    metrics: dict[str, Any],
    observed_at: datetime,
) -> list[FieldCaptureObservation]:
    if not isinstance(metrics, dict):
        return []
    bundle_payload = metrics.get("capture_bundle")
    field_payloads = metrics.get("field_results")
    if not isinstance(bundle_payload, dict) or not isinstance(field_payloads, list):
        return []
    bundle = _capture_bundle_from_payload(bundle_payload)
    field_results = [
        _field_result_from_payload(item)
        for item in field_payloads
        if isinstance(item, dict) and str(item.get("field_name") or "")
    ]
    if not field_results:
        return []
    return build_capture_bundle_observations(
        subject_type=subject_type,
        subject_id=subject_id,
        target_type=target_type,
        target_id=target_id,
        bundle=bundle,
        field_results=field_results,
        observed_at=observed_at,
    )


def build_capture_bundle_observations(
    *,
    subject_type: str,
    subject_id: int | None,
    target_type: str | None,
    target_id: int | None,
    bundle: CaptureBundle,
    field_results: list[FieldExtractionResult],
    observed_at: datetime,
) -> list[FieldCaptureObservation]:
    """Build field observations from generic field extraction results."""

    observations: list[FieldCaptureObservation] = []
    for result in field_results:
        observations.append(
            FieldCaptureObservation(
                subject_type=subject_type,
                subject_id=subject_id,
                target_type=target_type,
                target_id=target_id,
                task_type=bundle.task_type,
                app_type=bundle.app_type or "unknown",
                field_name=result.field_name,
                value=result.value,
                accepted=result.accepted,
                action_template_key=bundle.action_template_key,
                action_names=bundle.actions,
                page_state=result.page_state or bundle.page_state,
                extraction_source=result.source,
                confidence=result.confidence,
                evidence=result.evidence,
                quality_error=result.quality_error,
                screenshot_path=bundle.screenshot_path,
                observed_at=observed_at,
            )
        )
    return observations


def _capture_bundle_from_payload(payload: dict[str, Any]) -> CaptureBundle:
    return CaptureBundle(
        task_type=str(payload.get("task_type") or ""),
        app_type=str(payload.get("app_type") or "unknown"),
        requested_fields=tuple(str(item) for item in payload.get("requested_fields") or ()),
        action_template_key=str(payload.get("action_template_key") or "") or None,
        actions=tuple(str(item) for item in payload.get("actions") or ()),
        opened_url=str(payload.get("opened_url") or "") or None,
        status=str(payload.get("status") or "unknown"),
        page_state=str(payload.get("page_state") or "unknown"),
        screenshot_path=str(payload.get("screenshot_path") or "") or None,
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        error=str(payload.get("error") or "") or None,
    )


def _field_result_from_payload(payload: dict[str, Any]) -> FieldExtractionResult:
    return FieldExtractionResult(
        field_name=str(payload.get("field_name") or ""),
        value=payload.get("value"),
        source=str(payload.get("source") or "") or None,
        accepted=bool(payload.get("accepted")),
        page_state=str(payload.get("page_state") or "unknown"),
        confidence=_confidence_value(payload.get("confidence")) or 0.0,
        evidence=payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
        quality_error=str(payload.get("quality_error") or "") or None,
    )


def _confidence_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))
