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
_APP_CONFIG_TABLE = "app_config"


@dataclass(frozen=True, slots=True)
class RuntimeConfigItem:
    key: str
    value: str
    enabled: bool = True
    status: str = "active"
    description: str = ""
    secret: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeConfigDisplayItem:
    key: str
    label: str
    value: str
    description: str = ""
    enabled: bool = True
    status: str = "active"
    secret: bool = False


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
    "TENCENT_DOC_ACCESS_TOKEN": "QQ_ACCESS_TOKEN",
    "TENCENT_DOC_CLIENT_ID": "QQ_CLIENT_ID",
    "TENCENT_DOC_CLIENT_SECRET": "QQ_CLIENT_SECRET",
    "TENCENT_DOC_OPEN_ID": "QQ_OPEN_ID",
    "TENCENT_DOC_TOKEN_URL": "QQ_TOKEN_URL",
    "FETCH_INTERVAL_MINUTES": "FETCH_INTERVAL_MINUTES",
    "FETCH_LIMIT": "FETCH_LIMIT",
    "CHECK_INTERVAL_MINUTES": "CHECK_INTERVAL_MINUTES",
    "DETAIL_TIME": "DETAIL_TIME",
    "DETAIL_INTERVAL_MINUTES": "DETAIL_INTERVAL_MINUTES",
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
    "APP_OPEN_RECOVERY_RETRIES": "APP_OPEN_RECOVERY_RETRIES",
    "APP_RESTART_WAIT": "APP_RESTART_WAIT",
    "CRAWL_ACTIVE_START": "CRAWL_ACTIVE_START",
    "CRAWL_ACTIVE_END": "CRAWL_ACTIVE_END",
    "CRAWL_MAX_TASK_SECONDS": "CRAWL_MAX_TASK_SECONDS",
    "TASK_RUNNING_TIMEOUT_MINUTES": "TASK_RUNNING_TIMEOUT_MINUTES",
}

_DATA_SOURCE_KEYS: tuple[str, ...] = (
    "TENCENT_DOC_URL",
    "EXCEL_DETAIL_INPUT_PATH",
    "SINGLE_TEST_LINK",
)

_OPENAPI_CONFIG_KEYS: tuple[str, ...] = (
    "TENCENT_DOC_CLIENT_ID",
    "TENCENT_DOC_OPEN_ID",
    "TENCENT_DOC_ACCESS_TOKEN",
    "TENCENT_DOC_CLIENT_SECRET",
    "TENCENT_DOC_TOKEN_URL",
)

_APP_BEHAVIOR_CONFIG_KEYS: tuple[str, ...] = (
    "APP_OPEN_RECOVERY_RETRIES",
    "APP_RESTART_WAIT",
    "DETAIL_INTERVAL_MINUTES",
    "TASK_RUNNING_TIMEOUT_MINUTES",
)

_APP_CONFIG_KEYS: tuple[str, ...] = _OPENAPI_CONFIG_KEYS + _APP_BEHAVIOR_CONFIG_KEYS

_SECRET_KEYS = {
    "TENCENT_DOC_CLIENT_ID",
    "TENCENT_DOC_OPEN_ID",
    "TENCENT_DOC_ACCESS_TOKEN",
    "TENCENT_DOC_CLIENT_SECRET",
}

_DESCRIPTIONS: dict[str, str] = {
    "TENCENT_DOC_URL": "在线腾讯文档链接。配置后调度器会持续读取目标文档。",
    "EXCEL_DETAIL_INPUT_PATH": "本地 Excel 输入文件。用于临时跑批，执行一次 excel-detail 即结束。",
    "SINGLE_TEST_LINK": "单条测试链接。用于临时测试，执行一次 link-detail 后自动停用。",
    "TENCENT_DOC_CLIENT_ID": "腾讯文档 OpenAPI Client-Id。",
    "TENCENT_DOC_OPEN_ID": "腾讯文档 OpenAPI Open-Id，对应授权账号身份。",
    "TENCENT_DOC_ACCESS_TOKEN": "腾讯文档 OpenAPI Access-Token。可选；配置后优先使用。",
    "TENCENT_DOC_CLIENT_SECRET": "腾讯文档 OpenAPI Client-Secret。未配置 Access-Token 时用于换 token。",
    "TENCENT_DOC_TOKEN_URL": "腾讯文档 OpenAPI token 换取地址。",
}

_DISPLAY_LABELS: dict[str, str] = {
    "TENCENT_DOC_URL": "在线文档链接",
    "EXCEL_DETAIL_INPUT_PATH": "本地 Excel 路径",
    "SINGLE_TEST_LINK": "单条测试链接",
    "TENCENT_DOC_CLIENT_ID": "Client-Id",
    "TENCENT_DOC_OPEN_ID": "Open-Id",
    "TENCENT_DOC_ACCESS_TOKEN": "Access-Token",
    "TENCENT_DOC_CLIENT_SECRET": "Client-Secret",
    "TENCENT_DOC_TOKEN_URL": "Token URL",
}

_DESCRIPTIONS.update(
    {
        "APP_OPEN_RECOVERY_RETRIES": "Retry count for transient blank/update/stuck app pages after force-stopping the target app.",
        "APP_RESTART_WAIT": "Seconds to wait after force-stopping the target app before reopening the link.",
        "DETAIL_INTERVAL_MINUTES": "Minutes between due detail-crawl queue scans. Each scan consumes tasks with scheduled_at <= now.",
        "TASK_RUNNING_TIMEOUT_MINUTES": "Minutes before a running task is considered abandoned and returned to retry/final state.",
    }
)
_DISPLAY_LABELS.update(
    {
        "APP_OPEN_RECOVERY_RETRIES": "App recovery retries",
        "APP_RESTART_WAIT": "App restart wait",
        "DETAIL_INTERVAL_MINUTES": "Detail queue interval",
        "TASK_RUNNING_TIMEOUT_MINUTES": "Running task timeout",
    }
)


def ensure_runtime_config_defaults(cursor) -> None:
    _ensure_data_source_defaults(cursor)
    _ensure_app_config_defaults(cursor)


def _ensure_data_source_defaults(cursor) -> None:
    rows = [
        (
            "TENCENT_DOC_URL",
            Config.QQ_DOC_URL,
            "active" if Config.QQ_DOC_URL else "unavailable",
            _DESCRIPTIONS["TENCENT_DOC_URL"],
        ),
        (
            "EXCEL_DETAIL_INPUT_PATH",
            Config.EXCEL_DETAIL_INPUT_PATH,
            "active" if Config.EXCEL_DETAIL_INPUT_PATH else "unavailable",
            _DESCRIPTIONS["EXCEL_DETAIL_INPUT_PATH"],
        ),
        ("SINGLE_TEST_LINK", "", "unavailable", _DESCRIPTIONS["SINGLE_TEST_LINK"]),
    ]
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


def _ensure_app_config_defaults(cursor) -> None:
    rows = []
    for key in _APP_CONFIG_KEYS:
        default = _default_value_for_key(key)
        if key == "TENCENT_DOC_TOKEN_URL" and not default:
            default = "https://docs.qq.com/oauth/v2/token"
        rows.append(
            (
                key,
                default,
                "active" if default else "unavailable",
                1 if key in _SECRET_KEYS else 0,
                _DESCRIPTIONS.get(key, ""),
            )
        )
    cursor.executemany(
        """
        INSERT INTO app_config (config_key, config_value, status, is_secret, description, updated_by)
        VALUES (%s, %s, %s, %s, %s, 'system')
        ON DUPLICATE KEY UPDATE
            is_secret = VALUES(is_secret),
            description = VALUES(description)
        """,
        rows,
    )


def load_runtime_config() -> dict[str, str]:
    values = _load_data_source_values()
    values.update(_derive_tencent_doc_keys(values))
    values.update(_load_app_config_values())
    apply_runtime_config(values)
    if values:
        logger.info("runtime config loaded: %s", ", ".join(sorted(values)))
    return values


def _load_data_source_values() -> dict[str, str]:
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
                """,
                _DATA_SOURCE_KEYS,
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    return {
        str(row["source_key"]): "" if row["data_source_link"] is None else str(row["data_source_link"])
        for row in rows
    }


def _load_app_config_values() -> dict[str, str]:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT config_key, config_value
                FROM {_APP_CONFIG_TABLE}
                WHERE status = 'active'
                  AND config_key IN ({", ".join(["%s"] * len(_APP_CONFIG_KEYS))})
                  AND config_value IS NOT NULL
                  AND config_value <> ''
                """,
                _APP_CONFIG_KEYS,
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    return {str(row["config_key"]): str(row["config_value"]) for row in rows}


def apply_runtime_config(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in _CONFIG_ATTRS:
            continue
        _apply_value(key, value)

    if values.get("TENCENT_DOC_URL"):
        _apply_tencent_doc_url(values["TENCENT_DOC_URL"])


def list_runtime_config() -> list[RuntimeConfigItem]:
    return _list_data_source_config() + _list_app_config()


def _list_data_source_config() -> list[RuntimeConfigItem]:
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
                """,
                _DATA_SOURCE_KEYS,
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


def _list_app_config() -> list[RuntimeConfigItem]:
    from apps.finance_crawler.storage.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT config_key, config_value, status, description, is_secret
                FROM {_APP_CONFIG_TABLE}
                WHERE config_key IN ({", ".join(["%s"] * len(_APP_CONFIG_KEYS))})
                ORDER BY config_key
                """,
                _APP_CONFIG_KEYS,
            )
            return [
                RuntimeConfigItem(
                    key=str(row["config_key"]),
                    value="" if row["config_value"] is None else str(row["config_value"]),
                    enabled=str(row["status"]) == "active",
                    status=str(row["status"] or ""),
                    description=str(row.get("description") or ""),
                    secret=bool(row.get("is_secret")),
                )
                for row in cursor.fetchall()
            ]
    finally:
        conn.close()


def grouped_runtime_config() -> list[RuntimeConfigDisplayGroup]:
    values = {item.key: item for item in list_runtime_config()}
    return [
        RuntimeConfigDisplayGroup(
            title="任务源配置",
            description="数据从哪里来。",
            items=tuple(_display_items(values, _DATA_SOURCE_KEYS)),
        ),
        RuntimeConfigDisplayGroup(
            title="腾讯文档 OpenAPI",
            description="读写腾讯文档使用的 OpenAPI 身份；MySQL 连接不放这里。",
            items=tuple(_display_items(values, _OPENAPI_CONFIG_KEYS)),
        ),
        RuntimeConfigDisplayGroup(
            title="App 采集和调度保护",
            description="手机 App 白屏、系统更新弹窗、卡死时的自动恢复策略，以及到期详情任务轮询间隔。",
            items=tuple(_display_items(values, _APP_BEHAVIOR_CONFIG_KEYS)),
        ),
    ]


def _display_items(values: dict[str, RuntimeConfigItem], keys: tuple[str, ...]) -> list[RuntimeConfigDisplayItem]:
    items = []
    for key in keys:
        source = values.get(key)
        raw_value = source.value if source else _default_value_for_key(key)
        items.append(
            RuntimeConfigDisplayItem(
                key=key,
                label=_DISPLAY_LABELS.get(key, key),
                value=_format_display_value(key, raw_value, secret=bool(source.secret if source else key in _SECRET_KEYS)),
                description=(source.description if source else "") or _DESCRIPTIONS.get(key, ""),
                enabled=source.enabled if source else bool(raw_value),
                status=source.status if source else ("active" if raw_value else "unavailable"),
                secret=bool(source.secret if source else key in _SECRET_KEYS),
            )
        )
    return items


def format_runtime_config_for_cli() -> str:
    lines = [
        "运行时配置",
        "MySQL 连接只从项目根目录 .env / 环境变量读取；其它运行配置从 MySQL 配置表读取。",
    ]
    for group in grouped_runtime_config():
        lines.append("")
        lines.append(f"[{group.title}]")
        lines.append(group.description)
        for item in group.items:
            state = "" if item.enabled else "（未启用）"
            lines.append(f"  {item.label}: {item.value}{state}")
    return "\n".join(lines)


def set_runtime_config(values: dict[str, str], *, updated_by: str = "cli") -> None:
    source_values: dict[str, str] = {}
    app_values: dict[str, str] = {}
    for key, value in values.items():
        if key in _DATA_SOURCE_KEYS:
            source_values[key] = value
            continue
        if key in _APP_CONFIG_KEYS:
            app_values[key] = value
            continue
        raise ValueError(
            f"unsupported config key: {key}; source keys: {', '.join(_DATA_SOURCE_KEYS)}; "
            f"app keys: {', '.join(_APP_CONFIG_KEYS)}"
        )

    if source_values:
        _set_data_source_config(source_values, updated_by=updated_by)
    if app_values:
        _set_app_config(app_values, updated_by=updated_by)

    apply_runtime_config(values | _derive_tencent_doc_keys(values))


def _set_data_source_config(values: dict[str, str], *, updated_by: str) -> None:
    from apps.finance_crawler.storage.db import get_conn

    rows = []
    for key, value in values.items():
        if key == "TENCENT_DOC_URL" and value:
            parse_doc_url(value)
        rows.append((key, value, _DESCRIPTIONS.get(key, ""), updated_by))

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


def _set_app_config(values: dict[str, str], *, updated_by: str) -> None:
    from apps.finance_crawler.storage.db import get_conn

    rows = [
        (
            key,
            value,
            "active" if value.strip() else "unavailable",
            1 if key in _SECRET_KEYS else 0,
            _DESCRIPTIONS.get(key, ""),
            updated_by,
        )
        for key, value in values.items()
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(
                f"""
                INSERT INTO {_APP_CONFIG_TABLE} (config_key, config_value, status, is_secret, description, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    config_value = VALUES(config_value),
                    status = VALUES(status),
                    is_secret = VALUES(is_secret),
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


def _format_display_value(key: str, value: str, *, secret: bool = False) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return "未配置"
    if secret:
        if len(cleaned) <= 8:
            return "***"
        return f"{cleaned[:4]}***{cleaned[-4:]}"
    return cleaned
