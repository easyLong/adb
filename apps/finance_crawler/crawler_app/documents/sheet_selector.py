"""Configurable sheet selection for document task configs."""

from __future__ import annotations

from datetime import date
from typing import Any

from apps.finance_crawler.crawler_app.documents.sheets import parse_business_date_from_sheet_title
from apps.finance_crawler.integrations.tencent_docs.client import DocInfo, SheetInfo


def select_sheet(
    *,
    base_doc: DocInfo,
    sheets: list[SheetInfo],
    selector: dict[str, Any] | None,
    target_date: date | None = None,
) -> SheetInfo:
    selected = select_sheets(
        base_doc=base_doc,
        sheets=sheets,
        selector=selector,
        target_date=target_date,
    )
    return selected[0]


def select_sheets(
    *,
    base_doc: DocInfo,
    sheets: list[SheetInfo],
    selector: dict[str, Any] | None,
    target_date: date | None = None,
) -> list[SheetInfo]:
    selector = selector or {}
    mode = str(selector.get("mode") or ("date_sheet" if target_date else "linked_tab")).strip()

    if mode == "date_sheet":
        return _select_date_sheets(base_doc, sheets, target_date)
    if mode == "fixed_sheet":
        return [_select_sheet_id(sheets, str(selector.get("sheet_id") or selector.get("fallback_sheet_id") or ""))]
    if mode == "linked_tab":
        return [_select_sheet_id(sheets, base_doc.sheet_id or str(selector.get("fallback_sheet_id") or ""))]
    if mode == "sheet_title":
        return [_select_sheet_title(sheets, str(selector.get("title") or ""))]
    if mode == "sheet_title_contains":
        return [_select_sheet_title_contains(sheets, str(selector.get("keyword") or ""))]
    if mode == "sheet_group":
        return _select_sheet_group(sheets, selector.get("sheet_ids") or [])
    raise ValueError(f"unsupported sheet selector mode: {mode}")


def _select_date_sheet(base_doc: DocInfo, sheets: list[SheetInfo], target_date: date | None) -> SheetInfo:
    return _select_date_sheets(base_doc, sheets, target_date)[0]


def _select_date_sheets(base_doc: DocInfo, sheets: list[SheetInfo], target_date: date | None) -> list[SheetInfo]:
    if target_date is None:
        if base_doc.sheet_id:
            return [_select_sheet_id(sheets, base_doc.sheet_id)]
        raise RuntimeError("target_date is required for date_sheet selector")

    preferred = {
        target_date.strftime("%m%d"),
        target_date.strftime("%m-%d"),
        target_date.strftime("%Y%m%d"),
        target_date.isoformat(),
    }
    normalized_preferred = {_normalize(item) for item in preferred}
    matches = []
    seen: set[str] = set()
    for sheet in sheets:
        title = _normalize(sheet.title)
        if title in normalized_preferred or any(item in title for item in normalized_preferred):
            if sheet.sheet_id not in seen:
                matches.append(sheet)
                seen.add(sheet.sheet_id)
    if matches:
        return matches

    available = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in sheets)
    raise RuntimeError(f"sheet for date not found: {target_date.isoformat()}; available: {available}")


def _select_sheet_id(sheets: list[SheetInfo], sheet_id: str) -> SheetInfo:
    if not sheet_id:
        raise RuntimeError("sheet_id is required for this sheet selector")
    for sheet in sheets:
        if sheet.sheet_id == sheet_id:
            return sheet
    available = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in sheets)
    raise RuntimeError(f"sheet_id not found: {sheet_id}; available: {available}")


def _select_sheet_title(sheets: list[SheetInfo], title: str) -> SheetInfo:
    if not title:
        raise RuntimeError("title is required for sheet_title selector")
    normalized_title = _normalize(title)
    for sheet in sheets:
        if _normalize(sheet.title) == normalized_title:
            return sheet
    available = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in sheets)
    raise RuntimeError(f"sheet title not found: {title}; available: {available}")


def _select_sheet_title_contains(sheets: list[SheetInfo], keyword: str) -> SheetInfo:
    if not keyword:
        raise RuntimeError("keyword is required for sheet_title_contains selector")
    normalized_keyword = _normalize(keyword)
    matches = [sheet for sheet in sheets if normalized_keyword in _normalize(sheet.title)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        available = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in sheets)
        raise RuntimeError(f"sheet title keyword not found: {keyword}; available: {available}")
    matched = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in matches)
    raise RuntimeError(f"sheet title keyword is ambiguous: {keyword}; matches: {matched}")


def _select_sheet_group(sheets: list[SheetInfo], sheet_ids: Any) -> list[SheetInfo]:
    ids = [str(item) for item in sheet_ids if str(item)]
    if not ids:
        raise RuntimeError("sheet_ids is required for sheet_group selector")
    selected = []
    for sheet_id in ids:
        for sheet in sheets:
            if sheet.sheet_id == sheet_id:
                selected.append(sheet)
                break
    if selected:
        return selected
    available = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in sheets)
    raise RuntimeError(f"none of sheet_ids found: {ids}; available: {available}")


def business_date_for_sheet(sheet: SheetInfo, target_date: date | None) -> date | None:
    return target_date or parse_business_date_from_sheet_title(sheet.title)


def _normalize(value: str) -> str:
    return "".join(ch for ch in str(value or "").strip().casefold() if ch not in " \t\r\n_-/.")
