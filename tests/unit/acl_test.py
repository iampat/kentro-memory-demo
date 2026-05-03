"""ACL evaluator tests — driven by the demo.md access matrix.

Initial demo state (`demo.md` § "The scenario"):

    | Agent             | Customer                                     | Deal | AuditLog  |
    |-------------------|----------------------------------------------|------|-----------|
    | Sales             | read all + write deal_size + sales_notes    | r/w  | invisible |
    | Customer Service  | read name + contact + support_tickets,       |      |           |
    |                   | write support_tickets only                   | n/a  | invisible |

Post-Scene-4 state (the four-rule change):
- Field read:       redact deal_size from CS contexts.
- Entity visibility: Sales gains read access to AuditLog for Acme.
- Write permission:  CS write to support_tickets requires manager approval (= denied in v0).
- Conflict:          deal_size resolver swapped (covered in Step 5 tests, not here).
"""

from kentro.acl import (
    AclDecision,
    evaluate_entity_visibility,
    evaluate_field_read,
    evaluate_write,
)
from kentro.types import (
    EntityVisibilityRule,
    FieldReadRule,
    RuleSet,
    WriteRule,
)

SALES = "sales"
CS = "customer_service"


def _initial_ruleset() -> RuleSet:
    rules = [
        # Sales — Customer reads (name, contact, deal_size, sales_notes, support_tickets)
        FieldReadRule(agent_id=SALES, entity_type="Customer", field_name="name", allowed=True),
        FieldReadRule(agent_id=SALES, entity_type="Customer", field_name="contact", allowed=True),
        FieldReadRule(
            agent_id=SALES, entity_type="Customer", field_name="deal_size", allowed=True
        ),
        FieldReadRule(
            agent_id=SALES, entity_type="Customer", field_name="sales_notes", allowed=True
        ),
        FieldReadRule(
            agent_id=SALES, entity_type="Customer", field_name="support_tickets", allowed=True
        ),
        # Sales — writes
        WriteRule(agent_id=SALES, entity_type="Customer", field_name="deal_size", allowed=True),
        WriteRule(agent_id=SALES, entity_type="Customer", field_name="sales_notes", allowed=True),
        WriteRule(agent_id=SALES, entity_type="Deal", field_name=None, allowed=True),
        # Sales — visibility
        EntityVisibilityRule(agent_id=SALES, entity_type="Customer", allowed=True),
        EntityVisibilityRule(agent_id=SALES, entity_type="Deal", allowed=True),
        # CS — Customer reads
        FieldReadRule(agent_id=CS, entity_type="Customer", field_name="name", allowed=True),
        FieldReadRule(agent_id=CS, entity_type="Customer", field_name="contact", allowed=True),
        FieldReadRule(
            agent_id=CS, entity_type="Customer", field_name="support_tickets", allowed=True
        ),
        # CS — writes
        WriteRule(agent_id=CS, entity_type="Customer", field_name="support_tickets", allowed=True),
        # CS — visibility
        EntityVisibilityRule(agent_id=CS, entity_type="Customer", allowed=True),
    ]
    return RuleSet(rules=tuple(rules), version=1)


def _post_scene4_ruleset() -> RuleSet:
    base = list(_initial_ruleset().rules)
    # 1. Redact deal_size from CS contexts.
    base.append(
        FieldReadRule(
            agent_id=CS,
            entity_type="Customer",
            field_name="deal_size",
            allowed=False,
        )
    )
    # 2. Sales gains read access to AuditLog for Acme.
    base.append(
        EntityVisibilityRule(
            agent_id=SALES,
            entity_type="AuditLog",
            entity_key="Acme",
            allowed=True,
        )
    )
    # 3. CS write to support_tickets now requires manager approval.
    base.append(
        WriteRule(
            agent_id=CS,
            entity_type="Customer",
            field_name="support_tickets",
            allowed=True,
            requires_approval=True,
        )
    )
    return RuleSet(rules=tuple(base), version=2)


# === Initial state ===


def test_sales_can_read_customer_deal_size() -> None:
    d = evaluate_field_read(
        entity_type="Customer",
        field_name="deal_size",
        agent_id=SALES,
        ruleset=_initial_ruleset(),
    )
    if not d.allowed:
        raise AssertionError(f"expected allowed, got: {d}")


