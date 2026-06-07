"""Common contracts for app/field/page-state based capture flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ActionTemplate:
    """Resolved action template for one app, task, and field combination."""

    key: str
    app_type: str
    task_type: str
    fields: tuple[str, ...]
    actions: tuple[str, ...]
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    """Captured screen evidence used by state detectors and field extractors."""

    app_type: str
    records: list[dict[str, Any]]
    screenshot_path: str | None = None
    output_dir: Path | None = None
    expected_account_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CaptureBundle:
    """Shared output of one action run, consumed by multiple field extractors."""

    task_type: str
    app_type: str
    requested_fields: tuple[str, ...]
    action_template_key: str | None = None
    actions: tuple[str, ...] = ()
    opened_url: str | None = None
    status: str = "unknown"
    page_state: str = "unknown"
    ui_records: list[dict[str, Any]] = field(default_factory=list)
    ocr_records: list[dict[str, Any]] = field(default_factory=list)
    screenshot_path: str | None = None
    raw_result: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "app_type": self.app_type,
            "requested_fields": list(self.requested_fields),
            "action_template_key": self.action_template_key,
            "actions": list(self.actions),
            "opened_url": self.opened_url,
            "status": self.status,
            "page_state": self.page_state,
            "screenshot_path": self.screenshot_path,
            "metadata": self.metadata,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class PageState:
    """Current page state before extracting a field."""

    name: str
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FieldExtraction:
    """Raw extraction result for one requested field."""

    field_name: str
    value: Any = None
    source: str | None = None
    page_state: str = "unknown"
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    quality_error: str | None = None


@dataclass(frozen=True, slots=True)
class FieldExtractionResult:
    """Field-level accepted/rejected result derived from a shared capture bundle."""

    field_name: str
    value: Any = None
    source: str | None = None
    accepted: bool = False
    page_state: str = "unknown"
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    quality_error: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "value": self.value,
            "source": self.source,
            "accepted": self.accepted,
            "page_state": self.page_state,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "quality_error": self.quality_error,
        }


@dataclass(frozen=True, slots=True)
class EvidenceValidation:
    """Final accept/reject decision for an extracted field value."""

    accepted: bool
    reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class AppAdapter(Protocol):
    """App-specific target opening and recovery behavior."""

    app_type: str

    def reset_state(self) -> None:
        ...

    def open_target(self, target_url: str) -> None:
        ...


class PageStateDetector(Protocol):
    """Detect whether a snapshot is on the expected page state."""

    def detect(self, snapshot: PageSnapshot) -> PageState:
        ...


class FieldExtractor(Protocol):
    """Extract one field from a captured page snapshot."""

    field_name: str

    def extract(
        self,
        snapshot: PageSnapshot,
        page_state: PageState,
        action_template: ActionTemplate,
    ) -> FieldExtraction:
        ...


class EvidenceValidator(Protocol):
    """Validate whether a field extraction is safe to store/write back."""

    def validate(
        self,
        extraction: FieldExtraction,
        snapshot: PageSnapshot,
        page_state: PageState,
        action_template: ActionTemplate,
    ) -> EvidenceValidation:
        ...
