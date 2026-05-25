"""ADB crawler wrapper built on the validated alipay_capture.py flow."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.alipay_crawler.alipay.capture_engine import (
    capture_pages,
    collect_ui_records,
    connect_uiautomator,
    is_lockscreen_showing,
    open_alipay_link,
    resolve_embedded_alipay_scheme,
    set_device_awake,
)
from apps.alipay_crawler.config import Config
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("crawler")

_device = None

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


def device():
    global _device
    if _device is None:
        _device = connect_uiautomator(Config.DEVICE_SERIAL or None)
        info = _device.info
        logger.info(
            "设备连接成功: %s (%sx%s)",
            info.get("productName", "unknown"),
            info.get("displayWidth"),
            info.get("displayHeight"),
        )
    return _device


def resolve_short_url(short_url: str) -> str:
    _prepare_adb_path()
    resolved = resolve_embedded_alipay_scheme(short_url)
    return resolved or short_url


def open_url(url: str) -> None:
    _prepare_adb_path()
    serial = Config.DEVICE_SERIAL or None
    set_device_awake(serial)
    if is_lockscreen_showing(serial):
        raise RuntimeError("手机已锁屏，请手动解锁后重试")
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
    return "error", "页面状态未知或控件内容过少"


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
    # Give Alipay WebView a moment after open_url().
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
        logger.warning("截图失败: %s", exc)
        return None


def parse_numbers(texts: list[str]) -> tuple[int, int]:
    read_count = 0
    comment_count = 0
    for text in texts:
        for pattern in (
            r"(?P<num>\d+)\s*阅读",
            r"阅读\s*(?P<num>\d+)",
            r"(?P<num>\d+)\s*浏览",
            r"浏览\s*(?P<num>\d+)",
            r"(?P<num>\d+)\s*查看",
            r"查看\s*(?P<num>\d+)",
        ):
            match = re.search(pattern, text)
            if match:
                read_count = max(read_count, int(match.group("num")))
        for pattern in (
            r"(?P<num>\d+)\s*评论",
            r"评论\s*(?P<num>\d+)",
            r"(?P<num>\d+)\s*回复",
            r"回复\s*(?P<num>\d+)",
            r"(?P<num>\d+)\s*留言",
            r"留言\s*(?P<num>\d+)",
        ):
            match = re.search(pattern, text)
            if match:
                comment_count = max(comment_count, int(match.group("num")))
    return read_count, comment_count


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

    summary = capture_pages(
        device=device(),
        output_dir=output_dir,
        max_scrolls=Config.SCROLL_TIMES,
        wait_after_open=0,
        wait_after_scroll=Config.POST_DELAY_MIN,
        enable_ocr=False,
        dynamic_wait=False,
        ready_timeout=0,
        ready_check_interval=0,
    )

    jsonl_path = Path(summary["ui_jsonl"])
    texts: list[str] = []
    if jsonl_path.exists():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("text", "content_desc"):
                value = (row.get(key) or "").strip()
                if value:
                    texts.append(value)

    read_count, comment_count = parse_numbers(texts)
    screenshot = next(output_dir.glob("page_000.png"), None)
    result.update(
        {
            "status": "success",
            "content": "\n".join(dict.fromkeys(texts)),
            "read_count": read_count,
            "comment_count": comment_count,
            "screenshot_path": str(screenshot) if screenshot else None,
        }
    )
    return result
