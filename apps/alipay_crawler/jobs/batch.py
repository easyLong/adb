"""Daily batch job entrypoint."""

from __future__ import annotations

from apps.alipay_crawler.utils.logger import get_logger
from apps.alipay_crawler.utils.rate_limiter import TaskBudgetExceeded
from apps.alipay_crawler.workflows.batch_crawl import run_batch_crawl

logger = get_logger("batch")


def run_batch(limit: int | None = None) -> list[dict]:
    return run_batch_crawl(limit)


if __name__ == "__main__":
    try:
        run_batch()
    except TaskBudgetExceeded as exc:
        logger.warning("batch stopped by runtime budget: %s", exc)
