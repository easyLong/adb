"""Input adapters that provide crawlable source records."""

from apps.finance_crawler.sources.excel import ExcelSource
from apps.finance_crawler.sources.tencent_docs import TencentDocsSource

__all__ = ["ExcelSource", "TencentDocsSource"]
