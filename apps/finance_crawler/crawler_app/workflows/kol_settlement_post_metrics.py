"""KOL settlement post metric collection from a database table."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date
from typing import Any

from apps.finance_crawler.crawler_app.documents.fields import ACCOUNT_NAME, ARTICLE_TITLE, COMMENT_COUNT, LIKE_COUNT, SCREENSHOT
from apps.finance_crawler.crawler_app.storage.db import get_conn
from apps.finance_crawler.crawler_app.tasks.handlers import get_task_handler
from apps.finance_crawler.crawler_app.tasks.submission import TaskSubmission, submit_task_submissions
from apps.finance_crawler.crawler_app.tasks.types import KOL_SETTLEMENT_POST_METRICS
from apps.finance_crawler.crawler_app.web.capture_files import capture_public_url
from apps.finance_crawler.crawler_app.workflows.execution import crawl_pending_tasks
from apps.finance_crawler.storage.db import log_task
from apps.finance_crawler.utils.link_source import resolve_source_app

SOURCE_TABLE = "kol_business_settlements"
SOURCE_ID_FIELD = "id"
SOURCE_DATE_FIELD = "settlement_date"
SOURCE_URL_FIELD = "post_url"
REQUESTED_FIELDS = (ACCOUNT_NAME, ARTICLE_TITLE, COMMENT_COUNT, LIKE_COUNT, SCREENSHOT)
RESULT_FIELD_MAP = {
    ACCOUNT_NAME: "ip_name",
    ARTICLE_TITLE: "article_title",
    COMMENT_COUNT: "comment_count",
    LIKE_COUNT: "like_count",
    SCREENSHOT: "screenshot_url",
}


def submit_kol_settlement_post_metric_tasks(
    *,
    target_date: date | None = None,
    limit: int | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    started = time.time()
    task_name = "kol_settlement_post_metrics_submit"
    conn = get_conn()
    try:
        rows = _list_pending_settlement_rows(conn, target_date=target_date, limit=limit)
        submissions = [_submission_from_settlement_row(row, max_attempts=max_attempts) for row in rows]
        submitted = submit_task_submissions(conn, submissions)
        conn.commit()
        summary = {
            "source_table": SOURCE_TABLE,
            "target_date": target_date.isoformat() if target_date else None,
            "source_rows": len(rows),
            "submitted": submitted,
            "task_type": KOL_SETTLEMENT_POST_METRICS,
        }
        log_task(task_name, "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        conn.rollback()
        log_task(task_name, "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def crawl_kol_settlement_post_metric_tasks(*, limit: int | None = None) -> dict[str, Any]:
    started = time.time()
    task_name = "kol_settlement_post_metrics_crawl"
    try:
        summary = crawl_pending_tasks(get_task_handler(KOL_SETTLEMENT_POST_METRICS), limit=limit)
        log_task(task_name, "success", json.dumps(_json_safe(summary), ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        log_task(task_name, "error", str(exc), time.time() - started)
        raise


def writeback_kol_settlement_post_metric_results(*, limit: int | None = None) -> dict[str, Any]:
    started = time.time()
    task_name = "kol_settlement_post_metrics_writeback"
    conn = get_conn()
    try:
        rows = _list_successful_settlement_executions(conn, limit=limit)
        updated = 0
        skipped = 0
        errors: list[str] = []
        for row in rows:
            try:
                values = _result_values_for_settlement(row)
                if not values:
                    skipped += 1
                    continue
                updated += _update_settlement_row(conn, row, values)
            except Exception as exc:  # keep later rows writable
                errors.append(f"submission={row.get('submission_id')}: {exc}")
        conn.commit()
        summary = {
            "candidates": len(rows),
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
        }
        log_task(task_name, "success" if not errors else "warning", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        conn.rollback()
        log_task(task_name, "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def run_kol_settlement_post_metrics(*, target_date: date | None = None, limit: int | None = None) -> dict[str, Any]:
    submitted = submit_kol_settlement_post_metric_tasks(target_date=target_date, limit=limit)
    crawled = crawl_kol_settlement_post_metric_tasks(limit=limit)
    written = writeback_kol_settlement_post_metric_results(limit=limit)
    return {"submitted": submitted, "crawled": crawled, "written": written}


def _list_pending_settlement_rows(conn, *, target_date: date | None, limit: int | None) -> list[dict[str, Any]]:
    sql = f"""
        SELECT
            {_identifier(SOURCE_ID_FIELD)} AS source_pk,
            {_identifier(SOURCE_DATE_FIELD)} AS settlement_date,
            {_identifier(SOURCE_URL_FIELD)} AS post_url
        FROM {_identifier(SOURCE_TABLE)}
        WHERE {_identifier(SOURCE_URL_FIELD)} IS NOT NULL
          AND TRIM({_identifier(SOURCE_URL_FIELD)}) <> ''
          AND {_identifier(SOURCE_DATE_FIELD)} IS NOT NULL
          AND (
                ip_name IS NULL OR ip_name = ''
             OR article_title IS NULL OR article_title = ''
             OR comment_count IS NULL
             OR like_count IS NULL
             OR screenshot_url IS NULL OR screenshot_url = ''
          )
    """
    params: list[Any] = []
    if target_date:
        sql += f" AND {_identifier(SOURCE_DATE_FIELD)} = %s"
        params.append(target_date)
    sql += f" ORDER BY {_identifier(SOURCE_DATE_FIELD)} DESC, {_identifier(SOURCE_ID_FIELD)} ASC"
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def _submission_from_settlement_row(row: dict[str, Any], *, max_attempts: int) -> TaskSubmission:
    source_pk = int(row["source_pk"])
    settlement_date = _date_text(row.get("settlement_date"))
    post_url = str(row.get("post_url") or "").strip()
    source_locator = {
        "source_type": "db_table",
        "source_table": SOURCE_TABLE,
        "source_pk": source_pk,
        "date_field": SOURCE_DATE_FIELD,
        "settlement_date": settlement_date,
        "url_field": SOURCE_URL_FIELD,
        "post_url": post_url,
        "unique_fields": [SOURCE_DATE_FIELD, SOURCE_URL_FIELD],
        "requested_fields": list(REQUESTED_FIELDS),
        "result_field_map": RESULT_FIELD_MAP,
    }
    return TaskSubmission(
        task_type=KOL_SETTLEMENT_POST_METRICS,
        document_id=0,
        sheet_id=SOURCE_TABLE,
        row_index=source_pk,
        app_type=resolve_source_app(None, post_url),
        post_url=post_url,
        account_name="",
        post_time="",
        source_locator=source_locator,
        dedupe_key=_dedupe_key(settlement_date=settlement_date, post_url=post_url),
        source_row_id=None,
        priority=0,
        max_attempts=max_attempts,
        created_by="kol_settlement_post_metrics_submit",
    )


def _dedupe_key(*, settlement_date: str, post_url: str) -> str:
    payload = json.dumps(
        {
            "source_table": SOURCE_TABLE,
            "settlement_date": settlement_date,
            "post_url": post_url,
            "task_type": KOL_SETTLEMENT_POST_METRICS,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _list_successful_settlement_executions(conn, *, limit: int | None) -> list[dict[str, Any]]:
    sql = """
        SELECT
            s.id AS submission_id,
            s.source_locator_json,
            s.post_url,
            e.id AS execution_id,
            e.result_json
        FROM task_submissions s
        JOIN task_executions e ON e.id = s.latest_execution_id
        WHERE s.task_type = %s
          AND s.status = 'success'
          AND e.status = 'success'
        ORDER BY s.id ASC
    """
    params: list[Any] = [KOL_SETTLEMENT_POST_METRICS]
    if limit and limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = list(cursor.fetchall())
    for row in rows:
        row["source_locator"] = _json_loads(row.get("source_locator_json"))
        row["result"] = _json_loads(row.get("result_json"))
    return rows


def _result_values_for_settlement(row: dict[str, Any]) -> dict[str, Any]:
    result = row.get("result") or {}
    field_values = _accepted_field_values(result.get("field_results"))
    values: dict[str, Any] = {}
    account_name = field_values.get(ACCOUNT_NAME) or result.get(ACCOUNT_NAME)
    if account_name:
        values["ip_name"] = account_name
    if field_values.get(ARTICLE_TITLE):
        values["article_title"] = field_values[ARTICLE_TITLE]
    if COMMENT_COUNT in field_values:
        values["comment_count"] = field_values[COMMENT_COUNT]
    if LIKE_COUNT in field_values:
        values["like_count"] = field_values[LIKE_COUNT]
    if field_values.get(SCREENSHOT):
        values["screenshot_url"] = capture_public_url(field_values[SCREENSHOT])
    return values


def _accepted_field_values(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, list):
        return {}
    values: dict[str, Any] = {}
    for item in payload:
        if not isinstance(item, dict) or not item.get("accepted"):
            continue
        field_name = str(item.get("field_name") or "")
        if field_name in REQUESTED_FIELDS and item.get("value") is not None:
            values[field_name] = item.get("value")
    return values


def _update_settlement_row(conn, row: dict[str, Any], values: dict[str, Any]) -> int:
    locator = row.get("source_locator") or {}
    source_pk = int(locator["source_pk"])
    settlement_date = locator["settlement_date"]
    post_url = str(locator["post_url"])
    assignments = ", ".join(f"{_identifier(column)} = %s" for column in values)
    params = [
        *values.values(),
        source_pk,
        settlement_date,
        post_url,
    ]
    sql = f"""
        UPDATE {_identifier(SOURCE_TABLE)}
        SET {assignments}
        WHERE {_identifier(SOURCE_ID_FIELD)} = %s
          AND {_identifier(SOURCE_DATE_FIELD)} = %s
          AND {_identifier(SOURCE_URL_FIELD)} = %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return int(cursor.rowcount)


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe SQL identifier: {value}")
    return f"`{value}`"


def _date_text(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()


def _json_loads(value: Any) -> Any:
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    return json.loads(str(value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value
