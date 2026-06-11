"""Homepage profile metric workflow.

The Tencent Docs sheet is treated as an adapter: rows are imported as dated
metric requests, the crawler stores observations in MySQL, and writeback only
updates the configured sheet cell.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.capture.core import (
    ActionTemplate,
    CaptureBundle,
    EvidenceValidation,
    FieldExtraction,
    FieldExtractionResult,
    PageSnapshot,
    PageState,
)
from apps.finance_crawler.crawler_app.errors import DEVICE_UNAVAILABLE, classify_crawl_error
from apps.finance_crawler.integrations.tencent_docs import client
from apps.finance_crawler.integrations.tencent_docs.write_requests import cell_request, row_cells_request
from apps.finance_crawler.crawler_app.writeback.locator import load_sheet_context, locate_by_date_url
from apps.finance_crawler.mobile.capture_engine import (
    capture_pages,
    collect_ui_records,
    open_app_link,
    run_adb,
    save_screenshot,
    try_ocr,
)
from apps.finance_crawler.mobile.capture_records import read_capture_records as _read_capture_records
from apps.finance_crawler.mobile.device_session import device as session_device
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.mobile.parsers import extract_profile_fans_count, parse_count_token
from apps.finance_crawler.storage.device_pool import release_device_lease, start_device_lease
from apps.finance_crawler.storage.db import log_task
from apps.finance_crawler.crawler_app.storage.profile_metrics import (
    create_daily_profile_metric_sources,
    get_pending_profile_metric_sources,
    get_pending_profile_writebacks,
    mark_profile_writeback,
    profile_key_for_url,
    profile_summary,
    record_profile_metric,
    upsert_profile_source,
)
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.link_source import detect_link_source
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("profile_metrics")

SOURCE_TYPE = "tencent_docs"
FANS_COL_INDEX = 4
DUPLICATE_ROW_MARKER = "\u91cd\u590d"
SUPPORTED_PROFILE_PLATFORMS = {
    "\u8682\u8681",
    "alipay",
    "Alipay",
    "\u7406\u8d22\u901a",
    "\u817e\u8baf\u7406\u8d22\u901a",
    "tenpay",
    "Tenpay",
    "TENPAY",
    "tencentwm",
}


def create_daily_profile_metric_tasks(metric_date: date | None = None) -> int:
    resolved_date = metric_date or _configured_metric_date() or date.today()
    count = create_daily_profile_metric_sources(resolved_date)
    logger.info("daily profile metric tasks created date=%s count=%s", resolved_date, count)
    return count


def ensure_daily_profile_metric_rows(
    metric_date: date | None = None,
    *,
    doc_url: str | None = None,
    template_range: str | None = None,
) -> dict[str, Any]:
    """Append missing profile rows for a date from the configured template range."""

    resolved_date = metric_date or date.today()
    doc = _profile_doc(doc_url)
    template_rows, _ = client.fetch_grid(template_range or Config.PROFILE_METRICS_TEMPLATE_RANGE, doc=doc)
    existing_rows, existing_start = client.fetch_grid(Config.PROFILE_METRICS_READ_RANGE, doc=doc)

    existing_links = {
        _normalize_homepage_url(_cell(row, 3))
        for row in existing_rows
        if _parse_date(_cell(row, 0)) == resolved_date and _normalize_homepage_url(_cell(row, 3))
    }
    template_items = [_daily_template_values(row, resolved_date) for row in template_rows]
    template_items = [item for item in template_items if item and _normalize_homepage_url(str(item[3])) not in existing_links]
    if not template_items:
        summary = {
            "date": resolved_date.isoformat(),
            "template_rows": len(template_rows),
            "written": 0,
            "row_start": None,
            "row_end": None,
        }
        logger.info("profile daily rows already complete: %s", summary)
        return summary

    next_row = _next_append_row(existing_rows, existing_start)
    requests = [
        row_cells_request(next_row + offset, 0, values, doc=doc)
        for offset, values in enumerate(template_items)
    ]
    client.post_batch_update(requests, "profile_daily_rows", doc=doc)
    summary = {
        "date": resolved_date.isoformat(),
        "template_rows": len(template_rows),
        "written": len(template_items),
        "row_start": next_row,
        "row_end": next_row + len(template_items) - 1,
    }
    logger.info("profile daily rows prepared: %s", summary)
    return summary


def run_profile_metrics() -> dict[str, Any]:
    started = time.time()
    summary: dict[str, Any] = {}
    try:
        imported = sync_profile_sources_from_tencent_docs()
        crawled = crawl_pending_profile_metrics()
        written = writeback_profile_metrics() if Config.PROFILE_METRICS_WRITEBACK_ENABLED else 0
        summary = {
            "imported": imported,
            "crawled": len(crawled),
            "written": written,
            **profile_summary(),
        }
        log_task("profile_metrics", "success", json.dumps(summary, ensure_ascii=False), time.time() - started)
        return summary
    except Exception as exc:
        log_task("profile_metrics", "error", str(exc), time.time() - started)
        raise


def sync_profile_sources_from_tencent_docs(doc_url: str | None = None) -> int:
    doc = _profile_doc(doc_url)
    rows, start_row = client.fetch_grid(Config.PROFILE_METRICS_READ_RANGE, doc=doc)
    imported = 0
    for offset, row in enumerate(rows):
        sheet_row_index = start_row + offset + 1
        if sheet_row_index == 1:
            continue
        parsed = _parse_profile_source_row(row, sheet_row_index=sheet_row_index, doc=doc)
        if not parsed:
            continue
        target_id, source_id = upsert_profile_source(parsed)
        fans = parsed.get("existing_fans_count")
        if fans is not None:
            metric_id = record_profile_metric(
                target_id=target_id,
                metric_date=parsed["metric_date"],
                app_type=parsed["app_type"],
                homepage_url=parsed["homepage_url"],
                status="success",
                fans_count=fans,
                metrics={"source": "tencent_docs_existing"},
                crawled_at=datetime.now(),
            )
            mark_profile_writeback(
                metric_source_id=source_id,
                metric_id=metric_id,
                locator=parsed["source_locator"],
                status="success",
            )
        imported += 1
    logger.info("profile sources synced from Tencent Docs: imported=%s", imported)
    return imported


def crawl_pending_profile_metrics(
    limit: int | None = None,
    *,
    target_date: date | None = None,
    source_name: str | None = None,
) -> list[dict[str, Any]]:
    resolved_limit = limit if limit is not None else Config.PROFILE_METRICS_CRAWL_LIMIT
    metric_date = target_date or _configured_metric_date()
    records = get_pending_profile_metric_sources(
        limit=resolved_limit or None,
        metric_date=metric_date,
        source_name=source_name,
    )
    if not records:
        logger.info("profile metric crawl skipped: no pending records")
        return []

    results: list[dict[str, Any]] = []
    consecutive_device_errors = 0
    max_device_errors = _max_consecutive_device_errors()
    for index, record in enumerate(records, start=1):
        logger.info(
            "profile metric crawl %s/%s row=%s account=%s",
            index,
            len(records),
            record.get("source_locator", {}).get("row_index"),
            record.get("account_name"),
        )
        result: dict[str, Any] = {}
        lease = start_device_lease(
            app_type=str(record.get("app_type") or "unknown"),
            task_scope="profile:daily_metrics",
            task_id=record.get("metric_source_id") or record.get("target_id") or index,
            worker_id="profile_metrics",
        )
        try:
            result = _crawl_profile(record)
        except Exception as exc:
            result = {
                "target_id": record.get("target_id"),
                "account_name": record.get("account_name"),
                "metric_date": record.get("metric_date"),
                "status": "error",
                "fans_count": None,
                "error": str(exc),
                "error_type": classify_crawl_error(exc).kind,
            }
        finally:
            success = result.get("status") == "success"
            release_device_lease(
                lease,
                status="success" if success else "failed",
                error=None if success else str(result.get("error") or "profile metric crawl failed"),
                error_type=None if success else str(result.get("error_type") or ""),
            )
        results.append(result)
        if _is_device_unavailable_error(result.get("error")):
            consecutive_device_errors += 1
            if consecutive_device_errors >= max_device_errors:
                reset_device_session()
                raise DeviceUnavailable(
                    "profile metric crawl stopped after %s consecutive device errors: %s"
                    % (consecutive_device_errors, result.get("error"))
                )
        else:
            consecutive_device_errors = 0
    return results


def writeback_profile_metrics(limit: int | None = None) -> int:
    rows = get_pending_profile_writebacks(limit=limit)
    if not rows:
        logger.info("profile metric writeback skipped: no pending rows")
        return 0

    requests_by_doc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    successes_by_doc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    failures: list[tuple[dict[str, Any], str]] = []
    contexts_by_doc: dict[tuple[str, str], Any] = {}
    success_count = 0
    for row in rows:
        locator = row.get("source_locator") or {}
        try:
            doc = client.DocInfo(file_id=str(locator["file_id"]), sheet_id=str(locator["sheet_id"]))
            key = (doc.file_id, doc.sheet_id)
            context = contexts_by_doc.get(key)
            if context is None:
                context = load_sheet_context(doc, Config.PROFILE_METRICS_READ_RANGE)
                contexts_by_doc[key] = context
            metric_date = row.get("metric_date")
            if not isinstance(metric_date, date):
                parsed_date = _parse_date(str(metric_date or ""))
                if parsed_date is None:
                    raise RuntimeError(f"invalid metric_date for profile writeback: {metric_date}")
                metric_date = parsed_date
            located = locate_by_date_url(
                context,
                target_date=metric_date,
                url=str(row.get("homepage_url") or ""),
                date_col_index=int(locator.get("date_col_index", 0)),
                url_col_index=int(locator.get("url_col_index", 3)),
            )
            if not located.matched:
                raise RuntimeError(located.error or "profile writeback row was not located")
            fans_col = int(locator.get("fans_col_index", FANS_COL_INDEX))
            requests_by_doc.setdefault(key, []).append(
                row_cells_request(
                    int(located.primary_row),
                    fans_col,
                    [row["fans_count"], "" if row.get("growth_count") is None else row["growth_count"]],
                    doc=doc,
                )
            )
            for duplicate_row_index in located.duplicate_rows:
                requests_by_doc[key].append(
                    row_cells_request(
                        int(duplicate_row_index),
                        fans_col,
                        [DUPLICATE_ROW_MARKER, DUPLICATE_ROW_MARKER],
                        doc=doc,
                    )
                )
            success_row = dict(row)
            success_locator = dict(locator)
            success_locator["resolved_row_index"] = int(located.primary_row)
            success_locator["duplicate_row_indexes"] = [int(item) for item in located.duplicate_rows]
            success_row["source_locator"] = success_locator
            successes_by_doc.setdefault(key, []).append(success_row)
        except Exception as exc:
            failures.append((row, str(exc)))

    for (file_id, sheet_id), requests in requests_by_doc.items():
        doc_successes = successes_by_doc.get((file_id, sheet_id), [])
        try:
            client.post_batch_update(
                requests,
                "profile_metric_writeback",
                doc=client.DocInfo(file_id=file_id, sheet_id=sheet_id),
            )
            for row in doc_successes:
                mark_profile_writeback(
                    metric_source_id=int(row["metric_source_id"]),
                    metric_id=int(row["metric_id"]) if row.get("metric_id") is not None else None,
                    locator=row.get("source_locator") or {},
                    status="success",
                )
            success_count += len(doc_successes)
        except Exception as exc:
            for row in doc_successes:
                failures.append((row, str(exc)))
    for row, error in failures:
        mark_profile_writeback(
            metric_source_id=int(row["metric_source_id"]),
            metric_id=int(row["metric_id"]) if row.get("metric_id") is not None else None,
            locator=row.get("source_locator") or {},
            status="error",
            error=error,
        )
    logger.info("profile metric writeback finished: success=%s failed=%s", success_count, len(failures))
    return success_count


def _crawl_profile(record: dict[str, Any]) -> dict[str, Any]:
    url = str(record["homepage_url"])
    output_dir = Config.CAPTURE_DIR / ("profile_%s_%s" % (record["target_id"], datetime.now().strftime("%Y%m%d_%H%M%S")))
    started = time.perf_counter()
    try:
        known_error = _known_unavailable_profile_url(url)
        if known_error:
            metric_id = record_profile_metric(
                target_id=int(record["target_id"]),
                metric_date=record["metric_date"],
                app_type=str(record.get("app_type") or "unknown"),
                homepage_url=url,
                status="blocked",
                fans_count=None,
                metrics={"workflow": "profile_metrics", "duration": 0},
                error=known_error,
            )
            return {
                "metric_id": metric_id,
                "target_id": record["target_id"],
                "account_name": record.get("account_name"),
                "metric_date": record["metric_date"],
                "status": "blocked",
                "fans_count": None,
                "error": known_error,
            }
        app_type = str(record.get("app_type") or "unknown")
        action_run = ProfileFansActionRunner(app_type).run(url, output_dir)
        fans_result = _resolve_profile_fans_count(
            action_run.records,
            screenshot_path=action_run.screenshot_path,
            output_dir=action_run.output_dir,
            app_type=app_type,
            expected_account_name=str(record.get("account_name") or ""),
        )
        fans_count = fans_result.get("fans_count")
        texts = [str(item.get("text") or "").strip() for item in action_run.records if str(item.get("text") or "").strip()]
        blocked_error = None if fans_count is not None else _blocked_profile_page_error(texts)
        # Runtime blocked pages are device/app-session conditions and should remain retryable on
        # another device. Known terminal URLs are handled above as "blocked".
        status = "success" if fans_count is not None else "error"
        error = None if fans_count is not None else (blocked_error or fans_result.get("quality_error") or "profile fans count was not detected")
        error_type = None if error is None else classify_crawl_error(
            error,
            status=status,
            page_state=str(fans_result.get("page_state") or ""),
        ).kind
        metric_id = record_profile_metric(
            target_id=int(record["target_id"]),
            metric_date=record["metric_date"],
            app_type=str(record.get("app_type") or "unknown"),
            homepage_url=url,
            status=status,
            fans_count=fans_count,
            metrics={
                "workflow": "profile_metrics",
                "duration": round(time.perf_counter() - started, 3),
                "capture_pages": action_run.capture_pages_count,
                "fans": fans_result,
                "capture_bundle": fans_result.get("capture_bundle"),
                "field_results": fans_result.get("field_results") or [],
                "error_type": error_type,
            },
            screenshot_path=action_run.screenshot_path,
            error=error,
        )
        return {
            "metric_id": metric_id,
            "target_id": record["target_id"],
            "account_name": record.get("account_name"),
            "metric_date": record["metric_date"],
            "status": status,
            "fans_count": fans_count,
            "error": error,
            "error_type": error_type,
        }
    except Exception as exc:
        error_type = classify_crawl_error(exc).kind
        record_profile_metric(
            target_id=int(record["target_id"]),
            metric_date=record["metric_date"],
            app_type=str(record.get("app_type") or "unknown"),
            homepage_url=url,
            status="error",
            fans_count=None,
            metrics={"workflow": "profile_metrics", "duration": round(time.perf_counter() - started, 3), "error_type": error_type},
            error=str(exc),
        )
        logger.warning("profile metric crawl failed target=%s url=%s: %s", record.get("target_id"), url, exc)
        return {
            "target_id": record["target_id"],
            "account_name": record.get("account_name"),
            "metric_date": record["metric_date"],
            "status": "error",
            "fans_count": None,
            "error": str(exc),
            "error_type": error_type,
        }


def _parse_profile_source_row(
    row: list[str],
    *,
    sheet_row_index: int,
    doc: client.DocInfo,
) -> dict[str, Any] | None:
    metric_date = _parse_date(_cell(row, 0))
    account_name = _cell(row, 1)
    platform = _cell(row, 2)
    homepage_url = _cell(row, 3)
    existing_fans_count = _parse_int(_cell(row, 4))
    if not metric_date or not homepage_url or homepage_url == "/":
        return None
    if platform and platform not in SUPPORTED_PROFILE_PLATFORMS:
        return None
    app_type = detect_link_source(homepage_url)
    profile_key = profile_key_for_url(homepage_url)
    locator = {
        "file_id": doc.file_id,
        "sheet_id": doc.sheet_id,
        "row_index": sheet_row_index,
        "fans_col_index": FANS_COL_INDEX,
        "url_col_index": 3,
    }
    return {
        "profile_key": profile_key,
        "account_name": account_name,
        "platform": platform,
        "app_type": app_type,
        "homepage_url": homepage_url,
        "metric_date": metric_date,
        "source_type": SOURCE_TYPE,
        "source_name": "profile_metrics",
        "source_key": profile_key_for_url(f"{doc.file_id}:{doc.sheet_id}:{sheet_row_index}:{homepage_url}"),
        "source_locator": locator,
        "requested_fields": ["fans_count"],
        "source": {"doc_url": Config.PROFILE_METRICS_DOC_URL or Config.QQ_DOC_URL},
        "existing_fans_count": existing_fans_count,
    }


def _daily_template_values(row: list[str], metric_date: date) -> list[Any] | None:
    homepage_url = _cell(row, 3)
    if not homepage_url or homepage_url == "/":
        return None
    return [
        profile_sheet_date_value(metric_date),
        _cell(row, 1),
        _cell(row, 2),
        homepage_url,
        "",
        "",
        "",
        _cell(row, 7),
    ]


def _normalize_homepage_url(value: str) -> str:
    return value.strip()


def _next_append_row(rows: list[list[str]], start_row: int) -> int:
    last_used = start_row
    for offset, row in enumerate(rows):
        if any(str(cell or "").strip() for cell in row):
            last_used = start_row + offset + 1
    return max(2, last_used + 1)


def profile_sheet_date_value(metric_date: date) -> str:
    return metric_date.isoformat()


def _open_profile_url(url: str, *, source_app: str | None = None) -> None:
    if "think.klv5qu.com" in url:
        deep_link = _antfortune_deep_link(url)
        if deep_link:
            serial = assert_device_ready()
            run_adb(
                [
                    "shell",
                    "am",
                    "start",
                    "-a",
                    "android.intent.action.VIEW",
                    "-d",
                    shlex.quote(deep_link),
                    "-p",
                    Config.AFWEALTH_PACKAGE,
                ],
                serial=serial,
                timeout=20,
            )
            return
    try:
        serial = assert_device_ready()
        open_app_link(url, serial=serial)
    except RuntimeError as exc:
        if "target app package is not installed" not in str(exc) and "unable to resolve Intent" not in str(exc):
            raise
        serial = assert_device_ready()
        run_adb(
            ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", shlex.quote(url)],
            serial=serial,
            timeout=20,
        )


def _reset_profile_app_state(source_app: Any) -> None:
    app_type = str(source_app or "").lower()
    if app_type == "antfortune":
        package_name = Config.AFWEALTH_PACKAGE
    elif app_type == "tenpay":
        package_name = Config.TENPAY_PACKAGE
    else:
        package_name = Config.ALIPAY_PACKAGE
    try:
        serial = assert_device_ready()
        run_adb(["shell", "am", "force-stop", package_name], serial=serial, timeout=10)
        reset_device_session()
        time.sleep(Config.APP_RESTART_WAIT)
    except Exception as exc:
        if _is_device_unavailable_error(exc):
            raise DeviceUnavailable(str(exc)) from exc
        logger.info("profile app state reset skipped app=%s package=%s error=%s", app_type, package_name, exc)


class ProfileAppAdapter:
    """App-specific open/reset behavior for profile metric targets."""

    def __init__(self, app_type: str) -> None:
        self.app_type = str(app_type or "unknown").lower()

    def reset_state(self) -> None:
        _reset_profile_app_state(self.app_type)

    def open_target(self, target_url: str) -> None:
        _open_profile_url(target_url, source_app=self.app_type)


@dataclass(frozen=True, slots=True)
class ProfileActionRun:
    records: list[dict[str, Any]]
    screenshot_path: str | None
    output_dir: Any
    capture_pages_count: int


class ProfileFansActionRunner:
    """Run profile page actions and return evidence for field extractors."""

    def __init__(self, app_type: str) -> None:
        self.app_type = str(app_type or "unknown")
        self.enable_ocr = self.app_type.lower() == "tenpay"
        self.adapter = ProfileAppAdapter(self.app_type)

    def run(self, url: str, output_dir: Any) -> ProfileActionRun:
        self.adapter.reset_state()
        self.adapter.open_target(url)
        summary = capture_pages(
            session_device(),
            output_dir,
            max_scrolls=0,
            wait_after_open=_profile_home_initial_wait(self.app_type),
            wait_after_scroll=Config.DETAIL_SCROLL_WAIT,
            enable_ocr=self.enable_ocr,
            dynamic_wait=False,
            ready_timeout=8,
            ready_check_interval=0.5,
            serial=None,
        )
        records = _read_capture_records(summary)
        screenshot_path = str(output_dir / "page_000.png") if (output_dir / "page_000.png").exists() else None
        capture_pages_count = 1
        if _profile_home_needs_recapture(records, self.app_type):
            logger.info("profile home not ready app=%s; waiting and recapturing", self.app_type)
            time.sleep(_profile_home_recapture_wait(self.app_type))
            records = _capture_current_records(
                session_device(),
                output_dir,
                "page_001_retry",
                serial=assert_device_ready(),
                enable_ocr=self.enable_ocr,
            )
            retry_screenshot_path = output_dir / "page_001_retry.png"
            if retry_screenshot_path.exists():
                screenshot_path = str(retry_screenshot_path)
            capture_pages_count = 2
        return ProfileActionRun(
            records=records,
            screenshot_path=screenshot_path,
            output_dir=output_dir,
            capture_pages_count=capture_pages_count,
        )


def _resolve_profile_fans_count(
    records: list[dict[str, Any]],
    *,
    screenshot_path: str | None,
    output_dir: Any,
    app_type: str,
    expected_account_name: str = "",
) -> dict[str, Any]:
    """Extract fans count with quality gates to avoid silently accepting approximations."""

    action_template = _profile_fans_action_template(app_type)
    snapshot = PageSnapshot(
        app_type=app_type or "unknown",
        records=records,
        screenshot_path=screenshot_path,
        output_dir=output_dir,
        expected_account_name=expected_account_name,
    )
    page_state = ProfilePageStateDetector().detect(snapshot)
    extraction = ProfileFansCountExtractor().extract(snapshot, page_state, action_template)
    validation = ProfileFansEvidenceValidator().validate(extraction, snapshot, page_state, action_template)
    result: dict[str, Any] = {
        "fans_count": extraction.value if validation.accepted else None,
        "home_fans_count": extraction.evidence.get("home_fans_count"),
        "page_state": extraction.page_state or page_state.name,
        "page_state_confidence": page_state.confidence,
        "source": extraction.source if validation.accepted else None,
        "exact_required": bool(extraction.evidence.get("exact_required")),
        "exact_used": bool(extraction.evidence.get("exact_used")),
        "account_verified": bool(extraction.evidence.get("account_verified")),
        "action_template": action_template.key,
        "actions": list(action_template.actions),
        "quality_error": None if validation.accepted else (validation.reason or extraction.quality_error),
    }
    bundle, field_result = _profile_fans_standard_results(
        snapshot=snapshot,
        action_template=action_template,
        extraction=extraction,
        validation=validation,
        result=result,
    )
    result["capture_bundle"] = bundle.to_json_dict()
    result["field_results"] = [field_result.to_json_dict()]
    return result


def _profile_fans_standard_results(
    *,
    snapshot: PageSnapshot,
    action_template: ActionTemplate,
    extraction: FieldExtraction,
    validation: EvidenceValidation,
    result: dict[str, Any],
) -> tuple[CaptureBundle, FieldExtractionResult]:
    bundle = CaptureBundle(
        task_type=action_template.task_type,
        app_type=action_template.app_type or snapshot.app_type or "unknown",
        requested_fields=action_template.fields,
        action_template_key=action_template.key,
        actions=action_template.actions,
        status="success" if validation.accepted else "error",
        page_state=extraction.page_state or "unknown",
        ui_records=snapshot.records,
        screenshot_path=snapshot.screenshot_path,
        metadata={
            "expected_account_name": snapshot.expected_account_name,
            "output_dir": str(snapshot.output_dir) if snapshot.output_dir else None,
        },
        error=None if validation.accepted else (validation.reason or extraction.quality_error),
    )
    field_result = FieldExtractionResult(
        field_name="fans_count",
        value=result.get("fans_count"),
        source=result.get("source"),
        accepted=validation.accepted,
        page_state=str(result.get("page_state") or extraction.page_state or "unknown"),
        confidence=extraction.confidence if validation.accepted else 0.0,
        evidence=dict(result),
        quality_error=result.get("quality_error"),
    )
    return bundle, field_result


def _profile_fans_action_template(app_type: str) -> ActionTemplate:
    app = str(app_type or "unknown").lower()
    if app == "tenpay":
        return ActionTemplate(
            key="tenpay_profile_daily_metrics_v1:fans_count",
            app_type=app,
            task_type="profile_daily_metrics",
            fields=("fans_count",),
            actions=(
                "reset_app",
                "open_profile",
                "capture_home",
                "ui_controls",
                "ocr",
                "tenpay_counter_layout",
                "open_exact_fans_if_abbreviated",
                "verify_account_anchor",
            ),
            config={
                "exact_if_abbreviated": True,
                "ocr": True,
                "tenpay_counter_layout": True,
                "require_account_anchor": True,
            },
        )
    return ActionTemplate(
        key=f"{app}_profile_daily_metrics_v1:fans_count",
        app_type=app,
        task_type="profile_daily_metrics",
        fields=("fans_count",),
        actions=("open_profile", "capture_home", "ui_controls", "open_exact_fans_if_abbreviated"),
        config={"exact_if_abbreviated": True, "require_account_anchor": True},
    )


class ProfilePageStateDetector:
    def detect(self, snapshot: PageSnapshot) -> PageState:
        records = snapshot.records
        texts = [str(item.get("text") or "").strip() for item in records if str(item.get("text") or "").strip()]
        if _profile_login_required_error(texts):
            return PageState(
                name="login_required",
                confidence=0.95,
                evidence={"reason": _profile_login_required_error(texts), "text_count": len(texts)},
            )
        if _blocked_profile_page_error(texts):
            return PageState(
                name="blocked",
                confidence=0.9,
                evidence={"reason": _blocked_profile_page_error(texts), "text_count": len(texts)},
            )
        if _has_exact_fans_evidence(records):
            return PageState(
                name="fans_detail",
                confidence=0.95,
                evidence={"exact_fans_evidence": True, "account_verified": _has_expected_profile_anchor(records, snapshot.expected_account_name)},
            )
        if _has_profile_home_fans_context(records):
            return PageState(
                name="profile_home",
                confidence=0.9,
                evidence={"home_fans_context": True, "account_verified": _has_expected_profile_anchor(records, snapshot.expected_account_name)},
            )
        if _profile_home_needs_recapture(records, snapshot.app_type):
            return PageState(name="loading", confidence=0.8, evidence={"text_count": len(texts)})
        return PageState(name="unknown", confidence=0.2, evidence={"text_count": len(texts)})


class ProfileFansCountExtractor:
    field_name = "fans_count"

    def extract(
        self,
        snapshot: PageSnapshot,
        page_state: PageState,
        action_template: ActionTemplate,
    ) -> FieldExtraction:
        records = snapshot.records
        output_dir = snapshot.output_dir
        exact_required = _has_abbreviated_fans_count(records)
        has_home_context = page_state.name == "profile_home"
        has_exact_evidence = page_state.name == "fans_detail"
        account_verified = _has_expected_profile_anchor(records, snapshot.expected_account_name)
        homepage_ocr_records: list[dict[str, Any]] = []
        evidence: dict[str, Any] = {
            "exact_required": exact_required,
            "exact_used": False,
            "account_verified": account_verified,
            "home_fans_count": None,
        }

        ui_exact = _extract_exact_fans_count(records)
        if ui_exact is not None and has_exact_evidence and account_verified:
            evidence.update({"exact_used": True})
            return FieldExtraction(
                field_name=self.field_name,
                value=ui_exact,
                source="ui_exact",
                page_state="fans_detail",
                confidence=0.95,
                evidence=evidence,
            )

        if snapshot.screenshot_path and output_dir:
            homepage_ocr_records = try_ocr(output_dir / "page_000.png") or []
            if not homepage_ocr_records and snapshot.screenshot_path:
                homepage_ocr_records = try_ocr(snapshot.screenshot_path) or []
        if homepage_ocr_records:
            (output_dir / "page_000_ocr_records.json").write_text(
                json.dumps(homepage_ocr_records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            ocr_exact = _extract_exact_fans_count(homepage_ocr_records)
            if (
                ocr_exact is not None
                and _has_exact_fans_evidence(homepage_ocr_records)
                and account_verified
            ):
                evidence.update({"exact_used": True})
                return FieldExtraction(
                    field_name=self.field_name,
                    value=ocr_exact,
                    source="ocr_exact",
                    page_state="fans_detail",
                    confidence=0.9,
                    evidence=evidence,
                )

        if has_home_context:
            evidence["home_fans_count"] = _extract_home_profile_fans_count(records)
        else:
            quality_error = (
                "exact fans page is not tied to expected profile"
                if has_exact_evidence and not account_verified
                else "profile fans context was not detected"
            )
            if page_state.name == "login_required":
                quality_error = str(page_state.evidence.get("reason") or "profile login required")
            elif page_state.name == "blocked":
                quality_error = str(page_state.evidence.get("reason") or "profile page is blocked")
            return FieldExtraction(
                field_name=self.field_name,
                value=None,
                source=None,
                page_state=page_state.name,
                confidence=0.0,
                evidence=evidence,
                quality_error=quality_error,
            )

        if exact_required:
            exact_fans = _open_exact_fans_page_if_abbreviated(
                records,
                output_dir=output_dir,
                app_type=action_template.app_type,
            )
            if exact_fans is not None:
                evidence.update({"exact_used": True})
                return FieldExtraction(
                    field_name=self.field_name,
                    value=exact_fans,
                    source="exact_page",
                    page_state="fans_detail",
                    confidence=0.95,
                    evidence=evidence,
                )
            return FieldExtraction(
                field_name=self.field_name,
                value=None,
                source=None,
                page_state=page_state.name,
                confidence=0.0,
                evidence=evidence,
                quality_error="abbreviated fans count requires exact detail page",
            )

        if action_template.config.get("require_account_anchor") and not account_verified:
            return FieldExtraction(
                field_name=self.field_name,
                value=None,
                source=None,
                page_state=page_state.name,
                confidence=0.0,
                evidence=evidence,
                quality_error="profile account anchor did not match expected account",
            )

        if evidence.get("home_fans_count") is not None:
            return FieldExtraction(
                field_name=self.field_name,
                value=evidence["home_fans_count"],
                source="ui_home",
                page_state="profile_home",
                confidence=0.8,
                evidence=evidence,
            )
        return FieldExtraction(
            field_name=self.field_name,
            value=None,
            source=None,
            page_state=page_state.name,
            confidence=0.0,
            evidence=evidence,
            quality_error="profile fans count was not detected",
        )


class ProfileFansEvidenceValidator:
    def validate(
        self,
        extraction: FieldExtraction,
        snapshot: PageSnapshot,
        page_state: PageState,
        action_template: ActionTemplate,
    ) -> EvidenceValidation:
        if extraction.quality_error:
            return EvidenceValidation(False, extraction.quality_error, extraction.evidence)
        if extraction.value is None:
            return EvidenceValidation(False, "profile fans count was not detected", extraction.evidence)
        if extraction.page_state == "fans_detail" and action_template.config.get("require_account_anchor"):
            if not extraction.evidence.get("account_verified"):
                return EvidenceValidation(False, "exact fans page is not tied to expected profile", extraction.evidence)
        if extraction.evidence.get("exact_required") and not extraction.evidence.get("exact_used"):
            return EvidenceValidation(False, "abbreviated fans count requires exact detail page", extraction.evidence)
        return EvidenceValidation(True, None, extraction.evidence)


def _open_exact_fans_page_if_abbreviated(
    records: list[dict[str, Any]],
    *,
    output_dir: Any,
    app_type: str,
) -> int | None:
    """Open the fans detail page when the homepage only shows an abbreviated count."""

    if not _has_abbreviated_fans_count(records):
        return None
    tap_bounds = _fans_tap_bounds(records)
    if not tap_bounds:
        logger.info("exact fans skipped: abbreviated count found but fans tap target was not located")
        return None

    try:
        serial = assert_device_ready()
        device = session_device()
        x = int((int(tap_bounds.get("left", 0)) + int(tap_bounds.get("right", 0))) / 2)
        y = int((int(tap_bounds.get("top", 0)) + int(tap_bounds.get("bottom", 0))) / 2)
        if x <= 0 or y <= 0:
            return None
        logger.info("opening exact fans page app=%s tap=(%s,%s)", app_type, x, y)
        run_adb(["shell", "input", "tap", str(x), str(y)], serial=serial, timeout=10)
        time.sleep(max(Config.PAGE_LOAD_WAIT, 2.5))
        exact_image_path = output_dir / "fans_exact.png"
        exact_records = _capture_current_records(device, output_dir, "fans_exact", serial=serial)
        exact_count = _extract_exact_fans_count(exact_records)
        if exact_count is None:
            ocr_records = try_ocr(exact_image_path) or []
            if ocr_records:
                (output_dir / "fans_exact_ocr_records.json").write_text(
                    json.dumps(ocr_records, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                exact_count = _extract_exact_fans_count(ocr_records)
        if exact_count is not None:
            logger.info("exact fans count detected: %s", exact_count)
            return exact_count
        logger.info("exact fans page opened but exact count was not detected")
    except Exception as exc:
        if _is_device_unavailable_error(exc):
            raise DeviceUnavailable(str(exc)) from exc
        logger.warning("exact fans lookup failed: %s", exc)
    return None


def _capture_current_records(
    device: Any,
    output_dir: Any,
    name: str,
    *,
    serial: str | None = None,
    enable_ocr: bool = False,
) -> list[dict[str, Any]]:
    image_path = output_dir / f"{name}.png"
    xml_path = output_dir / f"{name}.xml"
    records_path = output_dir / f"{name}_records.json"
    save_screenshot(device, image_path, serial=serial)
    xml_text = device.dump_hierarchy(compressed=False, pretty=True)
    xml_path.write_text(xml_text, encoding="utf-8")
    records = collect_ui_records(xml_text, 0)
    for record in records:
        record["source"] = "ui"
    if enable_ocr:
        ocr_records = try_ocr(image_path) or []
        for record in ocr_records:
            record["source"] = "ocr"
        if ocr_records:
            (output_dir / f"{name}_ocr_records.json").write_text(
                json.dumps(ocr_records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        records = [*records, *ocr_records]
    records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def _profile_home_initial_wait(app_type: str) -> float:
    lowered = str(app_type or "").lower()
    if lowered == "tenpay":
        return max(Config.PAGE_LOAD_WAIT, 8.0)
    if lowered == "antfortune":
        return max(Config.PAGE_LOAD_WAIT, 6.0)
    return max(Config.PAGE_LOAD_WAIT, 4.0)


def _profile_home_recapture_wait(app_type: str) -> float:
    lowered = str(app_type or "").lower()
    if lowered == "tenpay":
        return max(Config.PAGE_LOAD_WAIT, 12.0)
    if lowered == "antfortune":
        return max(Config.PAGE_LOAD_WAIT, 8.0)
    return max(Config.PAGE_LOAD_WAIT, 4.0)


def _profile_home_needs_recapture(records: list[dict[str, Any]], app_type: str) -> bool:
    lowered = str(app_type or "").lower()
    if lowered not in {"tenpay", "antfortune"}:
        return False
    if _has_profile_home_fans_context(records) or _has_exact_fans_evidence(records):
        return False
    texts = [str(record.get("text") or "").strip() for record in records if str(record.get("text") or "").strip()]
    if not texts:
        return True
    if lowered == "tenpay":
        return any("\u817e\u8baf\u7406\u8d22\u901a" in text for text in texts) and len(texts) <= 6
    if lowered == "antfortune":
        return len(texts) <= 6
    return False


def _has_abbreviated_fans_count(records: list[dict[str, Any]]) -> bool:
    fans_label = "\u7c89\u4e1d"
    for record in records:
        text = str(record.get("text") or "").strip()
        if not text:
            continue
        compact = text.replace(" ", "")
        if fans_label in compact and any(unit in compact for unit in ("\u4e07", "w", "W")):
            return True
    for value_record, label_record in _fans_counter_pairs(records):
        value_text = str(value_record.get("text") or "").strip()
        label_text = str(label_record.get("text") or "").strip()
        if fans_label in label_text and any(unit in value_text for unit in ("\u4e07", "w", "W")):
            return True
    tenpay_pair = _tenpay_fans_counter_pair(records)
    if tenpay_pair:
        value_text = str(tenpay_pair[0].get("text") or "").strip()
        return any(unit in value_text for unit in ("\u4e07", "w", "W"))
    return False


def _has_profile_fans_context(records: list[dict[str, Any]]) -> bool:
    for record in records:
        text = str(record.get("text") or "").strip()
        if "\u7c89\u4e1d" in text:
            return True
    return bool(_fans_counter_pairs(records) or _tenpay_fans_counter_pair(records))


def _has_profile_home_fans_context(records: list[dict[str, Any]]) -> bool:
    if _fans_counter_pairs(records) or _tenpay_fans_counter_pair(records):
        return True
    for record in records:
        text = re.sub(r"\s+", "", str(record.get("text") or ""))
        if "\u7c89\u4e1d" not in text or "\u4eba" in text:
            continue
        if text == "\u7c89\u4e1d":
            return True
        if re.search(r"\d+(?:\.\d+)?(?:[\u4e07wWkK])?\u7c89\u4e1d", text):
            return True
    return False


def _has_expected_profile_anchor(records: list[dict[str, Any]], expected_account_name: str) -> bool:
    expected = re.sub(r"\s+", "", expected_account_name or "")
    if not expected:
        return True
    for record in records:
        text = re.sub(r"\s+", "", str(record.get("text") or ""))
        if expected and expected in text:
            return True
    return False


def _has_exact_fans_evidence(records: list[dict[str, Any]]) -> bool:
    for record in records:
        text = str(record.get("text") or "").strip().replace(",", "")
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        if "\u7c89\u4e1d" in compact and "\u4eba" in compact and re.search(r"\d{5,}", compact):
            return True
        patterns = [
            r"(?:TA\u7684\u7c89\u4e1d|\u4ed6\u7684\u7c89\u4e1d|\u5979\u7684\u7c89\u4e1d)\(\d{5,}\u4eba\)",
            r"(?:\u7c89\u4e1d\u603b\u6570|\u5168\u90e8\u7c89\u4e1d|\u7c89\u4e1d\u6570)\D*\d{5,}",
        ]
        if any(re.search(pattern, compact) for pattern in patterns):
            return True
    return False


def _fans_tap_bounds(records: list[dict[str, Any]]) -> dict[str, int] | None:
    pairs = _fans_counter_pairs(records)
    if pairs:
        value_bounds = _normalized_bounds(pairs[0][0].get("bounds") or {})
        label_bounds = _normalized_bounds(pairs[0][1].get("bounds") or {})
        return _merge_bounds(value_bounds, label_bounds)
    tenpay_pair = _tenpay_fans_counter_pair(records)
    if tenpay_pair:
        return _merge_bounds(
            _normalized_bounds(tenpay_pair[0].get("bounds") or {}),
            _normalized_bounds(tenpay_pair[1].get("bounds") or {}),
        )
    for record in records:
        text = str(record.get("text") or "")
        bounds = _normalized_bounds(record.get("bounds") or {})
        if "\u7c89\u4e1d" in text and isinstance(bounds, dict):
            return bounds
    return None


def _fans_counter_pairs(records: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    fans_label = "\u7c89\u4e1d"
    numeric: list[tuple[dict[str, Any], dict[str, int]]] = []
    labels: list[tuple[dict[str, Any], dict[str, int]]] = []

    for record in records:
        text = str(record.get("text") or "").strip()
        bounds = _normalized_bounds(record.get("bounds") or {})
        if not text or not isinstance(bounds, dict):
            continue
        if fans_label in text:
            labels.append((record, bounds))
        if _looks_like_counter_text(text):
            numeric.append((record, bounds))

    pairs: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for label_record, label_bounds in labels:
        label_x = (int(label_bounds.get("left", 0)) + int(label_bounds.get("right", 0))) / 2
        label_left = int(label_bounds.get("left", 0))
        label_right = int(label_bounds.get("right", 0))
        label_width = max(label_right - label_left, 1)
        label_top = int(label_bounds.get("top", 0))
        for value_record, value_bounds in numeric:
            value_left = int(value_bounds.get("left", 0))
            value_right = int(value_bounds.get("right", 0))
            value_x = (int(value_bounds.get("left", 0)) + int(value_bounds.get("right", 0))) / 2
            value_bottom = int(value_bounds.get("bottom", 0))
            value_top = int(value_bounds.get("top", 0))
            horizontal_overlap = min(label_right, value_right) - max(label_left, value_left)
            max_center_distance = max(90, int(label_width * 1.15))
            if (
                value_bottom <= label_top + 24
                and (horizontal_overlap > 0 or abs(value_x - label_x) <= max_center_distance)
                and 250 <= value_top <= 1500
            ):
                distance = abs(value_x - label_x) + abs(label_top - value_bottom)
                pairs.append((distance, value_record, label_record))
    pairs.sort(key=lambda item: item[0])
    return [(value, label) for _, value, label in pairs]


def _extract_home_profile_fans_count(records: list[dict[str, Any]]) -> int | None:
    fans_count = extract_profile_fans_count(records)
    if fans_count is not None:
        return fans_count
    tenpay_pair = _tenpay_fans_counter_pair(records)
    if not tenpay_pair:
        return None
    return parse_count_token(str(tenpay_pair[0].get("text") or ""))


def _tenpay_fans_counter_pair(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Infer the middle counter on Tenpay profile pages when OCR misses the fans label."""

    texts = [str(record.get("text") or "").strip() for record in records]
    if not any(
        marker in text
        for text in texts
        for marker in ("\u7406\u8d22\u901a\u793e\u533a", "\u5b9e\u76d8\u603b\u91d1\u989d", "\u6301\u4ed3\u6536\u76ca", "\u52a8\u6001")
    ):
        return None

    candidates: list[tuple[int, int, dict[str, Any], dict[str, int]]] = []
    for record in records:
        text = str(record.get("text") or "").strip()
        bounds = _normalized_bounds(record.get("bounds") or {})
        if not _looks_like_counter_text(text):
            continue
        top = int(bounds.get("top", 0))
        left = int(bounds.get("left", 0))
        if 650 <= top <= 950 and 0 <= left <= 650:
            candidates.append((top, left, record, bounds))
    if len(candidates) < 3:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    best_group: list[tuple[int, int, dict[str, Any], dict[str, int]]] = []
    for index, candidate in enumerate(candidates):
        top = candidate[0]
        group = [item for item in candidates[index:] if abs(item[0] - top) <= 90]
        if len(group) >= 3:
            best_group = sorted(group[:3], key=lambda item: item[1])
            break
    if len(best_group) < 3:
        return None

    value_record = best_group[1][2]
    value_bounds = best_group[1][3]
    label_bounds = {
        "left": int(value_bounds.get("left", 0)),
        "top": int(value_bounds.get("bottom", 0)),
        "right": int(value_bounds.get("right", 0)),
        "bottom": int(value_bounds.get("bottom", 0)) + 90,
    }
    label_record = {"text": "\u7c89\u4e1d", "bounds": label_bounds, "source": "tenpay_layout"}
    return value_record, label_record


