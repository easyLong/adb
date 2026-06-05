"""Tencent Docs writeback orchestration."""

from __future__ import annotations

from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import columns as tencent_docs_columns
from apps.finance_crawler.integrations.tencent_docs import client, screenshots, write_requests
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("tencent_docs_writeback")

_HIGHLIGHT_YELLOW = {"red": 255, "green": 255, "blue": 0, "alpha": 255}
_RED_BOLD = {
    "bold": True,
    "color": {"red": 255, "green": 0, "blue": 0, "alpha": 255},
}
_MISSING_CONTENT_STATUSES = {"deleted", "not_found"}


def _detail_status(item: dict[str, Any]) -> Any:
    return item.get("detail_status")


def _detail_remark(item: dict[str, Any]) -> Any:
    return item.get("detail_remark") or item.get("detail_status")


def write_detail_rows(rows: list[dict[str, Any]]) -> None:
    for doc, doc_rows in _group_rows_by_doc(rows):
        _write_detail_rows_for_doc(doc_rows, doc=doc)


def _write_detail_rows_for_doc(rows: list[dict[str, Any]], *, doc: client.DocInfo) -> None:
    requests_payload: list[dict[str, Any]] = []
    screenshot_upload_rows: list[tuple[int, str, int]] = []
    columns = tencent_docs_columns.fetch_header_columns(
        doc,
        aliases_by_field={
            "account_name": tencent_docs_columns.MAIN_COLUMN_ALIASES["account_name"],
            "read_count": tencent_docs_columns.MAIN_COLUMN_ALIASES["read_count"],
            "comment_count": tencent_docs_columns.MAIN_COLUMN_ALIASES["comment_count"],
            "detail_status": tencent_docs_columns.MAIN_COLUMN_ALIASES["detail_status"],
            "screenshot": tencent_docs_columns.MAIN_COLUMN_ALIASES["screenshot"],
        },
        fallbacks={
            "account_name": Config.QQ_COL_ACCOUNT_NAME,
            "read_count": Config.QQ_COL_READ_COUNT,
            "comment_count": Config.QQ_COL_COMMENT_COUNT,
            "detail_status": Config.QQ_COL_DETAIL_STATUS,
            "screenshot": Config.QQ_COL_SCREENSHOT,
        },
        strict_fallback_title=True,
    )
    account_col = columns["account_name"]
    read_col = columns["read_count"]
    comment_col = columns["comment_count"]
    detail_col = columns["detail_status"]
    screenshot_col = columns["screenshot"]
    check_col = Config.QQ_COL_CHECK_STATUS
    if any(item.get("check_status") is not None for item in rows):
        check_columns = tencent_docs_columns.fetch_header_columns(
            doc,
            aliases_by_field={"check_status": tencent_docs_columns.MAIN_COLUMN_ALIASES["check_status"]},
            fallbacks={"check_status": Config.QQ_COL_CHECK_STATUS},
            strict_fallback_title=True,
        )
        check_col = check_columns["check_status"]
    for item in rows:
        row_index = int(item["row_index"])
        screenshot_path = item.get("screenshot_path")
        detail_status = _detail_status(item)
        if detail_status in _MISSING_CONTENT_STATUSES and account_col >= 0:
            requests_payload.append(_missing_content_account_request(row_index, doc=doc, col_index=account_col))

        should_upload_screenshot = write_requests.can_upload_screenshot(screenshot_path, col_index=screenshot_col)
        has_detail_row = (
            item.get("read_count") is not None
            and item.get("comment_count") is not None
            and detail_status is not None
            and comment_col == read_col + 1
            and detail_col == comment_col + 1
        )
        if has_detail_row:
            values = [item["read_count"], item["comment_count"], _detail_remark(item)]
            screenshot_written_with_row = (
                screenshot_col == detail_col + 1
                and bool(screenshot_path)
                and not Config.SCREENSHOT_PUBLIC_BASE_URL
                and not should_upload_screenshot
            )
            if screenshot_written_with_row:
                values.append(write_requests.screenshot_cell_value(screenshot_path))
            requests_payload.append(
                write_requests.row_cells_request(
                    row_index,
                    read_col,
                    values,
                    doc=doc,
                )
            )
            if should_upload_screenshot:
                requests_payload.append(write_requests.cell_request(row_index, screenshot_col, "", doc=doc))
                screenshot_upload_rows.append((row_index, str(screenshot_path), screenshot_col))
            elif screenshot_col >= 0 and screenshot_path and not screenshot_written_with_row:
                requests_payload.append(
                    write_requests.screenshot_cell_request(row_index, screenshot_path, doc=doc, col_index=screenshot_col)
                )
            continue
        if item.get("check_status") is not None:
            requests_payload.append(write_requests.cell_request(row_index, check_col, item["check_status"], doc=doc))
        if item.get("read_count") is not None:
            requests_payload.append(write_requests.cell_request(row_index, read_col, item["read_count"], doc=doc))
        if item.get("comment_count") is not None:
            requests_payload.append(write_requests.cell_request(row_index, comment_col, item["comment_count"], doc=doc))
        if detail_status is not None:
            requests_payload.append(write_requests.cell_request(row_index, detail_col, _detail_remark(item), doc=doc))
        if should_upload_screenshot:
            requests_payload.append(write_requests.cell_request(row_index, screenshot_col, "", doc=doc))
            screenshot_upload_rows.append((row_index, str(screenshot_path), screenshot_col))
        elif screenshot_col >= 0 and screenshot_path:
            requests_payload.append(
                write_requests.screenshot_cell_request(row_index, screenshot_path, doc=doc, col_index=screenshot_col)
            )

    client.post_batch_update(requests_payload, "write_detail_rows", doc=doc)
    fallback_requests = screenshots.post_screenshot_images(screenshot_upload_rows, doc=doc)
    client.post_batch_update(fallback_requests, "screenshot_fallback", doc=doc)


