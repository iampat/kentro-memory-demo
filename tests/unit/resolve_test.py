"""Tests for `kentro_server.core.resolve.resolve`.

Pure-function tests — `FieldWriteRow` instances are constructed directly without a
session. The LLMClient is the OfflineLLMClient stub by default; one test substitutes
a fake decision-emitting client to exercise the SkillResolver KNOWN path.

PR 35: resolver lookup now reads `ResolverPolicySet` (sibling to `RuleSet`),
not `ConflictRule`s embedded in the ACL ruleset.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from kentro.types import (
    AutoResolverSpec,
    FieldStatus,
    LatestWriteResolverSpec,
    RawResolverSpec,
    ResolverPolicy,
    ResolverPolicySet,
    SkillResolverSpec,
)
from kentro_server.core.resolve import resolve
from kentro_server.skills.llm_client import (
    LLMClient,
    OfflineLLMClient,
    SkillResolverDecision,
)
from kentro_server.store.models import FieldWriteRow

T0 = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
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


def _empty_policies() -> ResolverPolicySet:
    return ResolverPolicySet(policies=(), version=1)


def _policies_with_skill_for_deal_size() -> ResolverPolicySet:
    return ResolverPolicySet(
        policies=(
            ResolverPolicy(
                entity_type="Customer",
                field_name="deal_size",
                resolver=SkillResolverSpec(prompt="written outweighs verbal"),
            ),
        ),
        version=2,
    )


# === Fast paths ===


def test_single_candidate_returns_known_with_that_winner() -> None:
    write = _w("250000", at=T0)
    out = resolve(
        candidates=[write],
        spec=AutoResolverSpec(),
        resolver_policies=_empty_policies(),
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
        spec=AutoResolverSpec(),
        resolver_policies=_empty_policies(),
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
            resolver_policies=_empty_policies(),
            entity_type="Customer",
            field_name="deal_size",
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
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
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
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.KNOWN or out.winner is not email:
        raise AssertionError(
            f"LatestWrite should pick the email, got status={out.status} winner={out.winner}"
        )


# === AutoResolver dispatch ===


def test_auto_resolver_falls_back_to_latest_write_when_no_policy() -> None:
    transcript, email = _demo_conflict_candidates()
    out = resolve(
        candidates=[transcript, email],
        spec=AutoResolverSpec(),
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.KNOWN or out.winner is not email:
        raise AssertionError(f"AutoResolver default-to-LatestWrite expected, got {out}")
    if not isinstance(out.resolver_used, LatestWriteResolverSpec):
        raise AssertionError(
            f"AutoResolver must record LatestWriteResolverSpec, got {out.resolver_used}"
        )


def test_auto_resolver_handles_policy_wrapping_auto_spec() -> None:
    """Defensive: a ResolverPolicy(resolver=AutoResolverSpec()) would otherwise
    dispatch to itself and fall through to TypeError. The dispatcher must treat
    that case as 'no specific policy' and use the LatestWrite fallback."""
    transcript, email = _demo_conflict_candidates()
    bogus = ResolverPolicy(
        entity_type="Customer",
        field_name="deal_size",
        resolver=AutoResolverSpec(),
    )
    out = resolve(
        candidates=[transcript, email],
        spec=AutoResolverSpec(),
        resolver_policies=ResolverPolicySet(policies=(bogus,), version=1),
        entity_type="Customer",
        field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    if out.status != FieldStatus.KNOWN or out.winner is not email:
        raise AssertionError(
            f"AutoResolver-in-ResolverPolicy must fall back to LatestWrite, got {out}"
        )
    if not isinstance(out.resolver_used, LatestWriteResolverSpec):
        raise AssertionError(f"resolver_used should record the fallback, got {out.resolver_used}")


def test_auto_resolver_dispatches_to_skill_resolver_via_policy() -> None:
    transcript, email = _demo_conflict_candidates()
    out = resolve(
        candidates=[transcript, email],
        spec=AutoResolverSpec(),
        resolver_policies=_policies_with_skill_for_deal_size(),
        entity_type="Customer",
        field_name="deal_size",
        llm=OfflineLLMClient(),
    )
    # Offline stub returns UNRESOLVED — verifies AutoResolver routed through to the skill.
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError(f"offline skill must return UNRESOLVED, got {out.status}")
    if not isinstance(out.resolver_used, SkillResolverSpec):
        raise AssertionError(
            f"resolver_used should record the SkillResolverSpec, got {out.resolver_used}"
        )
    if out.reason is None or "offline" not in out.reason.lower():
        raise AssertionError(f"offline reason expected, got {out.reason!r}")


# === SkillResolver direct (with a fake online client) ===


@dataclass
class _FakeOnlineLLM(LLMClient):
    decision: SkillResolverDecision

    def run_skill_resolver(
        self, *, prompt, candidates, model=None, mode="pick", source_metadata=None
    ):
        return self.decision

    def extract_entities(
        self, *, document_text, registered_schemas, document_label=None, model=None
    ):
        raise NotImplementedError("not exercised in resolve_test")

    def identify_nl_intents(self, *, text, model=None):
        raise NotImplementedError("not exercised in resolve_test")

    def parse_nl_rule(
        self,
        *,
        intent_description,
        intent_kind,
        registered_schemas,
        known_agent_ids,
        model=None,
    ):
        raise NotImplementedError("not exercised in resolve_test")


def test_skill_resolver_known_when_decision_picks_existing_value() -> None:
    transcript, email = _demo_conflict_candidates()
    fake = _FakeOnlineLLM(
        SkillResolverDecision(
            chosen_value_json="300000",
            reason="written outweighs verbal — email beats transcript",
        )
    )
    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="written outweighs verbal"),
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
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
    fake = _FakeOnlineLLM(
        SkillResolverDecision(
            chosen_value_json=None,
            reason="cannot determine source type",
        )
    )
    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="written outweighs verbal"),
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
        llm=fake,
    )
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError(f"expected UNRESOLVED, got {out.status}")
    if out.reason != "cannot determine source type":
        raise AssertionError(f"skill reason should pass through, got {out.reason!r}")


def test_skill_resolver_passes_decision_actions_through_resolved_field_value() -> None:
    """A SkillResolver decision can carry workflow `actions`. The resolver
    must propagate them onto `ResolvedFieldValue.actions` so the read-path
    orchestrator can dispatch them through the ACL gate."""
    from kentro_server.skills.llm_client import (  # noqa: PLC0415 — test-local
        NotifyAction,
        WriteEntityAction,
    )

    transcript, email = _demo_conflict_candidates()
    actions = (
        WriteEntityAction(
            entity_type="Customer",
            entity_key="Acme",
            field_name="sales_notes",
            value_json='"escalated by skill"',
        ),
        NotifyAction(channel="#deals-review", message="$300K wins; review needed"),
    )

    @dataclass
    class _ScriptedLLM(LLMClient):
        def run_skill_resolver(
            self, *, prompt, candidates, model=None, mode="pick", source_metadata=None
        ):
            return SkillResolverDecision(
                chosen_value_json="300000",
                reason="written outweighs verbal",
                actions=actions,
            )

        def extract_entities(
            self, *, document_text, registered_schemas, document_label=None, model=None
        ):
            raise AssertionError("not under test")

        def identify_nl_intents(self, *, text, model=None):
            raise AssertionError("not under test")

        def parse_nl_rule(
            self,
            *,
            intent_description,
            intent_kind,
            registered_schemas,
            known_agent_ids,
            model=None,
        ):
            raise AssertionError("not under test")

    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="written outweighs verbal"),
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
        llm=_ScriptedLLM(),
    )
    if out.status != FieldStatus.KNOWN or out.winner is not email:
        raise AssertionError(f"expected KNOWN/email, got {out}")
    if out.actions != actions:
        raise AssertionError(f"actions must propagate through resolve(), got {out.actions}")


def test_skill_resolver_unresolved_when_decision_picks_unknown_value() -> None:
    """Defensive: if the LLM hallucinates a value not in candidates, we don't trust it."""
    transcript, email = _demo_conflict_candidates()
    fake = _FakeOnlineLLM(
        SkillResolverDecision(
            chosen_value_json="999999",  # not present
            reason="invented this number",
        )
    )
    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="..."),
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
        llm=fake,
    )
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError("hallucinated value must result in UNRESOLVED")
    if out.reason is None or "not present" not in out.reason:
        raise AssertionError(f"reason should mention the unknown value, got {out.reason!r}")


