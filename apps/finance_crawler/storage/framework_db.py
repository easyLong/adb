"""Generic framework tables for sources, tasks, crawl results, and writebacks."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from apps.finance_crawler.crawlers.registry import iter_app_profiles
from apps.finance_crawler.domain.records import CrawlResult, SourceRecord, WritebackResult
from apps.finance_crawler.utils.link_source import resolve_source_app


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _record_key_for_url(url: str) -> str:
    return f"url:{hashlib.sha1(url.encode('utf-8')).hexdigest()}"


def _hash_key(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"


def _record_key_for_source_url(prefix: str, *source_parts: Any, url: str) -> str:
    return _hash_key(prefix, *source_parts, url.strip())


def ensure_framework_tables(cursor) -> None:
    """Create framework-level tables without changing legacy business tables."""

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_sources (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            source_type VARCHAR(64) NOT NULL,
            name VARCHAR(128) NOT NULL,
            config_json LONGTEXT NULL,
            enabled TINYINT DEFAULT 1,
            last_fetched_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_source_type_name (source_type, name),
            INDEX idx_source_type (source_type),
            INDEX idx_enabled (enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawler_apps (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            app_type VARCHAR(64) NOT NULL,
            display_name VARCHAR(128) NOT NULL,
            package_name VARCHAR(128) NULL,
            enabled TINYINT DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_app_type (app_type),
            INDEX idx_enabled (enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_jobs (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            job_type VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'running',
            source_id BIGINT UNSIGNED NULL,
            started_at DATETIME NOT NULL,
            finished_at DATETIME NULL,
            summary_json LONGTEXT NULL,
            error TEXT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_job_type (job_type),
            INDEX idx_status (status),
            INDEX idx_source_id (source_id),
            INDEX idx_started_at (started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_tasks (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            job_id BIGINT UNSIGNED NULL,
            legacy_post_id INT NULL,
            source_id BIGINT UNSIGNED NULL,
            source_type VARCHAR(64) NOT NULL,
            source_record_key VARCHAR(191) NOT NULL,
            source_locator_json LONGTEXT NULL,
            app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
            original_url VARCHAR(1000) NOT NULL,
            canonical_url VARCHAR(1000) NULL,
            source_time DATETIME NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            priority INT NOT NULL DEFAULT 0,
            scheduled_at DATETIME NULL,
            locked_at DATETIME NULL,
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 3,
            error TEXT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_source_record (source_type, source_record_key),
            INDEX idx_task_status (status),
            INDEX idx_task_app (app_type),
            INDEX idx_task_source (source_id),
            INDEX idx_task_job (job_id),
            INDEX idx_legacy_post (legacy_post_id),
            INDEX idx_original_url (original_url(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_results (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            task_id BIGINT UNSIGNED NULL,
            legacy_post_id INT NULL,
            app_type VARCHAR(64) NOT NULL,
            url VARCHAR(1000) NOT NULL,
            workflow VARCHAR(64) NULL,
            status VARCHAR(32) NOT NULL,
            account_name VARCHAR(255) NULL,
            content MEDIUMTEXT NULL,
            metrics_json LONGTEXT NULL,
            screenshot_path VARCHAR(700) NULL,
            error TEXT NULL,
            crawled_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_result_task (task_id),
            INDEX idx_result_legacy_post (legacy_post_id),
            INDEX idx_result_app (app_type),
            INDEX idx_result_workflow (workflow),
            INDEX idx_result_status (status),
            INDEX idx_result_url (url(191)),
            INDEX idx_crawled_at (crawled_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_writebacks (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            task_id BIGINT UNSIGNED NULL,
            result_id BIGINT UNSIGNED NULL,
            legacy_post_id INT NULL,
            sink_type VARCHAR(64) NOT NULL,
            sink_locator_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL,
            error TEXT NULL,
            written_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_writeback_task (task_id),
            INDEX idx_writeback_result (result_id),
            INDEX idx_writeback_legacy_post (legacy_post_id),
            INDEX idx_writeback_sink (sink_type),
            INDEX idx_writeback_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_task_submissions (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            task_type VARCHAR(64) NOT NULL DEFAULT 'batch',
            source_id BIGINT UNSIGNED NULL,
            source_type VARCHAR(64) NOT NULL,
            source_name VARCHAR(191) NULL,
            source_record_key VARCHAR(191) NOT NULL,
            source_locator_json LONGTEXT NULL,
            app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
            original_url VARCHAR(1000) NOT NULL,
            canonical_url VARCHAR(1000) NULL,
            source_time DATETIME NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            priority INT NOT NULL DEFAULT 0,
            scheduled_at DATETIME NULL,
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 3,
            latest_execution_id BIGINT UNSIGNED NULL,
            last_error TEXT NULL,
            result_summary_json LONGTEXT NULL,
            created_by VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_submission_record (source_type, source_record_key, task_type),
            INDEX idx_submission_status (status),
            INDEX idx_submission_app (app_type),
            INDEX idx_submission_source (source_id),
            INDEX idx_submission_schedule (scheduled_at),
            INDEX idx_submission_priority (priority),
            INDEX idx_submission_latest_execution (latest_execution_id),
            INDEX idx_submission_url (original_url(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_task_executions (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            submission_id BIGINT UNSIGNED NOT NULL,
            job_id BIGINT UNSIGNED NULL,
            attempt_no INT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'queued',
            worker_id VARCHAR(128) NULL,
            app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
            url VARCHAR(1000) NOT NULL,
            account_name VARCHAR(255) NULL,
            content MEDIUMTEXT NULL,
            metrics_json LONGTEXT NULL,
            result_json LONGTEXT NULL,
            screenshot_path VARCHAR(700) NULL,
            writeback_status VARCHAR(32) NULL,
            writeback_locator_json LONGTEXT NULL,
            writeback_error TEXT NULL,
            error TEXT NULL,
            started_at DATETIME NULL,
            heartbeat_at DATETIME NULL,
            finished_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_submission_attempt (submission_id, attempt_no),
            INDEX idx_execution_submission (submission_id),
            INDEX idx_execution_job (job_id),
            INDEX idx_execution_status (status),
            INDEX idx_execution_app (app_type),
            INDEX idx_execution_started (started_at),
            INDEX idx_execution_finished (finished_at),
            INDEX idx_execution_url (url(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    seed_crawler_apps(cursor)


def seed_crawler_apps(cursor) -> None:
    apps = [
        (profile.source_app, profile.display_name, profile.package_name)
        for profile in iter_app_profiles()
    ]
    apps.append(("unknown", "Unknown App", None))
    cursor.executemany(
        """
        INSERT INTO crawler_apps (app_type, display_name, package_name)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            display_name = VALUES(display_name),
            package_name = VALUES(package_name),
            enabled = 1
        """,
        apps,
    )


def upsert_crawl_source_tx(
    cursor,
    source_type: str,
    name: str,
    config: dict[str, Any] | None = None,
) -> int:
    cursor.execute(
        """
        INSERT INTO crawl_sources (source_type, name, config_json)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            config_json = VALUES(config_json),
            enabled = 1
        """,
        (source_type, name, _json_dumps(config)),
    )
    return int(cursor.lastrowid)


def upsert_crawl_task_tx(
    cursor,
    record: SourceRecord,
    *,
    job_id: int | None = None,
    source_id: int | None = None,
    legacy_post_id: int | None = None,
    status: str = "pending",
    max_attempts: int = 3,
) -> int:
    app_type = resolve_source_app(record.app_type, record.url)
    cursor.execute(
        """
        INSERT INTO crawl_tasks
            (job_id, legacy_post_id, source_id, source_type, source_record_key,
             source_locator_json, app_type, original_url, source_time, status, max_attempts)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            job_id = COALESCE(VALUES(job_id), job_id),
            legacy_post_id = COALESCE(VALUES(legacy_post_id), legacy_post_id),
            source_id = COALESCE(VALUES(source_id), source_id),
            source_locator_json = VALUES(source_locator_json),
            app_type = VALUES(app_type),
            original_url = VALUES(original_url),
            source_time = VALUES(source_time),
            status = IF(status IN ('done', 'success'), status, VALUES(status)),
            max_attempts = VALUES(max_attempts)
        """,
        (
            job_id,
            legacy_post_id,
            source_id,
            record.source_type,
            record.record_id,
            _json_dumps(record.locator),
            app_type,
            record.url,
            record.post_time,
            status,
            max_attempts,
        ),
    )
    return int(cursor.lastrowid)


def upsert_task_submission_tx(
    cursor,
    *,
    task_type: str,
    source_type: str,
    source_record_key: str,
    original_url: str,
    source_name: str | None = None,
    source_id: int | None = None,
    source_locator: dict[str, Any] | None = None,
    app_type: str | None = None,
    canonical_url: str | None = None,
    source_time: datetime | None = None,
    priority: int = 0,
    scheduled_at: datetime | None = None,
    max_attempts: int = 3,
    created_by: str | None = None,
) -> int:
    app = resolve_source_app(app_type, original_url)
    _migrate_existing_submission_key_tx(
        cursor,
        task_type=task_type,
        source_type=source_type,
        source_name=source_name,
        source_record_key=source_record_key,
        original_url=original_url,
    )
    cursor.execute(
        """
        INSERT INTO crawl_task_submissions
            (task_type, source_id, source_type, source_name, source_record_key,
             source_locator_json, app_type, original_url, canonical_url, source_time,
             status, priority, scheduled_at, max_attempts, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            id = LAST_INSERT_ID(id),
            source_id = COALESCE(VALUES(source_id), source_id),
            source_name = VALUES(source_name),
            source_locator_json = VALUES(source_locator_json),
            app_type = VALUES(app_type),
            original_url = VALUES(original_url),
            canonical_url = VALUES(canonical_url),
            source_time = VALUES(source_time),
            priority = VALUES(priority),
            scheduled_at = VALUES(scheduled_at),
            max_attempts = VALUES(max_attempts)
        """,
        (
            task_type,
            source_id,
            source_type,
            source_name,
            source_record_key,
            _json_dumps(source_locator),
            app,
            original_url,
            canonical_url,
            source_time,
            priority,
            scheduled_at,
            max_attempts,
            created_by,
        ),
    )
    return int(cursor.lastrowid)


def _migrate_existing_submission_key_tx(
    cursor,
    *,
    task_type: str,
    source_type: str,
    source_name: str | None,
    source_record_key: str,
    original_url: str,
) -> None:
    cursor.execute(
        """
        SELECT id, source_record_key
        FROM crawl_task_submissions
        WHERE task_type = %s
          AND source_type = %s
          AND source_name <=> %s
          AND original_url = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (task_type, source_type, source_name, original_url),
    )
    row = cursor.fetchone()
    if not row or row["source_record_key"] == source_record_key:
        return
    try:
        cursor.execute(
            """
            UPDATE crawl_task_submissions
            SET source_record_key = %s
            WHERE id = %s
            """,
            (source_record_key, row["id"]),
        )
    except Exception:
        # If another row already owns the stable key, the upsert below will
        # merge into it via uk_submission_record.
        pass


