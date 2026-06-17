"""WeChat demand intake workflows driven by ops_platform metadata."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from apps.finance_crawler.crawler_app.storage.ops_platform import (
    OpsWechatGroupConfig,
    list_wechat_group_configs_once,
)
from apps.finance_crawler.crawler_app.storage.db import get_conn as get_crawler_app_conn


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
    for group in groups:
        result = _capture_one_group(
            script=script,
            group=group,
            target_date=target_date,
            pages=pages,
            output_root=output_root,
            serial=serial,
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
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=max(180, 45 + max(pages, 0) * 30),
    )
    output_dir = output_root / _safe_name(group.group_name) / target_date.isoformat()
    status = "success" if completed.returncode == 0 else "error"
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
            for index, screenshot in enumerate(screenshots):
                observation_key = _hash_key(run_key, screenshot.name)
                cursor.execute(
                    """
                    INSERT INTO wechat_message_observations (
                        run_id, chat_id, screen_index, bubble_index, message_date,
                        message_type, screenshot_path, raw_json, observation_key,
                        status
                    )
                    VALUES (%s, NULL, %s, 0, %s, 'screenshot', %s, %s, %s, 'active')
                    ON DUPLICATE KEY UPDATE
                        screenshot_path = VALUES(screenshot_path),
                        raw_json = VALUES(raw_json),
                        status = VALUES(status)
                    """,
                    (
                        capture_run_id,
                        index,
                        target_date,
                        str(screenshot),
                        json.dumps({"group_config_id": group.id, "file_name": screenshot.name}, ensure_ascii=False),
                        observation_key,
                    ),
                )
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
