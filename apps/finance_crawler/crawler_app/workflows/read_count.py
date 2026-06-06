"""Unified v2 read-count workflow."""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

from apps.finance_crawler.crawler_app.documents.intake import (
    submit_read_count_tasks_from_tencent_doc,
    summary_to_dict,
)
from apps.finance_crawler.crawler_app.storage.db import get_conn
from apps.finance_crawler.crawler_app.tasks.handlers import READ_COUNT_HANDLER
from apps.finance_crawler.crawler_app.workflows.execution import crawl_pending_tasks
from apps.finance_crawler.crawler_app.writeback.executor import apply_pending_writebacks
from apps.finance_crawler.storage.db import log_task


def submit_read_count_tasks(
    *,
    doc_url: str | None = None,
    target_date: date | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    started = time.time()
    conn = get_conn()
    try:
        summary = submit_read_count_tasks_from_tencent_doc(
            conn,
            doc_url=doc_url,
            target_date=target_date,
            limit=limit,
        )
        conn.commit()
        output = summary_to_dict(summary)
        log_task("v2_read_count_submit", "success", json.dumps(output, ensure_ascii=False), time.time() - started)
        return output
    except Exception as exc:
        conn.rollback()
        log_task("v2_read_count_submit", "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def crawl_pending_read_count_tasks(*, limit: int | None = None) -> dict[str, Any]:
    started = time.time()
    try:
        summary = crawl_pending_tasks(READ_COUNT_HANDLER, limit=limit)
        log_task("v2_read_count_crawl", "success", json.dumps(_log_safe(summary), ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        log_task("v2_read_count_crawl", "error", str(exc), time.time() - started)
        raise


def writeback_read_count_results(*, limit: int | None = None) -> dict[str, Any]:
    started = time.time()
    conn = get_conn()
    try:
        summary = apply_pending_writebacks(conn, limit=limit)
        conn.commit()
        log_task("v2_read_count_writeback", "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        conn.rollback()
        log_task("v2_read_count_writeback", "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def run_read_count_workflow(
    *,
    doc_url: str | None = None,
    target_date: date | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    submitted = submit_read_count_tasks(doc_url=doc_url, target_date=target_date, limit=limit)
    crawled = crawl_pending_read_count_tasks(limit=limit)
    written = writeback_read_count_results()
    return {"submitted": submitted, "crawled": crawled, "written": written}


def _log_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _log_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_log_safe(item) for item in value]
    return str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value
