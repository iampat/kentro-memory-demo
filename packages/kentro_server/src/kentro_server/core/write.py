"""Write-path orchestrator — ACL check + record_field_write + WriteResult mapping.

Per the handoff §1.5: write checks ACL first; if a conflict exists with existing live
writes, both are stored (per `core/conflict.record_field_write`); the typed
`WriteResult` carries the outcome (no exceptions for permission-denied or
conflict-recorded — those are domain outcomes the SDK enum represents).

Versioning: `write_field` loads the active ruleset *once* and uses the same
version for both the ACL check and the lineage stamp (`rule_version_at_write`).
The previous implementation took a caller-supplied `ruleset_version` AND
re-loaded internally, which left a window where the ACL check ran under v(N+1)
while lineage stamped v(N). One source of truth, no skew.
"""

import logging

from kentro.acl import evaluate_write
from kentro.types import WriteResult, WriteStatus
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from kentro_server.core.conflict import record_field_write
from kentro_server.core.rules import load_active_ruleset
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.store import TenantStore
from kentro_server.store.models import EntityRow

logger = logging.getLogger(__name__)


def write_field(
    store: TenantStore,
    *,
    schema: SchemaRegistry,
    agent_id: str,
    entity_type: str,
    entity_key: str,
    field_name: str,
    value_json: str,
    confidence: float | None = None,
) -> WriteResult:
    """Write `value_json` to `(entity_type, entity_key).field_name` as `agent_id`.

    Loads the active ruleset internally — the ACL check and the lineage stamp
    both reference the same version, no caller-supplied `ruleset_version` to
    drift out of sync.

    Returns a `WriteResult` with status enum:
      - APPLIED: write recorded, no conflict
      - CONFLICT_RECORDED: write recorded; a conflict now exists for this field
      - PERMISSION_DENIED: ACL denied; no write
    """
    type_def = schema.get(entity_type)
    if type_def is None:
        return WriteResult(
            status=WriteStatus.PERMISSION_DENIED,
            entity_type=entity_type,
            entity_key=entity_key,
            field_name=field_name,
            reason=f"unregistered entity_type {entity_type!r}",
        )

    if not _field_writable(type_def, field_name):
        return WriteResult(
            status=WriteStatus.PERMISSION_DENIED,
            entity_type=entity_type,
            entity_key=entity_key,
            field_name=field_name,
            reason=(f"field {field_name!r} is not declared on {entity_type!r} (or is deprecated)"),
        )

    ruleset = load_active_ruleset(store)
    acl = evaluate_write(
        entity_type=entity_type,
        field_name=field_name,
        agent_id=agent_id,
        ruleset=ruleset,
    )
    if not acl.allowed:
        return WriteResult(
            status=WriteStatus.PERMISSION_DENIED,
            entity_type=entity_type,
            entity_key=entity_key,
            field_name=field_name,
            reason=acl.reason,
        )

    with store.session() as session:
        entity_id = _get_or_create_entity(session, entity_type=entity_type, key=entity_key)
        _, conflict_row = record_field_write(
            session,
            entity_id=entity_id,
            field_name=field_name,
            value_json=value_json,
            confidence=confidence,
            written_by_agent_id=agent_id,
            rule_version_at_write=ruleset.version,
        )
        session.commit()
        conflict_id = conflict_row.id if conflict_row is not None else None

    if conflict_id is not None:
        return WriteResult(
            status=WriteStatus.CONFLICT_RECORDED,
            entity_type=entity_type,
            entity_key=entity_key,
            field_name=field_name,
            conflict_id=conflict_id,
        )
    return WriteResult(
        status=WriteStatus.APPLIED,
        entity_type=entity_type,
        entity_key=entity_key,
        field_name=field_name,
    )


