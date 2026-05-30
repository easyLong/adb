"""ADB device health checks used before and during crawl jobs."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from apps.finance_crawler.config import Config
from apps.finance_crawler.utils.logger import get_logger


logger = get_logger("device_health")


class DeviceUnavailable(RuntimeError):
    """Raised when the configured Android device is not ready for automation."""


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    detail: str = ""


_last_ready_serial: str | None = None
_last_ready_checked_at = 0.0


def adb_executable() -> str:
    configured = Path(Config.ADB_PATH)
    if configured.exists():
        return str(configured)
    return "adb"


def adb_command(args: list[str], timeout: int | None = None) -> str:
    result = subprocess.run(
        [adb_executable(), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout or Config.DEVICE_CHECK_TIMEOUT,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _adb_command_no_check(args: list[str], timeout: int | None = None) -> str:
    result = subprocess.run(
        [adb_executable(), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout or Config.DEVICE_CHECK_TIMEOUT,
        encoding="utf-8",
        errors="replace",
    )
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


def list_adb_devices() -> list[AdbDevice]:
    output = adb_command(["devices", "-l"])
    devices: list[AdbDevice] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=2)
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        detail = parts[2] if len(parts) > 2 else ""
        devices.append(AdbDevice(serial=serial, state=state, detail=detail))
    return devices


def _is_connectable_wireless_serial(serial: str) -> bool:
    return bool(re.match(r"^\d{1,3}(?:\.\d{1,3}){3}:\d+$", serial))


def _discover_wireless_connect_serials() -> list[str]:
    try:
        output = _adb_command_no_check(["mdns", "services"])
    except Exception as exc:
        logger.debug("adb mdns discovery failed: %s", exc)
        return []

    serials: list[str] = []
    for line in output.splitlines():
        if "_adb-tls-connect._tcp" not in line:
            continue
        match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}:\d+)", line)
        if match:
            serials.append(match.group(1))
    return _dedupe(serials)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    output = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _reconnect_candidates() -> list[str]:
    candidates: list[str] = []
    configured = Config.DEVICE_SERIAL.strip()
    if _is_connectable_wireless_serial(configured):
        candidates.append(configured)
    if _last_ready_serial and _is_connectable_wireless_serial(_last_ready_serial):
        candidates.append(_last_ready_serial)
    candidates.extend(_discover_wireless_connect_serials())
    return _dedupe(candidates)


def _try_adb_connect(serial: str) -> bool:
    try:
        output = _adb_command_no_check(["connect", serial], timeout=Config.DEVICE_CHECK_TIMEOUT)
    except Exception as exc:
        logger.warning("adb reconnect failed for %s: %s", serial, exc)
        return False

    lowered = output.lower()
    ok = "connected to" in lowered or "already connected" in lowered
    if ok:
        logger.info("adb wireless reconnect ok: %s", serial)
    else:
        logger.warning("adb wireless reconnect failed: %s; output=%s", serial, output)
    return ok


def _attempt_wireless_reconnect() -> None:
    if not Config.DEVICE_AUTO_RECONNECT:
        return

    candidates = _reconnect_candidates()
    if not candidates:
        return

    for serial in candidates:
        if _try_adb_connect(serial):
            return


def _select_device(devices: list[AdbDevice]) -> AdbDevice:
    configured = Config.DEVICE_SERIAL.strip()
    if configured:
        for item in devices:
            if item.serial == configured:
                return item
        raise DeviceUnavailable(f"configured device not found: {configured}")

    ready = [item for item in devices if item.state == "device"]
    if not ready:
        states = ", ".join(f"{item.serial}:{item.state}" for item in devices) or "none"
        raise DeviceUnavailable(f"no adb device is ready; devices={states}")
    if _last_ready_serial:
        for item in ready:
            if item.serial == _last_ready_serial:
                return item
    wireless_ready = [item for item in ready if _is_connectable_wireless_serial(item.serial)]
    if len(wireless_ready) == 1:
        return wireless_ready[0]
    if len(ready) > 1:
        serials = ", ".join(item.serial for item in ready)
        raise DeviceUnavailable(f"multiple adb devices found; set DEVICE_SERIAL. devices={serials}")
    return ready[0]


def _check_device_ready_once(*, allow_cache: bool = True) -> str:
    global _last_ready_checked_at, _last_ready_serial
    now = time.monotonic()
    if (
        allow_cache
        and _last_ready_serial
        and Config.DEVICE_HEALTH_CACHE_SECONDS > 0
        and now - _last_ready_checked_at <= Config.DEVICE_HEALTH_CACHE_SECONDS
    ):
        return _last_ready_serial

    try:
        device = _select_device(list_adb_devices())
    except subprocess.TimeoutExpired as exc:
        raise DeviceUnavailable("adb device check timed out") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise DeviceUnavailable(f"adb device check failed: {message}") from exc

    if device.state == "unauthorized":
        raise DeviceUnavailable(f"adb device unauthorized: {device.serial}")
    if device.state != "device":
        raise DeviceUnavailable(f"adb device not ready: {device.serial}:{device.state}")

    serial_args = ["-s", device.serial] if device.serial else []
    try:
        boot_completed = adb_command(
            [*serial_args, "shell", "getprop", "sys.boot_completed"],
            timeout=Config.DEVICE_CHECK_TIMEOUT,
        ).strip()
    except Exception as exc:
        raise DeviceUnavailable(f"adb shell unavailable for {device.serial}: {exc}") from exc

    if boot_completed != "1":
        raise DeviceUnavailable(f"device is connected but Android has not completed boot: {device.serial}")
    _last_ready_serial = device.serial
    _last_ready_checked_at = now
    return device.serial


def assert_device_ready() -> str:
    global _last_ready_checked_at, _last_ready_serial

    attempts = max(1, Config.DEVICE_RECONNECT_RETRIES + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _check_device_ready_once(allow_cache=attempt == 0)
        except DeviceUnavailable as exc:
            last_error = exc
            _last_ready_checked_at = 0.0
            if attempt >= attempts - 1:
                break
            logger.warning(
                "adb device unavailable, retrying %s/%s: %s",
                attempt + 1,
                attempts - 1,
                exc,
            )
            _attempt_wireless_reconnect()
            time.sleep(max(Config.DEVICE_RECONNECT_DELAY_SECONDS, 0.0))

    raise last_error or DeviceUnavailable("adb device is unavailable")
