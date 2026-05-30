"""Generate summary reports from MySQL."""

from __future__ import annotations

from datetime import date, datetime

from apps.finance_crawler.config import Config
from apps.finance_crawler.domain.task_types import DETAIL_CRAWL_TASK_TYPE, INITIAL_CHECK_TASK_TYPE
from apps.finance_crawler.storage.db import get_conn, log_task
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("report")


def generate_report(target_date: date | str | None = None) -> str:
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)

    return _generate_framework_report(target_date)


def _generate_framework_report(target_date: date) -> str:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions
                WHERE task_type = %s
                  AND DATE(source_time) = %s
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date),
            )
            total = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions s
                JOIN crawl_task_executions e ON e.id = s.latest_execution_id
                WHERE s.task_type = %s
                  AND DATE(s.source_time) = %s
                  AND e.status = 'success'
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date),
            )
            success = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions s
                JOIN crawl_task_executions e ON e.id = s.latest_execution_id
                WHERE s.task_type = %s
                  AND DATE(s.source_time) = %s
                  AND e.status IN ('deleted', 'error')
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date),
            )
            failed = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions i
                WHERE i.task_type = %s
                  AND DATE(i.source_time) = %s
                  AND i.status = 'not_found'
                """,
                (INITIAL_CHECK_TASK_TYPE, target_date),
            )
            initial_check_failed = cursor.fetchone()["cnt"]

            read_expr = "CAST(JSON_UNQUOTE(JSON_EXTRACT(e.metrics_json, '$.read_count')) AS UNSIGNED)"
            cursor.execute(
                f"""
                SELECT COUNT(*) AS cnt
                FROM crawl_task_submissions s
                JOIN crawl_task_executions e ON e.id = s.latest_execution_id
                WHERE s.task_type = %s
                  AND DATE(s.source_time) = %s
                  AND e.status = 'success'
                  AND {read_expr} > %s
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date, Config.READ_COUNT_THRESHOLD),
            )
            over_threshold = cursor.fetchone()["cnt"]

            cursor.execute(
                f"""
                SELECT {read_expr} AS read_count
                FROM crawl_task_submissions s
                JOIN crawl_task_executions e ON e.id = s.latest_execution_id
                WHERE s.task_type = %s
                  AND DATE(s.source_time) = %s
                  AND e.status = 'success'
                ORDER BY read_count DESC
                LIMIT %s
                """,
                (DETAIL_CRAWL_TASK_TYPE, target_date, Config.REPORT_TOP_N),
            )
            top_rows = cursor.fetchall()
            top_str = "/".join(str(row["read_count"]) for row in top_rows if row["read_count"] is not None) or "暂无"

        report = (
            f"{target_date.month}月{target_date.day}日预发帖{total}条，"
            f"初检失败{initial_check_failed}条，采集失败{failed}条，成功发帖{success}条，"
            f"阅读量超过{Config.READ_COUNT_THRESHOLD}的有{over_threshold}条，"
            f"阅读数前三数据为{top_str}"
        )
        _save_report_file(target_date, report)
        logger.info("framework report generated: %s", report)
        log_task("report", "success", report)
        return report
    except Exception as exc:
        logger.exception("framework report generation failed")
        log_task("report", "error", str(exc))
        raise
    finally:
        conn.close()


def _save_report_file(target_date: date, report: str) -> None:
    path = Config.REPORT_DIR / f"{target_date}.txt"
    path.write_text(
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{report}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    print(generate_report())
