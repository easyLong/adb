"""Repository helpers for the crawler_app v2 tables."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from apps.finance_crawler.crawler_app.documents.column_resolver import ColumnMapping
from apps.finance_crawler.crawler_app.documents.rows import DocumentSourceRow
from apps.finance_crawler.crawler_app.tasks.submission import TaskSubmission


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def json_loads(value: str | bytes | None) -> Any:
    if not value:
        return {}
    return json.loads(value)


def upsert_kol_base_profile(
    conn,
    *,
    kol_name: str,
    platform: str,
    homepage_url: str | None = None,
    group_name: str | None = None,
    kol_type: str | None = None,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO kol_base_profiles (
                kol_name, platform, homepage_url, group_name, kol_type
            )
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                homepage_url = COALESCE(NULLIF(VALUES(homepage_url), ''), homepage_url),
                group_name = COALESCE(NULLIF(VALUES(group_name), ''), group_name),
                kol_type = COALESCE(NULLIF(VALUES(kol_type), ''), kol_type),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                kol_name,
                platform,
                homepage_url or "",
                group_name or "",
                kol_type or "other",
            ),
        )
        return int(cursor.lastrowid)


def get_kol_base_profile(conn, *, kol_name: str, platform: str) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM kol_base_profiles
            WHERE kol_name = %s
              AND platform = %s
            LIMIT 1
            """,
            (kol_name, platform),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def list_kol_base_profiles(conn) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM kol_base_profiles
            ORDER BY id ASC
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def upsert_kol_daily_snapshot(
    conn,
    *,
    kol_profile_id: int | None,
    snapshot_date: date,
    kol_name: str,
    platform: str,
    homepage_url: str | None = None,
    group_name: str | None = None,
    kol_type: str | None = None,
    fans_count: int | None = None,
    growth_count: int | None = None,
    read_count: int | None = None,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO kol_daily_snapshots (
                kol_profile_id, snapshot_date, kol_name, platform,
                homepage_url, group_name, kol_type,
                fans_count, growth_count, read_count
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                kol_profile_id = VALUES(kol_profile_id),
                homepage_url = VALUES(homepage_url),
                group_name = VALUES(group_name),
                kol_type = VALUES(kol_type),
                fans_count = VALUES(fans_count),
                growth_count = VALUES(growth_count),
                read_count = VALUES(read_count),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                kol_profile_id,
                snapshot_date,
                kol_name,
                platform,
                homepage_url or "",
                group_name or "",
                kol_type or "other",
                fans_count,
                growth_count,
                read_count,
            ),
        )
        return int(cursor.lastrowid)


def list_kol_daily_snapshots(conn, *, snapshot_date: date) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM kol_daily_snapshots
            WHERE snapshot_date = %s
            ORDER BY id ASC
            """,
            (snapshot_date,),
        )
        return [dict(row) for row in cursor.fetchall()]


def upsert_document(conn, *, source_type: str, doc_url: str, file_id: str, title: str = "") -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO documents (source_type, doc_url, file_id, title, status)
            VALUES (%s, %s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                doc_url = VALUES(doc_url),
                title = COALESCE(NULLIF(VALUES(title), ''), title),
                status = 'active',
                updated_at = CURRENT_TIMESTAMP
            """,
            (source_type, doc_url, file_id, title),
        )
        return int(cursor.lastrowid)


