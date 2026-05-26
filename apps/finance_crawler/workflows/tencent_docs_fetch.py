"""Fetch workflow for importing Tencent Docs links into storage."""

from __future__ import annotations

import time
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.sources.tencent_docs import TencentDocsSource
from apps.finance_crawler.storage.db import log_task, upsert_post
from apps.finance_crawler.utils.link_source import resolve_source_app
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("fetch_docs_workflow")


def fetch_and_save(limit: int | None = None) -> list[dict[str, Any]]:
    """Import eligible Tencent Docs rows into the legacy and framework tables."""

    start = time.time()
    source = TencentDocsSource(limit=limit)

    try:
        records = source.fetch_records()
        candidates: list[dict[str, Any]] = []
        new_count = 0
        by_source: dict[str, int] = {}
        for record in records:
            if record.post_time is None:
                logger.warning("skip source record without post_time: %s", record.record_id)
                continue

            row_index = record.locator.get("row_index")
            source_app = resolve_source_app(record.app_type, record.url)
            inserted = upsert_post(
                record.url,
                record.post_time,
                row_index=row_index,
                file_id=source.doc.file_id,
                sheet_id=source.doc.sheet_id,
                source_app=source_app,
            )
            by_source[source_app] = by_source.get(source_app, 0) + 1
            if inserted:
                new_count += 1
            candidate = dict(record.raw)
            candidate.update(
                {
                    "url": record.url,
                    "source_app": source_app,
                    "post_time": record.post_time,
                    "row_index": row_index,
                }
            )
            candidates.append(candidate)

        duration = time.time() - start
        source_summary = ", ".join(f"{key}={value}" for key, value in sorted(by_source.items())) or "none"
        msg = (
            f"eligible={len(candidates)}, new={new_count}, "
            f"limit={source.limit or 'all'}, older_than={Config.POST_ELIGIBLE_HOURS}h, "
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
