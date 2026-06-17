import unittest
from datetime import date, datetime
from unittest.mock import ANY, patch

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.capture.planner import (
    plan_capture_for_task,
    plan_capture_from_profile,
    plan_minimal_capture_actions,
)
from apps.finance_crawler.crawler_app.capture.post_fields import (
    build_post_capture_bundle,
    extract_post_field_results,
    writeback_values_from_field_results,
)
from apps.finance_crawler.crawler_app.documents import column_resolver
from apps.finance_crawler.crawler_app.documents.fields import (
    ACCOUNT_NAME,
    CHECK_RESULT,
    COMMENT_COUNT,
    READ_COUNT as READ_COUNT_FIELD,
    REMARK,
    SCREENSHOT,
)
from apps.finance_crawler.crawler_app.documents.intake import (
    submit_document_tasks_from_source,
    submit_read_count_tasks_from_source,
)
from apps.finance_crawler.crawler_app.documents.rows import extract_source_rows
from apps.finance_crawler.crawler_app.documents.sheet_selector import select_sheet, select_sheets
from apps.finance_crawler.crawler_app.documents.sheets import parse_business_date_from_sheet_title, select_sheet_for_date
from apps.finance_crawler.crawler_app.documents.sources import DocumentSheetSnapshot, TencentDocsSource
from apps.finance_crawler.crawler_app.storage import repository
from apps.finance_crawler.crawler_app.storage.schema import ensure_crawler_app_tables
from apps.finance_crawler.crawler_app.workflows.corrections import (
    apply_pending_correction_writebacks,
    plan_configured_document_correction_in_conn,
    plan_document_correction_in_conn,
)
from apps.finance_crawler.crawler_app.strategies.post import (
    crawl_detail_task,
    crawl_initial_check_task,
    detail_writeback_values,
    initial_check_writeback_values,
)
from apps.finance_crawler.crawler_app.strategies.read_count import read_count_writeback_values
from apps.finance_crawler.crawler_app.tasks.handlers import get_task_handler
from apps.finance_crawler.crawler_app.tasks.submission import build_task_submission, make_dedupe_key
from apps.finance_crawler.crawler_app.tasks.types import DETAIL, INITIAL_CHECK, READ_COUNT
from apps.finance_crawler.crawler_app.workflows.document_tasks import (
    build_sheet_selector,
    default_fields_for_task,
    parse_field_names,
    validate_document_task_config_payload,
)
from apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots import (
    locate_kol_daily_rows_by_date_url,
    parse_kol_crawl_source_row,
    parse_kol_daily_row,
    resolve_kol_daily_header,
    run_kol_daily_crawl_pipeline,
    writeback_kol_daily_crawl_results_to_tencent_docs,
    writeback_kol_daily_snapshots_to_tencent_docs,
)
from apps.finance_crawler.crawler_app.workflows.execution import (
    _attach_capture_action_profile,
    _record_field_capture_observations,
    _requested_writeback_values,
    _sleep_between_submissions,
    execution_summary,
)
from apps.finance_crawler.crawler_app.errors import (
    DEVICE_UNAVAILABLE,
    FIELD_NOT_DETECTED,
    PAGE_NOT_FOUND,
    classify_crawl_error,
)
from apps.finance_crawler.crawler_app.workflows.submit_triggers import (
    TriggerBinding,
    _canonical_rows_by_url,
    _effective_target_date,
    _skip_summary_for_trigger,
    _submit_bindings_from_tencent_doc,
    _validate_trigger_binding_policy,
)
from apps.finance_crawler.crawler_app.writeback.executor import apply_pending_writebacks
from apps.finance_crawler.crawler_app.writeback.locator import (
    SheetWritebackContext,
    locate_by_date_url,
    locate_by_post_url,
)
from apps.finance_crawler.crawler_app.capture.observations import build_profile_metric_observations
from apps.finance_crawler.crawlers.constants import SOURCE_ALIPAY, SOURCE_TENPAY
from apps.finance_crawler.integrations.tencent_docs import client as tencent_docs_client
from apps.finance_crawler.integrations.tencent_docs.client import DocInfo, SheetInfo
from apps.finance_crawler.mobile.action_plan import (
    ACTION_CLICK_DETAIL,
    ACTION_OCR,
    ACTION_OPEN_LINK,
    ACTION_SCREENSHOT,
    ACTION_SCROLL,
    ACTION_TAP_RETRY,
    ACTION_UI_CONTROLS,
)
from apps.finance_crawler.utils.device_health import AdbDevice, classify_adb_transport
from apps.finance_crawler.workflows.profile_metrics import writeback_profile_metrics


