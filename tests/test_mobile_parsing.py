import subprocess
import unittest
from datetime import datetime, date

from apps.finance_crawler.mobile.capture_engine import _is_input_injection_permission_error
from apps.finance_crawler.mobile.page_status import detect_page_status_from_texts
from apps.finance_crawler.mobile.crawler import is_transient_open_failure
from apps.finance_crawler.mobile.parsers import extract_account_name, extract_profile_fans_count
from apps.finance_crawler.workflows.article_details import (
    extract_tenpay_bottom_counts_from_ocr,
    extract_tenpay_title_from_ocr,
)
from apps.finance_crawler.workflows.docs_link_reads import (
    extract_read_count_from_records as extract_doc_link_read_count_from_records,
    extract_read_count_from_texts as extract_doc_link_read_count_from_texts,
)
from apps.finance_crawler.workflows.profile_post_reads import extract_read_count_from_texts, infer_post_date
from apps.finance_crawler.workflows.profile_metrics import (
    _daily_template_values,
    _next_append_row,
    _parse_profile_source_row,
)
from apps.finance_crawler.integrations.tencent_docs.client import DocInfo


class PageStatusTests(unittest.TestCase):
    def test_missing_content_page_is_not_found(self) -> None:
        status, error = detect_page_status_from_texts(["内容不见了，先去看看其他的吧", "返回"])

        self.assertEqual(status, "not_found")
        self.assertIn("内容不见了", error or "")

    def test_random_controls_are_not_enough_for_success(self) -> None:
        status, error = detect_page_status_from_texts(["返回", "更多", "打开", "分享", "设置"])

        self.assertEqual(status, "error")
        self.assertIsNotNone(error)

    def test_post_controls_indicate_success(self) -> None:
        status, error = detect_page_status_from_texts(["头像", "困", "关注", "9小时前", "9", "阅读"])

        self.assertEqual(status, "success")
        self.assertIsNone(error)


class AccountParserTests(unittest.TestCase):
    def test_missing_content_text_is_not_account_name(self) -> None:
        account = extract_account_name(["内容不见了，先去看看其他的吧", "返回"])

        self.assertEqual(account, "")


class ProfileMetricParserTests(unittest.TestCase):
    def test_extract_fans_count_from_profile_counters(self) -> None:
        records = [
            {"text": "0", "bounds": {"left": 50, "top": 736, "right": 78, "bottom": 793}},
            {"text": "\u5173\u6ce8", "bounds": {"left": 33, "top": 790, "right": 98, "bottom": 832}},
            {"text": "14", "bounds": {"left": 174, "top": 736, "right": 222, "bottom": 793}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 165, "top": 790, "right": 233, "bottom": 832}},
            {"text": "41", "bounds": {"left": 306, "top": 736, "right": 354, "bottom": 793}},
            {"text": "\u83b7\u8d5e", "bounds": {"left": 298, "top": 790, "right": 365, "bottom": 832}},
        ]

        self.assertEqual(extract_profile_fans_count(records), 14)

    def test_extract_fans_count_supports_wan_unit(self) -> None:
        records = [
            {"text": "1.2\u4e07", "bounds": {"left": 174, "top": 736, "right": 250, "bottom": 793}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 165, "top": 790, "right": 233, "bottom": 832}},
        ]

        self.assertEqual(extract_profile_fans_count(records), 12000)

    def test_extract_fans_count_treats_dot_as_thousands_separator(self) -> None:
        records = [
            {"text": "8.203", "bounds": {"left": 174, "top": 736, "right": 250, "bottom": 793}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 165, "top": 790, "right": 233, "bottom": 832}},
        ]

        self.assertEqual(extract_profile_fans_count(records), 8203)

    def test_extract_fans_count_from_inline_antfortune_counter(self) -> None:
        records = [
            {
                "text": "23\u5173\u6ce88743\u7c89\u4e1d3.7w\u83b7\u8d5e",
                "bounds": {"left": 33, "top": 658, "right": 506, "bottom": 765},
            },
        ]

        self.assertEqual(extract_profile_fans_count(records), 8743)

    def test_tenpay_profile_source_is_supported(self) -> None:
        parsed = _parse_profile_source_row(
            [
                "2026-05-27 00:00:00",
                "\u590f\u5c0f\u9c7c\u4eca\u5929\u53c8\u6323\u94b1\u4e86",
                "\u7406\u8d22\u901a",
                "https://www.tencentwm.com/h5/v6/pages/discussion/main/mycomment/index?userId=demo",
                "",
                "",
                "",
                "1\u7fa4",
            ],
            sheet_row_index=3,
            doc=DocInfo("file", "sheet"),
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["app_type"], "tenpay")

    def test_daily_template_values_clear_metric_columns(self) -> None:
        values = _daily_template_values(
            [
                "2026-05-27 00:00:00",
                "\u62ce\u58f6\u51b2",
                "\u7406\u8d22\u901a",
                "https://www.tencentwm.com/profile",
                "20000",
                "1",
                "99",
                "1\u7fa4",
            ],
            date(2026, 6, 4),
        )

        self.assertEqual(
            values,
            [
                "2026-06-04",
                "\u62ce\u58f6\u51b2",
                "\u7406\u8d22\u901a",
                "https://www.tencentwm.com/profile",
                "",
                "",
                "",
                "1\u7fa4",
            ],
        )

    def test_next_append_row_uses_last_non_empty_row(self) -> None:
        self.assertEqual(
            _next_append_row(
                [
                    ["header"],
                    ["2026-06-03", "name"],
                    ["", ""],
                ],
                0,
            ),
            3,
        )