def write_initial_check_results(rows: list[dict[str, Any]]) -> None:
    for doc, doc_rows in _group_rows_by_doc(rows):
        _write_initial_check_results_for_doc(doc_rows, doc=doc)


def _write_initial_check_results_for_doc(rows: list[dict[str, Any]], *, doc: client.DocInfo) -> None:
    requests_payload: list[dict[str, Any]] = []
    columns = tencent_docs_columns.fetch_header_columns(
        doc,
        aliases_by_field={"account_name": tencent_docs_columns.MAIN_COLUMN_ALIASES["account_name"]},
        fallbacks={"account_name": Config.QQ_COL_ACCOUNT_NAME},
        strict_fallback_title=True,
    )
    account_col = columns["account_name"]

    for item in rows:
        row_index = int(item["row_index"])
        exists = bool(item["exists"])
        if exists:
            requests_payload.append(
                write_requests.cell_request(row_index, account_col, item.get("account_name") or "", doc=doc)
            )
        else:
            requests_payload.append(_missing_content_account_request(row_index, doc=doc, col_index=account_col))

    client.post_batch_update(requests_payload, "initial_check", doc=doc)


def write_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    write_initial_check_results(
        [{"row_index": row_index, "exists": exists, "account_name": account_name}]
    )


def _missing_content_account_request(
    row_index: int,
    *,
    doc: client.DocInfo | None = None,
    col_index: int | None = None,
) -> dict[str, Any]:
    resolved_doc = doc or client.configured_doc()
    return write_requests.cell_request(
        row_index,
        Config.QQ_COL_ACCOUNT_NAME if col_index is None else col_index,
        "N",
        background_color=_HIGHLIGHT_YELLOW,
        text_format=_RED_BOLD,
        doc=resolved_doc,
    )


def _group_rows_by_doc(rows: list[dict[str, Any]]) -> list[tuple[client.DocInfo, list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    default_doc = client.configured_doc()
    for item in rows:
        file_id = str(item.get("file_id") or default_doc.file_id)
        sheet_id = str(item.get("sheet_id") or default_doc.sheet_id)
        grouped.setdefault((file_id, sheet_id), []).append(item)
    return [(client.DocInfo(file_id, sheet_id), items) for (file_id, sheet_id), items in grouped.items()]