def _looks_like_counter_text(text: str) -> bool:
    compact = text.strip().replace(",", "").replace(" ", "")
    if not compact:
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?(?:[\u4e07wWkK])?", compact))


def _normalized_bounds(bounds: dict[str, Any]) -> dict[str, int]:
    left = int(bounds.get("left", 0) or 0)
    top = int(bounds.get("top", 0) or 0)
    right = int(bounds.get("right", left + int(bounds.get("width", 0) or 0)) or 0)
    bottom = int(bounds.get("bottom", top + int(bounds.get("height", 0) or 0)) or 0)
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _merge_bounds(first: dict[str, Any], second: dict[str, Any]) -> dict[str, int]:
    first = _normalized_bounds(first)
    second = _normalized_bounds(second)
    return {
        "left": min(int(first.get("left", 0)), int(second.get("left", 0))),
        "top": min(int(first.get("top", 0)), int(second.get("top", 0))),
        "right": max(int(first.get("right", 0)), int(second.get("right", 0))),
        "bottom": max(int(first.get("bottom", 0)), int(second.get("bottom", 0))),
    }


def _extract_exact_fans_count(records: list[dict[str, Any]]) -> int | None:
    candidates: list[int] = []
    for record in records:
        text = str(record.get("text") or "").strip().replace(",", "")
        if not text:
            continue
        compact = re.sub(r"\s+", "", text)
        if "\u7c89\u4e1d" in compact and "\u4eba" in compact:
            for value in re.findall(r"\d{5,}", compact):
                candidates.append(int(value))
        patterns = [
            r"(?:TA\u7684\u7c89\u4e1d|\u4ed6\u7684\u7c89\u4e1d|\u5979\u7684\u7c89\u4e1d)\((?P<num>\d{5,})\u4eba\)",
            r"(?:\u7c89\u4e1d\u603b\u6570|\u5168\u90e8\u7c89\u4e1d|\u7c89\u4e1d\u6570|\u7c89\u4e1d)\D*(?P<num>\d{5,})",
            r"(?P<num>\d{5,})\D*(?:\u7c89\u4e1d)",
            r"^(?P<num>\d{5,})$",
        ]
        for pattern in patterns:
            match = re.search(pattern, compact)
            if match:
                candidates.append(int(match.group("num")))
    if not candidates:
        if _has_profile_fans_context(records):
            fans_count = _extract_home_profile_fans_count(records)
            if fans_count is not None and not _has_abbreviated_fans_count(records):
                return fans_count
        return None
    candidates.sort(reverse=True)
    return candidates[0]


