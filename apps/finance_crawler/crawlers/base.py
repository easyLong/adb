"""Shared adapter contracts for app-specific crawl behavior."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse


@dataclass(frozen=True)
class AppLinkProfile:
    """URL/deep-link behavior for one mobile app."""

    source_app: str
    display_name: str
    schemes: tuple[str, ...] = ()
    host_suffixes: tuple[str, ...] = ()
    package_name: str | None = None
    ready_keywords: tuple[str, ...] = ()

    def matches_url(self, url: str) -> bool:
        parsed = urlparse((url or "").strip())
        scheme = parsed.scheme.lower()
        host = parsed.netloc.lower()
        if scheme in self.schemes:
            return True
        return any(host == suffix or host.endswith(f".{suffix}") for suffix in self.host_suffixes)

    def build_deep_link(self, url: str) -> str | None:
        """Return a direct app deep link when this profile can rewrite the URL."""
        return None


@dataclass(frozen=True)
class CrawlAdapterContext:
    source_app: str | None
    output_dir: Path
    capture_ocr_snapshot: Callable[[Path, str], list[dict[str, Any]]]
    device: Callable[[], Any]
    scroll_forward: Callable[[Any], bool]
    scroll_wait: float
    max_detail_scrolls: int


class AppCrawlerAdapter(Protocol):
    source_app: str

    def before_main_capture(self, context: CrawlAdapterContext) -> dict[str, Any]:
        """Run optional app-specific work before the common post capture."""
        ...

    def result_fields(
        self,
        *,
        account_name: str,
        comment_count: int,
        adapter_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Return app-specific fields to merge into the common crawl result."""
        ...

    def extract_account_name(self, texts: list[str]) -> str | None:
        """Return an app-specific account name, or None to use generic parsing."""
        ...

    def extract_content(self, texts: list[str]) -> str | None:
        """Return app-specific post content, or None to use generic parsing."""
        ...

    def parse_counts(self, texts: list[str]) -> tuple[int, int, bool, bool] | None:
        """Return read/comment counts, or None to use generic parsing."""
        ...


class DefaultCrawlerAdapter:
    source_app = "default"

    def before_main_capture(self, context: CrawlAdapterContext) -> dict[str, Any]:
        return {}

    def result_fields(
        self,
        *,
        account_name: str,
        comment_count: int,
        adapter_data: dict[str, Any],
    ) -> dict[str, Any]:
        return {}

    def extract_account_name(self, texts: list[str]) -> str | None:
        return None

    def extract_content(self, texts: list[str]) -> str | None:
        return None

    def parse_counts(self, texts: list[str]) -> tuple[int, int, bool, bool] | None:
        return None
