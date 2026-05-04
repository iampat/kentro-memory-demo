"""Rule routes — NL parse, apply (atomic version bump), get-active.

POST /rules/parse takes plain English; the multi-step LLM parse runs in
`skills.nl_to_ruleset.parse_nl_to_ruleset(...)`. Returns NLResponse with both
`parsed_ruleset` (compilable rules) and `notes` (skipped intents) so the
caller can decide whether to apply automatically or surface a clarification.
"""

import logging

from fastapi import APIRouter
from kentro.rules import render_rule, render_rule_as_rego
from kentro.types import NLResponse, RuleSet
from pydantic import BaseModel, ConfigDict

from kentro_server.api.auth import AdminPrincipalDep, PrincipalDep
from kentro_server.api.deps import LLMClientDep, SchemaRegistryDep, TenantRegistryDep
from kentro_server.api.dtos import (
    ApplyRulesetRequest,
    ApplyRulesetResponse,
    NLParseRequest,
)
from kentro_server.core.rules import apply_ruleset, load_active_ruleset
from kentro_server.skills.nl_to_ruleset import parse_nl_to_ruleset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rules", tags=["rules"])


@router.post("/parse", response_model=NLResponse)
def parse_rules(
    body: NLParseRequest,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
    llm: LLMClientDep,
    registry: TenantRegistryDep,
) -> NLResponse:
    """Parse plain-English rule changes into a typed RuleSet (does NOT apply)."""
    agent_ids = tuple(a.id for a in registry.agents_for(principal.tenant_id))
    return parse_nl_to_ruleset(
        llm=llm,
        text=body.text,
        registered_schemas=schema.list_all(),
        known_agent_ids=agent_ids,
    )


@router.post("/apply", response_model=ApplyRulesetResponse)
def apply_rules(body: ApplyRulesetRequest, principal: AdminPrincipalDep) -> ApplyRulesetResponse:
    """Atomically commit a RuleSet as a new version. ADMIN only.

    Without the admin gate, any agent could re-grant itself anything — see the
    auth model docstring on `Principal`.
    """
    new_version = apply_ruleset(
        principal.store,
        rules=body.ruleset.rules,
        summary=body.summary,
    )
    return ApplyRulesetResponse(
        version=new_version,
        rules_applied=len(body.ruleset.rules),
    )


@router.get("/active", response_model=RuleSet)
def get_active(principal: PrincipalDep) -> RuleSet:
    return load_active_ruleset(principal.store)


class RenderedRule(BaseModel):
    """One rule rendered both human-readable and as a Rego-flavored snippet."""

    model_config = ConfigDict(frozen=True)
    summary: str
    rego: str


class RenderedRulesetResponse(BaseModel):
    """Response shape for `GET /rules/active/rendered` — drives the policy editor.

    Pairs each `Rule` with `render_rule(...)` (one-line summary) and
    `render_rule_as_rego(...)` (multi-line Rego-style snippet for the expandable
    `<pre>` in the UI). Order matches the active ruleset 1:1.
    """

    model_config = ConfigDict(frozen=True)
    version: int
    rules: tuple[RenderedRule, ...] = ()


@router.get("/active/rendered", response_model=RenderedRulesetResponse)
def get_active_rendered(principal: PrincipalDep) -> RenderedRulesetResponse:
    """Live ruleset paired with human-readable + Rego-flavored renderings.

    The UI's policy editor uses `summary` for the row label and `rego` for the
    expandable code block. Server-side rendering keeps the JS layer free of
    rule-shape knowledge; if a new `Rule` variant lands, only `kentro.rules`
    needs updating and the UI picks it up automatically.
    """
    ruleset = load_active_ruleset(principal.store)
    rendered = tuple(
        RenderedRule(summary=render_rule(r), rego=render_rule_as_rego(r)) for r in ruleset.rules
    )
    return RenderedRulesetResponse(version=ruleset.version, rules=rendered)
