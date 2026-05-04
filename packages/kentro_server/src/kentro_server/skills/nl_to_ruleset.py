"""NL → RuleSet orchestrator: turn a plain-English message into a typed RuleSet.

Two-step LLM parse (per the design walkthrough — chosen over single-shot for
chat-iterability):

    1. `identify_nl_intents(text)` → splits the user's message into atomic
       intents. Each intent has a `kind` (one of the four rule dimensions)
       and a one-sentence `description`. The splitter may also return free-text
       `notes` describing fragments it could NOT classify; we surface those.

    2. For each intent: `parse_nl_rule(intent, schemas, agent_ids)` → returns
       JSON for one `Rule` variant, or `rule_json=None` with a skip-reason.

The orchestrator validates each compiled rule against the live schema and the
known-agents allowlist, then assembles `NLResponse`:

    - `parsed_ruleset.rules` — only the rules that compiled AND validated.
    - `intents` — every intent the LLM identified, even ones we skipped.
    - `notes` — `intent_list.notes` (step-1 skips) + per-intent skip-reasons
      (step-2 skips), joined as a human-readable summary.

Partial-success is the contract: a 4-intent message where 3 compile cleanly
and 1 is unclassifiable returns the 3 in `parsed_ruleset` and a note about
the 1 — the caller can choose to apply, ask the user to clarify, or both.

Rate-limit guard: `max_intents` caps the per-call LLM fan-out (1 splitter call
plus N compiler calls). Beyond the cap, extra intents are dropped and a note
is emitted; this turns "user pasted a 50-clause manifesto" from "51 LLM calls
silently" into "20 calls + an explicit note".
"""

import logging
from typing import Literal, cast

from kentro.types import (
    ConflictRule,
    EntityVisibilityRule,
    FieldReadRule,
    NLIntent,
    NLResponse,
    Rule,
    RuleSet,
    WriteRule,
)
from pydantic import TypeAdapter, ValidationError

from kentro_server.skills.llm_client import LLMClient

logger = logging.getLogger(__name__)


_VALID_INTENT_KINDS: frozenset[str] = frozenset(
    {"field_read", "entity_visibility", "write_permission", "conflict_resolver"}
)
_RULE_ADAPTER: TypeAdapter[Rule] = TypeAdapter(Rule)
_IntentKind = Literal["field_read", "entity_visibility", "write_permission", "conflict_resolver"]

DEFAULT_MAX_INTENTS = 20


def parse_nl_to_ruleset(
    *,
    llm: LLMClient,
    text: str,
    registered_schemas: list,  # list[EntityTypeDef]
    known_agent_ids: tuple[str, ...],
    fast_model: str | None = None,
    max_intents: int = DEFAULT_MAX_INTENTS,
) -> NLResponse:
    """Parse `text` into an NLResponse. Never raises on a per-intent failure.

    Top-level failures (the LLM call itself raising, e.g. offline) propagate.
    Per-intent failures (rule_json missing, validation against schema/agents
    failing) are collected into `notes` so the caller sees the whole picture.

    `max_intents` caps the per-intent LLM fan-out — see module docstring.
    """
    intent_list = llm.identify_nl_intents(text=text, model=fast_model)
    intents: list[NLIntent] = []
    valid_rules: list[Rule] = []
    skip_notes: list[str] = []

    # Step-1 splitter notes: any fragment the splitter could not classify.
    # Surface verbatim so the user sees what was dropped.
    if intent_list.notes:
        skip_notes.append(f"splitter notes: {intent_list.notes}")

    # Cap per-call fan-out. We process the first `max_intents` and report the rest.
    raw_intents = list(intent_list.intents)
    if len(raw_intents) > max_intents:
        skipped = len(raw_intents) - max_intents
        skip_notes.append(
            f"capped at {max_intents} intents — dropped {skipped} (raise max_intents to handle more)"
        )
        raw_intents = raw_intents[:max_intents]

    schema_by_name = {td.name: td for td in registered_schemas}

    for raw in raw_intents:
        # Coerce the LLM's free-form `kind` into our literal set; reject if it
        # doesn't match (the LLM is told the four allowed kinds in the skill).
        if raw.kind not in _VALID_INTENT_KINDS:
            skip_notes.append(f"intent {raw.description!r}: unknown kind {raw.kind!r} (skipped)")
            continue
        # Membership check above guarantees raw.kind is one of the four literals;
        # cast() is the lightest way to tell ty that without re-validating.
        intent = NLIntent(kind=cast(_IntentKind, raw.kind), description=raw.description)
        intents.append(intent)

        parsed = llm.parse_nl_rule(
            intent_description=raw.description,
            intent_kind=raw.kind,
            registered_schemas=registered_schemas,
            known_agent_ids=known_agent_ids,
            model=fast_model,
        )
        if parsed.rule_json is None:
            skip_notes.append(f"intent {raw.description!r}: {parsed.reason}")
            continue

        try:
            rule = _RULE_ADAPTER.validate_json(parsed.rule_json)
        except ValidationError as exc:
            logger.info("parse_nl_to_ruleset: rule JSON failed schema validation: %s", exc)
            # Surface the actual validation problem so callers can see WHY
            # it failed (e.g. "field required", "wrong discriminator value")
            # instead of a count. Each error is "<location>: <msg>"; the
            # location is a tuple path through the JSON.
            err_summaries = []
            for e in exc.errors():
                loc = ".".join(str(p) for p in e.get("loc", ()))
                msg = e.get("msg", "")
                err_summaries.append(f"{loc}: {msg}" if loc else msg)
            details = "; ".join(err_summaries)
            skip_notes.append(
                f"intent {raw.description!r}: LLM-produced rule did not match the "
                f"Rule schema — {details}. Raw JSON: {parsed.rule_json}"
            )
            continue

        validation_error = _validate_rule_against_world(rule, schema_by_name, known_agent_ids)
        if validation_error is not None:
            skip_notes.append(f"intent {raw.description!r}: {validation_error}")
            continue

        valid_rules.append(rule)

    notes = "\n".join(skip_notes) if skip_notes else None
    summary = f"parsed {len(valid_rules)} rule(s) from {len(intent_list.intents)} intent(s)" + (
        f"; skipped {len(skip_notes)}" if skip_notes else ""
    )
    return NLResponse(
        parsed_ruleset=RuleSet(rules=tuple(valid_rules), version=0),
        intents=tuple(intents),
        notes=notes,
        summary=summary,
    )


