"""Replay Tencent Docs writebacks from stored crawl results.

This script does not open the phone or scrape again. It only reads failed
Tencent Docs sink writebacks from MySQL, rebuilds the writeback payload from
stored crawl_results, and sends it to Tencent Docs again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _load_env_file(path_text: str) -> None:
    if not path_text:
        return
    path = Path(path_text)
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        ps_match = re.match(r'\$env:(MYSQL_[A-Z0-9_]+)\s*=\s*"([^"]*)"', line)
        if ps_match:
            os.environ[ps_match.group(1)] = ps_match.group(2)
            continue

        dotenv_match = re.match(r"(MYSQL_[A-Z0-9_]+)\s*=\s*(.*)", line)
        if dotenv_match:
            value = dotenv_match.group(2).strip().strip('"').strip("'")
            os.environ[dotenv_match.group(1)] = value


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


def _row_index(item: dict[str, Any]) -> int | None:
    locator = _json_loads(item.get("sink_locator_json"))
    metrics = _json_loads(item.get("metrics_json"))
    value = locator.get("row_index") or metrics.get("row_index") or metrics.get("doc_row_index")
    return int(value) if value else None


def _query_failed_writebacks(limit: int, error_like: str) -> list[dict[str, Any]]:
    from apps.finance_crawler.storage.db import get_conn

    limit_clause = "LIMIT %s" if limit > 0 else ""
    params: list[Any] = [f"%{error_like}%"]
    if limit > 0:
        params.append(limit)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    w.id AS writeback_id,
                    w.task_id,
                    w.result_id,
                    w.sink_locator_json,
                    r.workflow,
                    r.status AS result_status,
                    r.account_name,
                    r.content,
                    r.metrics_json,
                    r.screenshot_path,
                    r.error AS result_error
                FROM crawl_writebacks w
                JOIN crawl_results r ON r.id = w.result_id
                WHERE w.sink_type = 'tencent_docs'
                  AND w.status = 'error'
                  AND w.error LIKE %s
                ORDER BY w.id
                {limit_clause}
                """,
                params,
            )
            return list(cursor.fetchall())
    finally:
        conn.close()


def _mark_replayed(items: list[dict[str, Any]]) -> None:
    from apps.finance_crawler.storage.db import get_conn
    from apps.finance_crawler.storage.framework_db import update_task_execution_writeback

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                UPDATE crawl_writebacks
                SET status = 'success',
                    error = NULL,
                    written_at = NOW()
                WHERE id = %s
                """,
                [(item["writeback_id"],) for item in items],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for item in items:
        row_index = _row_index(item)
        result_id = item.get("result_id")
        if result_id and row_index:
            update_task_execution_writeback(
                int(result_id),
                writeback_status="success",
                writeback_locator={"row_index": row_index},
                writeback_error=None,
            )


def _to_writeback_row(item: dict[str, Any]) -> dict[str, Any] | None:
    from apps.finance_crawler.config import Config
    from apps.finance_crawler.services.remarks import detail_remark

    row_index = _row_index(item)
    if not row_index:
        return None

    metrics = _json_loads(item.get("metrics_json"))
    status = str(item.get("result_status") or "error")
    result = {
        "status": status,
        "error": item.get("result_error"),
    }
    return {
        "file_id": Config.QQ_FILE_ID,
        "sheet_id": Config.QQ_SHEET_ID,
        "row_index": row_index,
        "read_count": "N" if status in {"deleted", "not_found"} else int(metrics.get("read_count") or 0),
        "comment_count": int(metrics.get("comment_count") or 0),
        "detail_status": status,
        "detail_remark": detail_remark(result),
        "screenshot_path": item.get("screenshot_path"),
    }


def _chunk(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), max(size, 1))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay failed Tencent Docs writebacks.")
    parser.add_argument("--env-file", action="append", default=[], help="Load MySQL-only env file before running.")
    parser.add_argument("--error-like", default="Requests Use Up", help="Substring to match crawl_writebacks.error.")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to replay; 0 means all.")
    parser.add_argument("--batch-size", type=int, default=10, help="Rows to replay per script batch.")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be replayed.")
    args = parser.parse_args()

    for env_file in args.env_file:
        _load_env_file(env_file)

    from apps.finance_crawler.config import Config
    from apps.finance_crawler.integrations.tencent_docs import writeback as tencent_docs_writeback
    from apps.finance_crawler.services.runtime_config import load_runtime_config

    load_runtime_config()
    items = _query_failed_writebacks(args.limit, args.error_like)
    rows = []
    replay_items = []
    skipped = 0
    for item in items:
        row = _to_writeback_row(item)
        if row is None:
            skipped += 1
            continue
        rows.append(row)
        replay_items.append(item)

    print(f"matched={len(items)} replayable={len(rows)} skipped={skipped}")
    if args.dry_run:
        for row in rows[:10]:
            print(row)
        return 0

    missing = [
        name
        for name, value in {
            "TENCENT_DOC_CLIENT_ID": Config.QQ_CLIENT_ID,
            "TENCENT_DOC_OPEN_ID": Config.QQ_OPEN_ID,
            "TENCENT_DOC_ACCESS_TOKEN or TENCENT_DOC_CLIENT_SECRET": Config.QQ_ACCESS_TOKEN or Config.QQ_CLIENT_SECRET,
            "TENCENT_DOC_FILE_ID": Config.QQ_FILE_ID,
            "TENCENT_DOC_SHEET_ID": Config.QQ_SHEET_ID,
        }.items()
        if not value
    ]
    if missing:
        print("missing config: " + ", ".join(missing), file=sys.stderr)
        return 2

    for row_batch, item_batch in zip(_chunk(rows, args.batch_size), _chunk(replay_items, args.batch_size), strict=True):
        tencent_docs_writeback.write_detail_rows(row_batch)
        _mark_replayed(item_batch)
        print(f"replayed={len(item_batch)} last_writeback_id={item_batch[-1]['writeback_id']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
