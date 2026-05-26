"""Stable interfaces between data sources, app crawlers, and result sinks."""

from __future__ import annotations

from typing import Protocol

from apps.alipay_crawler.domain.records import CrawlResult, SourceRecord, WritebackResult


class LinkSource(Protocol):
    """A provider that turns an external source into crawlable records."""

    source_type: str

    def fetch_records(self) -> list[SourceRecord]:
        """Return records containing URLs and source locators."""


class AppCrawler(Protocol):
    """An app-specific crawler, for example Alipay or Ant Fortune."""

    app_type: str

    def supports(self, url: str) -> bool:
        """Return whether this crawler can open and parse the URL."""

    def crawl(self, record: SourceRecord) -> CrawlResult:
        """Open the record in the target app and return parsed data."""


class ResultSink(Protocol):
    """A destination that writes crawl results back to a business system."""

    sink_type: str

    def write_results(self, results: list[CrawlResult]) -> list[WritebackResult]:
        """Persist or write back crawl results."""
