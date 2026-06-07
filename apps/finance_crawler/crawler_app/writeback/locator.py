"""URL/date based Tencent Docs writeback row location."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from apps.finance_crawler.crawler_app.documents.column_resolver import ColumnMapping, resolve_header
from apps.finance_crawler.integrations.tencent_docs import client


@dataclass(frozen=True, slots=True)
class SheetWritebackContext:
    rows: list[list[str]]
    start_row: int
    mapping: ColumnMapping


@dataclass(frozen=True, slots=True)
class LocatedRows:
    primary_row: int | None
    duplicate_rows: tuple[int, ...] = ()
    error: str | None = None

    @property
    def matched(self) -> bool:
        return self.primary_row is not None and self.error is None


def load_sheet_context(doc: client.DocInfo, read_range: str) -> SheetWritebackContext:
    rows, start_row = client.fetch_grid(read_range, doc=doc)
    header = rows[0] if rows else []
    return SheetWritebackContext(rows=rows, start_row=start_row, mapping=resolve_header(header))


def locate_by_post_url(context: SheetWritebackContext, post_url: str) -> LocatedRows:
    post_url_col = context.mapping.columns.get("post_url")
    if post_url_col is None:
        return LocatedRows(None, error="post_url column not mapped in current sheet")
    return locate_by_column_values(
        context,
        {post_url_col: post_url},
        normalizers={post_url_col: _normalize_text},
        missing_error=f"post_url not found in current sheet: {post_url}",
    )


def locate_by_date_url(
    context: SheetWritebackContext,
    *,
    target_date: date,
    url: str,
    date_col_index: int,
    url_col_index: int,
) -> LocatedRows:
    return locate_by_column_values(
        context,
        {date_col_index: target_date, url_col_index: url},
        normalizers={date_col_index: _normalize_date, url_col_index: _normalize_text},
        missing_error=f"date + URL not found in current sheet: {target_date.isoformat()} {url}",
    )


def locate_by_column_values(
    context: SheetWritebackContext,
    expected: dict[int, Any],
    *,
    normalizers: dict[int, Callable[[Any], str]] | None = None,
    missing_error: str,
) -> LocatedRows:
    if not expected:
        return LocatedRows(None, error="missing writeback row location key")
    normalizers = normalizers or {}
    normalized_expected = {
        int(column): normalizers.get(int(column), _normalize_text)(value)
        for column, value in expected.items()
    }
    if any(not value for value in normalized_expected.values()):
        return LocatedRows(None, error="empty writeback row location key")

    matches: list[int] = []
    for offset, row in enumerate(context.rows):
        if all(_cell_matches(row, column, expected_value, normalizers.get(column, _normalize_text)) for column, expected_value in normalized_expected.items()):
            matches.append(context.start_row + offset + 1)
    if not matches:
        return LocatedRows(None, error=missing_error)
    return LocatedRows(primary_row=matches[0], duplicate_rows=tuple(matches[1:]))


def _cell_matches(row: list[str], column_index: int, expected_value: str, normalizer: Callable[[Any], str]) -> bool:
    if column_index < 0 or column_index >= len(row):
        return False
    return normalizer(row[column_index]) == expected_value


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_date(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]
