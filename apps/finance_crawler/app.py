"""Command-line entrypoint, scheduler, and supervisor."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from datetime import date
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
from apps.finance_crawler.workflows.local_excel_detail import run_local_excel_detail
from apps.finance_crawler.workflows.profile_metrics import (
    crawl_pending_profile_metrics,
    create_daily_profile_metric_tasks,
    ensure_daily_profile_metric_rows,
    run_profile_metrics,
    sync_profile_sources_from_tencent_docs,
    writeback_profile_metrics,
)
from apps.finance_crawler.workflows.profile_post_reads import crawl_profile_post_reads
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


def _reload_runtime_config_for_task(task_name: str) -> None:
    if task_name == "heartbeat":
        return
    load_runtime_config()


def _register_jobs() -> None:
    schedule.every(Config.FETCH_INTERVAL_MINUTES).minutes.do(
        safe_run, fetch_and_save, "fetch_docs"
    )
    logger.info("registered fetch every %s minutes", Config.FETCH_INTERVAL_MINUTES)

    if Config.ENABLE_CHECKER:
        from apps.finance_crawler.jobs.checker import run_check

        schedule.every(Config.CHECK_INTERVAL_MINUTES).minutes.do(
            safe_run, run_check, "check"
        )
        logger.info("registered check every %s minutes", Config.CHECK_INTERVAL_MINUTES)

    if Config.DETAIL_INTERVAL_MINUTES > 0:
        schedule.every(Config.DETAIL_INTERVAL_MINUTES).minutes.do(
            safe_run, run_detail, "detail_crawl_due"
        )
        logger.info(
            "registered detail due-task scan every %s minutes; task due time is scheduled_at <= now",
            Config.DETAIL_INTERVAL_MINUTES,
        )

    schedule.every().day.at(Config.REPORT_TIME).do(
        safe_run, generate_report, "report"
    )
    logger.info("registered report daily at %s", Config.REPORT_TIME)

    if Config.PROFILE_METRICS_DOC_URL and Config.PROFILE_METRICS_INTERVAL_MINUTES > 0:
        if Config.PROFILE_METRICS_DAILY_PREPARE_TIME:
            schedule.every().day.at(Config.PROFILE_METRICS_DAILY_PREPARE_TIME).do(
                safe_run, ensure_daily_profile_metric_rows, "profile_daily_rows"
            )
            logger.info(
                "registered profile daily row prepare at %s",
                Config.PROFILE_METRICS_DAILY_PREPARE_TIME,
            )
        schedule.every(Config.PROFILE_METRICS_INTERVAL_MINUTES).minutes.do(
            safe_run, run_profile_metrics, "profile_metrics"
        )
        logger.info(
            "registered profile metrics every %s minutes",
            Config.PROFILE_METRICS_INTERVAL_MINUTES,
        )

    if Config.HEARTBEAT_INTERVAL_MINUTES > 0:
        schedule.every(Config.HEARTBEAT_INTERVAL_MINUTES).minutes.do(
            safe_run, heartbeat, "heartbeat"
        )
        logger.info("registered heartbeat every %s minutes", Config.HEARTBEAT_INTERVAL_MINUTES)


def run_forever() -> None:
    logger.info("Finance crawler scheduler starting")
    init_db()
    load_runtime_config()
    _register_jobs()

    logger.info("sync Tencent Docs once on startup")
    safe_run(fetch_and_save, "fetch_docs_init")
    if Config.DETAIL_INTERVAL_MINUTES > 0:
        logger.info("scan due detail tasks once on startup")
        safe_run(run_detail, "detail_crawl_due_init")
    if Config.PROFILE_METRICS_DOC_URL and Config.PROFILE_METRICS_INTERVAL_MINUTES > 0:
        if Config.PROFILE_METRICS_DAILY_PREPARE_TIME:
            logger.info("ensure profile daily rows once on startup")
            safe_run(ensure_daily_profile_metric_rows, "profile_daily_rows_init")
        logger.info("run profile metrics once on startup")
        safe_run(run_profile_metrics, "profile_metrics_init")

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
            "config",
            "fetch",
            "check",
            "detail",
            "excel-detail",
            "link-detail",
            "report",
            "profile-sync",
            "profile-daily-rows",
            "profile-create-tasks",
            "profile-crawl",
            "profile-writeback",
            "profile-metrics",
            "profile-post-reads",
            "article-sync",
            "article-crawl",
            "article-writeback",
            "article-details",
            "doc-link-reads",
            "doc-columns-check",
        ],
        help="run one task and exit",
    )
    parser.add_argument("--config-set", action="append", default=[], help="runtime config KEY=VALUE")
    parser.add_argument("--tencent-doc-url", default="", help="set runtime Tencent Docs URL")
    parser.add_argument("--excel-input-path", default="", help="set runtime Excel detail input path")
    parser.add_argument("--single-link", default="", help="set one-shot detail test link")
    parser.add_argument("--report-date", default="", help="report date in YYYY-MM-DD format; defaults to yesterday")
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
    if args.once == "profile-sync":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        count = safe_run(sync_profile_sources_from_tencent_docs, "profile_sync_once") or 0
        print(f"profile sources synced: {count}")
        return 0
    if args.once == "profile-daily-rows":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = _parse_optional_date(args.report_date) or None
        summary = safe_run(
            lambda: ensure_daily_profile_metric_rows(target_date, doc_url=args.tencent_doc_url or None),
            "profile_daily_rows_once",
        ) or {}
        print(f"profile daily rows summary: {summary}")
        return 0
    if args.once == "profile-create-tasks":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = date.fromisoformat(args.report_date) if args.report_date else None
        count = safe_run(lambda: create_daily_profile_metric_tasks(target_date), "profile_create_tasks_once") or 0
        print(f"profile daily tasks created: {count}")
        return 0
    if args.once == "profile-crawl":
        init_db()
        load_runtime_config()
        rows = safe_run(crawl_pending_profile_metrics, "profile_crawl_once") or []
        print(f"profile metrics crawled: {len(rows)}")
        return 0
    if args.once == "profile-writeback":
        init_db()
        load_runtime_config()
        count = safe_run(writeback_profile_metrics, "profile_writeback_once") or 0
        print(f"profile metrics written: {count}")
        return 0
    if args.once == "profile-metrics":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        summary = safe_run(run_profile_metrics, "profile_metrics_once") or {}
        print(f"profile metrics summary: {summary}")
        return 0
    if args.once == "profile-post-reads":
        init_db()
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        target_date = date.fromisoformat(args.report_date) if args.report_date else None
        rows = safe_run(lambda: crawl_profile_post_reads(target_date=target_date), "profile_post_reads_once") or []
        print(f"profile post reads crawled: {len(rows)}")
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
        updates = _config_updates_from_args(args)
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
        updates = _config_updates_from_args(args)
        if updates:
            set_runtime_config(updates)
        load_runtime_config()
        print(_format_doc_columns_check(args.tencent_doc_url or None))
        return 0

    try:
        run_forever()
    except KeyboardInterrupt:
        logger.info("scheduler stopped")
        return 0


def _config_updates_from_args(args: argparse.Namespace) -> dict[str, str]:
    updates: dict[str, str] = {}
    for item in args.config_set or []:
        if "=" not in item:
            raise ValueError(f"--config-set expects KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        updates[key.strip()] = value.strip()
    if args.tencent_doc_url:
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
