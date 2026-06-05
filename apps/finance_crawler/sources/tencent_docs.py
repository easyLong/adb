"""Tencent Docs source adapter."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.domain.records import SourceRecord
from apps.finance_crawler.integrations.tencent_docs import client as tencent_docs_client
from apps.finance_crawler.integrations.tencent_docs import columns as tencent_docs_columns
from apps.finance_crawler.integrations.tencent_docs import write_requests
from apps.finance_crawler.utils import tabular_links
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("tencent_docs_source")

_HEADER_COMMENT_COUNT = "\u8bc4\u8bba\u6570"
_HEADER_REMARK = "\u5907\u6ce8"


class TencentDocsSource:
    """Read candidate links from one or more Tencent Docs sheets."""

    source_type = "tencent_docs"

    def __init__(self, limit: int | None = None) -> None:
        self.limit = Config.FETCH_LIMIT if limit is None else limit
        self.doc = tencent_docs_client.configured_doc()

    @property
    def source_name(self) -> str:
        return f"{self.doc.file_id}:multi"

    def fetch_candidates(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for sheet in self._target_sheets():
            doc = sheet.doc
            logger.info("scan Tencent Docs sheet: %s (%s)", sheet.title, sheet.sheet_id)
            rows, start_row = tencent_docs_client.fetch_grid(doc=doc)
            _ensure_result_headers(rows, start_row, doc=doc)
            columns = tencent_docs_columns.resolve_columns(
                rows,
                start_row,
                {
                    "post_time": tencent_docs_columns.MAIN_COLUMN_ALIASES["post_time"],
                    "url": tencent_docs_columns.MAIN_COLUMN_ALIASES["url"],
                },
                {
                    "post_time": Config.QQ_COL_POST_TIME,
                    "url": Config.QQ_COL_URL,
                },
                strict_fallback_title=True,
            )
            sheet_candidates = tabular_links.eligible_candidates(
                rows,
                start_row,
                sheet.title,
                source_time_col=columns["post_time"],
                url_col=columns["url"],
            )
            for item in sheet_candidates:
                item.update(
                    {
                        "file_id": sheet.file_id,
                        "sheet_id": sheet.sheet_id,
                        "sheet_title": sheet.title,
                        "post_time_col_index": columns["post_time"],
                        "url_col_index": columns["url"],
                    }
                )
            candidates.extend(sheet_candidates)
            if self.limit and self.limit > 0 and len(candidates) >= self.limit:
                candidates = candidates[: self.limit]
                break

        tabular_links.save_latest_candidates(candidates)
        by_sheet = _sheet_summary(candidates)
        logger.info(
            "Tencent Docs source candidates=%s limit=%s sheets=%s",
            len(candidates),
            self.limit,
            by_sheet or "none",
        )
        return candidates

    def fetch_records(self) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for item in self.fetch_candidates():
            row_index = item["row_index"]
            file_id = item["file_id"]
            sheet_id = item["sheet_id"]
            source_name = f"{file_id}:{sheet_id}"
            records.append(
                SourceRecord(
                    record_id=f"{file_id}:{sheet_id}:{row_index}",
                    source_type=self.source_type,
                    source_name=source_name,
                    url=item["url"],
                    app_type=item.get("source_app"),
                    source_time=item.get("source_time"),
                    locator={
                        "file_id": file_id,
                        "sheet_id": sheet_id,
                        "sheet_title": item.get("sheet_title"),
                        "row_index": row_index,
                        "source_time_text": item.get("source_time_text"),
                        "detail_only": item.get("detail_only"),
                        "post_time_col_index": item.get("post_time_col_index"),
                        "url_col_index": item.get("url_col_index"),
                    },
                    raw=_json_safe_item(item),
                )
            )
        return records

    def _target_sheets(self) -> list[tencent_docs_client.SheetInfo]:
        mode = (Config.QQ_SCAN_MODE or "today").strip().lower()
        if mode == "single":
            title = tencent_docs_client.fetch_sheet_title(self.doc)
            return [tencent_docs_client.SheetInfo(self.doc.file_id, self.doc.sheet_id, title)]

        sheets = tencent_docs_client.fetch_file_sheets(self.doc.file_id)
        if mode == "all":
            return sheets

        title_filter = Config.QQ_SHEET_TITLE_FILTER.strip()
        if mode == "filter" or title_filter:
            if not title_filter:
                logger.warning("TENCENT_DOC_SCAN_MODE=filter requires TENCENT_DOC_SHEET_TITLE_FILTER")
                return []
            return [sheet for sheet in sheets if title_filter in sheet.title]

        target_date = _scan_date()
        output = []
        for sheet in sheets:
            parsed = tabular_links.parse_sheet_date(sheet.title)
            if not parsed:
                continue
            sheet_date = date(parsed[0], parsed[1], parsed[2])
            if sheet_date == target_date:
                output.append(sheet)
        return output


def _scan_date() -> date:
    if Config.QQ_SCAN_DATE:
        return datetime.strptime(Config.QQ_SCAN_DATE, "%Y-%m-%d").date()
    return datetime.now().date()


def _sheet_summary(candidates: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for item in candidates:
        key = f"{item.get('sheet_title') or item.get('sheet_id')}"
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _json_safe_item(item: dict[str, Any]) -> dict[str, Any]:
    copied = dict(item)
    value = copied.get("source_time")
    if hasattr(value, "isoformat"):
        copied["source_time"] = value.isoformat(sep=" ")
    return copied


def _ensure_result_headers(
    rows: list[list[str]],
    start_row: int,
    *,
    doc: tencent_docs_client.DocInfo | None = None,
) -> None:
    if start_row != 0 or not rows:
        return

    header_cells = _header_cells(doc=doc)
    header_format = _reference_header_format(header_cells)
    columns = tencent_docs_columns.resolve_columns(
        rows,
        start_row,
        {
            "comment_count": tencent_docs_columns.MAIN_COLUMN_ALIASES["comment_count"],
            "detail_status": tencent_docs_columns.MAIN_COLUMN_ALIASES["detail_status"],
        },
        {
            "comment_count": Config.QQ_COL_COMMENT_COUNT,
            "detail_status": Config.QQ_COL_DETAIL_STATUS,
        },
    )
    requests_payload: list[dict[str, Any]] = []
    if _header_needs_update(
        header_cells,
        columns["comment_count"],
        _HEADER_COMMENT_COUNT,
        header_format,
        tencent_docs_columns.MAIN_COLUMN_ALIASES["comment_count"],
    ):
        requests_payload.append(
            write_requests.cell_request(
                1,
                columns["comment_count"],
                _HEADER_COMMENT_COUNT,
                cell_format=header_format,
                doc=doc,
            )
        )
    if _header_needs_update(
        header_cells,
        columns["detail_status"],
        _HEADER_REMARK,
        header_format,
        tencent_docs_columns.MAIN_COLUMN_ALIASES["detail_status"],
    ):
        requests_payload.append(
            write_requests.cell_request(
                1,
                columns["detail_status"],
                _HEADER_REMARK,
                cell_format=header_format,
                doc=doc,
            )
        )
    if requests_payload:
        try:
            tencent_docs_client.post_batch_update(requests_payload, "ensure_result_headers", doc=doc)
        except Exception as exc:
            logger.warning("Tencent Docs result header update skipped: %s", exc)


def _header_cells(doc: tencent_docs_client.DocInfo | None = None) -> list[dict[str, Any]]:
    try:
        grid_data = tencent_docs_client.fetch_raw_grid("A1:Q1", doc=doc)
    except Exception as exc:
        logger.warning("failed to read header format: %s", exc)
        return []
    return grid_data.get("rows", [{}])[0].get("values", [])


def _header_needs_update(
    cells: list[dict[str, Any]],
    col_index: int,
    expected_text: str,
    expected_format: dict[str, Any] | None,
    aliases: tuple[str, ...],
) -> bool:
    if col_index < 0:
        return False
    if len(cells) <= col_index:
        return True
    cell = cells[col_index]
    text = tencent_docs_client.cell_to_text(cell)
    if not text:
        return True
    normalized = tencent_docs_columns.normalize_title(text)
    recognized = any(
        alias_text and (alias_text == normalized or alias_text in normalized or normalized in alias_text)
        for alias_text in (tencent_docs_columns.normalize_title(alias) for alias in aliases + (expected_text,))
    )
    if not recognized:
        logger.warning("Tencent Docs header update skipped for non-empty unrecognized col=%s title=%s", col_index, text)
        return False
    if text != expected_text:
        return False
    if expected_format and cell.get("cellFormat") != expected_format:
        return True
    return False


def _reference_header_format(cells: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cells:
        return None

    header_rows = [[tencent_docs_client.cell_to_text(cell) for cell in cells]]
    columns = tencent_docs_columns.resolve_columns(
        header_rows,
        0,
        {
            "read_count": tencent_docs_columns.MAIN_COLUMN_ALIASES["read_count"],
            "url": tencent_docs_columns.MAIN_COLUMN_ALIASES["url"],
            "account_name": tencent_docs_columns.MAIN_COLUMN_ALIASES["account_name"],
        },
        {
            "read_count": Config.QQ_COL_READ_COUNT,
            "url": Config.QQ_COL_URL,
            "account_name": Config.QQ_COL_ACCOUNT_NAME,
        },
    )
    for col_index in (
        columns["read_count"],
        columns["url"],
        columns["account_name"],
    ):
        if len(cells) <= col_index:
            continue
        cell_format = cells[col_index].get("cellFormat")
        if cell_format:
            return deepcopy(cell_format)
    return None
