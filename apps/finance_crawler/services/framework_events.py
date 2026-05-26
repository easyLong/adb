"""Best-effort recording of generic framework results and writebacks."""

from __future__ import annotations

from typing import Any

from apps.finance_crawler.domain.records import CrawlResult, WritebackResult
from apps.finance_crawler.storage.framework_db import (
    get_task_id_by_legacy_post_id,
    insert_crawl_result,
    record_writeback,
)
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("framework_events")


def record_crawl_result_for_post(
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
    """Record a crawl result without allowing framework writes to break legacy flow."""

    post_id = int(post["id"])
    source_app = resolve_source_app(post.get("source_app"), post["url"])
    merged_metrics = {
        "workflow": workflow,
        "doc_row_index": post.get("doc_row_index"),
        "source_app": source_app,
    }
    if metrics:
        merged_metrics.update(metrics)

    try:
        task_id = get_task_id_by_legacy_post_id(post_id)
        result = CrawlResult(
            task_id=task_id,
            url=post["url"],
            app_type=source_app,
            status=status,
            account_name=account_name,
            content=content,
            metrics=merged_metrics,
            screenshot_path=screenshot_path,
            error=error,
        )
        result_id = insert_crawl_result(result, legacy_post_id=post_id)
        return task_id, result_id
    except Exception as exc:
        logger.warning("failed to write crawl_results workflow=%s post_id=%s: %s", workflow, post_id, exc)
        return None, None


def record_sink_writeback_for_post(
    *,
    post_id: int,
    sink_type: str,
    status: str,
    task_id: int | None = None,
    result_id: int | None = None,
    locator: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record a sink writeback without allowing framework writes to break legacy flow."""

    try:
        record_writeback(
            WritebackResult(
                task_id=task_id,
                result_id=result_id,
                sink_type=sink_type,
                status=status,
                locator=locator or {},
                error=error,
            ),
            legacy_post_id=post_id,
        )
    except Exception as exc:
        logger.warning(
            "failed to write crawl_writebacks sink=%s post_id=%s status=%s: %s",
            sink_type,
            post_id,
            status,
            exc,
        )
