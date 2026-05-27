"""Daily detail-crawl job entrypoint."""

from __future__ import annotations

from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils.rate_limiter import TaskBudgetExceeded
from apps.finance_crawler.workflows.detail_crawl import run_detail_crawl

logger = get_logger("detail_crawl")


def run_detail(limit: int | None = None) -> list[dict]:
    return run_detail_crawl(limit)


if __name__ == "__main__":
    try:
        run_detail()
    except TaskBudgetExceeded as exc:
        logger.warning("detail crawl stopped by runtime budget: %s", exc)