def upsert_document_sheet(
    conn,
    *,
    document_id: int,
    sheet_id: str,
    sheet_title: str,
    business_date: date | None,
    header_row_index: int = 0,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO document_sheets (
                document_id, sheet_id, sheet_title, business_date, header_row_index, status
            )
            VALUES (%s, %s, %s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                sheet_title = VALUES(sheet_title),
                business_date = VALUES(business_date),
                header_row_index = VALUES(header_row_index),
                status = 'active',
                updated_at = CURRENT_TIMESTAMP
            """,
            (document_id, sheet_id, sheet_title, business_date, header_row_index),
        )
        return int(cursor.lastrowid)


def upsert_column_mapping(
    conn,
    *,
    document_id: int,
    sheet_id: str,
    header_row_index: int,
    mapping: ColumnMapping,
) -> int:
    payload = mapping.to_json_dict()
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO column_mappings (
                document_id, sheet_id, header_row_index, header_hash,
                mapping_json, resolution_json, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                mapping_json = VALUES(mapping_json),
                resolution_json = VALUES(resolution_json),
                status = 'active'
            """,
            (
                document_id,
                sheet_id,
                header_row_index,
                mapping.header_hash,
                json_dumps(mapping.columns),
                json_dumps(payload),
            ),
        )
        return int(cursor.lastrowid)


def upsert_source_rows(
    conn,
    *,
    document_id: int,
    sheet_id: str,
    column_mapping_id: int,
    rows: list[DocumentSourceRow],
) -> dict[int, int]:
    if not rows:
        return {}
    output: dict[int, int] = {}
    with conn.cursor() as cursor:
        for row in rows:
            cursor.execute(
                """
                INSERT INTO source_rows (
                    document_id, sheet_id, column_mapping_id, row_index,
                    business_date, post_url, account_name, post_time,
                    row_hash, row_json, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
                ON DUPLICATE KEY UPDATE
                    id = LAST_INSERT_ID(id),
                    column_mapping_id = VALUES(column_mapping_id),
                    business_date = VALUES(business_date),
                    post_url = VALUES(post_url),
                    account_name = VALUES(account_name),
                    post_time = VALUES(post_time),
                    row_hash = VALUES(row_hash),
                    row_json = VALUES(row_json),
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document_id,
                    sheet_id,
                    column_mapping_id,
                    row.row_index,
                    row.business_date,
                    row.post_url,
                    row.account_name,
                    row.post_time,
                    row.row_hash,
                    json_dumps(row.values),
                ),
            )
            output[row.row_index] = int(cursor.lastrowid)
    return output


def get_source_row_by_position(
    conn,
    *,
    document_id: int,
    sheet_id: str,
    row_index: int,
) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM source_rows
            WHERE document_id = %s
              AND sheet_id = %s
              AND row_index = %s
            LIMIT 1
            """,
            (document_id, sheet_id, row_index),
        )
        row = cursor.fetchone()
    if not row:
        return None
    item = dict(row)
    item["row_values"] = json_loads(item.get("row_json"))
    return item


def find_source_rows_for_correction(
    conn,
    *,
    source_type: str,
    file_id: str,
    sheet_id: str | None = None,
    row_index: int | None = None,
    post_url: str | None = None,
    business_date: date | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT r.*, d.source_type, d.file_id
        FROM source_rows r
        JOIN documents d ON d.id = r.document_id
        WHERE d.source_type = %s
          AND d.file_id = %s
          AND r.status = 'active'
    """
    params: list[Any] = [source_type, file_id]
    if sheet_id:
        sql += " AND r.sheet_id = %s"
        params.append(sheet_id)
    if row_index and row_index > 0:
        sql += " AND r.row_index = %s"
        params.append(row_index)
    if post_url:
        sql += " AND r.post_url = %s"
        params.append(post_url)
    if business_date:
        sql += " AND r.business_date = %s"
        params.append(business_date)
    sql += " ORDER BY r.updated_at DESC, r.id DESC"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["row_values"] = json_loads(item.get("row_json"))
        output.append(item)
    return output


def get_column_mapping(conn, column_mapping_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM column_mappings
            WHERE id = %s
            LIMIT 1
            """,
            (column_mapping_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    item = dict(row)
    item["mapping"] = json_loads(item.get("mapping_json"))
    item["resolution"] = json_loads(item.get("resolution_json"))
    return item


def upsert_document_task_config(
    conn,
    *,
    config_key: str,
    source_type: str,
    doc_url: str,
    file_id: str | None,
    sheet_id: str | None,
    task_type: str,
    field_names: list[str] | tuple[str, ...],
    sheet_selector: dict[str, Any] | None = None,
    status: str = "active",
    priority: int = 0,
    max_attempts: int = 3,
    description: str | None = None,
    updated_by: str | None = "system",
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO document_task_configs (
                config_key, source_type, doc_url, file_id, sheet_id, task_type,
                field_names_json, sheet_selector_json, status, priority,
                max_attempts, description, updated_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                source_type = VALUES(source_type),
                doc_url = VALUES(doc_url),
                file_id = VALUES(file_id),
                sheet_id = VALUES(sheet_id),
                task_type = VALUES(task_type),
                field_names_json = VALUES(field_names_json),
                sheet_selector_json = VALUES(sheet_selector_json),
                status = VALUES(status),
                priority = VALUES(priority),
                max_attempts = VALUES(max_attempts),
                description = VALUES(description),
                updated_by = VALUES(updated_by),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                config_key,
                source_type,
                doc_url,
                file_id,
                sheet_id,
                task_type,
                json_dumps(list(field_names)),
                json_dumps(sheet_selector or {}),
                status,
                priority,
                max_attempts,
                description,
                updated_by,
            ),
        )
        return int(cursor.lastrowid)


def get_document_task_config(conn, config_key: str) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM document_task_configs
            WHERE config_key = %s
            LIMIT 1
            """,
            (config_key,),
        )
        row = cursor.fetchone()
    return _decode_document_task_config(row) if row else None


def list_document_task_configs(conn, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    sql = """
        SELECT *
        FROM document_task_configs
    """
    params: list[Any] = []
    if not include_disabled:
        sql += " WHERE status = %s"
        params.append("active")
    sql += " ORDER BY priority DESC, id ASC"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [_decode_document_task_config(row) for row in rows]


def upsert_document_trigger_config(
    conn,
    *,
    config_key: str,
    source_type: str,
    doc_url: str,
    file_id: str | None,
    sheet_selector: dict[str, Any] | None = None,
    submit_policy: dict[str, Any] | None = None,
    scan_interval_seconds: int = 300,
    status: str = "active",
    description: str | None = None,
    updated_by: str | None = "system",
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO document_trigger_configs (
                config_key, source_type, doc_url, file_id, sheet_selector_json,
                submit_policy_json, scan_interval_seconds, next_scan_at,
                status, description, updated_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                source_type = VALUES(source_type),
                doc_url = VALUES(doc_url),
                file_id = VALUES(file_id),
                sheet_selector_json = VALUES(sheet_selector_json),
                submit_policy_json = VALUES(submit_policy_json),
                scan_interval_seconds = VALUES(scan_interval_seconds),
                status = VALUES(status),
                description = VALUES(description),
                updated_by = VALUES(updated_by),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                config_key,
                source_type,
                doc_url,
                file_id,
                json_dumps(sheet_selector or {}),
                json_dumps(submit_policy or {}),
                max(int(scan_interval_seconds or 0), 0),
                status,
                description,
                updated_by,
            ),
        )
        return int(cursor.lastrowid)


def upsert_document_trigger_binding(
    conn,
    *,
    config_id: int,
    task_type: str,
    field_names: list[str] | tuple[str, ...],
    status: str = "active",
    priority: int = 0,
    max_attempts: int = 3,
    description: str | None = None,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO document_trigger_bindings (
                config_id, task_type, field_names_json, status,
                priority, max_attempts, description
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                id = LAST_INSERT_ID(id),
                field_names_json = VALUES(field_names_json),
                status = VALUES(status),
                priority = VALUES(priority),
                max_attempts = VALUES(max_attempts),
                description = VALUES(description),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                config_id,
                task_type,
                json_dumps(list(field_names)),
                status,
                priority,
                max_attempts,
                description,
            ),
        )
        return int(cursor.lastrowid)


def get_document_trigger_config(conn, config_key: str) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM document_trigger_configs
            WHERE config_key = %s
            LIMIT 1
            """,
            (config_key,),
        )
        row = cursor.fetchone()
    return _decode_document_trigger_config(row) if row else None


def get_document_trigger_config_by_id(conn, config_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM document_trigger_configs
            WHERE id = %s
            LIMIT 1
            """,
            (config_id,),
        )
        row = cursor.fetchone()
    return _decode_document_trigger_config(row) if row else None


def list_document_trigger_configs(conn, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM document_trigger_configs"
    params: list[Any] = []
    if not include_disabled:
        sql += " WHERE status = %s"
        params.append("active")
    sql += " ORDER BY id ASC"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return [_decode_document_trigger_config(row) for row in cursor.fetchall()]


def get_document_trigger_bindings(conn, config_id: int, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    sql = """
        SELECT *
        FROM document_trigger_bindings
        WHERE config_id = %s
    """
    params: list[Any] = [config_id]
    if not include_disabled:
        sql += " AND status = %s"
        params.append("active")
    sql += " ORDER BY priority DESC, id ASC"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return [_decode_document_trigger_binding(row) for row in cursor.fetchall()]


def get_due_document_trigger_configs(conn, *, limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT *
        FROM document_trigger_configs
        WHERE status = 'active'
          AND (next_scan_at IS NULL OR next_scan_at <= NOW())
          AND (locked_until IS NULL OR locked_until < NOW())
        ORDER BY COALESCE(next_scan_at, created_at), id
    """
    params: list[Any] = []
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return [_decode_document_trigger_config(row) for row in cursor.fetchall()]


def claim_document_trigger_config(
    conn,
    *,
    config_id: int,
    worker_id: str,
    lock_seconds: int = 300,
) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE document_trigger_configs
            SET scan_status = 'running',
                locked_by = %s,
                locked_until = DATE_ADD(NOW(), INTERVAL %s SECOND),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status = 'active'
              AND (next_scan_at IS NULL OR next_scan_at <= NOW())
              AND (locked_until IS NULL OR locked_until < NOW())
            """,
            (worker_id, max(int(lock_seconds or 0), 1), config_id),
        )
        return int(cursor.rowcount) == 1


def finish_document_trigger_scan(
    conn,
    *,
    config_id: int,
    status: str,
    error: str | None = None,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE document_trigger_configs
            SET scan_status = %s,
                locked_by = NULL,
                locked_until = NULL,
                last_scan_at = NOW(),
                next_scan_at = CASE
                    WHEN scan_interval_seconds > 0
                    THEN DATE_ADD(NOW(), INTERVAL scan_interval_seconds SECOND)
                    ELSE NULL
                END,
                last_error = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (status, error, config_id),
        )


def start_submit_run(
    conn,
    *,
    config_id: int | None,
    trigger_type: str,
    sheet_id: str | None = None,
    sheet_title: str | None = None,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO submit_runs (
                config_id, trigger_type, sheet_id, sheet_title, status, started_at
            )
            VALUES (%s, %s, %s, %s, 'running', NOW())
            """,
            (config_id, trigger_type, sheet_id, sheet_title),
        )
        return int(cursor.lastrowid)


def finish_submit_run(
    conn,
    *,
    run_id: int,
    status: str,
    sheet_id: str | None = None,
    sheet_title: str | None = None,
    source_rows: int = 0,
    submitted_tasks: int = 0,
    skipped_rows: int = 0,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE submit_runs
            SET status = %s,
                sheet_id = COALESCE(%s, sheet_id),
                sheet_title = COALESCE(%s, sheet_title),
                source_rows = %s,
                submitted_tasks = %s,
                skipped_rows = %s,
                summary_json = %s,
                error = %s,
                finished_at = NOW()
            WHERE id = %s
            """,
            (
                status,
                sheet_id,
                sheet_title,
                source_rows,
                submitted_tasks,
                skipped_rows,
                json_dumps(summary or {}),
                error,
                run_id,
            ),
        )


def get_capture_action_profile(
    conn,
    *,
    app_type: str,
    task_type: str,
    field_names: tuple[str, ...] | list[str],
) -> dict[str, Any] | None:
    requested = {str(item) for item in field_names if str(item)}
    if not requested:
        return None
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM capture_action_profiles
            WHERE task_type = %s
              AND status = 'active'
              AND app_type IN (%s, 'unknown')
            ORDER BY
              CASE WHEN app_type = %s THEN 1 ELSE 0 END DESC,
              priority DESC,
              id ASC
            """,
            (task_type, app_type or "unknown", app_type or "unknown"),
        )
        rows = [_decode_capture_action_profile(row) for row in cursor.fetchall()]

    exact = [row for row in rows if set(row.get("field_names") or []) == requested]
    if exact:
        return exact[0]
    superset = [row for row in rows if requested.issubset(set(row.get("field_names") or []))]
    return superset[0] if superset else None


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
            json_dumps(item.source_locator),
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


def get_pending_task_submissions(conn, *, task_type: str, limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT *
        FROM task_submissions
        WHERE task_type = %s
          AND status IN ('pending', 'retry')
          AND attempts < max_attempts
        ORDER BY priority DESC, id ASC
    """
    params: list[Any] = [task_type]
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [_decode_submission(row) for row in rows]


def start_task_execution(conn, submission_id: int, *, worker_id: str = "crawler_app") -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, attempts, max_attempts, status
            FROM task_submissions
            WHERE id = %s
            FOR UPDATE
            """,
            (submission_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"task submission not found: {submission_id}")
        attempts = int(row["attempts"] or 0)
        cursor.execute(
            """
            SELECT COALESCE(MAX(attempt_no), 0) AS max_attempt_no
            FROM task_executions
            WHERE submission_id = %s
            """,
            (submission_id,),
        )
        max_attempt_no = int((cursor.fetchone() or {}).get("max_attempt_no") or 0)
        attempts = max(attempts, max_attempt_no)
        max_attempts = int(row["max_attempts"] or 0)
        if attempts >= max_attempts:
            raise ValueError(f"task submission exceeded attempts: {submission_id}")
        attempt_no = attempts + 1
        cursor.execute(
            """
            INSERT INTO task_executions (
                submission_id, attempt_no, status, started_at, heartbeat_at
            )
            VALUES (%s, %s, 'running', NOW(), NOW())
            """,
            (submission_id, attempt_no),
        )
        execution_id = int(cursor.lastrowid)
        cursor.execute(
            """
            UPDATE task_submissions
            SET status = 'running',
                attempts = %s,
                latest_execution_id = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (attempt_no, execution_id, submission_id),
        )
        return execution_id


def finish_task_execution(
    conn,
    *,
    submission_id: int,
    execution_id: int,
    status: str,
    result: dict[str, Any],
    metrics: dict[str, Any] | None = None,
    opened_url: str = "",
    screenshot_path: str | None = None,
    error: str | None = None,
) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE task_executions
            SET status = %s,
                opened_url = %s,
                metrics_json = %s,
                result_json = %s,
                screenshot_path = %s,
                error = %s,
                finished_at = NOW(),
                heartbeat_at = NOW()
            WHERE id = %s
            """,
            (
                status,
                opened_url or None,
                json_dumps(metrics or {}),
                json_dumps(result),
                screenshot_path,
                error,
                execution_id,
            ),
        )
        cursor.execute(
            """
            SELECT attempts, max_attempts
            FROM task_submissions
            WHERE id = %s
            """,
            (submission_id,),
        )
        submission = cursor.fetchone() or {}
        final_submission_status = _submission_status_after_execution(
            status,
            attempts=int(submission.get("attempts") or 0),
            max_attempts=int(submission.get("max_attempts") or 0),
        )
        cursor.execute(
            """
            UPDATE task_submissions
            SET status = %s,
                last_error = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (final_submission_status, error, submission_id),
        )
    return final_submission_status


def create_writeback_plans(
    conn,
    *,
    submission_id: int | None,
    execution_id: int | None,
    document_id: int,
    sheet_id: str,
    row_index: int,
    column_mapping_id: int,
    values: dict[str, Any],
    payload_extra: dict[str, Any] | None = None,
) -> int:
    if not values:
        return 0
    payload_extra = dict(payload_extra or {})
    rows = [
        (
            submission_id,
            execution_id,
            document_id,
            sheet_id,
            row_index,
            column_mapping_id,
            field_name,
            "" if value is None else str(value),
            json_dumps({"value": value, **payload_extra}),
        )
        for field_name, value in values.items()
    ]
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO writeback_plans (
                submission_id, execution_id, document_id, sheet_id, row_index,
                column_mapping_id, field_name, value_text, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        return int(cursor.rowcount)


def get_pending_writeback_plans(
    conn,
    *,
    limit: int | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            p.*,
            m.mapping_json,
            d.file_id,
            COALESCE(s.post_url, r.post_url) AS current_post_url
        FROM writeback_plans p
        JOIN column_mappings m ON m.id = p.column_mapping_id
        JOIN documents d ON d.id = p.document_id
        LEFT JOIN task_submissions s ON s.id = p.submission_id
        LEFT JOIN source_rows r ON r.id = s.source_row_id
        WHERE p.status = 'planned'
    """
    params: list[Any] = []
    if source:
        sql += " AND p.payload_json LIKE %s"
        params.append(f'%"{source}"%')
    sql += " ORDER BY p.id ASC"
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    for row in rows:
        row["mapping"] = json_loads(row.get("mapping_json"))
        row["payload"] = json_loads(row.get("payload_json"))
    return rows


def mark_writeback_plans(conn, plan_ids: list[int], *, status: str, error: str | None = None) -> None:
    if not plan_ids:
        return
    placeholders = ", ".join(["%s"] * len(plan_ids))
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE writeback_plans
            SET status = %s,
                error = %s,
                applied_at = CASE WHEN %s = 'success' THEN NOW() ELSE applied_at END
            WHERE id IN ({placeholders})
            """,
            [status, error, status, *plan_ids],
        )


def mark_corrections(conn, correction_ids: list[int], *, status: str) -> None:
    if not correction_ids:
        return
    placeholders = ", ".join(["%s"] * len(correction_ids))
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE corrections
            SET status = %s,
                applied_at = CASE WHEN %s = 'success' THEN NOW() ELSE applied_at END
            WHERE id IN ({placeholders})
            """,
            [status, status, *correction_ids],
        )


def _decode_submission(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["source_locator"] = json_loads(item.get("source_locator_json"))
    return item


def _decode_document_task_config(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["field_names"] = json_loads(item.get("field_names_json")) or []
    item["sheet_selector"] = json_loads(item.get("sheet_selector_json")) or {}
    return item


def _decode_document_trigger_config(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["sheet_selector"] = json_loads(item.get("sheet_selector_json")) or {}
    item["submit_policy"] = json_loads(item.get("submit_policy_json")) or {}
    return item


def _decode_document_trigger_binding(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["field_names"] = json_loads(item.get("field_names_json")) or []
    return item


def _decode_capture_action_profile(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["field_names"] = json_loads(item.get("field_names_json")) or []
    item["action_names"] = json_loads(item.get("action_names_json")) or []
    item["capture_config"] = json_loads(item.get("capture_config_json")) or {}
    return item


def _submission_status_after_execution(status: str, *, attempts: int, max_attempts: int) -> str:
    if status in {"success", "not_found", "skipped"}:
        return status
    if max_attempts <= 0:
        return "failed"
    if attempts < max_attempts:
        return "retry"
    return "failed"
