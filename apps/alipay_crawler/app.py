"""Command-line entrypoint and scheduler."""

from __future__ import annotations

import argparse
import time
import traceback
from collections.abc import Callable
from typing import Any

import schedule

from apps.alipay_crawler.config import Config
from apps.alipay_crawler.integrations.qq_docs import fetch_and_save
from apps.alipay_crawler.jobs.batch import run_batch
from apps.alipay_crawler.services.report import generate_report
from apps.alipay_crawler.storage.db import init_db, log_task
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("scheduler")


def safe_run(func: Callable[[], Any], task_name: str) -> Any:
    logger.info("================== task start: %s ==================", task_name)
    start = time.time()
    try:
        result = func()
        logger.info("任务完成: %s, %.1fs", task_name, time.time() - start)
        return result
    except Exception as exc:
        duration = time.time() - start
        logger.error("任务异常: %s, %s", task_name, exc)
        logger.error(traceback.format_exc())
        log_task(task_name, "error", str(exc), duration)
        return None


def _register_jobs() -> None:
    schedule.every(Config.FETCH_INTERVAL_MINUTES).minutes.do(
        safe_run, fetch_and_save, "fetch_docs"
    )
    logger.info("已注册：每 %s 分钟同步腾讯文档", Config.FETCH_INTERVAL_MINUTES)

    if Config.ENABLE_CHECKER:
        from apps.alipay_crawler.jobs.checker import run_check

        schedule.every(Config.CHECK_INTERVAL_MINUTES).minutes.do(
            safe_run, run_check, "check"
        )
        logger.info("已注册：每 %s 分钟初检帖子", Config.CHECK_INTERVAL_MINUTES)

    schedule.every().day.at(Config.BATCH_TIME).do(safe_run, run_batch, "batch")
    logger.info("已注册：每天 %s 批量抓取，limit=%s", Config.BATCH_TIME, Config.BATCH_LIMIT)

    schedule.every().day.at(Config.REPORT_TIME).do(
        safe_run, generate_report, "report"
    )
    logger.info("已注册：每天 %s 生成报告", Config.REPORT_TIME)


def run_forever() -> None:
    logger.info("支付宝采集调度服务启动")
    init_db()
    _register_jobs()

    logger.info("启动时先同步一次腾讯文档")
    safe_run(fetch_and_save, "fetch_docs_init")

    logger.info("调度器运行中，Ctrl+C 停止")
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("调度器已停止")


def main() -> int:
    parser = argparse.ArgumentParser(description="Alipay crawler scheduler")
    parser.add_argument(
        "--once",
        choices=["db", "fetch", "check", "batch", "report"],
        help="只执行一个任务后退出，便于测试",
    )
    args = parser.parse_args()

    if args.once == "db":
        init_db()
        return 0
    if args.once == "fetch":
        init_db()
        candidates = safe_run(fetch_and_save, "fetch_docs_once") or []
        print(f"eligible candidates: {len(candidates)}")
        return 0
    if args.once == "check":
        from apps.alipay_crawler.jobs.checker import run_check

        init_db()
        results = safe_run(run_check, "check_once") or []
        print(f"checked posts: {len(results)}")
        return 0
    if args.once == "batch":
        init_db()
        safe_run(run_batch, "batch_once")
        return 0
    if args.once == "report":
        init_db()
        print(generate_report())
        return 0

    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
