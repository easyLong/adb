"""Best-effort recording of generic framework results and writebacks."""

from __future__ import annotations

from typing import Any

from apps.finance_crawler.domain.records import CrawlResult, WritebackResult
from apps.finance_crawler.storage.framework_db import (
    insert_crawl_result,
    record_writeback,
)
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils.record_identity import workflow_record_id, workflow_record_url

logger = get_logger("framework_events")


def record_crawl_result_for_record(
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
    """Record a crawl result without allowing framework writes to break workflow execution."""

    record_id = workflow_record_id(record)
    url = workflow_record_url(record)
    source_app = resolve_source_app(record.get("source_app"), url)
    merged_metrics = {
        "workflow": workflow,
        "doc_row_index": record.get("doc_row_index"),
        "source_app": source_app,
    }
    if metrics:
        merged_metrics.update(metrics)

    try:
        task_id = record.get("task_id")
        result = CrawlResult(
            task_id=task_id,
            url=url,
            app_type=source_app,
            status=status,
            account_name=account_name,
            content=content,
            metrics=merged_metrics,
            screenshot_path=screenshot_path,
            error=error,
        )
        result_id = insert_crawl_result(result)
        return task_id, result_id
    except Exception as exc:
        logger.warning("failed to write crawl_results workflow=%s record_id=%s: %s", workflow, record_id, exc)
        return None, None


def record_sink_writeback_for_record(
    *,
    record_id: int,
    sink_type: str,
    status: str,
    task_id: int | None = None,
    result_id: int | None = None,
    locator: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record a sink writeback without allowing framework writes to break workflow execution."""

    try:
        record_writeback(
            WritebackResult(
                task_id=task_id,
                result_id=result_id,
                sink_type=sink_type,
                status=status,
                locator=locator or {},
                error=error,
            )
        )
    except Exception as exc:
        logger.warning(
            "failed to write crawl_writebacks sink=%s record_id=%s status=%s: %s",
            sink_type,
            record_id,
            status,
            exc,
        )
