"""Helpers for reading UI/OCR capture records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_capture_records(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("ui_jsonl", "ocr_jsonl"):
        path_text = summary.get(key)
        if not path_text:
            continue
        rows.extend(read_jsonl(Path(str(path_text))))
    return [normalize_bounds(row) for row in rows]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    output = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            output.append(json.loads(line))
    return output


def normalize_bounds(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    bounds = dict(item.get("bounds") or {})
    if "right" not in bounds:
        bounds["right"] = int(bounds.get("left") or 0) + int(bounds.get("width") or 0)
    if "bottom" not in bounds:
        bounds["bottom"] = int(bounds.get("top") or 0) + int(bounds.get("height") or 0)
    item["bounds"] = bounds
    return item
