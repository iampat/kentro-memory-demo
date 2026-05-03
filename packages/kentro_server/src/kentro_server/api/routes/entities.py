"""Entity read/write routes.

- `GET  /entities/{type}/{key}` — read with the default `AutoResolver`.
- `POST /entities/{type}/{key}/read` — read with a non-default `ResolverSpec`
  (body is `ReadRequest`). POST because a SkillResolverSpec carries a
  potentially long `prompt` string that we don't want in a URL.
- `POST /entities/{type}/{key}/{field}` — write a single field as the
  authenticated agent.
"""

import logging

from fastapi import APIRouter
from kentro.types import AutoResolverSpec, EntityRecord, WriteResult

from kentro_server.api.auth import PrincipalDep
from kentro_server.api.deps import LLMClientDep, SchemaRegistryDep
from kentro_server.api.dtos import ReadRequest, WriteRequest
from kentro_server.core.read import read_entity
from kentro_server.core.rules import load_active_ruleset
from kentro_server.core.write import write_field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("/{entity_type}/{entity_key}", response_model=EntityRecord)
def get_entity(
    entity_type: str,
    entity_key: str,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
    llm: LLMClientDep,
) -> EntityRecord:
    """Default-resolver read (AutoResolver). For SkillResolver / etc., POST /read."""
    ruleset = load_active_ruleset(principal.store)
    return read_entity(
        store=principal.store,
        schema=schema,
        ruleset=ruleset,
        agent_id=principal.agent_id,
        entity_type=entity_type,
        entity_key=entity_key,
        resolver=AutoResolverSpec(),
        llm=llm,
    )


@router.post("/{entity_type}/{entity_key}/read", response_model=EntityRecord)
def read(
    entity_type: str,
    entity_key: str,
    body: ReadRequest,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
    llm: LLMClientDep,
) -> EntityRecord:
    """Read with an explicit ResolverSpec (raw / latest_write / prefer_agent / skill / auto)."""
    ruleset = load_active_ruleset(principal.store)
    return read_entity(
        store=principal.store,
        schema=schema,
        ruleset=ruleset,
        agent_id=principal.agent_id,
        entity_type=entity_type,
        entity_key=entity_key,
        resolver=body.resolver,
        llm=llm,
    )


@router.post("/{entity_type}/{entity_key}/{field_name}", response_model=WriteResult)
def write(
    entity_type: str,
    entity_key: str,
    field_name: str,
    body: WriteRequest,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
) -> WriteResult:
    """Write one field. Returns a typed WriteResult (APPLIED / CONFLICT_RECORDED / DENIED).

    `write_field` loads the active ruleset internally, so the ACL check and the
    lineage stamp share the same version (no skew window).
    """
    return write_field(
        store=principal.store,
        schema=schema,
        agent_id=principal.agent_id,
        entity_type=entity_type,
        entity_key=entity_key,
        field_name=field_name,
        value_json=body.value_json,
        confidence=body.confidence,
    )
