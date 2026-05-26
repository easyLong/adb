"""MySQL access layer for posts and task logs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pymysql
import pymysql.cursors

from apps.alipay_crawler.config import Config
from apps.alipay_crawler.storage.framework_db import (
    ensure_framework_tables,
    upsert_legacy_post_task_tx,
)
from apps.alipay_crawler.utils.link_source import detect_link_source
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("db")


def _server_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=Config.DB_CONNECT_TIMEOUT,
        autocommit=False,
    )


def get_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        db=Config.DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=Config.DB_CONNECT_TIMEOUT,
        autocommit=False,
    )


def _ensure_database() -> None:
    conn = _server_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{Config.DB_NAME}` "
                "DEFAULT CHARACTER SET utf8mb4 "
                "DEFAULT COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


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
        (Config.DB_NAME, table, column),
    )
    return cursor.fetchone() is not None


def _ensure_column(cursor, table: str, column: str, ddl: str) -> None:
    if not _column_exists(cursor, table, column):
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN {ddl}")


def init_db() -> None:
    _ensure_database()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    url VARCHAR(700) NOT NULL UNIQUE,
                    source_app VARCHAR(32) NOT NULL DEFAULT 'unknown',
                    post_time DATETIME NOT NULL,
                    doc_row_index INT NULL COMMENT 'Tencent sheet row number, 1-based',
                    doc_file_id VARCHAR(128) NULL,
                    doc_sheet_id VARCHAR(128) NULL,
                    fetched_at DATETIME NOT NULL,
                    last_seen_at DATETIME NOT NULL,

                    check_status VARCHAR(20) DEFAULT 'pending',
                    check_time DATETIME NULL,
                    check_error TEXT NULL,
                    check_retries INT DEFAULT 0,
                    account_name VARCHAR(255) NULL,

                    content MEDIUMTEXT NULL,
                    read_count INT DEFAULT 0,
                    comment_count INT DEFAULT 0,
                    screenshot_path VARCHAR(700) NULL,
                    batch_status VARCHAR(20) DEFAULT 'pending',
                    batch_time DATETIME NULL,
                    batch_error TEXT NULL,
                    batch_retries INT DEFAULT 0,

                    written_back TINYINT DEFAULT 0,
                    written_back_at DATETIME NULL,

                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,

                    INDEX idx_post_time (post_time),
                    INDEX idx_check_status (check_status),
                    INDEX idx_batch_status (batch_status),
                    INDEX idx_written_back (written_back),
                    INDEX idx_doc_row (doc_file_id, doc_sheet_id, doc_row_index)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS task_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    task_name VARCHAR(80) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    message TEXT NULL,
                    duration FLOAT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_task_name (task_name),
                    INDEX idx_created_at (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )

            ensure_framework_tables(cursor)

            # Lightweight migrations for older local databases.
            _ensure_column(cursor, "posts", "doc_row_index", "doc_row_index INT NULL")
            _ensure_column(cursor, "posts", "doc_file_id", "doc_file_id VARCHAR(128) NULL")
            _ensure_column(cursor, "posts", "doc_sheet_id", "doc_sheet_id VARCHAR(128) NULL")
            _ensure_column(cursor, "posts", "last_seen_at", "last_seen_at DATETIME NULL")
            _ensure_column(cursor, "posts", "account_name", "account_name VARCHAR(255) NULL")
            _ensure_column(
                cursor,
                "posts",
                "source_app",
                "source_app VARCHAR(32) NOT NULL DEFAULT 'unknown'",
            )
            cursor.execute(
                """
                UPDATE posts
                SET source_app = CASE
                    WHEN url LIKE 'afwealth://%%'
                      OR url LIKE 'https://think.klv5qu.com/%%'
                      OR url LIKE 'http://think.klv5qu.com/%%'
                    THEN 'antfortune'
                    WHEN url LIKE 'tenpay://%%'
                      OR url LIKE 'tencentwm://%%'
                      OR url LIKE 'https://%%tencentwm.com/%%'
                      OR url LIKE 'http://%%tencentwm.com/%%'
                    THEN 'tenpay'
                    WHEN url LIKE 'alipay://%%'
                      OR url LIKE 'alipays://%%'
                      OR url LIKE '%%alipay%%'
                    THEN 'alipay'
                    ELSE 'unknown'
                END
                WHERE source_app IS NULL
                   OR source_app = ''
                   OR source_app = 'unknown'
                """
            )

        conn.commit()
        logger.info("数据库初始化完成")
    except Exception:
        conn.rollback()
        logger.exception("数据库初始化失败")
        raise
    finally:
        conn.close()


def log_task(task_name: str, status: str, message: str = "", duration: float = 0) -> None:
    try:
        conn = get_conn()
    except Exception as exc:
        logger.warning("无法写入 task_log: %s", exc)
        return

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO task_log (task_name, status, message, duration)
                VALUES (%s, %s, %s, %s)
                """,
                (task_name, status, message, round(duration, 2)),
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.warning("写入 task_log 失败: %s", exc)
    finally:
        conn.close()


