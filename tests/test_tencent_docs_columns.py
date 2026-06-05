import unittest
from unittest import mock

from apps.finance_crawler.integrations.tencent_docs.client import DocInfo
from apps.finance_crawler.integrations.tencent_docs import columns


class TencentDocsColumnTests(unittest.TestCase):
    def test_resolve_main_columns_from_reordered_titles(self) -> None:
        rows = [
            [
                "unused",
                "\u539f\u6587\u94fe\u63a5",
                "\u8d26\u53f7",
                "\u53d1\u5e03\u65f6\u95f4",
                "\u622a\u56fe",
                "\u9605\u8bfb\u6570\u56de\u586b",
                "\u8bc4\u8bba\u6570",
                "\u5907\u6ce8",
            ],
            ["", "https://example.com", "", "10:00", "", "", "", ""],
        ]

        resolved = columns.resolve_columns(
            rows,
            0,
            columns.MAIN_COLUMN_ALIASES,
            {
                "post_time": 9,
                "url": 13,
                "account_name": 11,
                "read_count": 14,
                "comment_count": 15,
                "check_status": 16,
                "detail_status": 16,
                "screenshot": 12,
            },
        )

        self.assertEqual(resolved["url"], 1)
        self.assertEqual(resolved["account_name"], 2)
        self.assertEqual(resolved["post_time"], 3)
        self.assertEqual(resolved["read_count"], 5)
        self.assertEqual(resolved["comment_count"], 6)
        self.assertEqual(resolved["detail_status"], 7)

    def test_resolve_doc_link_reads_titles(self) -> None:
        rows = [
            ["\u6807\u9898", "\u8d26\u53f7\u540d\u79f0", "\u94fe\u63a5", "\u9605\u8bfb\u91cf"],
            ["a", "b", "https://example.com", ""],
        ]

        resolved = columns.resolve_columns(
            rows,
            0,
            columns.DOC_LINK_READS_ALIASES,
            {"link": 10, "read_count": 12, "title": 0, "account_name": 9},
        )

        self.assertEqual(resolved, {"link": 2, "read_count": 3, "title": 0, "account_name": 1})

    def test_fallback_when_header_is_missing(self) -> None:
        resolved = columns.resolve_column_info(
            [["a", "b"]],
            0,
            ("\u9605\u8bfb\u6570",),
            12,
            field_name="read_count",
        )

        self.assertEqual(resolved.index, 12)
        self.assertEqual(resolved.source, "fallback")
        self.assertEqual(resolved.match_type, "none")

    def test_ambiguous_title_raises_without_matching_fallback(self) -> None:
        with self.assertRaises(RuntimeError):
            columns.resolve_column(
                [["\u94fe\u63a5", "\u539f\u6587\u94fe\u63a5"]],
                0,
                columns.MAIN_COLUMN_ALIASES["url"],
                13,
                field_name="url",
            )

    def test_strict_fallback_rejects_unrecognized_header_title(self) -> None:
        with self.assertRaises(RuntimeError):
            columns.resolve_column(
                [["\u5e16\u5b50\u94fe\u63a5"]],
                0,
                columns.MAIN_COLUMN_ALIASES["read_count"],
                0,
                field_name="read_count",
                strict_fallback_title=True,
            )

    def test_inspect_header_columns_exposes_resolution_metadata(self) -> None:
        with mock.patch.object(
            columns.client,
            "fetch_grid",
            return_value=([["\u53d1\u5e03\u65f6\u95f4", "\u539f\u6587\u94fe\u63a5"]], 0),
        ):
            resolutions = columns.inspect_header_columns(
                DocInfo("file", "sheet"),
                aliases_by_field={
                    "post_time": columns.MAIN_COLUMN_ALIASES["post_time"],
                    "url": columns.MAIN_COLUMN_ALIASES["url"],
                    "read_count": columns.MAIN_COLUMN_ALIASES["read_count"],
                },
                fallbacks={"post_time": 9, "url": 13, "read_count": 14},
            )

        by_field = {item.field: item for item in resolutions}
        self.assertEqual(by_field["post_time"].index, 0)
        self.assertEqual(by_field["post_time"].source, "title")
        self.assertEqual(by_field["url"].index, 1)
        self.assertEqual(by_field["read_count"].index, 14)
        self.assertEqual(by_field["read_count"].source, "fallback")

    def test_fetch_header_rows_uses_short_lived_cache(self) -> None:
        columns.clear_header_cache()
        doc = DocInfo("file", "sheet")
        with mock.patch.object(
            columns.client,
            "fetch_grid",
            return_value=([["\u94fe\u63a5"]], 0),
        ) as fetch_grid:
            self.assertEqual(columns.fetch_header_rows(doc), ([["\u94fe\u63a5"]], 0))
            self.assertEqual(columns.fetch_header_rows(doc), ([["\u94fe\u63a5"]], 0))

        self.assertEqual(fetch_grid.call_count, 1)
        columns.clear_header_cache()


if __name__ == "__main__":
    unittest.main()
