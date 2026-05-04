"""Resolver-policy version management — sibling to `core.rules`.

Conflict resolution is governed by `ResolverPolicy` records (one per
`(entity_type, field_name)`), stored in `entity_type_resolvers` — one row per
(version, entity_type) holding a JSON array of every policy for that type.

Versioned together with ACL rules: `apply_resolver_policy()` and
`apply_ruleset()` both bump the same `RuleVersionRow`. A snapshot is therefore
always (rules, resolvers, version) atomic. Reads use whatever is at the latest
version.

UPSERT semantics: a new policy for `(entity_type, field_name)` replaces the
existing policy for the same field. This mirrors `core.rules` so the apply
flow looks the same on both sides.
"""

import json
import logging
from typing import Any

from kentro.types import ResolverPolicy, ResolverPolicySet
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlmodel import Session, select

from kentro_server.store import TenantStore
from kentro_server.store.models import (
    EntityTypeResolversRow,
    EntityTypeRulesRow,
    RuleVersionRow,
)

_POLICY_ADAPTER: TypeAdapter[ResolverPolicy] = TypeAdapter(ResolverPolicy)

logger = logging.getLogger(__name__)


def _policy_key(policy: ResolverPolicy) -> tuple[str, str]:
    """A resolver policy's identity is just (entity_type, field_name)."""
    return (policy.entity_type, policy.field_name)


def apply_resolver_policies(
    store: TenantStore,
    *,
    policies: tuple[ResolverPolicy, ...],
    summary: str | None = None,
) -> int:
    """Atomically commit resolver policies as a new rule version. UPSERT.

    Mirrors `apply_ruleset`: read latest, layer on top, write a fresh version.
    Returns the new version.
    """
    with store.session() as session:
        next_version = _next_version(session)
        merged: dict[tuple[str, str], ResolverPolicy] = {}
        latest = session.exec(
            select(func.max(RuleVersionRow.version))  # type: ignore[arg-type]
        ).one_or_none()
        if latest is not None:
            for row in session.exec(
                select(EntityTypeResolversRow).where(EntityTypeResolversRow.rule_version == latest)
            ).all():
                for p in _decode_policies(row.resolvers_json):
                    merged[_policy_key(p)] = p
        for policy in policies:
            merged[_policy_key(policy)] = policy

        version_row = RuleVersionRow(version=next_version, summary=summary)
        session.add(version_row)

        by_type: dict[str, list[ResolverPolicy]] = {}
        for p in merged.values():
            by_type.setdefault(p.entity_type, []).append(p)
        for entity_type, type_policies in by_type.items():
            payload = json.dumps([_policy_to_dict(p) for p in type_policies])
            session.add(
                EntityTypeResolversRow(
                    rule_version=next_version,
                    entity_type=entity_type,
                    resolvers_json=payload,
                )
            )
        # Forward-port ACL rows from the previous version so the new version
        # is a complete snapshot. Without this, applying resolvers would
        # silently wipe the active ACL ruleset.
        if latest is not None:
            for row in session.exec(
                select(EntityTypeRulesRow).where(EntityTypeRulesRow.rule_version == latest)
            ).all():
                session.add(
                    EntityTypeRulesRow(
                        rule_version=next_version,
                        entity_type=row.entity_type,
                        rules_json=row.rules_json,
                    )
                )
        session.commit()
    logger.info(
        "tenant=%s applied resolver-policy version %d (%d policies across %d types)",
        store.tenant_id,
        next_version,
        sum(len(ps) for ps in by_type.values()),
        len(by_type),
    )
    return next_version


def load_active_resolver_policies(store: TenantStore) -> ResolverPolicySet:
    """Return the ResolverPolicySet at the latest applied version (empty if none)."""
    with store.session() as session:
        latest = session.exec(
            select(func.max(RuleVersionRow.version))  # type: ignore[arg-type]
        ).one_or_none()
        if latest is None:
            return ResolverPolicySet(policies=(), version=0)
        rows = list(
            session.exec(
                select(EntityTypeResolversRow).where(EntityTypeResolversRow.rule_version == latest)
            ).all()
        )
    flat: list[ResolverPolicy] = []
    for row in rows:
        flat.extend(_decode_policies(row.resolvers_json))
    return ResolverPolicySet(policies=tuple(flat), version=latest)


def _next_version(session: Session) -> int:
    current = session.exec(
        select(func.max(RuleVersionRow.version))  # type: ignore[arg-type]
    ).one_or_none()
    if current is None:
        return 1
    return int(current) + 1


def _decode_policies(resolvers_json: str) -> list[ResolverPolicy]:
    raw = json.loads(resolvers_json)
    return [_POLICY_ADAPTER.validate_python(item) for item in raw]


def _policy_to_dict(policy: ResolverPolicy) -> dict[str, Any]:
    return policy.model_dump(mode="json")


__all__ = ["apply_resolver_policies", "load_active_resolver_policies"]
