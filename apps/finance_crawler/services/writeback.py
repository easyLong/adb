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
from apps.finance_crawler.integrations.tencent_docs import client as tencent_docs_client
from apps.finance_crawler.sinks.excel import ExcelSink
from apps.finance_crawler.sinks.tencent_docs import TencentDocsSink
from apps.finance_crawler.services.remarks import detail_remark
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils.record_identity import workflow_record_id, workflow_record_url

logger = get_logger("writeback_service")

_MISSING_CONTENT_STATUSES = {"deleted", "not_found"}


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

    def prepare_initial_check(self, *, record: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        ...

    def prepare_detail(self, *, record: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        ...

    def write_initial_check_results(self, plans: list[WritebackPlan]) -> None:
        ...

    def write_detail_results(self, plans: list[WritebackPlan]) -> None:
        ...


class TencentDocsWritebackService:
    """Prepare and write Tencent Docs rows without leaking sheet internals to workflows."""

    sink_type = "tencent_docs"

    def __init__(self, sink: TencentDocsSink | None = None) -> None:
        self.sink = sink or TencentDocsSink()
        self.snapshots: dict[tuple[str, str], tuple[list[list[str]], int]] = {}
        self.snapshot_available = False

    def load_snapshot(self, *, alert=None, warning_dedupe_key: str | None = None) -> None:
        self.snapshots = {}
        self.snapshot_available = True

    def prepare_initial_check(self, *, record: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        if result.get("status") not in {"success", "not_found"}:
            return WritebackPlan(
                sink_type=self.sink_type,
                skip_reason=result.get("error") or "technical error skipped writeback",
            )

        row_index = self._resolve_row_index(record)
        if not row_index:
            return WritebackPlan(sink_type=self.sink_type, skip_reason="row not found")

        return WritebackPlan(
            sink_type=self.sink_type,
            row={
                **self._doc_fields(record),
                "row_index": row_index,
                "exists": result.get("status") == "success",
                "account_name": result.get("account_name"),
            },
            locator={**self._doc_fields(record), "row_index": row_index},
        )

    def prepare_detail(self, *, record: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        row_index = self._resolve_row_index(record)
        if not row_index:
            return WritebackPlan(sink_type=self.sink_type, skip_reason="row not found")
        detail_status = result["status"]

        return WritebackPlan(
            sink_type=self.sink_type,
            row={
                **self._doc_fields(record),
                "row_index": row_index,
                "read_count": "N" if detail_status in _MISSING_CONTENT_STATUSES else result.get("read_count") or 0,
                "comment_count": result.get("comment_count") or 0,
                "detail_status": detail_status,
                "detail_remark": detail_remark(result),
                "screenshot_path": result.get("screenshot_path"),
            },
            locator={**self._doc_fields(record), "row_index": row_index},
        )

    def write_initial_check_results(self, plans: list[WritebackPlan]) -> None:
        rows = [plan.row for plan in plans if plan.can_write and plan.row]
        if rows:
            self.sink.write_initial_check_results(rows)

    def write_detail_results(self, plans: list[WritebackPlan]) -> None:
        rows = [plan.row for plan in plans if plan.can_write and plan.row]
        if rows:
            self.sink.write_detail_results(rows)

    def _resolve_row_index(self, record: dict[str, Any]) -> int | None:
        if not self.snapshot_available:
            return None
        try:
            doc = self._doc_info(record)
            rows, start_row = self._snapshot(doc)
            return self.sink.resolve_row_index_for_url(
                workflow_record_url(record),
                preferred_row_index=record.get("doc_row_index"),
                rows=rows,
                start_row=start_row,
                doc=doc,
            )
        except Exception as exc:
            logger.warning("unsafe %s writeback skipped id=%s: %s", self.sink_type, workflow_record_id(record), exc)
            return None

    def _snapshot(self, doc: tencent_docs_client.DocInfo) -> tuple[list[list[str]], int]:
        key = (doc.file_id, doc.sheet_id)
        if key not in self.snapshots:
            self.snapshots[key] = self.sink.fetch_grid(doc=doc)
        return self.snapshots[key]

    @staticmethod
    def _doc_info(record: dict[str, Any]) -> tencent_docs_client.DocInfo:
        file_id = str(record.get("doc_file_id") or Config.QQ_FILE_ID)
        sheet_id = str(record.get("doc_sheet_id") or Config.QQ_SHEET_ID)
        return tencent_docs_client.DocInfo(file_id, sheet_id)

    @staticmethod
    def _doc_fields(record: dict[str, Any]) -> dict[str, str]:
        doc = TencentDocsWritebackService._doc_info(record)
        return {"file_id": doc.file_id, "sheet_id": doc.sheet_id}


class ExcelWritebackService:
    """Write back rows to a local Excel workbook using source row indexes."""

    sink_type = "excel"

    def __init__(self, sink: ExcelSink | None = None) -> None:
        self.sink = sink or _configured_excel_sink()

    def load_snapshot(self, *, alert=None, warning_dedupe_key: str | None = None) -> None:
        return None

    def prepare_initial_check(self, *, record: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        if result.get("status") not in {"success", "not_found"}:
            return WritebackPlan(
                sink_type=self.sink_type,
                skip_reason=result.get("error") or "technical error skipped writeback",
            )
        row_index = self._row_index(record)
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

    def prepare_detail(self, *, record: dict[str, Any], result: dict[str, Any]) -> WritebackPlan:
        row_index = self._row_index(record)
        if not row_index:
            return WritebackPlan(sink_type=self.sink_type, skip_reason="row not found")
        return WritebackPlan(
            sink_type=self.sink_type,
            row={
                "row_index": row_index,
                "read_count": result.get("read_count") or 0,
                "comment_count": result.get("comment_count") or 0,
                "detail_status": result["status"],
                "detail_remark": detail_remark(result),
                "screenshot_path": result.get("screenshot_path"),
            },
            locator={"path": str(self.sink.save_as), "row_index": row_index},
        )

    def write_initial_check_results(self, plans: list[WritebackPlan]) -> None:
        rows = [plan.row for plan in plans if plan.can_write and plan.row]
        if rows:
            self.sink.write_initial_check_results(rows)

    def write_detail_results(self, plans: list[WritebackPlan]) -> None:
        rows = [plan.row for plan in plans if plan.can_write and plan.row]
        if rows:
            self.sink.write_detail_results(rows)

    @staticmethod
    def _row_index(record: dict[str, Any]) -> int | None:
        value = record.get("doc_row_index") or record.get("row_index")
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
