"""Tencent Docs spreadsheet request builders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client


def cell_request(
    row_index: int,
    col_index: int,
    value: Any,
    background_color: dict[str, int] | None = None,
    text_format: dict[str, Any] | None = None,
    cell_format: dict[str, Any] | None = None,
    doc: client.DocInfo | None = None,
) -> dict[str, Any]:
    resolved_doc = doc or client.configured_doc()
    cell: dict[str, Any] = {
        "cellValue": {
            "text": "" if value is None else str(value),
        }
    }
    cell_format = dict(cell_format or {})
    if background_color:
        cell_format["backgroundColor"] = background_color
    if text_format:
        cell_format["textFormat"] = text_format
    if cell_format:
        cell["cellFormat"] = cell_format

    return {
        "updateRangeRequest": {
            "sheetId": resolved_doc.sheet_id,
            "gridData": {
                "startRow": row_index - 1,
                "startColumn": col_index,
                "rows": [{"values": [cell]}],
            },
        }
    }


def row_cells_request(
    row_index: int,
    start_col_index: int,
    values: list[Any],
    text_format: dict[str, Any] | None = None,
    cell_format: dict[str, Any] | None = None,
    doc: client.DocInfo | None = None,
) -> dict[str, Any]:
    resolved_doc = doc or client.configured_doc()
    cells = []
    for value in values:
        cell: dict[str, Any] = {"cellValue": {"text": "" if value is None else str(value)}}
        merged_cell_format = dict(cell_format or {})
        if text_format:
            merged_cell_format["textFormat"] = text_format
        if merged_cell_format:
            cell["cellFormat"] = merged_cell_format
        cells.append(cell)
    return {
        "updateRangeRequest": {
            "sheetId": resolved_doc.sheet_id,
            "gridData": {
                "startRow": row_index - 1,
                "startColumn": start_col_index,
                "rows": [
                    {
                        "values": cells
                    }
                ],
            },
        }
    }


def screenshot_cell_value(path_text: str | None) -> str:
    if not path_text:
        return ""

    path = Path(path_text)
    if Config.SCREENSHOT_PUBLIC_BASE_URL:
        try:
            relative = path.resolve().relative_to(Config.CAPTURE_DIR.resolve())
            return f"{Config.SCREENSHOT_PUBLIC_BASE_URL.rstrip('/')}/{relative.as_posix()}"
        except ValueError:
            return f"{Config.SCREENSHOT_PUBLIC_BASE_URL.rstrip('/')}/{path.name}"
    return str(path)


def screenshot_cell_request(row_index: int, path_text: str | None, doc: client.DocInfo | None = None) -> dict[str, Any]:
    resolved_doc = doc or client.configured_doc()
    value = screenshot_cell_value(path_text)
    if value.startswith(("http://", "https://")):
        cell = {
            "cellValue": {
                "link": {
                    "text": "截图",
                    "url": value,
                }
            }
        }
    else:
        cell = {"cellValue": {"text": value}}

    return {
        "updateRangeRequest": {
            "sheetId": resolved_doc.sheet_id,
            "gridData": {
                "startRow": row_index - 1,
                "startColumn": Config.QQ_COL_SCREENSHOT,
                "rows": [{"values": [cell]}],
            },
        }
    }


def can_upload_screenshot(path_text: str | None) -> bool:
    if not Config.QQ_UPLOAD_SCREENSHOTS or Config.QQ_COL_SCREENSHOT < 0 or not path_text:
        return False
    path = Path(path_text)
    return path.exists() and path.is_file()


def image_display_size(path: Path) -> tuple[float, float]:
    width = Config.QQ_IMAGE_INSERT_WIDTH
    height = Config.QQ_IMAGE_INSERT_HEIGHT
    if width > 0 and height > 0:
        return width, height

    with Image.open(path) as image:
        original_width, original_height = image.size

    if original_width <= 0 or original_height <= 0:
        return 160.0, 300.0

    if width > 0:
        return width, round(width * original_height / original_width, 2)
    if height > 0:
        return round(height * original_width / original_height, 2), height
    return float(original_width), float(original_height)


def screenshot_image_request(row_index: int, path_text: str, doc: client.DocInfo | None = None) -> dict[str, Any]:
    resolved_doc = doc or client.configured_doc()
    path = Path(path_text)
    image_id = client.upload_image(path)
    width, height = image_display_size(path)
    return {
        "insertImageRequest": {
            "sheetId": resolved_doc.sheet_id,
            "imageData": [
                {
                    "type": 1,
                    "imageId": image_id,
                    "row": row_index,
                    "col": Config.QQ_COL_SCREENSHOT + 1,
                    "width": width,
                    "height": height,
                }
            ],
        }
    }
