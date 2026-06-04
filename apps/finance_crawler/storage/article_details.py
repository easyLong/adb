"""Storage helpers for demand-1 article detail collection."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from apps.finance_crawler.storage.db import get_conn


def article_key_for_url(url: str) -> str:
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def upsert_article_source(row: dict[str, Any]) -> tuple[int, int]:
    article_key = row.get("article_key") or article_key_for_url(str(row["article_url"]))
    source_locator = dict(row.get("source_locator") or {})
    source_key = row.get("source_key") or _source_key(source_locator, article_key)
    source_date = row.get("source_date")

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO article_detail_targets
                    (article_key, ip_name, product_name, app_type, article_url, status,
                     source_json, first_seen_date, latest_seen_date)
                VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ip_name = COALESCE(NULLIF(VALUES(ip_name), ''), ip_name),
                    product_name = COALESCE(NULLIF(VALUES(product_name), ''), product_name),
                    app_type = VALUES(app_type),
                    article_url = VALUES(article_url),
                    status = 'active',
                    source_json = VALUES(source_json),
                    first_seen_date = LEAST(COALESCE(first_seen_date, VALUES(first_seen_date)), VALUES(first_seen_date)),
                    latest_seen_date = GREATEST(COALESCE(latest_seen_date, VALUES(latest_seen_date)), VALUES(latest_seen_date))
                """,
                (
                    article_key,
                    row.get("ip_name") or "",
                    row.get("product_name") or "",
                    row.get("app_type") or "unknown",
                    row["article_url"],
                    _json_dumps(row.get("source") or {}),
                    source_date,
                    source_date,
                ),
            )
            cursor.execute("SELECT id FROM article_detail_targets WHERE article_key = %s", (article_key,))
            target_id = int(cursor.fetchone()["id"])
            cursor.execute(
                """
                INSERT INTO article_detail_sources
                    (target_id, source_date, source_type, source_name, source_key,
                     source_locator_json, requested_fields_json, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')
                ON DUPLICATE KEY UPDATE
                    target_id = VALUES(target_id),
                    source_date = VALUES(source_date),
                    source_name = VALUES(source_name),
                    source_locator_json = VALUES(source_locator_json),
                    requested_fields_json = VALUES(requested_fields_json),
                    status = 'active'
                """,
                (
                    target_id,
                    source_date,
                    row.get("source_type") or "tencent_docs",
                    row.get("source_name") or "",
                    source_key,
                    _json_dumps(source_locator),
                    _json_dumps(row.get("requested_fields") or ["article_title", "screenshot", "comment_count", "like_count"]),
                ),
            )
            cursor.execute(
                "SELECT id FROM article_detail_sources WHERE source_type = %s AND source_key = %s",
                (row.get("source_type") or "tencent_docs", source_key),
            )
            source_id = int(cursor.fetchone()["id"])
        conn.commit()
        return target_id, source_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_article_sources(limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT
            s.id AS source_id,
            s.source_date,
            s.source_locator_json,
            t.id AS target_id,
            t.ip_name,
            t.product_name,
            t.app_type,
            t.article_url
        FROM article_detail_sources s
        JOIN article_detail_targets t ON t.id = s.target_id
        LEFT JOIN article_detail_runs r ON r.id = s.latest_run_id
        WHERE s.status = 'active'
          AND t.status = 'active'
          AND s.attempts < s.max_attempts
          AND (r.id IS NULL OR r.status NOT IN ('success', 'blocked'))
        ORDER BY COALESCE(s.source_date, DATE('1970-01-01')) ASC, s.id ASC
    """
    params: list[Any] = []
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return [_decode_row(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def record_article_detail_run(
    *,
    target_id: int,
    source_id: int | None,
    app_type: str,
    article_url: str,
    status: str,
    article_title: str | None,
    comment_count: int | None,
    like_count: int | None,
    metrics: dict[str, Any] | None = None,
    screenshot_path: str | None = None,
    error: str | None = None,
    crawled_at: datetime | None = None,
) -> int:
    crawled_at = crawled_at or datetime.now()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO article_detail_runs
                    (target_id, source_id, app_type, article_url, status, article_title,
                     read_count, comment_count, like_count, metrics_json, screenshot_path, error, crawled_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s)
                """,
                (
                    target_id,
                    source_id,
                    app_type or "unknown",
                    article_url,
                    status,
                    article_title,
                    comment_count,
                    like_count,
                    _json_dumps(metrics or {}),
                    screenshot_path,
                    error,
                    crawled_at,
                ),
            )
            run_id = int(cursor.lastrowid)
            if source_id:
                if status == "success":
                    cursor.execute(
                        """
                        UPDATE article_detail_sources
                        SET latest_run_id = %s,
                            attempts = 0,
                            last_error = NULL,
                            writeback_status = CASE
                                WHEN source_type = 'tencent_docs'
                                 AND source_locator_json LIKE '%%"file_id"%%'
                                 AND source_locator_json LIKE '%%"sheet_id"%%'
                                 AND source_locator_json LIKE '%%"row_index"%%'
                                THEN 'pending'
                                ELSE writeback_status
                            END,
                            writeback_error = NULL
                        WHERE id = %s
                        """,
                        (run_id, source_id),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE article_detail_sources
                        SET latest_run_id = %s,
                            attempts = attempts + 1,
                            last_error = %s
                        WHERE id = %s
                        """,
                        (run_id, error, source_id),
                    )
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_article_writebacks(limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT
            s.id AS source_id,
            s.source_locator_json,
            s.latest_run_id AS run_id,
            r.article_title,
            r.comment_count,
            r.like_count,
            r.screenshot_path,
            r.status AS run_status,
            t.ip_name,
            t.article_url
        FROM article_detail_sources s
        JOIN article_detail_runs r ON r.id = s.latest_run_id
        JOIN article_detail_targets t ON t.id = s.target_id
        WHERE s.status = 'active'
          AND s.writeback_status = 'pending'
          AND r.status = 'success'
        ORDER BY s.id ASC
    """
    params: list[Any] = []
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return [_decode_row(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def mark_article_writeback(
    *,
    source_id: int,
    run_id: int | None,
    locator: dict[str, Any],
    status: str,
    error: str | None = None,
) -> None:
    written_at = datetime.now() if status == "success" else None
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO article_detail_writebacks
                    (source_id, run_id, sink_type, sink_locator_json, field_name,
                     status, error, written_at)
                VALUES (%s, %s, 'tencent_docs', %s, 'article_detail', %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    run_id = VALUES(run_id),
                    sink_locator_json = VALUES(sink_locator_json),
                    status = VALUES(status),
                    error = VALUES(error),
                    written_at = VALUES(written_at)
                """,
                (source_id, run_id, _json_dumps(locator), status, error, written_at),
            )
            cursor.execute(
                """
                UPDATE article_detail_sources
                SET writeback_status = %s,
                    writeback_error = %s,
                    written_at = %s
                WHERE id = %s
                """,
                (status, error, written_at, source_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def article_detail_summary() -> dict[str, int]:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM article_detail_targets WHERE status = 'active'")
            targets = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) AS count FROM article_detail_sources WHERE status = 'active'")
            sources = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) AS count FROM article_detail_runs WHERE status = 'success'")
            runs = int(cursor.fetchone()["count"])
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM article_detail_sources
                WHERE status = 'active'
                  AND writeback_status = 'pending'
                """
            )
            pending_writebacks = int(cursor.fetchone()["count"])
    finally:
        conn.close()
    return {
        "article_targets": targets,
        "article_sources": sources,
        "article_success_runs": runs,
        "article_pending_writebacks": pending_writebacks,
    }


def _source_key(locator: dict[str, Any], article_key: str) -> str:
    file_id = str(locator.get("file_id") or "")
    sheet_id = str(locator.get("sheet_id") or "")
    row_index = str(locator.get("row_index") or "")
    raw = f"{file_id}:{sheet_id}:{row_index}:{article_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    decoded["source_locator"] = _json_loads(decoded.pop("source_locator_json", None)) or {}
    return decoded