def upsert_legacy_post_task_tx(
    cursor,
    *,
    post_id: int,
    url: str,
    post_time: datetime,
    row_index: int | None = None,
    file_id: str | None = None,
    sheet_id: str | None = None,
    source_app: str | None = None,
) -> int:
    source_type = "tencent_docs" if file_id or sheet_id or row_index else "manual"
    source_name = f"{file_id or 'unknown'}:{sheet_id or 'unknown'}" if source_type == "tencent_docs" else "manual"
    source_id = upsert_crawl_source_tx(
        cursor,
        source_type,
        source_name,
        {"file_id": file_id, "sheet_id": sheet_id} if source_type == "tencent_docs" else None,
    )
    record_id = (
        f"{file_id or 'unknown'}:{sheet_id or 'unknown'}:{row_index}"
        if source_type == "tencent_docs" and row_index
        else _record_key_for_url(url)
    )
    record = SourceRecord(
        record_id=record_id,
        source_type=source_type,
        source_name=source_name,
        url=url,
        app_type=resolve_source_app(source_app, url),
        post_time=post_time,
        locator={"file_id": file_id, "sheet_id": sheet_id, "row_index": row_index},
        raw={"legacy_post_id": post_id},
    )
    return upsert_crawl_task_tx(
        cursor,
        record,
        source_id=source_id,
        legacy_post_id=post_id,
        status="pending",
    )


