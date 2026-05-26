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
    save_screenshot,
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

    if any("腾讯理财通" in text for text in texts):
        ignored = {"腾讯理财通", "已关注", "关注"}
        for index, text in enumerate(texts[:30]):
            if "腾讯理财通" not in text:
                continue
            for candidate in texts[index + 1 : index + 8]:
                cleaned = candidate.strip()
                if cleaned and cleaned not in ignored and usable(cleaned):
                    return cleaned

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
        save_screenshot(device(), path, serial=_device_serial)
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
    if text in {"发表观点", "发表观点.", "发表评论"}:
        return True
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
    if text in {"腾讯理财通", "已关注", "关注", "听一听", "讨论区", "去查看明细"}:
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s*\d{1,2}:\d{2}", text):
        return True
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
    if any("腾讯理财通" in text for text in texts):
        content_parts: list[str] = []
        started = False
        for text in texts:
            cleaned = (text or "").strip()
            if not cleaned:
                continue
            if _is_post_content_stop(cleaned):
                break
            if not started and cleaned in {"已关注", "关注"}:
                started = True
                continue
            if not started:
                continue
            if _is_post_content_noise(cleaned):
                continue
            if re.fullmatch(r"\d+", cleaned):
                continue
            content_parts.append(cleaned)
        if content_parts:
            return "\n".join(dict.fromkeys(content_parts))

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
    if any("腾讯理财通" in text for text in texts):
        for index, text in enumerate(texts):
            if "发表观点" not in text:
                continue
            numeric_tail = [
                candidate.strip()
                for candidate in texts[index + 1 : index + 6]
                if re.fullmatch(r"\d+", candidate.strip())
            ]
            if numeric_tail:
                return 0, int(numeric_tail[0]), False, True

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


def _ocr_texts(rows: list[dict[str, Any]]) -> list[str]:
    return [(row.get("text") or "").strip() for row in rows if (row.get("text") or "").strip()]


def _has_any_text(texts: list[str], keywords: tuple[str, ...]) -> bool:
    return any(any(keyword in text for keyword in keywords) for text in texts)


def _is_tenpay_post_texts(texts: list[str]) -> bool:
    return _has_any_text(texts, ("腾讯理财通", "去查看明细", "发表观点"))


