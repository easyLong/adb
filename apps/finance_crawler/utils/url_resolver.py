"""Parallel URL resolution helpers for app deep links."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from apps.finance_crawler.config import Config
from apps.finance_crawler.utils.record_identity import workflow_record_id, workflow_record_url


def resolve_urls(
    records: list[dict],
    resolver: Callable[[str], str],
    logger,
) -> dict[int, str]:
    if not records:
        return {}

    workers = max(Config.URL_RESOLVE_WORKERS, 1)
    if workers == 1 or len(records) == 1:
        resolved: dict[int, str] = {}
        for record in records:
            record_id = workflow_record_id(record)
            url = workflow_record_url(record)
            try:
                resolved[record_id] = resolver(url)
            except Exception as exc:
                logger.warning("URL resolve failed id=%s: %s", record_id, exc)
                resolved[record_id] = url
        return resolved

    resolved = {workflow_record_id(record): workflow_record_url(record) for record in records}
    with ThreadPoolExecutor(max_workers=min(workers, len(records))) as executor:
        future_map = {
            executor.submit(resolver, workflow_record_url(record)): record
            for record in records
        }
        for future in as_completed(future_map):
            record = future_map[future]
            record_id = workflow_record_id(record)
            try:
                resolved[record_id] = future.result()
            except Exception as exc:
                logger.warning("URL resolve failed id=%s: %s", record_id, exc)
    return resolved
