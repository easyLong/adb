"""Generate summary reports from MySQL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.domain.task_types import DETAIL_CRAWL_TASK_TYPE, INITIAL_CHECK_TASK_TYPE
from apps.finance_crawler.integrations.tencent_docs import client as tencent_docs_client
from apps.finance_crawler.integrations.tencent_docs import write_requests as tencent_docs_write_requests
from apps.finance_crawler.storage.db import get_conn, log_task
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("report")

_REPORT_HEADERS = [
    "日期",
    "产品",
    "预发帖",
    "程序采集失败",
    "发帖成功",
    "发帖失败",
    "成功率",
    "阅读量超30",
    "最高阅读",
    "总阅读",
    "平均阅读量",
]
_REPORT_PRODUCTS = ("精选制造", "新兴产业", "舆情监测（内投）")
_REPORT_TEXT_FORMAT = {"font": "SimSun", "fontSize": 8}


@dataclass(frozen=True)
class ProductReportRow:
    report_date: date
    product: str
    total: int
    program_failed: int
    success: int
    post_failed: int
    over_threshold: int
    max_read: int
    total_read: int
    avg_read: Decimal
    include_metrics: bool = True

    @property
    def success_rate(self) -> Decimal:
        if self.total <= 0:
            return Decimal("0")
        return Decimal(self.success) / Decimal(self.total)

    def to_sheet_values(self) -> list[Any]:
        date_text = _report_date_text(self.report_date)
        if not self.include_metrics:
            return [date_text, self.product] + [""] * (len(_REPORT_HEADERS) - 2)
        return [
            date_text,
            self.product,
            self.total,
            self.program_failed,
            self.success,
            self.post_failed,
            _format_percent(self.success_rate),
            self.over_threshold,
            self.max_read,
            self.total_read,
            _format_decimal(self.avg_read),
        ]


def generate_report(target_date: date | str | None = None) -> str:
    target_date = resolve_report_date(target_date)
    return _generate_framework_report(target_date)


def resolve_report_date(target_date: date | str | None = None) -> date:
    if target_date is None:
        return date.today() - timedelta(days=1)
    if isinstance(target_date, str):
        return date.fromisoformat(target_date)
    return target_date


def _generate_framework_report(target_date: date) -> str:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions
                WHERE task_type = %s
                  AND DATE(source_time) = %s
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date),
            )
            total = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions s
                JOIN crawl_task_executions e ON e.id = s.latest_execution_id
                WHERE s.task_type = %s
                  AND DATE(s.source_time) = %s
                  AND e.status = 'success'
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date),
            )
            success = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions s
                JOIN crawl_task_executions e ON e.id = s.latest_execution_id
                WHERE s.task_type = %s
                  AND DATE(s.source_time) = %s
                  AND e.status IN ('deleted', 'error')
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date),
            )
            failed = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions i
                WHERE i.task_type = %s
                  AND DATE(i.source_time) = %s
                  AND i.status = 'not_found'
                """,
                (INITIAL_CHECK_TASK_TYPE, target_date),
            )
            initial_check_failed = cursor.fetchone()["cnt"]

            read_expr = "CAST(JSON_UNQUOTE(JSON_EXTRACT(e.metrics_json, '$.read_count')) AS UNSIGNED)"
            cursor.execute(
                f"""
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions s
                JOIN crawl_task_executions e ON e.id = s.latest_execution_id
                WHERE s.task_type = %s
                  AND DATE(s.source_time) = %s
                  AND e.status = 'success'
                  AND {read_expr} > %s
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date, Config.READ_COUNT_THRESHOLD),
            )
            over_threshold = cursor.fetchone()["cnt"]

            cursor.execute(
                f"""
                SELECT {read_expr} AS read_count
                FROM crawl_task_submissions s
                JOIN crawl_task_executions e ON e.id = s.latest_execution_id
                WHERE s.task_type = %s
                  AND DATE(s.source_time) = %s
                  AND e.status = 'success'
                ORDER BY read_count DESC
                LIMIT %s
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date, Config.REPORT_TOP_N),
            )
            top_rows = cursor.fetchall()
            top_str = "/".join(str(row["read_count"]) for row in top_rows if row["read_count"] is not None) or "暂无"

            product_rows = _fetch_product_report_rows(cursor, target_date, read_expr)

        report = (
            f"{target_date.month}月{target_date.day}日预发帖{total}条，"
            f"初检失败{initial_check_failed}条，采集失败{failed}条，成功发帖{success}条，"
            f"阅读量超过{Config.READ_COUNT_THRESHOLD}的有{over_threshold}条，"
            f"阅读数前三数据为{top_str}"
        )
        _save_report_file(target_date, report)
        write_report_to_tencent_docs(target_date, product_rows)
        logger.info("framework report generated: %s", report)
        log_task("report", "success", report)
        return report
    except Exception as exc:
        logger.exception("framework report generation failed")
        log_task("report", "error", str(exc))
        raise
    finally:
        conn.close()


