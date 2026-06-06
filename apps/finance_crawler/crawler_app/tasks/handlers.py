"""Task handler registry for crawler_app v2 execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apps.finance_crawler.crawler_app.runtime import ADB_RUNTIME, ExecutionRuntime
from apps.finance_crawler.crawler_app.strategies.read_count import (
    crawl_read_count_task,
    read_count_metrics,
    read_count_writeback_values,
)
from apps.finance_crawler.crawler_app.strategies.post import (
    crawl_detail_task,
    crawl_initial_check_task,
    detail_metrics,
    detail_writeback_values,
    initial_check_metrics,
    initial_check_writeback_values,
)
from apps.finance_crawler.crawler_app.tasks.types import DETAIL, INITIAL_CHECK, READ_COUNT


@dataclass(frozen=True)
class TaskHandler:
    task_type: str
    worker_id: str
    runtime: ExecutionRuntime
    crawl: Callable[[dict[str, Any]], dict[str, Any]]
    writeback_values: Callable[[dict[str, Any]], dict[str, Any]]
    metrics: Callable[[dict[str, Any]], dict[str, Any]]


READ_COUNT_HANDLER = TaskHandler(
    task_type=READ_COUNT,
    worker_id="read_count",
    runtime=ADB_RUNTIME,
    crawl=crawl_read_count_task,
    writeback_values=read_count_writeback_values,
    metrics=read_count_metrics,
)

INITIAL_CHECK_HANDLER = TaskHandler(
    task_type=INITIAL_CHECK,
    worker_id="initial_check",
    runtime=ADB_RUNTIME,
    crawl=crawl_initial_check_task,
    writeback_values=initial_check_writeback_values,
    metrics=initial_check_metrics,
)

DETAIL_HANDLER = TaskHandler(
    task_type=DETAIL,
    worker_id="detail",
    runtime=ADB_RUNTIME,
    crawl=crawl_detail_task,
    writeback_values=detail_writeback_values,
    metrics=detail_metrics,
)

TASK_HANDLERS = {
    READ_COUNT: READ_COUNT_HANDLER,
    INITIAL_CHECK: INITIAL_CHECK_HANDLER,
    DETAIL: DETAIL_HANDLER,
}


def get_task_handler(task_type: str) -> TaskHandler:
    try:
        return TASK_HANDLERS[task_type]
    except KeyError as exc:
        raise ValueError(f"unsupported crawler_app task type: {task_type}") from exc