class ProfilePostReadParserTests(unittest.TestCase):
    def test_infer_relative_hour_crosses_to_previous_date(self) -> None:
        inferred = infer_post_date("18\u5c0f\u65f6\u524d", now=datetime(2026, 6, 3, 16, 51))

        self.assertEqual(inferred, date(2026, 6, 2))

    def test_infer_minutes_stays_on_current_date(self) -> None:
        inferred = infer_post_date("35\u5206\u949f\u524d", now=datetime(2026, 6, 3, 16, 51))

        self.assertEqual(inferred, date(2026, 6, 3))

    def test_extract_read_count_from_detail_metadata(self) -> None:
        self.assertEqual(extract_read_count_from_texts(["18\u5c0f\u65f6\u524d 686\u9605\u8bfb \u5317\u4eac"]), 686)

    def test_invalid_short_date_is_ignored(self) -> None:
        inferred = infer_post_date("22-99", now=datetime(2026, 6, 3, 16, 51))

        self.assertIsNone(inferred)

    def test_default_max_posts_is_three(self) -> None:
        from apps.finance_crawler.config import Config

        self.assertEqual(Config.PROFILE_POST_READ_MAX_POSTS, 3)


class DocLinkReadParserTests(unittest.TestCase):
    def test_extract_read_count_from_inline_metadata(self) -> None:
        self.assertEqual(
            extract_doc_link_read_count_from_texts(["06-01 15:01 9\u9605\u8bfb \u56db\u5ddd"]),
            9,
        )

    def test_extract_read_count_from_split_ui_nodes(self) -> None:
        self.assertEqual(
            extract_doc_link_read_count_from_texts(["06-01 15:01", "18", "\u9605\u8bfb", "\u56db\u5ddd"]),
            18,
        )

    def test_extract_read_count_supports_wan_unit(self) -> None:
        self.assertEqual(extract_doc_link_read_count_from_texts(["1.2\u4e07\u9605\u8bfb"]), 12000)

    def test_extract_read_count_orders_records_by_bounds(self) -> None:
        records = [
            {"text": "\u9605\u8bfb", "bounds": {"top": 20, "left": 60}},
            {"text": "6", "bounds": {"top": 20, "left": 40}},
        ]

        self.assertEqual(extract_doc_link_read_count_from_records(records), 6)


class TenpayArticleParserTests(unittest.TestCase):
    def test_extract_title_from_first_screen_ocr_lines(self) -> None:
        rows = [
            {"text": "买基好养招财喵", "bounds": {"left": 187, "top": 303}},
            {"text": "走过路过，不要错过有奖竞", "bounds": {"left": 63, "top": 480}},
            {"text": "猜！下一个要大涨的会是谁？", "bounds": {"left": 60, "top": 570}},
            {"text": "讨论区", "bounds": {"left": 62, "top": 678}},
        ]

        self.assertEqual(
            extract_tenpay_title_from_ocr(rows),
            "走过路过，不要错过有奖竞猜！下一个要大涨的会是谁？",
        )

    def test_extract_comment_and_like_from_bottom_bar(self) -> None:
        rows = [
            {"text": "发表观点..", "bounds": {"left": 160, "top": 2243}},
            {"text": "339", "bounds": {"left": 668, "top": 2276}},
            {"text": "134", "bounds": {"left": 815, "top": 2276}},
            {"text": "17", "bounds": {"left": 965, "top": 2275}},
        ]

        self.assertEqual(
            extract_tenpay_bottom_counts_from_ocr(rows),
            {"comment_count": 339, "like_count": 134},
        )


class RecoveryClassifierTests(unittest.TestCase):
    def test_unknown_page_error_can_trigger_app_restart(self) -> None:
        self.assertTrue(
            is_transient_open_failure(
                {"status": "error", "error": "page status is unknown or too few controls were found"}
            )
        )

    def test_deleted_page_does_not_trigger_app_restart(self) -> None:
        self.assertFalse(is_transient_open_failure({"status": "not_found", "error": "内容不见了"}))

    def test_one_character_account_after_avatar_is_supported(self) -> None:
        account = extract_account_name(["头像", "困", "关注", "9小时前", "9", "阅读"])

        self.assertEqual(account, "困")

    def test_relative_time_after_avatar_is_not_account_name(self) -> None:
        account = extract_account_name(["头像", "5小时前", "关注", "阅读"])

        self.assertEqual(account, "")


class InputPermissionClassifierTests(unittest.TestCase):
    def test_adb_input_permission_error_is_detected(self) -> None:
        error = subprocess.CalledProcessError(
            255,
            ["adb", "shell", "input", "swipe"],
            stderr="java.lang.SecurityException: Injecting input events requires INJECT_EVENTS permission",
        )

        self.assertTrue(_is_input_injection_permission_error(error))

    def test_uiautomator_input_permission_error_is_detected(self) -> None:
        error = RuntimeError(
            "Unknown RPC error: -32001 java.lang.SecurityException: "
            "Injecting input events requires the caller to have the INJECT_EVENTS permission"
        )

        self.assertTrue(_is_input_injection_permission_error(error))


if __name__ == "__main__":
    unittest.main()
