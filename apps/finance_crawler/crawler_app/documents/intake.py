"""Document intake into crawler_app v2 tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from apps.finance_crawler.crawler_app.documents.column_resolver import resolve_header
from apps.finance_crawler.crawler_app.documents.rows import DocumentSourceRow, extract_source_rows
from apps.finance_crawler.crawler_app.documents.sources import (
    DocumentSheetSnapshot,
    DocumentSource,
    TencentDocsSource,
)
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.tasks.submission import TaskSubmission, build_task_submission
from apps.finance_crawler.crawler_app.tasks.types import READ_COUNT
from apps.finance_crawler.utils.link_source import resolve_source_app


@dataclass(frozen=True, slots=True)
class IntakeSummary:
    document_id: int
    sheet_id: str
    sheet_title: str
    column_mapping_id: int
    source_rows: int
    submissions: int
    problems: tuple[str, ...]


def submit_read_count_tasks_from_tencent_doc(
    conn,
    *,
    doc_url: str | None = None,
    target_date: date | None = None,
    range_a1: str | None = None,
    limit: int | None = None,
    sheet_selector: dict[str, object] | None = None,
    created_by: str = "crawler_app_intake",
) -> IntakeSummary:
    return submit_document_tasks_from_source(
        conn,
        source=TencentDocsSource(doc_url=doc_url),
        task_type=READ_COUNT,
        target_date=target_date,
        range_a1=range_a1,
        limit=limit,
        sheet_selector=sheet_selector,
        requested_fields=(READ_COUNT,),
        created_by=created_by,
    )


def submit_read_count_tasks_from_source(
    conn,
    *,
    source: DocumentSource,
    target_date: date | None = None,
    range_a1: str | None = None,
    limit: int | None = None,
    sheet_selector: dict[str, object] | None = None,
    created_by: str = "crawler_app_intake",
) -> IntakeSummary:
    return submit_document_tasks_from_source(
        conn,
        source=source,
        task_type=READ_COUNT,
        target_date=target_date,
        range_a1=range_a1,
        limit=limit,
        sheet_selector=sheet_selector,
        requested_fields=(READ_COUNT,),
        created_by=created_by,
    )


def submit_document_tasks_from_tencent_doc(
    conn,
    *,
    task_type: str,
    doc_url: str | None = None,
    target_date: date | None = None,
    range_a1: str | None = None,
    limit: int | None = None,
    sheet_selector: dict[str, object] | None = None,
    requested_fields: tuple[str, ...] | list[str] | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    created_by: str = "crawler_app_intake",
) -> IntakeSummary:
    return submit_document_tasks_from_source(
        conn,
        source=TencentDocsSource(doc_url=doc_url),
        task_type=task_type,
        target_date=target_date,
        range_a1=range_a1,
        limit=limit,
        sheet_selector=sheet_selector,
        requested_fields=requested_fields,
        priority=priority,
        max_attempts=max_attempts,
        created_by=created_by,
    )


def submit_document_tasks_from_source(
    conn,
    *,
    source: DocumentSource,
    task_type: str,
    target_date: date | None = None,
    range_a1: str | None = None,
    limit: int | None = None,
    sheet_selector: dict[str, object] | None = None,
    requested_fields: tuple[str, ...] | list[str] | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    created_by: str = "crawler_app_intake",
) -> IntakeSummary:
    snapshot = source.load_sheet(target_date=target_date, range_a1=range_a1, sheet_selector=sheet_selector)
    header = snapshot.rows[0] if snapshot.rows else []
    mapping = resolve_header(header)
    if not mapping.ok:
        return IntakeSummary(
            document_id=0,
            sheet_id=snapshot.sheet_id,
            sheet_title=snapshot.sheet_title,
            column_mapping_id=0,
            source_rows=0,
            submissions=0,
            problems=mapping.problems,
        )

    source_rows = extract_source_rows(
        snapshot.rows,
        mapping.columns,
        start_row=snapshot.start_row,
        data_start_offset=1,
        business_date=snapshot.business_date,
    )
    if limit and limit > 0:
        source_rows = source_rows[:limit]

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
    submissions = _build_document_task_submissions(
        source_rows,
        source_row_ids=source_row_ids,
        document_id=document_id,
        snapshot=snapshot,
        column_mapping_id=column_mapping_id,
        task_type=task_type,
        requested_fields=tuple(requested_fields or ()),
        priority=priority,
        max_attempts=max_attempts,
        created_by=created_by,
    )
    submission_count = repository.submit_task_submissions(conn, submissions)
    return IntakeSummary(
        document_id=document_id,
        sheet_id=snapshot.sheet_id,
        sheet_title=snapshot.sheet_title,
        column_mapping_id=column_mapping_id,
        source_rows=len(source_rows),
        submissions=submission_count,
        problems=(),
    )


def _build_document_task_submissions(
    rows: list[DocumentSourceRow],
    *,
    source_row_ids: dict[int, int],
    document_id: int,
    snapshot: DocumentSheetSnapshot,
    column_mapping_id: int,
    task_type: str,
    requested_fields: tuple[str, ...],
    priority: int,
    max_attempts: int,
    created_by: str,
) -> list[TaskSubmission]:
    submissions = []
    for row in rows:
        submissions.append(
            build_task_submission(
                row,
                document_id=document_id,
                sheet_id=snapshot.sheet_id,
                task_type=task_type,
                app_type=resolve_source_app(None, row.post_url),
                source_row_id=source_row_ids.get(row.row_index),
                source_locator_extra={
                    "file_id": snapshot.file_id,
                    "sheet_id": snapshot.sheet_id,
                    "sheet_title": snapshot.sheet_title,
                    "column_mapping_id": column_mapping_id,
                    "requested_fields": list(requested_fields),
                },
                priority=priority,
                max_attempts=max_attempts,
                created_by=created_by,
            )
        )
    return submissions


def summary_to_dict(summary: IntakeSummary) -> dict[str, Any]:
    return {
        "document_id": summary.document_id,
        "sheet_id": summary.sheet_id,
        "sheet_title": summary.sheet_title,
        "column_mapping_id": summary.column_mapping_id,
        "source_rows": summary.source_rows,
        "submissions": summary.submissions,
        "problems": list(summary.problems),
    }
