"""Tencent Docs read/write integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client as tencent_docs_client
from apps.finance_crawler.integrations.tencent_docs import rows as tencent_docs_rows
from apps.finance_crawler.integrations.tencent_docs import screenshots as tencent_docs_screenshots
from apps.finance_crawler.integrations.tencent_docs import writeback as tencent_docs_writeback
from apps.finance_crawler.integrations.tencent_docs import write_requests as tencent_docs_requests
from apps.finance_crawler.utils import tabular_links

BASE_URL = tencent_docs_client.BASE_URL
IMAGE_UPLOAD_URL = tencent_docs_client.IMAGE_UPLOAD_URL
DocInfo = tencent_docs_client.DocInfo


@dataclass(frozen=True)
class SheetInfo:
    sheet_id: str
    title: str


def _is_supported_post_url(url: str) -> bool:
    return tabular_links.is_supported_post_url(url)


def parse_doc_url(url: str) -> DocInfo:
    return tencent_docs_client.parse_doc_url(url)


def _configured_doc() -> DocInfo:
    return tencent_docs_client.configured_doc()


def configured_doc() -> DocInfo:
    """Return the configured Tencent Docs file and sheet identifiers."""
    return _configured_doc()


def _load_token_cache() -> dict[str, Any]:
    return tencent_docs_client._load_token_cache()


def _save_token_cache(token: str, expires_in: int) -> None:
    tencent_docs_client._save_token_cache(token, expires_in)


def get_access_token() -> str:
    return tencent_docs_client.get_access_token()


def _headers() -> dict[str, str]:
    return tencent_docs_client.headers()


def _check_response(data: dict[str, Any]) -> None:
    tencent_docs_client.check_response(data)


def fetch_sheet_title() -> str:
    return tencent_docs_client.fetch_sheet_title()


def _cell_to_text(cell: dict[str, Any] | Any) -> str:
    return tencent_docs_client.cell_to_text(cell)


def _grid_to_rows(grid_data: dict[str, Any]) -> tuple[list[list[str]], int]:
    return tencent_docs_client.grid_to_rows(grid_data)


def fetch_grid(range_a1: str | None = None) -> tuple[list[list[str]], int]:
    return tencent_docs_client.fetch_grid(range_a1)


def fetch_rows() -> list[list[str]]:
    rows, _ = fetch_grid()
    return rows


def get_row_index_map() -> dict[str, int]:
    return tencent_docs_rows.get_row_index_map()


def resolve_row_index_for_url(
    url: str,
    preferred_row_index: int | None = None,
    rows: list[list[str]] | None = None,
    start_row: int | None = None,
) -> int | None:
    return tencent_docs_rows.resolve_row_index_for_url(
        url,
        preferred_row_index=preferred_row_index,
        rows=rows,
        start_row=start_row,
    )


def _parse_sheet_date(sheet_title: str) -> tuple[int, int, int] | None:
    return tabular_links.parse_sheet_date(sheet_title)


def _parse_time_from_cell(value: str) -> tuple[int, int, int] | None:
    return tabular_links.parse_time_from_cell(value)


def _parse_post_time(value: str, sheet_title: str = "") -> datetime | None:
    return tabular_links.parse_post_time(value, sheet_title)


def _eligible_candidates(
    rows: list[list[str]],
    start_row: int,
    sheet_title: str = "",
) -> list[dict[str, Any]]:
    return tabular_links.eligible_candidates(rows, start_row, sheet_title)


def eligible_candidates(
    rows: list[list[str]],
    start_row: int,
    sheet_title: str = "",
) -> list[dict[str, Any]]:
    """Build eligible crawl candidates from sheet rows."""
    return _eligible_candidates(rows, start_row, sheet_title)


def _save_latest_candidates(candidates: list[dict[str, Any]]) -> None:
    tabular_links.save_latest_candidates(candidates)


def save_latest_candidates(candidates: list[dict[str, Any]]) -> None:
    """Persist the latest candidate snapshot for debugging."""
    _save_latest_candidates(candidates)


def fetch_and_save(limit: int | None = None) -> list[dict[str, Any]]:
    from apps.finance_crawler.workflows.tencent_docs_fetch import fetch_and_save as workflow_fetch_and_save

    return workflow_fetch_and_save(limit)


def _cell_request(
    row_index: int,
    col_index: int,
    value: Any,
    background_color: dict[str, int] | None = None,
    text_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return tencent_docs_requests.cell_request(
        row_index,
        col_index,
        value,
        background_color=background_color,
        text_format=text_format,
    )


def _multipart_headers() -> dict[str, str]:
    return tencent_docs_client.multipart_headers()


def _screenshot_cell_value(path_text: str | None) -> str:
    return tencent_docs_requests.screenshot_cell_value(path_text)


def _screenshot_cell_request(row_index: int, path_text: str | None) -> dict[str, Any]:
    return tencent_docs_requests.screenshot_cell_request(row_index, path_text)


def _can_upload_screenshot(path_text: str | None) -> bool:
    return tencent_docs_requests.can_upload_screenshot(path_text)


def _image_display_size(path: Path) -> tuple[float, float]:
    return tencent_docs_requests.image_display_size(path)


def upload_image(image_path: str | Path) -> str:
    return tencent_docs_client.upload_image(image_path)


def _screenshot_image_request(row_index: int, path_text: str) -> dict[str, Any]:
    return tencent_docs_requests.screenshot_image_request(row_index, path_text)


def _post_screenshot_images(rows: list[tuple[int, str]]) -> list[dict[str, Any]]:
    return tencent_docs_screenshots.post_screenshot_images(rows)


def _row_cells_request(
    row_index: int,
    start_col_index: int,
    values: list[Any],
) -> dict[str, Any]:
    return tencent_docs_requests.row_cells_request(row_index, start_col_index, values)


def _legacy_single_write_back_row(
    row_index: int,
    check_status: str | None = None,
    read_count: int | None = None,
    comment_count: int | None = None,
    batch_status: str | None = None,
) -> None:
    tencent_docs_writeback.legacy_single_write_back_row(
        row_index,
        check_status=check_status,
        read_count=read_count,
        comment_count=comment_count,
        batch_status=batch_status,
    )


def _legacy_single_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    tencent_docs_writeback.legacy_single_initial_check_result(row_index, exists, account_name)

def _post_batch_update(requests_payload: list[dict[str, Any]], log_context: str) -> None:
    tencent_docs_client.post_batch_update(requests_payload, log_context)


def write_back_rows(rows: list[dict[str, Any]]) -> None:
    tencent_docs_writeback.write_back_rows(rows)


def write_back_row(
    row_index: int,
    check_status: str | None = None,
    read_count: int | None = None,
    comment_count: int | None = None,
    batch_status: str | None = None,
) -> None:
    tencent_docs_writeback.write_back_row(
        row_index,
        check_status=check_status,
        read_count=read_count,
        comment_count=comment_count,
        batch_status=batch_status,
    )


def write_initial_check_results(rows: list[dict[str, Any]]) -> None:
    tencent_docs_writeback.write_initial_check_results(rows)


def write_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    tencent_docs_writeback.write_initial_check_result(row_index, exists, account_name)


if __name__ == "__main__":
    candidates = fetch_and_save()
    for item in candidates:
        print(
            item["row_index"],
            item["post_time"].strftime("%Y-%m-%d %H:%M:%S"),
            item["url"],
        )
