"""Tencent Docs source adapter."""

from __future__ import annotations

from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.domain.records import SourceRecord
from apps.finance_crawler.integrations.tencent_docs import client as tencent_docs_client
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.utils import tabular_links

logger = get_logger("tencent_docs_source")


class TencentDocsSource:
    """Read candidate links from a Tencent Docs sheet."""

    source_type = "tencent_docs"

    def __init__(self, limit: int | None = None) -> None:
        self.limit = Config.FETCH_LIMIT if limit is None else limit
        self.doc = tencent_docs_client.configured_doc()

    @property
    def source_name(self) -> str:
        return f"{self.doc.file_id}:{self.doc.sheet_id}"

    def fetch_candidates(self) -> list[dict[str, Any]]:
        sheet_title = tencent_docs_client.fetch_sheet_title()
        rows, start_row = tencent_docs_client.fetch_grid()
        candidates = tabular_links.eligible_candidates(rows, start_row, sheet_title)
        if self.limit and self.limit > 0:
            candidates = candidates[: self.limit]
        tabular_links.save_latest_candidates(candidates)
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
                    source_time=item.get("source_time"),
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
    value = copied.get("source_time")
    if hasattr(value, "isoformat"):
        copied["source_time"] = value.isoformat(sep=" ")
    return copied
