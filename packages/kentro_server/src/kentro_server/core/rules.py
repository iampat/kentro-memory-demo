"""ACL rule version management — per-entity-type storage with UPSERT semantics.

Each `rule_version` is a snapshot of all ACL rules. Storage is one row per
(version, entity_type) carrying a JSON array of every rule for that type, so
applying a change to "Customer" reads/writes one row regardless of how many
Customer rules exist.

`apply_ruleset` merges the supplied rules into the latest version with UPSERT
semantics: rules are keyed by (kind, agent_id, entity_type, field_name|entity_key)
and the new rule replaces any existing rule for the same key. This removes the
need for deny-overrides combining at read time — at most one rule matches a
given (agent, type, field) tuple.

Wildcards (e.g. `WriteRule.field_name=None`) are no longer supported. The SDK
type now requires `field_name: str`. The migration that introduced the new
tables expanded existing wildcard rows against the schema.
"""

import json
import logging
from typing import Any

from kentro.types import (
    EntityVisibilityRule,
    FieldReadRule,
    Rule,
    RuleSet,
    WriteRule,
)
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlmodel import Session, select

from kentro_server.store import TenantStore
from kentro_server.store.models import (
    EntityTypeResolversRow,
    EntityTypeRulesRow,
    RuleVersionRow,
)

_RULE_ADAPTER: TypeAdapter[Rule] = TypeAdapter(Rule)

logger = logging.getLogger(__name__)


def _rule_key(rule: Rule) -> tuple[str, str, str, str]:
    """Stable identity tuple — UPSERT uses this to dedupe.

    Same (kind, agent, entity_type, field|key) → same rule slot. A new rule
    with this key replaces any existing one for the same slot.
    """
    if isinstance(rule, FieldReadRule):
        return ("field_read", rule.agent_id, rule.entity_type, rule.field_name)
    if isinstance(rule, WriteRule):
        return ("write", rule.agent_id, rule.entity_type, rule.field_name)
    if isinstance(rule, EntityVisibilityRule):
        # entity_key=None is a valid identity here ("agent sees this type at
        # all"); narrower per-key rules use the key string.
        return ("entity_visibility", rule.agent_id, rule.entity_type, rule.entity_key or "")
    raise TypeError(f"_rule_key: unknown rule {type(rule).__name__}")


def apply_ruleset(
    store: TenantStore,
    *,
    rules: tuple[Rule, ...],
    summary: str | None = None,
) -> int:
    """Atomically commit a new rule version. UPSERT semantics across types.

    Reads the latest version's rules, indexes them by `_rule_key`, then layers
    the supplied rules on top (later wins for matching keys). Writes one
    `EntityTypeRulesRow` per (new_version, entity_type) and a fresh
    `RuleVersionRow`. Returns the new version.
    """
    with store.session() as session:
        next_version = _next_version(session)
        # Load the previous rules so we layer on top of them. UPSERT means
        # untouched rules survive the version bump.
        merged: dict[tuple[str, str, str, str], Rule] = {}
        latest = session.exec(
            select(func.max(RuleVersionRow.version))  # type: ignore[arg-type]
        ).one_or_none()
        if latest is not None:
            for row in session.exec(
                select(EntityTypeRulesRow).where(EntityTypeRulesRow.rule_version == latest)
            ).all():
                for r in _decode_rules(row.rules_json):
                    merged[_rule_key(r)] = r
        for rule in rules:
            merged[_rule_key(rule)] = rule

        version_row = RuleVersionRow(version=next_version, summary=summary)
        session.add(version_row)

        # Group merged rules by entity_type and write one row per type.
        by_type: dict[str, list[Rule]] = {}
        for r in merged.values():
            by_type.setdefault(r.entity_type, []).append(r)
        for entity_type, type_rules in by_type.items():
            payload = json.dumps([_rule_to_dict(r) for r in type_rules])
            session.add(
                EntityTypeRulesRow(
                    rule_version=next_version,
                    entity_type=entity_type,
                    rules_json=payload,
                )
            )
        # Forward-port the resolver-policy rows from the previous version so
        # the new version is a complete snapshot. Without this, applying a
        # ruleset would silently wipe the active resolver policies because
        # `load_active_resolver_policies` reads "rows at the latest version
        # only".
        if latest is not None:
            for row in session.exec(
                select(EntityTypeResolversRow).where(EntityTypeResolversRow.rule_version == latest)
            ).all():
                session.add(
                    EntityTypeResolversRow(
                        rule_version=next_version,
                        entity_type=row.entity_type,
                        resolvers_json=row.resolvers_json,
                    )
                )
        session.commit()
    logger.info(
        "tenant=%s applied rule version %d (%d rules across %d types)",
        store.tenant_id,
        next_version,
        sum(len(rs) for rs in by_type.values()),
        len(by_type),
    )
    return next_version


def load_active_ruleset(store: TenantStore) -> RuleSet:
    """Return the RuleSet at the latest applied version (empty + version=0 if none)."""
    with store.session() as session:
        latest = session.exec(
            select(func.max(RuleVersionRow.version))  # type: ignore[arg-type]
        ).one_or_none()
        if latest is None:
            return RuleSet(rules=(), version=0)
        rows = list(
            session.exec(
                select(EntityTypeRulesRow).where(EntityTypeRulesRow.rule_version == latest)
            ).all()
        )
    flat: list[Rule] = []
    for row in rows:
        flat.extend(_decode_rules(row.rules_json))
    return RuleSet(rules=tuple(flat), version=latest)


def _next_version(session: Session) -> int:
    current = session.exec(
        select(func.max(RuleVersionRow.version))  # type: ignore[arg-type]
    ).one_or_none()
    if current is None:
        return 1
    return int(current) + 1


def _decode_rules(rules_json: str) -> list[Rule]:
    """Parse a stored rules_json blob into a list of Rule variants."""
    raw_list = json.loads(rules_json)
    return [_RULE_ADAPTER.validate_python(item) for item in raw_list]


def _rule_to_dict(rule: Rule) -> dict[str, Any]:
    return rule.model_dump(mode="json")


__all__ = ["apply_ruleset", "load_active_ruleset"]
