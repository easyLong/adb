"""Workflow-facing writeback service.

This keeps business workflows from depending directly on Tencent Docs row
snapshots. Future sinks can implement the same small surface and be selected
from a registry/factory.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from apps.finance_crawler.config import Config
from apps.finance_crawler.sinks.excel import ExcelSink
from apps.finance_crawler.sinks.tencent_docs import TencentDocsSink
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("writeback_service")


@dataclass(frozen=True, slots=True)
class WritebackPlan:
    sink_type: str
    row: dict[str, Any] | None = None
    locator: dict[str, Any] = field(default_factory=dict)
    skip_reason: str | None = None

    @property
    def can_write(self) -> bool:
        return self.row is not None and self.skip_reason is None

    @property
    def row_index(self) -> int | None:
        value = self.locator.get("row_index")
        return int(value) if value else None


class WritebackService(Protocol):
    sink_type: str

    def load_snapshot(self, *, alert=None, warning_dedupe_key: str | None = None) -> None:
        ...

    def prepare_initial_check(self, *, post: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        ...

    def prepare_batch(self, *, post: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        ...

    def write_initial_check_results(self, plans: list[WritebackPlan]) -> None:
        ...

    def write_batch_results(self, plans: list[WritebackPlan]) -> None:
        ...


class TencentDocsWritebackService:
    """Prepare and write Tencent Docs rows without leaking sheet internals to workflows."""

    sink_type = "tencent_docs"

    def __init__(self, sink: TencentDocsSink | None = None) -> None:
        self.sink = sink or TencentDocsSink()
        self.rows: list[list[str]] = []
        self.start_row = 0
        self.snapshot_available = False

    def load_snapshot(self, *, alert=None, warning_dedupe_key: str | None = None) -> None:
        try:
            self.rows, self.start_row = self.sink.fetch_grid()
            self.snapshot_available = True
        except Exception as exc:
            self.rows, self.start_row = [], 0
            self.snapshot_available = False
            if alert:
                alert(str(exc), warning_dedupe_key)
            logger.warning("failed to load %s row snapshot; writeback will be skipped: %s", self.sink_type, exc)

    def prepare_initial_check(self, *, post: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        if result.get("status") not in {"success", "not_found"}:
            return WritebackPlan(
                sink_type=self.sink_type,
                skip_reason=result.get("error") or "technical error skipped writeback",
            )

        row_index = self._resolve_row_index(post)
        if not row_index:
            return WritebackPlan(sink_type=self.sink_type, skip_reason="row not found")

        return WritebackPlan(
            sink_type=self.sink_type,
            row={
                "row_index": row_index,
                "exists": result.get("status") == "success",
                "account_name": result.get("account_name"),
            },
            locator={"row_index": row_index},
        )

    def prepare_batch(self, *, post: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        row_index = self._resolve_row_index(post)
        if not row_index:
            return WritebackPlan(sink_type=self.sink_type, skip_reason="row not found")

        return WritebackPlan(
            sink_type=self.sink_type,
            row={
                "row_index": row_index,
                "read_count": result.get("read_count") or 0,
                "comment_count": result.get("comment_count") or 0,
                "batch_status": result["status"],
                "screenshot_path": result.get("screenshot_path"),
            },
            locator={"row_index": row_index},
        )

    def write_initial_check_results(self, plans: list[WritebackPlan]) -> None:
        rows = [plan.row for plan in plans if plan.can_write and plan.row]
        if rows:
            self.sink.write_initial_check_results(rows)

    def write_batch_results(self, plans: list[WritebackPlan]) -> None:
        rows = [plan.row for plan in plans if plan.can_write and plan.row]
        if rows:
            self.sink.write_batch_results(rows)

    def _resolve_row_index(self, post: dict[str, Any]) -> int | None:
        if not self.snapshot_available:
            return None
        try:
            return self.sink.resolve_row_index_for_url(
                post["url"],
                preferred_row_index=post.get("doc_row_index"),
                rows=self.rows,
                start_row=self.start_row,
            )
        except Exception as exc:
            logger.warning("unsafe %s writeback skipped id=%s: %s", self.sink_type, post.get("id"), exc)
            return None


class ExcelWritebackService:
    """Write back rows to a local Excel workbook using source row indexes."""

    sink_type = "excel"

    def __init__(self, sink: ExcelSink | None = None) -> None:
        self.sink = sink or _configured_excel_sink()

    def load_snapshot(self, *, alert=None, warning_dedupe_key: str | None = None) -> None:
        return None

    def prepare_initial_check(self, *, post: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        if result.get("status") not in {"success", "not_found"}:
            return WritebackPlan(
                sink_type=self.sink_type,
                skip_reason=result.get("error") or "technical error skipped writeback",
            )
        row_index = self._row_index(post)
        if not row_index:
            return WritebackPlan(sink_type=self.sink_type, skip_reason="row not found")
        return WritebackPlan(
            sink_type=self.sink_type,
            row={
                "row_index": row_index,
                "exists": result.get("status") == "success",
                "account_name": result.get("account_name"),
            },
            locator={"path": str(self.sink.save_as), "row_index": row_index},
        )

    def prepare_batch(self, *, post: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        row_index = self._row_index(post)
        if not row_index:
            return WritebackPlan(sink_type=self.sink_type, skip_reason="row not found")
        return WritebackPlan(
            sink_type=self.sink_type,
            row={
                "row_index": row_index,
                "read_count": result.get("read_count") or 0,
                "comment_count": result.get("comment_count") or 0,
                "batch_status": result["status"],
                "screenshot_path": result.get("screenshot_path"),
            },
            locator={"path": str(self.sink.save_as), "row_index": row_index},
        )

    def write_initial_check_results(self, plans: list[WritebackPlan]) -> None:
        rows = [plan.row for plan in plans if plan.can_write and plan.row]
        if rows:
            self.sink.write_initial_check_results(rows)

    def write_batch_results(self, plans: list[WritebackPlan]) -> None:
        rows = [plan.row for plan in plans if plan.can_write and plan.row]
        if rows:
            self.sink.write_batch_results(rows)

    @staticmethod
    def _row_index(post: dict[str, Any]) -> int | None:
        value = post.get("doc_row_index") or post.get("row_index")
        return int(value) if value else None


def _configured_excel_sink() -> ExcelSink:
    if not Config.WRITEBACK_EXCEL_PATH:
        raise ValueError("WRITEBACK_EXCEL_PATH is required when WRITEBACK_SINK_TYPE=excel")
    return ExcelSink(
        Config.WRITEBACK_EXCEL_PATH,
        save_as=Config.WRITEBACK_EXCEL_SAVE_AS or None,
        sheet_name=Config.WRITEBACK_EXCEL_SHEET_NAME or None,
    )


def default_writeback_service() -> WritebackService:
    return create_writeback_service(Config.WRITEBACK_SINK_TYPE)


_WRITEBACK_SERVICE_FACTORIES: dict[str, Callable[[], WritebackService]] = {
    TencentDocsWritebackService.sink_type: TencentDocsWritebackService,
    ExcelWritebackService.sink_type: ExcelWritebackService,
}


def create_writeback_service(sink_type: str | None = None) -> WritebackService:
    selected = (sink_type or Config.WRITEBACK_SINK_TYPE or TencentDocsWritebackService.sink_type).strip()
    factory = _WRITEBACK_SERVICE_FACTORIES.get(selected)
    if factory is None:
        available = ", ".join(sorted(_WRITEBACK_SERVICE_FACTORIES))
        raise ValueError(f"unsupported writeback sink: {selected}; available: {available}")
    return factory()