def _ocr_center(row: dict[str, Any]) -> tuple[int, int] | None:
    bounds = row.get("bounds") or {}
    try:
        left = int(bounds.get("left") or 0)
        top = int(bounds.get("top") or 0)
        width = int(bounds.get("width") or 0)
        height = int(bounds.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return left + width // 2, top + height // 2


def _find_ocr_row(rows: list[dict[str, Any]], keywords: tuple[str, ...]) -> dict[str, Any] | None:
    for row in rows:
        text = (row.get("text") or "").strip()
        if any(keyword in text for keyword in keywords):
            return row
    return None


def _capture_ocr_snapshot(output_dir: Path, name: str) -> list[dict[str, Any]]:
    screenshot_path = output_dir / f"{name}.png"
    save_screenshot(device(), screenshot_path, serial=_device_serial)
    rows = try_ocr(screenshot_path) or []
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        if float(row.get("confidence", -1)) < Config.OCR_MIN_CONFIDENCE:
            continue
        row["screenshot"] = screenshot_path.name
        filtered_rows.append(row)
    if filtered_rows:
        append_jsonl(output_dir / "tenpay_trade_ocr_records.jsonl", filtered_rows)
    return filtered_rows


def _group_ocr_lines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sortable_rows = []
    for row in rows:
        bounds = row.get("bounds") or {}
        try:
            top = int(bounds.get("top") or 0)
            left = int(bounds.get("left") or 0)
            height = int(bounds.get("height") or 0)
        except (TypeError, ValueError):
            continue
        sortable_rows.append((top, left, height, row))

    lines: list[dict[str, Any]] = []
    for top, left, height, row in sorted(sortable_rows, key=lambda item: (item[0], item[1])):
        matched_line = None
        for line in lines:
            threshold = max(26, int(max(height, line["height"]) * 0.75))
            if abs(top - line["top"]) <= threshold:
                matched_line = line
                break
        if matched_line is None:
            matched_line = {"top": top, "height": height, "rows": []}
            lines.append(matched_line)
        matched_line["rows"].append((left, row))
        matched_line["top"] = min(matched_line["top"], top)
        matched_line["height"] = max(matched_line["height"], height)

    grouped: list[dict[str, Any]] = []
    for line in sorted(lines, key=lambda item: item["top"]):
        ordered_rows = [row for _, row in sorted(line["rows"], key=lambda item: item[0])]
        text = " ".join((row.get("text") or "").strip() for row in ordered_rows if (row.get("text") or "").strip())
        if text:
            grouped.append({"text": text, "top": line["top"], "rows": ordered_rows})
    return grouped


def _normalize_trade_date(text: str) -> str | None:
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _parse_money_text(text: str) -> tuple[str, float] | None:
    match = re.search(r"([+\uff0b-]?\s*\d[\d,.]*)\s*元", text)
    if not match:
        return None
    amount_text = match.group(1).replace(" ", "").replace("\uff0b", "+")
    sign = ""
    if amount_text[:1] in {"+", "-"}:
        sign = amount_text[:1]
        amount_text = amount_text[1:]
    if "," not in amount_text and amount_text.count(".") > 1:
        parts = amount_text.split(".")
        amount_text = ",".join(parts[:-1]) + "." + parts[-1]
    amount_text = sign + amount_text
    try:
        amount = float(amount_text.replace("+", "").replace(",", ""))
    except ValueError:
        return None
    return amount_text, amount


def _clean_fund_name(text: str) -> str:
    cleaned = re.sub(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}[日号]?", "", text)
    cleaned = re.sub(r"[+\uff0b]?\s*\d[\d,]*(?:\.\d{1,2})?\s*元", "", cleaned)
    cleaned = re.sub(r"(买入|买|申购|卖出|卖|赎回|调仓|明细|成功|处理中|已完成)", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.strip(" :：-—|,，")
    return cleaned


def _looks_like_fund_name(text: str) -> bool:
    if not text or len(text) < 4:
        return False
    if any(word in text for word in ("收益", "策略", "调仓明细", "持仓明细", "交易明细", "全部", "筛选", "买入", "卖出")):
        return False
    if re.search(r"\d{1,2}:\d{2}", text):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _parse_tenpay_trade_details_page(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines = _group_ocr_lines(rows)
    details: list[dict[str, Any]] = []
    current_date: str | None = None
    seen: set[tuple[str, str, str]] = set()

    for index, line in enumerate(lines):
        text = line["text"]
        date_in_line = _normalize_trade_date(text)
        if date_in_line:
            current_date = date_in_line

        if "买入" not in text and "申购" not in text:
            continue
        money = _parse_money_text(text)
        if not money:
            continue
        amount_text, amount = money

        fund_name = ""
        for row in sorted(line["rows"], key=lambda item: int((item.get("bounds") or {}).get("left") or 0)):
            row_text = (row.get("text") or "").strip()
            if "买入" in row_text or "申购" in row_text:
                continue
            if "元" in row_text:
                continue
            cleaned = _clean_fund_name(row_text)
            cleaned = cleaned.replace("田", "").replace("因", "").strip()
            if _looks_like_fund_name(cleaned):
                fund_name = cleaned
                break
        if not fund_name:
            continue

        trade_date = current_date
        for next_line in lines[index + 1 : min(len(lines), index + 3)]:
            trade_date = _normalize_trade_date(next_line["text"]) or trade_date
            if trade_date:
                break

        key = (trade_date or "", fund_name, amount_text)
        if key in seen:
            continue
        seen.add(key)
        details.append(
            {
                "date": trade_date,
                "side": "buy",
                "fund_name": fund_name,
                "amount_text": amount_text,
                "amount": amount,
            }
        )
    return details


def _parse_tenpay_trade_details(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    rows_by_screenshot: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_screenshot.setdefault(str(row.get("screenshot") or "__single__"), []).append(row)

    for screenshot_rows in rows_by_screenshot.values():
        for detail in _parse_tenpay_trade_details_page(screenshot_rows):
            key = (detail.get("date") or "", detail.get("fund_name") or "", detail.get("amount_text") or "")
            if key in seen:
                continue
            seen.add(key)
            details.append(detail)
    return details


def _build_tenpay_summary(
    account_name: str,
    comment_count: int,
    trade_details: list[dict[str, Any]],
) -> dict[str, Any]:
    buy_funds = []
    for detail in trade_details:
        if detail.get("side") != "buy":
            continue
        buy_funds.append(
            {
                "fund_name": detail.get("fund_name") or "",
                "amount": detail.get("amount"),
                "amount_text": detail.get("amount_text") or "",
                "date": detail.get("date"),
            }
        )
    return {
        "account_name": account_name,
        "comment_count": int(comment_count or 0),
        "buy_funds": buy_funds,
    }


def _collect_tenpay_trade_details(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_result: dict[str, Any] = {
        "attempted": False,
        "opened": False,
        "trade_details": [],
        "error": None,
    }

    if not Config.BATCH_ENABLE_OCR:
        detail_result["error"] = "OCR is disabled; Tenpay trade detail extraction requires OCR"
        return detail_result

    entry_rows = _capture_ocr_snapshot(output_dir, "tenpay_trade_entry")
    entry_texts = _ocr_texts(entry_rows)
    if not _is_tenpay_post_texts(entry_texts):
        return detail_result

    detail_result["attempted"] = True
    button = _find_ocr_row(entry_rows, ("去查看明细", "查看明细"))
    center = _ocr_center(button) if button else None
    if center is None:
        detail_result["error"] = "Tenpay detail button was not detected"
        return detail_result

    current_device = device()
    current_device.click(center[0], center[1])
    time.sleep(3.0)

    detail_rows = _capture_ocr_snapshot(output_dir, "tenpay_trade_after_click")
    detail_texts = _ocr_texts(detail_rows)
    navigated = _has_any_text(detail_texts, ("调仓明细", "持仓明细", "交易明细", "买入", "卖出"))
    if not navigated:
        still_on_post = _is_tenpay_post_texts(detail_texts) and _find_ocr_row(detail_rows, ("去查看明细", "查看明细"))
        if not still_on_post:
            try:
                current_device.press("back")
                time.sleep(1.0)
            except Exception as exc:
                logger.warning("Tenpay detail recovery back navigation failed: %s", exc)
        detail_result["error"] = "Tenpay detail page did not open"
        return detail_result

    detail_result["opened"] = True
    tab = _find_ocr_row(detail_rows, ("调仓明细", "调仓"))
    tab_center = _ocr_center(tab) if tab else None
    if tab_center is not None:
        current_device.click(tab_center[0], tab_center[1])
        time.sleep(2.0)
        detail_rows = _capture_ocr_snapshot(output_dir, "tenpay_trade_rebalance_000")

    all_detail_rows = list(detail_rows)
    max_detail_scrolls = max(0, min(Config.SCROLL_TIMES, 2))
    for page_index in range(1, max_detail_scrolls + 1):
        if not scroll_forward(current_device):
            break
        time.sleep(Config.BATCH_SCROLL_WAIT)
        page_rows = _capture_ocr_snapshot(output_dir, f"tenpay_trade_rebalance_{page_index:03d}")
        page_texts = _ocr_texts(page_rows)
        if not _has_any_text(page_texts, ("调仓明细", "买入", "卖出", "转入", "转出")):
            break
        all_detail_rows.extend(page_rows)

    detail_result["trade_details"] = _parse_tenpay_trade_details(all_detail_rows)

    try:
        current_device.press("back")
        time.sleep(1.5)
    except Exception as exc:
        logger.warning("Tenpay detail back navigation failed: %s", exc)
    return detail_result


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
        save_screenshot(current_device, screenshot_path, serial=_device_serial)
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


def scrape_post_content(post_id: int, source_app: str | None = None) -> dict[str, Any]:
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

    tenpay_detail = (
        _collect_tenpay_trade_details(output_dir)
        if source_app in {None, "tenpay"}
        else {"attempted": False, "opened": False, "trade_details": [], "error": None}
    )
    summary = _adaptive_capture_pages(post_id, output_dir)
    texts = summary["texts"]
    read_count = summary["read_count"]
    comment_count = summary["comment_count"]
    content = extract_post_content(texts)
    account_name = extract_account_name(texts)
    tenpay_summary = (
        _build_tenpay_summary(account_name, comment_count, tenpay_detail["trade_details"])
        if source_app in {None, "tenpay"}
        else None
    )
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
            "account_name": account_name,
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
            "tenpay_trade_detail_attempted": tenpay_detail["attempted"],
            "tenpay_trade_detail_opened": tenpay_detail["opened"],
            "tenpay_trade_details": tenpay_detail["trade_details"],
            "tenpay_trade_detail_error": tenpay_detail["error"],
            "tenpay_summary": tenpay_summary,
        }
    )
    return result
