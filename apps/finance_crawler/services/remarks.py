"""Human-readable crawl result remarks for sheet writeback."""

from __future__ import annotations

from typing import Any


def detail_remark(result: dict[str, Any]) -> str:
    """Return a compact business-facing remark for the detail result column."""

    status = str(result.get("status") or "").strip()
    error = str(result.get("error") or "").strip()
    if status == "success":
        return "成功"
    if status in {"deleted", "not_found"}:
        return "失败：内容已删除/不存在"
    if not error:
        return f"失败：{status or '未知异常'}"

    lowered = error.lower()
    if _contains_any(lowered, "no devices", "device offline", "unauthorized", "device unavailable", "disconnect"):
        return "失败：设备断连/不可用"
    if "inject_events" in lowered or "injecting input events requires" in lowered:
        return "失败：设备输入权限异常"
    if _contains_any(lowered, "timeout", "timed out", "read timed out", "operation budget"):
        return "失败：操作超时"
    if _contains_any(lowered, "post content was not detected", "page may be blank", "blank"):
        return "失败：未识别到帖子正文"
    if _contains_any(lowered, "unsupported", "no crawler", "not supported"):
        return "失败：链接/应用不支持"
    if _contains_any(lowered, "rpc error", "uiautomator", "automator", "webdriver"):
        return "失败：自动化服务异常"
    return f"失败：采集异常（{_shorten(error)}）"


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _shorten(text: str, limit: int = 40) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."
