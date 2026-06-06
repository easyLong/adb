"""Sheet selection helpers for date-partitioned documents."""

from __future__ import annotations

import re
from datetime import date

from apps.finance_crawler.integrations.tencent_docs.client import DocInfo, SheetInfo


def select_sheet_for_date(base_doc: DocInfo, sheets: list[SheetInfo], target_date: date | None) -> SheetInfo:
    if target_date is None:
        for sheet in sheets:
            if sheet.sheet_id == base_doc.sheet_id:
                return sheet
        return SheetInfo(file_id=base_doc.file_id, sheet_id=base_doc.sheet_id, title="")

    preferred = {
        target_date.strftime("%m%d"),
        target_date.strftime("%m-%d"),
        target_date.strftime("%Y%m%d"),
        target_date.isoformat(),
    }
    for sheet in sheets:
        normalized = _normalize_sheet_title(sheet.title)
        if normalized in {_normalize_sheet_title(item) for item in preferred}:
            return sheet
    for sheet in sheets:
        normalized = _normalize_sheet_title(sheet.title)
        if any(_normalize_sheet_title(item) in normalized for item in preferred):
            return sheet

    available = ", ".join(f"{sheet.title}({sheet.sheet_id})" for sheet in sheets)
    raise RuntimeError(f"sheet for date not found: {target_date.isoformat()}; available: {available}")


def parse_business_date_from_sheet_title(title: str) -> date | None:
    text = str(title or "").strip()
    match = re.search(r"(?P<year>20\d{2})[-_/]?(?P<month>\d{2})[-_/]?(?P<day>\d{2})", text)
    if match:
        return date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    match = re.search(r"(?<!\d)(?P<month>\d{2})[-_/]?(?P<day>\d{2})(?!\d)", text)
    if match:
        today = date.today()
        return date(today.year, int(match.group("month")), int(match.group("day")))
    return None


def _normalize_sheet_title(value: str) -> str:
    return re.sub(r"[\s_\-/.]+", "", str(value or "").strip().casefold())

