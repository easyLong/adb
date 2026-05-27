"""Open one finance-community link on the phone and print crawl results."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.finance_crawler.mobile.crawler import (  # noqa: E402
    check_record_exists_and_account,
    open_url,
    resolve_short_url,
    scrape_record_content,
)
from apps.finance_crawler.utils.link_source import resolve_source_app  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test one Alipay/Ant Fortune/Tenpay link through the phone crawler."
    )
    parser.add_argument("url", help="URL or app deep link.")
    parser.add_argument(
        "--record-id",
        dest="record_id",
        type=int,
        default=0,
        help="Capture id used in local output folders. Defaults to a timestamp-based id.",
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="Skip existence/account check and only run detail scrape.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    record_id = args.record_id or int(datetime.now().strftime("%m%d%H%M%S"))
    source_app = resolve_source_app(None, args.url)
    started = time.perf_counter()

    deep_link = args.url
    check_result: dict[str, Any] | None = None
    scrape_result: dict[str, Any] | None = None
    error: str | None = None
    try:
        deep_link = resolve_short_url(args.url)
        open_url(deep_link)
        if not args.skip_check:
            check_result = check_record_exists_and_account(record_id)
        scrape_result = scrape_record_content(record_id, source_app=source_app)
    except Exception as exc:
        error = str(exc)

    payload = {
        "record_id": record_id,
        "source_app": source_app,
        "url": args.url,
        "opened_url": deep_link,
        "check": check_result,
        "scrape": scrape_result,
        "error": error,
        "duration": round(time.perf_counter() - started, 2),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if scrape_result and scrape_result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
