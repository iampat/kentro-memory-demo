"""Server-side conflict detection on the write path.

Called by the API write handler (Step 7 will wire it). Inserts the new `FieldWriteRow`,
then checks whether a `ConflictRow` should be opened for this (entity, field).

Detection contract (see plan + IMPLEMENTATION_PLAN Step 5):
- Two writes with the same `value_json` → corroboration (no conflict).
- Two writes with different `value_json` values → conflict.
- We re-use an already-open `ConflictRow` for the same (entity, field) instead of
  creating a duplicate. A new `ConflictRow` is only created when no open one exists.

`superseded` is NOT touched here. Resolution is purely a read-time view (see `resolve.py`).
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlmodel import Session, col, select

from kentro_server.store.models import ConflictRow, FieldWriteRow


def record_field_write(
    session: Session,
    *,
    entity_id: UUID,
    field_name: str,
    value_json: str,
    confidence: float | None = None,
    written_by_agent_id: str,
    rule_version_at_write: int,
    source_document_id: UUID | None = None,
    extraction_step_id: UUID | None = None,
) -> tuple[FieldWriteRow, ConflictRow | None]:
    """Persist a new write and (if it disagrees with existing live writes) ensure an open ConflictRow.

    Returns `(new_write_row, conflict_row_or_None)`. Caller commits the session.
    """
    write = FieldWriteRow(
        entity_id=entity_id,
        field_name=field_name,
        value_json=value_json,
        confidence=confidence,
        written_by_agent_id=written_by_agent_id,
        written_at=datetime.now(timezone.utc),
        source_document_id=source_document_id,
        rule_version_at_write=rule_version_at_write,
        extraction_step_id=extraction_step_id,
    )
    session.add(write)
    # Flush so the new row is visible to the conflict-detection query in this transaction.
    session.flush()

    live_writes = session.exec(
        select(FieldWriteRow).where(
            FieldWriteRow.entity_id == entity_id,
            FieldWriteRow.field_name == field_name,
            ~col(FieldWriteRow.superseded),
        )
    ).all()

    distinct_values = {w.value_json for w in live_writes}
    if len(distinct_values) <= 1:
        return write, None

    # Re-use an already-open ConflictRow for this (entity, field), or create one.
    open_conflict = session.exec(
        select(ConflictRow).where(
            ConflictRow.entity_id == entity_id,
            ConflictRow.field_name == field_name,
            col(ConflictRow.resolved_at).is_(None),
        )
    ).first()
    if open_conflict is not None:
        return write, open_conflict

    new_conflict = ConflictRow(entity_id=entity_id, field_name=field_name)
    session.add(new_conflict)
    session.flush()
    return write, new_conflict


__all__ = ["record_field_write"]
