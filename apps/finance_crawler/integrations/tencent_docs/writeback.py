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


def write_detail_rows(rows: list[dict[str, Any]]) -> None:
    requests_payload: list[dict[str, Any]] = []
    screenshot_upload_rows: list[tuple[int, str]] = []
    for item in rows:
        row_index = int(item["row_index"])
        screenshot_path = item.get("screenshot_path")
        detail_status = _detail_status(item)
        if detail_status in _MISSING_CONTENT_STATUSES and Config.QQ_COL_ACCOUNT_NAME >= 0:
            requests_payload.append(_missing_content_account_request(row_index))

        should_upload_screenshot = write_requests.can_upload_screenshot(screenshot_path)
        has_detail_row = (
            item.get("read_count") is not None
            and item.get("comment_count") is not None
            and detail_status is not None
            and Config.QQ_COL_COMMENT_COUNT == Config.QQ_COL_READ_COUNT + 1
            and Config.QQ_COL_DETAIL_STATUS == Config.QQ_COL_COMMENT_COUNT + 1
        )
        if has_detail_row:
            values = [item["read_count"], item["comment_count"], detail_status]
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
                )
            )
            if should_upload_screenshot:
                screenshot_upload_rows.append((row_index, str(screenshot_path)))
            elif Config.QQ_COL_SCREENSHOT >= 0 and screenshot_path and not screenshot_written_with_row:
                requests_payload.append(write_requests.screenshot_cell_request(row_index, screenshot_path))
            continue
        if item.get("check_status") is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_CHECK_STATUS, item["check_status"]))
        if item.get("read_count") is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_READ_COUNT, item["read_count"]))
        if item.get("comment_count") is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_COMMENT_COUNT, item["comment_count"]))
        if detail_status is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_DETAIL_STATUS, detail_status))
        if should_upload_screenshot:
            screenshot_upload_rows.append((row_index, str(screenshot_path)))
        elif Config.QQ_COL_SCREENSHOT >= 0 and screenshot_path:
            requests_payload.append(write_requests.screenshot_cell_request(row_index, screenshot_path))

    client.post_batch_update(requests_payload, "write_detail_rows")
    fallback_requests = screenshots.post_screenshot_images(screenshot_upload_rows)
    client.post_batch_update(fallback_requests, "screenshot_fallback")


def write_initial_check_results(rows: list[dict[str, Any]]) -> None:
    requests_payload: list[dict[str, Any]] = []

    for item in rows:
        row_index = int(item["row_index"])
        exists = bool(item["exists"])
        if exists:
            requests_payload.append(
                write_requests.cell_request(row_index, Config.QQ_COL_ACCOUNT_NAME, item.get("account_name") or "")
            )
        else:
            requests_payload.append(_missing_content_account_request(row_index))

    client.post_batch_update(requests_payload, "initial_check")


def write_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    write_initial_check_results(
        [{"row_index": row_index, "exists": exists, "account_name": account_name}]
    )


def _missing_content_account_request(row_index: int) -> dict[str, Any]:
    return write_requests.cell_request(
        row_index,
        Config.QQ_COL_ACCOUNT_NAME,
        "N",
        background_color=_HIGHLIGHT_YELLOW,
        text_format=_RED_BOLD,
    )
