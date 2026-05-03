"""Tests for `kentro.rules.ruleset_diff` and `kentro.rules.render_rule`.

Pure-function tests; no server, no Pydantic models beyond what the SDK ships.
"""

from kentro.rules import RuleSetDiff, render_rule, ruleset_diff
from kentro.types import (
    ConflictRule,
    EntityVisibilityRule,
    FieldReadRule,
    LatestWriteResolverSpec,
    PreferAgentResolverSpec,
    RawResolverSpec,
    RuleSet,
    SkillResolverSpec,
    WriteRule,
)

# === ruleset_diff =================================================================


def test_diff_empty_vs_empty() -> None:
    d = ruleset_diff(RuleSet(rules=()), RuleSet(rules=()))
    if d != RuleSetDiff(added=(), removed=(), unchanged=()):
        raise AssertionError(f"expected fully-empty diff, got {d!r}")


def test_diff_one_added() -> None:
    rule = FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True)
    d = ruleset_diff(RuleSet(rules=()), RuleSet(rules=(rule,)))
    if d.added != (rule,) or d.removed != () or d.unchanged != ():
        raise AssertionError(f"expected one added rule, got {d!r}")


def test_diff_one_removed() -> None:
    rule = FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True)
    d = ruleset_diff(RuleSet(rules=(rule,)), RuleSet(rules=()))
    if d.removed != (rule,) or d.added != () or d.unchanged != ():
        raise AssertionError(f"expected one removed rule, got {d!r}")


def test_diff_unchanged_regardless_of_order() -> None:
    """Order within RuleSet.rules must not affect diff classification."""
    a = FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True)
    b = FieldReadRule(
        agent_id="sales", entity_type="Customer", field_name="deal_size", allowed=False
    )
    old = RuleSet(rules=(a, b))
    new = RuleSet(rules=(b, a))  # same rules, swapped order
    d = ruleset_diff(old, new)
    if d.added or d.removed:
        raise AssertionError(f"swapped-order rules must be all unchanged, got {d!r}")
    if len(d.unchanged) != 2:
        raise AssertionError(f"expected 2 unchanged, got {len(d.unchanged)}")


def test_diff_mixed() -> None:
    keep = FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True)
    drop = FieldReadRule(
        agent_id="sales", entity_type="Customer", field_name="contact", allowed=True
    )
    add = FieldReadRule(
        agent_id="sales", entity_type="Customer", field_name="deal_size", allowed=False
    )
    d = ruleset_diff(RuleSet(rules=(keep, drop)), RuleSet(rules=(keep, add)))
    if d.added != (add,):
        raise AssertionError(f"expected only `add` added, got {d.added!r}")
    if d.removed != (drop,):
        raise AssertionError(f"expected only `drop` removed, got {d.removed!r}")
    if d.unchanged != (keep,):
        raise AssertionError(f"expected only `keep` unchanged, got {d.unchanged!r}")


# === render_rule ==================================================================


def test_render_field_read_allow() -> None:
    out = render_rule(
        FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True)
    )
    if out != "[allow] sales reads  Customer.name":
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_field_read_deny() -> None:
    out = render_rule(
        FieldReadRule(agent_id="cs", entity_type="Customer", field_name="deal_size", allowed=False)
    )
    if out != "[deny]  cs reads  Customer.deal_size":
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_write_with_specific_field() -> None:
    out = render_rule(
        WriteRule(agent_id="sales", entity_type="Customer", field_name="contact", allowed=True)
    )
    if out != "[allow] sales writes Customer.contact":
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_write_wildcard_field() -> None:
    out = render_rule(WriteRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True))
    if out != "[allow] ingestion_agent writes Customer.*":
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_write_requires_approval() -> None:
    out = render_rule(
        WriteRule(
            agent_id="sales",
            entity_type="Customer",
            field_name="deal_size",
            allowed=True,
            requires_approval=True,
        )
    )
    if out != "[allow] sales writes Customer.deal_size (requires_approval)":
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_visibility_allow_wildcard() -> None:
    out = render_rule(EntityVisibilityRule(agent_id="sales", entity_type="Customer", allowed=True))
    if out != "[allow]  sales sees   Customer.*":
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_visibility_hidden_specific_key() -> None:
    out = render_rule(
        EntityVisibilityRule(
            agent_id="sales", entity_type="Customer", entity_key="Acme", allowed=False
        )
    )
    if out != "[hidden] sales sees   Customer/Acme":
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_conflict_skill() -> None:
    out = render_rule(
        ConflictRule(
            entity_type="Customer",
            field_name="deal_size",
            resolver=SkillResolverSpec(prompt="written outweighs verbal"),
        )
    )
    if "Customer.deal_size" not in out or "written outweighs verbal" not in out:
        raise AssertionError(f"unexpected render: {out!r}")
    if not out.startswith("[skill]"):
        raise AssertionError(f"expected [skill] prefix, got {out!r}")


def test_render_conflict_prefer_agent() -> None:
    out = render_rule(
        ConflictRule(
            entity_type="Customer",
            field_name="deal_size",
            resolver=PreferAgentResolverSpec(agent_id="ingestion_agent"),
        )
    )
    if "ingestion_agent" not in out or not out.startswith("[prefer]"):
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_conflict_latest_write() -> None:
    out = render_rule(
        ConflictRule(
            entity_type="Customer",
            field_name="deal_size",
            resolver=LatestWriteResolverSpec(),
        )
    )
    if not out.startswith("[latest]"):
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_conflict_raw() -> None:
    out = render_rule(
        ConflictRule(
            entity_type="Customer",
            field_name="deal_size",
            resolver=RawResolverSpec(),
        )
    )
    if not out.startswith("[raw]"):
        raise AssertionError(f"unexpected render: {out!r}")


def test_render_all_rules_in_a_ruleset() -> None:
    """Roundtrip: every rule in a representative RuleSet must render without exception."""
    rules = (
        FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True),
        WriteRule(agent_id="sales", entity_type="Customer", allowed=True),
        EntityVisibilityRule(agent_id="cs", entity_type="Customer", allowed=False),
        ConflictRule(
            entity_type="Customer",
            field_name="deal_size",
            resolver=SkillResolverSpec(prompt="x"),
        ),
    )
    rendered = [render_rule(r) for r in rules]
    if len(rendered) != len(rules):
        raise AssertionError("render lost rules")
    if any(not s.strip() for s in rendered):
        raise AssertionError(f"empty render in {rendered!r}")
