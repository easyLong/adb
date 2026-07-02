"""Direct writer for the ops_platform AI demand intake tables."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from uuid import uuid4

import pymysql
import pymysql.cursors

from apps.finance_crawler.crawler_app.settings import ops_platform_database_settings
from apps.finance_crawler.storage.mysql_resilience import connect_with_retry
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("ops_platform_intake")

MAX_EXTERNAL_ID_LENGTH = 64


def make_source_key(source_type: str, source_name: str, external_source_id: str | None = None) -> str:
    identity = external_source_id or source_name
    return hashlib.sha256(f"{source_type}:{identity}".encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OpsDemandEvidence:
    external_evidence_id: str
    evidence_order: int = 100
    message_time: datetime | date | str | None = None
    display_time_text: str | None = None
    sender_name: str | None = None
    message_text: str | None = None
    screenshot_path: str | None = None
    evidence_reason: str | None = None


@dataclass(frozen=True, slots=True)
class OpsDemandCandidate:
    external_candidate_id: str
    external_capture_run_id: str | None = None
    external_source_key: str | None = None
    external_chat_id: str | None = None
    source_chat_name: str | None = None
    raw_customer_name: str | None = None
    raw_owner_name: str | None = None
    raw_business_platform: str | None = None
    business_category: str | None = None
    secondary_category: str | None = None
    tertiary_category: str | None = None
    start_time: datetime | date | str | None = None
    deadline: datetime | date | str | None = None
    business_name: str | None = None
    demand_title: str | None = None
    demand_content: str | None = None
    confidence: float | None = None
    status: str = "pending"
    match_suggestion: str | None = None
    matched_customer_code: str | None = None
    matched_customer_id: str | None = None
    matched_contact_context_id: str | None = None
    matched_business_platform: str | None = None
    match_confidence: float | None = None
    match_reason: str | None = None
    source_app: str = "crawler"
    created_at: datetime | date | str | None = None
    evidences: list[OpsDemandEvidence] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class OpsSourceContactContext:
    id: str
    source_app: str
    source_type: str
    source_key: str
    source_name: str
    contact_context_config_id: str
    customer_id: str
    contact_name: str
    business_platform: str | None = None
    external_source_id: str | None = None
    status: str = "active"
    is_primary: bool = True
    priority: int = 100


@dataclass(frozen=True, slots=True)
class OpsWechatGroupConfig:
    id: str
    group_id: str | None
    group_name: str
    source_key: str
    customer_code: str
    customer_name: str
    customer_id: str | None = None
    contact_context_config_id: str | None = None
    contact_name: str | None = None
    business_platform: str | None = None
    status: str = "active"
    collect_enabled: bool = True
    sort_order: int = 100


def _connect(*, database: str | None = None) -> pymysql.connections.Connection:
    settings = ops_platform_database_settings()
    kwargs = {
        "host": settings.host,
        "port": settings.port,
        "user": settings.user,
        "password": settings.password,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": settings.connect_timeout,
        "read_timeout": settings.read_timeout,
        "write_timeout": settings.write_timeout,
        "autocommit": False,
    }
    if database:
        kwargs["db"] = database
    return connect_with_retry(
        pymysql.connect,
        kwargs=kwargs,
        label="ops_platform",
        attempts=settings.connect_retries,
        retry_delay=settings.connect_retry_delay,
        retry_max_delay=settings.connect_retry_max_delay,
    )


def get_conn() -> pymysql.connections.Connection:
    settings = ops_platform_database_settings()
    return _connect(database=settings.database)


def ensure_database() -> None:
    settings = ops_platform_database_settings()
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.database}` "
                "DEFAULT CHARACTER SET utf8mb4 "
                "DEFAULT COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


def init_ops_platform_intake_tables() -> None:
    ensure_database()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_ops_platform_intake_tables(cursor)
        conn.commit()
        logger.info("ops_platform demand intake tables initialized")
    except Exception:
        conn.rollback()
        logger.exception("ops_platform demand intake table initialization failed")
        raise
    finally:
        conn.close()


def ensure_ops_platform_intake_tables(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS demand_intake_candidates (
            id CHAR(36) NOT NULL,
            source_app VARCHAR(32) NOT NULL DEFAULT 'crawler',
            external_candidate_id VARCHAR(64) NULL,
            external_capture_run_id VARCHAR(64) NULL,
            external_source_key CHAR(64) NULL,
            external_chat_id VARCHAR(64) NULL,
            source_chat_name VARCHAR(255) NULL,
            raw_customer_name VARCHAR(128) NULL,
            raw_owner_name VARCHAR(255) NULL,
            raw_business_platform VARCHAR(64) NULL,
            business_category VARCHAR(64) NULL,
            secondary_category VARCHAR(64) NULL,
            tertiary_category VARCHAR(64) NULL,
            start_time DATETIME NULL,
            deadline DATETIME NULL,
            business_name VARCHAR(255) NULL,
            demand_title VARCHAR(255) NULL,
            demand_content LONGTEXT NULL,
            confidence DECIMAL(8,4) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            match_suggestion TEXT NULL,
            matched_customer_code VARCHAR(32) NULL,
            matched_customer_id CHAR(36) NULL,
            matched_contact_context_id CHAR(36) NULL,
            matched_business_platform VARCHAR(64) NULL,
            match_confidence DECIMAL(8,4) NULL,
            match_reason VARCHAR(500) NULL,
            confirmed_requirement_id CHAR(36) NULL,
            confirmed_task_id CHAR(36) NULL,
            confirmed_at DATETIME NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            deleted_at DATETIME NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uk_demand_intake_external (source_app, external_candidate_id),
            KEY idx_demand_intake_status_created (status, created_at),
            KEY idx_demand_intake_capture (source_app, external_capture_run_id),
            KEY idx_demand_intake_source_key (source_app, external_source_key),
            KEY idx_demand_intake_external_chat (source_app, external_chat_id),
            KEY idx_demand_intake_matched_contact (matched_contact_context_id),
            KEY idx_demand_intake_confirmed_requirement (confirmed_requirement_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    _add_column_if_missing(cursor, "demand_intake_candidates", "demand_content", "LONGTEXT NULL")
    _add_column_if_missing(cursor, "demand_intake_candidates", "external_capture_run_id", "VARCHAR(64) NULL")
    _add_column_if_missing(cursor, "demand_intake_candidates", "external_source_key", "CHAR(64) NULL")
    _add_column_if_missing(cursor, "demand_intake_candidates", "matched_customer_code", "VARCHAR(32) NULL")
    _add_index_if_missing(cursor, "demand_intake_candidates", "idx_demand_intake_capture", "source_app, external_capture_run_id")
    _add_index_if_missing(cursor, "demand_intake_candidates", "idx_demand_intake_source_key", "source_app, external_source_key")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS demand_candidate_evidence (
            id CHAR(36) NOT NULL,
            candidate_id CHAR(36) NOT NULL,
            external_evidence_id VARCHAR(64) NULL,
            evidence_order INT NOT NULL DEFAULT 100,
            message_time DATETIME NULL,
            display_time_text VARCHAR(64) NULL,
            sender_name VARCHAR(128) NULL,
            message_text TEXT NULL,
            screenshot_path VARCHAR(500) NULL,
            evidence_reason TEXT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uk_demand_evidence_external (candidate_id, external_evidence_id),
            KEY idx_demand_evidence_candidate_order (candidate_id, evidence_order),
            KEY idx_demand_evidence_external (external_evidence_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def upsert_ops_demand_candidate(conn, candidate: OpsDemandCandidate) -> str:
    """Upsert one AI demand candidate and its evidence rows into ops_platform."""

    if not candidate.external_candidate_id:
        raise ValueError("external_candidate_id is required for idempotent ops write")
    _validate_external_id("external_candidate_id", candidate.external_candidate_id)
    if candidate.external_capture_run_id:
        _validate_external_id("external_capture_run_id", candidate.external_capture_run_id)
    if candidate.external_source_key:
        _validate_external_id("external_source_key", candidate.external_source_key)

    with conn.cursor() as cursor:
        candidate_id = _upsert_candidate(cursor, candidate)
        if not _candidate_review_locked(cursor, candidate_id):
            _replace_evidences(cursor, candidate_id, candidate.evidences)
    return candidate_id


def upsert_ops_demand_candidate_once(candidate: OpsDemandCandidate) -> str:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_ops_platform_intake_tables(cursor)
        candidate_id = upsert_ops_demand_candidate(conn, candidate)
        conn.commit()
        return candidate_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_source_contact_context(
    conn,
    *,
    source_key: str,
    source_app: str = "crawler",
    source_type: str = "wechat_group",
) -> OpsSourceContactContext | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                source.id,
                source.source_app,
                source.source_type,
                source.source_key,
                source.source_name,
                source.external_source_id,
                source.contact_context_config_id,
                source.status,
                source.is_primary,
                source.priority,
                context.customer_id,
                context.contact_name,
                context.business_platform
            FROM source_contact_contexts source
            JOIN contact_context_configs context
              ON context.id = source.contact_context_config_id
             AND context.deleted_at IS NULL
             AND context.status = 'active'
            WHERE source.source_app = %s
              AND source.source_type = %s
              AND source.source_key = %s
              AND source.status = 'active'
              AND source.deleted_at IS NULL
            ORDER BY source.is_primary DESC, source.priority ASC, source.updated_at DESC
            LIMIT 1
            """,
            (source_app, source_type, source_key),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return OpsSourceContactContext(
        id=str(row["id"]),
        source_app=str(row["source_app"]),
        source_type=str(row["source_type"]),
        source_key=str(row["source_key"]),
        source_name=str(row["source_name"]),
        external_source_id=row.get("external_source_id"),
        contact_context_config_id=str(row["contact_context_config_id"]),
        customer_id=str(row["customer_id"]),
        contact_name=str(row["contact_name"]),
        business_platform=row.get("business_platform"),
        status=str(row.get("status") or "active"),
        is_primary=bool(row.get("is_primary")),
        priority=int(row.get("priority") or 100),
    )


def upsert_source_contact_context(
    conn,
    *,
    source_name: str,
    contact_context_config_id: str,
    source_app: str = "crawler",
    source_type: str = "wechat_group",
    source_key: str | None = None,
    external_source_id: str | None = None,
    status: str = "active",
    is_primary: bool = True,
    priority: int = 100,
    match_method: str | None = "manual",
    remark: str | None = None,
) -> str:
    resolved_source_key = source_key or make_source_key(source_type, source_name, external_source_id)
    _validate_external_id("source_key", resolved_source_key)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO source_contact_contexts (
                id, source_app, source_type, source_key, source_name,
                external_source_id, contact_context_config_id, status,
                is_primary, priority, match_method, remark, first_seen_at, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                source_name = VALUES(source_name),
                external_source_id = VALUES(external_source_id),
                status = VALUES(status),
                is_primary = VALUES(is_primary),
                priority = VALUES(priority),
                match_method = VALUES(match_method),
                remark = VALUES(remark),
                last_seen_at = VALUES(last_seen_at),
                deleted_at = NULL
            """,
            (
                str(uuid4()),
                source_app,
                source_type,
                resolved_source_key,
                source_name,
                external_source_id,
                contact_context_config_id,
                status,
                1 if is_primary else 0,
                priority,
                match_method,
                remark,
            ),
        )
        if is_primary:
            cursor.execute(
                """
                UPDATE source_contact_contexts
                SET is_primary = 0
                WHERE source_app = %s
                  AND source_type = %s
                  AND source_key = %s
                  AND contact_context_config_id <> %s
                  AND deleted_at IS NULL
                """,
                (source_app, source_type, resolved_source_key, contact_context_config_id),
            )
        cursor.execute(
            """
            SELECT id
            FROM source_contact_contexts
            WHERE source_app = %s AND source_type = %s AND source_key = %s
              AND contact_context_config_id = %s
            LIMIT 1
            """,
            (source_app, source_type, resolved_source_key, contact_context_config_id),
        )
        row = cursor.fetchone()
    if not row:
        raise RuntimeError("source contact context upsert succeeded but id was not found")
    return str(row["id"])


def upsert_source_contact_context_once(**kwargs: Any) -> str:
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_ops_platform_intake_tables(cursor)
        source_context_id = upsert_source_contact_context(conn, **kwargs)
        conn.commit()
        return source_context_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_source_contact_context_once(
    *,
    source_key: str,
    source_app: str = "crawler",
    source_type: str = "wechat_group",
) -> OpsSourceContactContext | None:
    conn = get_conn()
    try:
        return get_source_contact_context(
            conn,
            source_key=source_key,
            source_app=source_app,
            source_type=source_type,
        )
    finally:
        conn.close()


def list_wechat_group_configs(
    conn,
    *,
    status: str = "active",
    collect_enabled: bool = True,
) -> list[OpsWechatGroupConfig]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                MIN(mapping.id) AS id,
                mapping.group_key AS group_id,
                mapping.group_name,
                mapping.group_key AS source_key,
                customer.id AS customer_id,
                mapping.customer_code,
                customer.customer_name,
                MIN(mapping.id) AS contact_context_config_id,
                GROUP_CONCAT(mapping.contact_name ORDER BY mapping.contact_name SEPARATOR ',') AS contact_name,
                mapping.business_platform,
                'active' AS status,
                1 AS collect_enabled,
                100 AS sort_order
            FROM group_contact_mappings mapping
            JOIN customers customer
              ON customer.customer_code = mapping.customer_code
             AND customer.deleted_at IS NULL
            WHERE mapping.status = %s
              AND mapping.collect_enabled = %s
              AND mapping.deleted_at IS NULL
            GROUP BY
                mapping.group_key,
                mapping.group_name,
                customer.id,
                mapping.customer_code,
                customer.customer_name,
                mapping.business_platform
            ORDER BY mapping.group_name ASC, customer.customer_name ASC, mapping.business_platform ASC
            """,
            (status, 1 if collect_enabled else 0),
        )
        rows = cursor.fetchall()
    return [_wechat_group_config_from_row(row) for row in rows]


