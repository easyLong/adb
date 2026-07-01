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


@dataclass(frozen=True)
class CapturePlan:
    """App-specific policy for the common record capture loop."""

    max_pages: int
    scroll_wait: float
    enable_ocr: bool
    ocr_min_confidence: float
    ocr_min_top: int = 140
    stop_when_counts_found: bool = True
    stop_on_repeated_screen: bool = True
    max_detail_scrolls: int = 2


class AppCrawlerAdapter(Protocol):
    source_app: str

    def capture_plan(self) -> CapturePlan:
        """Return how the common capture loop should collect this app."""
        ...

    def before_main_capture(self, context: CrawlAdapterContext) -> dict[str, Any]:
        """Run optional app-specific work before the common record capture."""
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

    def refine_capture_result(
        self,
        *,
        result: dict[str, Any],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Return app-specific result updates from captured artifacts."""
        ...


class DefaultCrawlerAdapter:
    source_app = "default"

    def capture_plan(self) -> CapturePlan:
        from apps.finance_crawler.config import Config

        return CapturePlan(
            max_pages=max(1, min(Config.DETAIL_MAX_CAPTURE_PAGES, Config.SCROLL_TIMES + 1)),
            scroll_wait=Config.DETAIL_SCROLL_WAIT,
            enable_ocr=Config.DETAIL_ENABLE_OCR,
            ocr_min_confidence=Config.OCR_MIN_CONFIDENCE,
            max_detail_scrolls=max(0, min(Config.SCROLL_TIMES, 2)),
        )

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

    def refine_capture_result(
        self,
        *,
        result: dict[str, Any],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        return {}
