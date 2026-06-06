"""Execute field-level writeback plans."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.documents.column_resolver import ColumnMapping, resolve_header
from apps.finance_crawler.crawler_app.documents.fields import SCREENSHOT
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.integrations.tencent_docs.screenshots import post_screenshot_images
from apps.finance_crawler.integrations.tencent_docs.write_requests import cell_request
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("crawler_app_writeback")
DUPLICATE_ROW_MARKER = "\u91cd\u590d"


@dataclass(frozen=True, slots=True)
class SheetWritebackContext:
    rows: list[list[str]]
    start_row: int
    mapping: ColumnMapping


def apply_pending_writebacks(
    conn,
    *,
    limit: int | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    plans = repository.get_pending_writeback_plans(conn, limit=limit, source=source)
    if not plans:
        return {"planned": 0, "success": 0, "failed": 0, "skipped": 0}

    requests_by_doc: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    screenshot_rows_by_doc: dict[tuple[str, str], list[tuple[int, str, int]]] = defaultdict(list)
    plan_ids_by_doc: dict[tuple[str, str], list[int]] = defaultdict(list)
    contexts_by_doc: dict[tuple[str, str], SheetWritebackContext] = {}
    skipped: list[tuple[int, str, int | None]] = []

    for plan in plans:
        field_name = str(plan["field_name"])
        correction_id = _correction_id(plan)
        file_id = str(plan["file_id"])
        sheet_id = str(plan["sheet_id"])
        doc = client.DocInfo(file_id=file_id, sheet_id=sheet_id)
        doc_key = (file_id, sheet_id)
        context = contexts_by_doc.get(doc_key)
        if context is None:
            context = _load_sheet_context(doc)
            contexts_by_doc[doc_key] = context

        if not context.mapping.ok:
            skipped.append((int(plan["id"]), "current sheet header cannot be resolved: " + "; ".join(context.mapping.problems), correction_id))
            continue
        if field_name not in context.mapping.columns:
            skipped.append((int(plan["id"]), f"field not mapped in current sheet: {field_name}", correction_id))
            continue
        post_url = _plan_post_url(plan)
        if not post_url:
            skipped.append((int(plan["id"]), "missing post_url for URL-based writeback", correction_id))
            continue
        try:
            row_indexes = _resolve_current_row_indexes(context, post_url)
        except RuntimeError as exc:
            skipped.append((int(plan["id"]), str(exc), correction_id))
            continue
        if not row_indexes:
            skipped.append((int(plan["id"]), f"post_url not found in current sheet: {post_url}", correction_id))
            continue

        target_column = int(context.mapping.columns[field_name])
        if field_name == SCREENSHOT:
            requests_by_doc[(file_id, sheet_id)].append(
                cell_request(
                    row_indexes[0],
                    target_column,
                    "",
                    doc=doc,
                )
            )
            screenshot_rows_by_doc[(file_id, sheet_id)].append(
                (row_indexes[0], str(plan.get("value_text") or ""), target_column)
            )
        else:
            requests_by_doc[(file_id, sheet_id)].extend(
                _overwrite_cell_requests(
                    row_indexes[0],
                    target_column,
                    plan.get("value_text") or "",
                    doc=doc,
                )
            )
        for duplicate_row_index in row_indexes[1:]:
            requests_by_doc[(file_id, sheet_id)].extend(
                _overwrite_cell_requests(
                    duplicate_row_index,
                    target_column,
                    DUPLICATE_ROW_MARKER,
                    doc=doc,
                )
            )
        plan_ids_by_doc[(file_id, sheet_id)].append(int(plan["id"]))

    for plan_id, error, correction_id in skipped:
        repository.mark_writeback_plans(conn, [plan_id], status="skipped", error=error)
        if correction_id:
            repository.mark_corrections(conn, [correction_id], status="skipped")

    success_count = 0
    failed_count = 0
    doc_keys = sorted(set(requests_by_doc) | set(screenshot_rows_by_doc))
    for (file_id, sheet_id) in doc_keys:
        requests = requests_by_doc[(file_id, sheet_id)]
        plan_ids = plan_ids_by_doc[(file_id, sheet_id)]
        correction_ids = _correction_ids_for_plans(plans, plan_ids)
        doc = client.DocInfo(file_id=file_id, sheet_id=sheet_id)
        try:
            if requests:
                client.post_batch_update(
                    requests,
                    "crawler_app_writeback",
                    doc=doc,
                )
            fallback_requests = post_screenshot_images(screenshot_rows_by_doc[(file_id, sheet_id)], doc=doc)
            if fallback_requests:
                client.post_batch_update(
                    fallback_requests,
                    "crawler_app_screenshot_fallback",
                    doc=doc,
                )
            repository.mark_writeback_plans(conn, plan_ids, status="success")
            repository.mark_corrections(conn, correction_ids, status="success")
            success_count += len(plan_ids)
        except Exception as exc:
            logger.warning("crawler_app writeback failed file=%s sheet=%s: %s", file_id, sheet_id, exc)
            repository.mark_writeback_plans(conn, plan_ids, status="error", error=str(exc))
            repository.mark_corrections(conn, correction_ids, status="error")
            failed_count += len(plan_ids)

    return {
        "planned": len(plans),
        "success": success_count,
        "failed": failed_count,
        "skipped": len(skipped),
    }


def _load_sheet_context(doc: client.DocInfo) -> SheetWritebackContext:
    rows, start_row = client.fetch_grid(Config.DOC_LINK_READS_READ_RANGE, doc=doc)
    header = rows[0] if rows else []
    return SheetWritebackContext(rows=rows, start_row=start_row, mapping=resolve_header(header))


def _overwrite_cell_requests(
    row_index: int,
    column_index: int,
    value: Any,
    *,
    doc: client.DocInfo,
) -> list[dict[str, Any]]:
    return [
        cell_request(row_index, column_index, "", doc=doc),
        cell_request(row_index, column_index, value, doc=doc),
    ]


def _plan_post_url(plan: dict[str, Any]) -> str:
    value = plan.get("current_post_url")
    if value:
        return str(value).strip()
    payload = plan.get("payload") or {}
    for key in ("post_url", "url"):
        if payload.get(key):
            return str(payload[key]).strip()
    return ""


def _resolve_current_row_indexes(
    context: SheetWritebackContext,
    post_url: str,
) -> list[int]:
    post_url_col = context.mapping.columns.get("post_url")
    if post_url_col is None:
        raise RuntimeError("post_url column not mapped in current sheet")
    target = post_url.strip()
    if not target:
        return []

    matches = []
    for offset, row in enumerate(context.rows):
        if _cell_text(row, post_url_col) == target:
            matches.append(context.start_row + offset + 1)
    return matches


def _cell_text(row: list[str], column_index: int) -> str:
    if column_index < 0 or column_index >= len(row):
        return ""
    return str(row[column_index] or "").strip()


def _correction_id(plan: dict[str, Any]) -> int | None:
    payload = plan.get("payload") or {}
    raw = payload.get("correction_id")
    return int(raw) if raw else None


def _correction_ids_for_plans(plans: list[dict[str, Any]], plan_ids: list[int]) -> list[int]:
    selected = set(plan_ids)
    correction_ids = []
    for plan in plans:
        if int(plan["id"]) not in selected:
            continue
        correction_id = _correction_id(plan)
        if correction_id and correction_id not in correction_ids:
            correction_ids.append(correction_id)
    return correction_ids
