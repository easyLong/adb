"""WeChat demand intake workflows driven by ops_platform metadata."""

from __future__ import annotations

import json
import base64
import hashlib
import mimetypes
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawler_app.storage.ops_platform import (
    OpsDemandCandidate,
    OpsDemandEvidence,
    OpsWechatGroupConfig,
    candidate_with_wechat_group_config,
    get_wechat_group_config_by_name,
    get_conn as get_ops_conn,
    list_wechat_group_configs_once,
    upsert_ops_demand_candidate,
)
from apps.finance_crawler.crawler_app.storage.db import get_conn as get_crawler_app_conn
from apps.finance_crawler.mobile.capture_engine import try_ocr
from apps.finance_crawler.storage.device_pool import acquire_device


@dataclass(frozen=True, slots=True)
class WechatGroupCaptureResult:
    group_config_id: str
    group_name: str
    source_key: str
    customer_name: str
    contact_name: str | None
    business_platform: str | None
    status: str
    returncode: int
    output_dir: str
    stdout: str
    stderr: str
    capture_run_id: int | None = None


@dataclass(frozen=True, slots=True)
class WechatDemandIntakeResult:
    capture_run_id: int
    group_name: str
    target_date: str
    status: str
    candidate_id: str | None = None
    external_candidate_id: str | None = None
    candidate_count: int = 0
    evidence_count: int = 0
    ocr_text_count: int = 0
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WechatMessageParseResult:
    capture_run_id: int
    group_name: str
    target_date: str
    status: str
    parse_mode: str = "ocr"
    screenshot_count: int = 0
    message_count: int = 0
    artifact_jsonl: str | None = None
    artifact_markdown: str | None = None
    reason: str | None = None


def list_wechat_demand_groups() -> list[dict[str, Any]]:
    return [_group_to_dict(group) for group in list_wechat_group_configs_once()]


