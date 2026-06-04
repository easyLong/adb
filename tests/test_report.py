from datetime import date, timedelta
from decimal import Decimal
import unittest

from apps.finance_crawler.integrations.tencent_docs.client import DocInfo
from apps.finance_crawler.integrations.tencent_docs.write_requests import row_cells_request
from apps.finance_crawler.services.report import ProductReportRow, _ordered_report_rows, normalize_report_product, resolve_report_date


class ReportDateTests(unittest.TestCase):
    def test_default_report_date_is_yesterday(self) -> None:
        self.assertEqual(resolve_report_date(), date.today() - timedelta(days=1))

    def test_explicit_report_date_is_respected(self) -> None:
        self.assertEqual(resolve_report_date("2026-05-28"), date(2026, 5, 28))


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
        self.assertEqual(ordered[2].to_sheet_values(), ["2026-05-29", "舆情监测（内投）"] + [""] * 9)

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
