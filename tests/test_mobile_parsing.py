import subprocess
import unittest
from datetime import datetime, date
from pathlib import Path
from unittest.mock import patch

from apps.finance_crawler.crawlers.base import CapturePlan
from apps.finance_crawler.mobile.capture_engine import _is_input_injection_permission_error
from apps.finance_crawler.mobile.action_plan import (
    ACTION_CLICK_DETAIL,
    ACTION_OCR,
    ACTION_OPEN_LINK,
    ACTION_SCREENSHOT,
    ACTION_SCROLL,
    ACTION_UI_CONTROLS,
    FieldCapturePlan,
)
from apps.finance_crawler.mobile.crawler import (
    _runtime_capture_plan,
    _should_run_adapter_before_main_capture,
    is_transient_open_failure,
    wait_for_page_status_ready,
)
from apps.finance_crawler.mobile.page_status import detect_page_status_from_texts
from apps.finance_crawler.mobile.parsers import extract_account_name, extract_profile_fans_count
from apps.finance_crawler.mobile.read_count_crawler import ReadCountTarget, crawl_read_count_target
from apps.finance_crawler.workflows.article_details import (
    extract_tenpay_bottom_counts_from_ocr,
    extract_tenpay_title_from_ocr,
)
from apps.finance_crawler.workflows.docs_link_reads import (
    _not_found_reason as doc_link_not_found_reason,
    extract_read_count_from_records as extract_doc_link_read_count_from_records,
    extract_read_count_from_texts as extract_doc_link_read_count_from_texts,
)
from apps.finance_crawler.workflows.profile_post_reads import extract_read_count_from_texts, infer_post_date
from apps.finance_crawler.workflows.profile_metrics import (
    _daily_template_values,
    _extract_exact_fans_count,
    _fans_tap_bounds,
    _has_abbreviated_fans_count,
    _has_exact_fans_evidence,
    _has_profile_fans_context,
    _is_device_unavailable_error,
    _next_append_row,
    _parse_profile_source_row,
    _profile_home_needs_recapture,
    _resolve_profile_fans_count,
)
from apps.finance_crawler.crawlers import get_app_adapter
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


