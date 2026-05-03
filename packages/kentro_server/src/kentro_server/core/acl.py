"""ACL evaluator — the foundational pure functions for read / write / visibility.

All functions are total: given any (entity, field, agent, ruleset) they return an
`AclDecision`. No exceptions, no side effects.

## Policy combining

Multiple rules can match a single operation. The combining algorithm is fixed:

1. **Deny overrides allow.** If any matching rule has `allowed=False`, the result is
   denied with that rule's denial reason.
2. **Otherwise, any matching `allowed=True` rule grants access.**
3. **Default deny.** If no rule matches, the operation is denied (least privilege).
4. **`requires_approval` acts as a deny in v0.** No approval workflow exists yet, so a
   write rule with `allowed=True, requires_approval=True` denies with a clear reason.
   The flag survives in the rule schema so a future approval workflow can act on it.

This matches Snowflake / BigQuery row-policy semantics and the locked decisions in
`memory.md` (Section: "SDK Design — locked decisions (v0)").

## Specificity (planned, not implemented in v0)

`EntityVisibilityRule.entity_key` and `WriteRule.field_name` are both optional.
A `None` value means "applies to all keys / all fields of this type". The current
combining algorithm treats wildcard and specific rules identically; this is fine for
the demo's hand-authored ruleset where rules are mutually exclusive in practice. If a
future ruleset has overlapping wildcard and specific rules with conflicting decisions,
revisit the combining algorithm before relying on a particular outcome.
"""

from kentro.types import (
    EntityVisibilityRule,
    FieldReadRule,
    Rule,
    RuleSet,
    WriteRule,
)
from pydantic import BaseModel, ConfigDict


class AclDecision(BaseModel):
    """Outcome of a single ACL evaluation."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str | None = None


_DENIED_BY_DEFAULT = "no rule grants access"
_REQUIRES_APPROVAL = "write blocked: manager approval required"


def evaluate_field_read(
    *,
    entity_type: str,
    field_name: str,
    agent_id: str,
    ruleset: RuleSet,
) -> AclDecision:
    """Decide whether `agent_id` may read `entity_type.field_name`."""
    matches = [
        r
        for r in ruleset.rules
        if isinstance(r, FieldReadRule)
        and r.agent_id == agent_id
        and r.entity_type == entity_type
        and r.field_name == field_name
    ]
    return _combine(matches)


def evaluate_entity_visibility(
    *,
    entity_type: str,
    entity_key: str,
    agent_id: str,
    ruleset: RuleSet,
) -> AclDecision:
    """Decide whether `agent_id` may see entities of `entity_type` (or this specific key)."""
    matches: list[Rule] = [
        r
        for r in ruleset.rules
        if isinstance(r, EntityVisibilityRule)
        and r.agent_id == agent_id
        and r.entity_type == entity_type
        and (r.entity_key is None or r.entity_key == entity_key)
    ]
    return _combine(matches)


def evaluate_write(
    *,
    entity_type: str,
    field_name: str | None,
    agent_id: str,
    ruleset: RuleSet,
) -> AclDecision:
    """Decide whether `agent_id` may write `entity_type.field_name`.

    `field_name=None` represents whole-entity operations (create, delete). The
    matching `WriteRule` may also have `field_name=None` (wildcard) or a specific name.
    """
    matches: list[Rule] = [
        r
        for r in ruleset.rules
        if isinstance(r, WriteRule)
        and r.agent_id == agent_id
        and r.entity_type == entity_type
        and (r.field_name is None or r.field_name == field_name)
    ]
    # Approval-required acts as a deny in v0 — handle before _combine so the reason is specific.
    for r in matches:
        if isinstance(r, WriteRule) and r.allowed and r.requires_approval:
            return AclDecision(allowed=False, reason=_REQUIRES_APPROVAL)
    return _combine(matches)


def _combine(matches: list) -> AclDecision:
    """Combine matching rules per the policy in this module's docstring."""
    if not matches:
        return AclDecision(allowed=False, reason=_DENIED_BY_DEFAULT)
    deny = next((r for r in matches if not r.allowed), None)
    if deny is not None:
        return AclDecision(allowed=False, reason=f"explicit deny on {_describe(deny)}")
    # At least one allow, no deny.
    return AclDecision(allowed=True, reason=None)


def _describe(rule: object) -> str:
    """Short label for a rule used in denial reasons."""
    return type(rule).__name__


__all__ = [
    "AclDecision",
    "evaluate_entity_visibility",
    "evaluate_field_read",
    "evaluate_write",
]
