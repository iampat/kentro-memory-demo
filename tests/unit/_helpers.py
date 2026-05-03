"""Shared test helpers (constants + the multi-purpose `FakeLLM`).

Lives alongside `conftest.py` rather than inside it because conftest is auto-
loaded by pytest as a fixture module — its contents aren't normally imported
as a regular Python module. Helpers that tests need to *import* by name go
here.

Tests that need fixtures (which only need to be referenced by name in argument
lists, not imported) get them from `conftest.py`.
"""

from dataclasses import dataclass, field

from kentro_server.skills.llm_client import (
    ExtractionResult,
    LLMClient,
    NLIntentList,
    ParsedRule,
    SkillResolverDecision,
)

# Two known-good keys for the standard test tenants.json (see conftest).
# `tests/unit/conftest.py::tenants_json_with_admin` writes a tenants.json with:
#   - ingestion_agent (admin) → ADMIN_KEY
#   - sales (non-admin) → AGENT_KEY
ADMIN_KEY = "admin-test-key"
AGENT_KEY = "agent-test-key"


@dataclass
class FakeLLM(LLMClient):
    """Multi-purpose `LLMClient` test double.

    Tests script its responses via attribute assignment after construction:

        fake = FakeLLM()
        fake.extraction_result = ExtractionResult(entities=(...,))
        fake.nl_intents = NLIntentList(intents=(...,))
        fake.nl_rules = [ParsedRule(rule_json=..., reason=...)]

    Counters track inner-call counts for assertions about caching, retries, etc.
    """

    extraction_result: ExtractionResult | None = None
    nl_intents: NLIntentList = field(default_factory=lambda: NLIntentList(intents=()))
    nl_rules: list[ParsedRule] = field(default_factory=list)
    skill_decision: SkillResolverDecision = field(
        default_factory=lambda: SkillResolverDecision(
            chosen_value_json=None,
            reason="not under test",
        )
    )
    extract_calls: int = 0
    skill_calls: int = 0
    intents_calls: int = 0
    rule_calls: int = 0

    def run_skill_resolver(self, *, prompt, candidates, model=None):
        self.skill_calls += 1
        return self.skill_decision

    def extract_entities(
        self, *, document_text, registered_schemas, document_label=None, model=None
    ):
        self.extract_calls += 1
        return self.extraction_result or ExtractionResult(entities=())

    def identify_nl_intents(self, *, text, model=None):
        self.intents_calls += 1
        return self.nl_intents

    def parse_nl_rule(
        self,
        *,
        intent_description,
        intent_kind,
        registered_schemas,
        known_agent_ids,
        model=None,
    ):
        self.rule_calls += 1
        if not self.nl_rules:
            return ParsedRule(rule_json=None, reason="fake LLM out of scripted rules")
        return self.nl_rules.pop(0)


__all__ = ["ADMIN_KEY", "AGENT_KEY", "FakeLLM"]
