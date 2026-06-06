"""Generic v2 document-task workflows."""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

from apps.finance_crawler.crawler_app.documents.fields import (
    ACCOUNT_NAME,
    CHECK_RESULT,
    COMMENT_COUNT,
    default_field_by_name,
    READ_COUNT,
    REMARK,
    SCREENSHOT,
)
from apps.finance_crawler.crawler_app.documents.intake import (
    submit_document_tasks_from_tencent_doc,
    summary_to_dict,
)
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.storage.db import get_conn
from apps.finance_crawler.crawler_app.tasks.handlers import get_task_handler
from apps.finance_crawler.crawler_app.tasks.types import DETAIL, INITIAL_CHECK, READ_COUNT as READ_COUNT_TASK
from apps.finance_crawler.crawler_app.workflows.execution import crawl_pending_tasks
from apps.finance_crawler.crawler_app.writeback.executor import apply_pending_writebacks
from apps.finance_crawler.integrations.tencent_docs.client import parse_doc_url_info
from apps.finance_crawler.storage.db import log_task

SHEET_SELECTOR_MODES = {
    "date_sheet",
    "fixed_sheet",
    "linked_tab",
    "sheet_title",
    "sheet_title_contains",
    "sheet_group",
}

TASK_ALLOWED_WRITEBACK_FIELDS = {
    INITIAL_CHECK: {ACCOUNT_NAME, CHECK_RESULT, REMARK},
    DETAIL: {ACCOUNT_NAME, READ_COUNT, COMMENT_COUNT, SCREENSHOT, REMARK},
    READ_COUNT_TASK: {READ_COUNT, REMARK},
}


