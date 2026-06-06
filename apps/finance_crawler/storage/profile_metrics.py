"""Storage helpers for homepage profile metric collection."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any

from apps.finance_crawler.storage.db import get_conn

PROFILE_DAILY_METRICS_TASK_TYPE = "profile_daily_metrics"
PROFILE_DAILY_METRICS_FIELDS = ("fans_count", "growth_count", "read_count")


def profile_key_for_url(url: str) -> str:
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def upsert_profile_trigger_config(
    *,
    config_key: str,
    doc_url: str,
    file_id: str | None = None,
    sheet_id: str | None = None,
    read_range: str = "A1:I5000",
    row_adapter: str = "kol_daily_profile",
    source_name: str,
    task_type: str = PROFILE_DAILY_METRICS_TASK_TYPE,
    requested_fields: tuple[str, ...] | list[str] | None = None,
    action_profile_key: str | None = None,
    aggregation_policy: dict[str, Any] | None = None,
    schedule_time: str | None = None,
    target_date_offset_days: int = 0,
    scan_interval_seconds: int = 300,
    status: str = "active",
    description: str | None = None,
    updated_by: str = "system",
) -> int:
    fields = tuple(requested_fields or PROFILE_DAILY_METRICS_FIELDS)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO profile_trigger_configs (
                    config_key, source_type, doc_url, file_id, sheet_id, read_range,
                    row_adapter, source_name, task_type, requested_fields_json,
                    action_profile_key, aggregation_policy_json, schedule_time,
                    target_date_offset_days, scan_interval_seconds, status,
                    description, updated_by
                )
                VALUES (%s, 'tencent_docs', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    doc_url = VALUES(doc_url),
                    file_id = VALUES(file_id),
                    sheet_id = VALUES(sheet_id),
                    read_range = VALUES(read_range),
                    row_adapter = VALUES(row_adapter),
                    source_name = VALUES(source_name),
                    task_type = VALUES(task_type),
                    requested_fields_json = VALUES(requested_fields_json),
                    action_profile_key = VALUES(action_profile_key),
                    aggregation_policy_json = VALUES(aggregation_policy_json),
                    schedule_time = VALUES(schedule_time),
                    target_date_offset_days = VALUES(target_date_offset_days),
                    scan_interval_seconds = VALUES(scan_interval_seconds),
                    status = VALUES(status),
                    description = VALUES(description),
                    updated_by = VALUES(updated_by)
                """,
                (
                    config_key,
                    doc_url,
                    file_id,
                    sheet_id,
                    read_range,
                    row_adapter,
                    source_name,
                    task_type,
                    _json_dumps(list(fields)),
                    action_profile_key,
                    _json_dumps(aggregation_policy or {}),
                    schedule_time,
                    int(target_date_offset_days),
                    int(scan_interval_seconds),
                    status,
                    description,
                    updated_by,
                ),
            )
            cursor.execute("SELECT id FROM profile_trigger_configs WHERE config_key = %s", (config_key,))
            config_id = int(cursor.fetchone()["id"])
        conn.commit()
        return config_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_profile_trigger_config(config_key: str) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM profile_trigger_configs
                WHERE config_key = %s
                LIMIT 1
                """,
                (config_key,),
            )
            row = cursor.fetchone()
            return _decode_profile_trigger_config(row) if row else None
    finally:
        conn.close()


def list_profile_trigger_configs(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM profile_trigger_configs"
    params: list[Any] = []
    if not include_disabled:
        sql += " WHERE status = %s"
        params.append("active")
    sql += " ORDER BY config_key ASC"
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return [_decode_profile_trigger_config(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_profile_action_profile(
    *,
    app_type: str | None = None,
    task_type: str = PROFILE_DAILY_METRICS_TASK_TYPE,
    field_names: tuple[str, ...] | list[str] | None = None,
    action_profile_key: str | None = None,
) -> dict[str, Any] | None:
    fields = ",".join(field_names or PROFILE_DAILY_METRICS_FIELDS)
    params: list[Any]
    if action_profile_key:
        sql = """
            SELECT *
            FROM profile_action_profiles
            WHERE action_profile_key = %s
              AND status = 'active'
            LIMIT 1
        """
        params = [action_profile_key]
    else:
        sql = """
            SELECT *
            FROM profile_action_profiles
            WHERE task_type = %s
              AND field_combo = %s
              AND app_type IN (%s, 'unknown')
              AND status = 'active'
            ORDER BY CASE WHEN app_type = %s THEN 0 ELSE 1 END, priority DESC, id ASC
            LIMIT 1
        """
        resolved_app = app_type or "unknown"
        params = [task_type, fields, resolved_app, resolved_app]
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return _decode_profile_action_profile(row) if row else None
    finally:
        conn.close()


def start_profile_trigger_run(
    *,
    trigger_config_id: int | None,
    config_key: str,
    trigger_type: str,
    target_date: date | None,
    action_profile_key: str | None,
) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO profile_trigger_runs (
                    trigger_config_id, config_key, trigger_type, target_date,
                    action_profile_key, status, started_at
                )
                VALUES (%s, %s, %s, %s, %s, 'running', %s)
                """,
                (trigger_config_id, config_key, trigger_type, target_date, action_profile_key, datetime.now()),
            )
            run_id = int(cursor.lastrowid)
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_profile_trigger_run(
    run_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    summary = summary or {}
    sync = summary.get("sync") if isinstance(summary.get("sync"), dict) else {}
    writeback = summary.get("writeback") if isinstance(summary.get("writeback"), dict) else {}
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE profile_trigger_runs
                SET status = %s,
                    file_id = %s,
                    sheet_id = %s,
                    sheet_title = %s,
                    source_rows = %s,
                    submitted_sources = %s,
                    skipped_rows = %s,
                    fans_crawled = %s,
                    read_crawled = %s,
                    written_rows = %s,
                    failed_rows = %s,
                    summary_json = %s,
                    error = %s,
                    finished_at = %s
                WHERE id = %s
                """,
                (
                    status,
                    sync.get("file_id") or writeback.get("file_id"),
                    sync.get("sheet_id") or writeback.get("sheet_id"),
                    sync.get("sheet_title") or writeback.get("sheet_title"),
                    int(sync.get("source_rows") or 0),
                    int(sync.get("imported") or 0),
                    int(sync.get("skipped") or 0),
                    int(summary.get("fans_crawled") or 0),
                    int(summary.get("read_crawled") or 0),
                    int(writeback.get("written") or 0),
                    int(writeback.get("failed") or 0),
                    _json_dumps(summary),
                    error,
                    datetime.now(),
                    run_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _decode_profile_trigger_config(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    decoded["requested_fields"] = _json_loads(decoded.pop("requested_fields_json", None)) or []
    decoded["aggregation_policy"] = _json_loads(decoded.pop("aggregation_policy_json", None)) or {}
    return decoded


def _decode_profile_action_profile(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    decoded["field_names"] = _json_loads(decoded.pop("field_names_json", None)) or []
    decoded["action_names"] = _json_loads(decoded.pop("action_names_json", None)) or []
    decoded["action_config"] = _json_loads(decoded.pop("action_config_json", None)) or {}
    decoded["aggregation_policy"] = _json_loads(decoded.pop("aggregation_policy_json", None)) or {}
    return decoded


def upsert_profile_source(row: dict[str, Any]) -> tuple[int, int]:
    """Upsert a profile target and one dated source-row binding."""

    profile_key = row.get("profile_key") or profile_key_for_url(str(row["homepage_url"]))
    metric_date = row["metric_date"]
    source_locator = dict(row.get("source_locator") or {})
    source_key = row.get("source_key") or _source_key(source_locator, profile_key, metric_date)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO profile_targets
                    (profile_key, account_name, platform, app_type, homepage_url, status,
                     source_json, first_seen_date, latest_seen_date)
                VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    account_name = COALESCE(NULLIF(VALUES(account_name), ''), account_name),
                    platform = COALESCE(NULLIF(VALUES(platform), ''), platform),
                    app_type = VALUES(app_type),
                    homepage_url = VALUES(homepage_url),
                    status = 'active',
                    source_json = VALUES(source_json),
                    first_seen_date = LEAST(COALESCE(first_seen_date, VALUES(first_seen_date)), VALUES(first_seen_date)),
                    latest_seen_date = GREATEST(COALESCE(latest_seen_date, VALUES(latest_seen_date)), VALUES(latest_seen_date))
                """,
                (
                    profile_key,
                    row.get("account_name") or "",
                    row.get("platform") or "",
                    row.get("app_type") or "unknown",
                    row["homepage_url"],
                    _json_dumps(row.get("source") or {}),
                    metric_date,
                    metric_date,
                ),
            )
            cursor.execute("SELECT id FROM profile_targets WHERE profile_key = %s", (profile_key,))
            target_id = int(cursor.fetchone()["id"])
            cursor.execute(
                """
                INSERT INTO profile_metric_sources
                    (target_id, metric_date, source_type, source_name, source_key,
                     source_locator_json, requested_fields_json, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')
                ON DUPLICATE KEY UPDATE
                    target_id = VALUES(target_id),
                    metric_date = VALUES(metric_date),
                    source_name = VALUES(source_name),
                    source_locator_json = VALUES(source_locator_json),
                    requested_fields_json = VALUES(requested_fields_json),
                    status = 'active'
                """,
                (
                    target_id,
                    metric_date,
                    row.get("source_type") or "tencent_docs",
                    row.get("source_name") or "",
                    source_key,
                    _json_dumps(source_locator),
                    _json_dumps(row.get("requested_fields") or ["fans_count"]),
                ),
            )
            cursor.execute(
                "SELECT id FROM profile_metric_sources WHERE source_type = %s AND source_key = %s",
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


def record_profile_metric(
    *,
    target_id: int,
    metric_date: date,
    app_type: str,
    homepage_url: str,
    status: str,
    fans_count: int | None,
    growth_count: int | None = None,
    read_count: int | None = None,
    metrics: dict[str, Any] | None = None,
    screenshot_path: str | None = None,
    error: str | None = None,
    crawled_at: datetime | None = None,
) -> int:
    crawled_at = crawled_at or datetime.now()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            if status == "success" and fans_count is not None and growth_count is None:
                growth_count = _calculate_growth_count_tx(cursor, target_id, metric_date, fans_count)
            cursor.execute(
                """
                INSERT INTO profile_metric_runs
                    (target_id, metric_date, app_type, homepage_url, status, fans_count,
                     growth_count, read_count, metrics_json, screenshot_path, error, crawled_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    app_type = VALUES(app_type),
                    homepage_url = VALUES(homepage_url),
                    status = VALUES(status),
                    fans_count = VALUES(fans_count),
                    growth_count = VALUES(growth_count),
                    read_count = VALUES(read_count),
                    metrics_json = VALUES(metrics_json),
                    screenshot_path = VALUES(screenshot_path),
                    error = VALUES(error),
                    crawled_at = VALUES(crawled_at)
                """,
                (
                    target_id,
                    metric_date,
                    app_type or "unknown",
                    homepage_url,
                    status,
                    fans_count,
                    growth_count,
                    read_count,
                    _json_dumps(metrics or {}),
                    screenshot_path,
                    error,
                    crawled_at,
                ),
            )
            cursor.execute(
                "SELECT id FROM profile_metric_runs WHERE target_id = %s AND metric_date = %s",
                (target_id, metric_date),
            )
            metric_id = int(cursor.fetchone()["id"])
            if status == "success":
                cursor.execute(
                    """
                    UPDATE profile_metric_sources
                    SET latest_metric_id = %s,
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
                    WHERE target_id = %s
                      AND metric_date = %s
                    """,
                    (metric_id, target_id, metric_date),
                )
            else:
                cursor.execute(
                    """
                    UPDATE profile_metric_sources
                    SET attempts = attempts + 1,
                        last_error = %s
                    WHERE target_id = %s
                      AND metric_date = %s
                    """,
                    (error, target_id, metric_date),
                )
        conn.commit()
        return metric_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _calculate_growth_count_tx(cursor, target_id: int, metric_date: date, fans_count: int) -> int:
    previous_date = metric_date - timedelta(days=1)
    cursor.execute(
        """
        SELECT fans_count
        FROM profile_metric_runs
        WHERE target_id = %s
          AND metric_date = %s
          AND status = 'success'
          AND fans_count IS NOT NULL
        LIMIT 1
        """,
        (target_id, previous_date),
    )
    previous = cursor.fetchone()
    if not previous or previous.get("fans_count") is None:
        return 0
    return fans_count - int(previous["fans_count"])


def create_daily_profile_metric_sources(metric_date: date, *, source_name: str = "daily_profile_metrics") -> int:
    """Create DB-only fan-count tasks for every active profile target."""

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, profile_key
                FROM profile_targets
                WHERE status = 'active'
                ORDER BY id ASC
                """
            )
            targets = cursor.fetchall()
            rows = []
            for target in targets:
                source_key = profile_key_for_url(
                    f"profile_daily:{metric_date.isoformat()}:{target['profile_key']}"
                )
                rows.append(
                    (
                        int(target["id"]),
                        metric_date,
                        "profile_daily",
                        source_name,
                        source_key,
                        _json_dumps({"created_by": "profile_daily", "metric_date": metric_date.isoformat()}),
                        _json_dumps(["fans_count"]),
                    )
                )
            if rows:
                cursor.executemany(
                    """
                    INSERT INTO profile_metric_sources
                        (target_id, metric_date, source_type, source_name, source_key,
                         source_locator_json, requested_fields_json, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')
                    ON DUPLICATE KEY UPDATE
                        target_id = VALUES(target_id),
                        metric_date = VALUES(metric_date),
                        source_name = VALUES(source_name),
                        source_locator_json = VALUES(source_locator_json),
                        requested_fields_json = VALUES(requested_fields_json),
                        status = 'active'
                    """,
                    rows,
                )
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_profile_metric_sources(
    limit: int | None = None,
    metric_date: date | None = None,
    source_name: str | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    date_clause = ""
    if metric_date:
        date_clause = "AND s.metric_date = %s"
        params.append(metric_date)
    source_clause = ""
    if source_name:
        source_clause = "AND s.source_name = %s"
        params.append(source_name)
    sql = f"""
        SELECT
            s.id AS metric_source_id,
            s.metric_date,
            s.source_locator_json,
            t.id AS target_id,
            t.account_name,
            t.platform,
            t.app_type,
            t.homepage_url
        FROM profile_metric_sources s
        JOIN profile_targets t ON t.id = s.target_id
        LEFT JOIN profile_metric_runs m
          ON m.target_id = s.target_id
         AND m.metric_date = s.metric_date
        WHERE s.status = 'active'
          AND t.status = 'active'
          AND s.attempts < s.max_attempts
          AND (m.id IS NULL OR m.status NOT IN ('success', 'blocked'))
          {date_clause}
          {source_clause}
        ORDER BY s.metric_date ASC, s.id ASC
    """
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows_by_target_date: dict[tuple[int, date], dict[str, Any]] = {}
            for row in cursor.fetchall():
                key = (int(row["target_id"]), row["metric_date"])
                rows_by_target_date[key] = row
            rows = [_decode_row(row) for row in rows_by_target_date.values()]
            rows.sort(key=lambda item: (item["metric_date"], int(item["metric_source_id"])))
            return rows[:limit] if limit and limit > 0 else rows
    finally:
        conn.close()


def get_profile_targets_for_post_reads(
    limit: int | None = None,
    metric_date: date | None = None,
    source_name: str | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    date_clause = ""
    if metric_date:
        date_clause = "AND s.metric_date = %s"
        params.append(metric_date)
    source_clause = ""
    if source_name:
        source_clause = "AND s.source_name = %s"
        params.append(source_name)
    sql = f"""
        SELECT
            s.id AS metric_source_id,
            s.metric_date,
            s.source_locator_json,
            t.id AS target_id,
            t.account_name,
            t.platform,
            t.app_type,
            t.homepage_url,
            m.id AS metric_id,
            m.fans_count,
            m.growth_count,
            m.read_count,
            m.status AS metric_status
        FROM profile_metric_sources s
        JOIN profile_targets t ON t.id = s.target_id
        LEFT JOIN profile_metric_runs m
          ON m.target_id = s.target_id
         AND m.metric_date = s.metric_date
        WHERE s.status = 'active'
          AND t.status = 'active'
          {date_clause}
          {source_clause}
        ORDER BY s.metric_date ASC, s.id ASC
    """
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows_by_url_date: dict[tuple[str, date], dict[str, Any]] = {}
            for row in cursor.fetchall():
                key = (str(row["homepage_url"]).strip(), row["metric_date"])
                rows_by_url_date[key] = row
            rows = [_decode_row(row) for row in rows_by_url_date.values()]
            rows.sort(key=lambda item: (item["metric_date"], int(item["metric_source_id"])))
            return rows[:limit] if limit and limit > 0 else rows
    finally:
        conn.close()


def update_profile_post_read_metric(
    *,
    target_id: int,
    metric_date: date,
    app_type: str,
    homepage_url: str,
    read_count: int | None,
    posts: list[dict[str, Any]],
    screenshot_path: str | None = None,
    error: str | None = None,
) -> int:
    status = "success" if error is None else "error"
    metrics = {
        "workflow": "profile_post_reads",
        "posts": posts,
        "post_count": len(posts),
    }
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, fans_count, growth_count, status, metrics_json
                FROM profile_metric_runs
                WHERE target_id = %s AND metric_date = %s
                LIMIT 1
                """,
                (target_id, metric_date),
            )
            existing = cursor.fetchone()
            if existing:
                existing_metrics = _json_loads(existing.get("metrics_json")) or {}
                if isinstance(existing_metrics, dict):
                    existing_metrics.update(metrics)
                    metrics = existing_metrics
                next_status = existing.get("status") or status
                if status == "error" and next_status != "success":
                    next_status = "error"
                cursor.execute(
                    """
                    UPDATE profile_metric_runs
                    SET read_count = %s,
                        metrics_json = %s,
                        screenshot_path = COALESCE(%s, screenshot_path),
                        error = %s,
                        status = %s,
                        crawled_at = %s
                    WHERE id = %s
                    """,
                    (
                        read_count,
                        _json_dumps(metrics),
                        screenshot_path,
                        error,
                        next_status,
                        datetime.now(),
                        int(existing["id"]),
                    ),
                )
                metric_id = int(existing["id"])
            else:
                cursor.execute(
                    """
                    INSERT INTO profile_metric_runs
                        (target_id, metric_date, app_type, homepage_url, status,
                         fans_count, growth_count, read_count, metrics_json,
                         screenshot_path, error, crawled_at)
                    VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s)
                    """,
                    (
                        target_id,
                        metric_date,
                        app_type or "unknown",
                        homepage_url,
                        status,
                        read_count,
                        _json_dumps(metrics),
                        screenshot_path,
                        error,
                        datetime.now(),
                    ),
                )
                metric_id = int(cursor.lastrowid)
        conn.commit()
        return metric_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_profile_writebacks(limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT
            s.id AS metric_source_id,
            s.metric_date,
            s.source_locator_json,
            s.latest_metric_id AS metric_id,
            m.fans_count,
            m.growth_count,
            m.status AS metric_status,
            t.account_name,
            t.homepage_url
        FROM profile_metric_sources s
        JOIN profile_metric_runs m ON m.id = s.latest_metric_id
        JOIN profile_targets t ON t.id = s.target_id
        WHERE s.status = 'active'
          AND s.writeback_status = 'pending'
          AND m.status = 'success'
          AND m.fans_count IS NOT NULL
        ORDER BY s.metric_date ASC, s.id ASC
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


def mark_profile_writeback(
    *,
    metric_source_id: int,
    metric_id: int | None,
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
                INSERT INTO profile_metric_writebacks
                    (metric_source_id, metric_id, sink_type, sink_locator_json, field_name,
                     status, error, written_at)
                VALUES (%s, %s, 'tencent_docs', %s, 'fans_count', %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    metric_id = VALUES(metric_id),
                    sink_locator_json = VALUES(sink_locator_json),
                    status = VALUES(status),
                    error = VALUES(error),
                    written_at = VALUES(written_at)
                """,
                (metric_source_id, metric_id, _json_dumps(locator), status, error, written_at),
            )
            cursor.execute(
                """
                UPDATE profile_metric_sources
                SET writeback_status = %s,
                    writeback_error = %s,
                    written_at = %s
                WHERE id = %s
                """,
                (status, error, written_at, metric_source_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def profile_summary() -> dict[str, int]:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM profile_targets WHERE status = 'active'")
            targets = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) AS count FROM profile_metric_sources WHERE status = 'active'")
            sources = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) AS count FROM profile_metric_runs WHERE status = 'success'")
            metrics = int(cursor.fetchone()["count"])
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM profile_metric_sources
                WHERE status = 'active'
                  AND writeback_status = 'pending'
                """
            )
            pending_writebacks = int(cursor.fetchone()["count"])
    finally:
        conn.close()
    return {
        "targets": targets,
        "sources": sources,
        "metrics": metrics,
        "pending_writebacks": pending_writebacks,
    }


def _source_key(locator: dict[str, Any], profile_key: str, metric_date: date) -> str:
    file_id = str(locator.get("file_id") or "")
    sheet_id = str(locator.get("sheet_id") or "")
    row_index = str(locator.get("row_index") or "")
    raw = f"{file_id}:{sheet_id}:{row_index}:{metric_date.isoformat()}:{profile_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    decoded["source_locator"] = _json_loads(decoded.pop("source_locator_json", None)) or {}
    return decoded
