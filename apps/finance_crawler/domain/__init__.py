"""Shared domain models for source records, crawl results, and writebacks."""

from apps.finance_crawler.domain.interfaces import AppCrawler, LinkSource, ResultSink
from apps.finance_crawler.domain.records import CrawlResult, SourceRecord, WritebackResult

__all__ = [
    "AppCrawler",
    "CrawlResult",
    "LinkSource",
    "ResultSink",
    "SourceRecord",
    "WritebackResult",
]
