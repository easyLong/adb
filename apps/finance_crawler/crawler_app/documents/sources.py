"""Document source adapters for crawler_app intake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.documents.sheet_selector import (
    business_date_for_sheet,
    select_sheet,
)
from apps.finance_crawler.integrations.tencent_docs import client


@dataclass(frozen=True, slots=True)
class DocumentSheetSnapshot:
    source_type: str
    doc_url: str
    file_id: str
    sheet_id: str
    sheet_title: str
    rows: list[list[object]]
    start_row: int
    business_date: date | None = None
    title: str = ""


class DocumentSource(Protocol):
    def load_sheet(
        self,
        *,
        target_date: date | None = None,
        range_a1: str | None = None,
        sheet_selector: dict[str, object] | None = None,
    ) -> DocumentSheetSnapshot:
        """Return one sheet snapshot for intake."""


@dataclass(frozen=True, slots=True)
class TencentDocsSource:
    doc_url: str | None = None

    def load_sheet(
        self,
        *,
        target_date: date | None = None,
        range_a1: str | None = None,
        sheet_selector: dict[str, object] | None = None,
    ) -> DocumentSheetSnapshot:
        doc_url_text = (self.doc_url or Config.QQ_DOC_URL or "").strip()
        if doc_url_text:
            doc_info = client.parse_doc_url_info(doc_url_text)
            base_doc = client.DocInfo(doc_info.file_id, doc_info.sheet_id)
            normalized_doc_url = doc_info.base_url
        else:
            base_doc = client.configured_doc()
            normalized_doc_url = Config.QQ_DOC_URL
        if not doc_url_text:
            doc_url_text = Config.QQ_DOC_URL

        sheets = client.fetch_file_sheets(base_doc.file_id)
        sheet = select_sheet(
            base_doc=base_doc,
            sheets=sheets,
            selector=sheet_selector,
            target_date=target_date,
        )
        doc = sheet.doc
        rows, start_row = client.fetch_grid(range_a1 or Config.DOC_LINK_READS_READ_RANGE, doc=doc)
        business_date = business_date_for_sheet(sheet, target_date)
        return DocumentSheetSnapshot(
            source_type="tencent_docs",
            doc_url=normalized_doc_url or doc_url_text,
            file_id=doc.file_id,
            sheet_id=doc.sheet_id,
            sheet_title=sheet.title,
            rows=rows,
            start_row=start_row,
            business_date=business_date,
        )
