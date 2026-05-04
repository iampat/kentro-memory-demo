"""Resolver-policy routes — sibling to /rules.

Conflict resolvers are stored separately from the ACL ruleset (see
`kentro_server.core.resolvers`). They answer "when two writes for
`(entity_type, field_name)` collide, which one wins?" — a different question
from "who can read what?", so they have their own endpoints.

Anyone with a valid bearer can change resolvers (per the demo's design — the
admin gate is for ACL changes, not data-quality knobs). If the security model
changes, swap `PrincipalDep` for `AdminPrincipalDep` here.
"""

import logging

from fastapi import APIRouter
from kentro.rules import render_resolver_policy
from kentro.types import ResolverPolicy, ResolverPolicySet
from pydantic import BaseModel, ConfigDict

from kentro_server.api.auth import PrincipalDep
from kentro_server.core.resolvers import apply_resolver_policies, load_active_resolver_policies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resolvers", tags=["resolvers"])


class ApplyResolversRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    policies: tuple[ResolverPolicy, ...]
    summary: str | None = None


class ApplyResolversResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: int
    policies_applied: int


@router.get("/active", response_model=ResolverPolicySet)
def get_active(principal: PrincipalDep) -> ResolverPolicySet:
    return load_active_resolver_policies(principal.store)


@router.post("/apply", response_model=ApplyResolversResponse)
def apply_resolvers(
    body: ApplyResolversRequest, principal: PrincipalDep
) -> ApplyResolversResponse:
    """Atomically commit resolver policies. UPSERT keyed by (entity_type, field_name)."""
    new_version = apply_resolver_policies(
        principal.store,
        policies=body.policies,
        summary=body.summary,
    )
    return ApplyResolversResponse(version=new_version, policies_applied=len(body.policies))


class RenderedResolverPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    summary: str
    entity_type: str
    field_name: str


class RenderedResolversResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: int
    policies: tuple[RenderedResolverPolicy, ...] = ()


@router.get("/active/rendered", response_model=RenderedResolversResponse)
def get_active_rendered(principal: PrincipalDep) -> RenderedResolversResponse:
    """Active resolver policies paired with one-line human summaries.

    Drives the LineageDrawer's resolver editor (it shows the active resolver
    for the field the drawer is open on).
    """
    policies = load_active_resolver_policies(principal.store)
    rendered = tuple(
        RenderedResolverPolicy(
            summary=render_resolver_policy(p),
            entity_type=p.entity_type,
            field_name=p.field_name,
        )
        for p in policies.policies
    )
    return RenderedResolversResponse(version=policies.version, policies=rendered)
