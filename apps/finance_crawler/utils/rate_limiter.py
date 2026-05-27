"""Runtime budgets and throttling for device automation jobs."""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Sequence, TypeVar

from apps.finance_crawler.config import Config

T = TypeVar("T")


class TaskBudgetExceeded(RuntimeError):
    """Raised when a crawl job should stop before it becomes risky."""


def _minutes(value: str) -> int | None:
    if not value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def ensure_active_window() -> None:
    start = _minutes(Config.CRAWL_ACTIVE_START)
    end = _minutes(Config.CRAWL_ACTIVE_END)
    if start is None or end is None:
        return

    now = datetime.now()
    current = now.hour * 60 + now.minute
    if start <= end:
        allowed = start <= current <= end
    else:
        allowed = current >= start or current <= end
    if not allowed:
        raise TaskBudgetExceeded(
            f"outside crawl active window {Config.CRAWL_ACTIVE_START}-{Config.CRAWL_ACTIVE_END}"
        )


class OperationBudget:
    def __init__(self, task_name: str) -> None:
        self.task_name = task_name
        self.started_at = time.monotonic()
        self.consecutive_errors = 0

    def limit_items(self, items: Sequence[T]) -> list[T]:
        limit = Config.MAX_RECORDS_PER_RUN
        if limit and limit > 0:
            return list(items[:limit])
        return list(items)

    def check(self) -> None:
        ensure_active_window()
        max_seconds = Config.CRAWL_MAX_TASK_SECONDS
        if max_seconds and time.monotonic() - self.started_at >= max_seconds:
            raise TaskBudgetExceeded(f"{self.task_name} exceeded {max_seconds}s runtime budget")
        if (
            Config.CRAWL_MAX_CONSECUTIVE_ERRORS > 0
            and self.consecutive_errors >= Config.CRAWL_MAX_CONSECUTIVE_ERRORS
        ):
            raise TaskBudgetExceeded(
                f"{self.task_name} stopped after {self.consecutive_errors} consecutive errors"
            )

    def record_status(self, status: str) -> None:
        if status in {"success", "not_found", "deleted"}:
            self.consecutive_errors = 0
        else:
            self.consecutive_errors += 1

    def sleep(self) -> None:
        delay_min = max(Config.POST_DELAY_MIN, 0)
        delay_max = max(Config.POST_DELAY_MAX, delay_min)
        time.sleep(random.uniform(delay_min, delay_max))
