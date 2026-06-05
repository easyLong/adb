"""Resolve Tencent Docs column indexes from header titles."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("tencent_docs_columns")

_HEADER_CACHE_TTL_SECONDS = 120.0
_PUNCT_RE = re.compile(r"[\u3000:：;；,，.。/\\|_\-()（）\[\]【】{}<>《》]+")
_HEADER_CACHE: dict[tuple[str, str, str], tuple[float, list[list[str]], int]] = {}


@dataclass(frozen=True, slots=True)
class ColumnResolution:
    field: str
    index: int
    title: str
    fallback: int
    source: str
    match_type: str
    matches: tuple[int, ...] = ()


MAIN_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "post_time": (
        "\u53d1\u5e03\u65f6\u95f4",
        "\u53d1\u6587\u65f6\u95f4",
        "\u65f6\u95f4",
        "\u65e5\u671f",
    ),
    "url": (
        "\u94fe\u63a5",
        "\u539f\u6587\u94fe\u63a5",
        "\u5e16\u5b50\u94fe\u63a5",
        "\u6587\u7ae0\u94fe\u63a5",
        "\u5185\u5bb9\u94fe\u63a5",
        "url",
    ),
    "account_name": (
        "\u8d26\u53f7",
        "\u8d26\u53f7\u540d\u79f0",
        "\u8d26\u6237\u540d\u79f0",
        "\u53d1\u5e03\u8d26\u53f7",
        "\u8d26\u53f7\u4e3b\u4f53",
    ),
    "read_count": (
        "\u9605\u8bfb\u6570",
        "\u9605\u8bfb\u91cf",
        "\u6d4f\u89c8\u6570",
        "\u6d4f\u89c8\u91cf",
    ),
    "comment_count": (
        "\u8bc4\u8bba\u6570",
        "\u8bc4\u8bba\u91cf",
    ),
    "check_status": (
        "\u521d\u68c0",
        "\u521d\u68c0\u72b6\u6001",
        "\u68c0\u67e5\u72b6\u6001",
    ),
    "detail_status": (
        "\u5907\u6ce8",
        "\u72b6\u6001",
        "\u8be6\u60c5\u72b6\u6001",
        "\u56de\u586b\u72b6\u6001",
    ),
    "screenshot": (
        "\u622a\u56fe",
        "\u622a\u56fe\u94fe\u63a5",
        "\u56fe\u7247",
    ),
}

DOC_LINK_READS_ALIASES: dict[str, tuple[str, ...]] = {
    "link": MAIN_COLUMN_ALIASES["url"],
    "read_count": MAIN_COLUMN_ALIASES["read_count"],
    "title": (
        "\u6807\u9898",
        "\u6587\u7ae0\u6807\u9898",
        "\u5185\u5bb9\u6807\u9898",
    ),
    "account_name": MAIN_COLUMN_ALIASES["account_name"],
}

ARTICLE_DETAIL_ALIASES: dict[str, tuple[str, ...]] = {
    "date": (
        "\u65e5\u671f",
        "\u53d1\u5e03\u65e5\u671f",
        "\u53d1\u5e03\u65f6\u95f4",
        "\u65f6\u95f4",
    ),
    "ip": (
        "ip",
        "ip\u540d\u79f0",
        "\u8d26\u53f7",
        "\u8d26\u53f7\u540d\u79f0",
    ),
    "product": (
        "\u4ea7\u54c1",
        "\u4ea7\u54c1\u540d",
        "\u4ea7\u54c1\u540d\u79f0",
    ),
    "url": MAIN_COLUMN_ALIASES["url"],
    "title": (
        "\u6807\u9898",
        "\u6587\u7ae0\u6807\u9898",
    ),
    "screenshot": MAIN_COLUMN_ALIASES["screenshot"],
    "read_count": MAIN_COLUMN_ALIASES["read_count"],
    "comment_count": MAIN_COLUMN_ALIASES["comment_count"],
    "like_count": (
        "\u70b9\u8d5e\u6570",
        "\u70b9\u8d5e\u91cf",
        "\u8d5e\u6570",
    ),
}


def default_main_fallbacks() -> dict[str, int]:
    return {
        "post_time": Config.QQ_COL_POST_TIME,
        "url": Config.QQ_COL_URL,
        "account_name": Config.QQ_COL_ACCOUNT_NAME,
        "read_count": Config.QQ_COL_READ_COUNT,
        "comment_count": Config.QQ_COL_COMMENT_COUNT,
        "check_status": Config.QQ_COL_CHECK_STATUS,
        "detail_status": Config.QQ_COL_DETAIL_STATUS,
        "screenshot": Config.QQ_COL_SCREENSHOT,
    }


def default_doc_link_read_fallbacks() -> dict[str, int]:
    return {
        "link": Config.DOC_LINK_READS_LINK_COL,
        "read_count": Config.DOC_LINK_READS_READ_COL,
        "title": 0,
        "account_name": Config.QQ_COL_ACCOUNT_NAME,
    }


def normalize_title(value: str) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[\ufe00-\ufe0f]", "", text)
    return _PUNCT_RE.sub("", text)


def header_map(rows: list[list[str]], start_row: int = 0) -> dict[str, list[int]]:
    if start_row != 0 or not rows:
        return {}
    output: dict[str, list[int]] = {}
    for index, value in enumerate(rows[0]):
        normalized = normalize_title(value)
        if not normalized:
            continue
        output.setdefault(normalized, []).append(index)
    return output


def resolve_column(
    rows: list[list[str]],
    start_row: int,
    aliases: tuple[str, ...],
    fallback: int,
    *,
    field_name: str = "",
    required: bool = False,
    strict_fallback_title: bool = False,
) -> int:
    return resolve_column_info(
        rows,
        start_row,
        aliases,
        fallback,
        field_name=field_name,
        required=required,
        strict_fallback_title=strict_fallback_title,
    ).index


def resolve_column_info(
    rows: list[list[str]],
    start_row: int,
    aliases: tuple[str, ...],
    fallback: int,
    *,
    field_name: str = "",
    required: bool = False,
    strict_fallback_title: bool = False,
) -> ColumnResolution:
    mapping = header_map(rows, start_row)
    normalized_aliases = [normalize_title(alias) for alias in aliases]
    matches: list[int] = []
    match_type = "exact"
    for alias in normalized_aliases:
        matches.extend(mapping.get(alias, []))

    if not matches:
        match_type = "contains"
        for header, indexes in mapping.items():
            for alias in normalized_aliases:
                if alias and len(alias) >= 2 and (alias in header or header in alias):
                    matches.extend(indexes)
                    break

    unique_matches = tuple(sorted(set(matches)))
    if len(unique_matches) == 1:
        resolved = unique_matches[0]
        if resolved != fallback:
            logger.info("resolved Tencent Docs column by title: %s=%s fallback=%s", field_name, resolved, fallback)
        return ColumnResolution(
            field=field_name,
            index=resolved,
            title=_header_title(rows, resolved),
            fallback=fallback,
            source="title",
            match_type=match_type,
            matches=unique_matches,
        )

    if len(unique_matches) > 1:
        if fallback in unique_matches:
            logger.warning(
                "ambiguous Tencent Docs column title for %s, using configured fallback=%s matches=%s",
                field_name,
                fallback,
                unique_matches,
            )
            return ColumnResolution(
                field=field_name,
                index=fallback,
                title=_header_title(rows, fallback),
                fallback=fallback,
                source="fallback",
                match_type="ambiguous",
                matches=unique_matches,
            )
        raise RuntimeError(f"ambiguous Tencent Docs column title for {field_name or aliases[0]}: {unique_matches}")

    if required and fallback < 0:
        raise RuntimeError(f"Tencent Docs column title not found for {field_name or aliases[0]}")
    fallback_title = _header_title(rows, fallback)
    fallback_match_type = "none"
    if fallback_title and not _title_matches_aliases(fallback_title, aliases):
        fallback_match_type = "unrecognized_fallback"
        if strict_fallback_title:
            raise RuntimeError(
                f"Tencent Docs column title not found for {field_name or aliases[0]}; "
                f"fallback col={fallback} has unrecognized title={fallback_title!r}"
            )
    return ColumnResolution(
        field=field_name,
        index=fallback,
        title=fallback_title,
        fallback=fallback,
        source="fallback",
        match_type=fallback_match_type,
        matches=(),
    )


def resolve_columns(
    rows: list[list[str]],
    start_row: int,
    aliases_by_field: dict[str, tuple[str, ...]],
    fallbacks: dict[str, int],
    *,
    strict_fallback_title: bool = False,
) -> dict[str, int]:
    return {
        field: resolve_column(
            rows,
            start_row,
            aliases,
            fallbacks.get(field, -1),
            field_name=field,
            strict_fallback_title=strict_fallback_title,
        )
        for field, aliases in aliases_by_field.items()
    }


def resolve_columns_info(
    rows: list[list[str]],
    start_row: int,
    aliases_by_field: dict[str, tuple[str, ...]],
    fallbacks: dict[str, int],
    *,
    strict_fallback_title: bool = False,
) -> dict[str, ColumnResolution]:
    return {
        field: resolve_column_info(
            rows,
            start_row,
            aliases,
            fallbacks.get(field, -1),
            field_name=field,
            strict_fallback_title=strict_fallback_title,
        )
        for field, aliases in aliases_by_field.items()
    }


def fetch_header_columns(
    doc: client.DocInfo,
    aliases_by_field: dict[str, tuple[str, ...]] | None = None,
    fallbacks: dict[str, int] | None = None,
    *,
    range_a1: str = "A1:AZ1",
    use_cache: bool = True,
    strict_fallback_title: bool = False,
) -> dict[str, int]:
    rows, start_row = fetch_header_rows(doc, range_a1=range_a1, use_cache=use_cache)
    return resolve_columns(
        rows,
        start_row,
        aliases_by_field or MAIN_COLUMN_ALIASES,
        fallbacks or default_main_fallbacks(),
        strict_fallback_title=strict_fallback_title,
    )


def fetch_header_rows(
    doc: client.DocInfo,
    *,
    range_a1: str = "A1:AZ1",
    use_cache: bool = True,
) -> tuple[list[list[str]], int]:
    key = (doc.file_id, doc.sheet_id, range_a1)
    now = time.monotonic()
    if use_cache:
        cached = _HEADER_CACHE.get(key)
        if cached and now - cached[0] <= _HEADER_CACHE_TTL_SECONDS:
            return cached[1], cached[2]

    rows, start_row = client.fetch_grid(range_a1, doc=doc)
    if use_cache:
        _HEADER_CACHE[key] = (now, rows, start_row)
    return rows, start_row


def clear_header_cache() -> None:
    _HEADER_CACHE.clear()


def inspect_header_columns(
    doc: client.DocInfo,
    aliases_by_field: dict[str, tuple[str, ...]] | None = None,
    fallbacks: dict[str, int] | None = None,
    *,
    range_a1: str = "A1:AZ1",
    use_cache: bool = False,
) -> list[ColumnResolution]:
    rows, start_row = fetch_header_rows(doc, range_a1=range_a1, use_cache=use_cache)
    resolved = resolve_columns_info(
        rows,
        start_row,
        aliases_by_field or MAIN_COLUMN_ALIASES,
        fallbacks or default_main_fallbacks(),
    )
    return list(resolved.values())


def _header_title(rows: list[list[str]], index: int) -> str:
    if index < 0 or not rows or index >= len(rows[0]):
        return ""
    return str(rows[0][index] or "").strip()


def _title_matches_aliases(title: str, aliases: tuple[str, ...]) -> bool:
    normalized = normalize_title(title)
    if not normalized:
        return True
    for alias in aliases:
        alias_text = normalize_title(alias)
        if alias_text and (alias_text == normalized or alias_text in normalized or normalized in alias_text):
            return True
    return False


def column(row_or_columns: dict[str, Any], key: str, fallback: int) -> int:
    value = row_or_columns.get(key)
    return int(value) if value is not None else fallback