def run_wechat_group_capture(
    *,
    target_date: date | None = None,
    pages: int = 12,
    out_dir: str = "exports/wechat",
    serial: str | None = None,
    limit: int = 0,
    no_search: bool = False,
    skip_navigation: bool = False,
    keep_on_device: bool = False,
) -> dict[str, Any]:
    target_date = target_date or date.today()
    groups = list_wechat_group_configs_once()
    if limit > 0:
        groups = groups[:limit]

    root = _project_root()
    script = root / "scripts" / "wechat_chat_export.py"
    output_root = _resolve_path(root, out_dir)
    batch_dir = output_root / "_batches" / target_date.isoformat() / datetime.now().strftime("%H%M%S")
    batch_dir.mkdir(parents=True, exist_ok=True)

    results: list[WechatGroupCaptureResult] = []
    with acquire_device(
        app_type="wechat",
        task_scope="wechat:group_capture",
        task_id=f"{target_date.isoformat()}:{limit or 'all'}",
        worker_id="wechat",
        adb_serial=serial,
    ) as lease:
        resolved_serial = serial or lease.adb_serial
        for group in groups:
            result = _capture_one_group(
                script=script,
                group=group,
                target_date=target_date,
                pages=pages,
                output_root=output_root,
                serial=resolved_serial,
                no_search=no_search,
                skip_navigation=skip_navigation,
                keep_on_device=keep_on_device,
            )
            results.append(result)

    summary = {
        "target_date": target_date.isoformat(),
        "total_groups": len(groups),
        "success": sum(1 for item in results if item.status == "success"),
        "failed": sum(1 for item in results if item.status != "success"),
        "batch_dir": str(batch_dir),
        "results": [asdict(item) for item in results],
    }
    (batch_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def run_wechat_demand_intake(
    *,
    target_date: date | None = None,
    capture_run_id: int = 0,
    limit: int = 0,
    intake_mode: str = "batch",
    context_size: int = 30,
) -> dict[str, Any]:
    """Convert active WeChat messages into ops_platform demand candidates."""

    intake_mode = (intake_mode or "batch").strip().lower()
    if intake_mode == "incremental":
        return run_wechat_incremental_demand_intake(limit=limit, context_size=context_size)
    if intake_mode != "batch":
        raise ValueError("intake_mode must be batch or incremental")
    runs = _load_capture_runs(target_date=target_date, capture_run_id=capture_run_id, limit=limit)
    results: list[WechatDemandIntakeResult] = []
    for run_row in runs:
        results.append(_intake_one_capture_run(run_row))
    return {
        "target_date": target_date.isoformat() if target_date else None,
        "capture_run_id": capture_run_id or None,
        "intake_mode": intake_mode,
        "total_runs": len(runs),
        "success": sum(1 for item in results if item.status == "success"),
        "skipped": sum(1 for item in results if item.status == "skipped"),
        "failed": sum(1 for item in results if item.status == "error"),
        "results": [asdict(item) for item in results],
    }


def run_wechat_incremental_demand_intake(
    *,
    limit: int = 0,
    context_size: int = 30,
) -> dict[str, Any]:
    groups = list_wechat_group_configs_once()
    if limit > 0:
        groups = groups[:limit]
    results: list[dict[str, Any]] = []
    for group in groups:
        results.append(_intake_one_group_incremental(group, context_size=max(context_size, 0)))
    return {
        "intake_mode": "incremental",
        "total_groups": len(groups),
        "success": sum(1 for item in results if item.get("status") == "success"),
        "skipped": sum(1 for item in results if item.get("status") == "skipped"),
        "failed": sum(1 for item in results if item.get("status") == "error"),
        "results": results,
    }


def run_wechat_messages_parse(
    *,
    target_date: date | None = None,
    capture_run_id: int = 0,
    limit: int = 0,
    parse_mode: str = "ocr",
) -> dict[str, Any]:
    """Parse WeChat screenshots into ordered text messages."""

    parse_mode = (parse_mode or "ocr").strip().lower()
    if parse_mode not in {"ocr", "model"}:
        raise ValueError("parse_mode must be ocr or model")
    runs = _load_capture_runs(target_date=target_date, capture_run_id=capture_run_id, limit=limit)
    results: list[WechatMessageParseResult] = []
    for run_row in runs:
        if parse_mode == "model":
            results.append(_parse_one_capture_run_with_model(run_row))
        else:
            results.append(_parse_one_capture_run_with_ocr(run_row))
    return {
        "target_date": target_date.isoformat() if target_date else None,
        "capture_run_id": capture_run_id or None,
        "parse_mode": parse_mode,
        "total_runs": len(runs),
        "success": sum(1 for item in results if item.status == "success"),
        "skipped": sum(1 for item in results if item.status == "skipped"),
        "failed": sum(1 for item in results if item.status == "error"),
        "results": [asdict(item) for item in results],
    }


def run_wechat_hourly_sync(
    *,
    target_date: date | None = None,
    pages: int | None = None,
    out_dir: str | None = None,
    serial: str | None = None,
    limit: int | None = None,
    parse_mode: str | None = None,
    context_size: int | None = None,
    no_search: bool = False,
    skip_navigation: bool = False,
    keep_on_device: bool = False,
) -> dict[str, Any]:
    """Run the production WeChat pipeline: capture, parse, then incremental intake."""

    target_date = target_date or date.today()
    pages = Config.WECHAT_SYNC_PAGES if pages is None else pages
    out_dir = out_dir or Config.WECHAT_SYNC_OUT_DIR
    serial = serial or Config.WECHAT_DEVICE_SERIAL or None
    limit = Config.WECHAT_SYNC_LIMIT if limit is None else limit
    parse_mode = (parse_mode or Config.WECHAT_SYNC_PARSE_MODE or "ocr").strip().lower()
    context_size = Config.WECHAT_SYNC_CONTEXT_SIZE if context_size is None else context_size

    capture_summary = run_wechat_group_capture(
        target_date=target_date,
        pages=pages,
        out_dir=out_dir,
        serial=serial,
        limit=limit or 0,
        no_search=no_search,
        skip_navigation=skip_navigation,
        keep_on_device=keep_on_device,
    )
    capture_run_ids = [
        int(item["capture_run_id"])
        for item in capture_summary.get("results", [])
        if item.get("status") == "success" and item.get("capture_run_id")
    ]

    parse_results: list[dict[str, Any]] = []
    for capture_run_id in capture_run_ids:
        parse_results.append(
            run_wechat_messages_parse(
                capture_run_id=capture_run_id,
                parse_mode=parse_mode,
            )
        )

    intake_summary = run_wechat_demand_intake(
        limit=limit or 0,
        intake_mode="incremental",
        context_size=context_size,
    )
    parse_success = sum(int(item.get("success") or 0) for item in parse_results)
    parse_failed = sum(int(item.get("failed") or 0) for item in parse_results)
    return {
        "target_date": target_date.isoformat(),
        "serial": serial,
        "pages": pages,
        "limit": limit or 0,
        "parse_mode": parse_mode,
        "context_size": context_size,
        "capture": capture_summary,
        "parse": {
            "total_capture_runs": len(capture_run_ids),
            "success": parse_success,
            "failed": parse_failed,
            "results": parse_results,
        },
        "intake": intake_summary,
    }


def _capture_one_group(
    *,
    script: Path,
    group: OpsWechatGroupConfig,
    target_date: date,
    pages: int,
    output_root: Path,
    serial: str | None,
    no_search: bool,
    skip_navigation: bool,
    keep_on_device: bool,
) -> WechatGroupCaptureResult:
    args = [
        sys.executable,
        str(script),
        "--group-name",
        group.group_name,
        "--date",
        target_date.isoformat(),
        "--pages",
        str(max(pages, 0)),
        "--out-dir",
        str(output_root),
    ]
    if serial:
        args.extend(["--serial", serial])
    if no_search:
        args.append("--no-search")
    if skip_navigation:
        args.append("--skip-navigation")
    if keep_on_device:
        args.append("--keep-on-device")

    completed = subprocess.run(
        args,
        cwd=str(_project_root()),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=max(180, 45 + max(pages, 0) * 30),
    )
    output_dir = output_root / _safe_name(group.group_name) / target_date.isoformat()
    if completed.returncode == 0:
        status = "success"
    elif "still on date picker" in completed.stderr:
        status = "no_messages"
    else:
        status = "error"
    _write_group_metadata(output_dir, group, completed.returncode)
    capture_run_id = _record_capture_result(
        group=group,
        target_date=target_date,
        output_dir=output_dir,
        status=status,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    return WechatGroupCaptureResult(
        group_config_id=group.id,
        group_name=group.group_name,
        source_key=group.source_key,
        customer_name=group.customer_name,
        contact_name=group.contact_name,
        business_platform=group.business_platform,
        status=status,
        returncode=completed.returncode,
        output_dir=str(output_dir),
        stdout=completed.stdout,
        stderr=completed.stderr,
        capture_run_id=capture_run_id,
    )


def _load_capture_runs(
    *,
    target_date: date | None,
    capture_run_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = ["status = 'success'"]
    params: list[Any] = []
    if capture_run_id:
        clauses.append("id = %s")
        params.append(capture_run_id)
    if target_date:
        clauses.append("target_date = %s")
        params.append(target_date)
    if target_date and not capture_run_id:
        clauses.append(
            """
            id IN (
                SELECT latest_id
                FROM (
                    SELECT MAX(id) AS latest_id
                    FROM wechat_capture_runs
                    WHERE status = 'success' AND target_date = %s
                    GROUP BY source_key
                ) latest_runs
            )
            """
        )
        params.append(target_date)
    sql = f"""
        SELECT id, source_key, source_name, external_source_id,
               ops_source_context_id, target_date, screenshot_dir, meta_json
        FROM wechat_capture_runs
        WHERE {' AND '.join(clauses)}
        ORDER BY id DESC
    """
    if limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())
    finally:
        conn.close()


def _parse_one_capture_run_with_model(run_row: dict[str, Any]) -> WechatMessageParseResult:
    capture_run_id = int(run_row["id"])
    group_name = str(run_row.get("source_name") or "")
    target_date_raw = run_row.get("target_date")
    target_date_text = target_date_raw.isoformat() if hasattr(target_date_raw, "isoformat") else str(target_date_raw or "")
    if not Config.OPENAI_API_KEY or not Config.OPENAI_BASE_URL or not Config.OPENAI_MODEL:
        return WechatMessageParseResult(
            capture_run_id=capture_run_id,
            group_name=group_name,
            target_date=target_date_text,
            status="error",
            parse_mode="model",
            reason="OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL is not configured",
        )

    screenshot_sources = _load_screenshot_sources(run_row)
    if not screenshot_sources:
        return WechatMessageParseResult(
            capture_run_id=capture_run_id,
            group_name=group_name,
            target_date=target_date_text,
            status="skipped",
            parse_mode="model",
            reason="no screenshot files",
        )

    messages: list[OpsDemandEvidence] = []
    errors: list[str] = []
    for screenshot_source in screenshot_sources:
        screenshot_path = Path(str(screenshot_source.get("screenshot_path") or ""))
        if not screenshot_path.exists():
            continue
        try:
            messages.extend(
                _recognize_screenshot_messages_with_model(
                    screenshot_path=screenshot_path,
                    group_name=group_name,
                    target_date_text=target_date_text,
                    observation_key=str(screenshot_source["observation_key"]),
                    screen_index=int(screenshot_source.get("screen_index") or 0),
                )
            )
        except Exception as exc:
            errors.append(f"{screenshot_path.name}: {exc}")

    messages = _renumber_evidences(_dedupe_evidences_by_message(_fill_missing_evidence_times(messages)))
    if messages:
        _upsert_full_message_observations(
            capture_run_id=capture_run_id,
            target_date_text=target_date_text,
            messages=messages,
            run_row=run_row,
        )
        _deactivate_superseded_message_observations(run_row)
        artifact_jsonl, artifact_markdown = _write_message_parse_artifacts(run_row, messages)
        return WechatMessageParseResult(
            capture_run_id=capture_run_id,
            group_name=group_name,
            target_date=target_date_text,
            status="success",
            parse_mode="model",
            screenshot_count=len(screenshot_sources),
            message_count=len(messages),
            artifact_jsonl=artifact_jsonl,
            artifact_markdown=artifact_markdown,
            reason="; ".join(errors[:3]) if errors else None,
        )

    return WechatMessageParseResult(
        capture_run_id=capture_run_id,
        group_name=group_name,
        target_date=target_date_text,
        status="error" if errors else "skipped",
        parse_mode="model",
        screenshot_count=len(screenshot_sources),
        message_count=0,
        reason="; ".join(errors[:5]) if errors else "no messages recognized",
    )


def _parse_one_capture_run_with_ocr(run_row: dict[str, Any]) -> WechatMessageParseResult:
    capture_run_id = int(run_row["id"])
    group_name = str(run_row.get("source_name") or "")
    target_date_raw = run_row.get("target_date")
    target_date_text = target_date_raw.isoformat() if hasattr(target_date_raw, "isoformat") else str(target_date_raw or "")
    screenshot_sources = _load_screenshot_sources(run_row)
    if not screenshot_sources:
        return WechatMessageParseResult(
            capture_run_id=capture_run_id,
            group_name=group_name,
            target_date=target_date_text,
            status="skipped",
            parse_mode="ocr",
            reason="no screenshot files",
        )

    messages: list[OpsDemandEvidence] = []
    errors: list[str] = []
    ocr_text_count = 0
    for screenshot_source in screenshot_sources:
        screenshot_path = Path(str(screenshot_source.get("screenshot_path") or ""))
        if not screenshot_path.exists():
            continue
        try:
            ocr_rows = try_ocr(screenshot_path) or []
            ocr_text_count += len(ocr_rows)
            _upsert_raw_ocr_observations(
                capture_run_id=capture_run_id,
                run_row=run_row,
                target_date_text=target_date_text,
                screen_index=int(screenshot_source.get("screen_index") or 0),
                screenshot_path=screenshot_path,
                ocr_rows=ocr_rows,
            )
            messages.extend(
                _extract_message_evidences(
                    ocr_rows,
                    screenshot_path=screenshot_path,
                    target_date_text=target_date_text,
                    observation_key=str(screenshot_source["observation_key"]),
                    screen_index=int(screenshot_source.get("screen_index") or 0),
                )
            )
        except Exception as exc:
            errors.append(f"{screenshot_path.name}: {exc}")

    messages = [
        replace(
            message,
            evidence_reason=json.dumps(
                {
                    "source": "ocr",
                    "ocr_text_count": ocr_text_count,
                },
                ensure_ascii=False,
            ),
        )
        for message in _renumber_evidences(
            _dedupe_evidences_by_message(_fill_missing_evidence_times(messages))
        )
    ]
    if messages:
        _upsert_full_message_observations(
            capture_run_id=capture_run_id,
            target_date_text=target_date_text,
            messages=messages,
            run_row=run_row,
        )
        _deactivate_superseded_message_observations(run_row)
        artifact_jsonl, artifact_markdown = _write_message_parse_artifacts(run_row, messages)
        return WechatMessageParseResult(
            capture_run_id=capture_run_id,
            group_name=group_name,
            target_date=target_date_text,
            status="success",
            parse_mode="ocr",
            screenshot_count=len(screenshot_sources),
            message_count=len(messages),
            artifact_jsonl=artifact_jsonl,
            artifact_markdown=artifact_markdown,
            reason="; ".join(errors[:3]) if errors else None,
        )

    return WechatMessageParseResult(
        capture_run_id=capture_run_id,
        group_name=group_name,
        target_date=target_date_text,
        status="error" if errors else "skipped",
        parse_mode="ocr",
        screenshot_count=len(screenshot_sources),
        message_count=0,
        reason="; ".join(errors[:5]) if errors else "no messages recognized",
    )


def _recognize_screenshot_messages_with_model(
    *,
    screenshot_path: Path,
    group_name: str,
    target_date_text: str,
    observation_key: str,
    screen_index: int,
) -> list[OpsDemandEvidence]:
    payload = _build_wechat_message_parse_payload(
        screenshot_path=screenshot_path,
        group_name=group_name,
        target_date_text=target_date_text,
    )
    content = _post_openai_chat_completion(payload)
    parsed = _parse_model_json_content(content)
    raw_messages = parsed.get("messages") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_messages, list):
        return []

    evidences: list[OpsDemandEvidence] = []
    for index, item in enumerate(raw_messages, start=1):
        if not isinstance(item, dict):
            continue
        message_text = _clean_model_message_text(str(item.get("message_text") or item.get("text") or ""))
        if not message_text or _is_noise_message(message_text):
            continue
        display_time_text = str(item.get("display_time_text") or item.get("time_text") or "").strip() or None
        message_time = _infer_model_message_time(display_time_text, target_date_text)
        sender_name = str(item.get("sender_name") or item.get("sender") or "").strip() or None
        confidence = item.get("confidence")
        order = screen_index * 100 + index
        evidences.append(
            OpsDemandEvidence(
                external_evidence_id=_short_external_id(
                    "wechat_model_message",
                    observation_key,
                    str(order),
                    sender_name or "",
                    message_text,
                ),
                evidence_order=order,
                message_time=message_time,
                display_time_text=display_time_text,
                sender_name=sender_name,
                message_text=message_text,
                screenshot_path=str(screenshot_path),
                evidence_reason=json.dumps(
                    {
                        "source": "model",
                        "model": Config.OPENAI_MODEL,
                        "confidence": confidence,
                        "screen_index": screen_index,
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return evidences


def _intake_one_capture_run_legacy(run_row: dict[str, Any]) -> WechatDemandIntakeResult:
    capture_run_id = int(run_row["id"])
    group_name = str(run_row.get("source_name") or "")
    target_date_raw = run_row.get("target_date")
    target_date_text = target_date_raw.isoformat() if hasattr(target_date_raw, "isoformat") else str(target_date_raw or "")
    evidence_rows = _load_active_message_evidences(run_row)
    evidence_texts = [str(evidence.message_text or "") for evidence in evidence_rows]
    signal = _extract_demand_signal(evidence_texts)
    if signal is None or not evidence_rows:
        return WechatDemandIntakeResult(
            capture_run_id=capture_run_id,
            group_name=group_name,
            target_date=target_date_text,
            status="skipped",
            evidence_count=len(evidence_rows),
            ocr_text_count=0,
            reason="no active demand-like messages; run wechat-messages-parse first if empty",
        )

    candidate = OpsDemandCandidate(
        external_candidate_id=_short_external_id(
            "wechat_candidate",
            str(run_row.get("source_key") or group_name),
            target_date_text,
            signal["seed"],
        ),
        external_capture_run_id=f"capture:{capture_run_id}",
        external_source_key=run_row.get("source_key"),
        external_chat_id=run_row.get("external_source_id") or run_row.get("source_key"),
        source_chat_name=group_name,
        business_category=signal["business_category"],
        secondary_category=signal["secondary_category"],
        tertiary_category=signal["tertiary_category"],
        start_time=target_date_text,
        business_name=signal["business_name"],
        demand_title=signal["demand_title"],
        demand_content=signal["demand_content"],
        confidence=signal["confidence"],
        status="pending",
        match_suggestion="由微信群截图 OCR 自动生成，需人工审核后进入正式需求。",
        created_at=target_date_text,
        evidences=evidence_rows,
    )

    ops_conn = get_ops_conn()
    try:
        with ops_conn.cursor() as cursor:
            group_config = get_wechat_group_config_by_name(ops_conn, group_name=group_name) if group_name else None
        candidate = candidate_with_wechat_group_config(candidate, group_config)
        candidate_id = upsert_ops_demand_candidate(ops_conn, candidate)
        ops_conn.commit()
    except Exception:
        ops_conn.rollback()
        raise
    finally:
        ops_conn.close()

    return WechatDemandIntakeResult(
        capture_run_id=capture_run_id,
        group_name=group_name,
        target_date=target_date_text,
        status="success",
        candidate_id=candidate_id,
        external_candidate_id=candidate.external_candidate_id,
        evidence_count=len(evidence_rows),
        ocr_text_count=0,
    )


def _intake_one_capture_run(run_row: dict[str, Any]) -> WechatDemandIntakeResult:
    capture_run_id = int(run_row["id"])
    group_name = str(run_row.get("source_name") or "")
    target_date_raw = run_row.get("target_date")
    target_date_text = target_date_raw.isoformat() if hasattr(target_date_raw, "isoformat") else str(target_date_raw or "")
    evidence_rows = _load_active_message_evidences(run_row)
    if not evidence_rows:
        return WechatDemandIntakeResult(
            capture_run_id=capture_run_id,
            group_name=group_name,
            target_date=target_date_text,
            status="skipped",
            evidence_count=0,
            ocr_text_count=0,
            reason="no active messages; run wechat-messages-parse first",
        )

    ops_conn = get_ops_conn()
    candidate_ids: list[str] = []
    external_candidate_ids: list[str] = []
    try:
        with ops_conn.cursor() as cursor:
            group_config = get_wechat_group_config_by_name(ops_conn, group_name=group_name) if group_name else None
        candidate_specs = _extract_demand_candidates_with_model(
            evidence_rows=evidence_rows,
            run_row=run_row,
            group_config=group_config,
        )
        if not candidate_specs:
            ops_conn.rollback()
            return WechatDemandIntakeResult(
                capture_run_id=capture_run_id,
                group_name=group_name,
                target_date=target_date_text,
                status="skipped",
                evidence_count=len(evidence_rows),
                ocr_text_count=0,
                reason="model found no new demand candidates",
            )
        for spec in candidate_specs:
            candidate = _build_ops_candidate_from_model_spec(
                spec=spec,
                run_row=run_row,
                group_name=group_name,
                target_date_text=target_date_text,
                evidence_rows=evidence_rows,
            )
            candidate = candidate_with_wechat_group_config(candidate, group_config)
            candidate_id = upsert_ops_demand_candidate(ops_conn, candidate)
            candidate_ids.append(candidate_id)
            external_candidate_ids.append(candidate.external_candidate_id)
        _supersede_stale_demand_candidates(
            ops_conn,
            capture_run_id=capture_run_id,
            active_external_candidate_ids=external_candidate_ids,
        )
        ops_conn.commit()
    except Exception:
        ops_conn.rollback()
        raise
    finally:
        ops_conn.close()

    return WechatDemandIntakeResult(
        capture_run_id=capture_run_id,
        group_name=group_name,
        target_date=target_date_text,
        status="success",
        candidate_id=candidate_ids[0] if candidate_ids else None,
        external_candidate_id=external_candidate_ids[0] if external_candidate_ids else None,
        candidate_count=len(candidate_ids),
        evidence_count=len(evidence_rows),
        ocr_text_count=0,
    )


def _intake_one_group_incremental(group: OpsWechatGroupConfig, *, context_size: int) -> dict[str, Any]:
    source_key = group.source_key
    source_name = group.group_name
    window = _load_incremental_message_window(
        source_key=source_key,
        source_name=source_name,
        context_size=context_size,
    )
    new_rows = window["new_rows"]
    context_rows = window["context_rows"]
    if not new_rows:
        return {
            "source_key": source_key,
            "group_name": source_name,
            "status": "skipped",
            "reason": "no new active messages after offset",
            "new_message_count": 0,
            "context_count": len(context_rows),
        }

    all_rows = context_rows + new_rows
    evidence_rows = _message_rows_to_evidences(all_rows)
    new_observation_ids = {int(row["id"]) for row in new_rows}
    new_evidence_orders = {
        evidence.evidence_order
        for evidence, row in zip(evidence_rows, all_rows, strict=False)
        if int(row["id"]) in new_observation_ids
    }
    from_observation_id = int(new_rows[0]["id"])
    to_observation_id = int(new_rows[-1]["id"])
    intake_run_id = _create_wechat_demand_intake_run(
        source_key=source_key,
        source_name=source_name,
        context_count=len(context_rows),
        new_message_count=len(new_rows),
        from_observation_id=from_observation_id,
        to_observation_id=to_observation_id,
    )
    run_ref = f"intake:{intake_run_id}"
    pseudo_run_row = {
        "id": intake_run_id,
        "source_key": source_key,
        "source_name": source_name,
        "external_source_id": group.group_id,
        "target_date": _message_date_from_row(new_rows[-1]) or date.today().isoformat(),
        "external_run_ref": run_ref,
    }
    ops_conn = get_ops_conn()
    candidate_ids: list[str] = []
    external_candidate_ids: list[str] = []
    candidate_specs: list[dict[str, Any]] = []
    try:
        candidate_specs = _extract_demand_candidates_with_model(
            evidence_rows=evidence_rows,
            run_row=pseudo_run_row,
            group_config=group,
            new_evidence_orders=new_evidence_orders,
        )
        for spec in candidate_specs:
            candidate = _build_ops_candidate_from_model_spec(
                spec=spec,
                run_row=pseudo_run_row,
                group_name=source_name,
                target_date_text=str(pseudo_run_row["target_date"]),
                evidence_rows=evidence_rows,
            )
            candidate = candidate_with_wechat_group_config(candidate, group)
            candidate_id = upsert_ops_demand_candidate(ops_conn, candidate)
            candidate_ids.append(candidate_id)
            external_candidate_ids.append(candidate.external_candidate_id)
        if int(window.get("last_observation_id") or 0) == 0:
            _supersede_bootstrap_capture_candidates(
                ops_conn,
                source_key=source_key,
                source_name=source_name,
                active_external_candidate_ids=external_candidate_ids,
            )
        ops_conn.commit()
        _advance_wechat_demand_intake_offset(
            source_key=source_key,
            source_name=source_name,
            last_observation_id=to_observation_id,
            last_message_time=new_rows[-1].get("inferred_message_time"),
            intake_run_id=intake_run_id,
        )
        _finish_wechat_demand_intake_run(
            intake_run_id=intake_run_id,
            status="success",
            candidate_count=len(candidate_ids),
            raw_model_json={"candidates": candidate_specs},
        )
        return {
            "source_key": source_key,
            "group_name": source_name,
            "status": "success",
            "intake_run_id": intake_run_id,
            "context_count": len(context_rows),
            "new_message_count": len(new_rows),
            "candidate_count": len(candidate_ids),
            "candidate_ids": candidate_ids,
            "external_candidate_ids": external_candidate_ids,
            "from_observation_id": from_observation_id,
            "to_observation_id": to_observation_id,
        }
    except Exception as exc:
        ops_conn.rollback()
        _finish_wechat_demand_intake_run(
            intake_run_id=intake_run_id,
            status="error",
            candidate_count=len(candidate_ids),
            error=str(exc),
            raw_model_json={"candidates": candidate_specs},
        )
        return {
            "source_key": source_key,
            "group_name": source_name,
            "status": "error",
            "intake_run_id": intake_run_id,
            "context_count": len(context_rows),
            "new_message_count": len(new_rows),
            "candidate_count": len(candidate_ids),
            "reason": str(exc),
        }
    finally:
        ops_conn.close()


def _message_date_from_row(row: dict[str, Any]) -> str | None:
    value = row.get("message_date")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value else None


def _supersede_stale_demand_candidates(
    conn,
    *,
    capture_run_id: int,
    active_external_candidate_ids: list[str],
) -> int:
    params: list[Any] = [f"capture:{capture_run_id}"]
    not_in_clause = ""
    if active_external_candidate_ids:
        placeholders = ", ".join(["%s"] * len(active_external_candidate_ids))
        not_in_clause = f"AND external_candidate_id NOT IN ({placeholders})"
        params.extend(active_external_candidate_ids)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE demand_intake_candidates
            SET status = 'superseded'
            WHERE source_app = 'crawler'
              AND external_capture_run_id = %s
              AND status NOT IN ('confirmed', 'rejected')
              {not_in_clause}
            """,
            params,
        )
        return int(cursor.rowcount or 0)


def _supersede_bootstrap_capture_candidates(
    conn,
    *,
    source_key: str,
    source_name: str,
    active_external_candidate_ids: list[str],
) -> int:
    params: list[Any] = []
    source_clauses: list[str] = []
    if source_key:
        source_clauses.append("external_source_key = %s")
        params.append(source_key)
    if source_name:
        source_clauses.append("source_chat_name = %s")
        params.append(source_name)
    if not source_clauses:
        return 0
    not_in_clause = ""
    if active_external_candidate_ids:
        placeholders = ", ".join(["%s"] * len(active_external_candidate_ids))
        not_in_clause = f"AND external_candidate_id NOT IN ({placeholders})"
        params.extend(active_external_candidate_ids)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE demand_intake_candidates
            SET status = 'superseded'
            WHERE source_app = 'crawler'
              AND status = 'pending'
              AND external_capture_run_id LIKE 'capture:%%'
              AND ({' OR '.join(source_clauses)})
              {not_in_clause}
            """,
            params,
        )
        return int(cursor.rowcount or 0)


def _load_screenshot_sources(run_row: dict[str, Any]) -> list[dict[str, Any]]:
    capture_run_id = int(run_row["id"])
    screenshot_dir = Path(str(run_row.get("screenshot_dir") or ""))
    if not screenshot_dir.exists():
        return []
    screenshots = [
        path
        for path in sorted(screenshot_dir.glob("*.png"))
        if not path.name.startswith("_")
    ]
    return [
        {
            "screen_index": index,
            "observation_key": _hash_key("wechat_screenshot", str(capture_run_id), path.name),
            "screenshot_path": str(path),
        }
        for index, path in enumerate(screenshots)
    ]


def _load_incremental_message_window(
    *,
    source_key: str,
    source_name: str,
    context_size: int,
) -> dict[str, Any]:
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT last_observation_id
                FROM wechat_demand_intake_offsets
                WHERE source_key = %s AND status = 'active'
                LIMIT 1
                """,
                (source_key,),
            )
            offset_row = cursor.fetchone() or {}
            last_observation_id = int(offset_row.get("last_observation_id") or 0)
            source_params: list[Any] = []
            source_clause = _active_message_source_clause(
                source_key=source_key,
                source_name=source_name,
                params=source_params,
            )
            cursor.execute(
                f"""
                SELECT {_active_message_select_columns()}
                FROM wechat_message_observations o
                LEFT JOIN wechat_capture_runs r ON r.id = o.run_id
                WHERE o.message_type = 'text'
                  AND o.status = 'active'
                  AND o.id > %s
                  AND {source_clause}
                ORDER BY o.id
                """,
                [last_observation_id, *source_params],
            )
            new_rows = list(cursor.fetchall())
            context_rows: list[dict[str, Any]] = []
            if context_size > 0:
                source_params = []
                source_clause = _active_message_source_clause(
                    source_key=source_key,
                    source_name=source_name,
                    params=source_params,
                )
                cursor.execute(
                    f"""
                    SELECT *
                    FROM (
                        SELECT {_active_message_select_columns()}
                        FROM wechat_message_observations o
                        LEFT JOIN wechat_capture_runs r ON r.id = o.run_id
                        WHERE o.message_type = 'text'
                          AND o.status = 'active'
                          AND o.id <= %s
                          AND {source_clause}
                        ORDER BY o.id DESC
                        LIMIT %s
                    ) latest_context
                    ORDER BY id
                    """,
                    [last_observation_id, *source_params, context_size],
                )
                context_rows = list(cursor.fetchall())
    finally:
        conn.close()
    return {
        "last_observation_id": last_observation_id,
        "context_rows": context_rows,
        "new_rows": new_rows,
    }


def _active_message_select_columns() -> str:
    return (
        "o.id, o.run_id, o.message_fingerprint, o.message_order, "
        "o.bubble_index, o.display_time_text, o.inferred_message_time, "
        "o.message_date, o.sender_name, o.message_text, o.screenshot_path, "
        "o.parser_type, o.raw_json"
    )


def _active_message_source_clause(*, source_key: str, source_name: str, params: list[Any]) -> str:
    source_clauses: list[str] = []
    if source_key:
        source_clauses.append("o.source_key = %s")
        params.append(source_key)
    if source_name:
        source_clauses.append("o.source_name = %s")
        params.append(source_name)
    if source_key:
        source_clauses.append("r.source_key = %s")
        params.append(source_key)
    if source_name:
        source_clauses.append("r.source_name = %s")
        params.append(source_name)
    return f"({' OR '.join(source_clauses)})" if source_clauses else "1=0"


def _load_active_message_evidences(run_row: dict[str, Any]) -> list[OpsDemandEvidence]:
    capture_run_id = int(run_row["id"])
    source_key = str(run_row.get("source_key") or "")
    source_name = str(run_row.get("source_name") or "")
    target_date_raw = run_row.get("target_date")
    target_date_text = target_date_raw.isoformat() if hasattr(target_date_raw, "isoformat") else str(target_date_raw or "")
    clauses = [
        "o.message_type = 'text'",
        "o.status = 'active'",
        "o.message_date = %s",
    ]
    params: list[Any] = [target_date_text]
    source_clauses: list[str] = []
    if source_key:
        source_clauses.append("o.source_key = %s")
        params.append(source_key)
    if source_name:
        source_clauses.append("o.source_name = %s")
        params.append(source_name)
    if source_key:
        source_clauses.append("r.source_key = %s")
        params.append(source_key)
    if source_name:
        source_clauses.append("r.source_name = %s")
        params.append(source_name)
    if source_clauses:
        clauses.append(f"({' OR '.join(source_clauses)})")
    else:
        clauses.append("o.run_id = %s")
        params.append(capture_run_id)

    sql = f"""
        SELECT o.id, o.run_id, o.message_fingerprint, o.message_order,
               o.bubble_index, o.display_time_text, o.inferred_message_time,
               o.sender_name, o.message_text, o.screenshot_path,
               o.parser_type, o.raw_json
        FROM wechat_message_observations o
        LEFT JOIN wechat_capture_runs r ON r.id = o.run_id
        WHERE {' AND '.join(clauses)}
        ORDER BY
            o.inferred_message_time IS NULL,
            o.inferred_message_time,
            o.message_order,
            o.id
    """
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = list(cursor.fetchall())
    finally:
        conn.close()

    return _message_rows_to_evidences(rows)


def _message_rows_to_evidences(rows: list[dict[str, Any]]) -> list[OpsDemandEvidence]:
    evidences: list[OpsDemandEvidence] = []
    for index, row in enumerate(rows, start=1):
        observation_id = str(row.get("id") or "")
        fingerprint = str(row.get("message_fingerprint") or "")
        evidences.append(
            OpsDemandEvidence(
                external_evidence_id=_short_external_id(
                    "wechat_active_message",
                    fingerprint or observation_id,
                ),
                evidence_order=index,
                message_time=row.get("inferred_message_time"),
                display_time_text=row.get("display_time_text"),
                sender_name=row.get("sender_name"),
                message_text=row.get("message_text"),
                screenshot_path=row.get("screenshot_path"),
                evidence_reason=json.dumps(
                    {
                        "source": "wechat_message_observations",
                        "observation_id": row.get("id"),
                        "source_run_id": row.get("run_id"),
                        "message_fingerprint": row.get("message_fingerprint"),
                        "parser_type": row.get("parser_type"),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return evidences


def _create_wechat_demand_intake_run(
    *,
    source_key: str,
    source_name: str,
    context_count: int,
    new_message_count: int,
    from_observation_id: int,
    to_observation_id: int,
) -> int:
    run_key = _hash_key(
        "wechat_incremental_intake",
        source_key,
        str(from_observation_id),
        str(to_observation_id),
        datetime.now().isoformat(timespec="microseconds"),
    )
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO wechat_demand_intake_runs (
                    run_key, source_key, source_name,
                    from_observation_id, to_observation_id,
                    context_count, new_message_count,
                    model_name, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running')
                """,
                (
                    run_key,
                    source_key,
                    source_name,
                    from_observation_id,
                    to_observation_id,
                    context_count,
                    new_message_count,
                    Config.OPENAI_MODEL,
                ),
            )
            run_id = int(cursor.lastrowid)
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _finish_wechat_demand_intake_run(
    *,
    intake_run_id: int,
    status: str,
    candidate_count: int,
    raw_model_json: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE wechat_demand_intake_runs
                SET status = %s,
                    candidate_count = %s,
                    raw_model_json = %s,
                    error = %s,
                    finished_at = NOW()
                WHERE id = %s
                """,
                (
                    status,
                    candidate_count,
                    json.dumps(raw_model_json or {}, ensure_ascii=False),
                    error,
                    intake_run_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _advance_wechat_demand_intake_offset(
    *,
    source_key: str,
    source_name: str,
    last_observation_id: int,
    last_message_time: Any,
    intake_run_id: int,
) -> None:
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO wechat_demand_intake_offsets (
                    source_key, source_name, last_observation_id,
                    last_message_time, last_intake_run_at, meta_json, status
                )
                VALUES (%s, %s, %s, %s, NOW(), %s, 'active')
                ON DUPLICATE KEY UPDATE
                    source_name = VALUES(source_name),
                    last_observation_id = VALUES(last_observation_id),
                    last_message_time = VALUES(last_message_time),
                    last_intake_run_at = VALUES(last_intake_run_at),
                    meta_json = VALUES(meta_json),
                    status = 'active'
                """,
                (
                    source_key,
                    source_name,
                    last_observation_id,
                    last_message_time,
                    json.dumps({"last_intake_run_id": intake_run_id}, ensure_ascii=False),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _extract_demand_candidates_with_model(
    *,
    evidence_rows: list[OpsDemandEvidence],
    run_row: dict[str, Any],
    group_config: OpsWechatGroupConfig | None,
    new_evidence_orders: set[int] | None = None,
) -> list[dict[str, Any]]:
    if not Config.OPENAI_API_KEY or not Config.OPENAI_BASE_URL or not Config.OPENAI_MODEL:
        return []
    payload = _build_demand_intake_model_payload(
        evidence_rows=evidence_rows,
        run_row=run_row,
        group_config=group_config,
        new_evidence_orders=new_evidence_orders or set(),
    )
    content = _post_openai_chat_completion(payload)
    parsed = _parse_model_json_content(content)
    raw_candidates = parsed.get("candidates") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_candidates, list):
        return []
    candidates: list[dict[str, Any]] = []
    valid_orders = {int(item.evidence_order) for item in evidence_rows}
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        title = str(item.get("demand_title") or item.get("title") or "").strip()
        content_text = str(item.get("demand_content") or item.get("content") or "").strip()
        evidence_orders = _parse_evidence_orders(item.get("evidence_orders"), valid_orders)
        if new_evidence_orders and not (set(evidence_orders) & new_evidence_orders):
            continue
        if not title or not content_text or not evidence_orders:
            continue
        business_category, secondary_category = _normalize_model_categories(
            item.get("business_category"),
            item.get("secondary_category"),
        )
        candidates.append(
            {
                "demand_title": title,
                "demand_content": content_text,
                "business_category": business_category,
                "secondary_category": secondary_category,
                "tertiary_category": str(item.get("tertiary_category") or title).strip() or title,
                "business_name": str(item.get("business_name") or title).strip() or title,
                "start_time": str(item.get("start_time") or "").strip() or None,
                "deadline": str(item.get("deadline") or "").strip() or None,
                "confidence": _safe_float_or_none(item.get("confidence")) or 0.7,
                "evidence_orders": evidence_orders,
                "reason": str(item.get("reason") or "").strip() or None,
            }
        )
    return candidates


def _build_demand_intake_model_payload(
    *,
    evidence_rows: list[OpsDemandEvidence],
    run_row: dict[str, Any],
    group_config: OpsWechatGroupConfig | None,
    new_evidence_orders: set[int] | None = None,
) -> dict[str, Any]:
    target_date_raw = run_row.get("target_date")
    target_date_text = target_date_raw.isoformat() if hasattr(target_date_raw, "isoformat") else str(target_date_raw or "")
    group_name = str(run_row.get("source_name") or "")
    contact_name = group_config.contact_name if group_config else None
    customer_name = group_config.customer_name if group_config else None
    business_platform = group_config.business_platform if group_config else None
    messages = [
        {
            "order": item.evidence_order,
            "scope": "new" if new_evidence_orders and item.evidence_order in new_evidence_orders else "context",
            "time": str(item.message_time or item.display_time_text or ""),
            "sender": item.sender_name,
            "text": item.message_text,
        }
        for item in evidence_rows
    ]
    system_prompt = (
        "You are a demand intake analyst. Identify new work demand candidates "
            "from a chronological WeChat group conversation. Return strict JSON only."
    )
    user_prompt = {
        "task": (
            "Read the full conversation history and group multi-turn discussions "
            "that refer to the same work item into one demand candidate. A demand "
            "is often formed by several messages, not by a single message. Other "
            "people's replies are usually evidence for the same demand. Pay special "
            "attention to messages from contact/owner people, but do not split every "
            "contact message into a separate demand. Messages marked context are "
            "only background for judging continuity. In incremental mode, only output "
            "a candidate if at least one evidence_orders item is marked new, and do "
            "not output a candidate when new messages only continue an already clear "
            "context demand without introducing a new work item."
        ),
        "group": {
            "group_name": group_name,
            "target_date": target_date_text,
            "customer_name": customer_name,
            "contact_names": contact_name,
            "business_platform": business_platform,
        },
        "category_rules": {
            "business_category": ["设计", "文案", "运营", "社区"],
            "secondary_category": {
                "设计": ["配图拓展", "banner新设计", "巨幅新设计", "长图新设计", "长图拓展", "长图套模板", "（其他）"],
                "文案": ["数据更新", "已有素材新编辑", "原创文案", "共建文案", "（其他）"],
                "运营": ["发布陪伴", "活动配置", "魔秀搭建", "页面推厂", "直播配置", "（其他）"],
                "社区": ["粉丝投放", "精华贴", "氛围贴", "（其他）"],
            },
        },
        "output_schema": {
            "candidates": [
                {
                    "demand_title": "short title",
                    "business_category": "设计|文案|运营|社区",
                    "secondary_category": "one mapped value",
                    "tertiary_category": "more specific name",
                    "business_name": "business/work name",
                    "demand_content": "summary of the demand and requested changes",
                    "start_time": "YYYY-MM-DD HH:MM:SS or null",
                    "deadline": "YYYY-MM-DD HH:MM:SS or null",
                    "confidence": 0.0,
                    "evidence_orders": [1, 2],
                    "reason": "why these messages form one demand",
                }
            ]
        },
        "messages": messages,
    }
    return {
        "model": Config.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": Config.OPENAI_MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }


def _build_ops_candidate_from_model_spec(
    *,
    spec: dict[str, Any],
    run_row: dict[str, Any],
    group_name: str,
    target_date_text: str,
    evidence_rows: list[OpsDemandEvidence],
) -> OpsDemandCandidate:
    selected_evidences = _select_candidate_evidences(evidence_rows, spec.get("evidence_orders") or [])
    seed = _candidate_seed_from_spec(spec, selected_evidences)
    return OpsDemandCandidate(
        external_candidate_id=_short_external_id(
            "wechat_model_candidate",
            str(run_row.get("source_key") or group_name),
            target_date_text,
            seed,
        ),
        external_capture_run_id=str(run_row.get("external_run_ref") or f"capture:{int(run_row['id'])}"),
        external_source_key=run_row.get("source_key"),
        external_chat_id=run_row.get("external_source_id") or run_row.get("source_key"),
        source_chat_name=group_name,
        business_category=spec.get("business_category"),
        secondary_category=spec.get("secondary_category"),
        tertiary_category=spec.get("tertiary_category"),
        start_time=spec.get("start_time") or target_date_text,
        deadline=spec.get("deadline"),
        business_name=spec.get("business_name"),
        demand_title=spec.get("demand_title"),
        demand_content=spec.get("demand_content"),
        confidence=spec.get("confidence"),
        status="pending",
        match_suggestion="model grouped active chat messages into a demand candidate; human review required",
        created_at=spec.get("start_time") or target_date_text,
        evidences=selected_evidences,
    )


def _select_candidate_evidences(
    evidence_rows: list[OpsDemandEvidence],
    evidence_orders: list[int],
) -> list[OpsDemandEvidence]:
    by_order = {int(item.evidence_order): item for item in evidence_rows}
    selected: list[OpsDemandEvidence] = []
    for output_order, evidence_order in enumerate(evidence_orders, start=1):
        evidence = by_order.get(int(evidence_order))
        if evidence is None:
            continue
        selected.append(replace(evidence, evidence_order=output_order))
    return selected


def _candidate_seed_from_spec(spec: dict[str, Any], evidences: list[OpsDemandEvidence]) -> str:
    first_evidence = evidences[0] if evidences else None
    return "|".join(
        [
            ",".join(str(evidence.external_evidence_id) for evidence in evidences),
            str(first_evidence.message_time if first_evidence else ""),
            str(first_evidence.sender_name if first_evidence else ""),
            str(first_evidence.message_text if first_evidence else ""),
        ]
    )


def _parse_evidence_orders(value: Any, valid_orders: set[int]) -> list[int]:
    raw_values = value if isinstance(value, list) else []
    result: list[int] = []
    for raw in raw_values:
        try:
            order = int(raw)
        except (TypeError, ValueError):
            continue
        if order in valid_orders and order not in result:
            result.append(order)
    return result


def _normalize_model_categories(
    business_category: Any,
    secondary_category: Any,
) -> tuple[str, str]:
    mapping = {
        "设计": {"配图拓展", "banner新设计", "巨幅新设计", "长图新设计", "长图拓展", "长图套模板", "（其他）"},
        "文案": {"数据更新", "已有素材新编辑", "原创文案", "共建文案", "（其他）"},
        "运营": {"发布陪伴", "活动配置", "魔秀搭建", "页面推厂", "直播配置", "（其他）"},
        "社区": {"粉丝投放", "精华贴", "氛围贴", "（其他）"},
    }
    category = str(business_category or "设计").strip()
    if category not in mapping:
        category = "设计"
    secondary = str(secondary_category or "（其他）").strip()
    if secondary not in mapping[category]:
        secondary = "（其他）"
    return category, secondary


def _upsert_full_message_observations(
    *,
    capture_run_id: int,
    target_date_text: str,
    messages: list[OpsDemandEvidence],
    run_row: dict[str, Any] | None = None,
) -> None:
    source_key = str((run_row or {}).get("source_key") or "")
    source_name = str((run_row or {}).get("source_name") or "")
    message_date = _parse_target_date(target_date_text)
    message_fingerprints = [
        _message_fingerprint(
            source_key=source_key,
            source_name=source_name,
            target_date_text=target_date_text,
            message=message,
        )
        for message in messages
    ]
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            for message in messages:
                raw_json = _message_observation_raw_json(message)
                parser_type = str(raw_json.get("source") or "unknown")[:32]
                normalized_text = _normalize_message_text_for_fingerprint(str(message.message_text or ""))
                message_fingerprint = _message_fingerprint(
                    source_key=source_key,
                    source_name=source_name,
                    target_date_text=target_date_text,
                    message=message,
                )
                observation_key = _hash_key("wechat_message", message_fingerprint)
                cursor.execute(
                    """
                    INSERT INTO wechat_message_observations (
                        message_fingerprint, run_id, chat_id,
                        source_key, source_name,
                        screen_index, bubble_index, message_order,
                        message_date, display_time_text, inferred_message_time,
                        sender_name, message_type, message_text,
                        normalized_message_text,
                        screenshot_path, raw_json, confidence,
                        observation_key, parser_type,
                        first_seen_run_id, latest_seen_run_id,
                        status
                    )
                    VALUES (
                        %s, %s, NULL,
                        %s, %s,
                        0, %s, %s,
                        %s, %s, %s,
                        %s, 'text', %s,
                        %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        'active'
                    )
                    ON DUPLICATE KEY UPDATE
                        run_id = VALUES(run_id),
                        source_key = VALUES(source_key),
                        source_name = VALUES(source_name),
                        bubble_index = VALUES(bubble_index),
                        message_order = VALUES(message_order),
                        message_date = VALUES(message_date),
                        display_time_text = VALUES(display_time_text),
                        inferred_message_time = VALUES(inferred_message_time),
                        sender_name = VALUES(sender_name),
                        message_text = VALUES(message_text),
                        normalized_message_text = VALUES(normalized_message_text),
                        screenshot_path = VALUES(screenshot_path),
                        raw_json = VALUES(raw_json),
                        confidence = VALUES(confidence),
                        parser_type = VALUES(parser_type),
                        latest_seen_run_id = VALUES(latest_seen_run_id),
                        status = VALUES(status)
                    """,
                    (
                        message_fingerprint,
                        capture_run_id,
                        source_key or None,
                        source_name or None,
                        message.evidence_order,
                        message.evidence_order,
                        message_date or target_date_text,
                        message.display_time_text,
                        message.message_time,
                        message.sender_name,
                        message.message_text,
                        normalized_text[:700],
                        message.screenshot_path,
                        json.dumps(raw_json, ensure_ascii=False),
                        _message_observation_confidence(raw_json),
                        observation_key,
                        parser_type,
                        capture_run_id,
                        capture_run_id,
                    ),
                )
            _deactivate_stale_message_observations_in_cursor(
                cursor,
                capture_run_id=capture_run_id,
                source_key=source_key,
                source_name=source_name,
                target_date_text=target_date_text,
                active_fingerprints=message_fingerprints,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _upsert_raw_ocr_observations(
    *,
    capture_run_id: int,
    run_row: dict[str, Any],
    target_date_text: str,
    screen_index: int,
    screenshot_path: Path,
    ocr_rows: list[dict[str, Any]],
) -> None:
    source_key = str(run_row.get("source_key") or "")
    source_name = str(run_row.get("source_name") or "")
    message_date = _parse_target_date(target_date_text) or target_date_text
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            for line_index, row in enumerate(sorted(ocr_rows, key=_ocr_sort_key), start=1):
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                bounds = row.get("bounds") or {}
                confidence = row.get("confidence", row.get("score"))
                observation_key = _hash_key(
                    "wechat_ocr",
                    str(capture_run_id),
                    str(screen_index),
                    str(line_index),
                    text,
                )
                cursor.execute(
                    """
                    INSERT INTO wechat_ocr_observations (
                        run_id, source_key, source_name,
                        screen_index, line_index, message_date,
                        screenshot_path, ocr_text, bbox_json, raw_json,
                        confidence, observation_key, parser_type, status
                    )
                    VALUES (
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, 'ocr', 'active'
                    )
                    ON DUPLICATE KEY UPDATE
                        source_key = VALUES(source_key),
                        source_name = VALUES(source_name),
                        message_date = VALUES(message_date),
                        screenshot_path = VALUES(screenshot_path),
                        ocr_text = VALUES(ocr_text),
                        bbox_json = VALUES(bbox_json),
                        raw_json = VALUES(raw_json),
                        confidence = VALUES(confidence),
                        status = VALUES(status)
                    """,
                    (
                        capture_run_id,
                        source_key or None,
                        source_name or None,
                        screen_index,
                        line_index,
                        message_date,
                        str(screenshot_path),
                        text,
                        json.dumps(bounds, ensure_ascii=False),
                        json.dumps(row, ensure_ascii=False),
                        _safe_float_or_none(confidence),
                        observation_key,
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _deactivate_stale_message_observations_in_cursor(
    cursor,
    *,
    capture_run_id: int,
    source_key: str,
    source_name: str,
    target_date_text: str,
    active_fingerprints: list[str],
) -> int:
    source_conditions = []
    params: list[Any] = [target_date_text]
    if source_key:
        source_conditions.append("o.source_key = %s")
        params.append(source_key)
    if source_name:
        source_conditions.append("o.source_name = %s")
        params.append(source_name)
    if source_key:
        source_conditions.append("r.source_key = %s")
        params.append(source_key)
    if source_name:
        source_conditions.append("r.source_name = %s")
        params.append(source_name)
    if not source_conditions:
        return 0

    fingerprint_clause = ""
    if active_fingerprints:
        placeholders = ", ".join(["%s"] * len(active_fingerprints))
        fingerprint_clause = f"AND (o.message_fingerprint IS NULL OR o.message_fingerprint NOT IN ({placeholders}))"
        params.extend(active_fingerprints)

    cursor.execute(
        f"""
        UPDATE wechat_message_observations o
        LEFT JOIN wechat_capture_runs r ON r.id = o.run_id
        SET o.status = 'superseded'
        WHERE o.message_type = 'text'
          AND o.status = 'active'
          AND o.message_date = %s
          AND ({' OR '.join(source_conditions)})
          {fingerprint_clause}
        """,
        params,
    )
    return int(cursor.rowcount or 0)


def _deactivate_superseded_message_observations(run_row: dict[str, Any]) -> int:
    capture_run_id = int(run_row["id"])
    source_key = str(run_row.get("source_key") or "")
    source_name = str(run_row.get("source_name") or "")
    target_date_raw = run_row.get("target_date")
    if not (source_key or source_name) or not target_date_raw:
        return 0
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE wechat_message_observations o
                JOIN wechat_capture_runs r ON r.id = o.run_id
                SET o.status = 'superseded'
                WHERE o.message_type = 'text'
                  AND o.status = 'active'
                  AND r.target_date = %s
                  AND r.id <> %s
                  AND (
                      (%s <> '' AND r.source_key = %s)
                      OR (%s <> '' AND r.source_name = %s)
                  )
                """,
                (target_date_raw, capture_run_id, source_key, source_key, source_name, source_name),
            )
            affected = int(cursor.rowcount or 0)
        conn.commit()
        return affected
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _message_observation_raw_json(message: OpsDemandEvidence) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "source": "wechat_demand_intake",
        "external_evidence_id": message.external_evidence_id,
        "evidence_reason": message.evidence_reason,
    }
    if message.evidence_reason:
        try:
            parsed = json.loads(str(message.evidence_reason))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            raw.update(parsed)
    return raw


def _message_observation_confidence(raw_json: dict[str, Any]) -> float:
    value = raw_json.get("confidence")
    try:
        if value is not None:
            return float(value)
    except (TypeError, ValueError):
        pass
    return 0.55


def _message_fingerprint(
    *,
    source_key: str,
    source_name: str,
    target_date_text: str,
    message: OpsDemandEvidence,
) -> str:
    source_identity = source_key or source_name or "unknown"
    normalized_text = _normalize_message_text_for_fingerprint(str(message.message_text or ""))
    sender = _normalize_message_text_for_fingerprint(str(message.sender_name or ""))
    time_bucket = _message_time_bucket(message.message_time, message.display_time_text)
    return _hash_key(
        "wechat_message_fingerprint",
        source_identity,
        target_date_text,
        sender,
        time_bucket,
        normalized_text,
    )


def _message_time_bucket(message_time: Any, display_time_text: str | None) -> str:
    if message_time:
        text = str(message_time)
        match = re.search(r"(\d{1,2}):(\d{2})", text)
        if match:
            return f"{int(match.group(1)):02d}:{match.group(2)}"
    if display_time_text:
        match = re.search(r"(\d{1,2})[:：](\d{2})", str(display_time_text))
        if match:
            return f"{int(match.group(1)):02d}:{match.group(2)}"
    return ""


def _normalize_message_text_for_fingerprint(value: str) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "：": ":",
        "？": "?",
        "！": "!",
        "，": ",",
        "。": ".",
        "（": "(",
        "）": ")",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", "", text)


def _safe_float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if parsed > 1:
            parsed = parsed / 100
        return max(0.0, min(parsed, 1.0))
    except (TypeError, ValueError):
        return None


def _build_wechat_message_parse_payload(
    *,
    screenshot_path: Path,
    group_name: str,
    target_date_text: str,
) -> dict[str, Any]:
    data_url = _image_to_data_url(screenshot_path)
    system_prompt = (
        "You are a precise WeChat group chat screenshot parser. "
        "Return strict JSON only. Do not infer messages that are not visible."
    )
    user_prompt = (
        "Parse this WeChat group chat screenshot into ordered visible chat messages. "
        "Ignore status bar, navigation title, search/date picker controls, input bar, "
        "system UI, unread markers, and pure image thumbnails without readable text. "
        "Do not extract image-only bubbles. Do not extract gray quoted/replied-to "
        "preview blocks, and do not merge quoted preview text into the current message. "
        "Only extract the actual sender's visible white/green chat bubble text. "
        "Keep the top-to-bottom order. If a time separator is visible, attach it to "
        "the following messages until another time separator appears. If sender name "
        "is not visible, use null. Preserve original Chinese text and line breaks.\n"
        f"group_name: {group_name}\n"
        f"target_date: {target_date_text}\n"
        "JSON schema: {\"messages\":[{\"display_time_text\":string|null,"
        "\"sender_name\":string|null,\"message_text\":string,"
        "\"message_type\":\"text\",\"confidence\":number}]}"
    )
    return {
        "model": Config.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": Config.OPENAI_MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }


def _post_openai_chat_completion(payload: dict[str, Any]) -> str:
    url = _openai_chat_completions_url()
    headers = {
        "Authorization": f"Bearer {Config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=Config.OPENAI_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400 and "response_format" in payload:
        retry_payload = dict(payload)
        retry_payload.pop("response_format", None)
        response = requests.post(
            url,
            headers=headers,
            json=retry_payload,
            timeout=Config.OPENAI_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("empty model choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    if not str(content or "").strip():
        raise ValueError("empty model content")
    return str(content)


def _openai_chat_completions_url() -> str:
    base_url = Config.OPENAI_BASE_URL.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return f"{base_url}/chat/completions"


def _image_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _parse_model_json_content(content: str) -> dict[str, Any] | list[Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start >= 0 and object_end > object_start:
        return json.loads(text[object_start : object_end + 1])
    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start >= 0 and array_end > array_start:
        return json.loads(text[array_start : array_end + 1])
    raise ValueError("model content is not valid JSON")


def _infer_model_message_time(display_time_text: str | None, target_date_text: str) -> str | None:
    if not display_time_text:
        return None
    text = str(display_time_text).strip()
    target = _parse_target_date(target_date_text)
    if target is None:
        return None
    full_match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(.*)", text)
    if full_match:
        target = date(int(full_match.group(1)), int(full_match.group(2)), int(full_match.group(3)))
        text = full_match.group(4).strip()
    elif "昨天" in text:
        # The capture workflow searches a concrete target_date in WeChat.
        # Relative labels such as "yesterday" are rendered by the device UI,
        # but the searched target_date is still the authoritative message date.
        text = text.replace("昨天", "").strip()
    elif "今天" in text:
        text = text.replace("今天", "").strip()

    match = re.search(r"(?:(上午|下午|晚上|凌晨|中午)\s*)?(\d{1,2})[:：](\d{2})", text)
    if not match:
        return None
    period = match.group(1) or ""
    hour = int(match.group(2))
    minute = int(match.group(3))
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    return f"{target.isoformat()} {hour:02d}:{minute:02d}:00"


def _parse_target_date(value: str) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _write_message_parse_artifacts(
    run_row: dict[str, Any],
    messages: list[OpsDemandEvidence],
) -> tuple[str | None, str | None]:
    screenshot_dir = Path(str(run_row.get("screenshot_dir") or ""))
    if not screenshot_dir.exists():
        return None, None
    jsonl_path = screenshot_dir / "messages.jsonl"
    markdown_path = screenshot_dir / "messages.md"
    with jsonl_path.open("w", encoding="utf-8") as fp:
        for message in messages:
            fp.write(
                json.dumps(
                    {
                        "order": message.evidence_order,
                        "message_time": message.message_time,
                        "display_time_text": message.display_time_text,
                        "sender_name": message.sender_name,
                        "message_text": message.message_text,
                        "screenshot_path": message.screenshot_path,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    lines = [f"# WeChat Messages - run {run_row.get('id')}", ""]
    for message in messages:
        sender = message.sender_name or "unknown"
        when = message.message_time or message.display_time_text or ""
        body = str(message.message_text or "").strip()
        lines.append(f"- {when} | {sender} | {body}")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(jsonl_path), str(markdown_path)


def _clean_ocr_texts(ocr_rows: list[dict[str, Any]]) -> list[str]:
    cleaned: list[str] = []
    ignored_contains = (
        "微信",
        "按日期查找",
        "消息免打扰",
    )
    for row in sorted(ocr_rows, key=lambda item: _ocr_sort_key(item)):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        bounds = row.get("bounds") or {}
        top = int(bounds.get("top") or 0)
        if top < 120:
            continue
        if text in {"返回", "今天", "昨天", "收到"}:
            continue
        if re.search(r"[（(]\d+[）)]$", text):
            continue
        if re.match(r"^(昨天|今天|上午|下午|晚上)?\d{1,2}[:：]\d{2}$", text):
            continue
        if re.match(r"^昨天(上午|下午|晚上)\d{1,2}[:：]\d{2}$", text):
            continue
        if any(marker in text for marker in ignored_contains):
            continue
        cleaned.append(text)
    return _dedupe_keep_order(cleaned)


def _extract_message_evidences(
    ocr_rows: list[dict[str, Any]],
    *,
    screenshot_path: Path,
    target_date_text: str,
    observation_key: str,
    screen_index: int,
) -> list[OpsDemandEvidence]:
    rows = sorted(ocr_rows, key=_ocr_sort_key)
    evidences: list[OpsDemandEvidence] = []
    current_time_text: str | None = None
    current_message_time: str | None = None
    current_sender: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_sender, current_lines
        message_text = "\n".join(_dedupe_keep_order(current_lines)).strip()
        if not message_text:
            current_lines = []
            return
        if _is_noise_message(message_text):
            current_lines = []
            return
        order = screen_index * 100 + len(evidences) + 1
        evidences.append(
            OpsDemandEvidence(
                external_evidence_id=_short_external_id("wechat_evidence", observation_key, str(order), message_text),
                evidence_order=order,
                message_time=current_message_time,
                display_time_text=current_time_text,
                sender_name=current_sender,
                message_text=message_text,
                screenshot_path=str(screenshot_path),
                evidence_reason="source chat message supports this candidate",
            )
        )
        current_lines = []

    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text or _is_ignored_ocr_line(text, row):
            continue
        parsed_time = _parse_wechat_time_text(text, target_date_text)
        if parsed_time:
            flush()
            current_time_text, current_message_time = parsed_time
            current_sender = None
            continue

        split_sender, split_message = _split_inline_sender_message(text)
        if split_sender and split_message:
            flush()
            current_sender = split_sender
            current_lines = [split_message]
            continue

        if _is_sender_line(text, row):
            flush()
            current_sender = text.rstrip(":：")
            continue

        current_lines.append(text)

    flush()
    return evidences


def _is_ignored_ocr_line(text: str, row: dict[str, Any]) -> bool:
    bounds = row.get("bounds") or {}
    top = int(bounds.get("top") or 0)
    if top < 120:
        return True
    if re.search(r"[（(]\d+[）)]$", text):
        return True
    ignored = {
        "返回",
        "今天",
        "昨天",
        "按日期查找",
        "消息免打扰",
    }
    return text in ignored


def _parse_wechat_time_text(text: str, target_date_text: str) -> tuple[str, str] | None:
    match = re.search(r"(?:(?:昨天|今天)?\s*)?(上午|下午|晚上)?\s*(\d{1,2})[:：](\d{2})", text)
    if not match:
        return None
    period = match.group(1) or ""
    hour = int(match.group(2))
    minute = int(match.group(3))
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    display = f"{hour:02d}:{minute:02d}"
    return display, f"{target_date_text} {display}:00"


def _split_inline_sender_message(text: str) -> tuple[str | None, str | None]:
    if "：" in text:
        sender, message = text.split("：", 1)
    elif ":" in text:
        sender, message = text.split(":", 1)
    else:
        return None, None
    sender = sender.strip()
    message = message.strip()
    if 1 <= len(sender) <= 8 and message:
        return sender, message
    return None, None


def _is_sender_line(text: str, row: dict[str, Any]) -> bool:
    if len(text) > 12:
        return False
    if _is_demand_like(text):
        return False
    if any(mark in text for mark in ("？", "?", "。", "，", ",", "！", "!")):
        return False
    if text.startswith("@"):
        return False
    bounds = row.get("bounds") or {}
    left = int(bounds.get("left") or 0)
    if 120 <= left <= 195:
        return True
    return text.endswith((":", "："))


def _is_noise_message(message_text: str) -> bool:
    compact = message_text.strip()
    if not compact:
        return True
    if re.search(r"[（(]\d+[）)]$", compact):
        return True
    lines = [line.strip() for line in compact.splitlines() if line.strip()]
    if lines and all(line.startswith("@") for line in lines):
        return True
    return False


def _renumber_evidences(evidences: list[OpsDemandEvidence]) -> list[OpsDemandEvidence]:
    return [replace(evidence, evidence_order=index) for index, evidence in enumerate(evidences, start=1)]


def _dedupe_evidences_by_message(evidences: list[OpsDemandEvidence]) -> list[OpsDemandEvidence]:
    seen: set[tuple[str, str, str]] = set()
    seen_long_texts: set[str] = set()
    result: list[OpsDemandEvidence] = []
    for evidence in evidences:
        compact_text = _compact_message_text(str(evidence.message_text or ""))
        key = (
            str(evidence.display_time_text or ""),
            str(evidence.sender_name or ""),
            compact_text,
        )
        if key in seen:
            continue
        if len(compact_text) >= 8 and compact_text in seen_long_texts:
            continue
        seen.add(key)
        if len(compact_text) >= 8:
            seen_long_texts.add(compact_text)
        result.append(evidence)
    return result


def _clean_model_message_text(value: str) -> str:
    lines = [line.strip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    if len(lines) > 1:
        cleaned = [lines[0]]
        for line in lines[1:]:
            if re.match(r"^[^:：\\s]{1,12}[:：].+", line):
                continue
            cleaned.append(line)
        lines = cleaned
    return "\n".join(lines).strip()


def _compact_message_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _fill_missing_evidence_times(evidences: list[OpsDemandEvidence]) -> list[OpsDemandEvidence]:
    last_display: str | None = None
    last_time: str | None = None
    filled: list[OpsDemandEvidence] = []
    for evidence in evidences:
        if evidence.display_time_text:
            last_display = str(evidence.display_time_text)
        if evidence.message_time:
            last_time = str(evidence.message_time)
        if not evidence.display_time_text and last_display:
            evidence = replace(evidence, display_time_text=last_display)
        if not evidence.message_time and last_time:
            evidence = replace(evidence, message_time=last_time)
        filled.append(evidence)
    return filled


def _ocr_sort_key(row: dict[str, Any]) -> tuple[int, int]:
    bounds = row.get("bounds") or {}
    return (int(bounds.get("top") or 0), int(bounds.get("left") or 0))


def _extract_demand_signal(texts: list[str]) -> dict[str, Any] | None:
    normalized = _dedupe_keep_order(
        line.strip()
        for text in texts
        for line in str(text or "").splitlines()
        if line and line.strip()
    )
    if not normalized:
        return None
    demand_lines = [text for text in normalized if _is_demand_like(text)]
    if not demand_lines:
        return None
    seed = demand_lines[0]
    business_category, secondary_category = _classify_category(" ".join(demand_lines))
    demand_title = _make_demand_title(seed)
    return {
        "seed": seed,
        "business_category": business_category,
        "secondary_category": secondary_category,
        "tertiary_category": demand_title,
        "business_name": demand_title,
        "demand_title": demand_title,
        "demand_content": "\n".join(normalized),
        "confidence": 0.45,
    }


def _is_demand_like(text: str) -> bool:
    lower = text.lower()
    if re.search(r"[（(]\d+[）)]$", text):
        return False
    keywords = (
        "图",
        "banner",
        "长图",
        "巨幅",
        "初稿",
        "出不",
        "能出",
        "看看",
        "修改",
        "调整",
        "设计",
        "文案",
        "配置",
        "页面",
        "素材",
    )
    if any(keyword in lower for keyword in keywords):
        return True
    return False


def _classify_category(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("文案", "话术", "标题", "copy")):
        return "文案", "（其他）"
    if any(keyword in lowered for keyword in ("配置", "发布", "直播", "活动", "推厂")):
        return "运营", "（其他）"
    if any(keyword in lowered for keyword in ("社区", "投放", "精华贴", "氛围贴")):
        return "社区", "（其他）"
    if "banner" in lowered:
        return "设计", "banner新设计"
    if "长图" in lowered:
        return "设计", "长图新设计"
    if "巨幅" in lowered:
        return "设计", "巨幅新设计"
    if "模板" in lowered:
        return "设计", "长图套模板"
    if "图" in lowered:
        return "设计", "配图拓展"
    return "设计", "（其他）"


def _make_demand_title(text: str) -> str:
    title = text.strip()
    title = title.replace("@", "").replace("  ", " ")
    if len(title) > 40:
        title = title[:40].rstrip() + "..."
    return title or "微信群需求线索"


def _dedupe_keep_order(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _short_external_id(*parts: str) -> str:
    digest = _hash_key(*parts)
    return digest[:MAX_EXTERNAL_ID_LENGTH_COMPAT]


MAX_EXTERNAL_ID_LENGTH_COMPAT = 64


def _write_group_metadata(output_dir: Path, group: OpsWechatGroupConfig, returncode: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _group_to_dict(group)
    payload["capture_returncode"] = returncode
    payload["captured_at"] = datetime.now().isoformat(timespec="seconds")
    (output_dir / "group_config.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _record_capture_result(
    *,
    group: OpsWechatGroupConfig,
    target_date: date,
    output_dir: Path,
    status: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> int:
    screenshots = sorted(output_dir.glob("*.png"))
    run_key = _hash_key(
        "wechat_capture",
        group.source_key,
        target_date.isoformat(),
        datetime.now().isoformat(timespec="microseconds"),
    )
    meta = {
        "group_config": _group_to_dict(group),
        "returncode": returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
    }
    conn = get_crawler_app_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO wechat_capture_runs (
                    run_key, chat_id, source_app, source_type, source_key,
                    source_name, external_source_id, ops_source_context_id,
                    capture_mode, target_date, device_serial, status,
                    screenshot_dir, screenshot_count, message_count, error,
                    meta_json, finished_at
                )
                VALUES (
                    %s, NULL, 'crawler', 'wechat_group', %s,
                    %s, %s, %s,
                    'date_search', %s, NULL, %s,
                    %s, %s, 0, %s,
                    %s, NOW()
                )
                """,
                (
                    run_key,
                    group.source_key,
                    group.group_name,
                    group.group_id,
                    group.contact_context_config_id,
                    target_date,
                    status,
                    str(output_dir),
                    len(screenshots),
                    stderr[-2000:] if status != "success" else None,
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            capture_run_id = int(cursor.lastrowid)
        conn.commit()
        return capture_run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _group_to_dict(group: OpsWechatGroupConfig) -> dict[str, Any]:
    return {
        "id": group.id,
        "group_id": group.group_id,
        "group_name": group.group_name,
        "source_key": group.source_key,
        "customer_code": group.customer_code,
        "customer_id": group.customer_id,
        "customer_name": group.customer_name,
        "contact_context_config_id": group.contact_context_config_id,
        "contact_name": group.contact_name,
        "business_platform": group.business_platform,
        "status": group.status,
        "collect_enabled": group.collect_enabled,
        "sort_order": group.sort_order,
    }


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _safe_name(value: str) -> str:
    import re

    text = re.sub(r"[\\/:*?\"<>|]+", "_", value.strip())
    text = re.sub(r"\s+", " ", text)
    return text or "wechat_group"


def _hash_key(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
