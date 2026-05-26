"""ADB crawler wrapper built on the validated capture flow."""

from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.alipay_crawler.alipay.capture_engine import (
    append_jsonl,
    collect_ui_records,
    connect_uiautomator,
    current_screen_signature,
    is_lockscreen_showing,
    open_alipay_link,
    resolve_embedded_alipay_scheme,
    save_text,
    scroll_forward,
    set_device_awake,
    stable_key,
    try_ocr,
)
from apps.alipay_crawler.config import Config
from apps.alipay_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("crawler")

_device = None
_device_serial: str | None = None
_last_device_prepare_at = 0.0

NOT_FOUND_KEYWORDS = [
    "内容不存在",
    "内容不见了",
    "已被删除",
    "页面不存在",
    "帖子不见了",
    "该内容无法查看",
]
ERROR_KEYWORDS = [
    "网络不给力",
    "加载失败",
    "请求超时",
    "连接失败",
    "稍后再试",
]
OK_KEYWORDS = [
    "阅读",
    "评论",
    "点赞",
    "关注",
    "理财",
]


def _prepare_adb_path() -> None:
    adb_dir = str(Path(Config.ADB_PATH).resolve().parent)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if adb_dir not in path_parts:
        os.environ["PATH"] = adb_dir + os.pathsep + os.environ.get("PATH", "")


def reset_device_session() -> None:
    global _device, _device_serial
    _device = None
    _device_serial = None


def device():
    global _device, _device_serial
    serial = assert_device_ready()
    if _device is None or _device_serial != serial:
        _device_serial = serial
        _device = connect_uiautomator(serial)
        try:
            info = _device.info
        except Exception as exc:
            reset_device_session()
            raise DeviceUnavailable(f"uiautomator2 device session is unavailable: {exc}") from exc
        logger.info(
            "device connected: %s (%sx%s)",
            info.get("productName", "unknown"),
            info.get("displayWidth"),
            info.get("displayHeight"),
        )
    return _device


def _prepare_device_if_needed(serial: str) -> None:
    global _last_device_prepare_at
    now = time.monotonic()
    if now - _last_device_prepare_at < Config.DEVICE_PREPARE_INTERVAL_SECONDS:
        return
    set_device_awake(serial)
    if is_lockscreen_showing(serial):
        raise RuntimeError("device is locked; unlock the phone and retry")
    _last_device_prepare_at = now


def resolve_short_url(short_url: str) -> str:
    _prepare_adb_path()
    resolved = resolve_embedded_alipay_scheme(short_url)
    return resolved or short_url


def open_url(url: str) -> None:
    _prepare_adb_path()
    serial = assert_device_ready()
    _prepare_device_if_needed(serial)
    open_alipay_link(url, serial=serial)
    time.sleep(Config.PAGE_LOAD_WAIT)


def _dump_records() -> list[dict[str, Any]]:
    xml_text = device().dump_hierarchy(compressed=False)
    return collect_ui_records(xml_text, 0)


def read_texts_from_screen() -> list[str]:
    texts: list[str] = []
    for record in _dump_records():
        for key in ("text", "content_desc"):
            value = (record.get(key) or "").strip()
            if value and len(value) > 1:
                texts.append(value)
    return texts


def detect_page_status() -> tuple[str, str | None]:
    texts = read_texts_from_screen()
    joined = "\n".join(texts)

    for keyword in NOT_FOUND_KEYWORDS:
        if keyword in joined:
            return "not_found", keyword
    for keyword in ERROR_KEYWORDS:
        if keyword in joined:
            return "error", keyword
    if any(keyword in joined for keyword in OK_KEYWORDS) or len(texts) >= 5:
        return "success", None
    return "error", "page status is unknown or too few controls were found"


