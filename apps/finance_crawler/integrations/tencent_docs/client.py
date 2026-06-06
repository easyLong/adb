"""Low-level Tencent Docs OpenAPI client helpers."""

from __future__ import annotations

import json
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse, urlunparse

import requests

from apps.finance_crawler.config import Config
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("tencent_docs_client")

BASE_URL = "https://docs.qq.com/openapi/spreadsheet/v3"
IMAGE_UPLOAD_URL = "https://docs.qq.com/openapi/resources/v2/images"


@dataclass(frozen=True)
class DocInfo:
    file_id: str
    sheet_id: str


@dataclass(frozen=True)
class DocUrlInfo:
    file_id: str
    sheet_id: str
    base_url: str


@dataclass(frozen=True)
class SheetInfo:
    file_id: str
    sheet_id: str
    title: str

    @property
    def doc(self) -> DocInfo:
        return DocInfo(self.file_id, self.sheet_id)


def parse_doc_url(url: str) -> DocInfo:
    parsed = urlparse(url)
    if "docs.qq.com" not in parsed.netloc:
        raise ValueError(f"不是腾讯文档链接: {url}")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"无法从链接提取 fileId: {url}")

    file_id = parts[-1]
    sheet_id = parse_qs(parsed.query).get("tab", [""])[0]
    if not sheet_id:
        raise ValueError("腾讯文档链接缺少 tab 参数，无法确定工作表 sheetId")

    return DocInfo(file_id=file_id, sheet_id=sheet_id)


def parse_doc_url_info(url: str) -> DocUrlInfo:
    parsed = urlparse(url)
    if "docs.qq.com" not in parsed.netloc:
        raise ValueError(f"not a Tencent Docs URL: {url}")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"cannot parse Tencent Docs file_id from URL: {url}")

    file_id = parts[-1]
    sheet_id = parse_qs(parsed.query).get("tab", [""])[0]
    base_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return DocUrlInfo(file_id=file_id, sheet_id=sheet_id, base_url=base_url)


def configured_doc() -> DocInfo:
    if Config.QQ_FILE_ID and Config.QQ_SHEET_ID:
        return DocInfo(Config.QQ_FILE_ID, Config.QQ_SHEET_ID)
    if not Config.QQ_DOC_URL:
        raise RuntimeError("TENCENT_DOC_URL is not configured")
    return parse_doc_url(Config.QQ_DOC_URL)