def _antfortune_deep_link(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    app_id = (query.get("appId") or [""])[0]
    page = (query.get("page") or [""])[0]
    if not app_id or not page:
        return None
    return "afwealth://platformapi/startapp?" + urlencode({"appId": app_id, "page": page})


def _known_unavailable_profile_url(url: str) -> str | None:
    if "ur.alipay.com" not in url:
        return None
    try:
        response = requests.get(url, allow_redirects=False, timeout=12)
    except Exception:
        return None
    location = response.headers.get("location") or ""
    if response.status_code in {301, 302, 303, 307, 308} and "/404" in location:
        return "profile short link redirects to Alipay 404"
    return None


def _blocked_profile_page_error(texts: list[str]) -> str | None:
    combined = "\n".join(texts)
    if "\u5b8c\u5584\u8eab\u4efd\u4fe1\u606f" in combined or "\u7f51\u7edc\u5b89\u5168\u6cd5" in combined:
        return "profile page is blocked by identity verification"
    if "404" in combined or "\u51fa\u9519\u4e86" in combined:
        return "profile page is unavailable"
    return None


def _profile_login_required_error(texts: list[str]) -> str | None:
    combined = "\n".join(texts)
    markers = (
        "\u6253\u5f00\u652f\u4ed8\u5b9d\u767b\u5f55",
        "\u5bc6\u7801\u767b\u5f55",
        "\u624b\u673a\u53f7\u767b\u5f55",
        "\u767b\u5f55\u540e\u67e5\u770b",
    )
    if any(marker in combined for marker in markers):
        return "profile page requires login"
    return None


def _max_consecutive_device_errors() -> int:
    return max(1, min(3, int(Config.CRAWL_MAX_CONSECUTIVE_ERRORS or 3)))


def _is_device_unavailable_error(error: Any) -> bool:
    return classify_crawl_error(error).kind == DEVICE_UNAVAILABLE


def _profile_doc(doc_url: str | None = None) -> client.DocInfo:
    url = (doc_url or Config.PROFILE_METRICS_DOC_URL or Config.QQ_DOC_URL).strip()
    if not url:
        raise RuntimeError("PROFILE_METRICS_DOC_URL is not configured")
    return client.parse_doc_url(url)


def _configured_metric_date() -> date | None:
    if not Config.PROFILE_METRICS_TARGET_DATE:
        return None
    return date.fromisoformat(Config.PROFILE_METRICS_TARGET_DATE)


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) and row[index] is not None else ""


def _parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def _parse_int(value: str) -> int | None:
    text = value.strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


