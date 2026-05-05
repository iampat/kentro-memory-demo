"""Read-path orchestrator — composes ACL + conflict resolution + lineage build.

Per the handoff §1.5: for each declared field of the entity → ACL filter → resolve →
build `FieldValue`. Fields the agent cannot see come back as `FieldValue(status=HIDDEN)`
so the caller knows the field exists but is gated. Fields nobody has written come
back as `FieldValue(status=UNKNOWN)`.

The function is pure-Python (no FastAPI). The HTTP route is a thin wrapper.
"""

import hashlib
import json
import logging

from kentro.acl import evaluate_entity_visibility, evaluate_field_read
from kentro.types import (
    EntityRecord,
    FieldStatus,
    FieldValue,
    FieldValueCandidate,
    LineageRecord,
    ResolverPolicySet,
    ResolverSpec,
    RuleSet,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from kentro_server.core.events import Event, EventBus
from kentro_server.core.resolve import resolve
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.core.write import write_field
from kentro_server.skills.llm_client import (
    LLMClient,
    NotifyAction,
    SkillAction,
    SkillResolverSourceMeta,
    WriteEntityAction,
)
from kentro_server.store import TenantStore
from kentro_server.store.models import (
    ConflictRow,
    DocumentRow,
    EntityRow,
    FieldWriteRow,
    SkillActionExecutionRow,
)

logger = logging.getLogger(__name__)


def read_entity(
    store: TenantStore,
    *,
    schema: SchemaRegistry,
    ruleset: RuleSet,
    resolver_policies: ResolverPolicySet,
    agent_id: str,
    entity_type: str,
    entity_key: str,
    resolver: ResolverSpec,
    llm: LLMClient,
    event_bus: EventBus | None = None,
    bypass_acl: bool = False,
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

    `bypass_acl=True` skips both visibility and per-field ACL checks. Callers
    set this for principals with `is_admin=True` so the canonical/global view
    sees every populated field regardless of which FieldReadRules exist (or
    don't). Resolver / lineage logic is unchanged — only the ACL gate is
    bypassed.
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
    # enforcement happens once instead of per-field. Admins (bypass_acl) skip
    # this gate entirely.
    if not bypass_acl:
        visibility = evaluate_entity_visibility(
            entity_type=entity_type,
            entity_key=entity_key,
            agent_id=agent_id,
            ruleset=ruleset,
        )
    else:
        visibility = None
    if visibility is not None and not visibility.allowed:
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
        source_metadata: dict = {}
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
            # Pre-load source_class + label for every distinct source document
            # referenced by the candidates. Used by SkillResolver prompts so
            # policies like "written outweighs verbal" can pivot on modality.
            # One query regardless of field count; harmless when no skill
            # resolver runs (we just don't read the dict).
            doc_ids = {w.source_document_id for w in all_writes if w.source_document_id}
            if doc_ids:
                docs = session.exec(
                    select(DocumentRow).where(col(DocumentRow.id).in_(doc_ids))
                ).all()
                for d in docs:
                    source_metadata[d.id] = SkillResolverSourceMeta(
                        source_class=d.source_class,
                        source_label=d.label,
                    )

    fields: dict[str, FieldValue] = {}
    for field_def in type_def.fields:
        field_name = field_def.name

        if not bypass_acl:
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
            resolver_policies=resolver_policies,
            entity_type=entity_type,
            field_name=field_name,
            llm=llm,
            source_metadata=source_metadata,
        )
        fields[field_name] = _to_field_value(resolved)

        # PR 10-5: workflow-aware Skills. If the resolver decision carried
        # actions, execute each through the same governance gate as a regular
        # write/read. Skills cannot bypass ACL — `write_field()` re-evaluates.
        # Codex 2026-05-03 finding #1: dedupe via SkillActionExecutionRow so
        # a refreshed/retried read does not replay the same action.
        if resolved.actions and entity_row is not None:
            scope_key = _scope_key_for_decision(
                store=store,
                entity_id=entity_row.id,
                field_name=field_name,
                candidates=candidates,
                winner=resolved.winner,
            )
            _execute_actions(
                actions=resolved.actions,
                store=store,
                schema=schema,
                acting_agent_id=agent_id,
                event_bus=event_bus,
                scope_key=scope_key,
            )

    return EntityRecord(entity_type=entity_type, key=entity_key, fields=fields)


def _scope_key_for_decision(
    *,
    store: TenantStore,
    entity_id,
    field_name: str,
    candidates: list[FieldWriteRow],
    winner: FieldWriteRow | None,
) -> str:
    """Build the dedupe scope_key for a resolver decision.

    When >=2 distinct candidate values exist, a `ConflictRow` was opened on
    the write path; we key off its UUID. Otherwise (single-candidate
    corroboration / SkillResolver invented an answer over agreeing writes)
    we key off the winner's field-write UUID. Both cases produce a stable
    identifier across retries — re-running the same read maps to the same
    scope_key, so the UNIQUE(scope_key, action_fingerprint) blocks replays.
    """
    distinct_values = {c.value_json for c in candidates}
    if len(distinct_values) >= 2:
        with store.session() as session:
            conflict = session.exec(
                select(ConflictRow).where(
                    ConflictRow.entity_id == entity_id,
                    ConflictRow.field_name == field_name,
                    col(ConflictRow.resolved_at).is_(None),
                )
            ).first()
            if conflict is not None:
                return f"conflict:{conflict.id}"
    # Fallback: single-candidate or no conflict row found — key off the
    # winning write id (or the latest candidate when winner is missing).
    fallback_write = winner or max(candidates, key=lambda c: c.written_at)
    return f"write:{fallback_write.id}"


def _execute_actions(
    *,
    actions,
    store: TenantStore,
    schema: SchemaRegistry,
    acting_agent_id: str,
    event_bus: EventBus | None,
    scope_key: str,
) -> None:
    """Walk SkillResolverDecision.actions; execute each through the ACL gate.

    Best-effort with logging; one failed action does not abort the others.
    A WriteEntityAction calls `write_field` directly (which re-evaluates ACL
    for the acting agent — no escape hatch). A NotifyAction publishes onto
    the EventBus when one is wired; logs only when not.

    Codex 2026-05-03 finding #1: each action is fingerprinted and gated by
    `SkillActionExecutionRow`. If a row already exists for
    `(scope_key, action_fingerprint)`, the action is skipped. After a
    successful execution we insert the row; the UNIQUE constraint guards
    against a concurrent racer.
    """
    for action in actions:
        fingerprint = _action_fingerprint(action)
        if _already_executed(store, scope_key=scope_key, fingerprint=fingerprint):
            logger.info(
                "skill action: dedupe-skip scope=%s fingerprint=%s action=%s",
                scope_key,
                fingerprint[:12],
                type(action).__name__,
            )
            continue
        match action:
            case WriteEntityAction():
                _execute_write_entity(
                    action=action,
                    store=store,
                    schema=schema,
                    acting_agent_id=acting_agent_id,
                )
            case NotifyAction():
                _execute_notify(action=action, store=store, event_bus=event_bus)
            case _:
                logger.warning("skill action: unknown type %s — dropping", type(action).__name__)
                continue
        _record_execution(
            store,
            scope_key=scope_key,
            fingerprint=fingerprint,
            agent_id=acting_agent_id,
        )


def _execute_write_entity(
    *,
    action: WriteEntityAction,
    store: TenantStore,
    schema: SchemaRegistry,
    acting_agent_id: str,
) -> None:
    """Run a WriteEntityAction through the same ACL gate as a regular write."""
    try:
        result = write_field(
            store=store,
            schema=schema,
            agent_id=acting_agent_id,
            entity_type=action.entity_type,
            entity_key=action.entity_key,
            field_name=action.field_name,
            value_json=action.value_json,
        )
        logger.info(
            "skill action: WriteEntity %s/%s.%s → %s",
            action.entity_type,
            action.entity_key,
            action.field_name,
            result.status.value,
        )
    except (ValueError, RuntimeError, IntegrityError) as exc:
        logger.error("skill action WriteEntity failed: %r (%s)", action, exc, exc_info=True)


def _execute_notify(
    *,
    action: NotifyAction,
    store: TenantStore,
    event_bus: EventBus | None,
) -> None:
    """Publish a NotifyAction onto the EventBus (when wired) and always log."""
    logger.info("skill notify %s: %s", action.channel, action.message)
    if event_bus is None:
        return
    event_bus.publish(
        Event(
            kind="notify",
            tenant_id=store.tenant_id,
            payload={
                "channel": action.channel,
                "message": action.message,
            },
        )
    )


def _action_fingerprint(action: SkillAction) -> str:
    """Stable SHA-256 over the action's normalized payload.

    Different action types use different fields, so the fingerprint includes
    `type` to keep the namespace clean. Sorted JSON keeps the byte string
    deterministic across Python runs.
    """
    match action:
        case WriteEntityAction():
            payload = {
                "type": action.type,
                "entity_type": action.entity_type,
                "entity_key": action.entity_key,
                "field_name": action.field_name,
                "value_json": action.value_json,
            }
        case NotifyAction():
            payload = {
                "type": action.type,
                "channel": action.channel,
                "message": action.message,
            }
        case _:
            # Fall back to repr for unknown types — they're skipped before this
            # path normally, but defense in depth.
            payload = {"type": "unknown", "repr": repr(action)}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _already_executed(store: TenantStore, *, scope_key: str, fingerprint: str) -> bool:
    """Return True if a SkillActionExecutionRow already records this action."""
    with store.session() as session:
        row = session.exec(
            select(SkillActionExecutionRow).where(
                SkillActionExecutionRow.scope_key == scope_key,
                SkillActionExecutionRow.action_fingerprint == fingerprint,
            )
        ).first()
    return row is not None


def _record_execution(
    store: TenantStore,
    *,
    scope_key: str,
    fingerprint: str,
    agent_id: str,
) -> None:
    """Insert the dedupe row. A UNIQUE-constraint race is silent (already done)."""
    try:
        with store.session() as session:
            session.add(
                SkillActionExecutionRow(
                    scope_key=scope_key,
                    action_fingerprint=fingerprint,
                    executed_by_agent_id=agent_id,
                )
            )
            session.commit()
    except IntegrityError:
        # Concurrent racer beat us to it — the action ran twice in this window
        # but the constraint protects future calls. Log so we have a signal if
        # this gets noisy.
        logger.info(
            "skill action: race-condition dedupe scope=%s fingerprint=%s",
            scope_key,
            fingerprint[:12],
        )


def _to_field_value(resolved) -> FieldValue:
    """Translate a `core.resolve.ResolvedFieldValue` into the SDK-shaped FieldValue.

    For KNOWN status the resolver returns every candidate that contributed —
    multiple corroborating writes can carry the same value via different
    sources. We surface all of them in `lineage` (one record per write,
    deduplicated by source_document_id so a doc that wrote the same field
    twice doesn't double-count) so the UI can show all sources that backed
    the resolved value, not just the latest one.

    Synthesised winners (SkillResolver in `synthesize` mode) have no row in
    `candidates` — the value was produced by the LLM. Lineage attributes
    every candidate as a contributing source; there is no anchor row.
    """
    # Synthesised winner — winner is None but synthesized_value_json is set.
    if (
        resolved.status == FieldStatus.KNOWN
        and resolved.winner is None
        and resolved.synthesized_value_json is not None
    ):
        chronological = sorted(resolved.candidates, key=lambda c: c.written_at)
        seen_sources: set = set()
        lineage_list: list[LineageRecord] = []
        for c in chronological:
            if c.source_document_id is not None:
                if c.source_document_id in seen_sources:
                    continue
                seen_sources.add(c.source_document_id)
            lineage_list.append(
                LineageRecord(
                    source_document_id=c.source_document_id,
                    written_at=c.written_at,
                    written_by_agent_id=c.written_by_agent_id,
                    rule_version=c.rule_version_at_write,
                    extraction_step_id=c.extraction_step_id,
                    value=_decode(c.value_json),
                )
            )
        return FieldValue(
            status=FieldStatus.KNOWN,
            value=_decode(resolved.synthesized_value_json),
            confidence=resolved.synthesized_confidence,
            lineage=tuple(lineage_list),
        )

    if resolved.status == FieldStatus.KNOWN and resolved.winner is not None:
        winner = resolved.winner
        # All candidates corroborate the resolved value (the resolver's
        # KNOWN-status fast paths require either a single candidate, or many
        # candidates with one distinct value). Emit a LineageRecord per
        # candidate so the UI can render every source that backed the call.
        # Sort: winner first, then chronological — keeps the demo's "this
        # is the source of truth" anchor visible while preserving timeline
        # readability for the corroborating sources below.
        chronological = sorted(resolved.candidates, key=lambda c: c.written_at)
        ordered = [winner] + [c for c in chronological if c.id != winner.id]
        seen_sources: set = set()
        lineage_list: list[LineageRecord] = []
        for c in ordered:
            # Dedupe by source_document_id only when set — multiple writes
            # from the same doc (rare but possible if a doc is re-ingested)
            # collapse to one lineage row. Writes with no source (manual
            # API writes) keep their own row.
            if c.source_document_id is not None:
                if c.source_document_id in seen_sources:
                    continue
                seen_sources.add(c.source_document_id)
            lineage_list.append(
                LineageRecord(
                    source_document_id=c.source_document_id,
                    written_at=c.written_at,
                    written_by_agent_id=c.written_by_agent_id,
                    rule_version=c.rule_version_at_write,
                    extraction_step_id=c.extraction_step_id,
                    value=_decode(c.value_json),
                )
            )
        return FieldValue(
            status=FieldStatus.KNOWN,
            value=_decode(winner.value_json),
            confidence=winner.confidence,
            lineage=tuple(lineage_list),
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
                    value=_decode(c.value_json),
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