def extract_account_name(texts: list[str]) -> str:
    ignore_exact = {
        "关注",
        "已关注",
        "评论",
        "阅读",
        "点赞",
        "分享",
        "收藏",
        "回复",
        "打开",
        "展开",
        "查看更多",
        "头像",
        "返回",
        "更多",
    }
    ignore_contains = {
        "支付宝",
        "蚂蚁财富",
        "理财",
        "基金",
        "阅读",
        "评论",
        "点赞",
        "关注",
        "开启护眼模式",
        "NFC",
        "蓝牙",
        "手机信号",
        "正在充电",
        "振铃器",
    }

    def usable(text: str) -> bool:
        cleaned = text.strip()
        if not cleaned or len(cleaned) > 30:
            return False
        if cleaned in ignore_exact:
            return False
        if any(word in cleaned for word in ignore_contains):
            return False
        if re.fullmatch(r"\d{1,2}:\d{2}", cleaned):
            return False
        if re.search(r"https?://|ur\.alipay\.com|\d{4}-\d{2}-\d{2}", cleaned):
            return False
        if re.search(r"^\d+$", cleaned):
            return False
        return True

    for index, text in enumerate(texts[:40]):
        if text.strip() != "头像":
            continue
        for candidate in texts[index + 1 : index + 6]:
            if usable(candidate):
                return candidate.strip()

    for text in texts[:40]:
        cleaned = text.strip()
        if usable(cleaned):
            return cleaned
    return ""


def check_post_exists_and_account(post_id: int) -> dict[str, Any]:
    time.sleep(1.0)
    status, error_msg = detect_page_status()
    if status == "not_found":
        return {"status": "not_found", "exists": False, "account_name": None, "error": error_msg}
    if status == "error":
        return {"status": "error", "exists": False, "account_name": None, "error": error_msg}

    texts = read_texts_from_screen()
    account_name = extract_account_name(texts)
    return {"status": "success", "exists": True, "account_name": account_name, "error": None}


def take_screenshot(post_id: int) -> str | None:
    path = Config.SCREENSHOT_DIR / f"post_{post_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    try:
        device().screenshot(str(path))
        return str(path)
    except Exception as exc:
        logger.warning("screenshot failed: %s", exc)
        return None


def _parse_count_token(raw: str) -> int:
    text = re.sub(r"\s+", "", raw.replace(",", "")).lower()
    match = re.fullmatch(r"(?P<num>\d+(?:\.\d+)?)(?P<unit>[万wk千]?)", text)
    if not match:
        return 0

    value = float(match.group("num"))
    unit = match.group("unit")
    if unit in {"万", "w"}:
        value *= 10000
    elif unit == "k":
        value *= 1000
    elif unit == "千":
        value *= 1000
    return int(value)


def _number_candidates(texts: list[str]) -> list[str]:
    candidates: list[str] = []
    cleaned = [item.strip() for item in texts if item and item.strip()]
    candidates.extend(cleaned)
    for index in range(max(len(cleaned) - 1, 0)):
        candidates.append("".join(cleaned[index : index + 2]))
    return candidates


def _current_post_scope_texts(texts: list[str]) -> list[str]:
    scope: list[str] = []
    after_latest_count: int | None = None
    for text in texts:
        cleaned = (text or "").strip()
        if not cleaned:
            continue
        scope.append(cleaned)
        if "暂无评论" in cleaned or "点击抢首评" in cleaned or "说说你的想法" in cleaned:
            break
        if after_latest_count is not None:
            after_latest_count += 1
            if after_latest_count >= 4:
                break
        if cleaned == "最新":
            after_latest_count = 0
    return scope


def _is_post_content_stop(text: str) -> bool:
    if text in {"评论", "转发", "热度", "最新", "点赞", "返回", "更多"}:
        return True
    return any(
        text.startswith(prefix)
        for prefix in (
            "来自以下讨论区",
            "风险提示",
            "暂无评论",
            "点击抢首评",
            "说说你的想法",
        )
    )


def _is_post_content_noise(text: str) -> bool:
    if text in {"头像", "关注", "已关注", "阅读", "浏览", "查看", "评论", "转发", "点赞"}:
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d{1,2}[-/]\d{1,2}\s*\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if len(text) <= 3 and text in {"北京", "上海", "天津", "重庆", "福建", "广东", "江苏", "浙江", "山东", "四川", "河南", "河北", "湖南", "湖北"}:
        return True
    return False


def extract_post_content(texts: list[str]) -> str:
    content_parts: list[str] = []
    for text in _current_post_scope_texts(texts):
        cleaned = (text or "").strip()
        if not cleaned:
            continue
        if _is_post_content_stop(cleaned):
            break
        if _is_post_content_noise(cleaned):
            continue
        # The first long/business text after author metadata is the post body.
        if len(cleaned) < 8 and not content_parts:
            continue
        content_parts.append(cleaned)
    return "\n".join(dict.fromkeys(content_parts))


