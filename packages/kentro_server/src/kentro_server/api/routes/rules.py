"""Rule routes — NL parse, apply (atomic version bump), get-active.

POST /rules/parse takes plain English; the multi-step LLM parse runs in
`skills.nl_to_ruleset.parse_nl_to_ruleset(...)`. Returns NLResponse with both
`parsed_ruleset` (compilable rules) and `notes` (skipped intents) so the
caller can decide whether to apply automatically or surface a clarification.
"""

import logging

from fastapi import APIRouter
from kentro.types import NLResponse, RuleSet

from kentro_server.api.auth import PrincipalDep
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
def apply_rules(body: ApplyRulesetRequest, principal: PrincipalDep) -> ApplyRulesetResponse:
    """Atomically commit a RuleSet as a new version. Returns the new version number."""
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
