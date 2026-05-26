"""Tencent Docs writeback orchestration."""

from __future__ import annotations

from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client, screenshots, write_requests
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("tencent_docs_writeback")


def legacy_single_write_back_row(
    row_index: int,
    check_status: str | None = None,
    read_count: int | None = None,
    comment_count: int | None = None,
    batch_status: str | None = None,
) -> None:
    requests_payload = []
    if check_status is not None:
        requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_CHECK_STATUS, check_status))
    if read_count is not None:
        requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_READ_COUNT, read_count))
    if comment_count is not None:
        requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_COMMENT_COUNT, comment_count))
    if batch_status is not None:
        requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_BATCH_STATUS, batch_status))

    if not requests_payload:
        return

    client.post_batch_update(requests_payload[:5], "legacy_write_back_row")
    logger.info("writeback Tencent Docs row=%s requests=%s", row_index, requests_payload)


def legacy_single_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    if exists:
        requests_payload = [write_requests.cell_request(row_index, Config.QQ_COL_ACCOUNT_NAME, account_name or "")]
    else:
        yellow = {"red": 255, "green": 255, "blue": 0, "alpha": 255}
        red_bold = {
            "bold": True,
            "color": {"red": 255, "green": 0, "blue": 0, "alpha": 255},
        }
        requests_payload = [
            write_requests.cell_request(
                row_index,
                Config.QQ_COL_ACCOUNT_NAME,
                "N",
                background_color=yellow,
                text_format=red_bold,
            )
        ]

    client.post_batch_update(requests_payload, "legacy_initial_check")
    logger.info("initial check writeback row=%s exists=%s account=%s", row_index, exists, account_name)


def write_back_rows(rows: list[dict[str, Any]]) -> None:
    requests_payload: list[dict[str, Any]] = []
    screenshot_upload_rows: list[tuple[int, str]] = []
    for item in rows:
        row_index = int(item["row_index"])
        screenshot_path = item.get("screenshot_path")
        should_upload_screenshot = write_requests.can_upload_screenshot(screenshot_path)
        has_batch_row = (
            item.get("read_count") is not None
            and item.get("comment_count") is not None
            and item.get("batch_status") is not None
            and Config.QQ_COL_COMMENT_COUNT == Config.QQ_COL_READ_COUNT + 1
            and Config.QQ_COL_BATCH_STATUS == Config.QQ_COL_COMMENT_COUNT + 1
        )
        if has_batch_row:
            values = [item["read_count"], item["comment_count"], item["batch_status"]]
            screenshot_written_with_row = (
                Config.QQ_COL_SCREENSHOT == Config.QQ_COL_BATCH_STATUS + 1
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
        if item.get("batch_status") is not None:
            requests_payload.append(write_requests.cell_request(row_index, Config.QQ_COL_BATCH_STATUS, item["batch_status"]))
        if should_upload_screenshot:
            screenshot_upload_rows.append((row_index, str(screenshot_path)))
        elif Config.QQ_COL_SCREENSHOT >= 0 and screenshot_path:
            requests_payload.append(write_requests.screenshot_cell_request(row_index, screenshot_path))

    client.post_batch_update(requests_payload, "write_back_rows")
    fallback_requests = screenshots.post_screenshot_images(screenshot_upload_rows)
    client.post_batch_update(fallback_requests, "screenshot_fallback")


def write_back_row(
    row_index: int,
    check_status: str | None = None,
    read_count: int | None = None,
    comment_count: int | None = None,
    batch_status: str | None = None,
) -> None:
    write_back_rows(
        [
            {
                "row_index": row_index,
                "check_status": check_status,
                "read_count": read_count,
                "comment_count": comment_count,
                "batch_status": batch_status,
            }
        ]
    )


def write_initial_check_results(rows: list[dict[str, Any]]) -> None:
    requests_payload: list[dict[str, Any]] = []
    yellow = {"red": 255, "green": 255, "blue": 0, "alpha": 255}
    red_bold = {
        "bold": True,
        "color": {"red": 255, "green": 0, "blue": 0, "alpha": 255},
    }

    for item in rows:
        row_index = int(item["row_index"])
        exists = bool(item["exists"])
        if exists:
            requests_payload.append(
                write_requests.cell_request(row_index, Config.QQ_COL_ACCOUNT_NAME, item.get("account_name") or "")
            )
        else:
            requests_payload.append(
                write_requests.cell_request(
                    row_index,
                    Config.QQ_COL_ACCOUNT_NAME,
                    "N",
                    background_color=yellow,
                    text_format=red_bold,
                )
            )

    client.post_batch_update(requests_payload, "initial_check")


def write_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    write_initial_check_results(
        [{"row_index": row_index, "exists": exists, "account_name": account_name}]
    )
