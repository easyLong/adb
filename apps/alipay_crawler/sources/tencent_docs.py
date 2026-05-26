"""Tencent Docs source adapter."""

from __future__ import annotations

from typing import Any

from apps.alipay_crawler.config import Config
from apps.alipay_crawler.domain.records import SourceRecord
from apps.alipay_crawler.integrations import qq_docs
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("tencent_docs_source")


class TencentDocsSource:
    """Read candidate post links from a Tencent Docs sheet."""

    source_type = "tencent_docs"

    def __init__(self, limit: int | None = None) -> None:
        self.limit = Config.FETCH_LIMIT if limit is None else limit
        self.doc = qq_docs.configured_doc()

    @property
    def source_name(self) -> str:
        return f"{self.doc.file_id}:{self.doc.sheet_id}"

    def fetch_candidates(self) -> list[dict[str, Any]]:
        sheet_title = qq_docs.fetch_sheet_title()
        rows, start_row = qq_docs.fetch_grid()
        candidates = qq_docs.eligible_candidates(rows, start_row, sheet_title)
        if self.limit and self.limit > 0:
            candidates = candidates[: self.limit]
        qq_docs.save_latest_candidates(candidates)
        logger.info("Tencent Docs source candidates=%s limit=%s", len(candidates), self.limit)
        return candidates

    def fetch_records(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for item in self.fetch_candidates():
            row_index = item["row_index"]
            records.append(
                SourceRecord(
                    record_id=f"{self.doc.file_id}:{self.doc.sheet_id}:{row_index}",
                    source_type=self.source_type,
                    source_name=self.source_name,
                    url=item["url"],
                    app_type=item.get("source_app"),
                    post_time=item.get("post_time"),
                    locator={
                        "file_id": self.doc.file_id,
                        "sheet_id": self.doc.sheet_id,
                        "row_index": row_index,
                    },
                    raw=_json_safe_item(item),
                )
            )
        return records


def _json_safe_item(item: dict[str, Any]) -> dict[str, Any]:
    copied = dict(item)
    value = copied.get("post_time")
    if hasattr(value, "isoformat"):
        copied["post_time"] = value.isoformat(sep=" ")
    return copied
