"""Server-side conflict resolution — read-time, pure (modulo the LLM seam).

Called by the API read handler (Step 7) AFTER the ACL evaluator. Given the live
candidates for one (entity, field), pick a winner — or signal UNRESOLVED.

This module is a closed-form dispatcher over `ResolverSpec` variants. The only side
effect is the `SkillResolver` LLM call, which goes through the `LLMClient` seam so
tests can substitute the offline stub deterministically.
"""

from dataclasses import dataclass

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

_RAW_REASON = "raw resolver requested — caller wants both candidates"
_PREFER_AGENT_NO_MATCH = "no candidate written by the preferred agent"
_AUTO_FALLBACK_DEFAULT: LatestWriteResolverSpec = LatestWriteResolverSpec()


@dataclass(frozen=True)
class ResolvedFieldValue:
    """Output of `resolve()`. Caller (Step 7 read handler) builds the wire-form `FieldValue` from this."""

    status: FieldStatus
    winner: FieldWriteRow | None
    candidates: tuple[FieldWriteRow, ...]
    reason: str | None
    resolver_used: ResolverSpec


def resolve(
    *,
    candidates: list[FieldWriteRow],
    spec: ResolverSpec,
    ruleset: RuleSet,
    entity_type: str,
    field_name: str,
    llm: LLMClient,
) -> ResolvedFieldValue:
    """Pick a winner over `candidates` per `spec`. Empty candidates is the caller's bug."""
    if not candidates:
        raise ValueError("resolve() called with zero candidates — caller should short-circuit on UNKNOWN")

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
        winner = max(candidates, key=lambda c: c.written_at)
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
        winner = max(matches, key=lambda c: c.written_at)
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
        )

    # Unreachable in practice — the discriminated union is closed.
    raise TypeError(f"unknown resolver spec: {type(spec).__name__}")


def _auto_dispatch(
    *,
    ruleset: RuleSet,
    entity_type: str,
    field_name: str,
) -> ResolverSpec:
    """Find the active `ConflictRule` for (entity, field); fall back to LatestWrite."""
    for rule in ruleset.rules:
        if (
            isinstance(rule, ConflictRule)
            and rule.entity_type == entity_type
            and rule.field_name == field_name
        ):
            return rule.resolver
    return _AUTO_FALLBACK_DEFAULT


__all__ = ["ResolvedFieldValue", "resolve"]