def _save_report_file(target_date: date, report: str) -> None:
    path = Config.REPORT_DIR / f"{target_date}.txt"
    path.write_text(
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{report}\n",
        encoding="utf-8",
    )


def _fetch_product_report_rows(cursor, target_date: date, read_expr: str) -> list[ProductReportRow]:
    cursor.execute(
        f"""
        SELECT
            COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(s.source_locator_json, '$.sheet_title')), ''), s.source_name, s.source_type) AS product_source,
            COUNT(*) AS total,
            SUM(CASE WHEN e.status IN ('deleted', 'error') THEN 1 ELSE 0 END) AS program_failed,
            SUM(CASE WHEN e.status = 'success' THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN s.status = 'not_found' THEN 1 ELSE 0 END) AS post_failed,
            SUM(CASE WHEN e.status = 'success' AND {read_expr} > %s THEN 1 ELSE 0 END) AS over_threshold,
            MAX(CASE WHEN e.status = 'success' THEN {read_expr} ELSE 0 END) AS max_read,
            SUM(CASE WHEN e.status = 'success' THEN COALESCE({read_expr}, 0) ELSE 0 END) AS total_read,
            AVG(CASE WHEN e.status = 'success' THEN COALESCE({read_expr}, 0) ELSE NULL END) AS avg_read
        FROM crawl_task_submissions s
        LEFT JOIN crawl_task_executions e ON e.id = s.latest_execution_id
        WHERE s.task_type = %s
          AND DATE(s.source_time) = %s
        GROUP BY product_source
        ORDER BY product_source
        """,
        (Config.READ_COUNT_THRESHOLD, DETAIL_CRAWL_TASK_TYPE, target_date),
    )
    rows: dict[str, ProductReportRow] = {}
    for item in cursor.fetchall():
        product = normalize_report_product(item.get("product_source") or "")
        if not product:
            product = "未命名产品"
        current = ProductReportRow(
            report_date=target_date,
            product=product,
            total=_int_value(item.get("total")),
            program_failed=_int_value(item.get("program_failed")),
            success=_int_value(item.get("success_count")),
            post_failed=_int_value(item.get("post_failed")),
            over_threshold=_int_value(item.get("over_threshold")),
            max_read=_int_value(item.get("max_read")),
            total_read=_int_value(item.get("total_read")),
            avg_read=_decimal_value(item.get("avg_read")),
        )
        if product in rows:
            previous = rows[product]
            rows[product] = ProductReportRow(
                report_date=target_date,
                product=product,
                total=previous.total + current.total,
                program_failed=previous.program_failed + current.program_failed,
                success=previous.success + current.success,
                post_failed=previous.post_failed + current.post_failed,
                over_threshold=previous.over_threshold + current.over_threshold,
                max_read=max(previous.max_read, current.max_read),
                total_read=previous.total_read + current.total_read,
                avg_read=_weighted_avg(previous, current),
            )
        else:
            rows[product] = current
    return list(rows.values())


def write_report_to_tencent_docs(target_date: date, rows: list[ProductReportRow]) -> None:
    ordered_rows = _ordered_report_rows(target_date, rows)
    if not ordered_rows:
        logger.info("skip Tencent Docs report writeback: no product rows date=%s", target_date)
        return

    report_sheet = _resolve_report_sheet()
    if report_sheet is None:
        logger.warning("skip Tencent Docs report writeback: sheet not found title=%s", Config.TENCENT_DOC_REPORT_SHEET_TITLE)
        return

    doc = report_sheet.doc
    sheet_rows, start_row = tencent_docs_client.fetch_grid("A1:K2000", doc=doc)
    header_row_index = _ensure_report_headers(sheet_rows, start_row, doc)
    row_indexes = _report_row_indexes(sheet_rows, start_row, header_row_index, target_date)
    reusable_row_indexes = _reusable_blank_report_row_indexes(sheet_rows, start_row, header_row_index, target_date)
    next_row_index = _next_report_row_index(sheet_rows, start_row, header_row_index)

    requests_payload: list[dict[str, Any]] = []
    for item in ordered_rows:
        row_index = row_indexes.get(item.product)
        if row_index is None:
            row_index = reusable_row_indexes.pop(item.product, None)
            if row_index is None:
                row_index = next_row_index
                next_row_index += 1
        requests_payload.append(
            tencent_docs_write_requests.row_cells_request(
                row_index,
                0,
                item.to_sheet_values(),
                text_format=_REPORT_TEXT_FORMAT,
                doc=doc,
            )
        )

    tencent_docs_client.post_batch_update(requests_payload, "daily_report", doc=doc)
    logger.info("Tencent Docs report writeback finished date=%s rows=%s sheet=%s", target_date, len(ordered_rows), report_sheet.title)