def submit_document_tasks(
    task_type: str,
    *,
    doc_url: str | None = None,
    target_date: date | None = None,
    limit: int | None = None,
    sheet_selector: dict[str, object] | None = None,
    requested_fields: tuple[str, ...] | list[str] | None = None,
    priority: int = 0,
    max_attempts: int = 3,
) -> dict[str, Any]:
    started = time.time()
    conn = get_conn()
    task_name = f"v2_{task_type}_submit"
    try:
        summary = submit_document_tasks_from_tencent_doc(
            conn,
            task_type=task_type,
            doc_url=doc_url,
            target_date=target_date,
            limit=limit,
            sheet_selector=sheet_selector,
            requested_fields=tuple(requested_fields or default_fields_for_task(task_type)),
            priority=priority,
            max_attempts=max_attempts,
            created_by=task_name,
        )
        conn.commit()
        output = summary_to_dict(summary)
        log_task(task_name, "success", json.dumps(output, ensure_ascii=False), time.time() - started)
        return output
    except Exception as exc:
        conn.rollback()
        log_task(task_name, "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def crawl_pending_document_tasks(task_type: str, *, limit: int | None = None) -> dict[str, Any]:
    started = time.time()
    task_name = f"v2_{task_type}_crawl"
    try:
        summary = crawl_pending_tasks(get_task_handler(task_type), limit=limit)
        log_task(task_name, "success", json.dumps(_log_safe(summary), ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        log_task(task_name, "error", str(exc), time.time() - started)
        raise


def writeback_document_task_results(task_type: str, *, limit: int | None = None) -> dict[str, Any]:
    started = time.time()
    task_name = f"v2_{task_type}_writeback"
    conn = get_conn()
    try:
        summary = apply_pending_writebacks(conn, limit=limit)
        conn.commit()
        log_task(task_name, "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        conn.rollback()
        log_task(task_name, "error", str(exc), time.time() - started)
        raise
    finally:
        conn.close()


def run_document_task_workflow(
    task_type: str,
    *,
    doc_url: str | None = None,
    target_date: date | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    submitted = submit_document_tasks(task_type, doc_url=doc_url, target_date=target_date, limit=limit)
    crawled = crawl_pending_document_tasks(task_type, limit=limit)
    written = writeback_document_task_results(task_type)
    return {"submitted": submitted, "crawled": crawled, "written": written}


def upsert_document_task_config(
    *,
    config_key: str,
    doc_url: str,
    task_type: str,
    field_names: tuple[str, ...] | list[str] | None = None,
    sheet_selector: dict[str, object] | None = None,
    status: str = "active",
    priority: int = 0,
    max_attempts: int = 3,
    description: str | None = None,
    updated_by: str = "cli",
) -> dict[str, Any]:
    doc = parse_doc_url_info(doc_url)
    fields = list(parse_field_names(",".join(field_names)) if field_names else default_fields_for_task(task_type))
    selector = sheet_selector or {"mode": "date_sheet", "fallback_sheet_id": doc.sheet_id or None}
    selector = _normalize_selector(selector, fallback_sheet_id=doc.sheet_id)
    problems = validate_document_task_config_payload(
        task_type=task_type,
        field_names=tuple(fields),
        sheet_selector=selector,
        status=status,
    )
    if problems:
        raise ValueError("invalid document task config: " + "; ".join(problems))
    conn = get_conn()
    try:
        repository.upsert_document_task_config(
            conn,
            config_key=config_key,
            source_type="tencent_docs",
            doc_url=doc.base_url,
            file_id=doc.file_id,
            sheet_id=doc.sheet_id or None,
            task_type=task_type,
            field_names=fields,
            sheet_selector=selector,
            status=status,
            priority=priority,
            max_attempts=max_attempts,
            description=description,
            updated_by=updated_by,
        )
        conn.commit()
        config = repository.get_document_task_config(conn, config_key) or {}
        return _config_summary(config)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_document_task_configs(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        return [
            _config_summary(item)
            for item in repository.list_document_task_configs(conn, include_disabled=include_disabled)
        ]
    finally:
        conn.close()


def check_document_task_config(config_key: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        config = repository.get_document_task_config(conn, config_key)
    finally:
        conn.close()
    if not config:
        raise ValueError(f"document task config not found: {config_key}")
    problems = validate_document_task_config(config)
    return {
        "ok": not problems,
        "problems": list(problems),
        "config": _config_summary(config),
    }


def submit_configured_document_tasks(
    config_key: str,
    *,
    target_date: date | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    config = _load_active_config(config_key)
    problems = validate_document_task_config(config)
    if problems:
        raise ValueError("invalid document task config: " + "; ".join(problems))
    task_type = str(config["task_type"])
    resolved_target_date = target_date or _configured_target_date(config)
    return submit_document_tasks(
        task_type,
        doc_url=_configured_doc_url_for_run(config, target_date=resolved_target_date),
        target_date=resolved_target_date,
        limit=limit,
        sheet_selector=config.get("sheet_selector") or {},
        requested_fields=tuple(config.get("field_names") or default_fields_for_task(task_type)),
        priority=int(config.get("priority") or 0),
        max_attempts=int(config.get("max_attempts") or 3),
    )


def run_configured_document_task_workflow(
    config_key: str,
    *,
    target_date: date | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    config = _load_active_config(config_key)
    task_type = str(config["task_type"])
    submitted = submit_configured_document_tasks(config_key, target_date=target_date, limit=limit)
    crawled = crawl_pending_document_tasks(task_type, limit=limit)
    written = writeback_document_task_results(task_type)
    return {"config": _config_summary(config), "submitted": submitted, "crawled": crawled, "written": written}


def default_fields_for_task(task_type: str) -> tuple[str, ...]:
    if task_type == INITIAL_CHECK:
        return (ACCOUNT_NAME,)
    if task_type == DETAIL:
        return (ACCOUNT_NAME, READ_COUNT, SCREENSHOT)
    if task_type == READ_COUNT_TASK:
        return (READ_COUNT,)
    return ()


def parse_field_names(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    fields = []
    seen = set()
    for item in value.split(","):
        field = item.strip()
        if field and field not in seen:
            fields.append(field)
            seen.add(field)
    return tuple(fields)


def build_sheet_selector(
    *,
    mode: str | None = None,
    sheet_id: str | None = None,
    sheet_title: str | None = None,
    sheet_keyword: str | None = None,
    sheet_ids: str | None = None,
) -> dict[str, object]:
    selected_mode = (mode or "date_sheet").strip()
    selector: dict[str, object] = {"mode": selected_mode}
    if sheet_id:
        selector["sheet_id"] = sheet_id.strip()
        selector["fallback_sheet_id"] = sheet_id.strip()
    if sheet_title:
        selector["title"] = sheet_title.strip()
    if sheet_keyword:
        selector["keyword"] = sheet_keyword.strip()
    if sheet_ids:
        selector["sheet_ids"] = [item.strip() for item in sheet_ids.split(",") if item.strip()]
    return selector


def validate_document_task_config(config: dict[str, Any]) -> tuple[str, ...]:
    return validate_document_task_config_payload(
        task_type=str(config.get("task_type") or ""),
        field_names=tuple(config.get("field_names") or ()),
        sheet_selector=config.get("sheet_selector") or {},
        status=str(config.get("status") or ""),
    )


def validate_document_task_config_payload(
    *,
    task_type: str,
    field_names: tuple[str, ...] | list[str] | None,
    sheet_selector: dict[str, object] | None,
    status: str = "active",
) -> tuple[str, ...]:
    problems: list[str] = []
    cleaned_task_type = str(task_type or "").strip()
    if not cleaned_task_type:
        problems.append("task_type is required")
    else:
        try:
            get_task_handler(cleaned_task_type)
        except ValueError:
            problems.append(f"unsupported task_type: {cleaned_task_type}")

    known_fields = default_field_by_name()
    cleaned_fields = tuple(str(field).strip() for field in field_names or () if str(field).strip())
    if not cleaned_fields:
        problems.append("field_names is required")
    unknown_fields = [field for field in cleaned_fields if field not in known_fields]
    if unknown_fields:
        problems.append("unknown field_names: " + ",".join(unknown_fields))
    duplicates = _duplicates(cleaned_fields)
    if duplicates:
        problems.append("duplicate field_names: " + ",".join(duplicates))

    allowed_fields = TASK_ALLOWED_WRITEBACK_FIELDS.get(cleaned_task_type)
    if allowed_fields is not None:
        unsupported_fields = [field for field in cleaned_fields if field in known_fields and field not in allowed_fields]
        if unsupported_fields:
            problems.append(
                f"field_names not supported by task_type {cleaned_task_type}: "
                + ",".join(unsupported_fields)
            )

    if not isinstance(sheet_selector, dict):
        problems.append("sheet_selector must be an object")
    else:
        problems.extend(_validate_sheet_selector(sheet_selector))

    cleaned_status = str(status or "").strip()
    if cleaned_status not in {"active", "disabled"}:
        problems.append(f"unsupported status: {cleaned_status}")
    return tuple(problems)


def _log_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _log_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_log_safe(item) for item in value]
    return str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value


def _load_active_config(config_key: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        config = repository.get_document_task_config(conn, config_key)
    finally:
        conn.close()
    if not config:
        raise ValueError(f"document task config not found: {config_key}")
    if config.get("status") != "active":
        raise ValueError(f"document task config is not active: {config_key}")
    return config


def _configured_target_date(config: dict[str, Any]) -> date | None:
    selector = config.get("sheet_selector") or {}
    raw = selector.get("target_date")
    if not raw:
        return None
    return date.fromisoformat(str(raw))


def _configured_doc_url_for_run(config: dict[str, Any], *, target_date: date | None) -> str:
    doc_url = str(config["doc_url"])
    if target_date is not None:
        return doc_url
    sheet_id = str(config.get("sheet_id") or "")
    if not sheet_id:
        return doc_url
    separator = "&" if "?" in doc_url else "?"
    return f"{doc_url}{separator}tab={sheet_id}"


def _config_summary(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": config.get("id"),
        "config_key": config.get("config_key"),
        "source_type": config.get("source_type"),
        "doc_url": config.get("doc_url"),
        "file_id": config.get("file_id"),
        "sheet_id": config.get("sheet_id"),
        "task_type": config.get("task_type"),
        "field_names": list(config.get("field_names") or []),
        "sheet_selector": config.get("sheet_selector") or {},
        "status": config.get("status"),
        "priority": config.get("priority"),
        "max_attempts": config.get("max_attempts"),
        "description": config.get("description"),
    }


def _normalize_selector(selector: dict[str, object], *, fallback_sheet_id: str) -> dict[str, object]:
    normalized = dict(selector)
    if fallback_sheet_id and not normalized.get("fallback_sheet_id"):
        normalized["fallback_sheet_id"] = fallback_sheet_id
    if normalized.get("mode") == "fixed_sheet" and fallback_sheet_id and not normalized.get("sheet_id"):
        normalized["sheet_id"] = fallback_sheet_id
    return normalized


def _validate_sheet_selector(selector: dict[str, object]) -> list[str]:
    problems: list[str] = []
    mode = str(selector.get("mode") or "date_sheet").strip()
    if mode not in SHEET_SELECTOR_MODES:
        return [f"unsupported sheet selector mode: {mode}"]
    if mode == "fixed_sheet" and not (selector.get("sheet_id") or selector.get("fallback_sheet_id")):
        problems.append("fixed_sheet requires sheet_id")
    if mode == "linked_tab" and not selector.get("fallback_sheet_id"):
        problems.append("linked_tab requires a configured URL with tab=...")
    if mode == "sheet_title" and not str(selector.get("title") or "").strip():
        problems.append("sheet_title requires title")
    if mode == "sheet_title_contains" and not str(selector.get("keyword") or "").strip():
        problems.append("sheet_title_contains requires keyword")
    if mode == "sheet_group":
        sheet_ids = selector.get("sheet_ids")
        if not isinstance(sheet_ids, list) or not [item for item in sheet_ids if str(item).strip()]:
            problems.append("sheet_group requires sheet_ids")
    return problems


def _duplicates(values: tuple[str, ...]) -> list[str]:
    seen = set()
    duplicates = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates
