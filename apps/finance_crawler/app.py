"""Command-line entrypoint, scheduler, and supervisor."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from typing import Any

import schedule

from apps.finance_crawler.config import Config
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
from apps.finance_crawler.workflows.local_excel_detail import run_local_excel_detail
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

    schedule.every().day.at(Config.DETAIL_TIME).do(safe_run, run_detail, "detail_crawl")
    logger.info("registered detail crawl daily at %s", Config.DETAIL_TIME)

    schedule.every().day.at(Config.REPORT_TIME).do(
        safe_run, generate_report, "report"
    )
    logger.info("registered report daily at %s", Config.REPORT_TIME)

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
        choices=["db", "config", "fetch", "check", "detail", "excel-detail", "link-detail", "report"],
        help="run one task and exit",
    )
    parser.add_argument("--config-set", action="append", default=[], help="runtime config KEY=VALUE")
    parser.add_argument("--tencent-doc-url", default="", help="set runtime Tencent Docs URL")
    parser.add_argument("--excel-input-path", default="", help="set runtime Excel detail input path")
    parser.add_argument("--single-link", default="", help="set one-shot detail test link")
    parser.add_argument("--report-date", default="", help="report date in YYYY-MM-DD format")
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


if __name__ == "__main__":
    raise SystemExit(main())
