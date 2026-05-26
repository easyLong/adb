"""Ant Fortune app profile."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawlers.base import AppLinkProfile
from apps.finance_crawler.crawlers.constants import SOURCE_ANTFORTUNE


class AntFortuneLinkProfile(AppLinkProfile):
    def __init__(self) -> None:
        super().__init__(
            source_app=SOURCE_ANTFORTUNE,
            display_name="Ant Fortune",
            schemes=("afwealth",),
            host_suffixes=("think.klv5qu.com",),
            package_name=Config.AFWEALTH_PACKAGE,
            ready_keywords=("理财盘友圈",),
        )

    def build_deep_link(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None

        query = parse_qs(parsed.query)
        host = parsed.netloc.lower()
        if (host == "think.klv5qu.com" or host.endswith(".think.klv5qu.com")) and query.get("appId") and query.get("url"):
            return f"afwealth://platformapi/startapp?{parsed.query}"
        return None
