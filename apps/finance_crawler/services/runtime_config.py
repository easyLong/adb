"""Database-backed runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.integrations.tencent_docs.client import parse_doc_url
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("runtime_config")

_DATA_SOURCE_TABLE = "data_source_links"


@dataclass(frozen=True, slots=True)
class RuntimeConfigItem:
    key: str
    value: str
    enabled: bool = True
    status: str = "active"
    description: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeConfigDisplayItem:
    key: str
    label: str
    value: str
    description: str = ""
    enabled: bool = True
    status: str = "active"


@dataclass(frozen=True, slots=True)
class RuntimeConfigDisplayGroup:
    title: str
    description: str
    items: tuple[RuntimeConfigDisplayItem, ...]


_CONFIG_ATTRS: dict[str, str] = {
    "TENCENT_DOC_URL": "QQ_DOC_URL",
    "TENCENT_DOC_FILE_ID": "QQ_FILE_ID",
    "TENCENT_DOC_SHEET_ID": "QQ_SHEET_ID",
    "TENCENT_DOC_READ_RANGE": "QQ_READ_RANGE",
    "TENCENT_DOC_SCAN_MODE": "QQ_SCAN_MODE",
    "TENCENT_DOC_SCAN_DATE": "QQ_SCAN_DATE",
    "TENCENT_DOC_SHEET_TITLE_FILTER": "QQ_SHEET_TITLE_FILTER",
    "FETCH_INTERVAL_MINUTES": "FETCH_INTERVAL_MINUTES",
    "FETCH_LIMIT": "FETCH_LIMIT",
    "CHECK_INTERVAL_MINUTES": "CHECK_INTERVAL_MINUTES",
    "DETAIL_TIME": "DETAIL_TIME",
    "REPORT_TIME": "REPORT_TIME",
    "EXCEL_DETAIL_INPUT_PATH": "EXCEL_DETAIL_INPUT_PATH",
    "EXCEL_DETAIL_OUTPUT_PATH": "EXCEL_DETAIL_OUTPUT_PATH",
    "EXCEL_DETAIL_RESULT_JSONL_PATH": "EXCEL_DETAIL_RESULT_JSONL_PATH",
    "EXCEL_DETAIL_SHEET_NAME": "EXCEL_DETAIL_SHEET_NAME",
    "EXCEL_DETAIL_SOURCE_FILTER": "EXCEL_DETAIL_SOURCE_FILTER",
    "EXCEL_DETAIL_ONLY_EMPTY": "EXCEL_DETAIL_ONLY_EMPTY",
    "WRITEBACK_EXCEL_PATH": "WRITEBACK_EXCEL_PATH",
    "WRITEBACK_EXCEL_SAVE_AS": "WRITEBACK_EXCEL_SAVE_AS",
    "WRITEBACK_EXCEL_SHEET_NAME": "WRITEBACK_EXCEL_SHEET_NAME",
    "DEVICE_SERIAL": "DEVICE_SERIAL",
    "CRAWL_ACTIVE_START": "CRAWL_ACTIVE_START",
    "CRAWL_ACTIVE_END": "CRAWL_ACTIVE_END",
    "CRAWL_MAX_TASK_SECONDS": "CRAWL_MAX_TASK_SECONDS",
}

_RUNTIME_TABLE_KEYS: tuple[str, ...] = (
    "TENCENT_DOC_URL",
    "EXCEL_DETAIL_INPUT_PATH",
    "SINGLE_TEST_LINK",
)


_DESCRIPTIONS: dict[str, str] = {
    "TENCENT_DOC_URL": "在线腾讯文档链接。配置后调度器会持续读取当天日期的工作表。",
    "TENCENT_DOC_FILE_ID": "系统从在线文档链接自动解析出的文档 ID，一般不需要手动修改。",
    "TENCENT_DOC_SHEET_ID": "系统从在线文档链接自动解析出的工作表 tab，一般不需要手动修改。",
    "TENCENT_DOC_READ_RANGE": "在线文档每次读取的表格范围。",
    "TENCENT_DOC_SCAN_MODE": "在线文档扫描模式。默认 today，只处理当天日期的工作表。",
    "TENCENT_DOC_SCAN_DATE": "补扫日期，例如 2026-05-27。留空表示按当天日期处理。",
    "TENCENT_DOC_SHEET_TITLE_FILTER": "工作表标题过滤词。只有 filter 模式才需要。",
    "FETCH_INTERVAL_MINUTES": "在线文档轮询间隔，单位分钟。",
    "EXCEL_DETAIL_INPUT_PATH": "本地 Excel 输入文件。用于临时跑批，执行一次 excel-detail 即结束。",
    "SINGLE_TEST_LINK": "单条测试链接。用于临时测试，执行一次 link-detail 后自动停用。",
    "EXCEL_DETAIL_OUTPUT_PATH": "本地 Excel 输出文件。留空表示写回原文件。",
    "EXCEL_DETAIL_SOURCE_FILTER": "本地 Excel 链路筛选，例如 alipay,antfortune,tenpay。留空表示全部支持链路。",
}

_DISPLAY_LABELS: dict[str, str] = {
    "TENCENT_DOC_URL": "在线文档链接",
    "EXCEL_DETAIL_INPUT_PATH": "本地 Excel 路径",
    "SINGLE_TEST_LINK": "单条测试链接",
}


def ensure_runtime_config_defaults(cursor) -> None:
    rows = [
        ("TENCENT_DOC_URL", Config.QQ_DOC_URL, "active", _DESCRIPTIONS["TENCENT_DOC_URL"]),
        (
            "EXCEL_DETAIL_INPUT_PATH",
            Config.EXCEL_DETAIL_INPUT_PATH,
            "active" if Config.EXCEL_DETAIL_INPUT_PATH else "unavailable",
            _DESCRIPTIONS["EXCEL_DETAIL_INPUT_PATH"],
        ),
        ("SINGLE_TEST_LINK", "", "unavailable", _DESCRIPTIONS["SINGLE_TEST_LINK"]),
    ]
    cursor.execute(
        """
        DELETE FROM data_source_links
        WHERE source_key NOT IN (%s, %s, %s)
        """,
        _RUNTIME_TABLE_KEYS,
    )
    cursor.executemany(
        """
        INSERT INTO data_source_links (source_key, data_source_link, status, description, updated_by)
        VALUES (%s, %s, %s, %s, 'system')
        ON DUPLICATE KEY UPDATE
            description = VALUES(description)
        """,
        rows,
    )
    cursor.execute(
        """
        UPDATE data_source_links
        SET status = 'unavailable'
        WHERE source_key IN ('EXCEL_DETAIL_INPUT_PATH', 'SINGLE_TEST_LINK')
          AND (data_source_link IS NULL OR data_source_link = '')
        """
    )


def load_runtime_config() -> dict[str, str]:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_key, data_source_link
                FROM data_source_links
                WHERE status = 'active'
                  AND source_key IN (%s, %s, %s)
                """
                ,
                _RUNTIME_TABLE_KEYS,
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    values = {
        str(row["source_key"]): ""
        if row["data_source_link"] is None
        else str(row["data_source_link"])
        for row in rows
    }
    values.update(_derive_tencent_doc_keys(values))
    apply_runtime_config(values)
    if values:
        logger.info("runtime config loaded: %s", ", ".join(sorted(values)))
    return values


