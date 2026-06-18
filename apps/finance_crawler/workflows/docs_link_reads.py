"""Write read counts from Tencent Docs K-column links back to M-column cells."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.integrations.tencent_docs import columns as tencent_docs_columns
from apps.finance_crawler.integrations.tencent_docs.write_requests import cell_request
from apps.finance_crawler.mobile import read_count_crawler
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.storage.device_pool import acquire_device
from apps.finance_crawler.storage.db import log_task
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("docs_link_reads")


@dataclass(frozen=True)
class DocLinkReadTarget:
    row_index: int
    link: str
    title: str
    account_name: str
    existing_read: str


def run_docs_link_reads(
    *,
    doc_url: str | None = None,
    target_date: date | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    started = time.time()
    doc = _select_doc(doc_url=doc_url, target_date=target_date)
    targets, columns = _read_targets(doc, limit=limit)
    results: list[dict[str, Any]] = []
    requests_payload: list[dict[str, Any]] = []
    written_count = 0

    if not targets:
        summary = {"targets": 0, "success": 0, "failed": 0, "written": 0}
        log_task("docs_link_reads", "success", json.dumps(summary), time.time() - started)
        return summary

    with acquire_device(
        app_type="read_count",
        task_scope="docs_link_reads",
        task_id=f"{doc.file_id}:{doc.sheet_id}:{target_date.isoformat() if target_date else 'all'}",
        worker_id="docs_link_reads",
    ):
        try:
            assert_device_ready()
        except DeviceUnavailable:
            reset_device_session()
            raise

        for index, target in enumerate(targets, start=1):
            logger.info(
                "doc link read crawl %s/%s row=%s account=%s",
                index,
                len(targets),
                target.row_index,
                target.account_name,
            )
            result = _crawl_target(target)
            results.append(result)
            read_value: Any = result["read_count"] if result.get("status") == "success" else "N"
            requests_payload.append(
                cell_request(
                    target.row_index,
                    columns["read_count"],
                    read_value,
                    doc=doc,
                )
            )
            if result.get("status") != "success":
                result["writeback_value"] = "N"
            if len(requests_payload) >= Config.QQ_BATCH_UPDATE_SIZE:
                client.post_batch_update(requests_payload, "docs_link_reads_partial", doc=doc)
                written_count += len(requests_payload)
                requests_payload.clear()

    if requests_payload:
        client.post_batch_update(requests_payload, "docs_link_reads", doc=doc)
        written_count += len(requests_payload)

    summary = {
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "targets": len(targets),
        "success": sum(1 for item in results if item.get("status") == "success"),
        "failed": sum(1 for item in results if item.get("status") != "success"),
        "marked_n": sum(1 for item in results if item.get("writeback_value") == "N"),
        "written": written_count,
        "results": results,
    }
    log_task("docs_link_reads", "success", json.dumps(_log_safe(summary), ensure_ascii=False), time.time() - started)
    return summary


def extract_read_count_from_records(records: list[dict[str, Any]]) -> int | None:
    return read_count_crawler.extract_read_count_from_records(records)


def extract_read_count_from_texts(texts: list[str]) -> int | None:
    return read_count_crawler.extract_read_count_from_texts(texts)


def _crawl_target(target: DocLinkReadTarget) -> dict[str, Any]:
    result = read_count_crawler.crawl_read_count_target(
        read_count_crawler.ReadCountTarget(
            row_index=target.row_index,
            link=target.link,
            title=target.title,
            account_name=target.account_name,
            existing_read=target.existing_read,
            output_prefix="doc_link_reads",
        )
    )
    if result.get("status") == "not_found":
        result = dict(result)
        result["status"] = "error"
    return result


def _not_found_reason(records: list[dict[str, Any]]) -> str | None:
    return read_count_crawler.not_found_reason_from_records(records)


def _read_targets(doc: client.DocInfo, *, limit: int | None = None) -> tuple[list[DocLinkReadTarget], dict[str, int]]:
    rows, start_row = client.fetch_grid(Config.DOC_LINK_READS_READ_RANGE, doc=doc)
    columns = tencent_docs_columns.resolve_columns(
        rows,
        start_row,
        tencent_docs_columns.DOC_LINK_READS_ALIASES,
        tencent_docs_columns.default_doc_link_read_fallbacks(),
        strict_fallback_title=True,
    )
    targets: list[DocLinkReadTarget] = []
    resolved_limit = Config.DOC_LINK_READS_CRAWL_LIMIT if limit is None else limit
    for offset, row in enumerate(rows):
        row_index = start_row + offset + 1
        if row_index == 1:
            continue
        link = _cell(row, columns["link"])
        if not link or not _looks_like_link(link):
            continue
        existing_read = _cell(row, columns["read_count"])
        if Config.DOC_LINK_READS_ONLY_EMPTY and existing_read:
            continue
        targets.append(
            DocLinkReadTarget(
                row_index=row_index,
                link=link,
                title=_cell(row, columns["title"]),
                account_name=_cell(row, columns["account_name"]),
                existing_read=existing_read,
            )
        )
        if resolved_limit and resolved_limit > 0 and len(targets) >= resolved_limit:
            break
    logger.info("doc link read targets=%s sheet=%s", len(targets), doc.sheet_id)
    return targets, columns


def _select_doc(*, doc_url: str | None = None, target_date: date | None = None) -> client.DocInfo:
    base = client.parse_doc_url(doc_url) if doc_url else client.configured_doc()
    sheet_title = Config.DOC_LINK_READS_SHEET_TITLE.strip()
    if target_date is not None:
        sheet_title = target_date.strftime("%m%d")
    if not sheet_title:
        return base

    sheets = client.fetch_file_sheets(base.file_id)
    for sheet in sheets:
        if sheet.title == sheet_title:
            return sheet.doc
    for sheet in sheets:
        if sheet_title in sheet.title:
            return sheet.doc
    available = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in sheets)
    raise RuntimeError(f"sheet title not found: {sheet_title}; available: {available}")


def _cell(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _looks_like_link(text: str) -> bool:
    return text.startswith(("http://", "https://", "alipays://", "alipay://"))


def _log_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _log_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_log_safe(item) for item in value]
    return value
