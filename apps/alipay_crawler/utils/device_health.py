"""ADB device health checks used before and during crawl jobs."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from apps.alipay_crawler.config import Config


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
    if len(ready) > 1:
        serials = ", ".join(item.serial for item in ready)
        raise DeviceUnavailable(f"multiple adb devices found; set DEVICE_SERIAL. devices={serials}")
    return ready[0]


def assert_device_ready() -> str:
    global _last_ready_checked_at, _last_ready_serial
    now = time.monotonic()
    if (
        _last_ready_serial
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
