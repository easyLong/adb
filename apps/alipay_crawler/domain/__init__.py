"""Shared domain models for source records, crawl results, and writebacks."""

from apps.alipay_crawler.domain.interfaces import AppCrawler, LinkSource, ResultSink
from apps.alipay_crawler.domain.records import CrawlResult, SourceRecord, WritebackResult

__all__ = [
    "AppCrawler",
    "CrawlResult",
    "LinkSource",
    "ResultSink",
    "SourceRecord",
    "WritebackResult",
]
