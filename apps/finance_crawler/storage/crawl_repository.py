"""Workflow-facing repository for crawl tasks and compatibility updates.

The workflows should not need to know whether pending work comes from the
legacy ``posts`` table or the newer ``crawl_*`` framework tables. This module
keeps that boundary in one place while the project migrates gradually.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.services.framework_events import (
    record_crawl_result_for_post,
    record_sink_writeback_for_post,
)
from apps.finance_crawler.storage import db


@dataclass(frozen=True, slots=True)
class PendingWriteback:
    """Framework identifiers needed after an external sink writeback completes."""

    post_id: int
    task_id: int | None
    result_id: int | None
    row_index: int

    @property
    def locator(self) -> dict[str, int]:
        return {"row_index": self.row_index}


def get_pending_initial_check_records() -> list[dict]:
    """Return records that need the existence/account check."""

    return db.get_pending_check_posts()


def save_initial_check_result(
    *,
    post_id: int,
    status: str,
    error: str | None = None,
    account_name: str | None = None,
) -> None:
    """Persist initial check result to the current compatibility path."""

    db.update_check_result(
        post_id,
        status,
        error,
        account_name,
    )


def get_pending_batch_records(limit: int | None = None) -> list[dict]:
    """Return records that need full batch crawling."""

    return db.get_pending_batch_posts(limit)


def save_batch_result(
    *,
    post_id: int,
    status: str,
    content: str | None = None,
    read_count: int = 0,
    comment_count: int = 0,
    screenshot_path: str | None = None,
    error: str | None = None,
) -> None:
    """Persist batch crawl result to the current compatibility path."""

    db.update_batch_result(
        post_id=post_id,
        status=status,
        content=content,
        read_count=read_count,
        comment_count=comment_count,
        screenshot_path=screenshot_path,
        error=error,
    )


def mark_writebacks_done(post_ids: list[int]) -> None:
    """Mark compatibility rows as written back after a sink succeeds."""

    db.mark_written_back_many(post_ids)


def record_crawl_result(
    *,
    post: dict[str, Any],
    workflow: str,
    status: str,
    metrics: dict[str, Any] | None = None,
    account_name: str | None = None,
    content: str | None = None,
    screenshot_path: str | None = None,
    error: str | None = None,
) -> tuple[int | None, int | None]:
    """Record normalized framework crawl output without leaking framework APIs to workflows."""

    return record_crawl_result_for_post(
        post=post,
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
    post_id: int,
    sink_type: str,
    status: str,
    task_id: int | None = None,
    result_id: int | None = None,
    locator: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record framework sink writeback status without leaking framework APIs to workflows."""

    record_sink_writeback_for_post(
        post_id=post_id,
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
            post_id=record.post_id,
            sink_type=sink_type,
            status=status,
            task_id=record.task_id,
            result_id=record.result_id,
            locator=record.locator,
            error=error,
        )
