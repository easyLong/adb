"""Generic framework tables for sources, tasks, crawl results, and writebacks."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from datetime import time as datetime_time
from datetime import timedelta
from typing import Any

from apps.finance_crawler.crawlers.registry import iter_app_profiles
from apps.finance_crawler.domain.records import CrawlResult, WritebackResult
from apps.finance_crawler.domain.task_types import (
    DETAIL_CRAWL_TASK_TYPE,
    INITIAL_CHECK_TASK_TYPE,
)
from apps.finance_crawler.utils.link_source import resolve_source_app


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _normalize_url_for_record_key(url: str) -> str:
    """Normalize only safe whitespace for stable task identity.

    Do not sort/drop query parameters here: finance app deep links often carry
    business identifiers in the query string.
    """

    return url.strip()


def _record_key_for_url(url: str) -> str:
    normalized = _normalize_url_for_record_key(url)
    return f"url:{hashlib.sha1(normalized.encode('utf-8')).hexdigest()}"


def _record_key_for_source_row(
    *,
    source_type: str,
    source_name: str | None,
    source_locator: dict[str, Any] | None,
    url: str,
) -> str:
    locator = source_locator or {}
    if source_type == "tencent_docs":
        return _hash_key(
            "tencent_docs_sheet_url",
            locator.get("file_id"),
            locator.get("sheet_id"),
            _normalize_url_for_record_key(url),
        )
    return _record_key_for_url(url)


def _parse_detail_time(value: str) -> datetime_time:
    parts = [int(part) for part in (value or "10:00").split(":")]
    if len(parts) == 2:
        return datetime_time(parts[0], parts[1])
    if len(parts) == 3:
        return datetime_time(parts[0], parts[1], parts[2])
    raise ValueError(f"invalid DETAIL_TIME: {value}")


def _initial_check_scheduled_at(source_time: datetime) -> datetime:
    return source_time + timedelta(hours=_config().INITIAL_CHECK_DELAY_HOURS)


def _detail_scheduled_at(source_time: datetime) -> datetime:
    detail_time = _parse_detail_time(_config().DETAIL_TIME)
    return datetime.combine(source_time.date() + timedelta(days=1), detail_time)


def _should_skip_initial_check(source_time: datetime, now: datetime | None = None) -> bool:
    return (now or datetime.now()) >= _detail_scheduled_at(source_time)


def _detail_source_dates_filter() -> list[str]:
    raw = str(getattr(_config(), "DETAIL_SOURCE_DATES", "") or "").strip()
    if not raw:
        return []
    dates: list[str] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        datetime.strptime(value, "%Y-%m-%d")
        dates.append(value)
    return dates


def _hash_key(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"


def _current_schema(cursor) -> str:
    cursor.execute("SELECT DATABASE() AS db_name")
    row = cursor.fetchone() or {}
    return row["db_name"]


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (_current_schema(cursor), table, column),
    )
    return cursor.fetchone() is not None


def _table_exists(cursor, table: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name = %s
        LIMIT 1
        """,
        (_current_schema(cursor), table),
    )
    return cursor.fetchone() is not None


def _ensure_column(cursor, table: str, column: str, ddl: str) -> None:
    if not _column_exists(cursor, table, column):
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN {ddl}")


