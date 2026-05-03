"""Source removal — delete a document, its writes, its blob, and trigger
re-resolution of any open conflicts that no longer have multiple distinct values.

This is the v0 implementation of the handoff §1.5 "Source removal" data flow:

    SDK → DELETE /documents/{source_id} → server removes the blob, removes the
    document's lineage edges, re-runs conflict resolution against surviving
    evidence for any affected fields → response carries `ReevaluationReport`.

Step 7's `DELETE /documents/{id}` route is a thin wrapper around this function.
The integration smoke test calls it directly (no HTTP) to prove the demo's
"delete the email and watch it fall back to $250K" beat.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import select

from kentro_server.store import TenantStore
from kentro_server.store.models import (
    ConflictRow,
    DocumentRow,
    FieldWriteRow,
)

logger = logging.getLogger(__name__)


def remove_document(
    *,
    store: TenantStore,
    document_id: UUID,
) -> tuple[int, list[tuple[UUID, str]]]:
    """Delete a document and re-resolve affected conflicts.

    Returns `(removed_writes_count, closed_conflicts)` where `closed_conflicts`
    lists `(entity_id, field_name)` pairs whose open `ConflictRow` was closed
    because the surviving live writes no longer carry multiple distinct values.

    The blob is best-effort deleted (warning on failure) so a stale on-disk file
    can never block the metadata cleanup.
    """
    affected_pairs: set[tuple[UUID, str]] = set()
    closed_pairs: list[tuple[UUID, str]] = []

    with store.session() as session:
        doc = session.get(DocumentRow, document_id)
        if doc is None:
            raise KeyError(f"no document with id {document_id!r}")
        blob_key = doc.blob_key

        writes = list(
            session.exec(
                select(FieldWriteRow).where(FieldWriteRow.source_document_id == document_id)
            ).all()
        )
        for w in writes:
            affected_pairs.add((w.entity_id, w.field_name))
            session.delete(w)

        # Recompute distinct values per affected (entity, field) and close any
        # ConflictRow that no longer has >1 distinct value among live writes.
        per_field_values: dict[tuple[UUID, str], set[str]] = defaultdict(set)
        for entity_id, field_name in affected_pairs:
            survivors = session.exec(
                select(FieldWriteRow).where(
                    FieldWriteRow.entity_id == entity_id,
                    FieldWriteRow.field_name == field_name,
                )
            ).all()
            for w in survivors:
                per_field_values[(entity_id, field_name)].add(w.value_json)

        for (entity_id, field_name), distinct in per_field_values.items():
            if len(distinct) > 1:
                continue
            open_conflicts = session.exec(
                select(ConflictRow).where(
                    ConflictRow.entity_id == entity_id,
                    ConflictRow.field_name == field_name,
                    col_is_null(ConflictRow.resolved_at),
                )
            ).all()
            for c in open_conflicts:
                c.resolved_at = datetime.now(UTC)
                session.add(c)
                closed_pairs.append((entity_id, field_name))

        session.delete(doc)
        session.commit()

    _best_effort_delete_blob(store, blob_key)

    return len(writes), closed_pairs


def col_is_null(column):
    """Tiny shim so the `ConflictRow.resolved_at IS NULL` filter reads cleanly without
    pulling SQLAlchemy `col(...)` import boilerplate into the call site."""
    return column.is_(None)


def _best_effort_delete_blob(store: TenantStore, blob_key: str) -> None:
    """Source removal already committed the metadata; if the blob delete fails we
    log and move on rather than corrupt the metadata-blob consistency the other way."""
    try:
        store.blobs.delete(blob_key)
    except OSError:
        logger.warning("source_removal: failed to delete blob %r", blob_key, exc_info=True)


__all__ = ["remove_document"]
