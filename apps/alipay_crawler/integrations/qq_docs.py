"""Tencent Docs read/write integration."""

from __future__ import annotations

import json
import mimetypes
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import requests
from PIL import Image

from apps.alipay_crawler.config import Config
from apps.alipay_crawler.storage.db import log_task, upsert_post
from apps.alipay_crawler.utils.link_source import detect_link_source
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("qq_docs")

BASE_URL = "https://docs.qq.com/openapi/spreadsheet/v3"
IMAGE_UPLOAD_URL = "https://docs.qq.com/openapi/resources/v2/images"


@dataclass(frozen=True)
class DocInfo:
    file_id: str
    sheet_id: str


@dataclass(frozen=True)
class SheetInfo:
    sheet_id: str
    title: str


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


def _configured_doc() -> DocInfo:
    if Config.QQ_FILE_ID and Config.QQ_SHEET_ID:
        return DocInfo(Config.QQ_FILE_ID, Config.QQ_SHEET_ID)
    return parse_doc_url(Config.QQ_DOC_URL)


def configured_doc() -> DocInfo:
    """Return the configured Tencent Docs file and sheet identifiers."""
    return _configured_doc()


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


def _headers() -> dict[str, str]:
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


def _check_response(data: dict[str, Any]) -> None:
    # Tencent Docs examples use ret/msg; some pages describe code/message.
    ret = data.get("ret", data.get("code", 0))
    if ret not in (0, "0", None):
        raise RuntimeError(f"腾讯文档 API 返回错误: {data}")