def _index_exists(cursor, table: str, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = %s
          AND table_name = %s
          AND index_name = %s
        LIMIT 1
        """,
        (_current_schema(cursor), table, index_name),
    )
    return cursor.fetchone() is not None


def _ensure_index(cursor, table: str, index_name: str, ddl: str) -> None:
    if not _index_exists(cursor, table, index_name):
        cursor.execute(f"ALTER TABLE `{table}` ADD INDEX {index_name} {ddl}")


def _ensure_unique_index(cursor, table: str, index_name: str, ddl: str) -> None:
    if not _index_exists(cursor, table, index_name):
        cursor.execute(f"ALTER TABLE `{table}` ADD UNIQUE KEY {index_name} {ddl}")


def _ensure_submission_object_key(cursor) -> None:
    _ensure_column(
        cursor,
        "crawl_task_submissions",
        "crawl_object_key",
        "crawl_object_key VARCHAR(191) NULL",
    )
    if _column_exists(cursor, "crawl_task_submissions", "source_record_key"):
        cursor.execute(
            """
            UPDATE crawl_task_submissions
            SET crawl_object_key = source_record_key
            WHERE crawl_object_key IS NULL
              AND source_record_key IS NOT NULL
            """
        )
        cursor.execute(
            """
            ALTER TABLE crawl_task_submissions
            MODIFY source_record_key VARCHAR(191) NULL
            """
        )
    cursor.execute(
        """
        UPDATE crawl_task_submissions
        SET crawl_object_key = CONCAT('legacy:', id)
        WHERE crawl_object_key IS NULL
           OR crawl_object_key = ''
        """
    )


def _ensure_data_source_links_table(cursor) -> None:
    if _table_exists(cursor, "runtime_config") and not _table_exists(cursor, "data_source_links"):
        cursor.execute("RENAME TABLE runtime_config TO data_source_links")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS data_source_links (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            source_key VARCHAR(128) NOT NULL,
            data_source_link TEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            description VARCHAR(255) NULL,
            updated_by VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_data_source_key (source_key),
            INDEX idx_data_source_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    if _column_exists(cursor, "data_source_links", "config_key"):
        cursor.execute(
            """
            ALTER TABLE data_source_links
            CHANGE COLUMN config_key source_key VARCHAR(128) NOT NULL
            """
        )

    if _column_exists(cursor, "data_source_links", "enabled") and not _column_exists(
        cursor, "data_source_links", "status"
    ):
        cursor.execute(
            """
            ALTER TABLE data_source_links
            CHANGE COLUMN enabled status VARCHAR(32) NOT NULL DEFAULT 'active'
            """
        )
        cursor.execute(
            """
            UPDATE data_source_links
            SET status = CASE
                WHEN status IN ('1', 'active') THEN 'active'
                ELSE 'unavailable'
            END
            """
        )
    elif not _column_exists(cursor, "data_source_links", "status"):
        cursor.execute(
            """
            ALTER TABLE data_source_links
            ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'active' AFTER data_source_link
            """
        )

    has_source_link = _column_exists(cursor, "data_source_links", "data_source_link")
    has_config_value = _column_exists(cursor, "data_source_links", "config_value")
    if has_config_value and not has_source_link:
        cursor.execute(
            """
            ALTER TABLE data_source_links
            CHANGE COLUMN config_value data_source_link TEXT NULL
            """
        )
        return
    if not has_source_link:
        cursor.execute(
            """
            ALTER TABLE data_source_links
            ADD COLUMN data_source_link TEXT NULL AFTER source_key
            """
        )
        return
    if has_config_value:
        cursor.execute(
            """
            UPDATE data_source_links
            SET data_source_link = config_value
            WHERE (data_source_link IS NULL OR data_source_link = '')
              AND config_value IS NOT NULL
            """
        )
        cursor.execute("ALTER TABLE data_source_links DROP COLUMN config_value")


def _ensure_app_config_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_config (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            config_key VARCHAR(128) NOT NULL,
            config_value TEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            is_secret TINYINT NOT NULL DEFAULT 0,
            description VARCHAR(255) NULL,
            updated_by VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_app_config_key (config_key),
            INDEX idx_app_config_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def ensure_framework_tables(cursor) -> None:
    """Create and migrate framework-level tables used by the current workflow."""

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

    _ensure_data_source_links_table(cursor)
    _ensure_app_config_table(cursor)
    from apps.finance_crawler.services.runtime_config import ensure_runtime_config_defaults

    ensure_runtime_config_defaults(cursor)

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
        CREATE TABLE IF NOT EXISTS crawl_results (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            task_id BIGINT UNSIGNED NULL,
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
            sink_type VARCHAR(64) NOT NULL,
            sink_locator_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL,
            error TEXT NULL,
            written_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_writeback_task (task_id),
            INDEX idx_writeback_result (result_id),
            INDEX idx_writeback_sink (sink_type),
            INDEX idx_writeback_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_task_submissions (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            task_type VARCHAR(64) NOT NULL DEFAULT 'detail_crawl',
            source_id BIGINT UNSIGNED NULL,
            source_type VARCHAR(64) NOT NULL,
            source_name VARCHAR(191) NULL,
            crawl_object_key VARCHAR(191) NOT NULL,
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
            UNIQUE KEY uk_submission_record (source_type, crawl_object_key, task_type),
            INDEX idx_submission_status (status),
            INDEX idx_submission_app (app_type),
            INDEX idx_submission_source (source_id),
            INDEX idx_submission_object_task (task_type, crawl_object_key, status),
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

    _ensure_submission_object_key(cursor)
    _ensure_unique_index(
        cursor,
        "crawl_task_submissions",
        "uk_submission_object_record",
        "(source_type, crawl_object_key, task_type)",
    )
    _ensure_index(
        cursor,
        "crawl_task_submissions",
        "idx_submission_object_task",
        "(task_type, crawl_object_key, status)",
    )
    _ensure_column(cursor, "crawl_results", "workflow", "workflow VARCHAR(64) NULL")
    _ensure_index(cursor, "crawl_results", "idx_result_workflow", "(workflow)")
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


def upsert_task_submission_tx(
    cursor,
    *,
    task_type: str,
    source_type: str,
    crawl_object_key: str,
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
        crawl_object_key=crawl_object_key,
        original_url=original_url,
    )
    cursor.execute(
        """
        INSERT INTO crawl_task_submissions
            (task_type, source_id, source_type, source_name, crawl_object_key,
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
            crawl_object_key,
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
    crawl_object_key: str,
    original_url: str,
) -> None:
    cursor.execute(
        """
        SELECT id, crawl_object_key
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
    if not row or row["crawl_object_key"] == crawl_object_key:
        return
    try:
        cursor.execute(
            """
            UPDATE crawl_task_submissions
            SET crawl_object_key = %s
            WHERE id = %s
            """,
            (crawl_object_key, row["id"]),
        )
    except Exception:
        # If another row already owns the stable object key, the upsert below will
        # merge into it via uk_submission_record.
        pass


def upsert_source_record_submissions_tx(
    cursor,
    *,
    source_type: str,
    source_name: str | None,
    source_config: dict[str, Any] | None,
    source_locator: dict[str, Any] | None,
    url: str,
    source_time: datetime | None = None,
    source_app: str | None = None,
    now: datetime | None = None,
    skip_initial_check: bool = False,
    created_by: str = "fetch",
) -> dict[str, int]:
    """Create the planned task submissions for one source record.

    One crawl object can produce multiple task types. For post-like finance
    links we schedule a lightweight initial check and a next-day detail crawl.
    If the next-day detail window has already arrived, the initial check is
    skipped because it no longer adds business value.
    """

    if source_time is None:
        raise ValueError("source_time is required")

    resolved_source_name = source_name or source_type
    source_id = upsert_crawl_source_tx(
        cursor,
        source_type,
        resolved_source_name,
        source_config,
    )
    object_key = _record_key_for_source_row(
        source_type=source_type,
        source_name=resolved_source_name,
        source_locator=source_locator,
        url=url,
    )

    submissions: dict[str, int] = {}
    locator = dict(source_locator or {})
    if not skip_initial_check and not _should_skip_initial_check(source_time, now):
        submissions[INITIAL_CHECK_TASK_TYPE] = upsert_task_submission_tx(
            cursor,
            task_type=INITIAL_CHECK_TASK_TYPE,
            source_id=source_id,
            source_type=source_type,
            source_name=resolved_source_name,
            crawl_object_key=object_key,
            source_locator=locator,
            app_type=source_app,
            original_url=url,
            source_time=source_time,
            scheduled_at=_initial_check_scheduled_at(source_time),
            max_attempts=_config().CHECK_MAX_RETRIES,
            created_by=created_by,
        )
    submissions[DETAIL_CRAWL_TASK_TYPE] = upsert_task_submission_tx(
        cursor,
        task_type=DETAIL_CRAWL_TASK_TYPE,
        source_id=source_id,
        source_type=source_type,
        source_name=resolved_source_name,
        crawl_object_key=object_key,
        source_locator=locator,
        app_type=source_app,
        original_url=url,
        source_time=source_time,
        scheduled_at=_detail_scheduled_at(source_time),
        max_attempts=_config().DETAIL_MAX_RETRIES,
        created_by=created_by,
    )
    return submissions


def upsert_source_record_submissions(
    *,
    source_type: str,
    source_name: str | None,
    source_config: dict[str, Any] | None = None,
    source_locator: dict[str, Any] | None = None,
    url: str,
    source_time: datetime | None = None,
    source_app: str | None = None,
    now: datetime | None = None,
    skip_initial_check: bool = False,
    created_by: str = "fetch",
) -> dict[str, int]:
    """Create or refresh planned task submissions for one source record."""

    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            submissions = upsert_source_record_submissions_tx(
                cursor,
                source_type=source_type,
                source_name=source_name,
                source_config=source_config,
                source_locator=source_locator,
                url=url,
                source_time=source_time,
                source_app=source_app,
                now=now,
                skip_initial_check=skip_initial_check,
                created_by=created_by,
            )
        conn.commit()
        return submissions
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
    task_type: str = DETAIL_CRAWL_TASK_TYPE,
    max_attempts: int = 3,
) -> int:
    """Create or refresh a submission for one local Excel row."""

    from apps.finance_crawler.storage.db import get_conn

    source_type = "excel"
    source_name = _hash_key("excel_source", path, sheet_name)
    object_key = _hash_key(
        "excel_row",
        path,
        sheet_name,
        row_index,
        _normalize_url_for_record_key(url),
    )
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
                crawl_object_key=object_key,
                source_locator={
                    "path": path,
                    "sheet_name": sheet_name,
                    "row_index": row_index,
                    "output_path": output_path,
                },
                app_type=source_app,
                original_url=url,
                max_attempts=max_attempts,
                created_by="excel_detail",
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
    crawl_object_key: str,
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
                crawl_object_key=crawl_object_key,
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
                "error" if writeback_status == "error" else row["status"],
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
                        if writeback_status == "error"
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
    """Move a submission back to pending without mutating old executions.

    Manual reruns append a new execution attempt. Keep the submission attempt
    counter aligned with historical executions so the next attempt number does
    not collide with the `(submission_id, attempt_no)` unique key.
    """

    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE crawl_task_submissions s
                LEFT JOIN (
                    SELECT submission_id, COALESCE(MAX(attempt_no), 0) AS latest_attempt
                    FROM crawl_task_executions
                    WHERE submission_id = %s
                    GROUP BY submission_id
                ) e ON e.submission_id = s.id
                SET s.status = 'pending',
                    s.scheduled_at = %s,
                    s.attempts = GREATEST(s.attempts, COALESCE(e.latest_attempt, 0)),
                    s.max_attempts = GREATEST(
                        s.max_attempts,
                        s.attempts + 1,
                        COALESCE(e.latest_attempt, 0) + 1
                    ),
                    s.last_error = NULL
                WHERE s.id = %s
                  AND s.status <> 'running'
                """,
                (submission_id, scheduled_at, submission_id),
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


def _submission_task_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for row in rows:
        locator = _json_loads(row.get("source_locator_json")) or {}
        # Keep a stable workflow-facing numeric id derived from the submission.
        record_id = -int(row["submission_id"])
        tasks.append(
            {
                "record_id": record_id,
                "task_id": row["submission_id"],
                "submission_id": row["submission_id"],
                "url": row["url"],
                "source_app": row.get("source_app"),
                "source_time": row.get("source_time"),
                "doc_row_index": locator.get("row_index"),
                "doc_file_id": locator.get("file_id"),
                "doc_sheet_id": locator.get("sheet_id"),
                "source_locator": locator,
                "attempts": row.get("attempts"),
            }
        )
    return tasks


def insert_crawl_result(
    result: CrawlResult,
    *,
    task_id: int | None = None,
) -> int:
    from apps.finance_crawler.storage.db import get_conn

    workflow = result.metrics.get("workflow") if result.metrics else None
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_results
                    (task_id, app_type, url, workflow, status, account_name,
                     content, metrics_json, screenshot_path, error, crawled_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_id or result.task_id,
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


def get_pending_check_submissions() -> list[dict[str, Any]]:
    """Return due initial-check records from task submissions."""
    from apps.finance_crawler.storage.db import get_conn

    finalize_exhausted_submissions(task_type=INITIAL_CHECK_TASK_TYPE)

    sql = """
        SELECT
            s.id AS submission_id,
            s.original_url AS url,
            s.app_type AS source_app,
            s.source_time AS source_time,
            s.source_locator_json,
            s.attempts
        FROM crawl_task_submissions s
        WHERE s.task_type = %s
          AND s.status IN ('pending', 'failed_retryable')
          AND (s.scheduled_at IS NULL OR s.scheduled_at <= %s)
          AND s.attempts < s.max_attempts
        ORDER BY s.source_time ASC
    """
    now = datetime.now()
    params: list[Any] = [
        INITIAL_CHECK_TASK_TYPE,
        now,
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return _submission_task_rows(cursor.fetchall())
    finally:
        conn.close()


def get_pending_detail_submissions(limit: int | None = None) -> list[dict[str, Any]]:
    """Return due detail-crawl records from task submissions."""
    from apps.finance_crawler.storage.db import get_conn

    finalize_exhausted_submissions(task_type=DETAIL_CRAWL_TASK_TYPE)

    check_clause = ""
    check_params: list[Any] = []
    if _config().DETAIL_REQUIRES_CHECK_SUCCESS:
        check_clause = """
          AND (
              EXISTS (
                  SELECT 1
                  FROM crawl_task_submissions c
                  WHERE c.task_type = %s
                    AND c.crawl_object_key = s.crawl_object_key
                    AND c.status = 'success'
              )
              OR NOT EXISTS (
                  SELECT 1
                  FROM crawl_task_submissions c
                  WHERE c.task_type = %s
                    AND c.crawl_object_key = s.crawl_object_key
              )
          )
        """
        check_params = [INITIAL_CHECK_TASK_TYPE, INITIAL_CHECK_TASK_TYPE]
    date_filter = _detail_source_dates_filter()
    date_clause = ""
    date_params: list[Any] = []
    if date_filter:
        placeholders = ", ".join(["%s"] * len(date_filter))
        date_clause = f"AND DATE(s.source_time) IN ({placeholders})"
        date_params = date_filter
    sql = f"""
        SELECT
            s.id AS submission_id,
            s.original_url AS url,
            s.app_type AS source_app,
            s.source_time AS source_time,
            s.source_locator_json,
            s.attempts
        FROM crawl_task_submissions s
        WHERE s.task_type = %s
          AND s.status IN ('pending', 'failed_retryable')
          AND (s.scheduled_at IS NULL OR s.scheduled_at <= %s)
          AND s.attempts < s.max_attempts
          {date_clause}
          {check_clause}
        ORDER BY s.source_time ASC
    """
    params: list[Any] = [
        DETAIL_CRAWL_TASK_TYPE,
        datetime.now(),
        *date_params,
        *check_params,
    ]
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return _submission_task_rows(cursor.fetchall())
    finally:
        conn.close()


def finalize_exhausted_submissions(*, task_type: str | None = None) -> int:
    """Mark retry-exhausted runnable submissions as final failures.

    Older rows can be left as ``pending`` while ``attempts >= max_attempts``.
    The queue will not pick those rows, so make the terminal state explicit
    before reporting or fetching pending work.
    """

    from apps.finance_crawler.storage.db import get_conn

    where = """
        status IN ('pending', 'failed_retryable')
        AND attempts >= max_attempts
        AND max_attempts > 0
    """
    params: list[Any] = []
    if task_type:
        where += " AND task_type = %s"
        params.append(task_type)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE crawl_task_submissions
                SET status = 'failed_final'
                WHERE {where}
                """,
                params,
            )
            affected = int(cursor.rowcount or 0)
        conn.commit()
        return affected
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _config():
    from apps.finance_crawler.config import Config

    return Config


def record_writeback(writeback: WritebackResult) -> int:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_writebacks
                    (task_id, result_id, sink_type, sink_locator_json,
                     status, error, written_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    writeback.task_id,
                    writeback.result_id,
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