def _load_token_cache() -> dict[str, Any]:
    if Config.TOKEN_CACHE_FILE.exists():
        try:
            return json.loads(Config.TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_token_cache(token: str, expires_in: int) -> None:
    payload = {
        "access_token": token,
        "expires_at": time.time() + max(expires_in - 300, 60),
    }
    Config.TOKEN_CACHE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_access_token() -> str:
    if Config.QQ_ACCESS_TOKEN:
        return Config.QQ_ACCESS_TOKEN

    cache = _load_token_cache()
    if cache.get("access_token") and cache.get("expires_at", 0) > time.time():
        return cache["access_token"]

    if not Config.QQ_CLIENT_ID or not Config.QQ_CLIENT_SECRET:
        raise RuntimeError(
            "缺少腾讯文档凭证：请设置 TENCENT_DOC_ACCESS_TOKEN，"
            "或设置 TENCENT_DOC_CLIENT_ID/TENCENT_DOC_CLIENT_SECRET 自动换 token"
        )

    response = requests.post(
        Config.QQ_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": Config.QQ_CLIENT_ID,
            "client_secret": Config.QQ_CLIENT_SECRET,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"腾讯文档 token 响应缺少 access_token: {data}")

    _save_token_cache(token, int(data.get("expires_in", 7200)))
    return token


def headers() -> dict[str, str]:
    missing = []
    if not Config.QQ_CLIENT_ID:
        missing.append("TENCENT_DOC_CLIENT_ID")
    if not Config.QQ_OPEN_ID:
        missing.append("TENCENT_DOC_OPEN_ID")
    if missing:
        raise RuntimeError("缺少腾讯文档请求头配置: " + ", ".join(missing))

    return {
        "Access-Token": get_access_token(),
        "Client-Id": Config.QQ_CLIENT_ID,
        "Open-Id": Config.QQ_OPEN_ID,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def multipart_headers() -> dict[str, str]:
    output = headers()
    output.pop("Content-Type", None)
    return output


def check_response(data: dict[str, Any]) -> None:
    ret = data.get("ret", data.get("code", 0))
    if ret not in (0, "0", None):
        raise RuntimeError(f"腾讯文档 API 返回错误: {data}")


def fetch_file_sheets(file_id: str | None = None) -> list[SheetInfo]:
    resolved_file_id = file_id or configured_doc().file_id
    url = f"{BASE_URL}/files/{resolved_file_id}"
    response = requests.get(
        url,
        headers=headers(),
        params={"concise": 1},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    check_response(data)

    properties = data.get("data", {}).get("properties", data.get("properties", []))
    sheets: list[SheetInfo] = []
    for item in properties:
        sheet_id = str(item.get("sheetId") or "").strip()
        if not sheet_id:
            continue
        sheets.append(
            SheetInfo(
                file_id=resolved_file_id,
                sheet_id=sheet_id,
                title=str(item.get("title") or "").strip(),
            )
        )
    return sheets


def fetch_sheet_title(doc: DocInfo | None = None) -> str:
    resolved_doc = doc or configured_doc()
    for item in fetch_file_sheets(resolved_doc.file_id):
        if item.sheet_id == resolved_doc.sheet_id:
            logger.info("current sheet: %s (%s)", item.title, resolved_doc.sheet_id)
            return item.title

    logger.warning("sheet title not found for sheetId=%s", resolved_doc.sheet_id)
    return ""


def cell_to_text(cell: dict[str, Any] | Any) -> str:
    if not isinstance(cell, dict):
        return "" if cell is None else str(cell)

    value = cell.get("cellValue", cell)
    if not isinstance(value, dict):
        return "" if value is None else str(value)

    if "text" in value:
        return str(value.get("text") or "").strip()
    if "number" in value:
        return str(value.get("number") or "").strip()
    if "time" in value and isinstance(value["time"], dict):
        time_value = value["time"]
        year = int(time_value.get("year") or 0)
        month = int(time_value.get("month") or 0)
        day = int(time_value.get("day") or 0)
        hour = int(time_value.get("hour") or 0)
        minute = int(time_value.get("minute") or 0)
        second = int(time_value.get("second") or 0)
        time_text = f"{hour:02d}:{minute:02d}:{second:02d}"
        if year and month and day:
            return f"{year:04d}-{month:02d}-{day:02d} {time_text}"
        return time_text
    if "link" in value and isinstance(value["link"], dict):
        return str(value["link"].get("url") or value["link"].get("text") or "").strip()
    if "location" in value and isinstance(value["location"], dict):
        return str(value["location"].get("name") or "").strip()
    return ""


def grid_to_rows(grid_data: dict[str, Any]) -> tuple[list[list[str]], int]:
    rows = []
    for row in grid_data.get("rows", []):
        values = row.get("values", []) if isinstance(row, dict) else []
        rows.append([cell_to_text(cell) for cell in values])
    return rows, int(grid_data.get("startRow", 0))


def fetch_raw_grid(range_a1: str | None = None, doc: DocInfo | None = None) -> dict[str, Any]:
    resolved_doc = doc or configured_doc()
    range_text = range_a1 or Config.QQ_READ_RANGE
    encoded_range = quote(range_text, safe=":")
    url = f"{BASE_URL}/files/{resolved_doc.file_id}/{resolved_doc.sheet_id}/{encoded_range}"

    response = requests.get(url, headers=headers(), timeout=20)
    response.raise_for_status()
    data = response.json()
    check_response(data)
    return data.get("data", {}).get("gridData", data.get("gridData", {}))


def fetch_grid(range_a1: str | None = None, doc: DocInfo | None = None) -> tuple[list[list[str]], int]:
    range_text = range_a1 or Config.QQ_READ_RANGE
    chunked_ranges = _chunk_a1_range(range_text, Config.QQ_READ_CHUNK_ROWS)
    if chunked_ranges:
        rows: list[list[str]] = []
        first_start_row: int | None = None
        for chunk_range in chunked_ranges:
            try:
                chunk_rows, chunk_start_row = _fetch_grid_once(chunk_range, doc=doc)
            except RuntimeError as exc:
                if rows and _is_range_boundary_error(exc):
                    logger.info("Tencent Docs range boundary reached at %s: %s", chunk_range, exc)
                    break
                raise
            if first_start_row is None:
                first_start_row = chunk_start_row
            rows.extend(chunk_rows)
        logger.info("read Tencent Docs rows=%s range=%s chunks=%s", len(rows), range_text, len(chunked_ranges))
        return rows, first_start_row or 0
    return _fetch_grid_once(range_text, doc=doc)


def _fetch_grid_once(range_text: str, doc: DocInfo | None = None) -> tuple[list[list[str]], int]:
    grid_data = fetch_raw_grid(range_text, doc=doc)
    rows, start_row = grid_to_rows(grid_data)
    logger.info("read Tencent Docs rows=%s range=%s", len(rows), range_text)
    return rows, start_row


def _chunk_a1_range(range_text: str, chunk_rows: int) -> list[str]:
    if chunk_rows <= 0:
        return []
    match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", range_text.strip(), re.IGNORECASE)
    if not match:
        return []

    start_col, start_row_text, end_col, end_row_text = match.groups()
    start_row = int(start_row_text)
    end_row = int(end_row_text)
    if end_row - start_row + 1 <= chunk_rows:
        return []

    ranges = []
    current = start_row
    while current <= end_row:
        chunk_end = min(current + chunk_rows - 1, end_row)
        ranges.append(f"{start_col}{current}:{end_col}{chunk_end}")
        current = chunk_end + 1
    return ranges


def _is_range_boundary_error(exc: Exception) -> bool:
    text = str(exc)
    return "invalid param error: 'range' invalid" in text or "RangeSize Validate error" in text


def upload_image(image_path: str | Path) -> str:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"screenshot not found: {path}")

    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    with path.open("rb") as file:
        response = requests.post(
            IMAGE_UPLOAD_URL,
            headers=multipart_headers(),
            files={"image": (path.name, file, mime_type)},
            timeout=Config.QQ_IMAGE_UPLOAD_TIMEOUT,
        )
    response.raise_for_status()
    data = response.json()
    check_response(data)

    payload = data.get("data", data)
    image_id = payload.get("imageID") or payload.get("imageId")
    if not image_id:
        raise RuntimeError(f"Tencent Docs upload image response missing imageID: {data}")
    return str(image_id)


def post_batch_update(requests_payload: list[dict[str, Any]], log_context: str, doc: DocInfo | None = None) -> None:
    if not requests_payload:
        return

    resolved_doc = doc or configured_doc()
    url = f"{BASE_URL}/files/{resolved_doc.file_id}/batchUpdate"
    chunk_size = max(Config.QQ_BATCH_UPDATE_SIZE, 1)
    for index in range(0, len(requests_payload), chunk_size):
        chunk = requests_payload[index : index + chunk_size]
        response = requests.post(
            url,
            headers=headers(),
            json={"requests": chunk},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        check_response(data)
        logger.info("Tencent Docs batchUpdate %s requests=%s", log_context, len(chunk))
        time.sleep(Config.QQ_WRITE_DELAY)
