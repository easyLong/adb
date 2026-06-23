"""Post-like initial-check and detail crawl strategies for crawler_app v2."""

from __future__ import annotations

import time
from typing import Any

from apps.finance_crawler.crawler_app.capture.post_fields import (
    build_post_capture_bundle,
    extract_post_field_results,
    writeback_values_from_field_results,
)
from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.capture.planner import resolve_capture_plan_for_task
from apps.finance_crawler.crawler_app.documents.fields import (
    ACCOUNT_NAME,
    CHECK_RESULT,
    COMMENT_COUNT,
    READ_COUNT,
    REMARK,
    SCREENSHOT,
)
from apps.finance_crawler.crawler_app.tasks.types import DETAIL, INITIAL_CHECK
from apps.finance_crawler.mobile.crawler import (
    check_record_exists_and_account,
    is_transient_open_failure,
    open_url,
    resolve_short_url,
    scrape_record_content,
)
from apps.finance_crawler.mobile.device_session import restart_app_for_url
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("crawler_app_post_strategy")


def crawl_initial_check_task(submission: dict[str, Any]) -> dict[str, Any]:
    app_type = str(submission.get("app_type") or "unknown")
    fields = _requested_fields(submission, default=(ACCOUNT_NAME,))
    opened_url = resolve_short_url(str(submission["post_url"]))
    result = _open_and_check_with_app_recovery(
        opened_url=opened_url,
        record_id=int(submission["id"]),
        source_app=app_type,
    )
    result.update(
        {
            "row_index": int(submission["row_index"]),
            "opened_url": opened_url,
            "capture_plan": resolve_capture_plan_for_task(
                task_type=INITIAL_CHECK,
                app_type=app_type,
                fields=fields,
                profile=submission.get("capture_action_profile"),
            ).to_json_dict(),
        }
    )
    return _with_post_field_results(result, task_type=INITIAL_CHECK, app_type=app_type, fields=fields)


def initial_check_metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "exists": result.get("exists"),
        ACCOUNT_NAME: result.get("account_name"),
        "app_restart_attempts": result.get("app_restart_attempts"),
    }


def initial_check_writeback_values(result: dict[str, Any]) -> dict[str, Any]:
    field_values = writeback_values_from_field_results(result)
    if field_values:
        return field_values
    status = str(result.get("status") or "")
    if status == "success":
        return {
            ACCOUNT_NAME: result.get("account_name") or "",
            CHECK_RESULT: "Y",
            REMARK: "成功",
        }
    if status == "not_found":
        reason = result.get("error") or "not_found"
        return {
            ACCOUNT_NAME: "N",
            CHECK_RESULT: "N",
            REMARK: reason,
        }
    if _is_final_failure(result):
        return {REMARK: _failure_remark(result)}
    return {}


def crawl_detail_task(submission: dict[str, Any]) -> dict[str, Any]:
    app_type = str(submission.get("app_type") or "unknown")
    fields = _requested_fields(submission, default=(ACCOUNT_NAME, READ_COUNT, COMMENT_COUNT, SCREENSHOT))
    opened_url = resolve_short_url(str(submission["post_url"]))
    capture_plan = resolve_capture_plan_for_task(
        task_type=DETAIL,
        app_type=app_type,
        fields=fields,
        profile=submission.get("capture_action_profile"),
    )
    result = _open_and_scrape_with_app_recovery(
        opened_url=opened_url,
        record_id=int(submission["id"]),
        source_app=app_type,
        capture_plan=capture_plan,
    )
    result.update(
        {
            "row_index": int(submission["row_index"]),
            "opened_url": opened_url,
            "capture_plan": capture_plan.to_json_dict(),
        }
    )
    return _with_post_field_results(result, task_type=DETAIL, app_type=app_type, fields=fields)


