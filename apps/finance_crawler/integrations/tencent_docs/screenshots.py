"""Tencent Docs screenshot upload and fallback writeback helpers."""

from __future__ import annotations

import time
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client, write_requests
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("tencent_docs_screenshots")


def post_screenshot_images(
    rows: list[tuple[int, str] | tuple[int, str, int]],
    *,
    doc: client.DocInfo | None = None,
) -> list[dict[str, Any]]:
    """Insert screenshots and return text/link fallback requests for failures."""
    requests_with_fallback: list[tuple[dict[str, Any], dict[str, Any]]] = []
    fallback_requests: list[dict[str, Any]] = []

    for row in rows:
        row_index = row[0]
        path_text = row[1]
        col_index = row[2] if len(row) > 2 else None
        try:
            requests_with_fallback.append(
                (
                    write_requests.screenshot_image_request(row_index, path_text, doc=doc, col_index=col_index),
                    write_requests.screenshot_cell_request(row_index, path_text, doc=doc, col_index=col_index),
                )
            )
            logger.info("Tencent Docs uploaded screenshot row=%s path=%s", row_index, path_text)
            if Config.QQ_IMAGE_UPLOAD_DELAY > 0:
                time.sleep(Config.QQ_IMAGE_UPLOAD_DELAY)
        except Exception as exc:
            logger.warning("Tencent Docs screenshot upload failed row=%s: %s", row_index, exc)
            fallback_requests.append(write_requests.screenshot_cell_request(row_index, path_text, doc=doc, col_index=col_index))

    if not requests_with_fallback:
        return fallback_requests

    chunk_size = max(Config.QQ_BATCH_UPDATE_SIZE, 1)
    for index in range(0, len(requests_with_fallback), chunk_size):
        chunk = requests_with_fallback[index : index + chunk_size]
        try:
            client.post_batch_update(
                [request for request, _ in chunk],
                "insert_screenshot_images",
                doc=doc,
            )
        except Exception as exc:
            logger.warning("Tencent Docs insert screenshot images failed: %s", exc)
            fallback_requests.extend(fallback for _, fallback in chunk)

    return fallback_requests
