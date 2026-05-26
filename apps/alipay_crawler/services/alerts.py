"""Alert delivery with a local JSONL fallback."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import requests

from apps.alipay_crawler.config import Config
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("alerts")
_last_sent_at: dict[str, float] = {}


def _allowed(key: str) -> bool:
    now = time.time()
    last = _last_sent_at.get(key, 0)
    if now - last < Config.ALERT_MIN_INTERVAL_SECONDS:
        return False
    _last_sent_at[key] = now
    return True


def send_alert(
    title: str,
    message: str,
    *,
    level: str = "error",
    dedupe_key: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if not Config.ALERT_ENABLED:
        return

    key = dedupe_key or f"{level}:{title}"
    if not _allowed(key):
        return

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "title": title,
        "message": message,
        "extra": extra or {},
    }

    try:
        with Config.ALERT_LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("failed to write alert log: %s", exc)

    if not Config.ALERT_WEBHOOK_URL:
        logger.warning("alert: %s - %s", title, message)
        return

    try:
        response = requests.post(Config.ALERT_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("failed to send alert webhook: %s", exc)