def _validate_rule_against_world(
    rule: Rule,
    schema_by_name: dict,
    known_agent_ids: tuple[str, ...],
) -> str | None:
    """Reject rules that name an unknown entity_type, field, or agent.

    Returns a human-readable error string when the rule is invalid; `None`
    when it's clean. The LLM is *told* to use only known names, but it
    occasionally invents — this guard makes that observable.

    Uses `match`/`case` per CLAUDE.md "Modern Python Idioms" so each branch
    sees the narrowed type rather than relying on `getattr(...)` after an
    `isinstance` check.
    """
    known_agents = set(known_agent_ids)

    match rule:
        case FieldReadRule(agent_id=agent_id, entity_type=entity_type, field_name=field_name):
            if entity_type not in schema_by_name:
                return f"unknown entity_type {entity_type!r}"
            field_names = {f.name for f in schema_by_name[entity_type].fields}
            if field_name not in field_names:
                return (
                    f"unknown field {entity_type}.{field_name!r} (declared: {sorted(field_names)})"
                )
            if agent_id not in known_agents:
                return f"unknown agent_id {agent_id!r} (known: {sorted(known_agents)})"

        case WriteRule(agent_id=agent_id, entity_type=entity_type, field_name=write_field_name):
            if entity_type not in schema_by_name:
                return f"unknown entity_type {entity_type!r}"
            if write_field_name is not None:
                field_names = {f.name for f in schema_by_name[entity_type].fields}
                if write_field_name not in field_names:
                    return (
                        f"unknown field {entity_type}.{write_field_name!r} "
                        f"(declared: {sorted(field_names)})"
                    )
            if agent_id not in known_agents:
                return f"unknown agent_id {agent_id!r} (known: {sorted(known_agents)})"

        case EntityVisibilityRule(agent_id=agent_id, entity_type=entity_type):
            if entity_type not in schema_by_name:
                return f"unknown entity_type {entity_type!r}"
            if agent_id not in known_agents:
                return f"unknown agent_id {agent_id!r} (known: {sorted(known_agents)})"

        case ConflictRule(entity_type=entity_type, field_name=conflict_field_name):
            if entity_type not in schema_by_name:
                return f"unknown entity_type {entity_type!r}"
            field_names = {f.name for f in schema_by_name[entity_type].fields}
            if conflict_field_name not in field_names:
                return (
                    f"unknown field {entity_type}.{conflict_field_name!r} "
                    f"(declared: {sorted(field_names)})"
                )

    return None


__all__ = ["DEFAULT_MAX_INTENTS", "parse_nl_to_ruleset"]
