"""Reusable Android device session helpers for app crawlers."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from apps.finance_crawler.config import Config
from apps.finance_crawler.mobile.capture_engine import (
    connect_uiautomator,
    is_lockscreen_showing,
    open_app_link,
    run_adb,
    resolve_app_deep_link,
    set_device_awake,
)
from apps.finance_crawler.crawlers.registry import get_app_profile, target_package_for_url
from apps.finance_crawler.utils.device_health import DeviceUnavailable, assert_device_ready
from apps.finance_crawler.utils.logger import get_logger

logger = get_logger("device_session")

_device: Any = None
_device_serial: str | None = None
_last_device_prepare_at = 0.0


def current_serial() -> str | None:
    return _device_serial


def _prepare_adb_path() -> None:
    adb_dir = str(Path(Config.ADB_PATH).resolve().parent)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if adb_dir not in path_parts:
        os.environ["PATH"] = adb_dir + os.pathsep + os.environ.get("PATH", "")


def reset_device_session() -> None:
    global _device, _device_serial, _last_device_prepare_at
    _device = None
    _device_serial = None
    _last_device_prepare_at = 0.0


def device():
    global _device, _device_serial
    serial = assert_device_ready()
    if _device is not None and _device_serial == serial:
        try:
            _device.info
            return _device
        except Exception as exc:
            logger.warning("uiautomator2 session stale, reconnecting: %s", exc)
            reset_device_session()

    if _device is None or _device_serial != serial:
        _device_serial = serial
        _device = connect_uiautomator(serial)
        try:
            info = _device.info
        except Exception as exc:
            reset_device_session()
            raise DeviceUnavailable(f"uiautomator2 device session is unavailable: {exc}") from exc
        logger.info(
            "device connected: %s (%sx%s)",
            info.get("productName", "unknown"),
            info.get("displayWidth"),
            info.get("displayHeight"),
        )
    return _device


def _prepare_device_if_needed(serial: str) -> None:
    global _last_device_prepare_at
    now = time.monotonic()
    if now - _last_device_prepare_at < Config.DEVICE_PREPARE_INTERVAL_SECONDS:
        return
    set_device_awake(serial)
    if is_lockscreen_showing(serial):
        raise RuntimeError("device is locked; unlock the phone and retry")
    _last_device_prepare_at = now


def resolve_short_url(short_url: str) -> str:
    _prepare_adb_path()
    resolved = resolve_app_deep_link(short_url)
    return resolved or short_url


def open_url(url: str) -> None:
    _prepare_adb_path()
    serial = assert_device_ready()
    _prepare_device_if_needed(serial)
    open_app_link(url, serial=serial)
    time.sleep(Config.PAGE_LOAD_WAIT)


def restart_app_for_url(url: str, *, source_app: str | None = None) -> bool:
    """Force-stop the target app for a URL before reopening a stuck/blank page."""

    _prepare_adb_path()
    serial = assert_device_ready()
    package_name = target_package_for_url(url)
    if not package_name and source_app:
        profile = get_app_profile(source_app)
        package_name = profile.package_name if profile else None
    if not package_name:
        logger.warning("app restart skipped; package not resolved url=%s source=%s", url, source_app)
        return False

    logger.warning("force-stopping app package=%s url=%s", package_name, url)
    run_adb(["shell", "am", "force-stop", package_name], serial=serial, timeout=10)
    reset_device_session()
    time.sleep(Config.APP_RESTART_WAIT)
    return True
