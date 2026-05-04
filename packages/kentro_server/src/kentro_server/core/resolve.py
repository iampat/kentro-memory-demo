"""Server-side conflict resolution — read-time, pure (modulo the LLM seam).

Called by the API read handler (Step 7) AFTER the ACL evaluator. Given the live
candidates for one (entity, field), pick a winner — or signal UNRESOLVED.

This module is a closed-form dispatcher over `ResolverSpec` variants. The only side
effect is the `SkillResolver` LLM call, which goes through the `LLMClient` seam so
tests can substitute the offline stub deterministically.

TODO(workflow-aware-skills, planned for pre-Step-10): after a SkillResolver
returns a decision, walk `decision.actions` and execute each action through
the same write_field path (so ACL applies). Action types: "write_entity"
(creates a Ticket-style entity inline), "notify" (emits a console-log +
websocket event for the UI to render as a toast). See the matching TODO on
SkillResolverDecision in skills/llm_client.py for the schema. Lands the
"memory is the workflow trigger" demo beat. Tracked in IMPLEMENTATION_PLAN.md
"Deferred to the very end".
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from kentro.types import (
    AutoResolverSpec,
    ConflictRule,
    FieldStatus,
    LatestWriteResolverSpec,
    PreferAgentResolverSpec,
    RawResolverSpec,
    ResolverSpec,
    RuleSet,
    SkillResolverSpec,
)

from kentro_server.skills.llm_client import LLMClient
from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)

_RAW_REASON = "raw resolver requested — caller wants both candidates"
_PREFER_AGENT_NO_MATCH = "no candidate written by the preferred agent"
_AUTO_FALLBACK_DEFAULT: LatestWriteResolverSpec = LatestWriteResolverSpec()


@dataclass(frozen=True)
class ResolvedFieldValue:
    """Output of `resolve()`. Caller (Step 7 read handler) builds the wire-form `FieldValue` from this.

    `actions` carries any workflow steps a SkillResolver wants executed
    alongside the pick. The caller is responsible for executing them through
    the same ACL gate as a regular write — `resolve()` itself is side-effect-
    free (modulo the LLM call). PR 10-5 adds this field; the entities route
    walks it after a successful resolve.
    """

    status: FieldStatus
    winner: FieldWriteRow | None
    candidates: tuple[FieldWriteRow, ...]
    reason: str | None
    resolver_used: ResolverSpec
    actions: tuple = ()  # tuple[SkillAction, ...] — kept untyped here to avoid
    # importing SkillAction from skills/llm_client.py (would be a cycle).


def resolve(
    *,
    candidates: list[FieldWriteRow],
    spec: ResolverSpec,
    ruleset: RuleSet,
    entity_type: str,
    field_name: str,
    llm: LLMClient,
    tie_break_seq_by_write_id: dict[UUID, int | None] | None = None,
) -> ResolvedFieldValue:
    """Pick a winner over `candidates` per `spec`. Empty candidates is the caller's bug.

    `tie_break_seq_by_write_id` maps each candidate's `id` to its owning event's
    `activation_seq` (or None when the write predates the event system / is an
    admin-direct write). LatestWrite-style policies sort by this sequence so
    re-toggling an event makes its writes "newest" in resolver-tie-break terms.
    Corroboration / single-candidate fast paths still use `written_at` since the
    chosen lineage record is purely cosmetic when all values agree.
    """
    if not candidates:
        raise ValueError(
            "resolve() called with zero candidates — caller should short-circuit on UNKNOWN"
        )

    seq_map = tie_break_seq_by_write_id or {}

    # Single-candidate fast path: nothing to resolve.
    if len(candidates) == 1:
        return ResolvedFieldValue(
            status=FieldStatus.KNOWN,
            winner=candidates[0],
            candidates=tuple(candidates),
            reason=None,
            resolver_used=spec,
        )

    # Corroboration fast path: many writes, one distinct value.
    distinct_values = {c.value_json for c in candidates}
    if len(distinct_values) == 1:
        return ResolvedFieldValue(
            status=FieldStatus.KNOWN,
            winner=max(candidates, key=lambda c: c.written_at),
            candidates=tuple(candidates),
            reason=None,
            resolver_used=spec,
        )

    # AutoResolver dispatches to the schema's ConflictRule (or LatestWrite by default).
    if isinstance(spec, AutoResolverSpec):
        spec = _auto_dispatch(ruleset=ruleset, entity_type=entity_type, field_name=field_name)

    if isinstance(spec, RawResolverSpec):
        return ResolvedFieldValue(
            status=FieldStatus.UNRESOLVED,
            winner=None,
            candidates=tuple(candidates),
            reason=_RAW_REASON,
            resolver_used=spec,
        )

    if isinstance(spec, LatestWriteResolverSpec):
        winner = max(candidates, key=lambda c: _tie_break_key(c, seq_map))
        return ResolvedFieldValue(
            status=FieldStatus.KNOWN,
            winner=winner,
            candidates=tuple(candidates),
            reason=None,
            resolver_used=spec,
        )

    if isinstance(spec, PreferAgentResolverSpec):
        matches = [c for c in candidates if c.written_by_agent_id == spec.agent_id]
        if not matches:
            return ResolvedFieldValue(
                status=FieldStatus.UNRESOLVED,
                winner=None,
                candidates=tuple(candidates),
                reason=f"{_PREFER_AGENT_NO_MATCH} ({spec.agent_id!r})",
                resolver_used=spec,
            )
        winner = max(matches, key=lambda c: _tie_break_key(c, seq_map))
        return ResolvedFieldValue(
            status=FieldStatus.KNOWN,
            winner=winner,
            candidates=tuple(candidates),
            reason=None,
            resolver_used=spec,
        )

    if isinstance(spec, SkillResolverSpec):
        decision = llm.run_skill_resolver(
            prompt=spec.prompt,
            candidates=candidates,
            model=spec.model,
        )
        if decision.chosen_value_json is None:
            return ResolvedFieldValue(
                status=FieldStatus.UNRESOLVED,
                winner=None,
                candidates=tuple(candidates),
                reason=decision.reason,
                resolver_used=spec,
            )
        winner = next(
            (c for c in candidates if c.value_json == decision.chosen_value_json),
            None,
        )
        if winner is None:
            return ResolvedFieldValue(
                status=FieldStatus.UNRESOLVED,
                winner=None,
                candidates=tuple(candidates),
                reason=(
                    f"skill returned a value not present among candidates: "
                    f"{decision.chosen_value_json!r}"
                ),
                resolver_used=spec,
            )
        return ResolvedFieldValue(
            status=FieldStatus.KNOWN,
            winner=winner,
            candidates=tuple(candidates),
            reason=decision.reason,
            resolver_used=spec,
            actions=decision.actions,
        )

    # Unreachable in practice — the discriminated union is closed.
    raise TypeError(f"unknown resolver spec: {type(spec).__name__}")


def _tie_break_key(candidate: FieldWriteRow, seq_map: dict[UUID, int | None]) -> tuple:
    """Total ordering for LatestWrite-style tie-breaks.

    Primary key: presence-of-event (NULL event_id is "ambient"; ranks above any
    catalog event so admin-direct writes always win against toggle history).
    Secondary key: `activation_seq` (None for catalog events that haven't
    activated yet — treated as 0). Tertiary: `written_at` to keep the order
    deterministic when seqs collide.
    """
    seq = seq_map.get(candidate.id)
    is_ambient = candidate.event_id is None
    return (1 if is_ambient else 0, seq if seq is not None else 0, candidate.written_at)


def _auto_dispatch(
    *,
    ruleset: RuleSet,
    entity_type: str,
    field_name: str,
) -> ResolverSpec:
    """Find the active `ConflictRule` for (entity, field); fall back to LatestWrite.

    Defensive: a `ConflictRule(resolver=AutoResolverSpec())` would cause an
    infinite dispatch loop and ultimately fall through to a TypeError in the
    outer `resolve()`. Treat that case as "no rule" and use the fallback —
    AutoResolver inside ConflictRule is meaningless (auto already IS the read
    path's default). Future v0.1 should reject this at apply time.
    """
    for rule in ruleset.rules:
        if (
            isinstance(rule, ConflictRule)
            and rule.entity_type == entity_type
            and rule.field_name == field_name
        ):
            if isinstance(rule.resolver, AutoResolverSpec):
                logger.warning(
                    "ConflictRule for %s.%s wraps AutoResolverSpec — using fallback %s",
                    entity_type,
                    field_name,
                    type(_AUTO_FALLBACK_DEFAULT).__name__,
                )
                return _AUTO_FALLBACK_DEFAULT
            return rule.resolver
    return _AUTO_FALLBACK_DEFAULT


__all__ = ["ResolvedFieldValue", "resolve"]
