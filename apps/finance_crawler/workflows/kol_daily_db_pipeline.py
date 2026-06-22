"""Database-first daily KOL metrics pipeline."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots import (
    KOL_DAILY_CRAWL_FIELDS,
    KOL_DAILY_CRAWL_SOURCE_NAME,
    ensure_kol_daily_metric_rows_from_base_profiles,
    run_kol_daily_crawl_db_pipeline,
)
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.workflows.kol_tenpay_external_reads import (
    run_kol_tenpay_external_reads_db_lookback,
)

logger = get_logger("kol_daily_db_pipeline")


def run_kol_daily_db_pipeline(
    *,
    target_date: date | None = None,
    read_lookback_days: int | None = None,
    crawl_limit: int | None = None,
) -> dict[str, Any]:
    """Run daily KOL metrics in strict DB-first order.

    Order:
    1. Ensure today's database rows from KOL base profiles.
    2. Update recent Tenpay read counts in kol_daily_metrics.
    3. Crawl today's homepage fans/growth metrics into kol_daily_metrics.
    """

    resolved_date = target_date or date.today()
    resolved_lookback_days = max(
        int(read_lookback_days if read_lookback_days is not None else Config.KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS),
        1,
    )
    init_summary = ensure_kol_daily_metric_rows_from_base_profiles(metric_date=resolved_date)
    read_summary = run_kol_tenpay_external_reads_db_lookback(
        end_date=resolved_date - timedelta(days=1),
        days=resolved_lookback_days,
    )
    fans_summary = run_kol_daily_crawl_db_pipeline(
        target_date=resolved_date,
        limit=crawl_limit,
        source_name=KOL_DAILY_CRAWL_SOURCE_NAME,
        requested_fields=KOL_DAILY_CRAWL_FIELDS,
    )
    summary = {
        "date": resolved_date.isoformat(),
        "mode": "database_first_serial",
        "steps": ["daily_init", "tenpay_external_reads", "profile_fans_growth"],
        "daily_init": init_summary,
        "read_counts": read_summary,
        "fans_growth": fans_summary,
    }
    logger.info("KOL daily DB pipeline summary: %s", summary)
    return summary
