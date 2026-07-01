"""Tenpay / Tencent Wealth specific crawl extensions."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawlers.base import AppLinkProfile, CapturePlan, CrawlAdapterContext
from apps.finance_crawler.crawlers.constants import SOURCE_TENPAY
from apps.finance_crawler.mobile.parsers import parse_count_token
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("tenpay_crawler")


class TenpayLinkProfile(AppLinkProfile):
    def __init__(self) -> None:
        super().__init__(
            source_app=SOURCE_TENPAY,
            display_name="Tenpay / Tencent Wealth",
            schemes=("tenpay", "tencentwm"),
            host_suffixes=("tencentwm.com",),
            package_name=Config.TENPAY_PACKAGE,
            ready_keywords=("腾讯理财通",),
        )


def _ocr_texts(rows: list[dict[str, Any]]) -> list[str]:
    return [(row.get("text") or "").strip() for row in rows if (row.get("text") or "").strip()]


def _has_any_text(texts: list[str], keywords: tuple[str, ...]) -> bool:
    return any(any(keyword in text for keyword in keywords) for text in texts)


def _is_tenpay_post_texts(texts: list[str]) -> bool:
    return _has_any_text(texts, ("腾讯理财通", "去查看明细", "发表观点"))


def _is_usable_account_text(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned or len(cleaned) > 30:
        return False
    if cleaned in {"腾讯理财通", "已关注", "关注", "评论", "阅读", "点赞", "头像"}:
        return False
    if any(word in cleaned for word in ("理财", "基金", "阅读", "评论", "点赞", "关注")):
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}", cleaned):
        return False
    if re.search(r"https?://|\d{4}-\d{2}-\d{2}", cleaned):
        return False
    if re.fullmatch(r"\d+", cleaned):
        return False
    return True


def _extract_account_name(texts: list[str]) -> str | None:
    ignored = {"腾讯理财通", "已关注", "关注"}
    for index, text in enumerate(texts[:30]):
        if "腾讯理财通" not in text:
            continue
        for candidate in texts[index + 1 : index + 8]:
            cleaned = candidate.strip()
            if cleaned and cleaned not in ignored and _is_usable_account_text(cleaned):
                return cleaned
    return None


def _is_content_stop(text: str) -> bool:
    if text in {"发表观点", "发表观点.", "发表评论"}:
        return True
    if text in {"评论", "转发", "热度", "最新", "点赞", "返回", "更多"}:
        return True
    return any(text.startswith(prefix) for prefix in ("风险提示", "暂无评论", "点击抢首评", "说说你的想法"))


def _is_content_noise(text: str) -> bool:
    if text in {"腾讯理财通", "已关注", "关注", "听一听", "讨论区", "去查看明细"}:
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s*\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d+", text):
        return True
    return False


def _extract_content(texts: list[str]) -> str | None:
    if not any("腾讯理财通" in text for text in texts):
        return None
    content_parts: list[str] = []
    started = False
    for text in texts:
        cleaned = (text or "").strip()
        if not cleaned:
            continue
        if _is_content_stop(cleaned):
            break
        if not started and cleaned in {"已关注", "关注"}:
            started = True
            continue
        if not started:
            continue
        if _is_content_noise(cleaned):
            continue
        content_parts.append(cleaned)
    if not content_parts:
        return None
    return "\n".join(dict.fromkeys(content_parts))


def _parse_counts(texts: list[str]) -> tuple[int, int, bool, bool] | None:
    if not any("腾讯理财通" in text for text in texts):
        return None
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
    return None


def _ocr_center(row: dict[str, Any] | None) -> tuple[int, int] | None:
    if not row:
        return None
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


def _parse_trade_details_page(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _parse_trade_details(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    rows_by_screenshot: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_screenshot.setdefault(str(row.get("screenshot") or "__single__"), []).append(row)

    for screenshot_rows in rows_by_screenshot.values():
        for detail in _parse_trade_details_page(screenshot_rows):
            key = (detail.get("date") or "", detail.get("fund_name") or "", detail.get("amount_text") or "")
            if key in seen:
                continue
            seen.add(key)
            details.append(detail)
    return details


def _empty_detail_result(error: str | None = None) -> dict[str, Any]:
    return {
        "attempted": False,
        "opened": False,
        "trade_details": [],
        "error": error,
    }


def _collect_trade_details(context: CrawlAdapterContext) -> dict[str, Any]:
    context.output_dir.mkdir(parents=True, exist_ok=True)
    detail_result = _empty_detail_result()

    entry_rows = context.capture_ocr_snapshot(context.output_dir, "tenpay_trade_entry")
    entry_texts = _ocr_texts(entry_rows)
    if not _is_tenpay_post_texts(entry_texts):
        return detail_result

    detail_result["attempted"] = True
    button = _find_ocr_row(entry_rows, ("去查看明细", "查看明细"))
    center = _ocr_center(button)
    if center is None:
        detail_result["error"] = "Tenpay detail button was not detected"
        return detail_result

    current_device = context.device()
    current_device.click(center[0], center[1])
    time.sleep(3.0)

    detail_rows = context.capture_ocr_snapshot(context.output_dir, "tenpay_trade_after_click")
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
    tab_center = _ocr_center(tab)
    if tab_center is not None:
        current_device.click(tab_center[0], tab_center[1])
        time.sleep(2.0)
        detail_rows = context.capture_ocr_snapshot(context.output_dir, "tenpay_trade_rebalance_000")

    all_detail_rows = list(detail_rows)
    for page_index in range(1, context.max_detail_scrolls + 1):
        if not context.scroll_forward(current_device):
            break
        time.sleep(context.scroll_wait)
        page_rows = context.capture_ocr_snapshot(context.output_dir, f"tenpay_trade_rebalance_{page_index:03d}")
        page_texts = _ocr_texts(page_rows)
        if not _has_any_text(page_texts, ("调仓明细", "买入", "卖出", "转入", "转出")):
            break
        all_detail_rows.extend(page_rows)

    detail_result["trade_details"] = _parse_trade_details(all_detail_rows)

    try:
        current_device.press("back")
        time.sleep(1.5)
    except Exception as exc:
        logger.warning("Tenpay detail back navigation failed: %s", exc)
    return detail_result


def _build_summary(account_name: str, comment_count: int, trade_details: list[dict[str, Any]]) -> dict[str, Any]:
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


def _parse_bottom_counts_from_ocr_jsonl(ocr_jsonl: str | None) -> dict[str, int]:
    rows = _read_ocr_rows_jsonl(ocr_jsonl)
    if not rows:
        return {}

    rows_by_page: dict[object, list[tuple[int, int]]] = {}
    for row in rows:
        text = str(row.get("text") or "").replace(",", "").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?(?:[wWkK\u4e07\u5343])?", text):
            continue
        bounds = row.get("bounds") or {}
        try:
            top = int(bounds.get("top") or 0)
            left = int(bounds.get("left") or 0)
        except (TypeError, ValueError):
            continue
        if top < 2150:
            continue
        page_key = row.get("page_index", row.get("screenshot") or "__single__")
        rows_by_page.setdefault(page_key, []).append((left, parse_count_token(text)))

    for page_key in sorted(rows_by_page, key=lambda value: str(value)):
        numeric = sorted(rows_by_page[page_key], key=lambda item: item[0])
        if len(numeric) >= 2:
            return {
                "comment_count": numeric[0][1],
                "like_count": numeric[1][1],
            }
    return {}


def _read_ocr_rows_jsonl(ocr_jsonl: str | None) -> list[dict[str, Any]]:
    if not ocr_jsonl:
        return []
    path = Path(str(ocr_jsonl))
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _parse_article_title_from_ocr_jsonl(ocr_jsonl: str | None) -> str | None:
    rows = _read_ocr_rows_jsonl(ocr_jsonl)
    if not rows:
        return None

    first_page_rows = [row for row in rows if row.get("page_index", 0) == 0]
    lines = _group_ocr_lines(first_page_rows or rows)
    title_lines: list[str] = []
    for line in lines:
        try:
            top = int(line.get("top") or 0)
            left = min(int((row.get("bounds") or {}).get("left") or 0) for row in line.get("rows") or [])
        except (TypeError, ValueError):
            continue
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        if top < 380:
            continue
        if top > 650:
            break
        if left > 140:
            continue
        if _is_content_noise(text) or _is_content_stop(text):
            continue
        title_lines.append(text)

    if not title_lines:
        return None
    return "".join(title_lines).strip() or None


class TenpayCrawlerAdapter:
    source_app = SOURCE_TENPAY

    def capture_plan(self) -> CapturePlan:
        return CapturePlan(
            max_pages=max(1, min(Config.DETAIL_MAX_CAPTURE_PAGES, Config.SCROLL_TIMES + 1)),
            scroll_wait=Config.DETAIL_SCROLL_WAIT,
            enable_ocr=True,
            ocr_min_confidence=Config.OCR_MIN_CONFIDENCE,
            max_detail_scrolls=max(0, min(Config.SCROLL_TIMES, 2)),
        )

    def before_main_capture(self, context: CrawlAdapterContext) -> dict[str, Any]:
        return _collect_trade_details(context)

    def extract_account_name(self, texts: list[str]) -> str | None:
        return _extract_account_name(texts)

    def extract_content(self, texts: list[str]) -> str | None:
        return _extract_content(texts)

    def parse_counts(self, texts: list[str]) -> tuple[int, int, bool, bool] | None:
        return _parse_counts(texts)

    def refine_capture_result(
        self,
        *,
        result: dict[str, Any],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        requested = {str(item) for item in result.get("requested_fields") or () if str(item)}
        if not requested.intersection({"article_title", "comment_count", "like_count"}):
            return {}

        updates: dict[str, Any] = {}
        if "article_title" in requested:
            article_title = _parse_article_title_from_ocr_jsonl(summary.get("ocr_jsonl"))
            if article_title:
                updates["article_title"] = article_title

        if requested.intersection({"comment_count", "like_count"}):
            bottom_counts = _parse_bottom_counts_from_ocr_jsonl(summary.get("ocr_jsonl"))
            if "like_count" in requested and bottom_counts.get("like_count") is not None:
                updates["like_count"] = bottom_counts["like_count"]
                updates["like_found"] = True
            if (
                "comment_count" in requested
                and bottom_counts.get("comment_count") is not None
                and (not result.get("comment_found") or not result.get("comment_count"))
            ):
                updates["comment_count"] = bottom_counts["comment_count"]
                updates["comment_found"] = True
        return updates

    def result_fields(
        self,
        *,
        account_name: str,
        comment_count: int,
        adapter_data: dict[str, Any],
    ) -> dict[str, Any]:
        trade_details = adapter_data.get("trade_details") or []
        app_metrics = {
            "tenpay_trade_detail_attempted": adapter_data.get("attempted", False),
            "tenpay_trade_detail_opened": adapter_data.get("opened", False),
            "tenpay_trade_details": trade_details,
            "tenpay_trade_detail_error": adapter_data.get("error"),
            "tenpay_summary": _build_summary(account_name, comment_count, trade_details),
        }
        return {
            **app_metrics,
            "app_metrics": app_metrics,
        }
