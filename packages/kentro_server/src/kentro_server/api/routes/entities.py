"""Entity read/write routes.

- `GET  /entities/{type}` — list entities of one type (ACL-filtered).
- `GET  /entities/{type}/{key}` — read with the default `AutoResolver`.
- `POST /entities/{type}/{key}/read` — read with a non-default `ResolverSpec`
  (body is `ReadRequest`). POST because a SkillResolverSpec carries a
  potentially long `prompt` string that we don't want in a URL.
- `POST /entities/{type}/{key}/{field}` — write a single field as the
  authenticated agent.
"""

import logging

from fastapi import APIRouter
from kentro.acl import evaluate_entity_visibility
from kentro.types import (
    AutoResolverSpec,
    EntityListResponse,
    EntityRecord,
    EntitySummary,
    WriteResult,
)
from sqlmodel import col, select

from kentro_server.api.auth import PrincipalDep
from kentro_server.api.deps import EventBusDep, LLMClientDep, SchemaRegistryDep
from kentro_server.api.dtos import ReadRequest, WriteRequest
from kentro_server.core.read import read_entity
from kentro_server.core.rules import load_active_ruleset
from kentro_server.core.write import write_field
from kentro_server.store.models import EntityRow, FieldWriteRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("/{entity_type}", response_model=EntityListResponse)
def list_entities_of_type(
    entity_type: str,
    principal: PrincipalDep,
) -> EntityListResponse:
    """List entities of one type, filtered by `EntityVisibilityRule` for the caller.

    Used by the demo UI to populate left-pane lists per entity type. Hidden
    entities (per ACL) are dropped from the response — same default-deny shape
    as field reads. `field_count` is the count of distinct (live) field writes
    on the entity, surfacing "[N fields]" badges without per-key reads.
    """
    ruleset = load_active_ruleset(principal.store)
    summaries: list[EntitySummary] = []
    with principal.store.session() as session:
        rows = session.exec(
            select(EntityRow).where(EntityRow.type == entity_type).order_by(EntityRow.key)
        ).all()
        for row in rows:
            visibility = evaluate_entity_visibility(
                entity_type=row.type,
                entity_key=row.key,
                agent_id=principal.agent_id,
                ruleset=ruleset,
            )
            if not visibility.allowed:
                continue
            field_count = len(
                set(
                    session.exec(
                        select(FieldWriteRow.field_name).where(
                            FieldWriteRow.entity_id == row.id,
                            ~col(FieldWriteRow.superseded),
                        )
                    ).all()
                )
            )
            summaries.append(EntitySummary(type=row.type, key=row.key, field_count=field_count))
    return EntityListResponse(entity_type=entity_type, entities=tuple(summaries))


@router.get("/{entity_type}/{entity_key}", response_model=EntityRecord)
def get_entity(
    entity_type: str,
    entity_key: str,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
    llm: LLMClientDep,
    event_bus: EventBusDep,
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
        event_bus=event_bus,
    )


@router.post("/{entity_type}/{entity_key}/read", response_model=EntityRecord)
def read(
    entity_type: str,
    entity_key: str,
    body: ReadRequest,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
    llm: LLMClientDep,
    event_bus: EventBusDep,
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
        event_bus=event_bus,
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
