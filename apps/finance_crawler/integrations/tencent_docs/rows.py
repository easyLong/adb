"""Tencent Docs row lookup helpers."""

from __future__ import annotations

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.utils import tabular_links


def get_row_index_map(doc: client.DocInfo | None = None) -> dict[str, int]:
    rows, start_row = client.fetch_grid(doc=doc)
    mapping: dict[str, int] = {}
    for offset, row in enumerate(rows):
        if len(row) <= Config.QQ_COL_URL:
            continue
        url = row[Config.QQ_COL_URL].strip()
        if tabular_links.is_supported_crawl_url(url):
            # Tencent grid startRow is zero-based; sheet row number is one-based.
            mapping[url] = start_row + offset + 1
    return mapping


def resolve_row_index_for_url(
    url: str,
    preferred_row_index: int | None = None,
    rows: list[list[str]] | None = None,
    start_row: int | None = None,
    doc: client.DocInfo | None = None,
) -> int | None:
    """Resolve the current sheet row for a URL and guard against stale rows."""
    if rows is None or start_row is None:
        rows, start_row = client.fetch_grid(doc=doc)

    target = (url or "").strip()
    if not target:
        return None

    if Config.VALIDATE_DOC_ROW_BEFORE_WRITE and preferred_row_index:
        offset = preferred_row_index - start_row - 1
        if 0 <= offset < len(rows):
            row = rows[offset]
            if len(row) > Config.QQ_COL_URL and row[Config.QQ_COL_URL].strip() == target:
                return preferred_row_index

    matches: list[int] = []
    for offset, row in enumerate(rows):
        if len(row) <= Config.QQ_COL_URL:
            continue
        if row[Config.QQ_COL_URL].strip() == target:
            matches.append(start_row + offset + 1)

    if len(matches) > 1:
        raise RuntimeError(f"duplicate URL in Tencent Docs, skip unsafe writeback: {target}")
    return matches[0] if matches else None
