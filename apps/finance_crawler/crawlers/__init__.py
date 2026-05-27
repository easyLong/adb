"""App-specific crawler adapters."""

from apps.finance_crawler.crawlers.base import AppCrawlerAdapter, AppLinkProfile, CapturePlan, CrawlAdapterContext
from apps.finance_crawler.crawlers.registry import get_app_adapter

__all__ = ["AppCrawlerAdapter", "AppLinkProfile", "CapturePlan", "CrawlAdapterContext", "get_app_adapter"]