def _with_post_field_results(
    result: dict[str, Any],
    *,
    task_type: str,
    app_type: str,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    result_fields = _fields_with_runtime_outputs(task_type, fields)
    bundle = build_post_capture_bundle(
        task_type=task_type,
        app_type=app_type,
        requested_fields=result_fields,
        result=result,
    )
    field_results = extract_post_field_results(bundle)
    result["capture_bundle"] = bundle.to_json_dict()
    result["field_results"] = [item.to_json_dict() for item in field_results]
    return result


def detail_metrics(result: dict[str, Any]) -> dict[str, Any]:
    metrics = {
        ACCOUNT_NAME: result.get("account_name"),
        READ_COUNT: result.get("read_count"),
        COMMENT_COUNT: result.get("comment_count"),
        "capture_pages": result.get("capture_pages"),
        "ocr_attempted": result.get("ocr_attempted"),
    }
    metrics.update(result.get("app_metrics") or {})
    return metrics


def detail_writeback_values(result: dict[str, Any]) -> dict[str, Any]:
    field_values = writeback_values_from_field_results(result)
    if field_values:
        return field_values
    status = str(result.get("status") or "")
    if status == "success":
        values: dict[str, Any] = {
            READ_COUNT: result.get("read_count") or 0,
            COMMENT_COUNT: result.get("comment_count") or 0,
            REMARK: "成功",
        }
        if result.get("account_name"):
            values[ACCOUNT_NAME] = result.get("account_name")
        if result.get("screenshot_path"):
            values[SCREENSHOT] = result.get("screenshot_path")
        return values
    if status in {"not_found", "deleted"}:
        return {
            ACCOUNT_NAME: "N",
            READ_COUNT: "N",
            REMARK: result.get("error") or status,
        }
    if _is_final_failure(result):
        return {REMARK: _failure_remark(result)}
    return {}


def _requested_fields(submission: dict[str, Any], *, default: tuple[str, ...]) -> tuple[str, ...]:
    requested = submission.get("source_locator", {}).get("requested_fields")
    if not requested:
        return default
    fields = tuple(str(item) for item in requested if str(item))
    return fields or default


def _fields_with_runtime_outputs(task_type: str, fields: tuple[str, ...]) -> tuple[str, ...]:
    output_fields = list(dict.fromkeys(fields))
    if task_type == INITIAL_CHECK:
        for field_name in (CHECK_RESULT, REMARK):
            if field_name not in output_fields:
                output_fields.append(field_name)
    else:
        if REMARK not in output_fields:
            output_fields.append(REMARK)
    return tuple(output_fields)


def _open_and_check_with_app_recovery(
    *,
    opened_url: str,
    record_id: int,
    source_app: str,
) -> dict[str, Any]:
    attempts = max(1, Config.APP_OPEN_RECOVERY_RETRIES + 1)
    result: dict[str, Any] = {}
    restarts = 0

    for attempt in range(1, attempts + 1):
        open_url(opened_url)
        result = check_record_exists_and_account(record_id)
        if not is_transient_open_failure(result) or attempt >= attempts:
            if restarts:
                result["app_restart_attempts"] = restarts
            return result

        logger.warning(
            "transient initial-check page failure id=%s attempt=%s/%s error=%s; restarting app",
            record_id,
            attempt,
            attempts,
            result.get("error"),
        )
        if restart_app_for_url(opened_url, source_app=source_app):
            restarts += 1
        else:
            time.sleep(Config.APP_RESTART_WAIT)

    return result


def _open_and_scrape_with_app_recovery(
    *,
    opened_url: str,
    record_id: int,
    source_app: str,
    capture_plan,
) -> dict[str, Any]:
    attempts = max(1, Config.DETAIL_BLANK_REOPEN_RETRIES + 1)
    result: dict[str, Any] = {}
    restarts = 0

    for attempt in range(1, attempts + 1):
        open_url(opened_url)
        result = scrape_record_content(record_id, source_app=source_app, capture_plan=capture_plan)
        should_recover = _is_blank_target_result(result) or is_transient_open_failure(result)
        if not should_recover or attempt >= attempts:
            if attempt > 1 or restarts:
                metrics = dict(result.get("app_metrics") or {})
                metrics["blank_reopen_attempts"] = max(0, attempt - 1)
                metrics["app_restart_attempts"] = restarts
                result["app_metrics"] = metrics
            return result

        logger.warning(
            "transient detail page failure id=%s attempt=%s/%s error=%s; restarting app and reopening link",
            record_id,
            attempt,
            attempts,
            result.get("error"),
        )
        if restart_app_for_url(opened_url, source_app=source_app):
            restarts += 1
        time.sleep(Config.DETAIL_BLANK_REOPEN_WAIT)

    return result


def _is_blank_target_result(result: dict[str, Any]) -> bool:
    if result.get("status") != "error":
        return False
    return "post content was not detected" in str(result.get("error") or "")


def _is_final_failure(result: dict[str, Any]) -> bool:
    return str(result.get("final_submission_status") or "") == "failed"


def _failure_remark(result: dict[str, Any]) -> str:
    reason = result.get("error") or result.get("status") or "failed"
    return f"失败：{reason}"
