"""Toggleable demo events — the catalog model.

A catalog entry is an `EventRow` with a JSON payload describing a deferred
operation. Today the only kind is `ingest_document`. First activation runs
the operation and tags every created DB row with `event_id`. Subsequent
toggles flip `active` and bump `activation_seq`; no re-ingestion happens.
Resolver tie-breaking uses `activation_seq`, so re-toggling an event makes
its writes the "newest" — and can flip conflict outcomes under
`LatestWriteResolver`.

Auth and HTTP framing live in the route layer; this module is pure-Python
domain logic.
"""

import json
import logging
from uuid import UUID

from kentro.types import IngestionResult
from sqlalchemy import func
from sqlmodel import Session, col, select

from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.extraction import ingest_document
from kentro_server.skills.llm_client import LLMClient
from kentro_server.store import TenantStore
from kentro_server.store.models import EventRow

logger = logging.getLogger(__name__)


def register_ingest_event(
    store: TenantStore,
    *,
    catalog_key: str,
    title: str,
    description: str | None,
    content: str,
    label: str,
    source_class: str | None,
    catalog_order: int,
) -> EventRow:
    """Add (or return existing) an `ingest_document` catalog entry. Inactive by default.

    Idempotent on `catalog_key`: re-running the seed is safe. The payload is
    stored verbatim so first activation can hand it to the ingestor without
    re-resolving filenames.
    """
    payload = json.dumps(
        {
            "content": content,
            "label": label,
            "source_class": source_class,
        }
    )
    with store.session() as session:
        existing = session.exec(
            select(EventRow).where(EventRow.catalog_key == catalog_key)
        ).first()
        if existing is not None:
            return existing
        event = EventRow(
            catalog_key=catalog_key,
            title=title,
            description=description,
            kind="ingest_document",
            payload_json=payload,
            catalog_order=catalog_order,
            activation_seq=None,
            active=False,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event


def activate_event(
    store: TenantStore,
    *,
    schema: SchemaRegistry,
    llm: LLMClient,
    smart_model: str,
    rule_version: int,
    written_by_agent_id: str,
    event_id: UUID,
) -> tuple[EventRow, IngestionResult | None]:
    """Activate an event. Returns (updated_row, ingest_result_or_None).

    First activation runs the underlying operation and returns its
    `IngestionResult`. Subsequent activations only bump `activation_seq` +
    flip the flag and return `None` for the result (no new ingest happened).

    The first-activation branch executes outside the row-update transaction so
    a long-running LLM call doesn't hold a write lock. After ingestion
    completes, a separate transaction bumps the activation counter.
    """
    with store.session() as session:
        event = session.get(EventRow, event_id)
        if event is None:
            raise KeyError(f"event {event_id} not found")
        first_time = event.activation_seq is None
        kind = event.kind
        payload_json = event.payload_json

    ingest_result: IngestionResult | None = None
    if first_time:
        match kind:
            case "ingest_document":
                payload = json.loads(payload_json)
                ingest_result = ingest_document(
                    store=store,
                    llm=llm,
                    content=payload["content"].encode("utf-8"),
                    label=payload["label"],
                    registered_schemas=schema.list_all(),
                    written_by_agent_id=written_by_agent_id,
                    rule_version=rule_version,
                    smart_model=smart_model,
                    source_class=payload.get("source_class"),
                    event_id=event_id,
                )
            case _:
                raise ValueError(f"unknown event kind: {kind!r}")

    with store.session() as session:
        event = session.get(EventRow, event_id)
        if event is None:
            raise KeyError(f"event {event_id} disappeared after first-activation work")
        event.activation_seq = _next_activation_seq(session)
        event.active = True
        session.add(event)
        session.commit()
        session.refresh(event)
        return event, ingest_result


def deactivate_event(store: TenantStore, *, event_id: UUID) -> EventRow:
    """Flip the active flag off. Rows tagged with this event are filtered out
    of subsequent reads via the JOIN. Cheap; no DB churn beyond the flag."""
    with store.session() as session:
        event = session.get(EventRow, event_id)
        if event is None:
            raise KeyError(f"event {event_id} not found")
        event.active = False
        session.add(event)
        session.commit()
        session.refresh(event)
        return event


def list_events(store: TenantStore) -> list[EventRow]:
    """All catalog events, ordered by `catalog_order` for stable UI rendering."""
    with store.session() as session:
        return list(session.exec(select(EventRow).order_by(col(EventRow.catalog_order))).all())


def _next_activation_seq(session: Session) -> int:
    """`MAX(activation_seq) + 1`, or 1 if no event has ever been activated.

    Single-tenant demo, so concurrent toggles are not a meaningful concern; if
    two activations raced and produced the same seq, resolver tie-breaking
    would just be undefined-but-deterministic on row id. Acceptable for v0.
    """
    current_max = session.exec(select(func.max(EventRow.activation_seq))).one()
    if current_max is None:
        return 1
    return int(current_max) + 1


__all__ = [
    "activate_event",
    "deactivate_event",
    "list_events",
    "register_ingest_event",
]
