"""Canonical initial demo ruleset + resolver policies — the state Scene 1
begins from.

Used by `kentro-server seed-demo` (CLI) and `POST /demo/seed` so both paths
produce the same starting world. Mirrors the prototype's `data.js::
initialPolicies`, expressed as typed `Rule`s + `ResolverPolicy`s.

Scene-by-scene narrative (per `demo.md`):
  - Scene 1: Sales reads all Customer fields; CS sees only contact info;
    AuditLog is hidden from Sales; conflicts resolve latest-write.
  - Scene 4 onwards: admin tightens rules through the policy editor (deal_size
    redacted from CS; written-vs-verbal SkillResolver). Those changes are NOT
    seeded — the demo applies them live during the walk-through.

Per PR 35: wildcard write rules are no longer supported (`WriteRule.field_name`
is required), so the seed enumerates a `WriteRule` per (agent, entity, field).
Resolvers live in `initial_demo_resolvers()` — separate from the ACL ruleset.
"""

from kentro.types import (
    EntityVisibilityRule,
    FieldReadRule,
    LatestWriteResolverSpec,
    ResolverPolicy,
    ResolverPolicySet,
    Rule,
    RuleSet,
    WriteRule,
)

# Canonical Customer fields used by the demo. Kept in lockstep with the
# Customer schema in `kentro_server.demo.schemas`.
_CUSTOMER_FIELDS = ("name", "contact", "deal_size", "sales_notes", "support_tickets")
_CUSTOMER_FIELDS_FOR_CS = ("name", "contact", "support_tickets")

# Per-type field lists for the per-field WriteRule expansion below. Only
# entity types whose fields the ingestion_agent must persist need entries here;
# we don't write Person/AuditLog/Note in the seed corpus.
_DEAL_FIELDS = ("customer", "size", "stage")
_PERSON_FIELDS = ("full_name", "email")
_AUDITLOG_FIELDS = ("event_type", "subject_id", "occurred_at")
_NOTE_FIELDS = ("subject", "predicate", "object_json", "confidence", "source_label")


def _expand_write_rules(agent_id: str, entity_type: str, fields: tuple[str, ...]) -> list[Rule]:
    """One WriteRule per (agent, entity_type, field) — wildcards retired."""
    return [
        WriteRule(agent_id=agent_id, entity_type=entity_type, field_name=f, allowed=True)
        for f in fields
    ]


def initial_demo_ruleset() -> RuleSet:
    """Return the Scene-1 ACL ruleset for the demo tenant. Resolvers are
    separate — see `initial_demo_resolvers()`."""
    rules: list[Rule] = []

    # === Ingestion grants ===
    # ingestion_agent is a non-admin worker that writes extracted facts. It
    # needs explicit per-field WriteRules everywhere it persists data, plus
    # entity-visibility allows.
    for etype in ("Customer", "Person", "Deal", "AuditLog", "Note"):
        rules.append(
            EntityVisibilityRule(agent_id="ingestion_agent", entity_type=etype, allowed=True)
        )
    rules.extend(_expand_write_rules("ingestion_agent", "Customer", _CUSTOMER_FIELDS))
    rules.extend(_expand_write_rules("ingestion_agent", "Person", _PERSON_FIELDS))
    rules.extend(_expand_write_rules("ingestion_agent", "Deal", _DEAL_FIELDS))
    rules.extend(_expand_write_rules("ingestion_agent", "AuditLog", _AUDITLOG_FIELDS))
    rules.extend(_expand_write_rules("ingestion_agent", "Note", _NOTE_FIELDS))
    # ingestion_agent reads everything it writes (used by the upstream
    # extractor's de-dupe + lineage paths).
    rules.extend(
        FieldReadRule(
            agent_id="ingestion_agent", entity_type="Customer", field_name=f, allowed=True
        )
        for f in _CUSTOMER_FIELDS
    )

    # === Sales ===
    rules.append(EntityVisibilityRule(agent_id="sales", entity_type="Customer", allowed=True))
    rules.append(EntityVisibilityRule(agent_id="sales", entity_type="Deal", allowed=True))
    rules.extend(
        FieldReadRule(agent_id="sales", entity_type="Customer", field_name=f, allowed=True)
        for f in _CUSTOMER_FIELDS
    )
    # AuditLog is hidden from Sales.
    rules.append(EntityVisibilityRule(agent_id="sales", entity_type="AuditLog", allowed=False))

    # === Customer Service ===
    rules.append(
        EntityVisibilityRule(agent_id="customer_service", entity_type="Customer", allowed=True)
    )
    rules.append(
        EntityVisibilityRule(agent_id="customer_service", entity_type="AuditLog", allowed=True)
    )
    rules.extend(
        FieldReadRule(
            agent_id="customer_service",
            entity_type="Customer",
            field_name=f,
            allowed=True,
        )
        for f in _CUSTOMER_FIELDS_FOR_CS
    )

    return RuleSet(rules=tuple(rules))


def initial_demo_resolvers() -> ResolverPolicySet:
    """Return the Scene-1 resolver policies. Per `demo.md` cell 12: under the
    initial mechanical rule, Customer.deal_size resolves latest-write. Scene 4
    swaps this for a SkillResolver via the live LineageDrawer editor.

    Other fields with no policy fall through to AutoResolver → LatestWrite at
    read time.
    """
    return ResolverPolicySet(
        policies=(
            ResolverPolicy(
                entity_type="Customer",
                field_name="deal_size",
                resolver=LatestWriteResolverSpec(),
            ),
        )
    )


__all__ = ["initial_demo_resolvers", "initial_demo_ruleset"]