def upsert_legacy_post_submission_tx(
    cursor,
    *,
    post_id: int,
    url: str,
    post_time: datetime,
    row_index: int | None = None,
    file_id: str | None = None,
    sheet_id: str | None = None,
    source_app: str | None = None,
    task_type: str = "batch_crawl",
) -> int:
    source_type = "tencent_docs" if file_id or sheet_id or row_index else "manual"
    source_name = f"{file_id or 'unknown'}:{sheet_id or 'unknown'}" if source_type == "tencent_docs" else "manual"
    source_id = upsert_crawl_source_tx(
        cursor,
        source_type,
        source_name,
        {"file_id": file_id, "sheet_id": sheet_id} if source_type == "tencent_docs" else None,
    )
    record_key = (
        _record_key_for_source_url("tencent_docs_url", file_id, sheet_id, url=url)
        if source_type == "tencent_docs"
        else _record_key_for_url(url)
    )
    return upsert_task_submission_tx(
        cursor,
        task_type=task_type,
        source_id=source_id,
        source_type=source_type,
        source_name=source_name,
        source_record_key=record_key,
        source_locator={
            "legacy_post_id": post_id,
            "file_id": file_id,
            "sheet_id": sheet_id,
            "row_index": row_index,
        },
        app_type=source_app,
        original_url=url,
        source_time=post_time,
        created_by="fetch",
    )