def apply_runtime_config(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in _CONFIG_ATTRS:
            continue
        _apply_value(key, value)

    if values.get("TENCENT_DOC_URL"):
        _apply_tencent_doc_url(values["TENCENT_DOC_URL"])


def list_runtime_config() -> list[RuntimeConfigItem]:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_key, data_source_link, status, description
                FROM data_source_links
                WHERE source_key IN (%s, %s, %s)
                ORDER BY source_key
                """
                ,
                _RUNTIME_TABLE_KEYS,
            )
            return [
                RuntimeConfigItem(
                    key=str(row["source_key"]),
                    value="" if row["data_source_link"] is None else str(row["data_source_link"]),
                    enabled=str(row["status"]) == "active",
                    status=str(row["status"] or ""),
                    description=str(row.get("description") or ""),
                )
                for row in cursor.fetchall()
            ]
    finally:
        conn.close()


def grouped_runtime_config() -> list[RuntimeConfigDisplayGroup]:
    values = {item.key: item for item in list_runtime_config()}
    items = []
    for key in _RUNTIME_TABLE_KEYS:
        source = values.get(key)
        if source:
            raw_value = source.value
            enabled = source.enabled
            item_description = source.description or _DESCRIPTIONS.get(key, "")
        else:
            raw_value = _default_value_for_key(key)
            enabled = True
            status = "active"
            item_description = _DESCRIPTIONS.get(key, "")
        status = source.status if source else status
        items.append(
            RuntimeConfigDisplayItem(
                key=key,
                label=_DISPLAY_LABELS.get(key, key),
                value=_format_display_value(key, raw_value),
                description=item_description,
                enabled=enabled,
                status=status,
            )
        )
    return [
        RuntimeConfigDisplayGroup(
            title="任务源配置",
            description="在线文档长期监测；本地 Excel 和单条测试链接执行完会自动停用。",
            items=tuple(items),
        )
    ]


def format_runtime_config_for_cli() -> str:
    lines = [
        "数据源链接配置",
        "data_source_links 只保留数据源入口，其它参数由程序默认和自动解析。",
    ]
    for group in grouped_runtime_config():
        lines.append("")
        lines.append(f"[{group.title}]")
        lines.append(group.description)
        for item in group.items:
            state = "" if item.enabled else "（已停用）"
            lines.append(f"  {item.label}: {item.value}{state}")
    return "\n".join(lines)


def set_runtime_config(values: dict[str, str], *, updated_by: str = "cli") -> None:
    from apps.finance_crawler.storage.db import get_conn

    rows = []
    for key, value in values.items():
        if key not in _RUNTIME_TABLE_KEYS:
            raise ValueError(
                f"{_DATA_SOURCE_TABLE} only supports source keys: {', '.join(_RUNTIME_TABLE_KEYS)}"
            )
        if key == "TENCENT_DOC_URL" and value:
            parse_doc_url(value)
        rows.append((key, value, _DESCRIPTIONS.get(key, ""), updated_by))

    if not rows:
        return

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO data_source_links (source_key, data_source_link, status, description, updated_by)
                VALUES (%s, %s, 'active', %s, %s)
                ON DUPLICATE KEY UPDATE
                    data_source_link = VALUES(data_source_link),
                    status = 'active',
                    description = COALESCE(NULLIF(VALUES(description), ''), description),
                    updated_by = VALUES(updated_by)
                """,
                rows,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    apply_runtime_config(values | _derive_tencent_doc_keys(values))


def get_data_source_link(source_key: str, *, require_enabled: bool = True) -> RuntimeConfigItem | None:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            enabled_clause = "AND status = 'active'" if require_enabled else ""
            cursor.execute(
                f"""
                SELECT source_key, data_source_link, status, description
                FROM data_source_links
                WHERE source_key = %s
                  {enabled_clause}
                LIMIT 1
                """,
                (source_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return RuntimeConfigItem(
                key=str(row["source_key"]),
                value="" if row["data_source_link"] is None else str(row["data_source_link"]),
                enabled=str(row["status"]) == "active",
                status=str(row["status"] or ""),
                description=str(row.get("description") or ""),
            )
    finally:
        conn.close()


def disable_data_source(source_key: str, *, updated_by: str = "system") -> None:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE data_source_links
                SET status = 'unavailable',
                    updated_by = %s
                WHERE source_key = %s
                """,
                (updated_by, source_key),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _apply_value(key: str, value: str) -> None:
    attr = _CONFIG_ATTRS[key]
    current = getattr(Config, attr)
    converted = _convert_value(value, current)
    setattr(Config, attr, converted)
    os.environ[key] = value


def _apply_tencent_doc_url(value: str) -> None:
    try:
        doc = parse_doc_url(value)
    except Exception as exc:
        logger.warning("runtime TENCENT_DOC_URL is not a Tencent Docs sheet URL: %s", exc)
        return
    Config.QQ_DOC_URL = value
    Config.QQ_FILE_ID = doc.file_id
    Config.QQ_SHEET_ID = doc.sheet_id
    os.environ["TENCENT_DOC_FILE_ID"] = doc.file_id
    os.environ["TENCENT_DOC_SHEET_ID"] = doc.sheet_id


def _derive_tencent_doc_keys(values: dict[str, str]) -> dict[str, str]:
    url = values.get("TENCENT_DOC_URL", "").strip()
    if not url:
        return {}
    try:
        doc = parse_doc_url(url)
    except Exception as exc:
        logger.warning("runtime TENCENT_DOC_URL is not a Tencent Docs sheet URL: %s", exc)
        return {}
    return {
        "TENCENT_DOC_FILE_ID": doc.file_id,
        "TENCENT_DOC_SHEET_ID": doc.sheet_id,
    }


def _convert_value(value: str, current: Any) -> Any:
    if isinstance(current, bool):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value) if value.strip() else 0
    if isinstance(current, float):
        return float(value) if value.strip() else 0.0
    return value


def _default_value_for_key(key: str) -> str:
    attr = _CONFIG_ATTRS.get(key)
    if not attr:
        return ""
    return str(getattr(Config, attr, ""))


def _format_display_value(key: str, value: str) -> str:
    cleaned = (value or "").strip()
    if key == "EXCEL_DETAIL_OUTPUT_PATH":
        return cleaned or "未配置，写回原文件"
    if key == "EXCEL_DETAIL_SOURCE_FILTER":
        return cleaned or "全部支持链路"
    if not cleaned:
        return "未配置"
    if key == "TENCENT_DOC_SCAN_MODE":
        return {
            "today": "当天工作表（推荐）",
            "single": "只读取链接里的单个工作表",
            "filter": "按标题过滤读取",
            "all": "读取全部工作表",
        }.get(cleaned, cleaned)
    if key == "TENCENT_DOC_SCAN_DATE":
        return f"{cleaned}（补扫指定日期）"
    if key == "FETCH_INTERVAL_MINUTES":
        return f"{cleaned} 分钟"
    return cleaned
