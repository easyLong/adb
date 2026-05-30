"""Recheck one initial-check link and optionally write the correction back.

Use this when a Tencent Docs row has a wrong initial-check account name, or a
missing page was previously misclassified as a successful account.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.finance_crawler.integrations.tencent_docs import writeback as tencent_docs_writeback  # noqa: E402
from apps.finance_crawler.mobile.crawler import check_record_exists_and_account, open_url, resolve_short_url  # noqa: E402
from apps.finance_crawler.services.runtime_config import load_runtime_config  # noqa: E402
from apps.finance_crawler.storage.crawl_repository import record_crawl_result  # noqa: E402
from apps.finance_crawler.storage.db import get_conn  # noqa: E402
from apps.finance_crawler.storage.framework_db import (  # noqa: E402
    finish_task_execution,
    request_task_rerun,
    start_task_execution,
)


def _json_loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _find_submission(url: str) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, task_type, source_locator_json, app_type, original_url, canonical_url
                FROM crawl_task_submissions
                WHERE task_type = 'initial_check'
                  AND (original_url = %s OR canonical_url = %s)
                ORDER BY id DESC
                LIMIT 1
                """,
                (url, url),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def _record_for_submission(submission: dict[str, Any], locator: dict[str, Any]) -> dict[str, Any]:
    submission_id = int(submission["id"])
    return {
        "record_id": -submission_id,
        "task_id": submission_id,
        "submission_id": submission_id,
        "url": submission["original_url"],
        "source_app": submission.get("app_type"),
        "doc_row_index": locator.get("row_index"),
        "doc_file_id": locator.get("file_id"),
        "doc_sheet_id": locator.get("sheet_id"),
        "source_locator": locator,
    }


def _write_tencent_docs(locator: dict[str, Any], result: dict[str, Any]) -> tuple[str, str | None]:
    if result["status"] not in {"success", "not_found"}:
        return "skipped", result.get("error") or "technical error skipped writeback"

    row_index = locator.get("row_index")
    file_id = locator.get("file_id")
    sheet_id = locator.get("sheet_id")
    if not row_index or not file_id or not sheet_id:
        return "skipped", "missing Tencent Docs locator"

    tencent_docs_writeback.write_initial_check_results(
        [
            {
                "file_id": file_id,
                "sheet_id": sheet_id,
                "row_index": row_index,
                "exists": result["status"] == "success",
                "account_name": result.get("account_name"),
            }
        ]
    )
    return "success", None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recheck and repair one initial-check link.")
    parser.add_argument("url", help="Original source URL, usually a Tencent Docs row URL.")
    parser.add_argument("--dry-run", action="store_true", help="Only crawl and print the result; do not write DB/docs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_runtime_config()
    submission = _find_submission(args.url)
    if not submission:
        print(json.dumps({"status": "error", "error": "initial_check submission not found"}, ensure_ascii=False))
        return 2

    locator = _json_loads(submission.get("source_locator_json"))
    opened_url = resolve_short_url(args.url)
    open_url(opened_url)
    result = check_record_exists_and_account(int(submission["id"]))

    payload = {
        "submission_id": submission["id"],
        "url": args.url,
        "opened_url": opened_url,
        "locator": locator,
        "result": result,
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if result["status"] in {"success", "not_found"} else 1

    request_task_rerun(int(submission["id"]), scheduled_at=datetime.now())
    execution_id = start_task_execution(int(submission["id"]), worker_id="repair_initial_check_link")

    writeback_status, writeback_error = _write_tencent_docs(locator, result)
    record = _record_for_submission(submission, locator)
    metrics = {"exists": result.get("exists"), "row_index": locator.get("row_index")}
    record_crawl_result(
        record=record,
        workflow="initial_check",
        status=result["status"],
        account_name=result.get("account_name"),
        metrics=metrics,
        error=result.get("error"),
    )
    finish_task_execution(
        execution_id,
        status=result["status"],
        account_name=result.get("account_name"),
        metrics=metrics,
        result=result,
        writeback_status=writeback_status,
        writeback_locator={
            key: locator.get(key)
            for key in ("file_id", "sheet_id", "row_index")
            if locator.get(key) is not None
        },
        writeback_error=writeback_error,
        error=result.get("error"),
    )

    payload["writeback_status"] = writeback_status
    payload["writeback_error"] = writeback_error
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] in {"success", "not_found"} and writeback_status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