def _ordered_report_rows(target_date: date, rows: list[ProductReportRow]) -> list[ProductReportRow]:
    by_product = {row.product: row for row in rows}
    ordered: list[ProductReportRow] = []
    for product in _REPORT_PRODUCTS:
        row = by_product.get(product)
        if row is None:
            row = ProductReportRow(
                report_date=target_date,
                product=product,
                total=0,
                program_failed=0,
                success=0,
                post_failed=0,
                over_threshold=0,
                max_read=0,
                total_read=0,
                avg_read=Decimal("0"),
                include_metrics=False,
            )
        ordered.append(row)
    for row in rows:
        if row.product not in _REPORT_PRODUCTS:
            ordered.append(row)
    return ordered


def _resolve_report_sheet() -> tencent_docs_client.SheetInfo | None:
    title = Config.TENCENT_DOC_REPORT_SHEET_TITLE.strip()
    if not title:
        return None
    for sheet in tencent_docs_client.fetch_file_sheets():
        if sheet.title.strip() == title:
            return sheet
    return None


def _ensure_report_headers(rows: list[list[str]], start_row: int, doc: tencent_docs_client.DocInfo) -> int:
    header_row_index = _find_report_header_row(rows, start_row)
    if header_row_index is not None:
        return header_row_index

    requests_payload = [
        tencent_docs_write_requests.row_cells_request(1, 0, ["内部数据表"], doc=doc),
        tencent_docs_write_requests.row_cells_request(2, 0, _REPORT_HEADERS, doc=doc),
    ]
    tencent_docs_client.post_batch_update(requests_payload, "daily_report_headers", doc=doc)
    return 2


def _find_report_header_row(rows: list[list[str]], start_row: int) -> int | None:
    for offset, row in enumerate(rows):
        if len(row) >= 2 and row[0].strip() == "日期" and row[1].strip() == "产品":
            return start_row + offset + 1
    return None


def _report_row_indexes(
    rows: list[list[str]],
    start_row: int,
    header_row_index: int,
    target_date: date,
) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for offset, row in enumerate(rows):
        row_index = start_row + offset + 1
        if row_index <= header_row_index or len(row) < 2:
            continue
        row_date = _parse_report_date(row[0])
        product = (row[1] or "").strip()
        if row_date == target_date and product:
            indexes.setdefault(product, row_index)
    return indexes


def _reusable_blank_report_row_indexes(
    rows: list[list[str]],
    start_row: int,
    header_row_index: int,
    target_date: date,
) -> dict[str, int]:
    target_rows = []
    for offset, row in enumerate(rows):
        row_index = start_row + offset + 1
        if row_index <= header_row_index or len(row) < 2:
            continue
        if _parse_report_date(row[0]) == target_date:
            target_rows.append(row_index)
    if not target_rows:
        return {}

    indexes: dict[str, int] = {}
    for offset, row in enumerate(rows):
        row_index = start_row + offset + 1
        if row_index <= max(target_rows):
            continue
        if not any((cell or "").strip() for cell in row[: len(_REPORT_HEADERS)]):
            break
        product = (row[1] or "").strip()
        if product in _REPORT_PRODUCTS and _parse_report_date(row[0]) != target_date and _row_metrics_blank(row):
            indexes.setdefault(product, row_index)
    return indexes


def _next_report_row_index(rows: list[list[str]], start_row: int, header_row_index: int) -> int:
    last_non_empty = header_row_index
    for offset, row in enumerate(rows):
        row_index = start_row + offset + 1
        if row_index <= header_row_index:
            continue
        if any((cell or "").strip() for cell in row[: len(_REPORT_HEADERS)]):
            last_non_empty = row_index
    return last_non_empty + 1


def _row_metrics_blank(row: list[str]) -> bool:
    metrics = row[2 : len(_REPORT_HEADERS)]
    return not any((cell or "").strip() for cell in metrics)


def normalize_report_product(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\d{4}\s*[-_ ]*", "", text)
    text = re.sub(r"^\d{1,2}[./-]\d{1,2}\s*[-_ ]*", "", text)
    text = re.sub(r"^(\d{2})(\d{2})", "", text)
    text = re.sub(r"[-_ ]*\d+\s*$", "", text)
    text = text.strip("-_ ")
    if text == "精选-制造":
        return "精选制造"
    return text


def _report_date_text(value: date) -> str:
    return value.isoformat()


def _parse_report_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def _int_value(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _decimal_value(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _format_decimal(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.0001"))
    return format(rounded.normalize(), "f")


def _format_percent(value: Decimal) -> str:
    rounded = (value * Decimal("100")).quantize(Decimal("0.01"))
    return f"{format(rounded.normalize(), 'f')}%"


def _weighted_avg(left: ProductReportRow, right: ProductReportRow) -> Decimal:
    total_success = left.success + right.success
    if total_success <= 0:
        return Decimal("0")
    return Decimal(left.total_read + right.total_read) / Decimal(total_success)


if __name__ == "__main__":
    print(generate_report())
