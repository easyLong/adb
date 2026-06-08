"""Profile-homepage trigger configuration and run orchestration."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs.client import parse_doc_url_info
from apps.finance_crawler.crawler_app.storage.profile_metrics import (
    finish_profile_trigger_run,
    get_profile_action_profile,
    get_profile_trigger_config,
    list_profile_trigger_configs,
    start_profile_trigger_run,
    upsert_profile_trigger_config,
)
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("profile_triggers")

KOL_DAILY_PROFILE_CONFIG_KEY = "kol_daily_metrics_wpvy0d"
KOL_DAILY_PROFILE_SOURCE_NAME = "kol_daily_crawl"
PROFILE_DAILY_METRICS_TASK_TYPE = "profile_daily_metrics"
KOL_DAILY_PROFILE_FIELDS = ("fans_count", "growth_count")


def ensure_default_profile_trigger_configs() -> list[dict[str, Any]]:
    """Keep built-in profile triggers aligned with runtime config."""

    resolved_doc_url = (Config.KOL_DAILY_SNAPSHOT_WRITEBACK_DOC_URL or "").strip()
    if not resolved_doc_url:
        return []
    doc = parse_doc_url_info(resolved_doc_url)
    config_id = upsert_profile_trigger_config(
        config_key=KOL_DAILY_PROFILE_CONFIG_KEY,
        doc_url=doc.base_url,
        file_id=doc.file_id,
        sheet_id=doc.sheet_id or None,
        read_range="A1:I5000",
        row_adapter="kol_daily_profile",
        source_name=KOL_DAILY_PROFILE_SOURCE_NAME,
        requested_fields=KOL_DAILY_PROFILE_FIELDS,
        action_profile_key=None,
        aggregation_policy={
            "growth_count": {"source": "previous_day_fans_count"},
        },
        schedule_time=Config.KOL_DAILY_CRAWL_TIME or "08:00",
        target_date_offset_days=0,
        scan_interval_seconds=300,
        status="active",
        description="Daily KOL homepage metrics from generated wpvy0d rows.",
        updated_by="system",
    )
    config = get_profile_trigger_config(KOL_DAILY_PROFILE_CONFIG_KEY) or {"id": config_id}
    return [config]


def run_default_kol_daily_profile_trigger(
    *,
    target_date: date | None = None,
    trigger_type: str = "scheduled",
    limit: int | None = None,
) -> dict[str, Any]:
    ensure_default_profile_trigger_configs()
    return run_profile_trigger_config(
        KOL_DAILY_PROFILE_CONFIG_KEY,
        target_date=target_date,
        trigger_type=trigger_type,
        limit=limit,
    )


def run_profile_trigger_config(
    config_key: str,
    *,
    target_date: date | None = None,
    trigger_type: str = "manual",
    limit: int | None = None,
) -> dict[str, Any]:
    config = get_profile_trigger_config(config_key)
    if not config:
        raise ValueError(f"profile trigger config not found: {config_key}")
    if str(config.get("status") or "") != "active":
        raise ValueError(f"profile trigger config is not active: {config_key}")

    effective_date = target_date or _effective_target_date(config)
    configured_action_profile_key = str(config.get("action_profile_key") or "").strip() or None
    if configured_action_profile_key:
        action_profile = get_profile_action_profile(
            action_profile_key=configured_action_profile_key,
            task_type=str(config.get("task_type") or PROFILE_DAILY_METRICS_TASK_TYPE),
            field_names=tuple(config.get("requested_fields") or KOL_DAILY_PROFILE_FIELDS),
        )
        action_profile_key = str((action_profile or {}).get("action_profile_key") or configured_action_profile_key)
    else:
        action_profile_key = None
    run_id = start_profile_trigger_run(
        trigger_config_id=int(config["id"]),
        config_key=config_key,
        trigger_type=trigger_type,
        target_date=effective_date,
        action_profile_key=action_profile_key or None,
    )
    try:
        if str(config.get("row_adapter") or "") != "kol_daily_profile":
            raise ValueError(f"unsupported profile row_adapter: {config.get('row_adapter')}")
        from apps.finance_crawler.crawler_app.workflows.kol_daily_snapshots import (
            run_kol_daily_crawl_pipeline,
        )

        summary = run_kol_daily_crawl_pipeline(
            target_date=effective_date,
            doc_url=_doc_url_for_config(config),
            limit=limit,
            source_name=str(config.get("source_name") or KOL_DAILY_PROFILE_SOURCE_NAME),
            requested_fields=tuple(config.get("requested_fields") or KOL_DAILY_PROFILE_FIELDS),
            action_profile_key=action_profile_key or None,
            trigger_config_id=int(config["id"]),
            trigger_run_id=run_id,
        )
        finish_profile_trigger_run(run_id, status="success", summary=summary)
        logger.info("profile trigger completed config=%s run=%s summary=%s", config_key, run_id, summary)
        return {"run_id": run_id, "config": _profile_trigger_summary(config), **summary}
    except Exception as exc:
        finish_profile_trigger_run(run_id, status="error", summary={}, error=str(exc))
        raise


def list_profile_triggers(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    ensure_default_profile_trigger_configs()
    return [_profile_trigger_summary(item) for item in list_profile_trigger_configs(include_disabled=include_disabled)]


def _effective_target_date(config: dict[str, Any]) -> date:
    offset_days = int(config.get("target_date_offset_days") or 0)
    return date.today() + timedelta(days=offset_days)


def _doc_url_for_config(config: dict[str, Any]) -> str:
    doc_url = str(config.get("doc_url") or "").strip()
    sheet_id = str(config.get("sheet_id") or "").strip()
    if not sheet_id or "tab=" in doc_url:
        return doc_url
    separator = "&" if "?" in doc_url else "?"
    return f"{doc_url}{separator}tab={sheet_id}"


def _profile_trigger_summary(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": config.get("id"),
        "config_key": config.get("config_key"),
        "source_type": config.get("source_type"),
        "doc_url": config.get("doc_url"),
        "file_id": config.get("file_id"),
        "sheet_id": config.get("sheet_id"),
        "read_range": config.get("read_range"),
        "row_adapter": config.get("row_adapter"),
        "source_name": config.get("source_name"),
        "task_type": config.get("task_type"),
        "requested_fields": list(config.get("requested_fields") or []),
        "action_profile_key": config.get("action_profile_key"),
        "aggregation_policy": config.get("aggregation_policy") or {},
        "schedule_time": config.get("schedule_time"),
        "target_date_offset_days": config.get("target_date_offset_days"),
        "scan_interval_seconds": config.get("scan_interval_seconds"),
        "status": config.get("status"),
        "description": config.get("description"),
    }
