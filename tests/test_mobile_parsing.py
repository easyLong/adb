import unittest

from apps.finance_crawler.mobile.page_status import detect_page_status_from_texts
from apps.finance_crawler.mobile.crawler import is_transient_open_failure
from apps.finance_crawler.mobile.parsers import extract_account_name


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


if __name__ == "__main__":
    unittest.main()