def _normalize_count_text(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    labels = "\u9605\u8bfb|\u6d4f\u89c8|\u67e5\u770b|\u8bc4\u8bba|\u56de\u590d|\u7559\u8a00"
    return re.sub(
        rf"(?:\d{{1,2}}[-/]\d{{1,2}})?(?P<time>\d{{1,2}}:\d{{2}})(?P<num>\d+(?:[,.]\d+)*(?:\.\d+)?\s*[涓噖WwKk鍗僝]?)(?=(?:{labels}))",
        lambda match: match.group("num"),
        compact,
    )


def parse_numbers_with_presence(texts: list[str]) -> tuple[int, int, bool, bool]:
    scoped_texts = _current_post_scope_texts(texts)
    read_count = 0
    comment_count = 0
    read_found = False
    no_comments = any(
        "暂无评论" in text or "点击抢首评" in text or "说说你的想法" in text
        for text in scoped_texts
    )
    comment_found = no_comments
    number = r"(?P<num>\d+(?:[,.]\d+)*(?:\.\d+)?\s*[万wWkK千]?)"
    for text in _number_candidates(scoped_texts):
        compact = _normalize_count_text(text)
        for pattern in (
            rf"{number}(?:次)?(?:阅读|浏览|查看|阅)",
            rf"(?:阅读|浏览|查看|阅)(?:量|数)?{number}",
        ):
            match = re.search(pattern, compact)
            if match:
                prefix = compact[: match.start()]
                if any(word in prefix for word in ("评论", "回复", "留言")):
                    continue
                read_found = True
                read_count = max(read_count, _parse_count_token(match.group("num")))
        for pattern in (
            rf"{number}(?:条)?(?:评论|回复|留言|评)",
            rf"(?:评论|回复|留言|评)(?:数|量)?{number}",
        ):
            match = re.search(pattern, compact)
            if match:
                prefix = compact[: match.start()]
                suffix = compact[match.end() :]
                if any(word in prefix for word in ("阅读", "浏览", "查看")):
                    continue
                if any(word in suffix for word in ("阅读", "浏览", "查看")):
                    continue
                if (
                    match.start() == 0
                    and match.end() == len(compact)
                    and re.search(r"[万wWkK千]", match.group("num"))
                ):
                    continue
                comment_found = True
                comment_count = max(comment_count, _parse_count_token(match.group("num")))
    if no_comments:
        comment_count = 0
    return read_count, comment_count, read_found, comment_found


def parse_numbers(texts: list[str]) -> tuple[int, int]:
    read_count, comment_count, _, _ = parse_numbers_with_presence(texts)
    return read_count, comment_count


def _record_texts(records: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for record in records:
        if record.get("package") == "com.android.systemui":
            continue
        for key in ("text", "content_desc"):
            value = (record.get(key) or "").strip()
            if value:
                texts.append(value)
    return texts


def _adaptive_capture_pages(post_id: int, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ui_jsonl = output_dir / "ui_records.jsonl"
    ocr_jsonl = output_dir / "ocr_records.jsonl"
    seen_record_keys: set[str] = set()
    seen_screen_signatures: set[str] = set()
    all_texts: list[str] = []
    total_ui_records = 0
    total_ocr_records = 0
    pages_captured = 0
    read_count = 0
    comment_count = 0
    read_found = False
    comment_found = False
    ocr_attempted = False
    ocr_available = None

    max_pages = max(1, min(Config.BATCH_MAX_CAPTURE_PAGES, Config.SCROLL_TIMES + 1))
    current_device = device()

    for page_index in range(max_pages):
        xml_text = current_device.dump_hierarchy(compressed=False, pretty=True)
        signature = current_screen_signature(xml_text)

        xml_path = output_dir / f"page_{page_index:03d}.xml"
        screenshot_path = output_dir / f"page_{page_index:03d}.png"
        save_text(xml_path, xml_text)
        current_device.screenshot(str(screenshot_path))
        pages_captured += 1

        records = collect_ui_records(xml_text, page_index)
        new_records = []
        for record in records:
            key = stable_key(record)
            if key in seen_record_keys:
                continue
            seen_record_keys.add(key)
            new_records.append(record)

        append_jsonl(ui_jsonl, new_records)
        total_ui_records += len(new_records)
        all_texts.extend(_record_texts(new_records))

        read_count, comment_count, read_found, comment_found = parse_numbers_with_presence(all_texts)
        if Config.BATCH_ENABLE_OCR and ocr_available is not False and not (read_found and comment_found):
            ocr_attempted = True
            ocr_records = try_ocr(screenshot_path)
            if ocr_records is None:
                ocr_available = False
            else:
                ocr_available = True
                filtered_ocr = []
                for row in ocr_records:
                    if float(row.get("confidence", -1)) < Config.OCR_MIN_CONFIDENCE:
                        continue
                    bounds = row.get("bounds") or {}
                    if int(bounds.get("top") or 0) < 140:
                        continue
                    row["page_index"] = page_index
                    row["screenshot"] = screenshot_path.name
                    filtered_ocr.append(row)
                append_jsonl(ocr_jsonl, filtered_ocr)
                total_ocr_records += len(filtered_ocr)
                all_texts.extend(row["text"] for row in filtered_ocr)
                read_count, comment_count, read_found, comment_found = parse_numbers_with_presence(all_texts)

        logger.info(
            "adaptive capture post=%s page=%s/%s ui_new=%s ocr_total=%s read_found=%s comment_found=%s",
            post_id,
            page_index + 1,
            max_pages,
            len(new_records),
            total_ocr_records,
            read_found,
            comment_found,
        )
        if read_found and comment_found:
            break
        if page_index >= max_pages - 1:
            break
        if signature in seen_screen_signatures:
            logger.info("adaptive capture stopped: repeated screen post=%s", post_id)
            break
        seen_screen_signatures.add(signature)
        if not scroll_forward(current_device):
            logger.info("adaptive capture stopped: no more scrollable content post=%s", post_id)
            break
        time.sleep(Config.BATCH_SCROLL_WAIT)

    return {
        "output_dir": str(output_dir),
        "ui_records": total_ui_records,
        "ocr_records": total_ocr_records,
        "ui_jsonl": str(ui_jsonl),
        "ocr_jsonl": str(ocr_jsonl) if ocr_jsonl.exists() else None,
        "texts": all_texts,
        "read_count": read_count,
        "comment_count": comment_count,
        "read_found": read_found,
        "comment_found": comment_found,
        "pages_captured": pages_captured,
        "ocr_attempted": ocr_attempted,
        "ocr_available": ocr_available,
    }


def scrape_post_content(post_id: int) -> dict[str, Any]:
    output_dir = Config.CAPTURE_DIR / f"post_{post_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    result: dict[str, Any] = {
        "status": "error",
        "content": None,
        "read_count": 0,
        "comment_count": 0,
        "screenshot_path": None,
        "error": None,
    }

    status, error_msg = detect_page_status()
    if status == "not_found":
        result.update({"status": "deleted", "error": error_msg})
        return result
    if status == "error":
        result.update({"status": "error", "error": error_msg})
        return result

    summary = _adaptive_capture_pages(post_id, output_dir)
    texts = summary["texts"]
    read_count = summary["read_count"]
    comment_count = summary["comment_count"]
    content = extract_post_content(texts)
    if not content and not summary["read_found"] and not summary["comment_found"]:
        result.update(
            {
                "status": "error",
                "error": "post content was not detected; page may be blank or not the target post",
                "capture_pages": summary["pages_captured"],
                "read_found": summary["read_found"],
                "comment_found": summary["comment_found"],
                "ocr_attempted": summary["ocr_attempted"],
                "ocr_available": summary["ocr_available"],
                "ocr_records": summary["ocr_records"],
            }
        )
        return result
    screenshot = next(output_dir.glob("page_000.png"), None)
    result.update(
        {
            "status": "success",
            "content": content,
            "read_count": read_count,
            "comment_count": comment_count,
            "screenshot_path": str(screenshot) if screenshot else None,
            "capture_pages": summary["pages_captured"],
            "read_found": summary["read_found"],
            "comment_found": summary["comment_found"],
            "ocr_attempted": summary["ocr_attempted"],
            "ocr_available": summary["ocr_available"],
            "ocr_records": summary["ocr_records"],
        }
    )
    return result
