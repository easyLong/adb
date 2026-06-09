"""Common page status detection from UI records and visible texts."""

from __future__ import annotations

from typing import Any

NOT_FOUND_KEYWORDS = (
    "内容不存在",
    "内容不见了",
    "内容不见了，先去看看其他的吧",
    "当前内容已删除",
    "该内容已删除",
    "已被删除",
    "页面不存在",
    "帖子不见了",
    "该内容无法查看",
    "无法查看该内容",
)
ERROR_KEYWORDS = (
    "网络不给力",
    "加载失败",
    "请求超时",
    "连接失败",
    "稍后再试",
    "页面加载失败",
    "服务异常",
)
OK_KEYWORDS = (
    "阅读",
    "评论",
    "点赞",
    "关注",
    "理财",
    "发表观点",
    "转发",
    "热度",
    "最新",
    "讨论区",
    "去查看明细",
    "腾讯理财通",
)
UNKNOWN_PAGE_STATUS_ERROR = "page status is unknown or too few controls were found"


def records_to_texts(records: list[dict[str, Any]], *, min_length: int = 2) -> list[str]:
    texts: list[str] = []
    for record in records:
        for key in ("text", "content_desc"):
            value = (record.get(key) or "").strip()
            if value and len(value) >= min_length:
                texts.append(value)
    return texts


def detect_page_status_from_texts(texts: list[str]) -> tuple[str, str | None]:
    joined = "\n".join(texts)

    for keyword in NOT_FOUND_KEYWORDS:
        if keyword in joined:
            return "not_found", keyword
    for keyword in ERROR_KEYWORDS:
        if keyword in joined:
            return "error", keyword
    if any(keyword in joined for keyword in OK_KEYWORDS):
        return "success", None
    return "error", UNKNOWN_PAGE_STATUS_ERROR
