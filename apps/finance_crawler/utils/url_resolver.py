"""Parallel URL resolution helpers for app deep links."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from apps.finance_crawler.config import Config


def resolve_urls(
    posts: list[dict],
    resolver: Callable[[str], str],
    logger,
) -> dict[int, str]:
    if not posts:
        return {}

    workers = max(Config.URL_RESOLVE_WORKERS, 1)
    if workers == 1 or len(posts) == 1:
        resolved: dict[int, str] = {}
        for post in posts:
            try:
                resolved[post["id"]] = resolver(post["url"])
            except Exception as exc:
                logger.warning("URL resolve failed id=%s: %s", post.get("id"), exc)
                resolved[post["id"]] = post["url"]
        return resolved

    resolved = {post["id"]: post["url"] for post in posts}
    with ThreadPoolExecutor(max_workers=min(workers, len(posts))) as executor:
        future_map = {
            executor.submit(resolver, post["url"]): post
            for post in posts
        }
        for future in as_completed(future_map):
            post = future_map[future]
            try:
                resolved[post["id"]] = future.result()
            except Exception as exc:
                logger.warning("URL resolve failed id=%s: %s", post.get("id"), exc)
    return resolved
