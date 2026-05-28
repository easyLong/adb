"""Fetch workflow for importing Tencent Docs links into storage."""

from __future__ import annotations

import time
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.sources.tencent_docs import TencentDocsSource
from apps.finance_crawler.storage.db import log_task
from apps.finance_crawler.storage.framework_db import upsert_source_record_submissions
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("fetch_docs_workflow")


def fetch_and_save(limit: int | None = None) -> list[dict[str, Any]]:
    """Import eligible Tencent Docs rows into framework task submissions."""

    start = time.time()
    source = TencentDocsSource(limit=limit)

    try:
        records = source.fetch_records()
        candidates: list[dict[str, Any]] = []
        submission_count = 0
        by_source: dict[str, int] = {}
        for record in records:
            if record.source_time is None:
                logger.warning("skip source record without source_time: %s", record.record_id)
                continue

            row_index = record.locator.get("row_index")
            source_app = resolve_source_app(record.app_type, record.url)
            submissions = upsert_source_record_submissions(
                source_type=record.source_type,
                source_name=record.source_name,
                source_config={
                    "file_id": record.locator.get("file_id"),
                    "sheet_id": record.locator.get("sheet_id"),
                    "sheet_title": record.locator.get("sheet_title"),
                },
                source_locator=record.locator,
                url=record.url,
                source_time=record.source_time,
                source_app=source_app,
                created_by="fetch_docs",
            )
            by_source[source_app] = by_source.get(source_app, 0) + 1
            submission_count += len(submissions)
            candidate = dict(record.raw)
            candidate.update(
                {
                    "url": record.url,
                    "source_app": source_app,
                    "source_time": record.source_time,
                    "row_index": row_index,
                    "submissions": submissions,
                }
            )
            candidates.append(candidate)

        duration = time.time() - start
        source_summary = ", ".join(f"{key}={value}" for key, value in sorted(by_source.items())) or "none"
        msg = (
            f"eligible={len(candidates)}, submissions={submission_count}, "
            f"limit={source.limit or 'all'}, initial_check_delay={Config.INITIAL_CHECK_DELAY_HOURS}h, "
            f"sources={source_summary}"
        )
        logger.info("Tencent Docs fetch workflow finished: %s", msg)
        log_task("fetch_docs", "success", msg, duration)
        return candidates
    except Exception as exc:
        duration = time.time() - start
        logger.exception("Tencent Docs fetch workflow failed")
        log_task("fetch_docs", "error", str(exc), duration)
        raise