def write_fields_bulk(
    store: TenantStore,
    *,
    schema: SchemaRegistry,
    agent_id: str,
    entity_type: str,
    entity_key: str,
    fields: list[tuple[str, str, float | None]],
) -> list[WriteResult]:
    """Write multiple fields onto one entity in a single transaction.

    All-or-nothing semantics: every field's ACL is evaluated up-front against
    the SAME loaded ruleset (no version skew); if ANY field is denied or its
    type is unwritable, the whole call returns PERMISSION_DENIED results for
    every field and nothing is written. If ACL passes for all, the writes
    execute inside one `session.begin()` block so a DB-level exception on
    field N rolls back fields 1..N-1.

    Returns one `WriteResult` per input field, in the same order as `fields`.
    Codex 2026-05-03 high finding fix for `/memory/remember` non-atomicity —
    apply this helper anywhere a single logical "remember" expands into N
    field writes.
    """
    type_def = schema.get(entity_type)
    if type_def is None:
        return [
            WriteResult(
                status=WriteStatus.PERMISSION_DENIED,
                entity_type=entity_type,
                entity_key=entity_key,
                field_name=fname,
                reason=f"unregistered entity_type {entity_type!r}",
            )
            for fname, _, _ in fields
        ]

    ruleset = load_active_ruleset(store)

    # Up-front: every field must pass ACL + writability before we touch the DB.
    for fname, _, _ in fields:
        if not _field_writable(type_def, fname):
            reason = f"field {fname!r} is not declared on {entity_type!r} (or is deprecated)"
            return [
                WriteResult(
                    status=WriteStatus.PERMISSION_DENIED,
                    entity_type=entity_type,
                    entity_key=entity_key,
                    field_name=f,
                    reason=reason if f == fname else "atomic-write rejected (sibling denial)",
                )
                for f, _, _ in fields
            ]
        acl = evaluate_write(
            entity_type=entity_type,
            field_name=fname,
            agent_id=agent_id,
            ruleset=ruleset,
        )
        if not acl.allowed:
            return [
                WriteResult(
                    status=WriteStatus.PERMISSION_DENIED,
                    entity_type=entity_type,
                    entity_key=entity_key,
                    field_name=f,
                    reason=acl.reason if f == fname else "atomic-write rejected (sibling denial)",
                )
                for f, _, _ in fields
            ]

    results: list[WriteResult] = []
    with store.session() as session, session.begin():
        entity_id = _get_or_create_entity(session, entity_type=entity_type, key=entity_key)
        for fname, value_json, confidence in fields:
            _, conflict_row = record_field_write(
                session,
                entity_id=entity_id,
                field_name=fname,
                value_json=value_json,
                confidence=confidence,
                written_by_agent_id=agent_id,
                rule_version_at_write=ruleset.version,
            )
            if conflict_row is not None:
                results.append(
                    WriteResult(
                        status=WriteStatus.CONFLICT_RECORDED,
                        entity_type=entity_type,
                        entity_key=entity_key,
                        field_name=fname,
                        conflict_id=conflict_row.id,
                    )
                )
            else:
                results.append(
                    WriteResult(
                        status=WriteStatus.APPLIED,
                        entity_type=entity_type,
                        entity_key=entity_key,
                        field_name=fname,
                    )
                )
    return results


def _field_writable(type_def, field_name: str) -> bool:
    for f in type_def.fields:
        if f.name == field_name:
            return not f.deprecated
    return False


def _get_or_create_entity(session, *, entity_type: str, key: str):
    """Same race-safe get-or-create as the ingestor uses, inlined for the write path."""
    existing = session.exec(
        select(EntityRow).where(
            EntityRow.type == entity_type,
            EntityRow.key == key,
        )
    ).first()
    if existing is not None:
        return existing.id
    new_entity = EntityRow(type=entity_type, key=key)
    session.add(new_entity)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        winner = session.exec(
            select(EntityRow).where(
                EntityRow.type == entity_type,
                EntityRow.key == key,
            )
        ).first()
        if winner is None:
            raise
        return winner.id
    return new_entity.id


__all__ = ["write_field", "write_fields_bulk"]
