"""Build and submit crawl tasks from normalized document rows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from apps.finance_crawler.crawler_app.documents.rows import DocumentSourceRow


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    task_type: str
    document_id: int
    sheet_id: str
    row_index: int
    app_type: str
    post_url: str
    account_name: str
    post_time: str
    source_locator: dict[str, object]
    dedupe_key: str
    source_row_id: int | None = None
    priority: int = 0
    max_attempts: int = 3
    created_by: str = "system"


def build_task_submission(
    row: DocumentSourceRow,
    *,
    document_id: int,
    sheet_id: str,
    task_type: str,
    app_type: str = "unknown",
    source_row_id: int | None = None,
    source_locator_extra: dict[str, object] | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    created_by: str = "system",
) -> TaskSubmission:
    source_locator = {
        "document_id": document_id,
        "sheet_id": sheet_id,
        "row_index": row.row_index,
        "business_date": row.business_date.isoformat() if row.business_date else None,
    }
    if source_locator_extra:
        source_locator.update(source_locator_extra)
    return TaskSubmission(
        task_type=task_type,
        document_id=document_id,
        sheet_id=sheet_id,
        row_index=row.row_index,
        app_type=app_type,
        post_url=row.post_url,
        account_name=row.account_name,
        post_time=row.post_time,
        source_locator=source_locator,
        dedupe_key=make_dedupe_key(document_id, sheet_id, row.row_index, row.post_url, task_type),
        source_row_id=source_row_id,
        priority=priority,
        max_attempts=max_attempts,
        created_by=created_by,
    )


def make_dedupe_key(document_id: int, sheet_id: str, row_index: int, post_url: str, task_type: str) -> str:
    payload = json.dumps(
        {
            "document_id": document_id,
            "sheet_id": sheet_id,
            "post_url": post_url,
            "task_type": task_type,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def submit_task_submissions(conn, submissions: list[TaskSubmission]) -> int:
    if not submissions:
        return 0
    rows = [
        (
            item.task_type,
            item.source_row_id,
            item.document_id,
            item.sheet_id,
            item.row_index,
            item.app_type,
            item.post_url,
            item.account_name,
            item.post_time,
            json.dumps(item.source_locator, ensure_ascii=False, sort_keys=True),
            item.dedupe_key,
            item.priority,
            item.max_attempts,
            item.created_by,
        )
        for item in submissions
    ]
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO task_submissions (
                task_type, source_row_id, document_id, sheet_id, row_index, app_type,
                post_url, account_name, post_time, source_locator_json, dedupe_key,
                priority, max_attempts, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                source_row_id = VALUES(source_row_id),
                row_index = VALUES(row_index),
                post_url = VALUES(post_url),
                account_name = VALUES(account_name),
                post_time = VALUES(post_time),
                source_locator_json = VALUES(source_locator_json),
                priority = VALUES(priority),
                max_attempts = VALUES(max_attempts),
                status = CASE
                    WHEN task_submissions.status IN ('failed', 'error') THEN 'pending'
                    ELSE task_submissions.status
                END,
                attempts = CASE
                    WHEN task_submissions.status IN ('failed', 'error') THEN 0
                    ELSE task_submissions.attempts
                END,
                latest_execution_id = CASE
                    WHEN task_submissions.status IN ('failed', 'error') THEN NULL
                    ELSE task_submissions.latest_execution_id
                END,
                last_error = CASE
                    WHEN task_submissions.status IN ('failed', 'error') THEN NULL
                    ELSE task_submissions.last_error
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
        return int(cursor.rowcount)


def submit_crawl_tasks(conn, submissions: list[TaskSubmission]) -> int:
    return submit_task_submissions(conn, submissions)


def submission_to_dict(item: TaskSubmission) -> dict[str, object]:
    return asdict(item)


CrawlTaskSubmission = TaskSubmission