def list_wechat_group_configs_once(
    *,
    status: str = "active",
    collect_enabled: bool = True,
) -> list[OpsWechatGroupConfig]:
    conn = get_conn()
    try:
        return list_wechat_group_configs(conn, status=status, collect_enabled=collect_enabled)
    finally:
        conn.close()


def get_wechat_group_config_by_name(
    conn,
    *,
    group_name: str,
    status: str = "active",
) -> OpsWechatGroupConfig | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                MIN(mapping.id) AS id,
                mapping.group_key AS group_id,
                mapping.group_name,
                mapping.group_key AS source_key,
                customer.id AS customer_id,
                mapping.customer_code,
                customer.customer_name,
                MIN(mapping.id) AS contact_context_config_id,
                GROUP_CONCAT(mapping.contact_name ORDER BY mapping.contact_name SEPARATOR ',') AS contact_name,
                mapping.business_platform,
                'active' AS status,
                1 AS collect_enabled,
                100 AS sort_order
            FROM group_contact_mappings mapping
            JOIN customers customer
              ON customer.customer_code = mapping.customer_code
             AND customer.deleted_at IS NULL
            WHERE mapping.group_name = %s
              AND mapping.status = %s
              AND mapping.collect_enabled = 1
              AND mapping.deleted_at IS NULL
            GROUP BY
                mapping.group_key,
                mapping.group_name,
                customer.id,
                mapping.customer_code,
                customer.customer_name,
                mapping.business_platform
            LIMIT 1
            """,
            (group_name, status),
        )
        row = cursor.fetchone()
    return _wechat_group_config_from_row(row) if row else None


def candidate_with_wechat_group_config(
    candidate: OpsDemandCandidate,
    group_config: OpsWechatGroupConfig | None,
) -> OpsDemandCandidate:
    if group_config is None:
        return candidate
    return replace(
        candidate,
        external_source_key=candidate.external_source_key or group_config.source_key,
        external_chat_id=candidate.external_chat_id or group_config.group_id or group_config.source_key,
        source_chat_name=candidate.source_chat_name or group_config.group_name,
        raw_customer_name=candidate.raw_customer_name or group_config.customer_name,
        raw_owner_name=candidate.raw_owner_name or group_config.contact_name,
        raw_business_platform=candidate.raw_business_platform or group_config.business_platform,
        matched_customer_code=group_config.customer_code,
        matched_customer_id=group_config.customer_id,
        matched_contact_context_id=group_config.contact_context_config_id,
        matched_business_platform=group_config.business_platform,
        match_confidence=candidate.match_confidence if candidate.match_confidence is not None else 1.0,
        match_reason=candidate.match_reason or "group_contact_mappings metadata",
    )


def candidate_with_source_context(
    candidate: OpsDemandCandidate,
    context: OpsSourceContactContext | None,
) -> OpsDemandCandidate:
    if context is None:
        return candidate
    return replace(
        candidate,
        external_source_key=candidate.external_source_key or context.source_key,
        external_chat_id=candidate.external_chat_id or context.source_key,
        source_chat_name=candidate.source_chat_name or context.source_name,
        matched_customer_id=context.customer_id,
        matched_contact_context_id=context.contact_context_config_id,
        matched_business_platform=context.business_platform,
        match_confidence=candidate.match_confidence if candidate.match_confidence is not None else 1.0,
        match_reason=candidate.match_reason or "source_contact_contexts active binding",
    )


def _upsert_candidate(cursor, candidate: OpsDemandCandidate) -> str:
    cursor.execute(
        """
        INSERT INTO demand_intake_candidates (
            id, source_app, external_candidate_id, external_capture_run_id,
            external_source_key, external_chat_id,
            source_chat_name, raw_customer_name, raw_owner_name, raw_business_platform,
            business_category, secondary_category, tertiary_category, start_time, deadline,
            business_name, demand_title, demand_content, confidence, status,
            match_suggestion, matched_customer_code, matched_customer_id, matched_contact_context_id,
            matched_business_platform, match_confidence, match_reason, created_at
        )
        VALUES (
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, COALESCE(%s, NOW())
        )
        ON DUPLICATE KEY UPDATE
            external_capture_run_id = IF(status IN ('confirmed', 'rejected'), external_capture_run_id, VALUES(external_capture_run_id)),
            external_source_key = IF(status IN ('confirmed', 'rejected'), external_source_key, VALUES(external_source_key)),
            external_chat_id = IF(status IN ('confirmed', 'rejected'), external_chat_id, VALUES(external_chat_id)),
            source_chat_name = IF(status IN ('confirmed', 'rejected'), source_chat_name, VALUES(source_chat_name)),
            raw_customer_name = IF(status IN ('confirmed', 'rejected'), raw_customer_name, VALUES(raw_customer_name)),
            raw_owner_name = IF(status IN ('confirmed', 'rejected'), raw_owner_name, VALUES(raw_owner_name)),
            raw_business_platform = IF(status IN ('confirmed', 'rejected'), raw_business_platform, VALUES(raw_business_platform)),
            business_category = IF(status IN ('confirmed', 'rejected'), business_category, VALUES(business_category)),
            secondary_category = IF(status IN ('confirmed', 'rejected'), secondary_category, VALUES(secondary_category)),
            tertiary_category = IF(status IN ('confirmed', 'rejected'), tertiary_category, VALUES(tertiary_category)),
            start_time = IF(status IN ('confirmed', 'rejected'), start_time, VALUES(start_time)),
            deadline = IF(status IN ('confirmed', 'rejected'), deadline, VALUES(deadline)),
            business_name = IF(status IN ('confirmed', 'rejected'), business_name, VALUES(business_name)),
            demand_title = IF(status IN ('confirmed', 'rejected'), demand_title, VALUES(demand_title)),
            demand_content = IF(status IN ('confirmed', 'rejected'), demand_content, VALUES(demand_content)),
            confidence = IF(status IN ('confirmed', 'rejected'), confidence, VALUES(confidence)),
            status = IF(status IN ('confirmed', 'rejected'), status, VALUES(status)),
            match_suggestion = IF(status IN ('confirmed', 'rejected'), match_suggestion, VALUES(match_suggestion)),
            matched_customer_code = IF(status IN ('confirmed', 'rejected'), matched_customer_code, VALUES(matched_customer_code)),
            matched_customer_id = IF(status IN ('confirmed', 'rejected'), matched_customer_id, VALUES(matched_customer_id)),
            matched_contact_context_id = IF(status IN ('confirmed', 'rejected'), matched_contact_context_id, VALUES(matched_contact_context_id)),
            matched_business_platform = IF(status IN ('confirmed', 'rejected'), matched_business_platform, VALUES(matched_business_platform)),
            match_confidence = IF(status IN ('confirmed', 'rejected'), match_confidence, VALUES(match_confidence)),
            match_reason = IF(status IN ('confirmed', 'rejected'), match_reason, VALUES(match_reason)),
            deleted_at = IF(status IN ('confirmed', 'rejected'), deleted_at, NULL)
        """,
        (
            str(uuid4()),
            candidate.source_app,
            candidate.external_candidate_id,
            candidate.external_capture_run_id,
            candidate.external_source_key,
            candidate.external_chat_id,
            candidate.source_chat_name,
            candidate.raw_customer_name,
            candidate.raw_owner_name,
            candidate.raw_business_platform,
            candidate.business_category,
            candidate.secondary_category,
            candidate.tertiary_category,
            candidate.start_time,
            candidate.deadline,
            candidate.business_name,
            candidate.demand_title,
            candidate.demand_content,
            candidate.confidence,
            candidate.status or "pending",
            candidate.match_suggestion,
            candidate.matched_customer_code,
            candidate.matched_customer_id,
            candidate.matched_contact_context_id,
            candidate.matched_business_platform,
            candidate.match_confidence,
            candidate.match_reason,
            candidate.created_at,
        ),
    )
    cursor.execute(
        """
        SELECT id
        FROM demand_intake_candidates
        WHERE source_app = %s AND external_candidate_id = %s
        LIMIT 1
        """,
        (candidate.source_app, candidate.external_candidate_id),
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("ops demand candidate upsert succeeded but id was not found")
    return str(row["id"])


def _upsert_evidences(cursor, candidate_id: str, evidences: list[OpsDemandEvidence]) -> int:
    count = 0
    for evidence in evidences:
        if not evidence.external_evidence_id:
            raise ValueError("external_evidence_id is required for idempotent ops write")
        _validate_external_id("external_evidence_id", evidence.external_evidence_id)
        cursor.execute(
            """
            INSERT INTO demand_candidate_evidence (
                id, candidate_id, external_evidence_id, evidence_order,
                message_time, display_time_text, sender_name, message_text,
                screenshot_path, evidence_reason
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                evidence_order = VALUES(evidence_order),
                message_time = VALUES(message_time),
                display_time_text = VALUES(display_time_text),
                sender_name = VALUES(sender_name),
                message_text = VALUES(message_text),
                screenshot_path = VALUES(screenshot_path),
                evidence_reason = VALUES(evidence_reason)
            """,
            (
                str(uuid4()),
                candidate_id,
                evidence.external_evidence_id,
                evidence.evidence_order,
                evidence.message_time,
                evidence.display_time_text,
                evidence.sender_name,
                evidence.message_text,
                evidence.screenshot_path,
                evidence.evidence_reason,
            ),
        )
        count += 1
    return count


def _replace_evidences(cursor, candidate_id: str, evidences: list[OpsDemandEvidence]) -> int:
    cursor.execute("DELETE FROM demand_candidate_evidence WHERE candidate_id = %s", (candidate_id,))
    return _upsert_evidences(cursor, candidate_id, evidences)


def _candidate_review_locked(cursor, candidate_id: str) -> bool:
    cursor.execute("SELECT status FROM demand_intake_candidates WHERE id = %s LIMIT 1", (candidate_id,))
    row = cursor.fetchone()
    return str((row or {}).get("status") or "").lower() in {"confirmed", "rejected"}


def _wechat_group_config_from_row(row: dict[str, Any]) -> OpsWechatGroupConfig:
    return OpsWechatGroupConfig(
        id=str(row["id"]),
        group_id=row.get("group_id"),
        group_name=str(row["group_name"]),
        source_key=str(row["source_key"]),
        customer_code=str(row["customer_code"]),
        customer_name=str(row["customer_name"]),
        customer_id=str(row["customer_id"]) if row.get("customer_id") else None,
        contact_context_config_id=row.get("contact_context_config_id"),
        contact_name=row.get("contact_name"),
        business_platform=row.get("business_platform"),
        status=str(row.get("status") or "active"),
        collect_enabled=bool(row.get("collect_enabled")),
        sort_order=int(row.get("sort_order") or 100),
    )


def _validate_external_id(field_name: str, value: str) -> None:
    if len(value) > MAX_EXTERNAL_ID_LENGTH:
        raise ValueError(f"{field_name} length must be <= {MAX_EXTERNAL_ID_LENGTH}: {value!r}")


def _add_column_if_missing(cursor, table_name: str, column_name: str, column_definition: str) -> None:
    if _column_exists(cursor, table_name, column_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_definition}")


def _modify_column(cursor, table_name: str, column_name: str, column_definition: str) -> None:
    if not _column_exists(cursor, table_name, column_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` MODIFY COLUMN `{column_name}` {column_definition}")


def _add_index_if_missing(cursor, table_name: str, index_name: str, columns: str) -> None:
    if _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` ADD INDEX `{index_name}` ({columns})")


def _add_unique_index_if_missing(cursor, table_name: str, index_name: str, columns: str) -> None:
    if _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` ADD UNIQUE INDEX `{index_name}` ({columns})")


def _drop_index_if_exists(cursor, table_name: str, index_name: str) -> None:
    if not _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` DROP INDEX `{index_name}`")


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    row: dict[str, Any] | None = cursor.fetchone()
    return bool(row and int(row.get("count", 0)) > 0)


def _index_exists(cursor, table_name: str, index_name: str) -> bool:
    cursor.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name = %s", (index_name,))
    return cursor.fetchone() is not None
