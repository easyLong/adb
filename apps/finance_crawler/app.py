"""Command-line entrypoint, scheduler, and supervisor."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from datetime import date, datetime, time as dt_time
from typing import Any

import schedule

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs import client as tencent_docs_client
from apps.finance_crawler.integrations.tencent_docs import columns as tencent_docs_columns
from apps.finance_crawler.jobs.detail import run_detail
from apps.finance_crawler.services.alerts import send_alert
from apps.finance_crawler.services.report import generate_report
from apps.finance_crawler.services.runtime_config import (
    format_runtime_config_for_cli,
    load_runtime_config,
    set_runtime_config,
)
from apps.finance_crawler.storage.db import init_db, log_task
from apps.finance_crawler.utils.logger import get_logger
from apps.finance_crawler.workflows.article_details import (
    crawl_pending_article_details,
    run_article_details,
    sync_article_sources_from_tencent_docs,
    writeback_article_details,
)
from apps.finance_crawler.workflows.docs_link_reads import run_docs_link_reads
from apps.finance_crawler.workflows.kol_daily_db_pipeline import run_kol_daily_db_pipeline
from apps.finance_crawler.workflows.local_excel_detail import run_local_excel_detail
from apps.finance_crawler.workflows.kol_tenpay_external_reads import (
    run_kol_tenpay_external_reads,
    run_kol_tenpay_external_reads_lookback,
)
from apps.finance_crawler.workflows.single_link_detail import run_single_link_detail
from apps.finance_crawler.workflows.tencent_docs_fetch import fetch_and_save

logger = get_logger("scheduler")


def safe_run(func: Callable[[], Any], task_name: str) -> Any:
    logger.info("================== task start: %s ==================", task_name)
    start = time.time()
    try:
        _reload_runtime_config_for_task(task_name)
        result = func()
        duration = time.time() - start
        logger.info("task completed: %s, %.1fs", task_name, duration)
        return result
    except Exception as exc:
        duration = time.time() - start
        logger.error("task failed: %s, %s", task_name, exc)
        logger.error(traceback.format_exc())
        log_task(task_name, "error", str(exc), duration)
        send_alert(
            f"Task failed: {task_name}",
            str(exc),
            dedupe_key=f"task_failed:{task_name}",
            extra={"duration": round(duration, 2)},
        )
        return None


def heartbeat() -> None:
    logger.info("scheduler heartbeat")


def run_v2_crawl_workers() -> dict[str, Any]:
    from apps.finance_crawler.crawler_app.tasks.types import DETAIL, INITIAL_CHECK, READ_COUNT
    from apps.finance_crawler.crawler_app.workflows.document_tasks import crawl_pending_document_tasks

    return {
        INITIAL_CHECK: crawl_pending_document_tasks(INITIAL_CHECK),
        DETAIL: crawl_pending_document_tasks(DETAIL),
        READ_COUNT: crawl_pending_document_tasks(READ_COUNT),
    }


def run_v2_writeback_worker() -> dict[str, Any]:
    from apps.finance_crawler.crawler_app.workflows.document_tasks import writeback_document_task_results

    return writeback_document_task_results("all")


def run_kol_daily_db_pipeline_job(target_date: date | None = None) -> dict[str, Any]:
    from apps.finance_crawler.crawler_app.storage.db import init_crawler_app_db

    init_crawler_app_db()
    return run_kol_daily_db_pipeline(target_date=target_date or date.today())


def run_wechat_hourly_sync_job(target_date: date | None = None) -> dict[str, Any]:
    from apps.finance_crawler.crawler_app.storage.db import init_crawler_app_db
    from apps.finance_crawler.crawler_app.storage.ops_platform import init_ops_platform_intake_tables
    from apps.finance_crawler.crawler_app.workflows.wechat_demand_intake import run_wechat_hourly_sync

    init_ops_platform_intake_tables()
    init_crawler_app_db()
    return run_wechat_hourly_sync(target_date=target_date or date.today())


def _reload_runtime_config_for_task(task_name: str) -> None:
    if task_name == "heartbeat":
        return
    load_runtime_config()


def _kol_daily_db_pipeline_scheduler_enabled() -> bool:
    return bool(Config.KOL_DAILY_CRAWL_TIME)


def _configured_scheduler_roles() -> set[str]:
    raw = str(Config.SCHEDULER_ROLES or "all").strip().lower()
    if not raw or raw == "all":
        return {"all"}
    roles = {item.strip() for item in re.split(r"[,;\s]+", raw) if item.strip()}
    return roles or {"all"}


def _scheduler_role_enabled(*roles: str) -> bool:
    configured = _configured_scheduler_roles()
    return "all" in configured or any(role in configured for role in roles)


def _parse_hhmm(value: str, *, default: str) -> dt_time:
    raw = (value or default).strip()
    try:
        parsed = datetime.strptime(raw, "%H:%M")
    except ValueError:
        logger.warning("invalid HH:MM time %r; fallback to %s", value, default)
        parsed = datetime.strptime(default, "%H:%M")
    return parsed.time()


def _parse_scheduler_workdays(value: str) -> set[int]:
    days: set[int] = set()
    for item in re.split(r"[,;\s]+", value or ""):
        if not item:
            continue
        try:
            day = int(item)
        except ValueError:
            continue
        if 1 <= day <= 7:
            days.add(day)
    return days or {1, 2, 3, 4, 5}


def _wechat_schedule_times() -> list[str]:
    start = _parse_hhmm(Config.WECHAT_SCHEDULER_START_TIME, default="08:00")
    end = _parse_hhmm(Config.WECHAT_SCHEDULER_END_TIME, default="19:00")
    interval = max(int(Config.WECHAT_SCHEDULER_INTERVAL_MINUTES or 0), 1)
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes < start_minutes:
        logger.warning("WECHAT_SCHEDULER_END_TIME is before start time; fallback to start only")
        end_minutes = start_minutes
    values: list[str] = []
    minute = start_minutes
    while minute <= end_minutes:
        values.append(f"{minute // 60:02d}:{minute % 60:02d}")
        minute += interval
    return values


def _run_scheduled_wechat_hourly_sync() -> dict[str, Any] | None:
    if not Config.WECHAT_SCHEDULER_ENABLED:
        logger.info("skip wechat hourly sync because WECHAT_SCHEDULER_ENABLED=false")
        return None
    workdays = _parse_scheduler_workdays(Config.WECHAT_SCHEDULER_WORKDAYS)
    today = datetime.now().isoweekday()
    if today not in workdays:
        logger.info("skip wechat hourly sync on non-workday: %s", today)
        return {"status": "skipped", "reason": "non_workday", "weekday": today}
    return run_wechat_hourly_sync_job(date.today())


def _register_jobs() -> None:
    roles = _configured_scheduler_roles()
    logger.info("scheduler roles enabled: %s", ",".join(sorted(roles)))

    if _scheduler_role_enabled("legacy") and Config.ENABLE_LEGACY_SCHEDULER_JOBS:
        schedule.every(Config.FETCH_INTERVAL_MINUTES).minutes.do(
            safe_run, fetch_and_save, "fetch_docs"
        )
        logger.info("registered fetch every %s minutes", Config.FETCH_INTERVAL_MINUTES)

    if _scheduler_role_enabled("submit", "v2_submit") and Config.SUBMIT_WORKER_INTERVAL_SECONDS > 0:
        from apps.finance_crawler.crawler_app.workflows.submit_triggers import submit_due_document_triggers

        schedule.every(Config.SUBMIT_WORKER_INTERVAL_SECONDS).seconds.do(
            safe_run, submit_due_document_triggers, "v2_submit_worker"
        )
        logger.info(
            "registered v2 submit worker every %s seconds",
            Config.SUBMIT_WORKER_INTERVAL_SECONDS,
        )

    if _scheduler_role_enabled("crawl", "v2_crawl") and Config.V2_CRAWL_WORKER_INTERVAL_SECONDS > 0:
        schedule.every(Config.V2_CRAWL_WORKER_INTERVAL_SECONDS).seconds.do(
            safe_run, run_v2_crawl_workers, "v2_crawl_worker"
        )
        logger.info(
            "registered v2 crawl worker every %s seconds",
            Config.V2_CRAWL_WORKER_INTERVAL_SECONDS,
        )

    if _scheduler_role_enabled("writeback", "v2_writeback") and Config.V2_WRITEBACK_WORKER_INTERVAL_SECONDS > 0:
        schedule.every(Config.V2_WRITEBACK_WORKER_INTERVAL_SECONDS).seconds.do(
            safe_run, run_v2_writeback_worker, "v2_writeback_worker"
        )
        logger.info(
            "registered v2 writeback worker every %s seconds",
            Config.V2_WRITEBACK_WORKER_INTERVAL_SECONDS,
        )

    if _scheduler_role_enabled("legacy") and Config.ENABLE_LEGACY_SCHEDULER_JOBS and Config.ENABLE_CHECKER:
        from apps.finance_crawler.jobs.checker import run_check

        schedule.every(Config.CHECK_INTERVAL_MINUTES).minutes.do(
            safe_run, run_check, "check"
        )
        logger.info("registered check every %s minutes", Config.CHECK_INTERVAL_MINUTES)

    if _scheduler_role_enabled("legacy") and Config.ENABLE_LEGACY_SCHEDULER_JOBS and Config.DETAIL_INTERVAL_MINUTES > 0:
        schedule.every(Config.DETAIL_INTERVAL_MINUTES).minutes.do(
            safe_run, run_detail, "detail_crawl_due"
        )
        logger.info(
            "registered detail due-task scan every %s minutes; task due time is scheduled_at <= now",
            Config.DETAIL_INTERVAL_MINUTES,
        )

    if _scheduler_role_enabled("legacy") and Config.ENABLE_LEGACY_SCHEDULER_JOBS:
        schedule.every().day.at(Config.REPORT_TIME).do(
            safe_run, generate_report, "report"
        )
        logger.info("registered report daily at %s", Config.REPORT_TIME)

    if _scheduler_role_enabled("profile", "kol_pipeline") and _kol_daily_db_pipeline_scheduler_enabled():
        schedule.every().day.at(Config.KOL_DAILY_CRAWL_TIME).do(
            safe_run, run_kol_daily_db_pipeline_job, "kol_daily_db_pipeline"
        )
        logger.info(
            "registered KOL daily DB pipeline at %s",
            Config.KOL_DAILY_CRAWL_TIME,
        )

    if _scheduler_role_enabled("wechat") and Config.WECHAT_SCHEDULER_ENABLED:
        for schedule_time in _wechat_schedule_times():
            schedule.every().day.at(schedule_time).do(
                safe_run,
                _run_scheduled_wechat_hourly_sync,
                "wechat_hourly_sync",
            )
        logger.info(
            "registered WeChat hourly sync at %s on workdays %s",
            ",".join(_wechat_schedule_times()),
            Config.WECHAT_SCHEDULER_WORKDAYS,
        )

    if _scheduler_role_enabled("heartbeat") and Config.HEARTBEAT_INTERVAL_MINUTES > 0:
        schedule.every(Config.HEARTBEAT_INTERVAL_MINUTES).minutes.do(
            safe_run, heartbeat, "heartbeat"
        )
        logger.info("registered heartbeat every %s minutes", Config.HEARTBEAT_INTERVAL_MINUTES)


def run_forever() -> None:
    logger.info("Finance crawler scheduler starting")
    init_db()
    load_runtime_config()
    _register_jobs()

    if _scheduler_role_enabled("legacy") and Config.ENABLE_LEGACY_SCHEDULER_JOBS:
        logger.info("sync Tencent Docs once on startup")
        safe_run(fetch_and_save, "fetch_docs_init")
    if _scheduler_role_enabled("submit", "v2_submit") and Config.SUBMIT_WORKER_INTERVAL_SECONDS > 0:
        from apps.finance_crawler.crawler_app.workflows.submit_triggers import submit_due_document_triggers

        logger.info("scan v2 document trigger configs once on startup")
        safe_run(submit_due_document_triggers, "v2_submit_worker_init")
    if _scheduler_role_enabled("crawl", "v2_crawl") and Config.V2_CRAWL_WORKER_INTERVAL_SECONDS > 0:
        logger.info("scan v2 crawl queues once on startup")
        safe_run(run_v2_crawl_workers, "v2_crawl_worker_init")
    if _scheduler_role_enabled("writeback", "v2_writeback") and Config.V2_WRITEBACK_WORKER_INTERVAL_SECONDS > 0:
        logger.info("scan v2 writeback queue once on startup")
        safe_run(run_v2_writeback_worker, "v2_writeback_worker_init")
    if _scheduler_role_enabled("legacy") and Config.ENABLE_LEGACY_SCHEDULER_JOBS and Config.DETAIL_INTERVAL_MINUTES > 0:
        logger.info("scan due detail tasks once on startup")
        safe_run(run_detail, "detail_crawl_due_init")
    logger.info("scheduler running; press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        time.sleep(10)


def run_supervisor() -> int:
    restarts = 0
    logger.info("scheduler supervisor starting")
    while True:
        child = subprocess.Popen([sys.executable, "-m", "apps.finance_crawler.app"])
        try:
            exit_code = child.wait()
        except KeyboardInterrupt:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            logger.info("scheduler supervisor stopped")
            return 0

        if exit_code == 0:
            logger.info("scheduler exited cleanly")
            return 0

        restarts += 1
        message = f"scheduler process exited with code {exit_code}; restart #{restarts}"
        logger.error(message)
        send_alert(
            "Scheduler crashed",
            message,
            dedupe_key="scheduler_crashed",
            extra={"exit_code": exit_code, "restarts": restarts},
        )

        if Config.SUPERVISOR_MAX_RESTARTS and restarts >= Config.SUPERVISOR_MAX_RESTARTS:
            logger.error("supervisor restart limit reached")
            return exit_code

        time.sleep(Config.SUPERVISOR_RESTART_DELAY_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Finance crawler scheduler")
    parser.add_argument(
        "--once",
        choices=[
            "db",
            "crawler-app-db",
            "ops-platform-db",
            "wechat-groups-list",
            "wechat-groups-capture",
            "wechat-messages-parse",
            "wechat-demand-intake",
            "wechat-hourly-sync",
            "device-pool-status",
            "device-pool-refresh",
            "config",
            "fetch",
            "check",
            "detail",
            "excel-detail",
            "link-detail",
            "report",
            "kol-tenpay-external-reads",
            "kol-daily-db-pipeline",
            "capture-file-server",
            "article-sync",
            "article-crawl",
            "article-writeback",
            "article-details",
            "doc-link-reads",
            "doc-columns-check",
            "v2-read-count-submit",
            "v2-read-count-crawl",
            "v2-read-count-writeback",
            "v2-read-count",
            "v2-initial-check-submit",
            "v2-initial-check-crawl",
            "v2-initial-check-writeback",
            "v2-initial-check",
            "v2-detail-submit",
            "v2-detail-crawl",
            "v2-detail-writeback",
            "v2-detail",
            "v2-doc-config-set",
            "v2-doc-config-check",
            "v2-doc-config-list",
            "v2-doc-config-submit",
            "v2-doc-config-run",
            "v2-trigger-set",
            "v2-trigger-bind",
            "v2-trigger-list",
            "v2-trigger-submit",
            "v2-submit-worker-once",
            "v2-crawl-worker-once",
            "v2-writeback-worker-once",
            "v2-correction-plan",
            "v2-correction-writeback",
            "v2-correction-apply",
            "kol-settlement-metrics-submit",
            "kol-settlement-metrics-crawl",
            "kol-settlement-metrics-writeback",
            "kol-settlement-metrics",
        ],
        help="run one task and exit",
    )
    parser.add_argument("--config-set", action="append", default=[], help="runtime config KEY=VALUE")
    parser.add_argument("--tencent-doc-url", default="", help="set runtime Tencent Docs URL")
    parser.add_argument("--excel-input-path", default="", help="set runtime Excel detail input path")
    parser.add_argument("--single-link", default="", help="set one-shot detail test link")
    parser.add_argument("--report-date", default="", help="report date in YYYY-MM-DD format; defaults to yesterday")
    parser.add_argument("--limit", type=int, default=0, help="limit rows/tasks for one-shot runs")
    parser.add_argument("--document-config-key", default="", help="v2 document task config key")
    parser.add_argument("--document-task-type", default="", help="v2 document task type for config set")
    parser.add_argument("--document-fields", default="", help="comma-separated v2 business fields for config set")
    parser.add_argument("--document-description", default="", help="v2 document task config description")
    parser.add_argument("--document-sheet-mode", default="", help="v2 sheet selector mode")
    parser.add_argument("--document-sheet-id", default="", help="v2 fixed/fallback sheet id")
    parser.add_argument("--document-sheet-title", default="", help="v2 exact sheet title selector")
    parser.add_argument("--document-sheet-keyword", default="", help="v2 sheet title keyword selector")
    parser.add_argument("--document-sheet-ids", default="", help="comma-separated v2 sheet ids for sheet_group")
    parser.add_argument("--submit-scan-interval-seconds", type=int, default=300, help="v2 trigger scan interval")
    parser.add_argument(
        "--submit-target-date-offset-days",
        type=int,
        default=0,
        help="v2 trigger date_sheet target offset; 0=today, -1=yesterday",
    )
    parser.add_argument("--correction-document-id", type=int, default=0, help="v2 correction document id")
    parser.add_argument("--correction-sheet-id", default="", help="v2 correction sheet id")
    parser.add_argument("--correction-row-index", type=int, default=0, help="v2 correction 1-based row index")
    parser.add_argument("--correction-post-url", default="", help="v2 correction post URL selector")
    parser.add_argument("--correction-field", default="", help="v2 correction business field name")
    parser.add_argument("--correction-value", default="", help="v2 correction new value")
    parser.add_argument("--correction-reason", default="", help="v2 correction reason")
    parser.add_argument("--correction-operator", default="cli", help="v2 correction operator name")
    parser.add_argument("--wechat-pages", type=int, default=12, help="WeChat screenshots to capture after first screen")
    parser.add_argument("--wechat-out-dir", default="exports/wechat", help="WeChat capture output directory")
    parser.add_argument("--wechat-serial", default="", help="ADB serial for WeChat capture")
    parser.add_argument("--wechat-limit", type=int, default=0, help="limit WeChat configured groups for a test run")
    parser.add_argument("--wechat-capture-run-id", type=int, default=0, help="specific WeChat capture run id for demand intake")
    parser.add_argument("--wechat-parse-mode", choices=["ocr", "model"], default="ocr", help="WeChat message parser mode")
    parser.add_argument("--wechat-intake-mode", choices=["batch", "incremental"], default="batch", help="WeChat demand intake mode")
    parser.add_argument("--wechat-context-size", type=int, default=30, help="context message count for incremental WeChat demand intake")
    parser.add_argument("--wechat-no-search", action="store_true", help="assume the target WeChat group is already open")
    parser.add_argument("--wechat-skip-navigation", action="store_true", help="capture current WeChat screen without navigation")
    parser.add_argument("--wechat-keep-on-device", action="store_true", help="keep WeChat screenshots on Android device")
    parser.add_argument(
        "--supervise",
        action="store_true",
        help="run scheduler in a parent process that restarts it after crashes",
    )
    args = parser.parse_args()

    if args.supervise:
        return run_supervisor()

    if args.once == "db":
        init_db()
        return 0
    if args.once == "crawler-app-db":
        from apps.finance_crawler.crawler_app.storage.db import init_crawler_app_db

        init_crawler_app_db()
        print(f"crawler_app database initialized: {Config.CRAWLER_APP_DB_NAME}")
        return 0
    if args.once == "ops-platform-db":
        from apps.finance_crawler.crawler_app.storage.ops_platform import init_ops_platform_intake_tables

        init_ops_platform_intake_tables()
        print(f"ops_platform demand intake tables initialized: {Config.OPS_PLATFORM_DB_NAME}")
        return 0
    if args.once in {"wechat-groups-list", "wechat-groups-capture", "wechat-messages-parse", "wechat-demand-intake", "wechat-hourly-sync"}:
        from apps.finance_crawler.crawler_app.storage.db import init_crawler_app_db
        from apps.finance_crawler.crawler_app.storage.ops_platform import init_ops_platform_intake_tables
        from apps.finance_crawler.crawler_app.workflows.wechat_demand_intake import (
            list_wechat_demand_groups,
            run_wechat_demand_intake,
            run_wechat_group_capture,
            run_wechat_hourly_sync,
            run_wechat_messages_parse,
        )

        init_ops_platform_intake_tables()
        init_crawler_app_db()
        if args.once == "wechat-groups-list":
            print(list_wechat_demand_groups())
            return 0
        target_date = _parse_optional_date(args.report_date) or date.today()
        if args.once == "wechat-messages-parse":
            summary = run_wechat_messages_parse(
                target_date=target_date if not args.wechat_capture_run_id else None,
                capture_run_id=args.wechat_capture_run_id,
                limit=args.wechat_limit,
                parse_mode=args.wechat_parse_mode,
            )
            print(json.dumps(summary, ensure_ascii=True, indent=2))
            return 0
        if args.once == "wechat-demand-intake":
            summary = run_wechat_demand_intake(
                target_date=target_date if not args.wechat_capture_run_id else None,
                capture_run_id=args.wechat_capture_run_id,
                limit=args.wechat_limit,
                intake_mode=args.wechat_intake_mode,
                context_size=args.wechat_context_size,
            )
            print(json.dumps(summary, ensure_ascii=True, indent=2))
            return 0
        if args.once == "wechat-hourly-sync":
            summary = run_wechat_hourly_sync(
                target_date=target_date,
                pages=args.wechat_pages,
                out_dir=args.wechat_out_dir,
                serial=args.wechat_serial or None,
                limit=args.wechat_limit,
                parse_mode=args.wechat_parse_mode,
                context_size=args.wechat_context_size,
                no_search=args.wechat_no_search,
                skip_navigation=args.wechat_skip_navigation,
                keep_on_device=args.wechat_keep_on_device,
            )
            print(json.dumps(summary, ensure_ascii=True, indent=2))
            return 0
        summary = run_wechat_group_capture(
            target_date=target_date,
            pages=args.wechat_pages,
            out_dir=args.wechat_out_dir,
            serial=args.wechat_serial or None,
            limit=args.wechat_limit,
            no_search=args.wechat_no_search,
            skip_navigation=args.wechat_skip_navigation,
            keep_on_device=args.wechat_keep_on_device,
        )
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0
    if args.once in {"device-pool-status", "device-pool-refresh"}:
        from apps.finance_crawler.storage.device_pool import device_pool_status, refresh_adb_devices

        init_db()
        load_runtime_config()
        if args.once == "device-pool-refresh":
            devices = refresh_adb_devices()
            print(
                [
                    {
                        "serial": item.serial,
                        "state": item.state,
                        "transport": item.transport,
                        "model": item.model,
                    }
                    for item in devices
                ]
            )
            return 0
        print(device_pool_status())
        return 0
    if args.once == "config":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
            print(f"updated config: {', '.join(sorted(updates))}")
        load_runtime_config()
        print(format_runtime_config_for_cli())
        return 0
    if args.once == "fetch":
        init_db()
        load_runtime_config()
        candidates = safe_run(fetch_and_save, "fetch_docs_once") or []
        print(f"eligible candidates: {len(candidates)}")
        return 0
    if args.once == "check":
        from apps.finance_crawler.jobs.checker import run_check

        init_db()
        load_runtime_config()
        results = safe_run(run_check, "check_once") or []
        print(f"checked records: {len(results)}")
        return 0
    if args.once == "detail":
        init_db()
        load_runtime_config()
        safe_run(run_detail, "detail_crawl_once")
        return 0
    if args.once == "excel-detail":
        init_db()
        load_runtime_config()
        results = safe_run(run_local_excel_detail, "excel_detail_once") or []
        print(f"excel detail rows: {len(results)}")
        return 0
    if args.once == "link-detail":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        result = safe_run(lambda: run_single_link_detail(args.single_link), "single_link_detail_once")
        print(f"single link result: {'yes' if result else 'none'}")
        return 0
    if args.once == "report":
        init_db()
        load_runtime_config()
        print(generate_report(args.report_date or None))
        return 0
    if args.once == "kol-tenpay-external-reads":
        from apps.finance_crawler.crawler_app.storage.db import init_crawler_app_db

        init_db()
        init_crawler_app_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        summary = safe_run(
            lambda: (
                run_kol_tenpay_external_reads(
                    target_date=target_date,
                    target_doc_url=args.tencent_doc_url or None,
                )
                if target_date
                else run_kol_tenpay_external_reads_lookback(
                    target_doc_url=args.tencent_doc_url or None,
                )
            ),
            "kol_tenpay_external_reads_once",
        ) or {}
        print(f"KOL Tenpay external reads summary: {summary}")
        return 0
    if args.once == "kol-daily-db-pipeline":
        from apps.finance_crawler.crawler_app.storage.db import init_crawler_app_db

        init_db()
        init_crawler_app_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        summary = safe_run(
            lambda: run_kol_daily_db_pipeline(target_date=target_date or date.today()),
            "kol_daily_db_pipeline_once",
        ) or {}
        print(f"KOL daily DB pipeline summary: {summary}")
        return 0
    if args.once == "article-sync":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        count = safe_run(sync_article_sources_from_tencent_docs, "article_sync_once") or 0
        print(f"article sources synced: {count}")
        return 0
    if args.once == "article-crawl":
        init_db()
        load_runtime_config()
        rows = safe_run(crawl_pending_article_details, "article_crawl_once") or []
        print(f"article details crawled: {len(rows)}")
        return 0
    if args.once == "article-writeback":
        init_db()
        load_runtime_config()
        count = safe_run(writeback_article_details, "article_writeback_once") or 0
        print(f"article details written: {count}")
        return 0
    if args.once == "article-details":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        summary = safe_run(run_article_details, "article_details_once") or {}
        print(f"article details summary: {summary}")
        return 0
    if args.once == "doc-link-reads":
        init_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        summary = safe_run(
            lambda: run_docs_link_reads(doc_url=args.tencent_doc_url or None, target_date=target_date),
            "doc_link_reads_once",
        ) or {}
        print(f"doc link reads summary: {summary}")
        return 0
    if args.once == "doc-columns-check":
        init_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        print(_format_doc_columns_check(args.tencent_doc_url or None))
        return 0
    if args.once == "v2-read-count-submit":
        from apps.finance_crawler.crawler_app.workflows.read_count import submit_read_count_tasks

        init_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        print(
            submit_read_count_tasks(
                doc_url=args.tencent_doc_url or None,
                target_date=target_date,
            )
        )
        return 0
    if args.once == "v2-read-count-crawl":
        from apps.finance_crawler.crawler_app.workflows.read_count import crawl_pending_read_count_tasks

        init_db()
        load_runtime_config()
        print(crawl_pending_read_count_tasks())
        return 0
    if args.once == "v2-read-count-writeback":
        from apps.finance_crawler.crawler_app.workflows.read_count import writeback_read_count_results

        init_db()
        load_runtime_config()
        print(writeback_read_count_results())
        return 0
    if args.once == "v2-read-count":
        from apps.finance_crawler.crawler_app.workflows.read_count import run_read_count_workflow

        init_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        print(
            run_read_count_workflow(
                doc_url=args.tencent_doc_url or None,
                target_date=target_date,
            )
        )
        return 0
    if args.once in {
        "v2-initial-check-submit",
        "v2-initial-check-crawl",
        "v2-initial-check-writeback",
        "v2-initial-check",
        "v2-detail-submit",
        "v2-detail-crawl",
        "v2-detail-writeback",
        "v2-detail",
    }:
        from apps.finance_crawler.crawler_app.tasks.types import DETAIL, INITIAL_CHECK
        from apps.finance_crawler.crawler_app.workflows.document_tasks import (
            crawl_pending_document_tasks,
            run_document_task_workflow,
            submit_document_tasks,
            writeback_document_task_results,
        )

        init_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        task_type = INITIAL_CHECK if "initial-check" in args.once else DETAIL
        if args.once.endswith("-submit"):
            print(
                submit_document_tasks(
                    task_type,
                    doc_url=args.tencent_doc_url or None,
                    target_date=target_date,
                )
            )
        elif args.once.endswith("-crawl"):
            print(crawl_pending_document_tasks(task_type))
        elif args.once.endswith("-writeback"):
            print(writeback_document_task_results(task_type))
        else:
            print(
                run_document_task_workflow(
                    task_type,
                    doc_url=args.tencent_doc_url or None,
                    target_date=target_date,
                )
            )
        return 0
    if args.once in {
        "v2-doc-config-set",
        "v2-doc-config-check",
        "v2-doc-config-list",
        "v2-doc-config-submit",
        "v2-doc-config-run",
    }:
        from apps.finance_crawler.crawler_app.workflows.document_tasks import (
            build_sheet_selector,
            check_document_task_config,
            list_document_task_configs,
            parse_field_names,
            run_configured_document_task_workflow,
            submit_configured_document_tasks,
            upsert_document_task_config,
        )

        init_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        if args.once == "v2-doc-config-list":
            print(list_document_task_configs())
            return 0
        if not args.document_config_key:
            raise ValueError("--document-config-key is required")
        if args.once == "v2-doc-config-check":
            print(check_document_task_config(args.document_config_key))
            return 0
        if args.once == "v2-doc-config-set":
            if not args.tencent_doc_url:
                raise ValueError("--tencent-doc-url is required for v2-doc-config-set")
            if not args.document_task_type:
                raise ValueError("--document-task-type is required for v2-doc-config-set")
            print(
                upsert_document_task_config(
                    config_key=args.document_config_key,
                    doc_url=args.tencent_doc_url,
                    task_type=args.document_task_type,
                    field_names=parse_field_names(args.document_fields),
                    sheet_selector=build_sheet_selector(
                        mode=args.document_sheet_mode or None,
                        sheet_id=args.document_sheet_id or None,
                        sheet_title=args.document_sheet_title or None,
                        sheet_keyword=args.document_sheet_keyword or None,
                        sheet_ids=args.document_sheet_ids or None,
                    ),
                    description=args.document_description or None,
                )
            )
            return 0
        if args.once == "v2-doc-config-submit":
            print(
                submit_configured_document_tasks(
                    args.document_config_key,
                    target_date=target_date,
                )
            )
            return 0
        print(
            run_configured_document_task_workflow(
                args.document_config_key,
                target_date=target_date,
            )
        )
        return 0
    if args.once in {
        "v2-trigger-set",
        "v2-trigger-bind",
        "v2-trigger-list",
        "v2-trigger-submit",
        "v2-submit-worker-once",
    }:
        from apps.finance_crawler.crawler_app.workflows.document_tasks import (
            build_sheet_selector,
            parse_field_names,
        )
        from apps.finance_crawler.crawler_app.workflows.submit_triggers import (
            list_document_triggers,
            submit_document_trigger_config,
            submit_due_document_triggers,
            upsert_document_trigger,
            upsert_document_trigger_binding,
        )

        init_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        if args.once == "v2-trigger-list":
            print(list_document_triggers(include_disabled=True))
            return 0
        if args.once == "v2-submit-worker-once":
            print(submit_due_document_triggers())
            return 0
        if not args.document_config_key:
            raise ValueError("--document-config-key is required")
        if args.once == "v2-trigger-set":
            if not args.tencent_doc_url:
                raise ValueError("--tencent-doc-url is required for v2-trigger-set")
            print(
                upsert_document_trigger(
                    config_key=args.document_config_key,
                    doc_url=args.tencent_doc_url,
                    sheet_selector=build_sheet_selector(
                        mode=args.document_sheet_mode or None,
                        sheet_id=args.document_sheet_id or None,
                        sheet_title=args.document_sheet_title or None,
                        sheet_keyword=args.document_sheet_keyword or None,
                        sheet_ids=args.document_sheet_ids or None,
                    ),
                    submit_policy={"target_date_offset_days": args.submit_target_date_offset_days},
                    scan_interval_seconds=args.submit_scan_interval_seconds,
                    description=args.document_description or None,
                )
            )
            return 0
        if args.once == "v2-trigger-bind":
            if not args.document_task_type:
                raise ValueError("--document-task-type is required for v2-trigger-bind")
            print(
                upsert_document_trigger_binding(
                    config_key=args.document_config_key,
                    task_type=args.document_task_type,
                    field_names=parse_field_names(args.document_fields),
                    description=args.document_description or None,
                )
            )
            return 0
        print(
            submit_document_trigger_config(
                args.document_config_key,
                target_date=target_date,
                trigger_type="manual",
            )
        )
        return 0
    if args.once == "v2-crawl-worker-once":
        init_db()
        load_runtime_config()
        print(run_v2_crawl_workers())
        return 0
    if args.once == "v2-writeback-worker-once":
        init_db()
        load_runtime_config()
        print(run_v2_writeback_worker())
        return 0
    if args.once in {
        "v2-correction-plan",
        "v2-correction-writeback",
        "v2-correction-apply",
    }:
        from apps.finance_crawler.crawler_app.workflows.corrections import (
            apply_pending_correction_writebacks,
            plan_and_apply_configured_document_correction,
            plan_and_apply_document_correction,
            plan_configured_document_correction,
            plan_document_correction,
        )

        init_db()
        load_runtime_config()
        if args.once == "v2-correction-writeback":
            print(apply_pending_correction_writebacks())
            return 0
        _require_correction_args(args)
        target_date = _parse_optional_date(args.report_date)
        if args.document_config_key:
            row_index = args.correction_row_index or None
            if args.once == "v2-correction-plan":
                print(
                    plan_configured_document_correction(
                        config_key=args.document_config_key,
                        target_date=target_date,
                        row_index=row_index,
                        post_url=args.correction_post_url or None,
                        field_name=args.correction_field,
                        new_value=args.correction_value,
                        reason=args.correction_reason,
                        operator_name=args.correction_operator,
                    )
                )
                return 0
            print(
                plan_and_apply_configured_document_correction(
                    config_key=args.document_config_key,
                    target_date=target_date,
                    row_index=row_index,
                    post_url=args.correction_post_url or None,
                    field_name=args.correction_field,
                    new_value=args.correction_value,
                    reason=args.correction_reason,
                    operator_name=args.correction_operator,
                )
            )
            return 0
        if args.once == "v2-correction-plan":
            print(
                plan_document_correction(
                    document_id=args.correction_document_id,
                    sheet_id=args.correction_sheet_id,
                    row_index=args.correction_row_index,
                    field_name=args.correction_field,
                    new_value=args.correction_value,
                    reason=args.correction_reason,
                    operator_name=args.correction_operator,
                )
            )
            return 0
        print(
            plan_and_apply_document_correction(
                document_id=args.correction_document_id,
                sheet_id=args.correction_sheet_id,
                row_index=args.correction_row_index,
                field_name=args.correction_field,
                new_value=args.correction_value,
                reason=args.correction_reason,
                operator_name=args.correction_operator,
            )
        )
        return 0

    if args.once in {
        "kol-settlement-metrics-submit",
        "kol-settlement-metrics-crawl",
        "kol-settlement-metrics-writeback",
        "kol-settlement-metrics",
    }:
        from apps.finance_crawler.crawler_app.storage.db import init_crawler_app_db
        from apps.finance_crawler.crawler_app.workflows.kol_settlement_post_metrics import (
            crawl_kol_settlement_post_metric_tasks,
            run_kol_settlement_post_metrics,
            submit_kol_settlement_post_metric_tasks,
            writeback_kol_settlement_post_metric_results,
        )

        init_db()
        init_crawler_app_db()
        updates = _config_updates_from_args(args, include_tencent_doc_url=False)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date)
        limit = args.limit if args.limit > 0 else None
        if args.once.endswith("-submit"):
            print(submit_kol_settlement_post_metric_tasks(target_date=target_date, limit=limit))
        elif args.once.endswith("-crawl"):
            print(crawl_kol_settlement_post_metric_tasks(limit=limit))
        elif args.once.endswith("-writeback"):
            print(writeback_kol_settlement_post_metric_results(limit=limit))
        else:
            print(run_kol_settlement_post_metrics(target_date=target_date, limit=limit))
        return 0

    if args.once == "capture-file-server":
        from apps.finance_crawler.crawler_app.web.capture_files import run_capture_file_server

        run_capture_file_server()
        return 0

    try:
        run_forever()
    except KeyboardInterrupt:
        logger.info("scheduler stopped")
        return 0


def _config_updates_from_args(args: argparse.Namespace, *, include_tencent_doc_url: bool = True) -> dict[str, str]:
    updates: dict[str, str] = {}
    for item in args.config_set or []:
        if "=" not in item:
            raise ValueError(f"--config-set expects KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        updates[key.strip()] = value.strip()
    if include_tencent_doc_url and args.tencent_doc_url:
        updates["TENCENT_DOC_URL"] = args.tencent_doc_url
    if args.excel_input_path:
        updates["EXCEL_DETAIL_INPUT_PATH"] = args.excel_input_path
    if args.single_link:
        updates["SINGLE_TEST_LINK"] = args.single_link
    return updates


def _parse_optional_date(value: str) -> date | None:
    if not value:
        return None
    cleaned = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return date.fromisoformat(cleaned)
    if re.fullmatch(r"\d{4}", cleaned):
        today = date.today()
        return date(today.year, int(cleaned[:2]), int(cleaned[2:]))
    raise ValueError(f"invalid date: {value}; expected YYYY-MM-DD or MMDD")


def _require_correction_args(args: argparse.Namespace) -> None:
    missing = []
    if args.document_config_key:
        if not args.correction_row_index and not args.correction_post_url:
            missing.append("--correction-row-index or --correction-post-url")
    else:
        if not args.correction_document_id:
            missing.append("--correction-document-id")
        if not args.correction_sheet_id:
            missing.append("--correction-sheet-id")
        if not args.correction_row_index:
            missing.append("--correction-row-index")
    if not args.correction_field:
        missing.append("--correction-field")
    if args.correction_value is None:
        missing.append("--correction-value")
    if not args.correction_reason:
        missing.append("--correction-reason")
    if missing:
        raise ValueError("missing correction arguments: " + ", ".join(missing))


def _format_doc_columns_check(doc_url: str | None = None) -> str:
    doc = tencent_docs_client.parse_doc_url(doc_url) if doc_url else tencent_docs_client.configured_doc()
    sheet_title = tencent_docs_client.fetch_sheet_title(doc)
    groups = [
        (
            "main",
            tencent_docs_columns.MAIN_COLUMN_ALIASES,
            tencent_docs_columns.default_main_fallbacks(),
        ),
        (
            "doc-link-reads",
            tencent_docs_columns.DOC_LINK_READS_ALIASES,
            tencent_docs_columns.default_doc_link_read_fallbacks(),
        ),
        (
            "article-details",
            tencent_docs_columns.ARTICLE_DETAIL_ALIASES,
            _article_detail_column_fallbacks(),
        ),
    ]
    lines = [
        f"Tencent Docs column check: file={doc.file_id} sheet={doc.sheet_id} title={sheet_title or ''}",
    ]
    rows, start_row = tencent_docs_columns.fetch_header_rows(doc, use_cache=False)
    for group_name, aliases, fallbacks in groups:
        lines.append("")
        lines.append(f"[{group_name}]")
        try:
            resolutions = list(
                tencent_docs_columns.resolve_columns_info(
                    rows,
                    start_row,
                    aliases,
                    fallbacks,
                ).values()
            )
        except Exception as exc:
            lines.append(f"  ERROR: {exc}")
            continue
        for item in resolutions:
            title = item.title or "<empty>"
            if item.source == "title":
                marker = "OK"
            elif item.match_type == "unrecognized_fallback":
                marker = "UNSAFE_FALLBACK"
            elif item.match_type == "ambiguous":
                marker = "AMBIGUOUS_FALLBACK"
            else:
                marker = "FALLBACK"
            matches = "" if not item.matches else f" matches={list(item.matches)}"
            lines.append(
                f"  {item.field}: {marker} col={item.index} title={title} "
                f"fallback={item.fallback} match={item.match_type}{matches}"
            )
    return "\n".join(lines)


def _article_detail_column_fallbacks() -> dict[str, int]:
    return {
        "date": 0,
        "ip": 1,
        "product": 2,
        "url": 8,
        "title": 9,
        "screenshot": 10,
        "read_count": 11,
        "comment_count": 12,
        "like_count": 13,
    }


if __name__ == "__main__":
    raise SystemExit(main())
