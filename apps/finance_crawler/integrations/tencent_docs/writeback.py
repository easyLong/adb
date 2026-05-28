"""Tencent Docs writeback orchestration."""

from __future__ import annotations

from typing import Any

from apps.finance_crawler.config import Config
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
    screenshot_upload_rows: list[tuple[int, str]] = []
    for item in rows:
        row_index = int(item["row_index"])
        screenshot_path = item.get("screenshot_path")
        detail_status = _detail_status(item)
        if detail_status in _MISSING_CONTENT_STATUSES and Config.QQ_COL_ACCOUNT_NAME >= 0:
            requests_payload.append(_missing_content_account_request(row_index, doc=doc))

        should_upload_screenshot = write_requests.can_upload_screenshot(screenshot_path)
        has_detail_row = (
            item.get("read_count") is not None
            and item.get("comment_count") is not None
            and detail_status is not None
            and Config.QQ_COL_COMMENT_COUNT == Config.QQ_COL_READ_COUNT + 1
            and Config.QQ_COL_DETAIL_STATUS == Config.QQ_COL_COMMENT_COUNT + 1
        )
        if has_detail_row:
            values = [item["read_count"], item["comment_count"], _detail_remark(item)]
            screenshot_written_with_row = (
                Config.QQ_COL_SCREENSHOT == Config.QQ_COL_DETAIL_STATUS + 1
                and bool(screenshot_path)
                and not Config.SCREENSHOT_PUBLIC_BASE_URL
                and not should_upload_screenshot
            )
            if screenshot_written_with_row:
                values.append(write_requests.screenshot_cell_value(screenshot_path))
            requests_payload.append(
                write_requests.row_cells_request(
                    row_index,
                    Config.QQ_COL_READ_COUNT,
                    values,
                    doc=doc,
                )
            )
            if should_upload_screenshot:
                requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_SCREENSHOT, "", doc=doc))
                screenshot_upload_rows.append((row_index, str(screenshot_path)))
            elif Config.QQ_COL_SCREENSHOT >= 0 and screenshot_path and not screenshot_written_with_row:
                requests_payload.append(write_requests.screenshot_cell_request(row_index, screenshot_path, doc=doc))
            continue
        if item.get("check_status") is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_CHECK_STATUS, item["check_status"], doc=doc))
        if item.get("read_count") is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_READ_COUNT, item["read_count"], doc=doc))
        if item.get("comment_count") is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_COMMENT_COUNT, item["comment_count"], doc=doc))
        if detail_status is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_DETAIL_STATUS, _detail_remark(item), doc=doc))
        if should_upload_screenshot:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_SCREENSHOT, "", doc=doc))
            screenshot_upload_rows.append((row_index, str(screenshot_path)))
        elif Config.QQ_COL_SCREENSHOT >= 0 and screenshot_path:
            requests_payload.append(write_requests.screenshot_cell_request(row_index, screenshot_path, doc=doc))

    client.post_batch_update(requests_payload, "write_detail_rows", doc=doc)
    fallback_requests = screenshots.post_screenshot_images(screenshot_upload_rows, doc=doc)
    client.post_batch_update(fallback_requests, "screenshot_fallback", doc=doc)


def write_initial_check_results(rows: list[dict[str, Any]]) -> None:
    for doc, doc_rows in _group_rows_by_doc(rows):
        _write_initial_check_results_for_doc(doc_rows, doc=doc)


def _write_initial_check_results_for_doc(rows: list[dict[str, Any]], *, doc: client.DocInfo) -> None:
    requests_payload: list[dict[str, Any]] = []

    for item in rows:
        row_index = int(item["row_index"])
        exists = bool(item["exists"])
        if exists:
            requests_payload.append(
                write_requests.cell_request(row_index, Config.QQ_COL_ACCOUNT_NAME, item.get("account_name") or "", doc=doc)
            )
        else:
            requests_payload.append(_missing_content_account_request(row_index, doc=doc))

    client.post_batch_update(requests_payload, "initial_check", doc=doc)


def write_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    write_initial_check_results(
        [{"row_index": row_index, "exists": exists, "account_name": account_name}]
    )


def _missing_content_account_request(row_index: int, *, doc: client.DocInfo | None = None) -> dict[str, Any]:
    resolved_doc = doc or client.configured_doc()
    return write_requests.cell_request(
        row_index,
        Config.QQ_COL_ACCOUNT_NAME,
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