class RuntimeCapturePlanTests(unittest.TestCase):
    def test_runtime_capture_plan_uses_v2_scroll_and_ocr_limits(self) -> None:
        class Adapter:
            source_app = "fake"

            def capture_plan(self):
                return CapturePlan(
                    max_pages=3,
                    scroll_wait=0.8,
                    enable_ocr=True,
                    ocr_min_confidence=0.5,
                    max_detail_scrolls=2,
                )

        no_scroll_plan = FieldCapturePlan(
            task_type="detail",
            app_type="alipay",
            fields=("read_count", "screenshot"),
            actions=(ACTION_OPEN_LINK, ACTION_UI_CONTROLS, ACTION_SCREENSHOT),
            max_scrolls=2,
            wait_after_scroll=1.2,
        )
        runtime = _runtime_capture_plan(Adapter(), no_scroll_plan)

        self.assertEqual(runtime.max_pages, 1)
        self.assertEqual(runtime.max_detail_scrolls, 0)
        self.assertFalse(runtime.enable_ocr)

        scroll_plan = FieldCapturePlan(
            task_type="detail",
            app_type="alipay",
            fields=("comment_count",),
            actions=(ACTION_OPEN_LINK, ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_OCR, ACTION_SCROLL),
            max_scrolls=2,
            wait_after_scroll=1.2,
        )
        runtime = _runtime_capture_plan(Adapter(), scroll_plan)

        self.assertEqual(runtime.max_pages, 3)
        self.assertEqual(runtime.max_detail_scrolls, 2)
        self.assertTrue(runtime.enable_ocr)
        self.assertEqual(runtime.scroll_wait, 1.2)

    def test_adapter_before_main_hook_requires_click_detail_in_v2_plan(self) -> None:
        plain_plan = FieldCapturePlan(
            task_type="detail",
            app_type="tenpay",
            fields=("read_count",),
            actions=(ACTION_OPEN_LINK, ACTION_UI_CONTROLS, ACTION_SCREENSHOT),
        )
        click_plan = FieldCapturePlan(
            task_type="detail",
            app_type="tenpay",
            fields=("trade_details",),
            actions=(ACTION_OPEN_LINK, ACTION_SCREENSHOT, ACTION_OCR, ACTION_CLICK_DETAIL),
        )

        self.assertTrue(_should_run_adapter_before_main_capture(None))
        self.assertFalse(_should_run_adapter_before_main_capture(plain_plan))
        self.assertTrue(_should_run_adapter_before_main_capture(click_plan))


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

    def test_extract_fans_count_uses_width_height_bounds(self) -> None:
        records = [
            {"text": "17", "bounds": {"left": 68, "top": 800, "width": 48, "height": 44}},
            {"text": "771", "bounds": {"left": 204, "top": 800, "width": 70, "height": 44}},
            {"text": "2.177", "bounds": {"left": 350, "top": 801, "width": 106, "height": 46}},
            {"text": "\u5173\u6ce8", "bounds": {"left": 55, "top": 860, "width": 75, "height": 42}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 191, "top": 859, "width": 77, "height": 43}},
            {"text": "\u5df2\u83b7\u8bc4\u8d5e", "bounds": {"left": 336, "top": 862, "width": 137, "height": 38}},
        ]

        self.assertEqual(extract_profile_fans_count(records), 771)

    def test_missing_tenpay_fans_value_does_not_use_follow_count(self) -> None:
        records = [
            {"text": "\u7406\u8d22\u901a\u793e\u533a\u4f18\u8d28\u521b\u4f5c\u8005", "bounds": {"left": 388, "top": 461, "width": 341, "height": 33}},
            {"text": "17", "bounds": {"left": 68, "top": 800, "width": 48, "height": 44}},
            {"text": "2.177", "bounds": {"left": 350, "top": 801, "width": 106, "height": 46}},
            {"text": "\u5173\u6ce8", "bounds": {"left": 55, "top": 860, "width": 75, "height": 42}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 191, "top": 859, "width": 77, "height": 43}},
            {"text": "\u5df2\u83b7\u8bc4\u8d5e", "bounds": {"left": 336, "top": 862, "width": 137, "height": 38}},
        ]

        self.assertIsNone(extract_profile_fans_count(records))
        result = _resolve_profile_fans_count(
            records,
            screenshot_path=None,
            output_dir=Path("."),
            app_type="tenpay",
            expected_account_name="\u95fb\u57fa\u8d77\u821e",
        )

        self.assertIsNone(result["fans_count"])
        self.assertEqual(result["page_state"], "profile_home")
        self.assertEqual(result["quality_error"], "profile fans count was not detected")

    def test_extract_fans_count_from_inline_antfortune_counter(self) -> None:
        records = [
            {
                "text": "23\u5173\u6ce88743\u7c89\u4e1d3.7w\u83b7\u8d5e",
                "bounds": {"left": 33, "top": 658, "right": 506, "bottom": 765},
            },
        ]

        self.assertEqual(extract_profile_fans_count(records), 8743)

    def test_abbreviated_fans_count_requests_exact_page(self) -> None:
        records = [
            {"text": "1.2\u4e07", "bounds": {"left": 174, "top": 736, "right": 250, "bottom": 793}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 165, "top": 790, "right": 233, "bottom": 832}},
        ]

        self.assertTrue(_has_abbreviated_fans_count(records))
        self.assertEqual(
            _fans_tap_bounds(records),
            {"left": 165, "top": 736, "right": 250, "bottom": 832},
        )

    def test_exact_fans_count_prefers_detail_page_integer(self) -> None:
        records = [
            {"text": "\u7c89\u4e1d\u603b\u6570 12345", "bounds": {"left": 30, "top": 250, "right": 400, "bottom": 310}},
            {"text": "1.2\u4e07\u7c89\u4e1d", "bounds": {"left": 30, "top": 500, "right": 240, "bottom": 560}},
        ]

        self.assertTrue(_has_exact_fans_evidence(records))
        self.assertEqual(_extract_exact_fans_count(records), 12345)

    def test_exact_fans_count_ignores_status_bar_number(self) -> None:
        records = [
            {"text": "22", "bounds": {"left": 1004, "top": 45, "right": 1048, "bottom": 88}},
            {"text": "TA\u7684\u7c89\u4e1d(20665\u4eba)", "bounds": {"left": 62, "top": 313, "right": 543, "bottom": 364}},
        ]

        self.assertEqual(_extract_exact_fans_count(records), 20665)

    def test_status_bar_number_is_not_profile_fans_context(self) -> None:
        records = [
            {"text": "09:32", "bounds": {"left": 394, "top": 38, "right": 501, "bottom": 96}},
            {"text": "21", "bounds": {"left": 1004, "top": 45, "right": 1048, "bottom": 88}},
            {"text": "\u817e\u8baf\u7406\u8d22\u901a", "bounds": {"left": 450, "top": 146, "right": 735, "bottom": 223}},
        ]

        self.assertFalse(_has_profile_fans_context(records))
        self.assertIsNone(_extract_exact_fans_count(records))

    def test_abbreviated_fans_count_is_rejected_without_exact_page(self) -> None:
        records = [
            {"text": "1.2\u4e07", "bounds": {"left": 174, "top": 736, "right": 250, "bottom": 793}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 165, "top": 790, "right": 233, "bottom": 832}},
        ]

        with patch("apps.finance_crawler.workflows.profile_metrics._open_exact_fans_page_if_abbreviated", return_value=None):
            result = _resolve_profile_fans_count(
                records,
                screenshot_path=None,
                output_dir=Path("."),
                app_type="tenpay",
            )

        self.assertIsNone(result["fans_count"])
        self.assertEqual(result["home_fans_count"], 12000)
        self.assertTrue(result["exact_required"])
        self.assertFalse(result["exact_used"])
        self.assertEqual(result["quality_error"], "abbreviated fans count requires exact detail page")

    def test_plain_home_fans_count_is_allowed_as_home_source(self) -> None:
        records = [
            {"text": "14", "bounds": {"left": 174, "top": 736, "right": 222, "bottom": 793}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 165, "top": 790, "right": 233, "bottom": 832}},
        ]

        result = _resolve_profile_fans_count(
            records,
            screenshot_path=None,
            output_dir=Path("."),
            app_type="tenpay",
        )

        self.assertEqual(result["fans_count"], 14)
        self.assertEqual(result["source"], "ui_home")
        self.assertFalse(result["exact_required"])
        self.assertFalse(result["exact_used"])
        self.assertEqual(result["page_state"], "profile_home")
        self.assertEqual(result["action_template"], "tenpay_profile_daily_metrics_v1:fans_count")
        self.assertIn("capture_home", result["actions"])
        self.assertEqual(result["capture_bundle"]["task_type"], "profile_daily_metrics")
        self.assertEqual(result["capture_bundle"]["requested_fields"], ["fans_count"])
        self.assertEqual(result["field_results"][0]["field_name"], "fans_count")
        self.assertTrue(result["field_results"][0]["accepted"])

    def test_home_fans_result_exposes_detected_account_name_for_daily_remark(self) -> None:
        records = [
            {"text": "\u5934\u50cf", "bounds": {"left": 30, "top": 300, "right": 90, "bottom": 360}},
            {"text": "\u65b0\u540d", "bounds": {"left": 100, "top": 300, "right": 220, "bottom": 360}},
            {"text": "14", "bounds": {"left": 174, "top": 736, "right": 222, "bottom": 793}},
            {"text": "\u7c89\u4e1d", "bounds": {"left": 165, "top": 790, "right": 233, "bottom": 832}},
        ]

        result = _resolve_profile_fans_count(
            records,
            screenshot_path=None,
            output_dir=Path("."),
            app_type="tenpay",
            expected_account_name="\u65e7\u540d",
        )

        self.assertEqual(result["fans_count"], 14)
        self.assertTrue(result["nickname_mismatch"])
        self.assertEqual(result["expected_account_name"], "\u65e7\u540d")
        self.assertEqual(result["detected_account_name"], "\u65b0\u540d")
        self.assertEqual(result["field_results"][0]["evidence"]["detected_account_name"], "\u65b0\u540d")

    def test_profile_login_page_is_rejected_with_state(self) -> None:
        records = [
            {"text": "\u6253\u5f00\u652f\u4ed8\u5b9d\u767b\u5f55"},
            {"text": "\u5bc6\u7801\u767b\u5f55"},
        ]

        result = _resolve_profile_fans_count(
            records,
            screenshot_path=None,
            output_dir=Path("."),
            app_type="antfortune",
        )

        self.assertIsNone(result["fans_count"])
        self.assertEqual(result["page_state"], "login_required")
        self.assertEqual(result["quality_error"], "profile page requires login")

    def test_tenpay_profile_counter_layout_recovers_misread_fans_label(self) -> None:
        records = [
            {"text": "\u7406\u8d22\u901a\u793e\u533a\u4f18\u8d28\u521b\u4f5c\u8005", "bounds": {"left": 388, "top": 461, "width": 341, "height": 33}},
            {"text": "103", "bounds": {"left": 55, "top": 800, "width": 76, "height": 45}},
            {"text": "2.1\u4e07", "bounds": {"left": 195, "top": 799, "width": 104, "height": 47}},
            {"text": "8.5\u4e07", "bounds": {"left": 379, "top": 798, "width": 113, "height": 49}},
            {"text": "\u5173\u6ce8", "bounds": {"left": 56, "top": 860, "width": 75, "height": 42}},
            {"text": "\u5df2\u83b7\u8bc4\u8d5e", "bounds": {"left": 368, "top": 862, "width": 138, "height": 38}},
        ]

        self.assertTrue(_has_profile_fans_context(records))
        self.assertTrue(_has_abbreviated_fans_count(records))
        self.assertEqual(
            _fans_tap_bounds(records),
            {"left": 195, "top": 799, "right": 299, "bottom": 936},
        )

    def test_initial_fans_detail_page_must_match_expected_profile(self) -> None:
        records = [
            {"text": "\u817e\u8baf\u7406\u8d22\u901a", "bounds": {"left": 418, "top": 145, "width": 277, "height": 50}},
            {"text": "TA\u7684\u7c89\u4e1d(16590\u4eba)", "bounds": {"left": 59, "top": 295, "width": 452, "height": 49}},
            {"text": "\u7406\u8d22\u901a\u7528\u6237", "bounds": {"left": 185, "top": 649, "width": 227, "height": 44}},
        ]

        result = _resolve_profile_fans_count(
            records,
            screenshot_path=None,
            output_dir=Path("."),
            app_type="tenpay",
            expected_account_name="\u62ce\u58f6\u51b2",
        )

        self.assertIsNone(result["fans_count"])
        self.assertEqual(result["page_state"], "fans_detail")
        self.assertFalse(result["account_verified"])
        self.assertEqual(result["quality_error"], "exact fans page is not tied to expected profile")

    def test_tenpay_title_only_page_needs_recapture(self) -> None:
        records = [
            {"text": "10:20", "bounds": {"left": 394, "top": 38, "right": 501, "bottom": 96}},
            {"text": "\u817e\u8baf\u7406\u8d22\u901a", "bounds": {"left": 450, "top": 146, "right": 735, "bottom": 223}},
        ]

        self.assertTrue(_profile_home_needs_recapture(records, "tenpay"))

    def test_tenpay_ready_profile_page_does_not_need_recapture(self) -> None:
        records = [
            {"text": "\u7406\u8d22\u901a\u793e\u533a\u4f18\u8d28\u521b\u4f5c\u8005", "bounds": {"left": 388, "top": 461, "width": 341, "height": 33}},
            {"text": "103", "bounds": {"left": 55, "top": 800, "width": 76, "height": 45}},
            {"text": "2.1\u4e07", "bounds": {"left": 195, "top": 799, "width": 104, "height": 47}},
            {"text": "8.5\u4e07", "bounds": {"left": 379, "top": 798, "width": 113, "height": 49}},
        ]

        self.assertFalse(_profile_home_needs_recapture(records, "tenpay"))

    def test_device_error_classifier_detects_adb_failures(self) -> None:
        self.assertTrue(_is_device_unavailable_error("no adb device is ready; devices=none"))
        self.assertTrue(_is_device_unavailable_error("uiautomator2 device session is unavailable: disconnected"))
        self.assertFalse(_is_device_unavailable_error("profile fans count was not detected"))

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
    def test_not_found_page_is_terminal_for_doc_link_reads(self) -> None:
        reason = doc_link_not_found_reason([{"text": "\u5185\u5bb9\u4e0d\u5b58\u5728"}])

        self.assertEqual(reason, "\u5185\u5bb9\u4e0d\u5b58\u5728")

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

    def test_retryable_page_cools_down_without_immediate_reopen(self) -> None:
        with (
            patch("apps.finance_crawler.mobile.read_count_crawler.resolve_short_url", return_value="afwealth://post"),
            patch("apps.finance_crawler.mobile.read_count_crawler.open_url") as open_url,
            patch("apps.finance_crawler.mobile.read_count_crawler.session_device", return_value=object()),
            patch("apps.finance_crawler.mobile.read_count_crawler.current_serial", return_value="device-1"),
            patch("apps.finance_crawler.mobile.read_count_crawler.capture_pages", return_value={}),
            patch(
                "apps.finance_crawler.mobile.read_count_crawler.read_capture_records",
                return_value=[{"text": "\u7f51\u7edc\u4e0d\u7ed9\u529b"}],
            ),
            patch("apps.finance_crawler.mobile.read_count_crawler.time.sleep") as sleep,
            patch("apps.finance_crawler.config.Config.DOC_LINK_READS_OPEN_RETRIES", 2),
            patch("apps.finance_crawler.config.Config.DOC_LINK_READS_RETRYABLE_COOLDOWN_SECONDS", 12.0),
        ):
            result = crawl_read_count_target(ReadCountTarget(row_index=2, link="https://example.invalid/post"))

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "retryable_error_page")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["cooldown_seconds"], 12.0)
        open_url.assert_called_once()
        sleep.assert_called_once_with(12.0)

    def test_device_pool_ordinary_failure_uses_short_app_cooldown_only(self) -> None:
        from apps.finance_crawler.storage.device_pool import _cooldown_seconds, _device_cooldown_seconds

        with patch("apps.finance_crawler.config.Config.DEVICE_FAILURE_COOLDOWN_SECONDS", 123):
            self.assertEqual(_cooldown_seconds(error="account name was not detected", error_type="field_not_detected"), 123)
            self.assertEqual(_device_cooldown_seconds(error_type="field_not_detected"), 0)

    def test_device_pool_device_unavailable_cools_down_whole_device(self) -> None:
        from apps.finance_crawler.storage.device_pool import _cooldown_seconds, _device_cooldown_seconds

        with patch("apps.finance_crawler.config.Config.DEVICE_UNAVAILABLE_COOLDOWN_SECONDS", 456):
            self.assertEqual(_cooldown_seconds(error="adb shell unavailable", error_type="device_unavailable"), 456)
            self.assertEqual(_device_cooldown_seconds(error_type="device_unavailable"), 456)

    def test_antfortune_read_count_warmup_launches_and_swipes_before_post(self) -> None:
        plan = FieldCapturePlan(
            task_type="read_count",
            app_type="antfortune",
            fields=("read_count",),
            actions=(ACTION_OPEN_LINK, ACTION_UI_CONTROLS, ACTION_SCREENSHOT),
            open_retries=0,
        )
        with (
            patch("apps.finance_crawler.mobile.read_count_crawler.resolve_short_url", return_value="afwealth://post"),
            patch("apps.finance_crawler.mobile.read_count_crawler.assert_device_ready", return_value="device-1"),
            patch(
                "apps.finance_crawler.mobile.read_count_crawler.run_adb",
                side_effect=[
                    "com.antfortune.wealth/com.alipay.mobile.quinox.LauncherActivity.alias.LauncherNewYear",
                    "",
                    "",
                ],
            ) as run_adb,
            patch("apps.finance_crawler.mobile.read_count_crawler.open_url") as open_url,
            patch("apps.finance_crawler.mobile.read_count_crawler.session_device", return_value=object()),
            patch("apps.finance_crawler.mobile.read_count_crawler.current_serial", return_value="device-1"),
            patch("apps.finance_crawler.mobile.read_count_crawler.capture_pages", return_value={}),
            patch(
                "apps.finance_crawler.mobile.read_count_crawler.read_capture_records",
                return_value=[{"text": "18\u9605\u8bfb"}],
            ),
            patch("apps.finance_crawler.mobile.read_count_crawler.time.sleep"),
            patch("apps.finance_crawler.config.Config.ANTFORTUNE_READ_COUNT_WARMUP_ENABLED", True),
            patch("apps.finance_crawler.config.Config.ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN", True),
            patch("apps.finance_crawler.config.Config.ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT", 1),
        ):
            result = crawl_read_count_target(
                ReadCountTarget(row_index=2, link="https://example.invalid/post", capture_plan=plan)
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["read_count"], 18)
        self.assertEqual(result["warmup"]["status"], "success")
        self.assertEqual(result["warmup"]["swipes"], 1)
        self.assertEqual(run_adb.call_args_list[0].args[0], ["shell", "cmd", "package", "resolve-activity", "--brief", "com.antfortune.wealth"])
        self.assertEqual(
            run_adb.call_args_list[1].args[0],
            [
                "shell",
                "am",
                "start",
                "-n",
                "com.antfortune.wealth/com.alipay.mobile.quinox.LauncherActivity.alias.LauncherNewYear",
            ],
        )
        self.assertEqual(
            run_adb.call_args_list[2].args[0],
            ["shell", "input", "swipe", "540", "1700", "540", "700", "650"],
        )
        open_url.assert_called_once_with("afwealth://post")

    def test_antfortune_retryable_page_restarts_app_and_reopens_post(self) -> None:
        plan = FieldCapturePlan(
            task_type="read_count",
            app_type="antfortune",
            fields=("read_count",),
            actions=(ACTION_OPEN_LINK, ACTION_UI_CONTROLS, ACTION_SCREENSHOT),
            open_retries=0,
        )
        with (
            patch("apps.finance_crawler.mobile.read_count_crawler.resolve_short_url", return_value="afwealth://post"),
            patch("apps.finance_crawler.mobile.read_count_crawler.assert_device_ready", return_value="device-1"),
            patch(
                "apps.finance_crawler.mobile.read_count_crawler.run_adb",
                side_effect=[
                    "",
                    "com.antfortune.wealth/com.alipay.mobile.quinox.LauncherActivity.alias.LauncherNewYear",
                    "",
                    "",
                ],
            ) as run_adb,
            patch("apps.finance_crawler.mobile.read_count_crawler.open_url") as open_url,
            patch("apps.finance_crawler.mobile.read_count_crawler.session_device", return_value=object()),
            patch("apps.finance_crawler.mobile.read_count_crawler.current_serial", return_value="device-1"),
            patch("apps.finance_crawler.mobile.read_count_crawler.capture_pages", side_effect=[{}, {}]),
            patch(
                "apps.finance_crawler.mobile.read_count_crawler.read_capture_records",
                side_effect=[
                    [{"text": "\u7f51\u7edc\u4e0d\u7ed9\u529b"}],
                    [{"text": "21\u9605\u8bfb"}],
                ],
            ),
            patch("apps.finance_crawler.mobile.read_count_crawler.time.sleep"),
            patch("apps.finance_crawler.config.Config.ANTFORTUNE_READ_COUNT_WARMUP_ENABLED", True),
            patch("apps.finance_crawler.config.Config.ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN", False),
            patch("apps.finance_crawler.config.Config.ANTFORTUNE_READ_COUNT_RECOVER_ON_RETRYABLE", True),
            patch("apps.finance_crawler.config.Config.ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT", 1),
        ):
            result = crawl_read_count_target(
                ReadCountTarget(row_index=3, link="https://example.invalid/post", capture_plan=plan)
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["read_count"], 21)
        self.assertTrue(result["used_app_recovery"])
        self.assertEqual(result["warmup"]["status"], "success")
        self.assertEqual(open_url.call_args_list[0].args[0], "afwealth://post")
        self.assertEqual(open_url.call_args_list[1].args[0], "afwealth://post")
        self.assertEqual(
            run_adb.call_args_list[0].args[0],
            ["shell", "am", "force-stop", "com.antfortune.wealth"],
        )
        self.assertEqual(
            run_adb.call_args_list[1].args[0],
            ["shell", "cmd", "package", "resolve-activity", "--brief", "com.antfortune.wealth"],
        )
        self.assertEqual(
            run_adb.call_args_list[3].args[0],
            ["shell", "input", "swipe", "540", "1700", "540", "700", "650"],
        )


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

    def test_tenpay_adapter_refines_bottom_comment_and_like_counts(self) -> None:
        output_dir = Path(self.id().replace(".", "_"))
        try:
            output_dir.mkdir(exist_ok=True)
            ocr_jsonl = output_dir / "ocr_records.jsonl"
            ocr_jsonl.write_text(
                "\n".join(
                    [
                        '{"text":"28","bounds":{"left":679,"top":2264},"page_index":0}',
                        '{"text":"133","bounds":{"left":814,"top":2265},"page_index":0}',
                        '{"text":"28","bounds":{"left":679,"top":2264},"page_index":1}',
                        '{"text":"133","bounds":{"left":814,"top":2264},"page_index":1}',
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                get_app_adapter("tenpay").refine_capture_result(
                    result={"requested_fields": ["comment_count", "like_count"]},
                    summary={"ocr_jsonl": str(ocr_jsonl)},
                ),
                {"like_count": 133, "like_found": True, "comment_count": 28, "comment_found": True},
            )

            self.assertEqual(
                get_app_adapter("tenpay").refine_capture_result(
                    result={"requested_fields": ["like_count"], "comment_count": 28, "comment_found": True},
                    summary={"ocr_jsonl": str(ocr_jsonl)},
                ),
                {"like_count": 133, "like_found": True},
            )

            self.assertEqual(
                get_app_adapter("tenpay").refine_capture_result(
                    result={"requested_fields": ["read_count"]},
                    summary={"ocr_jsonl": str(ocr_jsonl)},
                ),
                {},
            )
        finally:
            if output_dir.exists():
                for path in output_dir.iterdir():
                    path.unlink()
                output_dir.rmdir()

    def test_tenpay_account_name_allows_business_word_in_author_name(self) -> None:
        account = get_app_adapter("tenpay").extract_account_name(
            [
                "腾讯理财通",
                "会理财会生活",
                "(百万实盘)",
                "关注",
                "老登登场、小登退场，市场在",
            ]
        )

        self.assertEqual(account, "会理财会生活")

    def test_tenpay_account_name_skips_portfolio_label_before_author(self) -> None:
        account = get_app_adapter("tenpay").extract_account_name(
            [
                "腾讯理财通",
                "(百万实盘)",
                "D老师写字的地方",
                "关注",
                "260626:主线缩圈",
            ]
        )

        self.assertEqual(account, "D老师写字的地方")

    def test_tenpay_account_name_uses_ocr_when_ui_has_no_author(self) -> None:
        output_dir = Path(self.id().replace(".", "_"))
        try:
            output_dir.mkdir(exist_ok=True)
            ui_jsonl = output_dir / "ui_records.jsonl"
            ocr_jsonl = output_dir / "ocr_records.jsonl"
            ui_jsonl.write_text(
                '{"text":"腾讯理财通","content_desc":"","package":"com.tencent.fortuneplat"}\n',
                encoding="utf-8",
            )
            ocr_jsonl.write_text(
                "\n".join(
                    [
                        '{"text":"腾讯理财通","bounds":{"left":419,"top":145},"page_index":0}',
                        '{"text":"会理财会生活","bounds":{"left":188,"top":300},"page_index":0}',
                        '{"text":"(百万实盘)","bounds":{"left":449,"top":300},"page_index":0}',
                    ]
                ),
                encoding="utf-8",
            )

            updates = get_app_adapter("tenpay").refine_capture_result(
                result={"requested_fields": ["account_name"], "account_name": "(百万实盘)"},
                summary={"ui_jsonl": str(ui_jsonl), "ocr_jsonl": str(ocr_jsonl), "output_dir": str(output_dir)},
            )

            self.assertEqual(updates["account_name"], "会理财会生活")
            self.assertEqual(updates["account_name_source"], "ocr")
            self.assertEqual(updates["account_name_resolution"], "ocr_only")
        finally:
            if output_dir.exists():
                for path in output_dir.iterdir():
                    path.unlink()
                output_dir.rmdir()

    def test_tenpay_account_name_prefers_ui_when_conflict_model_unconfigured(self) -> None:
        output_dir = Path(self.id().replace(".", "_"))
        try:
            output_dir.mkdir(exist_ok=True)
            ui_jsonl = output_dir / "ui_records.jsonl"
            ocr_jsonl = output_dir / "ocr_records.jsonl"
            ui_jsonl.write_text(
                "\n".join(
                    [
                        '{"text":"腾讯理财通","content_desc":"","package":"com.tencent.fortuneplat"}',
                        '{"text":"UI作者","content_desc":"","package":"com.tencent.fortuneplat"}',
                    ]
                ),
                encoding="utf-8",
            )
            ocr_jsonl.write_text(
                "\n".join(
                    [
                        '{"text":"腾讯理财通","bounds":{"left":419,"top":145},"page_index":0}',
                        '{"text":"OCR作者","bounds":{"left":188,"top":300},"page_index":0}',
                    ]
                ),
                encoding="utf-8",
            )

            with patch("apps.finance_crawler.crawlers.tenpay.Config.OPENAI_API_KEY", ""):
                updates = get_app_adapter("tenpay").refine_capture_result(
                    result={"requested_fields": ["account_name"], "account_name": "old"},
                    summary={"ui_jsonl": str(ui_jsonl), "ocr_jsonl": str(ocr_jsonl), "output_dir": str(output_dir)},
                )

            self.assertEqual(updates["account_name"], "UI作者")
            self.assertEqual(updates["account_name_source"], "ui_controls")
            self.assertEqual(updates["account_name_resolution"], "ui_ocr_conflict_model_unavailable")
        finally:
            if output_dir.exists():
                for path in output_dir.iterdir():
                    path.unlink()
                output_dir.rmdir()

    def test_tenpay_account_name_uses_model_when_ui_and_ocr_conflict(self) -> None:
        output_dir = Path(self.id().replace(".", "_"))
        try:
            output_dir.mkdir(exist_ok=True)
            ui_jsonl = output_dir / "ui_records.jsonl"
            ocr_jsonl = output_dir / "ocr_records.jsonl"
            ui_jsonl.write_text(
                "\n".join(
                    [
                        '{"text":"腾讯理财通","content_desc":"","package":"com.tencent.fortuneplat"}',
                        '{"text":"UI作者","content_desc":"","package":"com.tencent.fortuneplat"}',
                    ]
                ),
                encoding="utf-8",
            )
            ocr_jsonl.write_text(
                "\n".join(
                    [
                        '{"text":"腾讯理财通","bounds":{"left":419,"top":145},"page_index":0}',
                        '{"text":"OCR作者","bounds":{"left":188,"top":300},"page_index":0}',
                    ]
                ),
                encoding="utf-8",
            )

            with (
                patch("apps.finance_crawler.crawlers.tenpay.Config.OPENAI_API_KEY", "key"),
                patch("apps.finance_crawler.crawlers.tenpay.Config.OPENAI_BASE_URL", "https://example.test/v1"),
                patch("apps.finance_crawler.crawlers.tenpay.Config.OPENAI_MODEL", "model"),
                patch(
                    "apps.finance_crawler.crawlers.tenpay._post_openai_chat_completion",
                    return_value='{"account_name":"模型作者","confidence":0.9,"reason":"visible author row"}',
                ) as post_model,
            ):
                updates = get_app_adapter("tenpay").refine_capture_result(
                    result={"requested_fields": ["account_name"], "account_name": "old"},
                    summary={"ui_jsonl": str(ui_jsonl), "ocr_jsonl": str(ocr_jsonl), "output_dir": str(output_dir)},
                )

            self.assertEqual(updates["account_name"], "模型作者")
            self.assertEqual(updates["account_name_source"], "model")
            self.assertEqual(updates["account_name_resolution"], "ui_ocr_conflict_model")
            post_model.assert_called_once()
        finally:
            if output_dir.exists():
                for path in output_dir.iterdir():
                    path.unlink()
                output_dir.rmdir()


class RecoveryClassifierTests(unittest.TestCase):
    def test_page_status_ready_wait_recaptures_unknown_state(self) -> None:
        with (
            patch(
                "apps.finance_crawler.mobile.crawler.detect_page_status",
                side_effect=[
                    ("error", "page status is unknown or too few controls were found"),
                    ("success", None),
                ],
            ) as detect,
            patch("apps.finance_crawler.mobile.crawler.time.sleep") as sleep,
        ):
            status, error, metrics = wait_for_page_status_ready(timeout=5.0, interval=0.5)

        self.assertEqual(status, "success")
        self.assertIsNone(error)
        self.assertEqual(detect.call_count, 2)
        sleep.assert_called_once_with(0.5)
        self.assertEqual(metrics["page_status_wait_attempts"], 2)
        self.assertFalse(metrics["page_status_wait_timed_out"])

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
