"""Generic framework tables for sources, tasks, crawl results, and writebacks."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from apps.alipay_crawler.domain.records import CrawlResult, SourceRecord, WritebackResult
from apps.alipay_crawler.utils.link_source import detect_link_source


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _record_key_for_url(url: str) -> str:
    return f"url:{hashlib.sha1(url.encode('utf-8')).hexdigest()}"


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

    seed_crawler_apps(cursor)


def seed_crawler_apps(cursor) -> None:
    apps = [
        ("alipay", "Alipay", "com.eg.android.AlipayGphone"),
        ("antfortune", "Ant Fortune", "com.antfortune.wealth"),
        ("tenpay", "Tenpay / Tencent Wealth", "com.tencent.fortuneplat"),
        ("unknown", "Unknown App", None),
    ]
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
    app_type = record.app_type or detect_link_source(record.url)
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
        app_type=source_app or detect_link_source(url),
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


def create_crawl_job(
    job_type: str,
    *,
    source_id: int | None = None,
    status: str = "running",
    summary: dict[str, Any] | None = None,
) -> int:
    from apps.alipay_crawler.storage.db import get_conn

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
    from apps.alipay_crawler.storage.db import get_conn

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


def get_task_id_by_legacy_post_id(legacy_post_id: int) -> int | None:
    from apps.alipay_crawler.storage.db import get_conn

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
    from apps.alipay_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_results
                    (task_id, legacy_post_id, app_type, url, status, account_name,
                     content, metrics_json, screenshot_path, error, crawled_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_id or result.task_id,
                    legacy_post_id,
                    result.app_type,
                    result.url,
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


def record_writeback(writeback: WritebackResult, *, legacy_post_id: int | None = None) -> int:
    from apps.alipay_crawler.storage.db import get_conn

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
