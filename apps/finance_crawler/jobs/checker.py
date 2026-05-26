"""Initial check job entrypoint."""

from __future__ import annotations

from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils.rate_limiter import TaskBudgetExceeded
from apps.finance_crawler.workflows.initial_check import run_initial_check

logger = get_logger("checker")


def run_check() -> list[dict]:
    return run_initial_check()


if __name__ == "__main__":
    try:
        run_check()
    except TaskBudgetExceeded as exc:
        logger.warning("check stopped by runtime budget: %s", exc)
