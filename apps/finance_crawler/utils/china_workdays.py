"""China workday calendar helpers."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def load_china_workday_calendar(path: str | Path, *, project_dir: Path | None = None) -> dict[str, Any]:
    calendar_path = Path(path)
    if not calendar_path.is_absolute() and project_dir is not None:
        calendar_path = project_dir / calendar_path
    with calendar_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def is_china_workday(
    target: date,
    *,
    calendar_path: str | Path,
    project_dir: Path | None = None,
) -> bool:
    calendar = load_china_workday_calendar(calendar_path, project_dir=project_dir)
    year_config = calendar.get(str(target.year))
    if not year_config:
        raise ValueError(f"China workday calendar missing year: {target.year}")
    date_key = target.isoformat()
    holidays = set(year_config.get("holidays") or [])
    workdays = set(year_config.get("workdays") or [])
    if date_key in holidays:
        return False
    if date_key in workdays:
        return True
    return target.isoweekday() <= 5


def previous_china_workday(
    target: date,
    *,
    calendar_path: str | Path,
    project_dir: Path | None = None,
    max_lookback_days: int = 30,
) -> date:
    current = target - timedelta(days=1)
    for _ in range(max(max_lookback_days, 1)):
        if is_china_workday(current, calendar_path=calendar_path, project_dir=project_dir):
            return current
        current -= timedelta(days=1)
    raise ValueError(f"China workday calendar cannot resolve previous workday for {target.isoformat()}")
