"""Runtime adapters for crawler_app task execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from apps.finance_crawler.mobile.device_session import reset_device_session
from apps.finance_crawler.utils.device_health import AdbDevice, DeviceUnavailable, prepare_adb_device


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    """Prepare an execution environment before a task batch starts."""

    name: str
    prepare: Callable[[], AdbDevice]


def _prepare_adb_runtime() -> AdbDevice:
    try:
        return prepare_adb_device()
    except DeviceUnavailable:
        reset_device_session()
        raise


ADB_RUNTIME = ExecutionRuntime(name="adb", prepare=_prepare_adb_runtime)
