"""ADB device pool, leases, and app-session health tracking."""

from __future__ import annotations

import os
import socket
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterator

from apps.finance_crawler.config import Config
from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.storage.db import get_conn
from apps.finance_crawler.storage.device_pool_schema import ensure_device_pool_tables
from apps.finance_crawler.utils.device_health import AdbDevice, DeviceUnavailable, list_adb_devices, prepare_adb_device
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("device_pool")


@dataclass(frozen=True, slots=True)
class DeviceLease:
    lease_id: int | None
    lease_token: str
    device_id: int | None
    adb_serial: str
    app_type: str
    task_scope: str
    task_id: str
    previous_env_serial: str | None
    previous_config_serial: str


def refresh_adb_devices() -> list[AdbDevice]:
    devices = list_adb_devices()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_device_pool_tables(cursor)
            seen_serials = []
            for device in devices:
                seen_serials.append(device.serial)
                status = "online" if device.ready else device.state or "offline"
                cursor.execute(
                    """
                    INSERT INTO adb_devices (
                        adb_serial, connect_type, model, product, device_name, status, last_seen_at, last_error
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), NULL)
                    ON DUPLICATE KEY UPDATE
                        connect_type = VALUES(connect_type),
                        model = VALUES(model),
                        product = VALUES(product),
                        device_name = VALUES(device_name),
                        status = CASE
                            WHEN status = 'disabled' THEN status
                            WHEN cooldown_until IS NOT NULL AND cooldown_until > NOW() THEN 'cooldown'
                            ELSE VALUES(status)
                        END,
                        cooldown_until = CASE
                            WHEN cooldown_until IS NOT NULL AND cooldown_until <= NOW() THEN NULL
                            ELSE cooldown_until
                        END,
                        last_seen_at = NOW(),
                        last_error = NULL
                    """,
                    (
                        device.serial,
                        device.transport,
                        device.model or None,
                        device.product or None,
                        device.device_name or None,
                        status,
                    ),
                )
            if seen_serials:
                placeholders = ", ".join(["%s"] * len(seen_serials))
                cursor.execute(
                    f"""
                    UPDATE adb_devices
                    SET status = CASE WHEN status = 'disabled' THEN status ELSE 'offline' END,
                        current_worker_id = NULL,
                        lease_until = NULL
                    WHERE adb_serial NOT IN ({placeholders})
                      AND status <> 'disabled'
                    """,
                    seen_serials,
                )
            else:
                cursor.execute(
                    """
                    UPDATE adb_devices
                    SET status = CASE WHEN status = 'disabled' THEN status ELSE 'offline' END,
                        current_worker_id = NULL,
                        lease_until = NULL
                    WHERE status <> 'disabled'
                    """
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return devices


def device_pool_status() -> dict[str, Any]:
    refresh_adb_devices()
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_device_pool_tables(cursor)
            cursor.execute(
                """
                SELECT id, adb_serial, connect_type, model, product, device_name, status,
                       last_seen_at, cooldown_until, current_worker_id, lease_until, last_error
                FROM adb_devices
                ORDER BY id ASC
                """
            )
            devices = cursor.fetchall()
            cursor.execute(
                """
                SELECT d.adb_serial, s.app_type, s.login_status, s.risk_status,
                       s.cooldown_until, s.success_count, s.failure_count,
                       s.last_success_at, s.last_failure_at, s.last_risk_reason
                FROM adb_device_app_sessions s
                JOIN adb_devices d ON d.id = s.device_id
                ORDER BY d.id ASC, s.app_type ASC
                """
            )
            sessions = cursor.fetchall()
            cursor.execute(
                """
                SELECT task_scope, task_id, app_type, adb_serial, status, leased_until, started_at
                FROM adb_execution_leases
                WHERE status = 'running'
                ORDER BY started_at ASC
                """
            )
            running_leases = cursor.fetchall()
    finally:
        conn.close()
    return {
        "devices": [_json_ready_row(item) for item in devices],
        "sessions": [_json_ready_row(item) for item in sessions],
        "running_leases": [_json_ready_row(item) for item in running_leases],
    }


@contextmanager
def acquire_device(
    *,
    app_type: str,
    task_scope: str,
    task_id: str | int,
    worker_id: str | None = None,
    adb_serial: str | None = None,
) -> Iterator[DeviceLease]:
    lease = start_device_lease(
        app_type=app_type,
        task_scope=task_scope,
        task_id=task_id,
        worker_id=worker_id,
        adb_serial=adb_serial,
    )
    try:
        yield lease
        release_device_lease(lease, status="success")
    except Exception as exc:
        release_device_lease(lease, status="failed", error=str(exc), error_type=_error_type(exc))
        raise


def start_device_lease(
    *,
    app_type: str,
    task_scope: str,
    task_id: str | int,
    worker_id: str | None = None,
    adb_serial: str | None = None,
) -> DeviceLease:
    wait_seconds = max(int(Config.DEVICE_LOCK_WAIT_SECONDS or 0), 0)
    poll_seconds = max(float(Config.DEVICE_LOCK_POLL_SECONDS or 1.0), 0.5)
    deadline = time.monotonic() + wait_seconds
    attempts = 0
    while True:
        attempts += 1
        try:
            lease = _acquire_device(
                app_type=app_type,
                task_scope=task_scope,
                task_id=str(task_id),
                worker_id=worker_id,
                adb_serial=adb_serial,
            )
            break
        except DeviceUnavailable as exc:
            if not _is_lock_wait_error(exc) or time.monotonic() >= deadline:
                raise
            if attempts == 1:
                logger.info(
                    "waiting for adb device lock app=%s scope=%s task=%s serial=%s max_wait=%ss",
                    app_type,
                    task_scope,
                    task_id,
                    adb_serial or Config.DEVICE_SERIAL or "any",
                    wait_seconds,
                )
            time.sleep(min(poll_seconds, max(0.1, deadline - time.monotonic())))
    _activate_lease_serial(lease)
    return lease


def release_device_lease(
    lease: DeviceLease,
    *,
    status: str,
    error: str | None = None,
    error_type: str | None = None,
) -> None:
    try:
        finish_device_lease(lease, status=status, error=error, error_type=error_type)
    finally:
        _restore_lease_serial(lease)


def _acquire_device(
    *,
    app_type: str,
    task_scope: str,
    task_id: str,
    worker_id: str | None,
    adb_serial: str | None,
) -> DeviceLease:
    preferred_serial = (adb_serial or Config.DEVICE_SERIAL or "").strip()
    previous_env_serial = os.environ.get("DEVICE_SERIAL")
    previous_config_serial = str(Config.DEVICE_SERIAL or "")
    if not Config.DEVICE_POOL_ENABLED:
        if preferred_serial:
            os.environ["DEVICE_SERIAL"] = preferred_serial
            Config.DEVICE_SERIAL = preferred_serial
            reset_device_session()
        device = prepare_adb_device()
        return DeviceLease(
            lease_id=None,
            lease_token=str(uuid.uuid4()),
            device_id=None,
            adb_serial=device.serial,
            app_type=app_type or "unknown",
            task_scope=task_scope,
            task_id=task_id,
            previous_env_serial=previous_env_serial,
            previous_config_serial=previous_config_serial,
        )

    refresh_adb_devices()
    worker = worker_id or _default_worker_id()
    lease_token = str(uuid.uuid4())
    lease_seconds = max(
        int(Config.DEVICE_LEASE_SECONDS or 600),
        int(Config.DEVICE_LOCK_WAIT_SECONDS or 0),
        60,
    )
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_device_pool_tables(cursor)
            serial_clause = ""
            params: list[Any] = [app_type or "unknown"]
            if preferred_serial:
                serial_clause = "AND d.adb_serial = %s"
                params.append(preferred_serial)
            cursor.execute(
                f"""
                SELECT d.*
                FROM adb_devices d
                LEFT JOIN adb_device_app_sessions s
                  ON s.device_id = d.id
                 AND s.app_type = %s
                WHERE d.status IN ('online', 'device')
                  AND (d.cooldown_until IS NULL OR d.cooldown_until <= NOW())
                  AND (d.lease_until IS NULL OR d.lease_until <= NOW())
                  AND (s.cooldown_until IS NULL OR s.cooldown_until <= NOW())
                  AND COALESCE(s.risk_status, 'ok') NOT IN ('blocked', 'disabled')
                  AND COALESCE(s.login_status, 'unknown') NOT IN ('login_required', 'disabled')
                  {serial_clause}
                ORDER BY COALESCE(s.failure_count, 0) ASC,
                         COALESCE(s.success_count, 0) DESC,
                         d.last_seen_at DESC,
                         d.id ASC
                LIMIT 1
                FOR UPDATE
                """,
                params,
            )
            row = cursor.fetchone()
            if not row:
                serial_text = preferred_serial or "any"
                raise DeviceUnavailable(
                    f"no available adb device lock for app_type={app_type or 'unknown'} serial={serial_text}"
                )
            device_id = int(row["id"])
            adb_serial = str(row["adb_serial"])
            leased_until = datetime.now() + timedelta(seconds=lease_seconds)
            cursor.execute(
                """
                UPDATE adb_devices
                SET current_worker_id = %s,
                    lease_until = %s,
                    status = 'online'
                WHERE id = %s
                """,
                (worker, leased_until, device_id),
            )
            cursor.execute(
                """
                INSERT INTO adb_device_app_sessions (device_id, app_type)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
                """,
                (device_id, app_type or "unknown"),
            )
            cursor.execute(
                """
                INSERT INTO adb_execution_leases (
                    task_scope, task_id, app_type, device_id, adb_serial, lease_token, worker_id, leased_until
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (task_scope, task_id, app_type or "unknown", device_id, adb_serial, lease_token, worker, leased_until),
            )
            lease_id = int(cursor.lastrowid)
        conn.commit()
        logger.info(
            "device lease acquired app=%s scope=%s task=%s serial=%s lease=%s",
            app_type,
            task_scope,
            task_id,
            adb_serial,
            lease_id,
        )
        return DeviceLease(
            lease_id=lease_id,
            lease_token=lease_token,
            device_id=device_id,
            adb_serial=adb_serial,
            app_type=app_type or "unknown",
            task_scope=task_scope,
            task_id=task_id,
            previous_env_serial=previous_env_serial,
            previous_config_serial=previous_config_serial,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_device_lease(
    lease: DeviceLease,
    *,
    status: str,
    error: str | None = None,
    error_type: str | None = None,
) -> None:
    if not Config.DEVICE_POOL_ENABLED or lease.device_id is None:
        return
    normalized_status = "success" if status == "success" else "failed"
    cooldown_seconds = _cooldown_seconds(error=error, error_type=error_type)
    cooldown_until = datetime.now() + timedelta(seconds=cooldown_seconds) if cooldown_seconds > 0 else None
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_device_pool_tables(cursor)
            cursor.execute(
                """
                UPDATE adb_execution_leases
                SET status = %s,
                    error_type = %s,
                    error = %s,
                    finished_at = NOW()
                WHERE lease_token = %s
                """,
                (normalized_status, error_type, error, lease.lease_token),
            )
            cursor.execute(
                """
                UPDATE adb_devices
                SET current_worker_id = NULL,
                    lease_until = NULL,
                    status = CASE
                        WHEN %s IS NOT NULL THEN 'cooldown'
                        WHEN status = 'disabled' THEN status
                        ELSE 'online'
                    END,
                    cooldown_until = %s,
                    last_error = %s
                WHERE id = %s
                """,
                (cooldown_until, cooldown_until, error if cooldown_until else None, lease.device_id),
            )
            if normalized_status == "success":
                cursor.execute(
                    """
                    UPDATE adb_device_app_sessions
                    SET risk_status = 'ok',
                        cooldown_until = NULL,
                        last_risk_reason = NULL,
                        success_count = success_count + 1,
                        last_success_at = NOW()
                    WHERE device_id = %s AND app_type = %s
                    """,
                    (lease.device_id, lease.app_type),
                )
            else:
                risk_status = "cooldown" if cooldown_until else "ok"
                login_status = "login_required" if _is_login_error(error) else "unknown"
                cursor.execute(
                    """
                    UPDATE adb_device_app_sessions
                    SET risk_status = %s,
                        login_status = CASE WHEN %s = 'login_required' THEN 'login_required' ELSE login_status END,
                        cooldown_until = %s,
                        last_risk_reason = %s,
                        failure_count = failure_count + 1,
                        last_failure_at = NOW()
                    WHERE device_id = %s AND app_type = %s
                    """,
                    (risk_status, login_status, cooldown_until, error, lease.device_id, lease.app_type),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.warning("failed to finish device lease=%s: %s", lease.lease_id, error)
    finally:
        conn.close()


def mark_device_app_cooldown(*, adb_serial: str, app_type: str, reason: str, seconds: int | None = None) -> None:
    cooldown_seconds = int(seconds if seconds is not None else Config.DEVICE_RISK_COOLDOWN_SECONDS)
    if cooldown_seconds <= 0:
        return
    cooldown_until = datetime.now() + timedelta(seconds=cooldown_seconds)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            ensure_device_pool_tables(cursor)
            cursor.execute("SELECT id FROM adb_devices WHERE adb_serial = %s", (adb_serial,))
            row = cursor.fetchone()
            if not row:
                return
            device_id = int(row["id"])
            cursor.execute(
                """
                INSERT INTO adb_device_app_sessions (
                    device_id, app_type, risk_status, cooldown_until, last_risk_reason, failure_count, last_failure_at
                )
                VALUES (%s, %s, 'cooldown', %s, %s, 1, NOW())
                ON DUPLICATE KEY UPDATE
                    risk_status = 'cooldown',
                    cooldown_until = VALUES(cooldown_until),
                    last_risk_reason = VALUES(last_risk_reason),
                    failure_count = failure_count + 1,
                    last_failure_at = NOW()
                """,
                (device_id, app_type or "unknown", cooldown_until, reason),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _activate_lease_serial(lease: DeviceLease) -> None:
    if lease.adb_serial:
        os.environ["DEVICE_SERIAL"] = lease.adb_serial
        Config.DEVICE_SERIAL = lease.adb_serial
        reset_device_session()


def _restore_lease_serial(lease: DeviceLease) -> None:
    if lease.previous_env_serial is None:
        os.environ.pop("DEVICE_SERIAL", None)
    else:
        os.environ["DEVICE_SERIAL"] = lease.previous_env_serial
    Config.DEVICE_SERIAL = lease.previous_config_serial
    reset_device_session()


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _json_ready_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: (value.isoformat(sep=" ") if hasattr(value, "isoformat") else value) for key, value in dict(row).items()}


def _error_type(error: Exception) -> str:
    text = str(error or "").lower()
    if "device" in text or "adb" in text or "uiautomator" in text:
        return "device_unavailable"
    if _is_login_error(text):
        return "login_required"
    if _is_risk_error(text):
        return "risk_control"
    return "unknown_error"


def _is_lock_wait_error(error: Exception) -> bool:
    text = str(error or "").lower()
    return "no available adb device lock" in text or "no available adb device" in text


def _is_login_error(error: Any) -> bool:
    text = str(error or "").lower()
    return "login" in text or "登录" in text or "密码" in text


def _is_risk_error(error: Any) -> bool:
    text = str(error or "").lower()
    markers = (
        "risk",
        "blocked",
        "稍后再试",
        "网络不给力",
        "滑块",
        "验证",
        "identity verification",
        "profile page is unavailable",
        "profile page is blocked",
        "too many requests",
    )
    return any(marker in text for marker in markers)


def _cooldown_seconds(*, error: str | None, error_type: str | None) -> int:
    normalized = str(error_type or "").lower()
    if normalized == "device_unavailable":
        return int(Config.DEVICE_UNAVAILABLE_COOLDOWN_SECONDS)
    if normalized == "login_required":
        return int(Config.DEVICE_LOGIN_COOLDOWN_SECONDS)
    if normalized == "risk_control" or _is_risk_error(error):
        return int(Config.DEVICE_RISK_COOLDOWN_SECONDS)
    return 0
