"""Models for auditable temporary corrections."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CorrectionRequest:
    target_type: str
    field_name: str
    new_value: str
    reason: str
    target_id: int | None = None
    document_id: int | None = None
    sheet_id: str | None = None
    row_index: int | None = None
    old_value: str | None = None
    operator_name: str = "cli"


def insert_correction(conn, request: CorrectionRequest, *, status: str = "planned") -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO corrections (
                target_type, target_id, document_id, sheet_id, row_index, field_name,
                old_value, new_value, reason, status, operator_name
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request.target_type,
                request.target_id,
                request.document_id,
                request.sheet_id,
                request.row_index,
                request.field_name,
                request.old_value,
                request.new_value,
                request.reason,
                status,
                request.operator_name,
            ),
        )
        return int(cursor.lastrowid)
