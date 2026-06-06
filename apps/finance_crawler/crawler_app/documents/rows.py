"""Convert spreadsheet rows into stable source rows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date

from apps.finance_crawler.crawler_app.documents.fields import ACCOUNT_NAME, POST_TIME, POST_URL


@dataclass(frozen=True, slots=True)
class DocumentSourceRow:
    row_index: int
    post_url: str
    account_name: str
    post_time: str
    values: dict[str, str]
    row_hash: str
    business_date: date | None = None


def extract_source_rows(
    rows: list[list[object]],
    columns: dict[str, int],
    *,
    start_row: int = 0,
    data_start_offset: int = 1,
    business_date: date | None = None,
    skip_empty_url: bool = True,
) -> list[DocumentSourceRow]:
    output: list[DocumentSourceRow] = []
    for offset, row in enumerate(rows[data_start_offset:], start=data_start_offset):
        values = _field_values(row, columns)
        post_url = values.get(POST_URL, "").strip()
        if skip_empty_url and not post_url:
            continue
        output.append(
            DocumentSourceRow(
                row_index=start_row + offset + 1,
                post_url=post_url,
                account_name=values.get(ACCOUNT_NAME, "").strip(),
                post_time=values.get(POST_TIME, "").strip(),
                values=values,
                row_hash=_row_hash(values),
                business_date=business_date,
            )
        )
    return output


def _field_values(row: list[object], columns: dict[str, int]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field_name, column_index in columns.items():
        values[field_name] = _cell_text(row, column_index)
    return values


def _cell_text(row: list[object], column_index: int) -> str:
    if column_index < 0 or column_index >= len(row):
        return ""
    return str(row[column_index] or "").strip()


def _row_hash(values: dict[str, str]) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