def test_cs_cannot_read_customer_deal_size() -> None:
    d = evaluate_field_read(
        entity_type="Customer",
        field_name="deal_size",
        agent_id=CS,
        ruleset=_initial_ruleset(),
    )
    if d.allowed:
        raise AssertionError(f"expected denied, got: {d}")
    if d.reason != "no rule grants access":
        raise AssertionError(f"expected default-deny reason, got: {d.reason!r}")


def test_cs_can_read_support_tickets() -> None:
    d = evaluate_field_read(
        entity_type="Customer",
        field_name="support_tickets",
        agent_id=CS,
        ruleset=_initial_ruleset(),
    )
    if not d.allowed:
        raise AssertionError(f"expected allowed, got: {d}")


def test_auditlog_invisible_to_sales_initially() -> None:
    d = evaluate_entity_visibility(
        entity_type="AuditLog",
        entity_key="Acme",
        agent_id=SALES,
        ruleset=_initial_ruleset(),
    )
    if d.allowed:
        raise AssertionError("AuditLog must be invisible to Sales initially")


def test_auditlog_invisible_to_cs() -> None:
    d = evaluate_entity_visibility(
        entity_type="AuditLog",
        entity_key="Acme",
        agent_id=CS,
        ruleset=_initial_ruleset(),
    )
    if d.allowed:
        raise AssertionError("AuditLog must be invisible to CS")


def test_cs_blocked_writing_deal_size() -> None:
    d = evaluate_write(
        entity_type="Customer",
        field_name="deal_size",
        agent_id=CS,
        ruleset=_initial_ruleset(),
    )
    if d.allowed:
        raise AssertionError(f"CS must not write deal_size, got: {d}")


def test_sales_can_write_deal_size() -> None:
    d = evaluate_write(
        entity_type="Customer",
        field_name="deal_size",
        agent_id=SALES,
        ruleset=_initial_ruleset(),
    )
    if not d.allowed:
        raise AssertionError(f"Sales must be allowed to write deal_size, got: {d}")


def test_sales_can_write_any_deal_field_via_wildcard() -> None:
    d = evaluate_write(
        entity_type="Deal",
        field_name="value",
        agent_id=SALES,
        ruleset=_initial_ruleset(),
    )
    if not d.allowed:
        raise AssertionError(f"Sales should match the Deal wildcard write rule, got: {d}")


def test_unknown_agent_denied_by_default() -> None:
    d = evaluate_field_read(
        entity_type="Customer",
        field_name="name",
        agent_id="random_other",
        ruleset=_initial_ruleset(),
    )
    if d.allowed:
        raise AssertionError("default-deny violated for unknown agent")


# === Post-Scene-4 state ===


def test_after_scene4_cs_cannot_read_deal_size_explicit_deny() -> None:
    d = evaluate_field_read(
        entity_type="Customer",
        field_name="deal_size",
        agent_id=CS,
        ruleset=_post_scene4_ruleset(),
    )
    if d.allowed:
        raise AssertionError("explicit deny rule must block CS read of deal_size")
    if d.reason is None or "explicit deny" not in d.reason:
        raise AssertionError(f"expected explicit-deny reason, got: {d.reason!r}")


def test_after_scene4_sales_can_read_acme_auditlog() -> None:
    d = evaluate_entity_visibility(
        entity_type="AuditLog",
        entity_key="Acme",
        agent_id=SALES,
        ruleset=_post_scene4_ruleset(),
    )
    if not d.allowed:
        raise AssertionError(f"Sales must gain Acme AuditLog access after Scene 4, got: {d}")


def test_after_scene4_sales_still_blocked_from_other_audit_keys() -> None:
    d = evaluate_entity_visibility(
        entity_type="AuditLog",
        entity_key="OtherCorp",
        agent_id=SALES,
        ruleset=_post_scene4_ruleset(),
    )
    if d.allowed:
        raise AssertionError("Scene 4 only granted AuditLog/Acme; OtherCorp must stay invisible")


def test_after_scene4_cs_write_support_tickets_requires_approval() -> None:
    d = evaluate_write(
        entity_type="Customer",
        field_name="support_tickets",
        agent_id=CS,
        ruleset=_post_scene4_ruleset(),
    )
    expected = AclDecision(
        allowed=False,
        reason="write blocked: manager approval required",
    )
    if d != expected:
        raise AssertionError(f"expected {expected}, got {d}")
