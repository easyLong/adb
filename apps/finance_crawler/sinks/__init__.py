"""Output adapters that write crawl results to business systems."""

from apps.finance_crawler.sinks.excel import ExcelSink
from apps.finance_crawler.sinks.tencent_docs import TencentDocsSink

__all__ = ["ExcelSink", "TencentDocsSink"]
