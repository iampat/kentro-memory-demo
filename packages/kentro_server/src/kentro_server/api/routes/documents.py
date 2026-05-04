"""POST /documents — ingest a markdown source; DELETE /documents/{id} — remove + re-resolve;
GET /documents — list ingested sources for the demo UI."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from kentro.types import (
    DocumentListResponse,
    DocumentSummary,
    ExtractionStepListResponse,
    ExtractionStepView,
    IngestionResult,
)
from sqlalchemy import or_
from sqlmodel import col, select

from kentro_server.api.auth import AdminPrincipalDep, PrincipalDep
from kentro_server.api.deps import LLMClientDep, SchemaRegistryDep, SettingsDep
from kentro_server.api.dtos import IngestRequest
from kentro_server.core.catalog import activate_event, register_ingest_event
from kentro_server.core.rules import load_active_ruleset
from kentro_server.core.source_removal import remove_document
from kentro_server.store.models import DocumentRow, EventRow, ExtractionStepRow, FieldWriteRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
def list_documents(principal: PrincipalDep) -> DocumentListResponse:
    """List every LIVE document in the tenant — used by the demo UI's source pane.

    Documents whose owning catalog event is currently inactive are filtered out:
    toggling an ingestion event off removes the doc from the panel without
    losing the underlying rows. Documents with NULL `event_id` (legacy /
    admin-direct ingests) always show.

    Not ACL-filtered today: documents are tenant-scoped (any agent on the tenant
    sees the full list). Per-document blob fetch + per-write permissions still
    gate access to derived field data.
    """
    summaries: list[DocumentSummary] = []
    with principal.store.session() as session:
        rows = session.exec(
            select(DocumentRow)
            .join(EventRow, isouter=True)
            .where(
                or_(
                    col(DocumentRow.event_id).is_(None),
                    col(EventRow.active).is_(True),
                )
            )
            .order_by(col(DocumentRow.created_at).desc())
        ).all()
        for row in rows:
            field_writes = session.exec(
                select(FieldWriteRow).where(FieldWriteRow.source_document_id == row.id)
            ).all()
            summaries.append(
                DocumentSummary(
                    id=str(row.id),
                    label=row.label,
                    source_class=row.source_class,
                    content_hash=row.content_hash,
                    created_at=row.created_at.isoformat(),
                    blob_key=row.blob_key,
                    field_write_count=len(field_writes),
                )
            )
    return DocumentListResponse(documents=tuple(summaries))


@router.post("", response_model=IngestionResult)
def ingest(
    body: IngestRequest,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
    llm: LLMClientDep,
    settings: SettingsDep,
) -> IngestionResult:
    """Ingest one document via the catalog so it stays toggleable.

    Every ingest registers an `EventRow` (catalog_key = `ad-hoc:<label>`) and
    activates it — first activation runs the LLM extraction and tags every
    created row with `event_id`. The viewer can later toggle the event off
    in the UI to "remove" the document from the world without losing the
    underlying rows. Re-posting the same label is a no-op past the first
    activation (the catalog entry is keyed on label).

    Ad-hoc events get `catalog_order >= 1000` so the demo author's
    hand-curated seed order stays at the top of the catalog UI.
    """
    ruleset = load_active_ruleset(principal.store)
    label = body.label or "untitled"
    event = register_ingest_event(
        principal.store,
        catalog_key=f"ad-hoc:{label}",
        title=label,
        description=None,
        content=body.content,
        label=label,
        source_class=body.source_class,
        catalog_order=_next_ad_hoc_catalog_order(principal.store),
    )
    _, result = activate_event(
        principal.store,
        schema=schema,
        llm=llm,
        smart_model=body.smart_model or settings.kentro_llm_smart_model,
        rule_version=ruleset.version,
        written_by_agent_id=principal.agent_id,
        event_id=event.id,
    )
    if result is None:
        # Catalog entry already existed and had been activated before — no new
        # extraction this turn. Tell the caller plainly.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"document with label {label!r} was previously ingested as catalog "
                f"event {event.id}; toggle it from the UI instead of re-posting"
            ),
        )
    return result


def _next_ad_hoc_catalog_order(store) -> int:
    """Push ad-hoc ingests below the seeded catalog entries. Counts from 1000+
    so the demo's hand-curated order (1..N) renders at the top."""
    with store.session() as session:
        existing = session.exec(
            select(EventRow.catalog_order).where(EventRow.catalog_order >= 1000)
        ).all()
    if not existing:
        return 1000
    return max(existing) + 1


@router.get("/{document_id}/extraction-steps", response_model=ExtractionStepListResponse)
def list_extraction_steps(
    document_id: UUID,
    principal: PrincipalDep,
) -> ExtractionStepListResponse:
    """Per-document trace of every LLM extraction step that produced its writes.

    Joins through `FieldWriteRow.source_document_id == document_id` to find the
    distinct `extraction_step_id`s, then loads the corresponding rows. Counts
    how many distinct (entity_type, key, field_name) writes each step produced
    so the UI can surface "extracted N facts" per step.

    Tenant-scoped via the bearer; not ACL-filtered (the steps are telemetry
    metadata, not field values). 404 if the document doesn't exist on this
    tenant — keeps an enumeration probe from learning that a UUID belongs to
    some other tenant.
    """
    with principal.store.session() as session:
        doc = session.exec(select(DocumentRow).where(DocumentRow.id == document_id)).first()
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"document {document_id} not found",
            )
        writes = session.exec(
            select(FieldWriteRow).where(FieldWriteRow.source_document_id == document_id)
        ).all()
        # Count distinct (entity_id, field_name) per extraction_step_id.
        per_step_writes: dict[UUID, set[tuple[UUID, str]]] = {}
        for w in writes:
            if w.extraction_step_id is None:
                continue
            per_step_writes.setdefault(w.extraction_step_id, set()).add(
                (w.entity_id, w.field_name)
            )
        if not per_step_writes:
            return ExtractionStepListResponse(document_id=str(document_id), steps=())
        step_ids = list(per_step_writes.keys())
        steps = session.exec(
            select(ExtractionStepRow)
            .where(col(ExtractionStepRow.id).in_(step_ids))
            .order_by(col(ExtractionStepRow.created_at))
        ).all()
        views = tuple(
            ExtractionStepView(
                id=str(s.id),
                document_id=str(document_id),
                name=s.name,
                model=s.model,
                input_excerpt=s.input_excerpt,
                output_summary=s.output_summary,
                tokens_in=s.tokens_in,
                tokens_out=s.tokens_out,
                latency_ms=s.latency_ms,
                created_at=s.created_at.isoformat(),
                produced_writes=len(per_step_writes.get(s.id, set())),
            )
            for s in steps
        )
    return ExtractionStepListResponse(document_id=str(document_id), steps=views)


@router.delete("/{document_id}")
def delete(document_id: UUID, principal: AdminPrincipalDep) -> dict:
    """Remove a document, its writes, its blob, and re-resolve any affected conflicts. ADMIN only.

    Source removal is irreversible (cascades to writes, conflict evidence, blob).
    Gated to admin so a low-privilege agent can't wipe historical state.

    Returns the demo-shaped `{removed_writes, closed_conflicts}` summary so the
    caller (and the smoke test) can assert the cascade ran.
    """
    try:
        removed_writes, closed_conflicts = remove_document(
            store=principal.store,
            document_id=document_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {document_id} not found",
        ) from exc
    return {
        "removed_writes": removed_writes,
        "closed_conflicts": [
            {"entity_id": str(eid), "field_name": fname} for eid, fname in closed_conflicts
        ],
    }