# === SkillResolver synthesize mode ============================================


def test_skill_resolver_synthesize_accepts_value_not_in_candidates() -> None:
    """In synthesize mode the LLM may produce a fresh value (e.g. a summary)
    that does NOT match any candidate verbatim. The resolver should land it as
    KNOWN with `synthesized_value_json` set and `winner=None`, so the read
    path can attribute lineage to ALL candidates."""
    transcript, email = _demo_conflict_candidates()
    synthesised = '"deal sized between $250K and $300K — sources disagree"'
    fake = _FakeOnlineLLM(
        SkillResolverDecision(
            chosen_value_json=synthesised,
            reason="combined two candidate values into a summary",
        )
    )
    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="Summarise them all", synthesize=True),
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
        llm=fake,
    )
    if out.status != FieldStatus.KNOWN:
        raise AssertionError(f"expected KNOWN from synthesize-mode decision, got {out.status}")
    if out.winner is not None:
        raise AssertionError("synthesize-mode resolution must not name a winner row")
    if out.synthesized_value_json != synthesised:
        raise AssertionError(
            f"synthesized_value_json should carry the LLM's value, got {out.synthesized_value_json!r}"
        )
    if len(out.candidates) != 2:
        raise AssertionError(
            "synthesize-mode resolution must surface every contributing candidate"
        )


def test_skill_resolver_synthesize_unresolved_on_refusal() -> None:
    """Synthesize-mode refusal still maps to UNRESOLVED with the candidates
    surfaced — never silent concatenation. (User-reported failure mode that
    motivated this PR.)"""
    transcript, email = _demo_conflict_candidates()
    fake = _FakeOnlineLLM(
        SkillResolverDecision(
            chosen_value_json=None,
            reason="policy is ambiguous — cannot pick a unique strategy",
        )
    )
    out = resolve(
        candidates=[transcript, email],
        spec=SkillResolverSpec(prompt="Summarise them all", synthesize=True),
        resolver_policies=_empty_policies(),
        entity_type="Customer",
        field_name="deal_size",
        llm=fake,
    )
    if out.status != FieldStatus.UNRESOLVED:
        raise AssertionError(f"synthesize-mode refusal must be UNRESOLVED, got {out.status}")
    if out.synthesized_value_json is not None:
        raise AssertionError("UNRESOLVED state must not carry a synthesized value")
    if out.reason is None or "ambiguous" not in out.reason:
        raise AssertionError(f"reason should be passed through, got {out.reason!r}")
