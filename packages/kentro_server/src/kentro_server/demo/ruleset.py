"""Canonical initial demo ruleset — the ACL state Scene 1 begins from.

Used by `kentro-server seed-demo` (CLI) and the upcoming `POST /demo/seed` route
(in PR 10-4) so both paths produce the same starting world. Mirrors the rules
the prototype's `data.js::initialPolicies` ships, expressed as typed `Rule`s.

Scene-by-scene narrative (per `demo.md`):
  - Scene 1: Sales reads all Customer fields; CS sees only contact info; AuditLog
    is hidden from Sales; conflicts resolve latest-write.
  - Scene 4 onwards: admin tightens rules through the policy editor (deal_size
    redacted from CS; written-vs-verbal SkillResolver). Those changes are NOT
    seeded — the demo applies them live during the walk-through.

Plus: the `ingestion_agent` (admin) needs grants for writing every demo entity
type so `POST /documents` actually persists field writes (default-deny ACL
otherwise rejects the writes; the schema-aware extractor would populate field
values that the write path would silently drop).
"""

from kentro.types import (
    AutoResolverSpec,
    ConflictRule,
    EntityVisibilityRule,
    FieldReadRule,
    LatestWriteResolverSpec,
    RuleSet,
    WriteRule,
)

# Canonical Customer fields used by the demo. Kept in lockstep with the Customer
# schema in `kentro_server.demo.schemas`. Hardcoded here (not introspected) so the
# ruleset stays declarative; if the schema gains a field that needs different
# ACL than the default, add it explicitly here.
_CUSTOMER_FIELDS_FOR_SALES = ("name", "contact", "deal_size", "sales_notes", "support_tickets")
_CUSTOMER_FIELDS_FOR_CS = ("name", "contact", "support_tickets")

# Mirror set for the ingestion agent (writes every Customer field via /documents).
_CUSTOMER_FIELDS_INGESTION = _CUSTOMER_FIELDS_FOR_SALES


def initial_demo_ruleset() -> RuleSet:
    """Return the Scene-1 starting ruleset for the demo tenant.

    Idempotent — the rules carry no timestamps; calling `POST /rules/apply` with
    this ruleset twice produces version 1 then version 2 with identical content
    (server stores them as separate snapshots; client-side `viz.ruleset_diff()`
    will say nothing changed).
    """
    rules = (
        # === Ingestion grants ============================================
        # The admin / ingestion_agent needs write everywhere to persist
        # extracted facts. Default-deny otherwise drops them silently.
        EntityVisibilityRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True),
        EntityVisibilityRule(agent_id="ingestion_agent", entity_type="Person", allowed=True),
        EntityVisibilityRule(agent_id="ingestion_agent", entity_type="Deal", allowed=True),
        EntityVisibilityRule(agent_id="ingestion_agent", entity_type="AuditLog", allowed=True),
        EntityVisibilityRule(agent_id="ingestion_agent", entity_type="Note", allowed=True),
        WriteRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True),
        WriteRule(agent_id="ingestion_agent", entity_type="Person", allowed=True),
        WriteRule(agent_id="ingestion_agent", entity_type="Deal", allowed=True),
        WriteRule(agent_id="ingestion_agent", entity_type="AuditLog", allowed=True),
        WriteRule(agent_id="ingestion_agent", entity_type="Note", allowed=True),
        # Ingestion agent also reads everything (so the Network tab shows
        # populated entities when the demoer toggles to the admin view).
        *(
            FieldReadRule(
                agent_id="ingestion_agent", entity_type="Customer", field_name=f, allowed=True
            )
            for f in _CUSTOMER_FIELDS_INGESTION
        ),
        # === Sales — reads every Customer field (Scene 1 baseline) =====
        EntityVisibilityRule(agent_id="sales", entity_type="Customer", allowed=True),
        EntityVisibilityRule(agent_id="sales", entity_type="Deal", allowed=True),
        *(
            FieldReadRule(agent_id="sales", entity_type="Customer", field_name=f, allowed=True)
            for f in _CUSTOMER_FIELDS_FOR_SALES
        ),
        # AuditLog is hidden from Sales — this is the "audit trail isn't a
        # place for Sales to read manager-level operational signals" rule.
        EntityVisibilityRule(agent_id="sales", entity_type="AuditLog", allowed=False),
        # === Customer Service — reads only contact-shaped fields ========
        EntityVisibilityRule(agent_id="customer_service", entity_type="Customer", allowed=True),
        EntityVisibilityRule(agent_id="customer_service", entity_type="AuditLog", allowed=True),
        *(
            FieldReadRule(
                agent_id="customer_service",
                entity_type="Customer",
                field_name=f,
                allowed=True,
            )
            for f in _CUSTOMER_FIELDS_FOR_CS
        ),
        # === Conflict resolution (Scene 1 baseline) =====================
        # Per `demo.md` cell 12: under the initial mechanical rule, latest-write wins.
        # Scene 4 swaps this for a SkillResolver via the live policy editor.
        ConflictRule(
            entity_type="Customer",
            field_name="deal_size",
            resolver=LatestWriteResolverSpec(),
        ),
        # Auto-resolver is the default for any other conflicting field — falls back
        # to LatestWriteResolver when no specific ConflictRule matches.
        ConflictRule(
            entity_type="Customer",
            field_name="contact",
            resolver=AutoResolverSpec(),
        ),
    )
    return RuleSet(rules=rules)


__all__ = ["initial_demo_ruleset"]
