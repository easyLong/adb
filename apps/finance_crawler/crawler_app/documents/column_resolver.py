"""Resolve fixed business fields from variable spreadsheet headers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from apps.finance_crawler.crawler_app.documents.fields import BusinessField, DEFAULT_BUSINESS_FIELDS

_PUNCT_RE = re.compile(r"[\u3000\s:：;；,，.。|/\\_\-()（）\[\]【】{}<>《》]+")


@dataclass(frozen=True, slots=True)
class FieldResolution:
    field_name: str
    label: str
    column_index: int | None
    header_title: str
    status: str
    match_type: str
    matched_alias: str = ""
    candidates: tuple[int, ...] = ()
    required: bool = False
    create_if_missing: bool = False


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    columns: dict[str, int]
    resolutions: tuple[FieldResolution, ...]
    problems: tuple[str, ...]
    header_hash: str

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_json_dict(self) -> dict[str, object]:
        return {
            "columns": self.columns,
            "resolutions": [asdict(item) for item in self.resolutions],
            "problems": list(self.problems),
            "header_hash": self.header_hash,
        }


def normalize_title(value: object) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[\ufe00-\ufe0f]", "", text)
    return _PUNCT_RE.sub("", text)


def header_hash(header: list[object] | tuple[object, ...]) -> str:
    payload = json.dumps([str(value or "") for value in header], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_header(
    header: list[object] | tuple[object, ...],
    *,
    fields: tuple[BusinessField, ...] = DEFAULT_BUSINESS_FIELDS,
) -> ColumnMapping:
    header_values = [str(value or "").strip() for value in header]
    normalized_headers = [normalize_title(value) for value in header_values]
    columns: dict[str, int] = {}
    resolutions: list[FieldResolution] = []
    problems: list[str] = []

    for field in fields:
        resolution = _resolve_field(field, header_values, normalized_headers)
        resolutions.append(resolution)
        if resolution.column_index is not None:
            columns[field.name] = resolution.column_index
        elif field.required:
            problems.append(f"missing required field: {field.name} ({field.label})")

    _append_duplicate_column_problems(resolutions, problems)
    return ColumnMapping(
        columns=columns,
        resolutions=tuple(resolutions),
        problems=tuple(problems),
        header_hash=header_hash(header_values),
    )


def _resolve_field(
    field: BusinessField,
    header_values: list[str],
    normalized_headers: list[str],
) -> FieldResolution:
    alias_values = [(index, alias, normalize_title(alias)) for index, alias in enumerate(field.aliases)]
    matches: list[tuple[int, int, str, str]] = []

    for alias_rank, alias, normalized_alias in alias_values:
        if not normalized_alias:
            continue
        for column_index, normalized_header in enumerate(normalized_headers):
            if normalized_header == normalized_alias:
                matches.append((alias_rank, column_index, alias, "exact"))

    if not matches:
        for alias_rank, alias, normalized_alias in alias_values:
            if not normalized_alias or len(normalized_alias) < 2:
                continue
            for column_index, normalized_header in enumerate(normalized_headers):
                if normalized_header and normalized_alias in normalized_header:
                    matches.append((alias_rank, column_index, alias, "contains"))

    if not matches:
        return FieldResolution(
            field_name=field.name,
            label=field.label,
            column_index=None,
            header_title="",
            status="missing",
            match_type="none",
            required=field.required,
            create_if_missing=field.create_if_missing,
        )

    best_rank = min(match[0] for match in matches)
    best_matches = [match for match in matches if match[0] == best_rank]
    candidate_columns = tuple(sorted({match[1] for match in best_matches}))
    if len(candidate_columns) > 1:
        return FieldResolution(
            field_name=field.name,
            label=field.label,
            column_index=None,
            header_title="",
            status="ambiguous",
            match_type=best_matches[0][3],
            matched_alias=best_matches[0][2],
            candidates=candidate_columns,
            required=field.required,
            create_if_missing=field.create_if_missing,
        )

    column_index = candidate_columns[0]
    match = best_matches[0]
    return FieldResolution(
        field_name=field.name,
        label=field.label,
        column_index=column_index,
        header_title=header_values[column_index],
        status="matched",
        match_type=match[3],
        matched_alias=match[2],
        candidates=candidate_columns,
        required=field.required,
        create_if_missing=field.create_if_missing,
    )


def _append_duplicate_column_problems(resolutions: list[FieldResolution], problems: list[str]) -> None:
    by_column: dict[int, list[str]] = {}
    for item in resolutions:
        if item.column_index is None:
            if item.status == "ambiguous":
                problems.append(
                    f"ambiguous field: {item.field_name} ({item.label}) candidates={list(item.candidates)}"
                )
            continue
        by_column.setdefault(item.column_index, []).append(item.field_name)
    for column_index, field_names in sorted(by_column.items()):
        if len(field_names) > 1:
            problems.append(f"one column matched multiple fields: col={column_index} fields={field_names}")
