"""Generic records shared by sources, crawlers, storage, and sinks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SourceRecord:
    """One crawl candidate from any source: Tencent Docs, Excel, API, etc."""

    record_id: str
    source_type: str
    url: str
    source_name: str | None = None
    app_type: str | None = None
    source_time: datetime | None = None
    locator: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    task_id: int | None = None


@dataclass(slots=True)
class CrawlResult:
    """Normalized app crawl output with app-specific values in metrics."""

    url: str
    app_type: str
    status: str
    account_name: str | None = None
    content: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    screenshot_path: str | None = None
    error: str | None = None
    crawled_at: datetime = field(default_factory=datetime.now)
    task_id: int | None = None
    source_record_id: str | None = None

    @property
    def read_count(self) -> int:
        return int(self.metrics.get("read_count") or 0)

    @property
    def comment_count(self) -> int:
        return int(self.metrics.get("comment_count") or 0)


@dataclass(slots=True)
class WritebackResult:
    """Result of writing crawl output to a business sink."""

    sink_type: str
    status: str
    task_id: int | None = None
    result_id: int | None = None
    locator: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    written_at: datetime | None = None