def upsert_post(
    url: str,
    post_time: datetime,
    row_index: int | None = None,
    file_id: str | None = None,
    sheet_id: str | None = None,
    source_app: str | None = None,
) -> bool:
    """Insert or refresh a post. Returns True if a new row was inserted."""
    now = datetime.now()
    source = source_app or detect_link_source(url)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            affected = cursor.execute(
                """
                INSERT INTO posts
                    (url, source_app, post_time, doc_row_index, doc_file_id, doc_sheet_id,
                     fetched_at, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    id = LAST_INSERT_ID(id),
                    source_app = VALUES(source_app),
                    post_time = VALUES(post_time),
                    doc_row_index = VALUES(doc_row_index),
                    doc_file_id = VALUES(doc_file_id),
                    doc_sheet_id = VALUES(doc_sheet_id),
                    last_seen_at = VALUES(last_seen_at)
                """,
                (url, source, post_time, row_index, file_id, sheet_id, now, now),
            )
            post_id = int(cursor.lastrowid or 0)
            if post_id:
                upsert_legacy_post_task_tx(
                    cursor,
                    post_id=post_id,
                    url=url,
                    post_time=post_time,
                    row_index=row_index,
                    file_id=file_id,
                    sheet_id=sheet_id,
                    source_app=source,
                )
        conn.commit()
        return affected == 1
    except Exception as exc:
        conn.rollback()
        logger.warning("帖子入库失败 url=%s error=%s", url, exc)
        return False
    finally:
        conn.close()


def insert_post(url: str, post_time: datetime) -> bool:
    """Backward-compatible wrapper used by older code."""
    return upsert_post(url, post_time)


def get_eligible_posts(limit: int | None = None) -> list[dict[str, Any]]:
    cutoff = datetime.now() - timedelta(hours=Config.POST_ELIGIBLE_HOURS)
    sql = """
        SELECT id, url, source_app, post_time, doc_row_index, check_status, batch_status
        FROM posts
        WHERE post_time <= %s
        ORDER BY post_time ASC
    """
    params: list[Any] = [cutoff]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def get_pending_check_posts() -> list[dict[str, Any]]:
    cutoff = datetime.now() - timedelta(hours=Config.POST_ELIGIBLE_HOURS)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, url, source_app, post_time, doc_row_index, check_retries
                FROM posts
                WHERE check_status = 'pending'
                  AND check_retries < %s
                  AND post_time <= %s
                ORDER BY post_time ASC
                LIMIT %s
                """,
                (Config.CHECK_MAX_RETRIES, cutoff, Config.FETCH_LIMIT or 1000),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def update_check_result(
    post_id: int,
    status: str,
    error: str | None = None,
    account_name: str | None = None,
) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE posts
                SET check_status = %s,
                    check_time = %s,
                    check_error = %s,
                    account_name = %s,
                    check_retries = check_retries + 1
                WHERE id = %s
                """,
                (status, datetime.now(), error, account_name, post_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_batch_posts(limit: int | None = None) -> list[dict[str, Any]]:
    if Config.BATCH_NEXT_DAY_ONLY:
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        cutoff = datetime.now() - timedelta(hours=Config.POST_ELIGIBLE_HOURS)
    check_clause = "AND check_status = 'success'" if Config.BATCH_REQUIRES_CHECK_SUCCESS else ""
    sql = f"""
        SELECT id, url, source_app, post_time, doc_row_index
        FROM posts
        WHERE post_time <= %s
          AND batch_status = 'pending'
          AND batch_retries < %s
          {check_clause}
        ORDER BY post_time ASC
    """
    params: list[Any] = [cutoff, Config.BATCH_MAX_RETRIES]
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


def update_batch_result(
    post_id: int,
    status: str,
    content: str | None = None,
    read_count: int = 0,
    comment_count: int = 0,
    screenshot_path: str | None = None,
    error: str | None = None,
) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE posts
                SET content = %s,
                    read_count = %s,
                    comment_count = %s,
                    screenshot_path = %s,
                    batch_status = %s,
                    batch_time = %s,
                    batch_error = %s,
                    batch_retries = batch_retries + 1
                WHERE id = %s
                """,
                (
                    content,
                    int(read_count or 0),
                    int(comment_count or 0),
                    screenshot_path,
                    status,
                    datetime.now(),
                    error,
                    post_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_written_back(post_id: int) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE posts
                SET written_back = 1,
                    written_back_at = %s
                WHERE id = %s
                """,
                (datetime.now(), post_id),
            )
        conn.commit()
    finally:
        conn.close()


def mark_written_back_many(post_ids: list[int]) -> None:
    if not post_ids:
        return

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE posts
                SET written_back = 1,
                    written_back_at = %s
                WHERE id IN %s
                """,
                (datetime.now(), tuple(post_ids)),
            )
        conn.commit()
    finally:
        conn.close()


def get_posts_by_date(target_date) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, url, source_app, post_time, check_status, batch_status,
                       read_count, comment_count, screenshot_path
                FROM posts
                WHERE DATE(post_time) = %s
                ORDER BY read_count DESC
                """,
                (target_date,),
            )
            return cursor.fetchall()
    finally:
        conn.close()
