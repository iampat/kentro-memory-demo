"""Tests for `parse_nl_to_ruleset` — the NL → RuleSet orchestrator.

Uses a fake LLMClient that returns canned `_NLIntentList` and `_ParsedRule`
results, so we can assert orchestration logic (per-intent validation,
partial-success notes, schema/agent allowlist) without hitting a real LLM.
"""

from dataclasses import dataclass, field

from kentro.types import (
    ConflictRule,
    EntityTypeDef,
    FieldDef,
    FieldReadRule,
    SkillResolverSpec,
    WriteRule,
)
from kentro_server.skills.llm_client import (
    LLMClient,
    _NLIntentItem,
    _NLIntentList,
    _ParsedRule,
)
from kentro_server.skills.nl_to_ruleset import parse_nl_to_ruleset


def _customer_schema() -> EntityTypeDef:
    return EntityTypeDef(
        name="Customer",
        fields=(
            FieldDef(name="name", type_str="str"),
            FieldDef(name="deal_size", type_str="float | None"),
        ),
    )


@dataclass
class _ScriptedLLM(LLMClient):
    """LLM that returns canned intents + per-intent parsed rules from queues."""

    intent_list: _NLIntentList = field(default_factory=lambda: _NLIntentList(intents=()))
    rule_queue: list[_ParsedRule] = field(default_factory=list)

    def run_skill_resolver(self, *, prompt, candidates, model=None):
        raise NotImplementedError("not exercised here")

    def extract_entities(
        self, *, document_text, registered_schemas, document_label=None, model=None
    ):
        raise NotImplementedError("not exercised here")

    def identify_nl_intents(self, *, text, model=None):
        return self.intent_list

    def parse_nl_rule(
        self,
        *,
        intent_description,
        intent_kind,
        registered_schemas,
        known_agent_ids,
        model=None,
    ):
        if not self.rule_queue:
            raise AssertionError(
                f"_ScriptedLLM ran out of canned rules; called with {intent_description!r}"
            )
        return self.rule_queue.pop(0)


def test_empty_message_returns_empty_ruleset() -> None:
    llm = _ScriptedLLM(intent_list=_NLIntentList(intents=()))
    out = parse_nl_to_ruleset(
        llm=llm,
        text="hi there",
        registered_schemas=[_customer_schema()],
        known_agent_ids=("sales",),
    )
    if out.parsed_ruleset.rules:
        raise AssertionError("empty intent list must yield empty ruleset")
    if out.intents:
        raise AssertionError("empty intent list must yield no NLIntents")


def test_single_valid_field_read_intent_compiles() -> None:
    llm = _ScriptedLLM(
        intent_list=_NLIntentList(
            intents=(_NLIntentItem(kind="field_read", description="redact deal_size from sales"),)
        ),
        rule_queue=[
            _ParsedRule(
                rule_json=FieldReadRule(
                    agent_id="sales",
                    entity_type="Customer",
                    field_name="deal_size",
                    allowed=False,
                ).model_dump_json(),
                reason="ok",
            )
        ],
    )
    out = parse_nl_to_ruleset(
        llm=llm,
        text="redact deal_size from sales",
        registered_schemas=[_customer_schema()],
        known_agent_ids=("sales", "cs"),
    )
    if len(out.parsed_ruleset.rules) != 1:
        raise AssertionError(f"expected 1 rule, got {len(out.parsed_ruleset.rules)}")
    if not isinstance(out.parsed_ruleset.rules[0], FieldReadRule):
        raise AssertionError("expected FieldReadRule")
    if out.notes is not None:
        raise AssertionError(f"clean parse must have no notes, got {out.notes!r}")


