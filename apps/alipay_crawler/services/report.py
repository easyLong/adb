"""Generate summary reports from MySQL."""

from __future__ import annotations

from datetime import date, datetime

from apps.alipay_crawler.config import Config
from apps.alipay_crawler.storage.db import get_conn, log_task
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("report")


def generate_report(target_date=None) -> str:
    if target_date is None:
        target_date = date.today()

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM posts WHERE DATE(post_time) = %s",
                (target_date,),
            )
            total = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM posts
                WHERE DATE(post_time) = %s
                  AND batch_status = 'success'
                """,
                (target_date,),
            )
            success = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM posts
                WHERE DATE(post_time) = %s
                  AND batch_status IN ('deleted', 'error')
                """,
                (target_date,),
            )
            failed = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM posts
                WHERE DATE(post_time) = %s
                  AND batch_status = 'success'
                  AND read_count > %s
                """,
                (target_date, Config.READ_COUNT_THRESHOLD),
            )
            over_threshold = cursor.fetchone()["cnt"]

            cursor.execute(
                """
                SELECT read_count
                FROM posts
                WHERE DATE(post_time) = %s
                  AND batch_status = 'success'
                ORDER BY read_count DESC
                LIMIT %s
                """,
                (target_date, Config.REPORT_TOP_N),
            )
            top_rows = cursor.fetchall()
            top_str = "/".join(str(row["read_count"]) for row in top_rows) or "暂无"

        report = (
            f"{target_date.month}月{target_date.day}日预发帖{total}条，"
            f"成功发帖{success}条，失败{failed}条，"
            f"阅读量超过{Config.READ_COUNT_THRESHOLD}的有{over_threshold}条，"
            f"阅读数前{Config.REPORT_TOP_N}数据为{top_str}"
        )
        _save_report_file(target_date, report)
        logger.info("报告生成: %s", report)
        log_task("report", "success", report)
        return report
    except Exception as exc:
        logger.exception("报告生成失败")
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
