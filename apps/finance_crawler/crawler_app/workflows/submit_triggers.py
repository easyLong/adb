"""Submit-worker entrypoints for document trigger configs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from apps.finance_crawler.crawler_app.documents.column_resolver import resolve_header
from apps.finance_crawler.crawler_app.documents.fields import default_field_by_name
from apps.finance_crawler.crawler_app.documents.rows import DocumentSourceRow, extract_source_rows
from apps.finance_crawler.crawler_app.documents.sheet_selector import select_sheets
from apps.finance_crawler.crawler_app.documents.sources import TencentDocsSource
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.storage.db import get_conn
from apps.finance_crawler.crawler_app.tasks.handlers import get_task_handler
from apps.finance_crawler.crawler_app.tasks.submission import build_task_submission
from apps.finance_crawler.crawler_app.tasks.types import INITIAL_CHECK
from apps.finance_crawler.crawler_app.workflows.document_tasks import (
    build_sheet_selector,
    default_fields_for_task,
    parse_field_names,
    validate_document_task_config_payload,
)
from apps.finance_crawler.integrations.tencent_docs import client as tencent_docs_client
from apps.finance_crawler.integrations.tencent_docs.client import SheetInfo, parse_doc_url_info
from apps.finance_crawler.utils.link_source import resolve_source_app


@dataclass(frozen=True, slots=True)
class TriggerBinding:
    task_type: str
    field_names: tuple[str, ...]
    priority: int = 0
    max_attempts: int = 3
    binding_id: int | None = None


def upsert_document_trigger(
    *,
    config_key: str,
    doc_url: str,
    sheet_selector: dict[str, object] | None = None,
    submit_policy: dict[str, object] | None = None,
    scan_interval_seconds: int = 300,
    status: str = "active",
    description: str | None = None,
    updated_by: str = "cli",
) -> dict[str, Any]:
    doc = parse_doc_url_info(doc_url)
    selector = sheet_selector or {"mode": "linked_tab", "fallback_sheet_id": doc.sheet_id or None}
    policy = submit_policy or {}
    conn = get_conn()
    try:
        existing = repository.get_document_trigger_config(conn, config_key)
        if existing:
            bindings = repository.get_document_trigger_bindings(conn, int(existing["id"]))
            for binding in bindings:
                _validate_trigger_binding_policy(
                    selector=selector,
                    submit_policy=policy,
                    task_type=str(binding.get("task_type") or ""),
                )
        config_id = repository.upsert_document_trigger_config(
            conn,
            config_key=config_key,
            source_type="tencent_docs",
            doc_url=doc.base_url,
            file_id=doc.file_id,
            sheet_selector=selector,
            submit_policy=policy,
            scan_interval_seconds=scan_interval_seconds,
            status=status,
            description=description,
            updated_by=updated_by,
        )
        conn.commit()
        config = repository.get_document_trigger_config_by_id(conn, config_id) or {}
        return _trigger_summary(config, repository.get_document_trigger_bindings(conn, config_id, include_disabled=True))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_document_trigger_binding(
    *,
    config_key: str,
    task_type: str,
    field_names: tuple[str, ...] | list[str] | None = None,
    status: str = "active",
    priority: int = 0,
    max_attempts: int = 3,
    description: str | None = None,
) -> dict[str, Any]:
    fields = tuple(field_names or default_fields_for_task(task_type))
    problems = validate_document_task_config_payload(
        task_type=task_type,
        field_names=fields,
        sheet_selector={"mode": "linked_tab", "fallback_sheet_id": "configured"},
        status=status,
    )
    if problems:
        raise ValueError("invalid trigger binding: " + "; ".join(problems))
    conn = get_conn()
    try:
        config = repository.get_document_trigger_config(conn, config_key)
        if not config:
            raise ValueError(f"document trigger config not found: {config_key}")
        _validate_trigger_binding_policy(
            selector=config.get("sheet_selector") or {},
            submit_policy=config.get("submit_policy") or {},
            task_type=task_type,
        )
        repository.upsert_document_trigger_binding(
            conn,
            config_id=int(config["id"]),
            task_type=task_type,
            field_names=fields,
            status=status,
            priority=priority,
            max_attempts=max_attempts,
            description=description,
        )
        conn.commit()
        bindings = repository.get_document_trigger_bindings(conn, int(config["id"]), include_disabled=True)
        return _trigger_summary(config, bindings)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_document_triggers(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        output = []
        for config in repository.list_document_trigger_configs(conn, include_disabled=include_disabled):
            bindings = repository.get_document_trigger_bindings(
                conn,
                int(config["id"]),
                include_disabled=include_disabled,
            )
            output.append(_trigger_summary(config, bindings))
        return output
    finally:
        conn.close()


def submit_document_trigger_config(
    config_key: str,
    *,
    target_date: date | None = None,
    trigger_type: str = "manual",
    limit: int | None = None,
) -> dict[str, Any]:
    conn = get_conn()
    try:
        config = repository.get_document_trigger_config(conn, config_key)
        if not config:
            raise ValueError(f"document trigger config not found: {config_key}")
        summary = submit_document_trigger_config_in_conn(
            conn,
            config,
            target_date=target_date,
            trigger_type=trigger_type,
            limit=limit,
        )
        conn.commit()
        return summary
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def submit_due_document_triggers(
    *,
    limit: int | None = None,
    worker_id: str = "submit_worker",
    lock_seconds: int = 300,
) -> dict[str, Any]:
    conn = get_conn()
    results: list[dict[str, Any]] = []
    try:
        configs = repository.get_due_document_trigger_configs(conn, limit=limit)
        for config in configs:
            config_id = int(config["id"])
            if not repository.claim_document_trigger_config(
                conn,
                config_id=config_id,
                worker_id=worker_id,
                lock_seconds=lock_seconds,
            ):
                continue
            conn.commit()
            try:
                refreshed = repository.get_document_trigger_config_by_id(conn, config_id) or config
                result = submit_document_trigger_config_in_conn(
                    conn,
                    refreshed,
                    target_date=None,
                    trigger_type="scheduled",
                    limit=None,
                )
                repository.finish_document_trigger_scan(conn, config_id=config_id, status="idle", error=None)
                conn.commit()
                results.append(result)
            except Exception as exc:
                conn.rollback()
                repository.finish_document_trigger_scan(conn, config_id=config_id, status="error", error=str(exc))
                conn.commit()
                results.append({"config_key": config.get("config_key"), "status": "error", "error": str(exc)})
        return {"configs": len(configs), "submitted": len(results), "results": results}
    finally:
        conn.close()


def submit_document_trigger_config_in_conn(
    conn,
    config: dict[str, Any],
    *,
    target_date: date | None = None,
    trigger_type: str = "manual",
    limit: int | None = None,
) -> dict[str, Any]:
    bindings = repository.get_document_trigger_bindings(conn, int(config["id"]))
    if not bindings:
        raise ValueError(f"document trigger has no active bindings: {config.get('config_key')}")
    trigger_bindings = [_binding_from_record(item) for item in bindings]
    effective_target_date = _effective_target_date(config, target_date)
    run_id = repository.start_submit_run(conn, config_id=int(config["id"]), trigger_type=trigger_type)
    try:
        skipped = _skip_summary_for_trigger(config, trigger_bindings, effective_target_date)
        if skipped:
            repository.finish_submit_run(
                conn,
                run_id=run_id,
                status="skipped",
                sheet_id="",
                sheet_title="",
                source_rows=0,
                submitted_tasks=0,
                skipped_rows=0,
                summary=skipped,
            )
            return skipped
        summary = _submit_bindings_from_tencent_doc(
            conn,
            config=config,
            bindings=trigger_bindings,
            target_date=effective_target_date,
            limit=limit,
            submit_run_id=run_id,
            created_by=f"submit_trigger:{config.get('config_key')}",
        )
        repository.finish_submit_run(
            conn,
            run_id=run_id,
            status=str(summary.get("status") or "success"),
            sheet_id=summary["sheet_id"],
            sheet_title=summary["sheet_title"],
            source_rows=int(summary["source_rows"]),
            submitted_tasks=int(summary["submitted_tasks"]),
            skipped_rows=int(summary["skipped_rows"]),
            summary=summary,
        )
        return summary
    except Exception as exc:
        repository.finish_submit_run(conn, run_id=run_id, status="error", summary={}, error=str(exc))
        raise


def _submit_bindings_from_tencent_doc(
    conn,
    *,
    config: dict[str, Any],
    bindings: list[TriggerBinding],
    target_date: date | None,
    limit: int | None,
    submit_run_id: int,
    created_by: str,
) -> dict[str, Any]:
    if _should_submit_all_date_sheets(config, target_date):
        sheets = _select_trigger_sheets(config, target_date)
        summaries = [
            _submit_bindings_from_tencent_doc_sheet(
                conn,
                config=config,
                bindings=bindings,
                target_date=target_date,
                limit=limit,
                submit_run_id=submit_run_id,
                created_by=created_by,
                sheet=sheet,
            )
            for sheet in sheets
        ]
        return _aggregate_sheet_submit_summaries(config, target_date, summaries)
    return _submit_bindings_from_tencent_doc_sheet(
        conn,
        config=config,
        bindings=bindings,
        target_date=target_date,
        limit=limit,
        submit_run_id=submit_run_id,
        created_by=created_by,
        sheet=None,
    )


def _submit_bindings_from_tencent_doc_sheet(
    conn,
    *,
    config: dict[str, Any],
    bindings: list[TriggerBinding],
    target_date: date | None,
    limit: int | None,
    submit_run_id: int,
    created_by: str,
    sheet: SheetInfo | None,
) -> dict[str, Any]:
    sheet_selector = config.get("sheet_selector") or {}
    if sheet is not None:
        sheet_selector = {"mode": "fixed_sheet", "sheet_id": sheet.sheet_id}
    snapshot = TencentDocsSource(doc_url=str(config["doc_url"])).load_sheet(
        target_date=target_date,
        sheet_selector=sheet_selector,
    )
    header = snapshot.rows[0] if snapshot.rows else []
    mapping = resolve_header(header)
    if not mapping.ok:
        return {
            "config_key": config.get("config_key"),
            "status": "invalid_header",
            "sheet_id": snapshot.sheet_id,
            "sheet_title": snapshot.sheet_title,
            "source_rows": 0,
            "unique_urls": 0,
            "submitted_tasks": 0,
            "skipped_rows": 0,
            "problems": list(mapping.problems),
        }

    source_rows = extract_source_rows(
        snapshot.rows,
        mapping.columns,
        start_row=snapshot.start_row,
        data_start_offset=1,
        business_date=snapshot.business_date,
    )
    if limit and limit > 0:
        source_rows = source_rows[:limit]
    canonical_rows, duplicate_rows = _canonical_rows_by_url(source_rows)

    document_id = repository.upsert_document(
        conn,
        source_type=snapshot.source_type,
        doc_url=snapshot.doc_url,
        file_id=snapshot.file_id,
        title=snapshot.title,
    )
    repository.upsert_document_sheet(
        conn,
        document_id=document_id,
        sheet_id=snapshot.sheet_id,
        sheet_title=snapshot.sheet_title,
        business_date=snapshot.business_date,
        header_row_index=snapshot.start_row,
    )
    column_mapping_id = repository.upsert_column_mapping(
        conn,
        document_id=document_id,
        sheet_id=snapshot.sheet_id,
        header_row_index=snapshot.start_row,
        mapping=mapping,
    )
    source_row_ids = repository.upsert_source_rows(
        conn,
        document_id=document_id,
        sheet_id=snapshot.sheet_id,
        column_mapping_id=column_mapping_id,
        rows=source_rows,
    )

    submitted_tasks = 0
    by_task: dict[str, int] = {}
    for binding in bindings:
        submissions = [
            build_task_submission(
                row,
                document_id=document_id,
                sheet_id=snapshot.sheet_id,
                task_type=binding.task_type,
                app_type=resolve_source_app(None, row.post_url),
                source_row_id=source_row_ids.get(row.row_index),
                source_locator_extra={
                    "file_id": snapshot.file_id,
                    "sheet_id": snapshot.sheet_id,
                    "sheet_title": snapshot.sheet_title,
                    "column_mapping_id": column_mapping_id,
                    "requested_fields": list(binding.field_names),
                    "submit_run_id": submit_run_id,
                    "trigger_config_id": int(config["id"]),
                    "trigger_binding_id": binding.binding_id,
                },
                priority=binding.priority,
                max_attempts=binding.max_attempts,
                created_by=created_by,
            )
            for row in canonical_rows
        ]
        repository.submit_task_submissions(conn, submissions)
        count = len(submissions)
        by_task[binding.task_type] = count
        submitted_tasks += count

    return {
        "config_key": config.get("config_key"),
        "status": "success",
        "sheet_id": snapshot.sheet_id,
        "sheet_title": snapshot.sheet_title,
        "business_date": snapshot.business_date.isoformat() if snapshot.business_date else None,
        "column_mapping_id": column_mapping_id,
        "source_rows": len(source_rows),
        "unique_urls": len(canonical_rows),
        "duplicate_rows": len(duplicate_rows),
        "submitted_tasks": submitted_tasks,
        "skipped_rows": len(duplicate_rows),
        "tasks": by_task,
        "problems": [],
    }


def _should_submit_all_date_sheets(config: dict[str, Any], target_date: date | None) -> bool:
    selector = config.get("sheet_selector") or {}
    return target_date is not None and str(selector.get("mode") or "").strip() == "date_sheet"


def _select_trigger_sheets(config: dict[str, Any], target_date: date | None) -> list[SheetInfo]:
    doc_info = parse_doc_url_info(str(config["doc_url"]))
    base_doc = tencent_docs_client.DocInfo(doc_info.file_id, doc_info.sheet_id)
    sheets = tencent_docs_client.fetch_file_sheets(base_doc.file_id)
    return select_sheets(
        base_doc=base_doc,
        sheets=sheets,
        selector=config.get("sheet_selector") or {},
        target_date=target_date,
    )


def _aggregate_sheet_submit_summaries(
    config: dict[str, Any],
    target_date: date | None,
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    tasks: dict[str, int] = {}
    problems: list[str] = []
    successful = [item for item in summaries if item.get("status") == "success"]
    for summary in summaries:
        for task_type, count in (summary.get("tasks") or {}).items():
            tasks[str(task_type)] = tasks.get(str(task_type), 0) + int(count or 0)
        problems.extend(str(item) for item in summary.get("problems") or [])
    if not summaries:
        status = "empty"
    elif len(successful) == len(summaries):
        status = "success"
    elif successful:
        status = "partial"
    else:
        status = str(summaries[0].get("status") or "error")
    sheet_count = len(summaries)
    target_date_text = target_date.isoformat() if target_date else None
    return {
        "config_key": config.get("config_key"),
        "status": status,
        "sheet_id": summaries[0]["sheet_id"] if sheet_count == 1 else f"multi:{sheet_count}",
        "sheet_title": summaries[0]["sheet_title"] if sheet_count == 1 else f"{target_date_text or 'selected'} ({sheet_count} sheets)",
        "business_date": target_date_text,
        "source_rows": sum(int(item.get("source_rows") or 0) for item in summaries),
        "unique_urls": sum(int(item.get("unique_urls") or 0) for item in summaries),
        "duplicate_rows": sum(int(item.get("duplicate_rows") or 0) for item in summaries),
        "submitted_tasks": sum(int(item.get("submitted_tasks") or 0) for item in summaries),
        "skipped_rows": sum(int(item.get("skipped_rows") or 0) for item in summaries),
        "tasks": tasks,
        "sheet_count": sheet_count,
        "sheets": summaries,
        "problems": problems,
    }


def _canonical_rows_by_url(rows: list[DocumentSourceRow]) -> tuple[list[DocumentSourceRow], list[DocumentSourceRow]]:
    seen: set[str] = set()
    canonical: list[DocumentSourceRow] = []
    duplicates: list[DocumentSourceRow] = []
    for row in rows:
        key = row.post_url.strip()
        if key in seen:
            duplicates.append(row)
            continue
        seen.add(key)
        canonical.append(row)
    return canonical, duplicates


def _binding_from_record(row: dict[str, Any]) -> TriggerBinding:
    task_type = str(row["task_type"])
    field_names = tuple(str(item) for item in (row.get("field_names") or default_fields_for_task(task_type)) if str(item))
    _validate_binding(task_type, field_names)
    return TriggerBinding(
        task_type=task_type,
        field_names=field_names,
        priority=int(row.get("priority") or 0),
        max_attempts=int(row.get("max_attempts") or 3),
        binding_id=int(row["id"]),
    )


def _effective_target_date(config: dict[str, Any], target_date: date | None) -> date | None:
    if target_date is not None:
        return target_date
    selector = config.get("sheet_selector") or {}
    if str(selector.get("mode") or "").strip() == "date_sheet":
        submit_policy = config.get("submit_policy") or {}
        offset_days = int(submit_policy.get("target_date_offset_days") or 0)
        return date.today() + timedelta(days=offset_days)
    return None


def _skip_summary_for_trigger(
    config: dict[str, Any],
    bindings: list[TriggerBinding],
    target_date: date | None,
) -> dict[str, Any] | None:
    if target_date is None:
        return None
    selector = config.get("sheet_selector") or {}
    if str(selector.get("mode") or "").strip() != "date_sheet":
        return None
    if not bindings or any(binding.task_type != INITIAL_CHECK for binding in bindings):
        return None
    if target_date.weekday() < 5:
        return None
    return {
        "config_key": config.get("config_key"),
        "status": "skipped",
        "skip_reason": "initial_check_weekend",
        "target_date": target_date.isoformat(),
        "sheet_id": "",
        "sheet_title": "",
        "source_rows": 0,
        "unique_urls": 0,
        "duplicate_rows": 0,
        "submitted_tasks": 0,
        "skipped_rows": 0,
        "tasks": {},
        "problems": [],
    }


def _validate_binding(task_type: str, field_names: tuple[str, ...]) -> None:
    get_task_handler(task_type)
    known = default_field_by_name()
    unknown = [field for field in field_names if field not in known]
    if unknown:
        raise ValueError("unknown trigger binding fields: " + ",".join(unknown))


def _validate_trigger_binding_policy(
    *,
    selector: dict[str, Any],
    submit_policy: dict[str, Any],
    task_type: str,
) -> None:
    if str(task_type or "").strip() != INITIAL_CHECK:
        return
    if str((selector or {}).get("mode") or "").strip() != "date_sheet":
        return
    offset_days = int((submit_policy or {}).get("target_date_offset_days") or 0)
    if offset_days != 0:
        raise ValueError("initial_check date_sheet trigger must target today: target_date_offset_days must be 0")


def _trigger_summary(config: dict[str, Any], bindings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": config.get("id"),
        "config_key": config.get("config_key"),
        "source_type": config.get("source_type"),
        "doc_url": config.get("doc_url"),
        "file_id": config.get("file_id"),
        "sheet_selector": config.get("sheet_selector") or {},
        "submit_policy": config.get("submit_policy") or {},
        "scan_interval_seconds": config.get("scan_interval_seconds"),
        "next_scan_at": str(config.get("next_scan_at") or ""),
        "last_scan_at": str(config.get("last_scan_at") or ""),
        "scan_status": config.get("scan_status"),
        "status": config.get("status"),
        "description": config.get("description"),
        "bindings": [
            {
                "id": item.get("id"),
                "task_type": item.get("task_type"),
                "field_names": list(item.get("field_names") or []),
                "status": item.get("status"),
                "priority": item.get("priority"),
                "max_attempts": item.get("max_attempts"),
            }
            for item in bindings
        ],
    }