def test_partial_success_compiles_some_skips_others_with_notes() -> None:
    llm = _ScriptedLLM(
        intent_list=_NLIntentList(
            intents=(
                _NLIntentItem(kind="field_read", description="redact A"),
                _NLIntentItem(kind="field_read", description="redact B (LLM gives up)"),
            )
        ),
        rule_queue=[
            _ParsedRule(
                rule_json=FieldReadRule(
                    agent_id="sales",
                    entity_type="Customer",
                    field_name="deal_size",
                    allowed=False,
                ).model_dump_json(),
                reason="ok",
            ),
            _ParsedRule(rule_json=None, reason="ambiguous — clarify which field"),
        ],
    )
    out = parse_nl_to_ruleset(
        llm=llm,
        text="redact A; also redact B somehow",
        registered_schemas=[_customer_schema()],
        known_agent_ids=("sales",),
    )
    if len(out.parsed_ruleset.rules) != 1:
        raise AssertionError(f"expected 1 valid rule, got {len(out.parsed_ruleset.rules)}")
    if out.notes is None or "clarify" not in out.notes:
        raise AssertionError(f"expected skip-note, got {out.notes!r}")
    if len(out.intents) != 2:
        raise AssertionError("intents must include all identified, even skipped")


def test_rule_referencing_unknown_field_is_rejected() -> None:
    """Validation guards against the LLM inventing field names."""
    llm = _ScriptedLLM(
        intent_list=_NLIntentList(
            intents=(_NLIntentItem(kind="field_read", description="redact foo"),)
        ),
        rule_queue=[
            _ParsedRule(
                rule_json=FieldReadRule(
                    agent_id="sales",
                    entity_type="Customer",
                    field_name="not_a_real_field",
                    allowed=False,
                ).model_dump_json(),
                reason="ok",
            )
        ],
    )
    out = parse_nl_to_ruleset(
        llm=llm,
        text="redact foo",
        registered_schemas=[_customer_schema()],
        known_agent_ids=("sales",),
    )
    if out.parsed_ruleset.rules:
        raise AssertionError("rule with unknown field must be skipped")
    if out.notes is None or "unknown field" not in out.notes:
        raise AssertionError(f"expected unknown-field note, got {out.notes!r}")


def test_rule_referencing_unknown_agent_is_rejected() -> None:
    llm = _ScriptedLLM(
        intent_list=_NLIntentList(
            intents=(_NLIntentItem(kind="write_permission", description="block ghost"),)
        ),
        rule_queue=[
            _ParsedRule(
                rule_json=WriteRule(
                    agent_id="ghost",
                    entity_type="Customer",
                    field_name="deal_size",
                    allowed=False,
                ).model_dump_json(),
                reason="ok",
            )
        ],
    )
    out = parse_nl_to_ruleset(
        llm=llm,
        text="block ghost from writing deal_size",
        registered_schemas=[_customer_schema()],
        known_agent_ids=("sales",),
    )
    if out.parsed_ruleset.rules:
        raise AssertionError("rule with unknown agent must be skipped")
    if out.notes is None or "unknown agent_id" not in out.notes:
        raise AssertionError(f"expected unknown-agent note, got {out.notes!r}")


def test_conflict_resolver_intent_compiles() -> None:
    llm = _ScriptedLLM(
        intent_list=_NLIntentList(
            intents=(
                _NLIntentItem(
                    kind="conflict_resolver",
                    description="written outweighs verbal for deal_size",
                ),
            )
        ),
        rule_queue=[
            _ParsedRule(
                rule_json=ConflictRule(
                    entity_type="Customer",
                    field_name="deal_size",
                    resolver=SkillResolverSpec(prompt="written outweighs verbal"),
                ).model_dump_json(),
                reason="picked skill resolver per the policy in the intent",
            )
        ],
    )
    out = parse_nl_to_ruleset(
        llm=llm,
        text="for deal_size, written outweighs verbal",
        registered_schemas=[_customer_schema()],
        known_agent_ids=("sales",),
    )
    if len(out.parsed_ruleset.rules) != 1:
        raise AssertionError(f"expected 1 rule, got {len(out.parsed_ruleset.rules)}")
    rule = out.parsed_ruleset.rules[0]
    if not isinstance(rule, ConflictRule):
        raise AssertionError(f"expected ConflictRule, got {type(rule)}")
    if not isinstance(rule.resolver, SkillResolverSpec):
        raise AssertionError(f"expected SkillResolverSpec, got {type(rule.resolver)}")
