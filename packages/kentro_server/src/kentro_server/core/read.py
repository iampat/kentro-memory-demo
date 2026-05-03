"""Read-path orchestrator — composes ACL + conflict resolution + lineage build.

Per the handoff §1.5: for each declared field of the entity → ACL filter → resolve →
build `FieldValue`. Fields the agent cannot see come back as `FieldValue(status=HIDDEN)`
so the caller knows the field exists but is gated. Fields nobody has written come
back as `FieldValue(status=UNKNOWN)`.

The function is pure-Python (no FastAPI). The HTTP route is a thin wrapper.
"""

import json
import logging

from kentro.types import (
    EntityRecord,
    FieldStatus,
    FieldValue,
    FieldValueCandidate,
    LineageRecord,
    ResolverSpec,
    RuleSet,
)
from sqlmodel import col, select

from kentro_server.core.acl import evaluate_entity_visibility, evaluate_field_read
from kentro_server.core.resolve import resolve
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.skills.llm_client import LLMClient
from kentro_server.store import TenantStore
from kentro_server.store.models import EntityRow, FieldWriteRow

logger = logging.getLogger(__name__)


def read_entity(
    store: TenantStore,
    *,
    schema: SchemaRegistry,
    ruleset: RuleSet,
    agent_id: str,
    entity_type: str,
    entity_key: str,
    resolver: ResolverSpec,
    llm: LLMClient,
) -> EntityRecord:
    """Build the EntityRecord an agent sees for `(entity_type, entity_key)`.

    Resolution order:
      - For every field declared on the entity's schema:
        - If ACL denies the read for `agent_id`: emit FieldValue(HIDDEN, reason=...)
        - Else if no live writes for the field: emit FieldValue(UNKNOWN)
        - Else: call `resolve(...)` with the supplied resolver and live candidates;
          map result to FieldValue(KNOWN | UNRESOLVED).

    Fields not declared on the schema are NOT returned, even if writes exist for
    them historically — the schema is the source of truth for what an entity is.

    If the entity does not exist in the DB, returns an EntityRecord with all
    declared fields as UNKNOWN. The caller may interpret "all UNKNOWN" as
    "entity not found" if it cares.
    """
    type_def = schema.get(entity_type)
    if type_def is None:
        # Entity type isn't registered. Return an empty record so the caller can
        # detect this; HTTP layer translates to 404 if appropriate.
        return EntityRecord(entity_type=entity_type, key=entity_key, fields={})

    # Entity-visibility ACL gate: short-circuit before we even touch the DB.
    # If the agent cannot see this entity, every declared field comes back
    # HIDDEN with the visibility-denial reason. This is the same outcome shape
    # as field-level deny — uniform from the SDK's perspective — but the
    # enforcement happens once instead of per-field.
    visibility = evaluate_entity_visibility(
        entity_type=entity_type,
        entity_key=entity_key,
        agent_id=agent_id,
        ruleset=ruleset,
    )
    if not visibility.allowed:
        return EntityRecord(
            entity_type=entity_type,
            key=entity_key,
            fields={
                f.name: FieldValue(
                    status=FieldStatus.HIDDEN,
                    reason=visibility.reason or "entity hidden by visibility rule",
                )
                for f in type_def.fields
            },
        )

    with store.session() as session:
        entity_row = session.exec(
            select(EntityRow).where(
                EntityRow.type == entity_type,
                EntityRow.key == entity_key,
            )
        ).first()
        live_writes_by_field: dict[str, list[FieldWriteRow]] = {}
        if entity_row is not None:
            all_writes = list(
                session.exec(
                    select(FieldWriteRow)
                    .where(
                        FieldWriteRow.entity_id == entity_row.id,
                        ~col(FieldWriteRow.superseded),
                    )
                    .order_by(col(FieldWriteRow.written_at))
                ).all()
            )
            for w in all_writes:
                live_writes_by_field.setdefault(w.field_name, []).append(w)

    fields: dict[str, FieldValue] = {}
    for field_def in type_def.fields:
        field_name = field_def.name

        acl = evaluate_field_read(
            entity_type=entity_type,
            field_name=field_name,
            agent_id=agent_id,
            ruleset=ruleset,
        )
        if not acl.allowed:
            fields[field_name] = FieldValue(
                status=FieldStatus.HIDDEN,
                reason=acl.reason or "field hidden by access rule",
            )
            continue

        candidates = live_writes_by_field.get(field_name, [])
        if not candidates:
            fields[field_name] = FieldValue(status=FieldStatus.UNKNOWN)
            continue

        resolved = resolve(
            candidates=candidates,
            spec=resolver,
            ruleset=ruleset,
            entity_type=entity_type,
            field_name=field_name,
            llm=llm,
        )
        fields[field_name] = _to_field_value(resolved)

    return EntityRecord(entity_type=entity_type, key=entity_key, fields=fields)


def _to_field_value(resolved) -> FieldValue:
    """Translate a `core.resolve.ResolvedFieldValue` into the SDK-shaped FieldValue."""
    if resolved.status == FieldStatus.KNOWN and resolved.winner is not None:
        winner = resolved.winner
        lineage = (
            LineageRecord(
                source_document_id=winner.source_document_id,
                written_at=winner.written_at,
                written_by_agent_id=winner.written_by_agent_id,
                rule_version=winner.rule_version_at_write,
                extraction_step_id=winner.extraction_step_id,
            ),
        )
        return FieldValue(
            status=FieldStatus.KNOWN,
            value=_decode(winner.value_json),
            confidence=winner.confidence,
            lineage=lineage,
        )

    # UNRESOLVED — surface every candidate.
    candidates_out = tuple(
        FieldValueCandidate(
            value=_decode(c.value_json),
            confidence=c.confidence,
            lineage=(
                LineageRecord(
                    source_document_id=c.source_document_id,
                    written_at=c.written_at,
                    written_by_agent_id=c.written_by_agent_id,
                    rule_version=c.rule_version_at_write,
                    extraction_step_id=c.extraction_step_id,
                ),
            ),
        )
        for c in resolved.candidates
    )
    return FieldValue(
        status=FieldStatus.UNRESOLVED,
        candidates=candidates_out,
        reason=resolved.reason,
    )


def _decode(value_json: str):
    """Decode a stored JSON-encoded value. On parse failure, log and fall back
    to the raw string — preserves observability over silently swallowing
    corrupt-data bugs (per CLAUDE.md "log before fallback")."""
    try:
        return json.loads(value_json)
    except json.JSONDecodeError:
        logger.warning(
            "read._decode: stored value_json is not valid JSON, returning raw string: %r",
            value_json[:200],
        )
        return value_json


__all__ = ["read_entity"]
