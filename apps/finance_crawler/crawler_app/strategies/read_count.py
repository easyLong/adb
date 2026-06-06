"""Read-count crawl strategy."""

from __future__ import annotations

from typing import Any

from apps.finance_crawler.crawler_app.capture.planner import resolve_capture_plan_for_task
from apps.finance_crawler.crawler_app.documents.fields import READ_COUNT, REMARK
from apps.finance_crawler.crawler_app.tasks.types import READ_COUNT as READ_COUNT_TASK
from apps.finance_crawler.mobile.read_count_crawler import ReadCountTarget, crawl_read_count_target


def crawl_read_count_task(submission: dict[str, Any]) -> dict[str, Any]:
    app_type = str(submission.get("app_type") or "unknown")
    target = ReadCountTarget(
        row_index=int(submission["row_index"]),
        link=str(submission["post_url"]),
        title="",
        account_name=str(submission.get("account_name") or ""),
        existing_read="",
        output_prefix="v2_read_count",
        capture_plan=resolve_capture_plan_for_task(
            task_type=READ_COUNT_TASK,
            app_type=app_type,
            fields=(READ_COUNT,),
            profile=submission.get("capture_action_profile"),
        ),
    )
    return crawl_read_count_target(target)


def read_count_metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {READ_COUNT: result.get("read_count")}


def read_count_writeback_values(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") == "success":
        return {READ_COUNT: result.get("read_count"), REMARK: "成功"}
    reason = result.get("not_found_reason") or result.get("error") or result.get("status") or "failed"
    if str(result.get("status") or "") == "not_found":
        return {
            READ_COUNT: "N",
            REMARK: reason,
        }
    if str(result.get("final_submission_status") or "") != "failed":
        return {}
    return {
        READ_COUNT: "N",
        REMARK: f"失败：{reason}",
    }
