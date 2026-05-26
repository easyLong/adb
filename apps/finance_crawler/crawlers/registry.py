"""Registry for app-specific crawler adapters."""

from __future__ import annotations

from urllib.parse import urlparse

from apps.finance_crawler.crawlers.alipay import AlipayLinkProfile
from apps.finance_crawler.crawlers.antfortune import AntFortuneLinkProfile
from apps.finance_crawler.crawlers.base import AppCrawlerAdapter, AppLinkProfile, DefaultCrawlerAdapter
from apps.finance_crawler.crawlers.constants import SOURCE_TENPAY, SOURCE_UNKNOWN
from apps.finance_crawler.crawlers.tenpay import TenpayCrawlerAdapter, TenpayLinkProfile

_DEFAULT_ADAPTER = DefaultCrawlerAdapter()
_ADAPTERS: dict[str, AppCrawlerAdapter] = {
    SOURCE_TENPAY: TenpayCrawlerAdapter(),
}
_PROFILES: tuple[AppLinkProfile, ...] = (
    AntFortuneLinkProfile(),
    TenpayLinkProfile(),
    AlipayLinkProfile(),
)


def get_app_adapter(source_app: str | None) -> AppCrawlerAdapter:
    return _ADAPTERS.get(source_app or "", _DEFAULT_ADAPTER)


def iter_app_profiles() -> tuple[AppLinkProfile, ...]:
    return _PROFILES


def get_app_profile(source_app: str | None) -> AppLinkProfile | None:
    for profile in _PROFILES:
        if profile.source_app == source_app:
            return profile
    return None


def detect_source_app(url: str) -> str:
    for profile in _PROFILES:
        if profile.matches_url(url):
            return profile.source_app
    return SOURCE_UNKNOWN


def profile_for_url(url: str) -> AppLinkProfile | None:
    for profile in _PROFILES:
        if profile.matches_url(url):
            return profile
    return None


def build_direct_app_link(url: str) -> str | None:
    for profile in _PROFILES:
        deep_link = profile.build_deep_link(url)
        if deep_link:
            return deep_link
    return None


def target_package_for_url(url: str) -> str | None:
    profile = profile_for_url(url)
    return profile.package_name if profile else None


def readiness_keywords_for_url(url: str) -> tuple[str, ...]:
    profile = profile_for_url(url)
    return profile.ready_keywords if profile else ()


def supported_schemes() -> set[str]:
    schemes = {"http", "https"}
    for profile in _PROFILES:
        schemes.update(profile.schemes)
    return schemes


def is_reasonable_app_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in supported_schemes():
        return False
    return bool(parsed.netloc or parsed.scheme not in {"http", "https"})
