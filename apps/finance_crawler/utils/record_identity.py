"""Helpers for workflow-facing crawl record identity."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def workflow_record_id(record: Mapping[str, Any]) -> int:
    """Return the stable numeric id workflows use for logs, captures, and maps."""

    for key in ("record_id", "submission_id"):
        value = record.get(key)
        if value is not None:
            return int(value)
    raise KeyError("workflow record is missing record_id/submission_id")


def workflow_record_url(record: Mapping[str, Any]) -> str:
    value = record.get("url")
    if not value:
        raise KeyError("workflow record is missing url")
    return str(value)
