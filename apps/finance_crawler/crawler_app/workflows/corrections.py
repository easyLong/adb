"""Auditable manual corrections for v2 document rows."""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

from apps.finance_crawler.crawler_app.corrections import models as correction_models
from apps.finance_crawler.crawler_app.documents.fields import default_field_by_name
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.storage.db import get_conn
from apps.finance_crawler.crawler_app.writeback.executor import apply_pending_writebacks
from apps.finance_crawler.storage.db import log_task


def plan_document_correction(
    *,
    document_id: int,
    sheet_id: str,
    row_index: int,
    field_name: str,
    new_value: str,
    reason: str,
    operator_name: str = "cli",
) -> dict[str, Any]:
    started = time.time()
    task_name = "v2_correction_plan"
    conn = get_conn()
    try:
        summary = plan_document_correction_in_conn(
            conn,
            document_id=document_id,
            sheet_id=sheet_id,
            row_index=row_index,
            field_name=field_name,
            new_value=new_value,
            reason=reason,
            operator_name=operator_name,
        )
        conn.commit()
        log_task(task_name, "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        conn.rollback()
        log_task(task_name, "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def plan_document_correction_in_conn(
    conn,
    *,
    document_id: int,
    sheet_id: str,
    row_index: int,
    field_name: str,
    new_value: str,
    reason: str,
    operator_name: str = "cli",
) -> dict[str, Any]:
    cleaned_field = field_name.strip()
    _validate_correction_input(
        document_id=document_id,
        sheet_id=sheet_id,
        row_index=row_index,
        field_name=cleaned_field,
        new_value=new_value,
        reason=reason,
    )

    source_row = repository.get_source_row_by_position(
        conn,
        document_id=document_id,
        sheet_id=sheet_id,
        row_index=row_index,
    )
    if not source_row:
        raise ValueError(
            f"source row not found: document_id={document_id} sheet_id={sheet_id} row_index={row_index}"
        )

    return _plan_source_row_correction(
        conn,
        source_row=source_row,
        field_name=cleaned_field,
        new_value=new_value,
        reason=reason,
        operator_name=operator_name,
    )


def plan_configured_document_correction(
    *,
    config_key: str,
    field_name: str,
    new_value: str,
    reason: str,
    target_date: date | None = None,
    row_index: int | None = None,
    post_url: str | None = None,
    operator_name: str = "cli",
) -> dict[str, Any]:
    started = time.time()
    task_name = "v2_configured_correction_plan"
    conn = get_conn()
    try:
        summary = plan_configured_document_correction_in_conn(
            conn,
            config_key=config_key,
            field_name=field_name,
            new_value=new_value,
            reason=reason,
            target_date=target_date,
            row_index=row_index,
            post_url=post_url,
            operator_name=operator_name,
        )
        conn.commit()
        log_task(task_name, "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        conn.rollback()
        log_task(task_name, "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def plan_configured_document_correction_in_conn(
    conn,
    *,
    config_key: str,
    field_name: str,
    new_value: str,
    reason: str,
    target_date: date | None = None,
    row_index: int | None = None,
    post_url: str | None = None,
    operator_name: str = "cli",
) -> dict[str, Any]:
    cleaned_field = field_name.strip()
    _validate_configured_correction_input(
        config_key=config_key,
        field_name=cleaned_field,
        new_value=new_value,
        reason=reason,
        row_index=row_index,
        post_url=post_url,
    )
    config = repository.get_document_task_config(conn, config_key)
    if not config:
        raise ValueError(f"document task config not found: {config_key}")
    if config.get("status") != "active":
        raise ValueError(f"document task config is not active: {config_key}")

    source_row = _resolve_configured_source_row(
        conn,
        config=config,
        target_date=target_date,
        row_index=row_index,
        post_url=post_url,
    )
    return _plan_source_row_correction(
        conn,
        source_row=source_row,
        field_name=cleaned_field,
        new_value=new_value,
        reason=reason,
        operator_name=operator_name,
    )


def _plan_source_row_correction(
    conn,
    *,
    source_row: dict[str, Any],
    field_name: str,
    new_value: str,
    reason: str,
    operator_name: str = "cli",
) -> dict[str, Any]:
    document_id = int(source_row["document_id"])
    sheet_id = str(source_row["sheet_id"])
    row_index = int(source_row["row_index"])
    column_mapping_id = int(source_row.get("column_mapping_id") or 0)
    column_mapping = repository.get_column_mapping(conn, column_mapping_id)
    if not column_mapping:
        raise ValueError(f"column mapping not found: {column_mapping_id}")
    mapping = column_mapping.get("mapping") or {}
    if field_name not in mapping:
        raise ValueError(f"field not mapped in sheet header: {field_name}")

    row_values = source_row.get("row_values") or {}
    old_value = "" if row_values.get(field_name) is None else str(row_values.get(field_name, ""))
    correction_id = correction_models.insert_correction(
        conn,
        correction_models.CorrectionRequest(
            target_type="source_row",
            target_id=int(source_row["id"]),
            document_id=document_id,
            sheet_id=sheet_id,
            row_index=row_index,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            operator_name=operator_name or "cli",
        ),
        status="planned",
    )
    writeback_count = repository.create_writeback_plans(
        conn,
        submission_id=None,
        execution_id=None,
        document_id=document_id,
        sheet_id=sheet_id,
        row_index=row_index,
        column_mapping_id=column_mapping_id,
        values={field_name: new_value},
        payload_extra={"correction_id": correction_id, "source": "manual_correction"},
    )
    return {
        "correction_id": correction_id,
        "writeback_plans": writeback_count,
        "document_id": document_id,
        "sheet_id": sheet_id,
        "row_index": row_index,
        "field_name": field_name,
        "old_value": old_value,
        "new_value": new_value,
    }


def apply_pending_correction_writebacks(*, limit: int | None = None) -> dict[str, Any]:
    started = time.time()
    task_name = "v2_correction_writeback"
    conn = get_conn()
    try:
        summary = apply_pending_writebacks(conn, limit=limit, source="manual_correction")
        conn.commit()
        log_task(task_name, "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        conn.rollback()
        log_task(task_name, "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def plan_and_apply_document_correction(
    *,
    document_id: int,
    sheet_id: str,
    row_index: int,
    field_name: str,
    new_value: str,
    reason: str,
    operator_name: str = "cli",
) -> dict[str, Any]:
    planned = plan_document_correction(
        document_id=document_id,
        sheet_id=sheet_id,
        row_index=row_index,
        field_name=field_name,
        new_value=new_value,
        reason=reason,
        operator_name=operator_name,
    )
    written = apply_pending_correction_writebacks()
    return {"planned": planned, "written": written}


def plan_and_apply_configured_document_correction(
    *,
    config_key: str,
    field_name: str,
    new_value: str,
    reason: str,
    target_date: date | None = None,
    row_index: int | None = None,
    post_url: str | None = None,
    operator_name: str = "cli",
) -> dict[str, Any]:
    planned = plan_configured_document_correction(
        config_key=config_key,
        field_name=field_name,
        new_value=new_value,
        reason=reason,
        target_date=target_date,
        row_index=row_index,
        post_url=post_url,
        operator_name=operator_name,
    )
    written = apply_pending_correction_writebacks()
    return {"planned": planned, "written": written}


def _validate_correction_input(
    *,
    document_id: int,
    sheet_id: str,
    row_index: int,
    field_name: str,
    new_value: str,
    reason: str,
) -> None:
    if document_id <= 0:
        raise ValueError("document_id is required")
    if not sheet_id.strip():
        raise ValueError("sheet_id is required")
    if row_index <= 0:
        raise ValueError("row_index must be 1-based and greater than 0")
    if field_name not in default_field_by_name():
        raise ValueError(f"unknown field_name: {field_name}")
    if new_value is None:
        raise ValueError("new_value is required")
    if not reason.strip():
        raise ValueError("reason is required")


def _validate_configured_correction_input(
    *,
    config_key: str,
    field_name: str,
    new_value: str,
    reason: str,
    row_index: int | None = None,
    post_url: str | None = None,
) -> None:
    if not config_key.strip():
        raise ValueError("config_key is required")
    if not row_index and not post_url:
        raise ValueError("row_index or post_url is required")
    if row_index is not None and row_index <= 0:
        raise ValueError("row_index must be 1-based and greater than 0")
    if field_name not in default_field_by_name():
        raise ValueError(f"unknown field_name: {field_name}")
    if new_value is None:
        raise ValueError("new_value is required")
    if not reason.strip():
        raise ValueError("reason is required")


def _resolve_configured_source_row(
    conn,
    *,
    config: dict[str, Any],
    target_date: date | None = None,
    row_index: int | None = None,
    post_url: str | None = None,
) -> dict[str, Any]:
    sheet_id = _configured_sheet_id(config)
    rows = repository.find_source_rows_for_correction(
        conn,
        source_type=str(config.get("source_type") or "tencent_docs"),
        file_id=str(config.get("file_id") or ""),
        sheet_id=sheet_id,
        row_index=row_index,
        post_url=post_url.strip() if post_url else None,
        business_date=target_date,
    )
    if not rows:
        raise ValueError(
            "source row not found for config="
            f"{config.get('config_key')} row_index={row_index or ''} post_url={post_url or ''}"
        )
    if len(rows) > 1:
        raise ValueError(
            "correction target is ambiguous: "
            f"config={config.get('config_key')} matches={len(rows)}; add -ReportDate or row/link"
        )
    return rows[0]


def _configured_sheet_id(config: dict[str, Any]) -> str | None:
    selector = config.get("sheet_selector") or {}
    mode = str(selector.get("mode") or "").strip()
    if mode == "fixed_sheet":
        return str(selector.get("sheet_id") or selector.get("fallback_sheet_id") or config.get("sheet_id") or "")
    if mode == "linked_tab":
        return str(selector.get("fallback_sheet_id") or config.get("sheet_id") or "")
    return None
