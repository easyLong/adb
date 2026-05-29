"""Workflow-facing repository for task queues, results, and writeback events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.services.framework_events import (
    record_crawl_result_for_record,
    record_sink_writeback_for_record,
)
from apps.finance_crawler.storage.framework_db import (
    get_pending_detail_submissions,
    get_pending_check_submissions,
)


@dataclass(frozen=True, slots=True)
class PendingWriteback:
    """Framework identifiers needed after an external sink writeback completes."""

    record_id: int
    task_id: int | None
    result_id: int | None
    row_index: int

    @property
    def locator(self) -> dict[str, int]:
        return {"row_index": self.row_index}


def get_pending_initial_check_records() -> list[dict]:
    """Return records that need the existence/account check."""

    return get_pending_check_submissions()


def get_pending_detail_records() -> list[dict]:
    """Return records that need full detail crawling."""

    return get_pending_detail_submissions()


def record_crawl_result(
    *,
    record: dict[str, Any],
    workflow: str,
    status: str,
    metrics: dict[str, Any] | None = None,
    account_name: str | None = None,
    content: str | None = None,
    screenshot_path: str | None = None,
    error: str | None = None,
) -> tuple[int | None, int | None]:
    """Record normalized framework crawl output without leaking framework APIs to workflows."""

    return record_crawl_result_for_record(
        record=record,
        workflow=workflow,
        status=status,
        metrics=metrics,
        account_name=account_name,
        content=content,
        screenshot_path=screenshot_path,
        error=error,
    )


def record_sink_writeback(
    *,
    record_id: int,
    sink_type: str,
    status: str,
    task_id: int | None = None,
    result_id: int | None = None,
    locator: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record framework sink writeback status without leaking framework APIs to workflows."""

    record_sink_writeback_for_record(
        record_id=record_id,
        sink_type=sink_type,
        status=status,
        task_id=task_id,
        result_id=result_id,
        locator=locator,
        error=error,
    )


def record_pending_writebacks(
    *,
    records: list[PendingWriteback],
    sink_type: str,
    status: str,
    error: str | None = None,
) -> None:
    """Record a group of external sink writeback outcomes."""

    for record in records:
        record_sink_writeback(
            record_id=record.record_id,
            sink_type=sink_type,
            status=status,
            task_id=record.task_id,
            result_id=record.result_id,
            locator=record.locator,
            error=error,
        )
