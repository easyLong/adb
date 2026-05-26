"""Alipay app profile."""

from __future__ import annotations

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawlers.base import AppLinkProfile
from apps.finance_crawler.crawlers.constants import SOURCE_ALIPAY


class AlipayLinkProfile(AppLinkProfile):
    def __init__(self) -> None:
        super().__init__(
            source_app=SOURCE_ALIPAY,
            display_name="Alipay",
            schemes=("alipay", "alipays", "alipaylite", "alipaytoken"),
            host_suffixes=("alipay.com",),
            package_name=Config.ALIPAY_PACKAGE,
            ready_keywords=("理财盘友圈",),
        )