def fetch_sheet_title() -> str:
    doc = _configured_doc()
    url = f"{BASE_URL}/files/{doc.file_id}"
    response = requests.get(
        url,
        headers=_headers(),
        params={"concise": 1},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    _check_response(data)

    properties = data.get("data", {}).get("properties", data.get("properties", []))
    for item in properties:
        if item.get("sheetId") == doc.sheet_id:
            title = str(item.get("title") or "").strip()
            logger.info("当前工作表: %s (%s)", title, doc.sheet_id)
            return title

    logger.warning("没有在元数据中找到 sheetId=%s 的标题", doc.sheet_id)
    return ""


def _cell_to_text(cell: dict[str, Any] | Any) -> str:
    if not isinstance(cell, dict):
        return "" if cell is None else str(cell)

    value = cell.get("cellValue", cell)
    if not isinstance(value, dict):
        return "" if value is None else str(value)

    if "text" in value:
        return str(value.get("text") or "").strip()
    if "number" in value:
        return str(value.get("number") or "").strip()
    if "link" in value and isinstance(value["link"], dict):
        return str(value["link"].get("url") or value["link"].get("text") or "").strip()
    if "location" in value and isinstance(value["location"], dict):
        return str(value["location"].get("name") or "").strip()
    return ""


def _grid_to_rows(grid_data: dict[str, Any]) -> tuple[list[list[str]], int]:
    rows = []
    for row in grid_data.get("rows", []):
        values = row.get("values", []) if isinstance(row, dict) else []
        rows.append([_cell_to_text(cell) for cell in values])
    return rows, int(grid_data.get("startRow", 0))


def fetch_grid(range_a1: str | None = None) -> tuple[list[list[str]], int]:
    doc = _configured_doc()
    range_text = range_a1 or Config.QQ_READ_RANGE
    encoded_range = quote(range_text, safe=":")
    url = f"{BASE_URL}/files/{doc.file_id}/{doc.sheet_id}/{encoded_range}"

    response = requests.get(url, headers=_headers(), timeout=20)
    response.raise_for_status()
    data = response.json()
    _check_response(data)

    grid_data = data.get("data", {}).get("gridData", data.get("gridData", {}))
    rows, start_row = _grid_to_rows(grid_data)
    logger.info("读取腾讯文档 %s 行，范围 %s", len(rows), range_text)
    return rows, start_row


def fetch_rows() -> list[list[str]]:
    rows, _ = fetch_grid()
    return rows


def get_row_index_map() -> dict[str, int]:
    rows, start_row = fetch_grid()
    mapping: dict[str, int] = {}
    for offset, row in enumerate(rows):
        if len(row) <= Config.QQ_COL_URL:
            continue
        url = row[Config.QQ_COL_URL].strip()
        if url.startswith(("http://", "https://", "alipay://", "alipays://", "afwealth://")):
            # Tencent grid startRow is zero-based; sheet row number is one-based.
            mapping[url] = start_row + offset + 1
    return mapping


def resolve_row_index_for_url(
    url: str,
    preferred_row_index: int | None = None,
    rows: list[list[str]] | None = None,
    start_row: int | None = None,
) -> int | None:
    """Resolve the current sheet row for a URL and guard against stale rows."""
    if rows is None or start_row is None:
        rows, start_row = fetch_grid()

    target = (url or "").strip()
    if not target:
        return None

    if Config.VALIDATE_DOC_ROW_BEFORE_WRITE and preferred_row_index:
        offset = preferred_row_index - start_row - 1
        if 0 <= offset < len(rows):
            row = rows[offset]
            if len(row) > Config.QQ_COL_URL and row[Config.QQ_COL_URL].strip() == target:
                return preferred_row_index

    matches: list[int] = []
    for offset, row in enumerate(rows):
        if len(row) <= Config.QQ_COL_URL:
            continue
        if row[Config.QQ_COL_URL].strip() == target:
            matches.append(start_row + offset + 1)

    if len(matches) > 1:
        raise RuntimeError(f"duplicate URL in Tencent Docs, skip unsafe writeback: {target}")
    return matches[0] if matches else None


def _parse_sheet_date(sheet_title: str) -> tuple[int, int, int] | None:
    text = sheet_title or ""
    match = re.search(r"(?<!\d)(?P<month>\d{2})(?P<day>\d{2})(?!\d)", text)
    if not match:
        match = re.search(r"(?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})", text)
    if not match:
        return None

    year_match = re.search(r"(?P<year>20\d{2})", text)
    year = int(year_match.group("year")) if year_match else 2026
    return year, int(match.group("month")), int(match.group("day"))


def _parse_time_from_cell(value: str) -> tuple[int, int, int] | None:
    text = (value or "").strip()
    if not text:
        return None

    text = text.replace("年", "-").replace("月", "-").replace("日", " ")
    text = re.sub(r"\s+", " ", text).strip()

    range_match = re.search(
        r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*[-~－—]\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?)",
        text,
    )
    if range_match:
        # Use the earliest time in a range, for example 10:30 in 10:30-11:30.
        start_time = range_match.group("start")
        parts = [int(part) for part in start_time.split(":")]
        return parts[0], parts[1], parts[2] if len(parts) == 3 else 0

    time_only_match = re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text)
    if time_only_match:
        parts = [int(part) for part in text.split(":")]
        return parts[0], parts[1], parts[2] if len(parts) == 3 else 0

    embedded_time = re.search(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?", text)
    if embedded_time:
        return (
            int(embedded_time.group("hour")),
            int(embedded_time.group("minute")),
            int(embedded_time.group("second") or 0),
        )

    return None


def _parse_post_time(value: str, sheet_title: str = "") -> datetime | None:
    sheet_date = _parse_sheet_date(sheet_title)
    cell_time = _parse_time_from_cell(value)
    if sheet_date and cell_time:
        return datetime(*sheet_date, *cell_time)

    text = (value or "").strip()
    if not text:
        return None

    text = text.replace("年", "-").replace("月", "-").replace("日", " ")
    text = re.sub(r"\s+", " ", text).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    match = re.search(r"(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})", text)
    if match:
        month, day, hour, minute = map(int, match.groups())
        now = datetime.now()
        return datetime(now.year, month, day, hour, minute)

    return None


def _eligible_candidates(
    rows: list[list[str]],
    start_row: int,
    sheet_title: str = "",
) -> list[dict[str, Any]]:
    now = datetime.now()
    cutoff = now - timedelta(hours=Config.POST_ELIGIBLE_HOURS)
    candidates: list[dict[str, Any]] = []

    # Treat the first row in A1:F1000 as header.
    for offset, row in enumerate(rows[1:], start=1):
        row_index = start_row + offset + 1
        if len(row) <= max(Config.QQ_COL_URL, Config.QQ_COL_POST_TIME):
            continue

        url = row[Config.QQ_COL_URL].strip()
        post_time = _parse_post_time(row[Config.QQ_COL_POST_TIME], sheet_title)
        if not url or not post_time:
            continue
        if not url.startswith(("http://", "https://", "alipay://", "alipays://", "afwealth://")):
            continue
        if post_time > cutoff:
            continue

        candidates.append(
            {
                "url": url,
                "source_app": detect_link_source(url),
                "post_time": post_time,
                "row_index": row_index,
                "age_hours": round((now - post_time).total_seconds() / 3600, 2),
            }
        )

    return candidates


def eligible_candidates(
    rows: list[list[str]],
    start_row: int,
    sheet_title: str = "",
) -> list[dict[str, Any]]:
    """Build eligible crawl candidates from sheet rows."""
    return _eligible_candidates(rows, start_row, sheet_title)


def _save_latest_candidates(candidates: list[dict[str, Any]]) -> None:
    serializable = []
    for item in candidates:
        copied = dict(item)
        copied["post_time"] = copied["post_time"].isoformat(sep=" ")
        serializable.append(copied)

    Config.LATEST_CANDIDATES_FILE.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_latest_candidates(candidates: list[dict[str, Any]]) -> None:
    """Persist the latest candidate snapshot for debugging."""
    _save_latest_candidates(candidates)


def fetch_and_save(limit: int | None = None) -> list[dict[str, Any]]:
    start = time.time()
    task_limit = Config.FETCH_LIMIT if limit is None else limit
    doc = _configured_doc()

    try:
        sheet_title = fetch_sheet_title()
        rows, start_row = fetch_grid()
        candidates = _eligible_candidates(rows, start_row, sheet_title)
        if task_limit and task_limit > 0:
            candidates = candidates[:task_limit]
        _save_latest_candidates(candidates)

        new_count = 0
        by_source: dict[str, int] = {}
        for item in candidates:
            inserted = upsert_post(
                item["url"],
                item["post_time"],
                row_index=item["row_index"],
                file_id=doc.file_id,
                sheet_id=doc.sheet_id,
                source_app=item["source_app"],
            )
            by_source[item["source_app"]] = by_source.get(item["source_app"], 0) + 1
            if inserted:
                new_count += 1

        duration = time.time() - start
        source_summary = ", ".join(f"{key}={value}" for key, value in sorted(by_source.items())) or "none"
        msg = (
            f"eligible={len(candidates)}, new={new_count}, "
            f"limit={task_limit or 'all'}, older_than={Config.POST_ELIGIBLE_HOURS}h, "
            f"sources={source_summary}"
        )
        logger.info("腾讯文档同步完成: %s", msg)
        log_task("fetch_docs", "success", msg, duration)
        return candidates
    except Exception as exc:
        duration = time.time() - start
        logger.exception("腾讯文档同步失败")
        log_task("fetch_docs", "error", str(exc), duration)
        raise


def _cell_request(
    row_index: int,
    col_index: int,
    value: Any,
    background_color: dict[str, int] | None = None,
    text_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # row_index passed around inside this project is 1-based.
    cell: dict[str, Any] = {
        "cellValue": {
            "text": "" if value is None else str(value),
        }
    }
    cell_format = {}
    if background_color:
        cell_format["backgroundColor"] = background_color
    if text_format:
        cell_format["textFormat"] = text_format
    if cell_format:
        cell["cellFormat"] = cell_format

    return {
        "updateRangeRequest": {
            "sheetId": _configured_doc().sheet_id,
            "gridData": {
                "startRow": row_index - 1,
                "startColumn": col_index,
                "rows": [
                    {
                        "values": [cell]
                    }
                ],
            },
        }
    }


def _multipart_headers() -> dict[str, str]:
    headers = _headers()
    headers.pop("Content-Type", None)
    return headers


def _screenshot_cell_value(path_text: str | None) -> str:
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


def _screenshot_cell_request(row_index: int, path_text: str | None) -> dict[str, Any]:
    value = _screenshot_cell_value(path_text)
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
            "sheetId": _configured_doc().sheet_id,
            "gridData": {
                "startRow": row_index - 1,
                "startColumn": Config.QQ_COL_SCREENSHOT,
                "rows": [{"values": [cell]}],
            },
        }
    }


