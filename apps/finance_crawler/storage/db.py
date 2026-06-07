"""MySQL connection, schema initialization, and task logging."""

from __future__ import annotations

import pymysql
import pymysql.cursors

from apps.finance_crawler.config import Config
from apps.finance_crawler.storage.framework_db import ensure_framework_tables
from apps.finance_crawler.utils.logger import get_logger

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


def init_db() -> None:
    """Create framework tables used by the current crawler workflow."""

    _ensure_database()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
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
