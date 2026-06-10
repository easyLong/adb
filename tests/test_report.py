from datetime import date, timedelta
from decimal import Decimal
import unittest

from apps.finance_crawler.integrations.tencent_docs.client import DocInfo
from apps.finance_crawler.integrations.tencent_docs.write_requests import row_cells_request
from apps.finance_crawler.services.report import (
    ProductReportRow,
    _classify_sheet_read_count,
    _product_report_row_from_sheet,
    normalize_report_product,
    resolve_report_date,
    _ordered_report_rows,
)


class ReportDateTests(unittest.TestCase):
    def test_default_report_date_is_yesterday(self) -> None:
        self.assertEqual(resolve_report_date(), date.today() - timedelta(days=1))

    def test_explicit_report_date_is_respected(self) -> None:
        self.assertEqual(resolve_report_date("2026-05-28"), date(2026, 5, 28))

    def test_weekend_report_date_is_detected(self) -> None:
        from apps.finance_crawler.services.report import _is_weekend

        self.assertTrue(_is_weekend(date(2026, 6, 6)))
        self.assertTrue(_is_weekend(date(2026, 6, 7)))
        self.assertFalse(_is_weekend(date(2026, 6, 5)))


class ReportProductTests(unittest.TestCase):
    def test_normalize_selected_manufacturing_sheet_title(self) -> None:
        self.assertEqual(normalize_report_product("0529-精选-制造"), "精选制造")

    def test_normalize_industry_sheet_title(self) -> None:
        self.assertEqual(normalize_report_product("0529新兴产业-300"), "新兴产业")

    def test_normalize_public_opinion_sheet_title(self) -> None:
        self.assertEqual(
            normalize_report_product("0529-\u7ea2\u571f\u8206\u60c5\u68c0\u76d1\u6d4b"),
            "\u8206\u60c5\u76d1\u6d4b\uff08\u5185\u6295\uff09",
        )

    def test_report_writeback_always_has_three_product_rows(self) -> None:
        row = ProductReportRow(
            report_date=date(2026, 5, 29),
            product="精选制造",
            total=193,
            program_failed=3,
            success=80,
            post_failed=110,
            over_threshold=0,
            max_read=20,
            total_read=293,
            avg_read=Decimal("3.6625"),
        )

        ordered = _ordered_report_rows(date(2026, 5, 29), [row])

        self.assertEqual([item.product for item in ordered], ["精选制造", "新兴产业", "舆情监测（内投）"])
        self.assertEqual(ordered[2].to_sheet_values(), ["2026-05-29", "舆情监测（内投）", 0, 0, 0, 0, "0%", 0, 0, 0, "0"])

    def test_report_date_format_has_no_time(self) -> None:
        row = ProductReportRow(
            report_date=date(2026, 5, 29),
            product="精选制造",
            total=1,
            program_failed=0,
            success=1,
            post_failed=0,
            over_threshold=0,
            max_read=5,
            total_read=5,
            avg_read=Decimal("5"),
        )

        self.assertEqual(row.to_sheet_values()[0], "2026-05-29")

    def test_success_rate_is_percent_text(self) -> None:
        row = ProductReportRow(
            report_date=date(2026, 5, 29),
            product="精选制造",
            total=193,
            program_failed=3,
            success=80,
            post_failed=110,
            over_threshold=0,
            max_read=20,
            total_read=293,
            avg_read=Decimal("3.6625"),
        )

        self.assertEqual(row.to_sheet_values()[6], "41.45%")


class SheetReportAggregationTests(unittest.TestCase):
    def test_sheet_current_values_drive_report_metrics(self) -> None:
        rows = [
            ["帖子链接", "阅读数"],
            ["https://ur.alipay.com/a", "12"],
            ["https://ur.alipay.com/b", "N"],
            ["https://ur.alipay.com/c", ""],
            ["", "99"],
            ["https://ur.alipay.com/d", "1.5万"],
        ]

        row, read_counts, problems = _product_report_row_from_sheet(
            date(2026, 6, 8),
            "0608-精选-制造",
            rows,
            0,
        )

        self.assertEqual(problems, [])
        self.assertEqual(row.product, "精选制造")
        self.assertEqual(row.total, 4)
        self.assertEqual(row.success, 2)
        self.assertEqual(row.post_failed, 1)
        self.assertEqual(row.program_failed, 1)
        self.assertEqual(row.over_threshold, 1)
        self.assertEqual(row.max_read, 15000)
        self.assertEqual(row.total_read, 15012)
        self.assertEqual(row.avg_read, Decimal("7506"))
        self.assertEqual(read_counts, [12, 15000])

    def test_non_numeric_technical_text_is_program_failed(self) -> None:
        status, read_count = _classify_sheet_read_count("connection error")

        self.assertEqual(status, "program_failed")
        self.assertIsNone(read_count)


class TencentDocsRequestTests(unittest.TestCase):
    def test_row_cells_request_can_apply_text_format_to_all_cells(self) -> None:
        request = row_cells_request(
            3,
            0,
            ["a", "b"],
            text_format={"font": "SimSun", "fontSize": 8},
            doc=DocInfo("file", "sheet"),
        )

        cells = request["updateRangeRequest"]["gridData"]["rows"][0]["values"]
        self.assertEqual(cells[0]["cellFormat"]["textFormat"]["fontSize"], 8)
        self.assertEqual(cells[1]["cellFormat"]["textFormat"]["font"], "SimSun")


if __name__ == "__main__":
    unittest.main()