class CrawlerAppDocumentTests(unittest.TestCase):
    def test_schema_creates_capture_action_profiles(self) -> None:
        class FakeCursor:
            def __init__(self) -> None:
                self.executed = []
                self.executemany_calls = []

            def execute(self, sql, params=None):
                self.executed.append(str(sql))

            def executemany(self, sql, rows):
                self.executemany_calls.append((str(sql), list(rows)))

            def fetchone(self):
                return None

        cursor = FakeCursor()

        ensure_crawler_app_tables(cursor)

        ddl = "\n".join(cursor.executed)
        self.assertIn("CREATE TABLE IF NOT EXISTS document_task_configs", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS document_trigger_configs", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS document_trigger_bindings", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS submit_runs", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS kol_daily_snapshots", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS profile_action_profiles", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS profile_trigger_configs", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS profile_trigger_runs", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS profile_targets", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS profile_metric_sources", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS profile_metric_runs", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS profile_metric_writebacks", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS capture_action_profiles", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS field_capture_observations", ddl)
        self.assertIn("app_type VARCHAR(64) NOT NULL", ddl)
        self.assertIn("task_type VARCHAR(64) NOT NULL", ddl)
        self.assertIn("field_combo VARCHAR(512) NOT NULL", ddl)
        self.assertIn("action_combo VARCHAR(512) NOT NULL", ddl)
        self.assertTrue(
            any("INSERT INTO capture_action_profiles" in sql for sql, _rows in cursor.executemany_calls)
        )
        profile_rows = []
        for sql, rows in cursor.executemany_calls:
            if "INSERT INTO capture_action_profiles" in sql:
                profile_rows.extend(rows)
        profile_keys = {(row[0], row[1], row[2]) for row in profile_rows}
        self.assertIn(("alipay", "initial_check", "account_name"), profile_keys)
        self.assertIn(("antfortune", "initial_check", "account_name"), profile_keys)
        self.assertIn(("alipay", "detail", "account_name,read_count,screenshot"), profile_keys)
        self.assertIn(("antfortune", "detail", "account_name,read_count,screenshot"), profile_keys)
        self.assertNotIn(("alipay,antfortune", "initial_check", "account_name"), profile_keys)
        profile_action_rows = []
        for sql, rows in cursor.executemany_calls:
            if "INSERT INTO profile_action_profiles" in sql:
                profile_action_rows.extend(rows)
        profile_action_keys = {row[0] for row in profile_action_rows}
        self.assertIn("alipay_profile_daily_metrics_v1", profile_action_keys)
        self.assertIn("antfortune_profile_daily_metrics_v1", profile_action_keys)

    def test_profile_metric_repository_uses_crawler_app_db(self) -> None:
        from apps.finance_crawler.crawler_app.storage import db as crawler_app_db
        from apps.finance_crawler.crawler_app.storage import profile_metrics as crawler_profile_metrics
        from apps.finance_crawler.storage import profile_metrics as legacy_profile_metrics

        self.assertIs(crawler_profile_metrics.get_conn, crawler_app_db.get_conn)
        self.assertIs(legacy_profile_metrics.get_conn, crawler_app_db.get_conn)

    def test_ops_platform_intake_upsert_preserves_reviewed_status(self) -> None:
        from apps.finance_crawler.crawler_app.storage.ops_platform import (
            OpsDemandCandidate,
            OpsDemandEvidence,
            OpsSourceContactContext,
            OpsWechatGroupConfig,
            candidate_with_source_context,
            candidate_with_wechat_group_config,
            upsert_ops_demand_candidate,
        )

        class FakeCursor:
            def __init__(self) -> None:
                self.executed = []

            def execute(self, sql, params=None):
                self.executed.append((str(sql), params))

            def fetchone(self):
                return {"id": "ops-candidate-id"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeConn:
            def __init__(self) -> None:
                self.cursor_obj = FakeCursor()

            def cursor(self):
                return self.cursor_obj

        conn = FakeConn()

        candidate = candidate_with_source_context(
            OpsDemandCandidate(
                external_candidate_id="crawler:candidate:1",
                external_capture_run_id="run:1",
                demand_title="活动 banner 设计",
                demand_content="需要一张活动 banner。",
                evidences=[
                    OpsDemandEvidence(
                        external_evidence_id="message:1",
                        message_text="帮忙做一版活动 banner",
                    )
                ],
            ),
            OpsSourceContactContext(
                id="source-context-id",
                source_app="crawler",
                source_type="wechat_group",
                source_key="source-key",
                source_name="创金设计需求响应群",
                contact_context_config_id="contact-context-id",
                customer_id="customer-id",
                contact_name="Diana",
                business_platform="微信",
            ),
        )

        candidate_id = upsert_ops_demand_candidate(conn, candidate)

        self.assertEqual(candidate_id, "ops-candidate-id")
        self.assertEqual(candidate.matched_customer_id, "customer-id")
        self.assertEqual(candidate.matched_contact_context_id, "contact-context-id")
        sql = "\n".join(item[0] for item in conn.cursor_obj.executed)
        self.assertIn("external_capture_run_id", sql)
        self.assertIn("external_source_key", sql)
        self.assertIn("matched_customer_id", sql)
        self.assertIn("demand_content", sql)
        self.assertIn("status = IF(status IN ('confirmed', 'rejected'), status, VALUES(status))", sql)
        self.assertIn(
            "matched_customer_id = IF(status IN ('confirmed', 'rejected'), matched_customer_id, VALUES(matched_customer_id))",
            sql,
        )
        self.assertIn("deleted_at = IF(status IN ('confirmed', 'rejected'), deleted_at, NULL)", sql)
        self.assertIn("external_evidence_id", sql)

        group_candidate = candidate_with_wechat_group_config(
            OpsDemandCandidate(external_candidate_id="crawler:candidate:2"),
            OpsWechatGroupConfig(
                id="group-config-id",
                group_id="wechat-group-id",
                group_name="创金设计需求响应群",
                source_key="wechat-group-source-key",
                customer_id="customer-id",
                customer_name="创金",
                contact_context_config_id="contact-context-id",
                contact_name="Diana",
                business_platform="招行",
            ),
        )

        self.assertEqual(group_candidate.external_source_key, "wechat-group-source-key")
        self.assertEqual(group_candidate.external_chat_id, "wechat-group-id")
        self.assertEqual(group_candidate.source_chat_name, "创金设计需求响应群")
        self.assertEqual(group_candidate.raw_customer_name, "创金")
        self.assertEqual(group_candidate.raw_owner_name, "Diana")
        self.assertEqual(group_candidate.matched_customer_id, "customer-id")

    def test_profile_metric_observation_rows_extract_field_evidence(self) -> None:
        rows = build_profile_metric_observations(
            metric_id=12,
            target_id=34,
            task_type="profile_daily_metrics",
            app_type="tenpay",
            status="success",
            fans_count=20664,
            read_count=None,
            metrics={
                "fans": {
                    "fans_count": 20664,
                    "page_state": "fans_detail",
                    "page_state_confidence": 0.95,
                    "source": "exact_page",
                    "action_template": "tenpay_profile_daily_metrics_v1:fans_count",
                    "actions": ["open_profile", "open_exact_fans_if_abbreviated"],
                    "exact_required": True,
                    "exact_used": True,
                    "account_verified": True,
                    "quality_error": None,
                }
            },
            screenshot_path="runs/profile/page_000.png",
            error=None,
            observed_at=datetime(2026, 6, 7, 8, 0, 0),
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.subject_type, "profile_metric_run")
        self.assertEqual(row.field_name, "fans_count")
        self.assertEqual(row.value_number, 20664)
        self.assertTrue(row.accepted)
        self.assertEqual(row.page_state, "fans_detail")
        self.assertEqual(row.extraction_source, "exact_page")
        self.assertEqual(row.action_template_key, "tenpay_profile_daily_metrics_v1:fans_count")

    def test_profile_metric_observation_acceptance_is_field_scoped(self) -> None:
        rows = build_profile_metric_observations(
            metric_id=12,
            target_id=34,
            task_type="profile_daily_metrics",
            app_type="tenpay",
            status="error",
            fans_count=None,
            read_count=None,
            metrics={
                "fans": {
                    "fans_count": 20664,
                    "page_state": "fans_detail",
                    "source": "exact_page",
                    "action_template": "tenpay_profile_daily_metrics_v1:fans_count",
                    "quality_error": None,
                },
                "posts": [],
                "post_count": 0,
            },
            screenshot_path="runs/profile/page_000.png",
            error="profile post read count was not detected",
            observed_at=datetime(2026, 6, 7, 8, 0, 0),
        )

        rows_by_field = {row.field_name: row for row in rows}
        self.assertTrue(rows_by_field["fans_count"].accepted)
        self.assertFalse(rows_by_field["read_count"].accepted)

    def test_profile_metric_observations_prefer_standard_field_results(self) -> None:
        rows = build_profile_metric_observations(
            metric_id=12,
            target_id=34,
            task_type="profile_daily_metrics",
            app_type="tenpay",
            status="success",
            fans_count=20664,
            read_count=None,
            metrics={
                "capture_bundle": {
                    "task_type": "profile_daily_metrics",
                    "app_type": "tenpay",
                    "requested_fields": ["fans_count"],
                    "action_template_key": "tenpay_profile_daily_metrics_v1:fans_count",
                    "actions": ["open_profile", "capture_home"],
                    "status": "success",
                    "page_state": "fans_detail",
                    "screenshot_path": "shot.png",
                },
                "field_results": [
                    {
                        "field_name": "fans_count",
                        "value": 20664,
                        "source": "exact_page",
                        "accepted": True,
                        "page_state": "fans_detail",
                        "confidence": 0.95,
                        "evidence": {"source": "standard"},
                    }
                ],
                "fans": {
                    "fans_count": 999,
                    "source": "legacy_should_not_win",
                },
            },
            screenshot_path="legacy-shot.png",
            error=None,
            observed_at=datetime(2026, 6, 7, 8, 0, 0),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].field_name, "fans_count")
        self.assertEqual(rows[0].value_number, 20664)
        self.assertEqual(rows[0].action_template_key, "tenpay_profile_daily_metrics_v1:fans_count")
        self.assertEqual(rows[0].screenshot_path, "shot.png")

    def test_post_field_results_share_one_capture_bundle(self) -> None:
        bundle = build_post_capture_bundle(
            task_type=DETAIL,
            app_type=SOURCE_ALIPAY,
            requested_fields=(ACCOUNT_NAME, READ_COUNT_FIELD, SCREENSHOT, REMARK),
            result={
                "status": "success",
                "opened_url": "https://example.com/post",
                "account_name": "acct",
                "read_count": 123,
                "screenshot_path": "shot.png",
                "capture_plan": {
                    "actions": ["open_link", "ui_controls", "screenshot"],
                },
            },
        )

        results = {item.field_name: item for item in extract_post_field_results(bundle)}

        self.assertEqual(bundle.page_state, "post_detail")
        self.assertEqual(results[ACCOUNT_NAME].value, "acct")
        self.assertEqual(results[READ_COUNT_FIELD].value, 123)
        self.assertEqual(results[SCREENSHOT].value, "shot.png")
        self.assertTrue(all(item.accepted for item in results.values()))

    def test_writeback_values_can_be_generated_from_field_results(self) -> None:
        result = {
            "field_results": [
                {"field_name": ACCOUNT_NAME, "value": "acct", "accepted": True},
                {"field_name": READ_COUNT_FIELD, "value": 123, "accepted": True},
                {"field_name": SCREENSHOT, "value": "shot.png", "accepted": True},
                {"field_name": COMMENT_COUNT, "value": None, "accepted": False},
            ]
        }

        self.assertEqual(
            writeback_values_from_field_results(result),
            {
                ACCOUNT_NAME: "acct",
                READ_COUNT_FIELD: 123,
                SCREENSHOT: "shot.png",
            },
        )
        self.assertEqual(detail_writeback_values(result), writeback_values_from_field_results(result))

    def test_execution_records_field_capture_observations(self) -> None:
        captured = []

        def fake_upsert(_conn, observations):
            captured.extend(observations)
            return len(observations)

        result = {
            "capture_bundle": {
                "task_type": DETAIL,
                "app_type": SOURCE_ALIPAY,
                "requested_fields": [READ_COUNT_FIELD, SCREENSHOT],
                "actions": ["open_link", "ui_controls", "screenshot"],
                "opened_url": "https://example.com/post",
                "status": "success",
                "page_state": "post_detail",
                "screenshot_path": "shot.png",
            },
            "field_results": [
                {
                    "field_name": READ_COUNT_FIELD,
                    "value": 123,
                    "source": "ui_controls",
                    "accepted": True,
                    "page_state": "post_detail",
                    "confidence": 0.8,
                    "evidence": {"status": "success"},
                },
                {
                    "field_name": SCREENSHOT,
                    "value": "shot.png",
                    "source": "screenshot",
                    "accepted": True,
                    "page_state": "post_detail",
                    "confidence": 0.8,
                    "evidence": {"status": "success"},
                },
            ],
        }

        with patch("apps.finance_crawler.crawler_app.workflows.execution.repository.upsert_field_capture_observations", fake_upsert):
            count = _record_field_capture_observations(
                object(),
                submission={"id": 7, "source_row_id": 5},
                execution_id=11,
                result=result,
            )

        self.assertEqual(count, 2)
        self.assertEqual(captured[0].subject_type, "task_execution")
        self.assertEqual(captured[0].subject_id, 11)
        self.assertEqual(captured[0].target_type, "source_row")
        self.assertEqual(captured[0].target_id, 5)
        self.assertEqual(captured[0].field_name, READ_COUNT_FIELD)

    def test_kol_daily_header_resolves_by_title(self) -> None:
        header = [
            "\u9605\u8bfb\u6570",
            "\u7b2c\u51e0\u7fa4",
            "\u5e73\u53f0",
            "\u65e5\u671f",
            "\u589e\u7c89\u6570",
            "\u4e3b\u9875\u94fe\u63a5",
            "\u7c89\u4e1d\u6570",
            "\u5927V\u540d\u79f0",
        ]

        mapping = resolve_kol_daily_header(header)

        self.assertEqual(mapping["problems"], [])
        self.assertEqual(mapping["columns"]["read_count"], 0)
        self.assertEqual(mapping["columns"]["group_name"], 1)
        self.assertEqual(mapping["columns"]["platform"], 2)
        self.assertEqual(mapping["columns"]["snapshot_date"], 3)
        self.assertEqual(mapping["columns"]["growth_count"], 4)
        self.assertEqual(mapping["columns"]["homepage_url"], 5)
        self.assertEqual(mapping["columns"]["fans_count"], 6)
        self.assertEqual(mapping["columns"]["kol_name"], 7)

    def test_kol_daily_row_parses_metrics(self) -> None:
        header = [
            "\u65e5\u671f",
            "\u5927V\u540d\u79f0",
            "\u5e73\u53f0",
            "\u4e3b\u9875\u94fe\u63a5",
            "\u7c89\u4e1d\u6570",
            "\u589e\u7c89\u6570",
            "\u9605\u8bfb\u6570",
            "\u7b2c\u51e0\u7fa4",
        ]
        mapping = resolve_kol_daily_header(header)

        row = parse_kol_daily_row(
            ["2026-06-05", "acct", "alipay", "https://example.com", "1.2\u4e07", "-3", "4,567", "1"],
            mapping["columns"],
        )

        self.assertIsNotNone(row)
        self.assertEqual(row.snapshot_date, date(2026, 6, 5))
        self.assertEqual(row.kol_name, "acct")
        self.assertEqual(row.fans_count, 12000)
        self.assertEqual(row.growth_count, -3)
        self.assertEqual(row.read_count, 4567)

    def test_kol_daily_writeback_appends_missing_date_with_font_10(self) -> None:
        class DummyConn:
            def close(self) -> None:
                pass

        target_date = date(2026, 6, 7)
        snapshot = {
            "snapshot_date": target_date,
            "kol_name": "acct",
            "platform": "alipay",
            "homepage_url": "https://example.com",
            "group_name": "1",
            "kol_type": "\u5176\u5b83",
            "fans_count": None,
            "growth_count": None,
            "read_count": None,
        }
        existing_rows = [
            ["\u65e5\u671f", "\u5927V\u540d\u79f0", "\u5e73\u53f0", "\u4e3b\u9875\u94fe\u63a5", "\u7b2c\u51e0\u7fa4", "\u7c7b\u578b", "\u7c89\u4e1d\u6570", "\u589e\u7c89\u6570", "\u9605\u8bfb\u6570"],
            ["2026-06-06", "old", "alipay", "", "", "", "", "", ""],
        ]
        captured_requests = []
        old_font_size = Config.KOL_DAILY_SNAPSHOT_WRITEBACK_FONT_SIZE
        Config.KOL_DAILY_SNAPSHOT_WRITEBACK_FONT_SIZE = 10
        try:
            with (
                patch("apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots.get_conn", return_value=DummyConn()),
                patch.object(repository, "list_kol_daily_snapshots", return_value=[snapshot]),
                patch.object(tencent_docs_client, "parse_doc_url", return_value=DocInfo("file", "sheet")),
                patch.object(tencent_docs_client, "fetch_grid", return_value=(existing_rows, 0)),
                patch.object(tencent_docs_client, "fetch_sheet_title", return_value="\u5927V\u6570\u636e\u7edf\u8ba1"),
                patch.object(
                    tencent_docs_client,
                    "post_batch_update",
                    side_effect=lambda requests, _context, doc=None: captured_requests.extend(requests),
                ),
            ):
                summary = writeback_kol_daily_snapshots_to_tencent_docs(
                    snapshot_date=target_date,
                    doc_url="https://docs.qq.com/sheet/file?tab=sheet",
                )
        finally:
            Config.KOL_DAILY_SNAPSHOT_WRITEBACK_FONT_SIZE = old_font_size

        self.assertEqual(summary["row_starts"]["2026-06-07"], 3)
        self.assertEqual(summary["written_rows"], 2)
        data_request = captured_requests[1]["updateRangeRequest"]["gridData"]
        self.assertEqual(data_request["startRow"], 2)
        first_cell = data_request["rows"][0]["values"][0]
        self.assertEqual(first_cell["cellFormat"]["textFormat"]["fontSize"], 10)

    def test_kol_daily_scheduled_job_targets_tomorrow(self) -> None:
        from apps.finance_crawler import app as app_module

        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 6, 6)

        captured_dates = []
        old_offset = Config.KOL_DAILY_SNAPSHOT_SCHEDULE_TARGET_OFFSET_DAYS
        Config.KOL_DAILY_SNAPSHOT_SCHEDULE_TARGET_OFFSET_DAYS = 1
        try:
            with (
                patch("apps.finance_crawler.app.date", FakeDate),
                patch(
                    "apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots.run_kol_daily_snapshot_pipeline",
                    side_effect=lambda snapshot_date=None: captured_dates.append(snapshot_date) or {},
                ),
            ):
                app_module.run_kol_daily_snapshot_job()
        finally:
            Config.KOL_DAILY_SNAPSHOT_SCHEDULE_TARGET_OFFSET_DAYS = old_offset

        self.assertEqual(captured_dates, [date(2026, 6, 7)])

    def test_kol_daily_crawl_row_parses_generated_sheet_columns(self) -> None:
        row = [
            "2026-06-06",
            "acct",
            "alipay",
            "https://ur.alipay.com/profile",
            "1",
            "\u5176\u5b83",
            "1.2\u4e07",
            "",
            "",
        ]

        parsed = parse_kol_crawl_source_row(
            row,
            sheet_row_index=12,
            doc=DocInfo("file", "sheet"),
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.metric_date, date(2026, 6, 6))
        self.assertEqual(parsed.account_name, "acct")
        self.assertEqual(parsed.homepage_url, "https://ur.alipay.com/profile")
        self.assertEqual(parsed.existing_fans_count, 12000)
        self.assertEqual(parsed.source_locator["row_index"], 12)
        self.assertEqual(parsed.source_locator["fans_col_index"], 6)
        self.assertEqual(parsed.source_locator["read_col_index"], 8)

    def test_kol_daily_writeback_locates_rows_by_date_and_url(self) -> None:
        rows = [
            ["\u65e5\u671f", "\u5927V\u540d\u79f0", "\u5e73\u53f0", "\u4e3b\u9875\u94fe\u63a5"],
            ["2026-06-05", "old", "alipay", "https://example.com/a"],
            ["2026-06-06", "acct", "alipay", "https://example.com/a"],
            ["2026-06-06", "acct2", "alipay", "https://example.com/a"],
            ["2026-06-06", "acct3", "alipay", "https://example.com/b"],
        ]

        located = locate_kol_daily_rows_by_date_url(rows, 0, date(2026, 6, 6))

        self.assertEqual(located["https://example.com/a"], [3, 4])
        self.assertEqual(located["https://example.com/b"], [5])

    def test_writeback_locator_uses_url_and_marks_duplicates(self) -> None:
        mapping = column_resolver.resolve_header(["帖子链接", "发帖账号昵称"])
        context = SheetWritebackContext(
            rows=[
                ["帖子链接", "发帖账号昵称"],
                ["https://example.com/a", ""],
                ["https://example.com/a", ""],
            ],
            start_row=0,
            mapping=mapping,
        )

        located = locate_by_post_url(context, "https://example.com/a")

        self.assertEqual(located.primary_row, 2)
        self.assertEqual(located.duplicate_rows, (3,))

    def test_writeback_locator_uses_date_and_homepage_url(self) -> None:
        context = SheetWritebackContext(
            rows=[
                ["日期", "大V名称", "平台", "主页链接"],
                ["2026-06-06", "acct", "tenpay", "https://example.com/a"],
                ["2026-06-06", "acct2", "tenpay", "https://example.com/a"],
            ],
            start_row=0,
            mapping=column_resolver.resolve_header(["日期", "大V名称", "平台", "主页链接"]),
        )

        located = locate_by_date_url(
            context,
            target_date=date(2026, 6, 6),
            url="https://example.com/a",
            date_col_index=0,
            url_col_index=3,
        )

        self.assertEqual(located.primary_row, 2)
        self.assertEqual(located.duplicate_rows, (3,))

    def test_error_classifier_normalizes_common_failures(self) -> None:
        self.assertEqual(classify_crawl_error("no adb device is ready").kind, DEVICE_UNAVAILABLE)
        self.assertEqual(classify_crawl_error("内容不见了", status="not_found").kind, PAGE_NOT_FOUND)
        self.assertEqual(classify_crawl_error("profile fans count was not detected").kind, FIELD_NOT_DETECTED)

    def test_profile_writeback_relocates_by_date_and_homepage_url(self) -> None:
        rows = [
            {
                "metric_source_id": 7,
                "metric_id": 8,
                "metric_date": date(2026, 6, 6),
                "fans_count": 771,
                "growth_count": 5,
                "homepage_url": "https://example.com/profile",
                "source_locator": {
                    "file_id": "file1",
                    "sheet_id": "sheet1",
                    "row_index": 99,
                    "date_col_index": 0,
                    "url_col_index": 3,
                    "fans_col_index": 6,
                },
            }
        ]

        with (
            patch("apps.finance_crawler.workflows.profile_metrics.get_pending_profile_writebacks", return_value=rows),
            patch("apps.finance_crawler.workflows.profile_metrics.mark_profile_writeback") as mark_writeback,
            patch.object(tencent_docs_client, "post_batch_update") as post_batch,
            patch.object(
                tencent_docs_client,
                "fetch_grid",
                return_value=(
                    [
                        ["日期", "大V名称", "平台", "主页链接", "第几群", "类型", "粉丝数", "增粉数"],
                        ["2026-06-06", "acct", "tenpay", "https://example.com/profile", "", "", "", ""],
                        ["2026-06-06", "acct dup", "tenpay", "https://example.com/profile", "", "", "", ""],
                    ],
                    0,
                ),
            ),
        ):
            written = writeback_profile_metrics()

        self.assertEqual(written, 1)
        requests = post_batch.call_args.args[0]
        primary = requests[0]["updateRangeRequest"]["gridData"]
        duplicate = requests[1]["updateRangeRequest"]["gridData"]
        self.assertEqual(primary["startRow"], 1)
        self.assertEqual(primary["startColumn"], 6)
        self.assertEqual(primary["rows"][0]["values"][0]["cellValue"]["text"], "771")
        self.assertEqual(primary["rows"][0]["values"][1]["cellValue"]["text"], "5")
        self.assertEqual(duplicate["startRow"], 2)
        self.assertEqual(duplicate["rows"][0]["values"][0]["cellValue"]["text"], "重复")
        self.assertEqual(duplicate["rows"][0]["values"][1]["cellValue"]["text"], "重复")
        self.assertEqual(mark_writeback.call_args.kwargs["locator"]["resolved_row_index"], 2)
        self.assertEqual(mark_writeback.call_args.kwargs["locator"]["duplicate_row_indexes"], [3])

    def test_kol_daily_crawl_scheduled_job_targets_today(self) -> None:
        from apps.finance_crawler import app as app_module

        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 6, 6)

        captured_dates = []
        with (
            patch("apps.finance_crawler.app.date", FakeDate),
            patch(
                "apps.finance_crawler.workflows.profile_triggers.run_default_kol_daily_profile_trigger",
                side_effect=lambda target_date=None, trigger_type=None: captured_dates.append((target_date, trigger_type)) or {},
            ),
        ):
            app_module.run_kol_daily_crawl_job()

        self.assertEqual(captured_dates, [(date(2026, 6, 6), "scheduled")])

    def test_kol_daily_crawl_skips_post_reads_when_read_count_not_requested(self) -> None:
        with (
            patch(
                "apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots.sync_kol_crawl_sources_from_writeback_doc",
                return_value={"imported": 1},
            ) as sync_sources,
            patch(
                "apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots.crawl_pending_profile_metrics",
                return_value=[{"status": "success"}],
            ),
            patch(
                "apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots.crawl_profile_post_reads",
                return_value=[{"status": "success"}],
            ) as crawl_reads,
            patch(
                "apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots.writeback_kol_daily_crawl_results_to_tencent_docs",
                return_value={"written": 1},
            ),
        ):
            summary = run_kol_daily_crawl_pipeline(
                target_date=date(2026, 6, 8),
                requested_fields=("fans_count", "growth_count"),
            )

        sync_sources.assert_called_once()
        self.assertEqual(sync_sources.call_args.kwargs["requested_fields"], ("fans_count", "growth_count"))
        crawl_reads.assert_not_called()
        self.assertEqual(summary["fans_crawled"], 1)
        self.assertEqual(summary["read_crawled"], 0)

    def test_kol_daily_writeback_only_writes_requested_metric_fields(self) -> None:
        metric_rows = [
            {
                "metric_source_id": 7,
                "metric_id": 8,
                "metric_date": date(2026, 6, 8),
                "metric_status": "success",
                "homepage_url": "https://example.com/profile",
                "fans_count": 22,
                "growth_count": 3,
                "read_count": 999,
                "source_locator": {"requested_fields": ["fans_count", "growth_count"]},
            }
        ]

        with (
            patch.object(
                tencent_docs_client,
                "fetch_grid",
                return_value=(
                    [
                        ["日期", "大V名称", "平台", "主页链接", "第几群", "类型", "粉丝数", "增粉数", "阅读数"],
                        ["2026-06-08", "acct", "tenpay", "https://example.com/profile", "", "", "", "", "old"],
                    ],
                    0,
                ),
            ),
            patch.object(tencent_docs_client, "fetch_sheet_title", return_value="大V数据统计"),
            patch.object(tencent_docs_client, "post_batch_update") as post_batch,
            patch(
                "apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots._kol_daily_crawl_metric_rows",
                return_value=metric_rows,
            ),
            patch(
                "apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots.mark_profile_writeback",
            ),
        ):
            summary = writeback_kol_daily_crawl_results_to_tencent_docs(
                target_date=date(2026, 6, 8),
                doc_url="https://docs.qq.com/sheet/file?tab=sheet",
            )

        self.assertEqual(summary["written"], 1)
        request = post_batch.call_args.args[0][0]["updateRangeRequest"]["gridData"]
        self.assertEqual(request["startColumn"], 6)
        values = request["rows"][0]["values"]
        self.assertEqual([item["cellValue"]["text"] for item in values], ["22", "3"])

    def test_scheduler_prefers_profile_trigger_over_legacy_profile_metrics(self) -> None:
        from apps.finance_crawler import app as app_module

        with (
            patch.object(Config, "PROFILE_METRICS_DOC_URL", "https://docs.qq.com/sheet/legacy"),
            patch.object(Config, "PROFILE_METRICS_INTERVAL_MINUTES", 5),
            patch.object(Config, "KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL", "https://docs.qq.com/sheet/kol"),
            patch.object(Config, "KOL_DAILY_CRAWL_TIME", "08:00"),
        ):
            self.assertTrue(app_module._kol_profile_trigger_scheduler_enabled())
            self.assertFalse(app_module._legacy_profile_scheduler_enabled())

    def test_scheduler_can_still_register_legacy_profile_metrics_without_profile_trigger(self) -> None:
        from apps.finance_crawler import app as app_module

        with (
            patch.object(Config, "PROFILE_METRICS_DOC_URL", "https://docs.qq.com/sheet/legacy"),
            patch.object(Config, "PROFILE_METRICS_INTERVAL_MINUTES", 5),
            patch.object(Config, "KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL", ""),
            patch.object(Config, "KOL_DAILY_CRAWL_TIME", "08:00"),
        ):
            self.assertFalse(app_module._kol_profile_trigger_scheduler_enabled())
            self.assertTrue(app_module._legacy_profile_scheduler_enabled())

    def test_scheduler_roles_split_independent_queues(self) -> None:
        from apps.finance_crawler import app as app_module

        with patch.object(Config, "SCHEDULER_ROLES", "submit,crawl"):
            self.assertEqual(app_module._configured_scheduler_roles(), {"submit", "crawl"})
            self.assertTrue(app_module._scheduler_role_enabled("submit", "v2_submit"))
            self.assertTrue(app_module._scheduler_role_enabled("crawl", "v2_crawl"))
            self.assertFalse(app_module._scheduler_role_enabled("writeback", "v2_writeback"))
            self.assertFalse(app_module._scheduler_role_enabled("profile"))

        with patch.object(Config, "SCHEDULER_ROLES", "profile"):
            self.assertEqual(app_module._configured_scheduler_roles(), {"profile"})
            self.assertTrue(app_module._scheduler_role_enabled("profile"))
            self.assertFalse(app_module._scheduler_role_enabled("crawl", "v2_crawl"))

        with patch.object(Config, "SCHEDULER_ROLES", "all"):
            self.assertEqual(app_module._configured_scheduler_roles(), {"all"})
            self.assertTrue(app_module._scheduler_role_enabled("profile"))
            self.assertTrue(app_module._scheduler_role_enabled("writeback"))

    def test_resolves_fixed_fields_from_shifted_titles(self) -> None:
        header = [
            "unused",
            "\u53d1\u5e16\u65f6\u95f4",
            "\u9605\u8bfb\u91cf",
            "\u539f\u6587\u94fe\u63a5",
            "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0",
            "\u5907\u6ce8",
        ]

        mapping = column_resolver.resolve_header(header)

        self.assertTrue(mapping.ok, mapping.problems)
        self.assertEqual(mapping.columns["post_time"], 1)
        self.assertEqual(mapping.columns["read_count"], 2)
        self.assertEqual(mapping.columns["post_url"], 3)
        self.assertEqual(mapping.columns["account_name"], 4)
        self.assertEqual(mapping.columns["remark"], 5)

    def test_prefers_specific_alias_over_generic_link(self) -> None:
        header = ["\u94fe\u63a5", "\u5e16\u5b50\u94fe\u63a5"]

        mapping = column_resolver.resolve_header(header)

        self.assertEqual(mapping.columns["post_url"], 1)

    def test_does_not_match_short_header_from_long_alias(self) -> None:
        mapping = column_resolver.resolve_header(["\u5185\u5bb9", "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0"])

        self.assertFalse(mapping.ok)
        self.assertNotIn("post_url", mapping.columns)

    def test_reports_missing_required_url(self) -> None:
        mapping = column_resolver.resolve_header(["\u53d1\u5e16\u8d26\u53f7\u6635\u79f0", "\u9605\u8bfb\u6570"])

        self.assertFalse(mapping.ok)
        self.assertIn("missing required field: post_url", mapping.problems[0])

    def test_reports_ambiguous_same_rank_titles(self) -> None:
        mapping = column_resolver.resolve_header(["\u5e16\u5b50\u94fe\u63a5", "\u5e16\u5b50\u94fe\u63a5"])

        self.assertFalse(mapping.ok)
        self.assertTrue(any("ambiguous field: post_url" in problem for problem in mapping.problems))

    def test_extracts_source_rows_with_original_sheet_index(self) -> None:
        rows = [
            ["\u5e16\u5b50\u94fe\u63a5", "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0", "\u53d1\u5e16\u65f6\u95f4"],
            ["https://example.com/a", "acct", "2026-06-04 10:00"],
            ["", "empty", ""],
        ]
        mapping = column_resolver.resolve_header(rows[0])

        source_rows = extract_source_rows(
            rows,
            mapping.columns,
            start_row=0,
            business_date=date(2026, 6, 4),
        )

        self.assertEqual(len(source_rows), 1)
        self.assertEqual(source_rows[0].row_index, 2)
        self.assertEqual(source_rows[0].post_url, "https://example.com/a")
        self.assertEqual(source_rows[0].account_name, "acct")
        self.assertEqual(source_rows[0].business_date, date(2026, 6, 4))

    def test_task_dedupe_key_is_stable(self) -> None:
        rows = [
            ["\u5e16\u5b50\u94fe\u63a5", "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0"],
            ["https://example.com/a", "acct"],
        ]
        mapping = column_resolver.resolve_header(rows[0])
        source_row = extract_source_rows(rows, mapping.columns)[0]

        submission = build_task_submission(
            source_row,
            document_id=12,
            sheet_id="0604",
            task_type="detail",
            app_type="wechat",
        )

        self.assertEqual(
            submission.dedupe_key,
            make_dedupe_key(12, "0604", 2, "https://example.com/a", "detail"),
        )
        self.assertEqual(
            submission.dedupe_key,
            make_dedupe_key(12, "0604", 99, "https://example.com/a", "detail"),
        )
        self.assertEqual(submission.source_locator["row_index"], 2)

    def test_task_submission_includes_extra_locator(self) -> None:
        rows = [
            ["\u5e16\u5b50\u94fe\u63a5"],
            ["https://example.com/a"],
        ]
        mapping = column_resolver.resolve_header(rows[0])
        source_row = extract_source_rows(rows, mapping.columns)[0]

        submission = build_task_submission(
            source_row,
            document_id=12,
            sheet_id="sheet1",
            task_type="read_count",
            source_locator_extra={"column_mapping_id": 99, "file_id": "file1"},
        )

        self.assertEqual(submission.source_locator["column_mapping_id"], 99)
        self.assertEqual(submission.source_locator["file_id"], "file1")

    def test_select_sheet_for_date(self) -> None:
        base = DocInfo("file1", "default")
        sheets = [
            SheetInfo("file1", "a", "0603"),
            SheetInfo("file1", "b", "0604"),
        ]

        selected = select_sheet_for_date(base, sheets, date(2026, 6, 4))

        self.assertEqual(selected.sheet_id, "b")

    def test_sheet_selector_supports_fixed_and_title_modes(self) -> None:
        base = DocInfo("file1", "")
        sheets = [
            SheetInfo("file1", "a", "初检"),
            SheetInfo("file1", "b", "详情"),
        ]

        self.assertEqual(
            select_sheet(base_doc=base, sheets=sheets, selector={"mode": "fixed_sheet", "sheet_id": "b"}).sheet_id,
            "b",
        )
        self.assertEqual(
            select_sheet(base_doc=base, sheets=sheets, selector={"mode": "sheet_title", "title": "初检"}).sheet_id,
            "a",
        )
        self.assertEqual(
            select_sheet(
                base_doc=base,
                sheets=sheets,
                selector={"mode": "sheet_title_contains", "keyword": "详"},
            ).sheet_id,
            "b",
        )

    def test_date_sheet_selector_returns_all_matching_date_sheets(self) -> None:
        base = DocInfo("file1", "")
        sheets = [
            SheetInfo("file1", "a", "0605-alpha"),
            SheetInfo("file1", "b", "0605-beta"),
            SheetInfo("file1", "c", "0604-alpha"),
        ]

        selected = select_sheets(
            base_doc=base,
            sheets=sheets,
            selector={"mode": "date_sheet"},
            target_date=date(2026, 6, 5),
        )

        self.assertEqual([item.sheet_id for item in selected], ["a", "b"])
        self.assertEqual(
            select_sheet(
                base_doc=base,
                sheets=sheets,
                selector={"mode": "date_sheet"},
                target_date=date(2026, 6, 5),
            ).sheet_id,
            "a",
        )

    def test_tencent_doc_url_info_keeps_base_document_identity(self) -> None:
        info = tencent_docs_client.parse_doc_url_info(
            "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm?tab=qlmz7y"
        )

        self.assertEqual(info.file_id, "DYm1aSG9nb3NHVWZm")
        self.assertEqual(info.sheet_id, "qlmz7y")
        self.assertEqual(info.base_url, "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm")

        base_info = tencent_docs_client.parse_doc_url_info("https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm")
        self.assertEqual(base_info.file_id, "DYm1aSG9nb3NHVWZm")
        self.assertEqual(base_info.sheet_id, "")

    def test_parse_business_date_from_sheet_title(self) -> None:
        self.assertEqual(parse_business_date_from_sheet_title("0604"), date(date.today().year, 6, 4))
        self.assertEqual(parse_business_date_from_sheet_title("2026-06-04"), date(2026, 6, 4))

    def test_read_count_intake_uses_document_source_snapshot(self) -> None:
        class FakeDocumentSource:
            def __init__(self) -> None:
                self.calls = []

            def load_sheet(self, *, target_date=None, range_a1=None, sheet_selector=None):
                self.calls.append({"target_date": target_date, "range_a1": range_a1, "sheet_selector": sheet_selector})
                return DocumentSheetSnapshot(
                    source_type="fake_docs",
                    doc_url="fake://doc",
                    file_id="file1",
                    sheet_id="sheet1",
                    sheet_title="0604",
                    rows=[
                        ["\u5e16\u5b50\u94fe\u63a5", "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0", "\u9605\u8bfb\u6570"],
                        ["https://example.com/a", "acct", ""],
                    ],
                    start_row=5,
                    business_date=date(2026, 6, 4),
                    title="Fake Doc",
                )

        source = FakeDocumentSource()
        captured = {}

        def submit_tasks(_conn, submissions):
            captured["submissions"] = submissions
            return len(submissions)

        with (
            patch.object(repository, "upsert_document", return_value=12) as upsert_document,
            patch.object(repository, "upsert_document_sheet") as upsert_sheet,
            patch.object(repository, "upsert_column_mapping", return_value=34) as upsert_mapping,
            patch.object(repository, "upsert_source_rows", return_value={7: 56}) as upsert_rows,
            patch.object(repository, "submit_task_submissions", side_effect=submit_tasks),
        ):
            summary = submit_read_count_tasks_from_source(
                object(),
                source=source,
                target_date=date(2026, 6, 4),
                range_a1="A1:Z100",
                created_by="test",
            )

        self.assertEqual(
            source.calls,
            [{"target_date": date(2026, 6, 4), "range_a1": "A1:Z100", "sheet_selector": None}],
        )
        upsert_document.assert_called_once_with(
            ANY,
            source_type="fake_docs",
            doc_url="fake://doc",
            file_id="file1",
            title="Fake Doc",
        )
        upsert_sheet.assert_called_once()
        upsert_mapping.assert_called_once()
        upsert_rows.assert_called_once()
        self.assertEqual(summary.document_id, 12)
        self.assertEqual(summary.sheet_id, "sheet1")
        self.assertEqual(summary.source_rows, 1)
        self.assertEqual(summary.submissions, 1)
        submission = captured["submissions"][0]
        self.assertEqual(submission.source_row_id, 56)
        self.assertEqual(submission.source_locator["file_id"], "file1")
        self.assertEqual(submission.source_locator["column_mapping_id"], 34)

    def test_tencent_docs_source_selects_date_sheet_from_base_url(self) -> None:
        with (
            patch.object(
                tencent_docs_client,
                "fetch_file_sheets",
                return_value=[
                    SheetInfo("file1", "s0603", "0603"),
                    SheetInfo("file1", "s0604", "0604"),
                ],
            ) as fetch_sheets,
            patch.object(tencent_docs_client, "fetch_grid", return_value=([["header"], ["value"]], 0)) as fetch_grid,
        ):
            snapshot = TencentDocsSource("https://docs.qq.com/sheet/file1").load_sheet(
                target_date=date(2026, 6, 4)
            )

        fetch_sheets.assert_called_once_with("file1")
        fetch_grid.assert_called_once()
        self.assertEqual(snapshot.doc_url, "https://docs.qq.com/sheet/file1")
        self.assertEqual(snapshot.sheet_id, "s0604")
        self.assertEqual(snapshot.business_date, date(2026, 6, 4))

    def test_document_intake_can_submit_initial_check_tasks(self) -> None:
        class FakeDocumentSource:
            def load_sheet(self, *, target_date=None, range_a1=None, sheet_selector=None):
                return DocumentSheetSnapshot(
                    source_type="fake_docs",
                    doc_url="fake://doc",
                    file_id="file1",
                    sheet_id="sheet1",
                    sheet_title="0604",
                    rows=[
                        ["\u5e16\u5b50\u94fe\u63a5", "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0"],
                        ["https://example.com/a", "acct"],
                    ],
                    start_row=0,
                    business_date=date(2026, 6, 4),
                    title="Fake Doc",
                )

        captured = {}

        def submit_tasks(_conn, submissions):
            captured["submissions"] = submissions
            return len(submissions)

        with (
            patch.object(repository, "upsert_document", return_value=12),
            patch.object(repository, "upsert_document_sheet"),
            patch.object(repository, "upsert_column_mapping", return_value=34),
            patch.object(repository, "upsert_source_rows", return_value={2: 56}),
            patch.object(repository, "submit_task_submissions", side_effect=submit_tasks),
        ):
            summary = submit_document_tasks_from_source(
                object(),
                source=FakeDocumentSource(),
                task_type=INITIAL_CHECK,
                created_by="test",
            )

        self.assertEqual(summary.submissions, 1)
        submission = captured["submissions"][0]
        self.assertEqual(submission.task_type, INITIAL_CHECK)
        self.assertEqual(submission.source_locator["column_mapping_id"], 34)

    def test_trigger_submit_keeps_first_row_for_duplicate_urls(self) -> None:
        rows = [
            ["\u5e16\u5b50\u94fe\u63a5", "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0"],
            ["https://example.com/a", "acct-a"],
            ["https://example.com/a", "acct-a-repeat"],
            ["https://example.com/b", "acct-b"],
        ]
        mapping = column_resolver.resolve_header(rows[0])
        source_rows = extract_source_rows(rows, mapping.columns)

        canonical, duplicates = _canonical_rows_by_url(source_rows)

        self.assertEqual([row.row_index for row in canonical], [2, 4])
        self.assertEqual([row.row_index for row in duplicates], [3])

    def test_trigger_date_sheet_can_target_yesterday(self) -> None:
        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 6, 6)

        config = {
            "sheet_selector": {"mode": "date_sheet"},
            "submit_policy": {"target_date_offset_days": -1},
        }

        with patch("apps.finance_crawler.crawler_app.workflows.submit_triggers.date", FakeDate):
            self.assertEqual(_effective_target_date(config, None), date(2026, 6, 5))

        self.assertEqual(_effective_target_date(config, date(2026, 6, 4)), date(2026, 6, 4))

    def test_initial_check_trigger_must_target_today(self) -> None:
        with self.assertRaisesRegex(ValueError, "initial_check date_sheet trigger must target today"):
            _validate_trigger_binding_policy(
                selector={"mode": "date_sheet"},
                submit_policy={"target_date_offset_days": -1},
                task_type=INITIAL_CHECK,
            )

        _validate_trigger_binding_policy(
            selector={"mode": "date_sheet"},
            submit_policy={"target_date_offset_days": 0},
            task_type=INITIAL_CHECK,
        )
        _validate_trigger_binding_policy(
            selector={"mode": "date_sheet"},
            submit_policy={"target_date_offset_days": -1},
            task_type=DETAIL,
        )

    def test_initial_check_trigger_skips_weekends(self) -> None:
        config = {"config_key": "daily_check", "sheet_selector": {"mode": "date_sheet"}}
        skipped = _skip_summary_for_trigger(
            config,
            [TriggerBinding(task_type=INITIAL_CHECK, field_names=(ACCOUNT_NAME,))],
            date(2026, 6, 6),
        )

        self.assertIsNotNone(skipped)
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["skip_reason"], "initial_check_weekend")
        self.assertIsNone(
            _skip_summary_for_trigger(
                config,
                [TriggerBinding(task_type=INITIAL_CHECK, field_names=(ACCOUNT_NAME,))],
                date(2026, 6, 5),
            )
        )
        self.assertIsNone(
            _skip_summary_for_trigger(
                config,
                [TriggerBinding(task_type=DETAIL, field_names=(READ_COUNT_FIELD,))],
                date(2026, 6, 6),
            )
        )

    def test_trigger_submit_splits_one_sheet_into_multiple_task_bindings(self) -> None:
        class FakeSource:
            def __init__(self, doc_url: str) -> None:
                self.doc_url = doc_url

            def load_sheet(self, *, target_date=None, range_a1=None, sheet_selector=None):
                return DocumentSheetSnapshot(
                    source_type="tencent_docs",
                    doc_url=self.doc_url,
                    file_id="file1",
                    sheet_id="sheet1",
                    sheet_title="0604",
                    rows=[
                        ["\u5e16\u5b50\u94fe\u63a5", "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0", "\u9605\u8bfb\u6570"],
                        ["https://example.com/a", "acct-a", ""],
                        ["https://example.com/a", "acct-a-repeat", ""],
                        ["https://example.com/b", "acct-b", ""],
                    ],
                    start_row=0,
                    business_date=date(2026, 6, 4),
                    title="Fake Doc",
                )

        captured = []

        def submit_tasks(_conn, submissions):
            captured.extend(submissions)
            return len(submissions)

        config = {
            "id": 9,
            "config_key": "daily_monitor",
            "doc_url": "https://docs.qq.com/sheet/file1",
            "sheet_selector": {"mode": "linked_tab", "fallback_sheet_id": "sheet1"},
        }
        bindings = [
            TriggerBinding(task_type=INITIAL_CHECK, field_names=(ACCOUNT_NAME,), binding_id=101),
            TriggerBinding(task_type=DETAIL, field_names=(ACCOUNT_NAME, READ_COUNT_FIELD, SCREENSHOT), binding_id=102),
        ]

        with (
            patch(
                "apps.finance_crawler.crawler_app.workflows.submit_triggers.TencentDocsSource",
                FakeSource,
            ),
            patch.object(repository, "upsert_document", return_value=12),
            patch.object(repository, "upsert_document_sheet"),
            patch.object(repository, "upsert_column_mapping", return_value=34),
            patch.object(repository, "upsert_source_rows", return_value={2: 56, 3: 57, 4: 58}),
            patch.object(repository, "submit_task_submissions", side_effect=submit_tasks),
        ):
            summary = _submit_bindings_from_tencent_doc(
                object(),
                config=config,
                bindings=bindings,
                target_date=None,
                limit=None,
                submit_run_id=77,
                created_by="test",
            )

        self.assertEqual(summary["status"], "success")
        self.assertEqual(summary["source_rows"], 3)
        self.assertEqual(summary["unique_urls"], 2)
        self.assertEqual(summary["duplicate_rows"], 1)
        self.assertEqual(summary["skipped_rows"], 1)
        self.assertEqual(summary["submitted_tasks"], 4)
        self.assertEqual(summary["tasks"], {INITIAL_CHECK: 2, DETAIL: 2})
        self.assertEqual([item.task_type for item in captured], [INITIAL_CHECK, INITIAL_CHECK, DETAIL, DETAIL])
        self.assertEqual([item.row_index for item in captured if item.task_type == INITIAL_CHECK], [2, 4])
        self.assertEqual([item.row_index for item in captured if item.task_type == DETAIL], [2, 4])
        self.assertEqual(captured[0].source_locator["trigger_config_id"], 9)
        self.assertEqual(captured[0].source_locator["trigger_binding_id"], 101)
        self.assertEqual(captured[2].source_locator["trigger_binding_id"], 102)

    def test_trigger_date_sheet_submits_all_matching_sheets(self) -> None:
        class FakeSource:
            calls = []

            def __init__(self, doc_url: str) -> None:
                self.doc_url = doc_url

            def load_sheet(self, *, target_date=None, range_a1=None, sheet_selector=None):
                sheet_id = sheet_selector["sheet_id"]
                FakeSource.calls.append({"target_date": target_date, "sheet_selector": sheet_selector})
                titles = {"s1": "0605-alpha", "s2": "0605-beta"}
                return DocumentSheetSnapshot(
                    source_type="tencent_docs",
                    doc_url=self.doc_url,
                    file_id="file1",
                    sheet_id=sheet_id,
                    sheet_title=titles[sheet_id],
                    rows=[
                        ["帖子链接", "发帖账号昵称", "阅读数"],
                        [f"https://example.com/{sheet_id}", f"acct-{sheet_id}", ""],
                    ],
                    start_row=0,
                    business_date=date(2026, 6, 5),
                    title="Fake Doc",
                )

        FakeSource.calls = []
        captured = []

        def submit_tasks(_conn, submissions):
            captured.extend(submissions)
            return len(submissions)

        config = {
            "id": 9,
            "config_key": "daily_detail",
            "doc_url": "https://docs.qq.com/sheet/file1",
            "sheet_selector": {"mode": "date_sheet"},
        }

        with (
            patch.object(
                tencent_docs_client,
                "fetch_file_sheets",
                return_value=[
                    SheetInfo("file1", "s1", "0605-alpha"),
                    SheetInfo("file1", "s2", "0605-beta"),
                    SheetInfo("file1", "s3", "0604-alpha"),
                ],
            ),
            patch(
                "apps.finance_crawler.crawler_app.workflows.submit_triggers.TencentDocsSource",
                FakeSource,
            ),
            patch.object(repository, "upsert_document", return_value=12),
            patch.object(repository, "upsert_document_sheet"),
            patch.object(repository, "upsert_column_mapping", side_effect=[34, 35]),
            patch.object(repository, "upsert_source_rows", return_value={2: 56}),
            patch.object(repository, "submit_task_submissions", side_effect=submit_tasks),
        ):
            summary = _submit_bindings_from_tencent_doc(
                object(),
                config=config,
                bindings=[TriggerBinding(task_type=DETAIL, field_names=(ACCOUNT_NAME, READ_COUNT_FIELD), binding_id=102)],
                target_date=date(2026, 6, 5),
                limit=None,
                submit_run_id=77,
                created_by="test",
            )

        self.assertEqual(summary["status"], "success")
        self.assertEqual(summary["sheet_id"], "multi:2")
        self.assertEqual(summary["sheet_count"], 2)
        self.assertEqual(summary["source_rows"], 2)
        self.assertEqual(summary["submitted_tasks"], 2)
        self.assertEqual(summary["tasks"], {DETAIL: 2})
        self.assertEqual([item["sheet_id"] for item in summary["sheets"]], ["s1", "s2"])
        self.assertEqual([item["sheet_selector"]["sheet_id"] for item in FakeSource.calls], ["s1", "s2"])
        self.assertEqual([item.sheet_id for item in captured], ["s1", "s2"])
        self.assertEqual([item.source_locator["submit_run_id"] for item in captured], [77, 77])

    def test_read_count_writeback_values_marks_failures_as_n(self) -> None:
        self.assertEqual(
            read_count_writeback_values({"status": "success", "read_count": 123}),
            {"read_count": 123, "remark": "成功"},
        )
        self.assertEqual(
            read_count_writeback_values({"status": "not_found", "not_found_reason": "page_missing"}),
            {"read_count": "N", "remark": "page_missing"},
        )
        self.assertEqual(
            read_count_writeback_values({"status": "error", "error": "adb failed", "final_submission_status": "retry"}),
            {},
        )
        self.assertEqual(
            read_count_writeback_values({"status": "error", "error": "adb failed", "final_submission_status": "failed"}),
            {"read_count": "N", "remark": "失败：adb failed"},
        )

    def test_read_count_task_handler_is_registered(self) -> None:
        handler = get_task_handler(READ_COUNT)

        self.assertEqual(handler.task_type, READ_COUNT)
        self.assertEqual(handler.runtime.name, "adb")
        self.assertEqual(handler.writeback_values({"status": "success", "read_count": 7}), {"read_count": 7, "remark": "成功"})

    def test_initial_check_and_detail_handlers_are_registered(self) -> None:
        initial_handler = get_task_handler(INITIAL_CHECK)
        detail_handler = get_task_handler(DETAIL)

        self.assertEqual(initial_handler.runtime.name, "adb")
        self.assertEqual(detail_handler.runtime.name, "adb")
        self.assertEqual(
            initial_handler.writeback_values({"status": "success", "account_name": "acct"}),
            {ACCOUNT_NAME: "acct", CHECK_RESULT: "Y", REMARK: "成功"},
        )
        self.assertEqual(
            detail_handler.writeback_values(
                {
                    "status": "success",
                    "account_name": "acct",
                    "read_count": 12,
                    "comment_count": 3,
                    "screenshot_path": "shot.png",
                }
            ),
            {
                ACCOUNT_NAME: "acct",
                READ_COUNT_FIELD: 12,
                COMMENT_COUNT: 3,
                SCREENSHOT: "shot.png",
                REMARK: "成功",
            },
        )

    def test_initial_check_writeback_values_marks_not_found_as_n(self) -> None:
        self.assertEqual(
            initial_check_writeback_values({"status": "not_found", "error": "page_missing"}),
            {ACCOUNT_NAME: "N", CHECK_RESULT: "N", REMARK: "page_missing"},
        )
        self.assertEqual(initial_check_writeback_values({"status": "error", "error": "adb failed"}), {})
        self.assertEqual(
            initial_check_writeback_values({"status": "error", "error": "adb failed", "final_submission_status": "failed"}),
            {REMARK: "失败：adb failed"},
        )

    def test_detail_writeback_values_marks_missing_content_as_n(self) -> None:
        self.assertEqual(
            detail_writeback_values({"status": "deleted", "error": "content deleted"}),
            {ACCOUNT_NAME: "N", READ_COUNT_FIELD: "N", REMARK: "content deleted"},
        )
        self.assertEqual(detail_writeback_values({"status": "error", "error": "adb failed"}), {})
        self.assertEqual(
            detail_writeback_values({"status": "error", "error": "adb failed", "final_submission_status": "failed"}),
            {REMARK: "失败：adb failed"},
        )

    def test_v2_initial_check_recovers_transient_app_failure(self) -> None:
        transient = {"status": "error", "error": "account name was not detected"}
        success = {"status": "success", "account_name": "acct"}

        with (
            patch.object(Config, "APP_OPEN_RECOVERY_RETRIES", 1),
            patch("apps.finance_crawler.crawler_app.strategies.post.resolve_short_url", return_value="app://post"),
            patch("apps.finance_crawler.crawler_app.strategies.post.open_url") as open_url_mock,
            patch(
                "apps.finance_crawler.crawler_app.strategies.post.check_record_exists_and_account",
                side_effect=[transient, success],
            ),
            patch("apps.finance_crawler.crawler_app.strategies.post.restart_app_for_url", return_value=True) as restart,
        ):
            result = crawl_initial_check_task(
                {
                    "id": 7,
                    "row_index": 2,
                    "post_url": "https://example.com/a",
                    "app_type": SOURCE_ALIPAY,
                    "source_locator": {"requested_fields": [ACCOUNT_NAME]},
                }
            )

        self.assertEqual(open_url_mock.call_count, 2)
        restart.assert_called_once_with("app://post", source_app=SOURCE_ALIPAY)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["app_restart_attempts"], 1)
        self.assertEqual(
            initial_check_writeback_values(result),
            {ACCOUNT_NAME: "acct", CHECK_RESULT: "Y", REMARK: "\u6210\u529f"},
        )

    def test_v2_detail_recovers_blank_page_failure(self) -> None:
        blank = {"status": "error", "error": "post content was not detected; page may be blank"}
        success = {"status": "success", "read_count": 3, "comment_count": 0, "account_name": "acct"}

        with (
            patch.object(Config, "DETAIL_BLANK_REOPEN_RETRIES", 1),
            patch.object(Config, "DETAIL_BLANK_REOPEN_WAIT", 0),
            patch("apps.finance_crawler.crawler_app.strategies.post.resolve_short_url", return_value="app://post"),
            patch("apps.finance_crawler.crawler_app.strategies.post.open_url") as open_url_mock,
            patch(
                "apps.finance_crawler.crawler_app.strategies.post.scrape_record_content",
                side_effect=[blank, success],
            ),
            patch("apps.finance_crawler.crawler_app.strategies.post.restart_app_for_url", return_value=True) as restart,
        ):
            result = crawl_detail_task(
                {
                    "id": 8,
                    "row_index": 2,
                    "post_url": "https://example.com/a",
                    "app_type": SOURCE_ALIPAY,
                    "source_locator": {"requested_fields": [ACCOUNT_NAME, READ_COUNT_FIELD]},
                }
            )

        self.assertEqual(open_url_mock.call_count, 2)
        restart.assert_called_once_with("app://post", source_app=SOURCE_ALIPAY)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["app_metrics"]["blank_reopen_attempts"], 1)
        self.assertEqual(result["app_metrics"]["app_restart_attempts"], 1)
        self.assertEqual(
            detail_writeback_values(result),
            {ACCOUNT_NAME: "acct", READ_COUNT_FIELD: 3, REMARK: "\u6210\u529f"},
        )

    def test_read_count_capture_plan_uses_simple_ui_capture(self) -> None:
        with patch.object(Config, "DOC_LINK_READS_ENABLE_OCR", False):
            plan = plan_capture_for_task(
                task_type=READ_COUNT,
                app_type=SOURCE_ALIPAY,
                fields=(READ_COUNT_FIELD,),
            )

        self.assertIn(ACTION_UI_CONTROLS, plan.actions)
        self.assertIn(ACTION_SCREENSHOT, plan.actions)
        self.assertIn(ACTION_TAP_RETRY, plan.actions)
        self.assertNotIn(ACTION_OCR, plan.actions)
        self.assertEqual(plan.max_scrolls, 0)
        self.assertEqual(plan.complexity, "interactive_retry")

    def test_capture_plan_can_be_built_from_profile(self) -> None:
        plan = plan_capture_from_profile(
            {
                "app_type": SOURCE_ALIPAY,
                "action_names": [ACTION_OPEN_LINK, ACTION_SCREENSHOT, ACTION_OCR],
                "capture_config": {"max_scrolls": 2, "open_retries": 1},
            },
            task_type=DETAIL,
            app_type=SOURCE_ALIPAY,
            fields=(READ_COUNT_FIELD, SCREENSHOT),
        )

        self.assertEqual(plan.actions, (ACTION_OPEN_LINK, ACTION_SCREENSHOT, ACTION_OCR))
        self.assertEqual(plan.max_scrolls, 2)
        self.assertEqual(plan.open_retries, 1)

    def test_capture_plan_merges_fields_into_minimal_action_set(self) -> None:
        with patch.object(Config, "DOC_LINK_READS_ENABLE_OCR", False):
            plan = plan_capture_for_task(
                task_type=DETAIL,
                app_type=SOURCE_ALIPAY,
                fields=(ACCOUNT_NAME, READ_COUNT_FIELD, SCREENSHOT, REMARK, READ_COUNT_FIELD),
            )

        self.assertEqual(
            plan.actions,
            (ACTION_OPEN_LINK, ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_TAP_RETRY),
        )
        self.assertEqual(plan.fields, (ACCOUNT_NAME, READ_COUNT_FIELD, SCREENSHOT, REMARK))
        self.assertEqual(len(plan.actions), len(set(plan.actions)))
        self.assertEqual(plan.max_scrolls, 0)

    def test_minimal_action_set_adds_dependencies_once(self) -> None:
        actions = plan_minimal_capture_actions(
            app_type=SOURCE_ALIPAY,
            fields=("trade_details", SCREENSHOT),
        )

        self.assertEqual(
            actions.actions,
            (ACTION_OPEN_LINK, ACTION_UI_CONTROLS, ACTION_SCREENSHOT, ACTION_OCR, ACTION_CLICK_DETAIL),
        )
        self.assertEqual(len(actions.actions), len(set(actions.actions)))

    def test_read_count_capture_plan_enables_app_ocr_policy(self) -> None:
        with patch.object(Config, "DOC_LINK_READS_ENABLE_OCR", False):
            plan = plan_capture_for_task(
                task_type=READ_COUNT,
                app_type=SOURCE_TENPAY,
                fields=(READ_COUNT_FIELD,),
            )

        self.assertIn(ACTION_OCR, plan.actions)

    def test_comment_count_capture_plan_allows_scroll(self) -> None:
        plan = plan_capture_for_task(
            task_type="article_detail",
            app_type=SOURCE_ALIPAY,
            fields=(COMMENT_COUNT, SCREENSHOT),
        )

        self.assertIn(ACTION_SCROLL, plan.actions)
        self.assertGreaterEqual(plan.max_scrolls, 1)
        self.assertEqual(plan.complexity, "scroll_capture")

    def test_interactive_detail_capture_plan_uses_click_and_ocr(self) -> None:
        plan = plan_capture_for_task(
            task_type="detail",
            app_type=SOURCE_TENPAY,
            fields=("trade_details",),
        )

        self.assertIn(ACTION_CLICK_DETAIL, plan.actions)
        self.assertIn(ACTION_OCR, plan.actions)
        self.assertEqual(plan.complexity, "click_detail")

    def test_execution_summary_counts_terminal_statuses(self) -> None:
        summary = execution_summary(
            READ_COUNT,
            [{"id": 1}, {"id": 2, "capture_action_profile_id": 9}, {"id": 3}],
            [
                {"status": "success"},
                {"status": "not_found"},
                {"status": "error"},
            ],
        )

        self.assertEqual(summary["task_type"], READ_COUNT)
        self.assertEqual(summary["submissions"], 3)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["not_found"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["capture_profiles"], {"matched": 1, "fallback": 2})
        self.assertIsNone(summary["device"])

    def test_execution_summary_includes_adb_device(self) -> None:
        summary = execution_summary(
            READ_COUNT,
            [{"id": 1}],
            [{"status": "success"}],
            device=AdbDevice(
                serial="192.168.1.10:5555",
                state="device",
                transport="wifi",
                model="P30Pro",
                product="HWVOG",
                device_name="HWVOG",
            ),
        )

        self.assertEqual(summary["device"]["serial"], "192.168.1.10:5555")
        self.assertEqual(summary["device"]["transport"], "wifi")
        self.assertEqual(summary["device"]["model"], "P30Pro")

    def test_v2_crawl_worker_uses_human_pacing_delays(self) -> None:
        with (
            patch.object(Config, "DETAIL_POST_DELAY_MIN", 1.0),
            patch.object(Config, "DETAIL_POST_DELAY_MAX", 2.0),
            patch("apps.finance_crawler.crawler_app.workflows.execution.random.uniform", return_value=1.5) as uniform,
            patch("apps.finance_crawler.crawler_app.workflows.execution.time.sleep") as sleep,
        ):
            _sleep_between_submissions(DETAIL)

        uniform.assert_called_once_with(1.0, 2.0)
        sleep.assert_called_once_with(1.5)

        with (
            patch.object(Config, "POST_DELAY_MIN", 2.0),
            patch.object(Config, "POST_DELAY_MAX", 4.5),
            patch("apps.finance_crawler.crawler_app.workflows.execution.random.uniform", return_value=3.0) as uniform,
            patch("apps.finance_crawler.crawler_app.workflows.execution.time.sleep") as sleep,
        ):
            _sleep_between_submissions(INITIAL_CHECK)

        uniform.assert_called_once_with(2.0, 4.5)
        sleep.assert_called_once_with(3.0)

        with (
            patch.object(Config, "READ_COUNT_POST_DELAY_MIN", 20.0),
            patch.object(Config, "READ_COUNT_POST_DELAY_MAX", 45.0),
            patch("apps.finance_crawler.crawler_app.workflows.execution.random.uniform", return_value=30.0) as uniform,
            patch("apps.finance_crawler.crawler_app.workflows.execution.time.sleep") as sleep,
        ):
            _sleep_between_submissions(READ_COUNT)

        uniform.assert_called_once_with(20.0, 45.0)
        sleep.assert_called_once_with(30.0)

    def test_adb_transport_classification(self) -> None:
        self.assertEqual(classify_adb_transport("192.168.1.10:5555"), "wifi")
        self.assertEqual(classify_adb_transport("ABC123", "product:x model:y transport_id:1"), "usb")
        self.assertEqual(classify_adb_transport("ABC123"), "unknown")

    def test_failed_execution_retries_until_max_attempts(self) -> None:
        self.assertEqual(
            repository._submission_status_after_execution("error", attempts=1, max_attempts=3),
            "retry",
        )
        self.assertEqual(
            repository._submission_status_after_execution("error", attempts=3, max_attempts=3),
            "failed",
        )
        self.assertEqual(
            repository._submission_status_after_execution("not_found", attempts=1, max_attempts=3),
            "not_found",
        )

    def test_start_task_execution_uses_submission_attempts_for_retry_budget(self) -> None:
        class FakeCursor:
            def __init__(self) -> None:
                self.insert_params = None
                self.update_params = None
                self.lastrowid = 99
                self._result = None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, sql, params=None):
                statement = str(sql)
                if "FROM task_submissions" in statement and "FOR UPDATE" in statement:
                    self._result = {"id": 7, "attempts": 1, "max_attempts": 3, "status": "pending"}
                elif "MAX(attempt_no)" in statement:
                    self._result = {"max_attempt_no": 3}
                elif "INSERT INTO task_executions" in statement:
                    self.insert_params = params
                elif "UPDATE task_submissions" in statement:
                    self.update_params = params

            def fetchone(self):
                return self._result

        class FakeConn:
            def __init__(self) -> None:
                self.cursor_obj = FakeCursor()

            def cursor(self):
                return self.cursor_obj

        conn = FakeConn()

        execution_id = repository.start_task_execution(conn, 7)

        self.assertEqual(execution_id, 99)
        self.assertEqual(conn.cursor_obj.insert_params, (7, 4))
        self.assertEqual(conn.cursor_obj.update_params, (2, 99, 7))

    def test_start_task_execution_skips_exhausted_submission(self) -> None:
        class FakeCursor:
            def __init__(self) -> None:
                self._result = None
                self.insert_called = False
                self.update_called = False

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, sql, params=None):
                statement = str(sql)
                if "FROM task_submissions" in statement and "FOR UPDATE" in statement:
                    self._result = {"id": 7, "attempts": 3, "max_attempts": 3, "status": "retry"}
                elif "INSERT INTO task_executions" in statement:
                    self.insert_called = True
                elif "UPDATE task_submissions" in statement:
                    self.update_called = True

            def fetchone(self):
                return self._result

        class FakeConn:
            def __init__(self) -> None:
                self.cursor_obj = FakeCursor()

            def cursor(self):
                return self.cursor_obj

        conn = FakeConn()

        execution_id = repository.start_task_execution(conn, 7)

        self.assertIsNone(execution_id)
        self.assertFalse(conn.cursor_obj.insert_called)
        self.assertFalse(conn.cursor_obj.update_called)

    def test_document_task_config_defaults_fields_by_task(self) -> None:
        self.assertEqual(default_fields_for_task(INITIAL_CHECK), (ACCOUNT_NAME,))
        self.assertEqual(default_fields_for_task(DETAIL), (ACCOUNT_NAME, READ_COUNT_FIELD, SCREENSHOT))
        self.assertEqual(default_fields_for_task(READ_COUNT), (READ_COUNT_FIELD,))
        self.assertEqual(parse_field_names("account_name, read_count, screenshot"), (
            ACCOUNT_NAME,
            READ_COUNT_FIELD,
            SCREENSHOT,
        ))

    def test_document_task_config_validation_accepts_daily_detail(self) -> None:
        problems = validate_document_task_config_payload(
            task_type=DETAIL,
            field_names=(ACCOUNT_NAME, READ_COUNT_FIELD, SCREENSHOT),
            sheet_selector={"mode": "date_sheet"},
        )

        self.assertEqual(problems, ())

    def test_document_task_config_validation_rejects_unknown_fields(self) -> None:
        problems = validate_document_task_config_payload(
            task_type=DETAIL,
            field_names=(ACCOUNT_NAME, "unknown_field"),
            sheet_selector={"mode": "date_sheet"},
        )

        self.assertIn("unknown field_names: unknown_field", problems)

    def test_document_task_config_validation_rejects_wrong_task_fields(self) -> None:
        problems = validate_document_task_config_payload(
            task_type=INITIAL_CHECK,
            field_names=(READ_COUNT_FIELD,),
            sheet_selector={"mode": "date_sheet"},
        )

        self.assertIn("field_names not supported by task_type initial_check: read_count", problems)

    def test_document_task_config_validation_rejects_bad_sheet_selectors(self) -> None:
        self.assertIn(
            "fixed_sheet requires sheet_id",
            validate_document_task_config_payload(
                task_type=DETAIL,
                field_names=(READ_COUNT_FIELD,),
                sheet_selector={"mode": "fixed_sheet"},
            ),
        )
        self.assertIn(
            "linked_tab requires a configured URL with tab=...",
            validate_document_task_config_payload(
                task_type=DETAIL,
                field_names=(READ_COUNT_FIELD,),
                sheet_selector={"mode": "linked_tab"},
            ),
        )

    def test_build_sheet_selector_keeps_unambiguous_shape(self) -> None:
        selector = build_sheet_selector(
            mode="sheet_group",
            sheet_ids="a, b",
        )

        self.assertEqual(selector, {"mode": "sheet_group", "sheet_ids": ["a", "b"]})

    def test_requested_fields_filter_writeback_values(self) -> None:
        values = {
            ACCOUNT_NAME: "acct",
            READ_COUNT_FIELD: 12,
            COMMENT_COUNT: 3,
            SCREENSHOT: "shot.png",
            REMARK: "clear me",
        }
        filtered = _requested_writeback_values(
            {"source_locator": {"requested_fields": [ACCOUNT_NAME, SCREENSHOT]}},
            values,
        )

        self.assertEqual(filtered, {ACCOUNT_NAME: "acct", SCREENSHOT: "shot.png", REMARK: "clear me"})

    def test_execution_attaches_capture_action_profile(self) -> None:
        class Handler:
            task_type = DETAIL

        submission = {
            "app_type": SOURCE_ALIPAY,
            "source_locator": {"requested_fields": [ACCOUNT_NAME, READ_COUNT_FIELD]},
        }
        profile = {"id": 42, "field_names": [ACCOUNT_NAME, READ_COUNT_FIELD], "action_names": []}

        with patch.object(repository, "get_capture_action_profile", return_value=profile) as get_profile:
            _attach_capture_action_profile(object(), Handler, submission)

        get_profile.assert_called_once_with(
            ANY,
            app_type=SOURCE_ALIPAY,
            task_type=DETAIL,
            field_names=(ACCOUNT_NAME, READ_COUNT_FIELD),
        )
        self.assertEqual(submission["capture_action_profile_id"], 42)
        self.assertEqual(submission["capture_action_profile"], profile)

    def test_document_correction_plans_audited_writeback(self) -> None:
        with (
            patch.object(
                repository,
                "get_source_row_by_position",
                return_value={
                    "id": 7,
                    "document_id": 1,
                    "sheet_id": "sheet1",
                    "row_index": 2,
                    "column_mapping_id": 34,
                    "row_values": {READ_COUNT_FIELD: "12"},
                },
            ) as get_row,
            patch.object(
                repository,
                "get_column_mapping",
                return_value={"id": 34, "mapping": {READ_COUNT_FIELD: 3}},
            ) as get_mapping,
            patch("apps.finance_crawler.crawler_app.corrections.models.insert_correction", return_value=99) as insert,
            patch.object(repository, "create_writeback_plans", return_value=1) as create_plans,
        ):
            summary = plan_document_correction_in_conn(
                object(),
                document_id=1,
                sheet_id="sheet1",
                row_index=2,
                field_name=READ_COUNT_FIELD,
                new_value="15",
                reason="manual fix",
                operator_name="tester",
            )

        get_row.assert_called_once_with(ANY, document_id=1, sheet_id="sheet1", row_index=2)
        get_mapping.assert_called_once_with(ANY, 34)
        insert.assert_called_once()
        create_plans.assert_called_once_with(
            ANY,
            submission_id=None,
            execution_id=None,
            document_id=1,
            sheet_id="sheet1",
            row_index=2,
            column_mapping_id=34,
            values={READ_COUNT_FIELD: "15"},
            payload_extra={"correction_id": 99, "source": "manual_correction"},
        )
        self.assertEqual(summary["correction_id"], 99)
        self.assertEqual(summary["old_value"], "12")
        self.assertEqual(summary["new_value"], "15")

    def test_document_correction_rejects_unmapped_field(self) -> None:
        with (
            patch.object(
                repository,
                "get_source_row_by_position",
                return_value={
                    "id": 7,
                    "document_id": 1,
                    "sheet_id": "sheet1",
                    "row_index": 2,
                    "column_mapping_id": 34,
                    "row_values": {READ_COUNT_FIELD: "12"},
                },
            ),
            patch.object(repository, "get_column_mapping", return_value={"id": 34, "mapping": {ACCOUNT_NAME: 2}}),
        ):
            with self.assertRaisesRegex(ValueError, "field not mapped"):
                plan_document_correction_in_conn(
                    object(),
                    document_id=1,
                    sheet_id="sheet1",
                    row_index=2,
                    field_name=READ_COUNT_FIELD,
                    new_value="15",
                    reason="manual fix",
                )

    def test_configured_document_correction_resolves_row_by_config_and_date(self) -> None:
        config = {
            "config_key": "daily_detail",
            "source_type": "tencent_docs",
            "file_id": "file1",
            "sheet_id": "",
            "sheet_selector": {"mode": "date_sheet"},
            "status": "active",
        }
        source_row = {
            "id": 7,
            "document_id": 1,
            "sheet_id": "s0604",
            "row_index": 2,
            "column_mapping_id": 34,
            "row_values": {READ_COUNT_FIELD: "12"},
        }

        with (
            patch.object(repository, "get_document_task_config", return_value=config) as get_config,
            patch.object(repository, "find_source_rows_for_correction", return_value=[source_row]) as find_rows,
            patch.object(repository, "get_column_mapping", return_value={"id": 34, "mapping": {READ_COUNT_FIELD: 3}}),
            patch("apps.finance_crawler.crawler_app.corrections.models.insert_correction", return_value=99),
            patch.object(repository, "create_writeback_plans", return_value=1),
        ):
            summary = plan_configured_document_correction_in_conn(
                object(),
                config_key="daily_detail",
                target_date=date(2026, 6, 4),
                row_index=2,
                field_name=READ_COUNT_FIELD,
                new_value="15",
                reason="manual fix",
            )

        get_config.assert_called_once_with(ANY, "daily_detail")
        find_rows.assert_called_once_with(
            ANY,
            source_type="tencent_docs",
            file_id="file1",
            sheet_id=None,
            row_index=2,
            post_url=None,
            business_date=date(2026, 6, 4),
        )
        self.assertEqual(summary["document_id"], 1)
        self.assertEqual(summary["sheet_id"], "s0604")
        self.assertEqual(summary["row_index"], 2)

    def test_configured_document_correction_resolves_fixed_sheet_by_link(self) -> None:
        config = {
            "config_key": "fixed_detail",
            "source_type": "tencent_docs",
            "file_id": "file1",
            "sheet_id": "fallback",
            "sheet_selector": {"mode": "fixed_sheet", "sheet_id": "fixed"},
            "status": "active",
        }
        source_row = {
            "id": 7,
            "document_id": 1,
            "sheet_id": "fixed",
            "row_index": 2,
            "column_mapping_id": 34,
            "row_values": {READ_COUNT_FIELD: "12"},
        }

        with (
            patch.object(repository, "get_document_task_config", return_value=config),
            patch.object(repository, "find_source_rows_for_correction", return_value=[source_row]) as find_rows,
            patch.object(repository, "get_column_mapping", return_value={"id": 34, "mapping": {READ_COUNT_FIELD: 3}}),
            patch("apps.finance_crawler.crawler_app.corrections.models.insert_correction", return_value=99),
            patch.object(repository, "create_writeback_plans", return_value=1),
        ):
            summary = plan_configured_document_correction_in_conn(
                object(),
                config_key="fixed_detail",
                post_url="https://example.com/a",
                field_name=READ_COUNT_FIELD,
                new_value="15",
                reason="manual fix",
            )

        find_rows.assert_called_once_with(
            ANY,
            source_type="tencent_docs",
            file_id="file1",
            sheet_id="fixed",
            row_index=None,
            post_url="https://example.com/a",
            business_date=None,
        )
        self.assertEqual(summary["sheet_id"], "fixed")

    def test_configured_document_correction_rejects_ambiguous_target(self) -> None:
        config = {
            "config_key": "daily_detail",
            "source_type": "tencent_docs",
            "file_id": "file1",
            "sheet_selector": {"mode": "date_sheet"},
            "status": "active",
        }

        with (
            patch.object(repository, "get_document_task_config", return_value=config),
            patch.object(repository, "find_source_rows_for_correction", return_value=[{"id": 1}, {"id": 2}]),
        ):
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                plan_configured_document_correction_in_conn(
                    object(),
                    config_key="daily_detail",
                    row_index=2,
                    field_name=READ_COUNT_FIELD,
                    new_value="15",
                    reason="manual fix",
                )

    def test_writeback_marks_correction_success(self) -> None:
        plans = [
            {
                "id": 11,
                "file_id": "file1",
                "sheet_id": "sheet1",
                "row_index": 99,
                "field_name": READ_COUNT_FIELD,
                "value_text": "15",
                "mapping": {READ_COUNT_FIELD: 3},
                "payload": {"correction_id": 99},
                "current_post_url": "https://example.com/a",
            }
        ]

        with (
            patch.object(repository, "get_pending_writeback_plans", return_value=plans) as get_plans,
            patch.object(repository, "mark_writeback_plans") as mark_plans,
            patch.object(repository, "mark_corrections") as mark_corrections,
            patch.object(
                tencent_docs_client,
                "fetch_grid",
                return_value=(
                    [
                        ["帖子链接", "阅读数"],
                        ["https://example.com/other", ""],
                        ["https://example.com/a", ""],
                    ],
                    0,
                ),
            ),
            patch.object(tencent_docs_client, "post_batch_update") as post_batch,
        ):
            summary = apply_pending_writebacks(object())

        get_plans.assert_called_once_with(ANY, limit=None, source=None)
        post_batch.assert_called_once()
        request = post_batch.call_args.args[0][0]["updateRangeRequest"]["gridData"]
        self.assertEqual(request["startRow"], 2)
        self.assertEqual(request["startColumn"], 1)
        mark_plans.assert_called_once_with(ANY, [11], status="success")
        mark_corrections.assert_called_once_with(ANY, [99], status="success")
        self.assertEqual(summary["success"], 1)

    def test_correction_writeback_only_applies_manual_correction_plans(self) -> None:
        class FakeConn:
            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        conn = FakeConn()
        with (
            patch("apps.finance_crawler.crawler_app.workflows.corrections.get_conn", return_value=conn),
            patch(
                "apps.finance_crawler.crawler_app.workflows.corrections.apply_pending_writebacks",
                return_value={"planned": 0, "success": 0, "failed": 0, "skipped": 0},
            ) as apply_writebacks,
            patch("apps.finance_crawler.crawler_app.workflows.corrections.log_task"),
        ):
            summary = apply_pending_correction_writebacks(limit=5)

        apply_writebacks.assert_called_once_with(conn, limit=5, source="manual_correction")
        self.assertEqual(summary["planned"], 0)

    def test_writeback_marks_correction_skipped_when_field_unmapped(self) -> None:
        plans = [
            {
                "id": 11,
                "file_id": "file1",
                "sheet_id": "sheet1",
                "row_index": 2,
                "field_name": READ_COUNT_FIELD,
                "value_text": "15",
                "mapping": {},
                "payload": {"correction_id": 99},
                "current_post_url": "https://example.com/a",
            }
        ]

        with (
            patch.object(repository, "get_pending_writeback_plans", return_value=plans),
            patch.object(repository, "mark_writeback_plans") as mark_plans,
            patch.object(repository, "mark_corrections") as mark_corrections,
            patch.object(
                tencent_docs_client,
                "fetch_grid",
                return_value=([["帖子链接"]], 0),
            ),
        ):
            summary = apply_pending_writebacks(object())

        mark_plans.assert_called_once_with(ANY, [11], status="skipped", error="field not mapped in current sheet: read_count")
        mark_corrections.assert_called_once_with(ANY, [99], status="skipped")
        self.assertEqual(summary["skipped"], 1)

    def test_writeback_marks_duplicate_current_url_rows(self) -> None:
        plans = [
            {
                "id": 12,
                "file_id": "file1",
                "sheet_id": "sheet1",
                "row_index": 2,
                "field_name": ACCOUNT_NAME,
                "value_text": "acct",
                "mapping": {ACCOUNT_NAME: 1},
                "payload": {},
                "current_post_url": "https://example.com/a",
            }
        ]

        with (
            patch.object(repository, "get_pending_writeback_plans", return_value=plans),
            patch.object(repository, "mark_writeback_plans") as mark_plans,
            patch.object(repository, "mark_corrections") as mark_corrections,
            patch.object(tencent_docs_client, "post_batch_update") as post_batch,
            patch.object(
                tencent_docs_client,
                "fetch_grid",
                return_value=(
                    [
                        ["帖子链接", "发帖账号昵称"],
                        ["https://example.com/a", ""],
                        ["https://example.com/a", ""],
                    ],
                    0,
                ),
            ),
        ):
            summary = apply_pending_writebacks(object())

        post_batch.assert_called_once()
        requests = post_batch.call_args.args[0]
        first_clear = requests[0]["updateRangeRequest"]["gridData"]
        first_write = requests[1]["updateRangeRequest"]["gridData"]
        duplicate_clear = requests[2]["updateRangeRequest"]["gridData"]
        duplicate_write = requests[3]["updateRangeRequest"]["gridData"]
        self.assertEqual(first_clear["startRow"], 1)
        self.assertEqual(first_clear["startColumn"], 1)
        self.assertEqual(first_clear["rows"][0]["values"][0]["cellValue"]["text"], "")
        self.assertEqual(first_write["startRow"], 1)
        self.assertEqual(first_write["startColumn"], 1)
        self.assertEqual(first_write["rows"][0]["values"][0]["cellValue"]["text"], "acct")
        self.assertEqual(duplicate_clear["startRow"], 2)
        self.assertEqual(duplicate_clear["startColumn"], 1)
        self.assertEqual(duplicate_clear["rows"][0]["values"][0]["cellValue"]["text"], "")
        self.assertEqual(duplicate_write["startRow"], 2)
        self.assertEqual(duplicate_write["startColumn"], 1)
        self.assertEqual(duplicate_write["rows"][0]["values"][0]["cellValue"]["text"], "\u91cd\u590d")
        mark_plans.assert_called_once_with(ANY, [12], status="success")
        mark_corrections.assert_called_once_with(ANY, [], status="success")
        self.assertEqual(summary["success"], 1)

    def test_writeback_uploads_screenshot_instead_of_writing_path_text(self) -> None:
        plans = [
            {
                "id": 13,
                "file_id": "file1",
                "sheet_id": "sheet1",
                "row_index": 2,
                "field_name": SCREENSHOT,
                "value_text": r"C:\Code\adb\shot.png",
                "mapping": {SCREENSHOT: 1},
                "payload": {},
                "current_post_url": "https://example.com/a",
            }
        ]

        with (
            patch.object(repository, "get_pending_writeback_plans", return_value=plans),
            patch.object(repository, "mark_writeback_plans") as mark_plans,
            patch.object(repository, "mark_corrections") as mark_corrections,
            patch.object(tencent_docs_client, "post_batch_update") as post_batch,
            patch(
                "apps.finance_crawler.crawler_app.writeback.executor.post_screenshot_images",
                return_value=[],
            ) as post_images,
            patch.object(
                tencent_docs_client,
                "fetch_grid",
                return_value=(
                    [
                        ["帖子链接", "截图"],
                        ["https://example.com/a", ""],
                    ],
                    0,
                ),
            ),
        ):
            summary = apply_pending_writebacks(object())

        post_batch.assert_called_once()
        clear_request = post_batch.call_args.args[0][0]["updateRangeRequest"]["gridData"]
        self.assertEqual(clear_request["startRow"], 1)
        self.assertEqual(clear_request["startColumn"], 1)
        self.assertEqual(clear_request["rows"][0]["values"][0]["cellValue"]["text"], "")
        post_images.assert_called_once_with([(2, r"C:\Code\adb\shot.png", 1)], doc=ANY)
        mark_plans.assert_called_once_with(ANY, [13], status="success")
        mark_corrections.assert_called_once_with(ANY, [], status="success")
        self.assertEqual(summary["success"], 1)

    def test_writeback_marks_screenshot_upload_failure_instead_of_writing_path(self) -> None:
        plans = [
            {
                "id": 14,
                "file_id": "file1",
                "sheet_id": "sheet1",
                "row_index": 2,
                "field_name": SCREENSHOT,
                "value_text": r"C:\Code\adb\shot.png",
                "mapping": {SCREENSHOT: 1},
                "payload": {},
                "current_post_url": "https://example.com/a",
            }
        ]
        fallback_request = {"updateRangeRequest": {"gridData": {"fallback": True}}}

        with (
            patch.object(repository, "get_pending_writeback_plans", return_value=plans),
            patch.object(repository, "mark_writeback_plans") as mark_plans,
            patch.object(repository, "mark_corrections") as mark_corrections,
            patch.object(tencent_docs_client, "post_batch_update") as post_batch,
            patch(
                "apps.finance_crawler.crawler_app.writeback.executor.post_screenshot_images",
                return_value=[fallback_request],
            ),
            patch.object(
                tencent_docs_client,
                "fetch_grid",
                return_value=(
                    [
                        ["帖子链接", "截图"],
                        ["https://example.com/a", ""],
                    ],
                    0,
                ),
            ),
        ):
            summary = apply_pending_writebacks(object())

        post_batch.assert_called_once()
        mark_plans.assert_called_once_with(
            ANY,
            [14],
            status="error",
            error="screenshot image writeback failed for 1 cells; local path fallback is disabled",
        )
        mark_corrections.assert_called_once_with(ANY, [], status="error")
        self.assertEqual(summary["success"], 0)
        self.assertEqual(summary["failed"], 1)


if __name__ == "__main__":
    unittest.main()