def _can_upload_screenshot(path_text: str | None) -> bool:
    if not Config.QQ_UPLOAD_SCREENSHOTS or Config.QQ_COL_SCREENSHOT < 0 or not path_text:
        return False
    path = Path(path_text)
    return path.exists() and path.is_file()


def _image_display_size(path: Path) -> tuple[float, float]:
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


def upload_image(image_path: str | Path) -> str:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"screenshot not found: {path}")

    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    with path.open("rb") as file:
        response = requests.post(
            IMAGE_UPLOAD_URL,
            headers=_multipart_headers(),
            files={"image": (path.name, file, mime_type)},
            timeout=Config.QQ_IMAGE_UPLOAD_TIMEOUT,
        )
    response.raise_for_status()
    data = response.json()
    _check_response(data)

    payload = data.get("data", data)
    image_id = payload.get("imageID") or payload.get("imageId")
    if not image_id:
        raise RuntimeError(f"Tencent Docs upload image response missing imageID: {data}")
    return str(image_id)


def _screenshot_image_request(row_index: int, path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    image_id = upload_image(path)
    width, height = _image_display_size(path)
    return {
        "insertImageRequest": {
            "sheetId": _configured_doc().sheet_id,
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


def _post_screenshot_images(rows: list[tuple[int, str]]) -> list[dict[str, Any]]:
    requests_with_fallback: list[tuple[dict[str, Any], dict[str, Any]]] = []
    fallback_requests: list[dict[str, Any]] = []

    for row_index, path_text in rows:
        try:
            requests_with_fallback.append(
                (
                    _screenshot_image_request(row_index, path_text),
                    _screenshot_cell_request(row_index, path_text),
                )
            )
            logger.info("Tencent Docs uploaded screenshot row=%s path=%s", row_index, path_text)
            if Config.QQ_IMAGE_UPLOAD_DELAY > 0:
                time.sleep(Config.QQ_IMAGE_UPLOAD_DELAY)
        except Exception as exc:
            logger.warning("Tencent Docs screenshot upload failed row=%s: %s", row_index, exc)
            fallback_requests.append(_screenshot_cell_request(row_index, path_text))

    if not requests_with_fallback:
        return fallback_requests

    doc = _configured_doc()
    url = f"{BASE_URL}/files/{doc.file_id}/batchUpdate"
    chunk_size = max(Config.QQ_BATCH_UPDATE_SIZE, 1)
    for index in range(0, len(requests_with_fallback), chunk_size):
        chunk = requests_with_fallback[index : index + chunk_size]
        try:
            response = requests.post(
                url,
                headers=_headers(),
                json={"requests": [request for request, _ in chunk]},
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            _check_response(data)
            logger.info("Tencent Docs inserted screenshot images requests=%s", len(chunk))
            time.sleep(Config.QQ_WRITE_DELAY)
        except Exception as exc:
            logger.warning("Tencent Docs insert screenshot images failed: %s", exc)
            fallback_requests.extend(fallback for _, fallback in chunk)

    return fallback_requests


def _row_cells_request(
    row_index: int,
    start_col_index: int,
    values: list[Any],
) -> dict[str, Any]:
    return {
        "updateRangeRequest": {
            "sheetId": _configured_doc().sheet_id,
            "gridData": {
                "startRow": row_index - 1,
                "startColumn": start_col_index,
                "rows": [
                    {
                        "values": [
                            {"cellValue": {"text": "" if value is None else str(value)}}
                            for value in values
                        ]
                    }
                ],
            },
        }
    }


def _legacy_single_write_back_row(
    row_index: int,
    check_status: str | None = None,
    read_count: int | None = None,
    comment_count: int | None = None,
    batch_status: str | None = None,
) -> None:
    requests_payload = []
    if check_status is not None:
        requests_payload.append(_cell_request(row_index, Config.QQ_COL_CHECK_STATUS, check_status))
    if read_count is not None:
        requests_payload.append(_cell_request(row_index, Config.QQ_COL_READ_COUNT, read_count))
    if comment_count is not None:
        requests_payload.append(_cell_request(row_index, Config.QQ_COL_COMMENT_COUNT, comment_count))
    if batch_status is not None:
        requests_payload.append(_cell_request(row_index, Config.QQ_COL_BATCH_STATUS, batch_status))

    if not requests_payload:
        return

    doc = _configured_doc()
    url = f"{BASE_URL}/files/{doc.file_id}/batchUpdate"
    response = requests.post(
        url,
        headers=_headers(),
        json={"requests": requests_payload[:5]},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    _check_response(data)

    logger.info("写回腾讯文档第 %s 行: %s", row_index, requests_payload)
    time.sleep(Config.QQ_WRITE_DELAY)


def _legacy_single_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    if exists:
        request = _cell_request(row_index, Config.QQ_COL_ACCOUNT_NAME, account_name or "")
        requests_payload = [request]
    else:
        yellow = {"red": 255, "green": 255, "blue": 0, "alpha": 255}
        red_bold = {
            "bold": True,
            "color": {"red": 255, "green": 0, "blue": 0, "alpha": 255},
        }
        requests_payload = [
            _cell_request(
                row_index,
                Config.QQ_COL_ACCOUNT_NAME,
                "N",
                background_color=yellow,
                text_format=red_bold,
            )
        ]

    doc = _configured_doc()
    url = f"{BASE_URL}/files/{doc.file_id}/batchUpdate"
    response = requests.post(
        url,
        headers=_headers(),
        json={"requests": requests_payload},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    _check_response(data)
    logger.info("initial check writeback row=%s exists=%s account=%s", row_index, exists, account_name)
    time.sleep(Config.QQ_WRITE_DELAY)

def _post_batch_update(requests_payload: list[dict[str, Any]], log_context: str) -> None:
    if not requests_payload:
        return

    doc = _configured_doc()
    url = f"{BASE_URL}/files/{doc.file_id}/batchUpdate"
    chunk_size = max(Config.QQ_BATCH_UPDATE_SIZE, 1)
    for index in range(0, len(requests_payload), chunk_size):
        chunk = requests_payload[index : index + chunk_size]
        response = requests.post(
            url,
            headers=_headers(),
            json={"requests": chunk},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        _check_response(data)
        logger.info("Tencent Docs batchUpdate %s requests=%s", log_context, len(chunk))
        time.sleep(Config.QQ_WRITE_DELAY)


def write_back_rows(rows: list[dict[str, Any]]) -> None:
    requests_payload: list[dict[str, Any]] = []
    screenshot_upload_rows: list[tuple[int, str]] = []
    for item in rows:
        row_index = int(item["row_index"])
        screenshot_path = item.get("screenshot_path")
        should_upload_screenshot = _can_upload_screenshot(screenshot_path)
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
                values.append(_screenshot_cell_value(screenshot_path))
            requests_payload.append(
                _row_cells_request(
                    row_index,
                    Config.QQ_COL_READ_COUNT,
                    values,
                )
            )
            if should_upload_screenshot:
                screenshot_upload_rows.append((row_index, str(screenshot_path)))
            elif Config.QQ_COL_SCREENSHOT >= 0 and screenshot_path and not screenshot_written_with_row:
                requests_payload.append(_screenshot_cell_request(row_index, screenshot_path))
            continue
        if item.get("check_status") is not None:
            requests_payload.append(_cell_request(row_index, Config.QQ_COL_CHECK_STATUS, item["check_status"]))
        if item.get("read_count") is not None:
            requests_payload.append(_cell_request(row_index, Config.QQ_COL_READ_COUNT, item["read_count"]))
        if item.get("comment_count") is not None:
            requests_payload.append(_cell_request(row_index, Config.QQ_COL_COMMENT_COUNT, item["comment_count"]))
        if item.get("batch_status") is not None:
            requests_payload.append(_cell_request(row_index, Config.QQ_COL_BATCH_STATUS, item["batch_status"]))
        if should_upload_screenshot:
            screenshot_upload_rows.append((row_index, str(screenshot_path)))
        elif Config.QQ_COL_SCREENSHOT >= 0 and screenshot_path:
            requests_payload.append(_screenshot_cell_request(row_index, screenshot_path))

    _post_batch_update(requests_payload, "write_back_rows")
    fallback_requests = _post_screenshot_images(screenshot_upload_rows)
    _post_batch_update(fallback_requests, "screenshot_fallback")


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
                _cell_request(row_index, Config.QQ_COL_ACCOUNT_NAME, item.get("account_name") or "")
            )
        else:
            requests_payload.append(
                _cell_request(
                    row_index,
                    Config.QQ_COL_ACCOUNT_NAME,
                    "N",
                    background_color=yellow,
                    text_format=red_bold,
                )
            )

    _post_batch_update(requests_payload, "initial_check")


def write_initial_check_result(
    row_index: int,
    exists: bool,
    account_name: str | None = None,
) -> None:
    write_initial_check_results(
        [{"row_index": row_index, "exists": exists, "account_name": account_name}]
    )


if __name__ == "__main__":
    candidates = fetch_and_save()
    for item in candidates:
        print(
            item["row_index"],
            item["post_time"].strftime("%Y-%m-%d %H:%M:%S"),
            item["url"],
        )