def upsert_submission_for_legacy_post(
    post: dict[str, Any],
    *,
    task_type: str = "batch_crawl",
) -> int:
    """Ensure a legacy post has a task-center submission."""

    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            submission_id = upsert_legacy_post_submission_tx(
                cursor,
                post_id=int(post["id"]),
                url=post["url"],
                post_time=post["post_time"],
                row_index=post.get("doc_row_index"),
                file_id=post.get("doc_file_id"),
                sheet_id=post.get("doc_sheet_id"),
                source_app=post.get("source_app"),
                task_type=task_type,
            )
        conn.commit()
        return submission_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_excel_row_submission(
    *,
    path: str,
    sheet_name: str,
    row_index: int,
    url: str,
    source_app: str | None = None,
    output_path: str | None = None,
    task_type: str = "batch_crawl",
    max_attempts: int = 3,
) -> int:
    """Create or refresh a submission for one local Excel row."""

    from apps.finance_crawler.storage.db import get_conn

    source_type = "excel"
    source_name = _hash_key("excel_source", path, sheet_name)
    record_key = _record_key_for_source_url("excel_url", path, sheet_name, url=url)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            source_id = upsert_crawl_source_tx(
                cursor,
                source_type,
                source_name,
                {"path": path, "sheet_name": sheet_name},
            )
            submission_id = upsert_task_submission_tx(
                cursor,
                task_type=task_type,
                source_id=source_id,
                source_type=source_type,
                source_name=source_name,
                source_record_key=record_key,
                source_locator={
                    "path": path,
                    "sheet_name": sheet_name,
                    "row_index": row_index,
                    "output_path": output_path,
                },
                app_type=source_app,
                original_url=url,
                max_attempts=max_attempts,
                created_by="excel_batch",
            )
        conn.commit()
        return submission_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_crawl_job(
    job_type: str,
    *,
    source_id: int | None = None,
    status: str = "running",
    summary: dict[str, Any] | None = None,
) -> int:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_jobs (job_type, status, source_id, started_at, summary_json)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (job_type, status, source_id, datetime.now(), _json_dumps(summary)),
            )
            job_id = int(cursor.lastrowid)
        conn.commit()
        return job_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_crawl_job(
    job_id: int,
    status: str,
    *,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE crawl_jobs
                SET status = %s,
                    finished_at = %s,
                    summary_json = %s,
                    error = %s
                WHERE id = %s
                """,
                (status, datetime.now(), _json_dumps(summary), error, job_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_task_submission(
    *,
    task_type: str,
    source_type: str,
    source_record_key: str,
    original_url: str,
    source_name: str | None = None,
    source_id: int | None = None,
    source_locator: dict[str, Any] | None = None,
    app_type: str | None = None,
    canonical_url: str | None = None,
    source_time: datetime | None = None,
    priority: int = 0,
    scheduled_at: datetime | None = None,
    max_attempts: int = 3,
    created_by: str | None = None,
) -> int:
    """Create or refresh a task submission.

    A submission is the user/business intent to crawl one row/link. Execution
    attempts are append-only rows in ``crawl_task_executions``.
    """

    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            submission_id = upsert_task_submission_tx(
                cursor,
                task_type=task_type,
                source_id=source_id,
                source_type=source_type,
                source_name=source_name,
                source_record_key=source_record_key,
                source_locator=source_locator,
                app_type=app_type,
                original_url=original_url,
                canonical_url=canonical_url,
                source_time=source_time,
                priority=priority,
                scheduled_at=scheduled_at,
                max_attempts=max_attempts,
                created_by=created_by,
            )
        conn.commit()
        return submission_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def start_task_execution(
    submission_id: int,
    *,
    job_id: int | None = None,
    worker_id: str | None = None,
) -> int:
    """Create an execution attempt and mark its submission running."""

    from apps.finance_crawler.storage.db import get_conn

    now = datetime.now()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, attempts, app_type, original_url
                FROM crawl_task_submissions
                WHERE id = %s
                FOR UPDATE
                """,
                (submission_id,),
            )
            submission = cursor.fetchone()
            if not submission:
                raise ValueError(f"task submission not found: {submission_id}")
            if submission["status"] not in {"pending", "failed_retryable"}:
                raise ValueError(
                    f"task submission {submission_id} is not runnable: {submission['status']}"
                )
            attempt_no = int(submission["attempts"] or 0) + 1
            cursor.execute(
                """
                INSERT INTO crawl_task_executions
                    (submission_id, job_id, attempt_no, status, worker_id, app_type, url,
                     started_at, heartbeat_at)
                VALUES (%s, %s, %s, 'running', %s, %s, %s, %s, %s)
                """,
                (
                    submission_id,
                    job_id,
                    attempt_no,
                    worker_id,
                    submission["app_type"],
                    submission["original_url"],
                    now,
                    now,
                ),
            )
            execution_id = int(cursor.lastrowid)
            cursor.execute(
                """
                UPDATE crawl_task_submissions
                SET status = 'running',
                    attempts = %s,
                    latest_execution_id = %s,
                    last_error = NULL
                WHERE id = %s
                """,
                (attempt_no, execution_id, submission_id),
            )
        conn.commit()
        return execution_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_task_execution(
    execution_id: int,
    *,
    status: str,
    account_name: str | None = None,
    content: str | None = None,
    metrics: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    screenshot_path: str | None = None,
    writeback_status: str | None = None,
    writeback_locator: dict[str, Any] | None = None,
    writeback_error: str | None = None,
    error: str | None = None,
) -> None:
    """Finish one execution and synchronize the parent submission status."""

    from apps.finance_crawler.storage.db import get_conn

    now = datetime.now()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT submission_id, attempt_no
                FROM crawl_task_executions
                WHERE id = %s
                FOR UPDATE
                """,
                (execution_id,),
            )
            execution = cursor.fetchone()
            if not execution:
                raise ValueError(f"task execution not found: {execution_id}")

            submission_id = int(execution["submission_id"])
            cursor.execute(
                """
                SELECT max_attempts
                FROM crawl_task_submissions
                WHERE id = %s
                FOR UPDATE
                """,
                (submission_id,),
            )
            submission = cursor.fetchone()
            if not submission:
                raise ValueError(f"task submission not found: {submission_id}")

            summary = _execution_summary(
                status=status,
                account_name=account_name,
                metrics=metrics,
                result=result,
                screenshot_path=screenshot_path,
                writeback_status=writeback_status,
            )
            cursor.execute(
                """
                UPDATE crawl_task_executions
                SET status = %s,
                    account_name = %s,
                    content = %s,
                    metrics_json = %s,
                    result_json = %s,
                    screenshot_path = %s,
                    writeback_status = %s,
                    writeback_locator_json = %s,
                    writeback_error = %s,
                    error = %s,
                    heartbeat_at = %s,
                    finished_at = %s
                WHERE id = %s
                """,
                (
                    status,
                    account_name,
                    content,
                    _json_dumps(metrics),
                    _json_dumps(result),
                    screenshot_path,
                    writeback_status,
                    _json_dumps(writeback_locator),
                    writeback_error,
                    error,
                    now,
                    now,
                    execution_id,
                ),
            )
            submission_status = _submission_status_from_execution(
                status,
                attempt_no=int(execution["attempt_no"] or 0),
                max_attempts=int(submission["max_attempts"] or 1),
            )
            cursor.execute(
                """
                UPDATE crawl_task_submissions
                SET status = %s,
                    latest_execution_id = %s,
                    last_error = %s,
                    result_summary_json = %s
                WHERE id = %s
                """,
                (
                    submission_status,
                    execution_id,
                    error or writeback_error,
                    _json_dumps(summary),
                    submission_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_task_execution_writeback(
    execution_id: int,
    *,
    writeback_status: str,
    writeback_locator: dict[str, Any] | None = None,
    writeback_error: str | None = None,
) -> None:
    """Update writeback outcome for a finished execution and refresh its submission summary."""

    from apps.finance_crawler.storage.db import get_conn

    now = datetime.now()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT e.submission_id, e.attempt_no, e.status, e.account_name, e.error,
                       e.metrics_json, e.result_json, e.screenshot_path, s.max_attempts
                FROM crawl_task_executions e
                JOIN crawl_task_submissions s ON s.id = e.submission_id
                WHERE e.id = %s
                FOR UPDATE
                """,
                (execution_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"task execution not found: {execution_id}")

            cursor.execute(
                """
                UPDATE crawl_task_executions
                SET writeback_status = %s,
                    writeback_locator_json = %s,
                    writeback_error = %s,
                    heartbeat_at = %s
                WHERE id = %s
                """,
                (
                    writeback_status,
                    _json_dumps(writeback_locator),
                    writeback_error,
                    now,
                    execution_id,
                ),
            )

            metrics = _json_loads(row.get("metrics_json")) or {}
            result = _json_loads(row.get("result_json")) or {}
            summary = _execution_summary(
                status=row["status"],
                account_name=row.get("account_name"),
                metrics=metrics,
                result=result,
                screenshot_path=row.get("screenshot_path"),
                writeback_status=writeback_status,
            )
            submission_status = _submission_status_from_execution(
                "error" if writeback_status in {"error", "skipped"} else row["status"],
                attempt_no=int(row["attempt_no"] or 0),
                max_attempts=int(row["max_attempts"] or 1),
            )
            cursor.execute(
                """
                UPDATE crawl_task_submissions
                SET status = %s,
                    last_error = %s,
                    result_summary_json = %s
                WHERE id = %s
                """,
                (
                    submission_status,
                    (
                        writeback_error or row.get("error")
                        if writeback_status in {"error", "skipped"}
                        else row.get("error")
                    ),
                    _json_dumps(summary),
                    row["submission_id"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def request_task_rerun(submission_id: int, *, scheduled_at: datetime | None = None) -> None:
    """Move a submission back to pending without mutating old executions."""

    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE crawl_task_submissions
                SET status = 'pending',
                    scheduled_at = %s,
                    last_error = NULL
                WHERE id = %s
                  AND status <> 'running'
                """,
                (scheduled_at, submission_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _submission_status_from_execution(status: str, *, attempt_no: int, max_attempts: int) -> str:
    if status in {"success"}:
        return "success"
    if status in {"not_found", "deleted"}:
        return "not_found"
    if status in {"cancelled"}:
        return "cancelled"
    if status in {"running", "queued"}:
        return status
    if attempt_no >= max(max_attempts, 1):
        return "failed_final"
    return "failed_retryable"


def _execution_summary(
    *,
    status: str,
    account_name: str | None,
    metrics: dict[str, Any] | None,
    result: dict[str, Any] | None,
    screenshot_path: str | None,
    writeback_status: str | None,
) -> dict[str, Any]:
    summary = {
        "status": status,
        "account_name": account_name,
        "screenshot_path": screenshot_path,
        "writeback_status": writeback_status,
    }
    if metrics:
        for key in ("read_count", "comment_count", "duration", "capture_pages", "ocr_attempted"):
            if key in metrics:
                summary[key] = metrics[key]
    if result:
        for key in ("read_count", "comment_count"):
            if key in result and key not in summary:
                summary[key] = result[key]
    return summary


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def get_task_id_by_legacy_post_id(legacy_post_id: int) -> int | None:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM crawl_tasks
                WHERE legacy_post_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (legacy_post_id,),
            )
            row = cursor.fetchone()
            return int(row["id"]) if row else None
    finally:
        conn.close()


def insert_crawl_result(
    result: CrawlResult,
    *,
    task_id: int | None = None,
    legacy_post_id: int | None = None,
) -> int:
    from apps.finance_crawler.storage.db import get_conn

    workflow = result.metrics.get("workflow") if result.metrics else None
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_results
                    (task_id, legacy_post_id, app_type, url, workflow, status, account_name,
                     content, metrics_json, screenshot_path, error, crawled_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_id or result.task_id,
                    legacy_post_id,
                    result.app_type,
                    result.url,
                    workflow,
                    result.status,
                    result.account_name,
                    result.content,
                    _json_dumps(result.metrics),
                    result.screenshot_path,
                    result.error,
                    result.crawled_at,
                ),
            )
            result_id = int(cursor.lastrowid)
        conn.commit()
        return result_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_check_tasks(limit: int) -> list[dict[str, Any]]:
    """Return task-shaped rows that can replace the legacy posts check query."""
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    t.legacy_post_id AS id,
                    t.id AS task_id,
                    t.original_url AS url,
                    t.app_type AS source_app,
                    t.source_time AS post_time,
                    p.doc_row_index,
                    COALESCE(p.check_retries, t.attempts, 0) AS check_retries
                FROM crawl_tasks t
                LEFT JOIN posts p ON p.id = t.legacy_post_id
                WHERE t.status = 'pending'
                  AND t.legacy_post_id IS NOT NULL
                  AND COALESCE(p.check_status, 'pending') = 'pending'
                  AND COALESCE(p.check_retries, t.attempts, 0) < %s
                  AND t.source_time <= %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM crawl_results r
                      WHERE r.task_id = t.id
                        AND r.workflow = 'initial_check'
                        AND r.status IN ('success', 'not_found')
                  )
                ORDER BY t.source_time ASC
                LIMIT %s
                """,
                (_config().CHECK_MAX_RETRIES, _check_cutoff(), limit),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def get_pending_batch_tasks(limit: int | None = None) -> list[dict[str, Any]]:
    """Return task-shaped rows that can replace the legacy posts batch query."""
    from apps.finance_crawler.storage.db import get_conn

    sql = """
        SELECT
            t.legacy_post_id AS id,
            t.id AS task_id,
            t.original_url AS url,
            t.app_type AS source_app,
            t.source_time AS post_time,
            p.doc_row_index,
            p.doc_file_id,
            p.doc_sheet_id
        FROM crawl_tasks t
        LEFT JOIN posts p ON p.id = t.legacy_post_id
        WHERE t.status = 'pending'
          AND t.legacy_post_id IS NOT NULL
          AND t.source_time <= %s
          AND COALESCE(p.batch_status, 'pending') = 'pending'
          AND COALESCE(p.batch_retries, t.attempts, 0) < %s
    """
    params: list[Any] = [_batch_cutoff(), _config().BATCH_MAX_RETRIES]
    if _config().BATCH_REQUIRES_CHECK_SUCCESS:
        sql += """
          AND EXISTS (
              SELECT 1
              FROM crawl_results r
              WHERE r.task_id = t.id
                AND r.workflow = 'initial_check'
                AND r.status = 'success'
          )
        """
    sql += """
          AND NOT EXISTS (
              SELECT 1
              FROM crawl_results r
              WHERE r.task_id = t.id
                AND r.workflow = 'batch_crawl'
                AND r.status IN ('success', 'deleted')
          )
        ORDER BY t.source_time ASC
    """
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def get_pending_batch_submissions(limit: int | None = None) -> list[dict[str, Any]]:
    """Return batch records from task submissions, with legacy posts as bootstrap fallback."""
    from apps.finance_crawler.storage.db import get_conn

    check_clause = "AND p.check_status = 'success'" if _config().BATCH_REQUIRES_CHECK_SUCCESS else ""
    submission_sql = f"""
        SELECT
            p.id AS id,
            s.id AS submission_id,
            p.url AS url,
            COALESCE(s.app_type, p.source_app) AS source_app,
            COALESCE(s.source_time, p.post_time) AS post_time,
            p.doc_row_index,
            p.doc_file_id,
            p.doc_sheet_id
        FROM crawl_task_submissions s
        JOIN posts p ON p.url = s.original_url
        WHERE s.task_type = 'batch_crawl'
          AND s.status IN ('pending', 'failed_retryable')
          AND (s.scheduled_at IS NULL OR s.scheduled_at <= %s)
          AND COALESCE(s.source_time, p.post_time) <= %s
          AND s.attempts < s.max_attempts
          {check_clause}
    """
    legacy_sql = f"""
        SELECT
            p.id AS id,
            NULL AS submission_id,
            p.url AS url,
            p.source_app AS source_app,
            p.post_time AS post_time,
            p.doc_row_index,
            p.doc_file_id,
            p.doc_sheet_id
        FROM posts p
        WHERE p.post_time <= %s
          AND p.batch_status = 'pending'
          AND p.batch_retries < %s
          {check_clause}
          AND NOT EXISTS (
              SELECT 1
              FROM crawl_task_submissions s
              WHERE s.task_type = 'batch_crawl'
                AND s.original_url = p.url
          )
    """
    sql = f"""
        SELECT *
        FROM (
            {submission_sql}
            UNION ALL
            {legacy_sql}
        ) pending_batch
        ORDER BY post_time ASC
    """
    params: list[Any] = [
        datetime.now(),
        _batch_cutoff(),
        _batch_cutoff(),
        _config().BATCH_MAX_RETRIES,
    ]
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def _config():
    from apps.finance_crawler.config import Config

    return Config


def _check_cutoff() -> datetime:
    from datetime import timedelta

    return datetime.now() - timedelta(hours=_config().POST_ELIGIBLE_HOURS)


def _batch_cutoff() -> datetime:
    from datetime import timedelta

    if _config().BATCH_NEXT_DAY_ONLY:
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return datetime.now() - timedelta(hours=_config().POST_ELIGIBLE_HOURS)


def record_writeback(writeback: WritebackResult, *, legacy_post_id: int | None = None) -> int:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_writebacks
                    (task_id, result_id, legacy_post_id, sink_type, sink_locator_json,
                     status, error, written_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    writeback.task_id,
                    writeback.result_id,
                    legacy_post_id,
                    writeback.sink_type,
                    _json_dumps(writeback.locator),
                    writeback.status,
                    writeback.error,
                    writeback.written_at or datetime.now(),
                ),
            )
            writeback_id = int(cursor.lastrowid)
        conn.commit()
        return writeback_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
