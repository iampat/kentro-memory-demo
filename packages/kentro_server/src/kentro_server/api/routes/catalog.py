"""Catalog endpoints — list + toggle the demo's `EventRow` entries.

The catalog is the toggleable surface for ingestion events. Schemas, rules,
and any agent-initiated writes are NOT in the catalog (they are base infra
or live, never reversible).

Toggle is the only mutation: activating runs the lazy ingest on first
activation and bumps `activation_seq` (which the resolver uses for
tie-breaking) on every activation. Deactivating just flips a flag — the
underlying rows persist so the next activate is O(1).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from kentro.types import CatalogEventView, CatalogListResponse, ToggleEventResponse

from kentro_server.api.auth import AdminPrincipalDep, PrincipalDep
from kentro_server.api.deps import LLMClientDep, SchemaRegistryDep, SettingsDep
from kentro_server.core.catalog import (
    activate_event,
    deactivate_event,
    list_events,
)
from kentro_server.core.rules import load_active_ruleset
from kentro_server.store.models import EventRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _to_view(row: EventRow) -> CatalogEventView:
    return CatalogEventView(
        id=str(row.id),
        catalog_key=row.catalog_key,
        title=row.title,
        description=row.description,
        kind=row.kind,
        catalog_order=row.catalog_order,
        activation_seq=row.activation_seq,
        active=row.active,
    )


@router.get("", response_model=CatalogListResponse)
def list_catalog(principal: PrincipalDep) -> CatalogListResponse:
    """All catalog events for the principal's tenant, ordered by catalog_order.

    Tenant-scoped via the bearer; not ACL-filtered (the catalog is admin-facing
    and the events are demo metadata, not field values).
    """
    rows = list_events(principal.store)
    return CatalogListResponse(events=tuple(_to_view(r) for r in rows))


@router.post("/{event_id}/toggle", response_model=ToggleEventResponse)
def toggle(
    event_id: UUID,
    principal: AdminPrincipalDep,
    schema: SchemaRegistryDep,
    llm: LLMClientDep,
    settings: SettingsDep,
) -> ToggleEventResponse:
    """Flip the event's active flag.

    Activating an event for the first time runs the underlying operation
    (currently: an LLM extraction) and tags every created row with
    `event_id`. Subsequent activations only bump `activation_seq` and flip
    the flag — re-toggle is O(1) and free of LLM cost.

    Admin-only because activation can drive an LLM call (cost gate) and
    deactivation alters the world-state every other agent reads.
    """
    ruleset = load_active_ruleset(principal.store)
    try:
        with principal.store.session() as session:
            event = session.get(EventRow, event_id)
            if event is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"event {event_id} not found",
                )
            currently_active = event.active

        if currently_active:
            updated = deactivate_event(principal.store, event_id=event_id)
        else:
            updated, _ = activate_event(
                principal.store,
                schema=schema,
                llm=llm,
                smart_model=settings.kentro_llm_smart_model,
                rule_version=ruleset.version,
                written_by_agent_id=principal.agent_id,
                event_id=event_id,
            )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return ToggleEventResponse(event=_to_view(updated))
