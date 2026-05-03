"""Tests for `kentro_server.core.resolve.resolve`.

Pure-function tests — `FieldWriteRow` instances are constructed directly without a
session. The LLMClient is the OfflineLLMClient stub by default; one test substitutes
a fake decision-emitting client to exercise the SkillResolver KNOWN path.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from kentro.types import (
    AutoResolverSpec,
    ConflictRule,
    FieldStatus,
    LatestWriteResolverSpec,
    PreferAgentResolverSpec,
    RawResolverSpec,
    RuleSet,
    SkillResolverSpec,
)
from kentro_server.core.resolve import resolve
from kentro_server.skills.llm_client import (
    LLMClient,
    OfflineLLMClient,
    SkillResolverDecision,
)
from kentro_server.store.models import FieldWriteRow

T0 = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=2)
T2 = T0 + timedelta(days=4)


def _w(value_json: str, agent_id: str = "ingestion_agent", at: datetime = T0) -> FieldWriteRow:
    return FieldWriteRow(
        id=uuid4(),
        entity_id=uuid4(),
        field_name="deal_size",
        value_json=value_json,
        written_by_agent_id=agent_id,
        written_at=at,
        rule_version_at_write=1,
    )


def _empty_ruleset() -> RuleSet:
    return RuleSet(rules=(), version=1)


def _ruleset_with_skill_conflict_rule() -> RuleSet:
    rule = ConflictRule(
        entity_type="Customer",
        field_name="deal_size",
        resolver=SkillResolverSpec(prompt="written outweighs verbal"),
    )
    return RuleSet(rules=(rule,), version=2)


# === Fast paths ===

def test_single_candidate_returns_known_with_that_winner() -> None:
    write = _w("250000", at=T0)
    out = resolve(
        candidates=[write],
        spec=AutoResolverSpec(),
        ruleset=_empty_ruleset(),
        entity_type="Customer",
        field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.KNOWN:
        raise AssertionError(f"expected KNOWN, got {out.status}")
    if out.winner is not write:
        raise AssertionError("single candidate must be the winner")


def test_corroboration_many_writes_one_distinct_value() -> None:
    a = _w('"Acme"', at=T0)
    b = _w('"Acme"', at=T1)
    out = resolve(
        candidates=[a, b],
        spec=AutoResolverSpec(),  # would dispatch but corroboration short-circuits
        ruleset=_empty_ruleset(),
        entity_type="Customer",
        field_name="name",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.KNOWN:
        raise AssertionError(f"corroboration should resolve to KNOWN, got {out.status}")
    if out.winner is not b:
        raise AssertionError("latest of corroborating writes should be the winner")
    if len(out.candidates) != 2:
        raise AssertionError("all corroborating writes must remain in candidates for lineage")


def test_resolve_raises_on_empty_candidates() -> None:
    with pytest.raises(ValueError, match="zero candidates"):
        resolve(
            candidates=[],
            spec=RawResolverSpec(),
            ruleset=_empty_ruleset(),
            entity_type="Customer", field_name="deal_size",
            llm=OfflineLLMClient(),
        )


# === Per-spec behavior on the demo conflict ($250K transcript vs $300K email) ===

def _demo_conflict_candidates() -> tuple[FieldWriteRow, FieldWriteRow]:
    transcript = _w("250000", agent_id="ingestion_agent", at=T0)
    email = _w("300000", agent_id="ingestion_agent", at=T1)
    return transcript, email


def test_raw_resolver_returns_unresolved_with_both_candidates() -> None:
    transcript, email = _demo_conflict_candidates()
    out = resolve(
        candidates=[transcript, email],
        spec=RawResolverSpec(),
        ruleset=_empty_ruleset(),
        entity_type="Customer", field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError(f"raw resolver must return UNRESOLVED, got {out.status}")
    if out.winner is not None:
        raise AssertionError("raw resolver must not pick a winner")
    returned_ids = {c.id for c in out.candidates}
    if returned_ids != {transcript.id, email.id}:
        raise AssertionError("raw resolver must return all candidates")
    if not out.reason or "raw resolver" not in out.reason:
        raise AssertionError(f"unexpected reason: {out.reason!r}")


def test_latest_write_picks_email() -> None:
    transcript, email = _demo_conflict_candidates()
    out = resolve(
        candidates=[transcript, email],
        spec=LatestWriteResolverSpec(),
        ruleset=_empty_ruleset(),
        entity_type="Customer", field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.KNOWN or out.winner is not email:
        raise AssertionError(f"LatestWrite should pick the email, got status={out.status} winner={out.winner}")


def test_prefer_agent_picks_matching_when_match_exists() -> None:
    transcript = _w("250000", agent_id="ingestion_agent", at=T0)
    email_correction = _w("300000", agent_id="manual_sales", at=T1)
    out = resolve(
        candidates=[transcript, email_correction],
        spec=PreferAgentResolverSpec(agent_id="manual_sales"),
        ruleset=_empty_ruleset(),
        entity_type="Customer", field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.KNOWN or out.winner is not email_correction:
        raise AssertionError(f"PreferAgent should pick the manual_sales row, got {out}")


def test_prefer_agent_no_match_returns_unresolved() -> None:
    transcript, email = _demo_conflict_candidates()
    out = resolve(
        candidates=[transcript, email],
        spec=PreferAgentResolverSpec(agent_id="auditor"),
        ruleset=_empty_ruleset(),
        entity_type="Customer", field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError("no-match PreferAgent must be UNRESOLVED")
    if out.reason is None or "auditor" not in out.reason:
        raise AssertionError(f"PreferAgent reason should name the missing agent, got {out.reason!r}")


# === AutoResolver dispatch ===

def test_auto_resolver_falls_back_to_latest_write_when_no_rule() -> None:
    transcript, email = _demo_conflict_candidates()
    out = resolve(
        candidates=[transcript, email],
        spec=AutoResolverSpec(),
        ruleset=_empty_ruleset(),
        entity_type="Customer", field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.KNOWN or out.winner is not email:
        raise AssertionError(f"AutoResolver default-to-LatestWrite expected, got {out}")
    if not isinstance(out.resolver_used, LatestWriteResolverSpec):
        raise AssertionError(f"AutoResolver must record LatestWriteResolverSpec, got {out.resolver_used}")


def test_auto_resolver_dispatches_to_skill_resolver_via_rule() -> None:
    transcript, email = _demo_conflict_candidates()
    out = resolve(
        candidates=[transcript, email],
        spec=AutoResolverSpec(),
        ruleset=_ruleset_with_skill_conflict_rule(),
        entity_type="Customer", field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    # Offline stub returns UNRESOLVED — verifies AutoResolver routed through to the skill.
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError(f"offline skill must return UNRESOLVED, got {out.status}")
    if not isinstance(out.resolver_used, SkillResolverSpec):
        raise AssertionError(f"resolver_used should record the SkillResolverSpec, got {out.resolver_used}")
    if out.reason is None or "offline" not in out.reason.lower():
        raise AssertionError(f"offline reason expected, got {out.reason!r}")


# === SkillResolver direct (with a fake online client) ===

@dataclass
class _FakeOnlineLLM(LLMClient):
    decision: SkillResolverDecision

    def run_skill_resolver(self, *, prompt, candidates, model):
        return self.decision


def test_skill_resolver_known_when_decision_picks_existing_value() -> None:
    transcript, email = _demo_conflict_candidates()
    fake = _FakeOnlineLLM(SkillResolverDecision(
        chosen_value_json="300000",
        reason="written outweighs verbal — email beats transcript",
    ))
    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="written outweighs verbal"),
        ruleset=_empty_ruleset(),
        entity_type="Customer", field_name="deal_size",
        llm=fake,
    )
    if out.status != FieldStatus.KNOWN:
        raise AssertionError(f"expected KNOWN from valid skill decision, got {out.status}")
    if out.winner is not email:
        raise AssertionError(f"skill picked $300K (email), got winner={out.winner}")
    if out.reason is None or "written" not in out.reason:
        raise AssertionError(f"skill reasoning should be in the result, got {out.reason!r}")


def test_skill_resolver_unresolved_when_decision_returns_none() -> None:
    transcript, email = _demo_conflict_candidates()
    fake = _FakeOnlineLLM(SkillResolverDecision(
        chosen_value_json=None,
        reason="cannot determine source type",
    ))
    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="written outweighs verbal"),
        ruleset=_empty_ruleset(),
        entity_type="Customer", field_name="deal_size",
        llm=fake,
    )
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError(f"expected UNRESOLVED, got {out.status}")
    if out.reason != "cannot determine source type":
        raise AssertionError(f"skill reason should pass through, got {out.reason!r}")


def test_skill_resolver_unresolved_when_decision_picks_unknown_value() -> None:
    """Defensive: if the LLM hallucinates a value not in candidates, we don't trust it."""
    transcript, email = _demo_conflict_candidates()
    fake = _FakeOnlineLLM(SkillResolverDecision(
        chosen_value_json="999999",  # not present
        reason="invented this number",
    ))
    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="..."),
        ruleset=_empty_ruleset(),
        entity_type="Customer", field_name="deal_size",
        llm=fake,
    )
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError("hallucinated value must result in UNRESOLVED")
    if out.reason is None or "not present" not in out.reason:
        raise AssertionError(f"reason should mention the unknown value, got {out.reason!r}")
