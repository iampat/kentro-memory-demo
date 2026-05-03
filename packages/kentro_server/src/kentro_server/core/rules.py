"""Rule version management — atomic apply + version bump + load-active.

Rule versions are monotonically increasing integers. `apply_ruleset` writes a new
version (containing every rule from the supplied `RuleSet`) atomically; readers
always use the latest version. Old versions remain in the DB so existing
`FieldWriteRow.rule_version_at_write` references stay resolvable for lineage.

The "no record-level re-ingestion on rule change" guarantee from `memory.md` is
honored here: applying a new rule version does NOT touch existing field-write
rows. Subsequent reads consult the new version when re-resolving conflicts; old
writes carry the rule version they were made under for lineage purposes.
"""

import logging

from kentro.types import Rule, RuleSet
from pydantic import TypeAdapter
from sqlalchemy import func
from sqlmodel import Session, select

from kentro_server.store import TenantStore
from kentro_server.store.models import RuleRow, RuleVersionRow

_RULE_ADAPTER: TypeAdapter[Rule] = TypeAdapter(Rule)

logger = logging.getLogger(__name__)


def apply_ruleset(
    store: TenantStore,
    *,
    rules: tuple[Rule, ...],
    summary: str | None = None,
) -> int:
    """Atomically write a new RuleVersionRow + one RuleRow per rule. Returns the new version."""
    with store.session() as session:
        next_version = _next_version(session)
        version_row = RuleVersionRow(version=next_version, summary=summary)
        session.add(version_row)
        for rule in rules:
            payload = rule.model_dump_json()
            session.add(
                RuleRow(
                    rule_version=next_version,
                    rule_type=rule.type,
                    payload_json=payload,
                )
            )
        session.commit()
    logger.info(
        "tenant=%s applied rule version %d (%d rules)",
        store.tenant_id,
        next_version,
        len(rules),
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
        rule_rows = list(session.exec(select(RuleRow).where(RuleRow.rule_version == latest)).all())
    rules = tuple(_payload_to_rule(row.payload_json) for row in rule_rows)
    return RuleSet(rules=rules, version=latest)


def _next_version(session: Session) -> int:
    """SELECT MAX(version) + 1, or 1 if none exist yet."""
    current = session.exec(
        select(func.max(RuleVersionRow.version))  # type: ignore[arg-type]
    ).one_or_none()
    if current is None:
        return 1
    return int(current) + 1


def _payload_to_rule(payload_json: str) -> Rule:
    """Parse a stored Rule JSON into the discriminated-union variant."""
    return _RULE_ADAPTER.validate_json(payload_json)


__all__ = ["apply_ruleset", "load_active_ruleset"]
