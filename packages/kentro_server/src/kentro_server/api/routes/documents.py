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
from sqlmodel import col, select

from kentro_server.api.auth import AdminPrincipalDep, PrincipalDep
from kentro_server.api.deps import LLMClientDep, SchemaRegistryDep, SettingsDep
from kentro_server.api.dtos import IngestRequest
from kentro_server.core.rules import load_active_ruleset
from kentro_server.core.source_removal import remove_document
from kentro_server.extraction import ingest_document
from kentro_server.store.models import DocumentRow, ExtractionStepRow, FieldWriteRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
def list_documents(principal: PrincipalDep) -> DocumentListResponse:
    """List every document in the tenant — used by the demo UI's source pane.

    Not ACL-filtered today: documents are tenant-scoped (any agent on the tenant
    sees the full list). The fields returned are metadata only (label, source
    class, hash) — never the blob contents. Per-document blob fetch + per-write
    permissions still gate access to derived field data.
    """
    summaries: list[DocumentSummary] = []
    with principal.store.session() as session:
        rows = session.exec(select(DocumentRow).order_by(col(DocumentRow.created_at).desc())).all()
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
    """Ingest one document. The authenticated agent is recorded as the writer."""
    ruleset = load_active_ruleset(principal.store)
    return ingest_document(
        store=principal.store,
        llm=llm,
        content=body.content.encode("utf-8"),
        label=body.label,
        registered_schemas=schema.list_all(),
        written_by_agent_id=principal.agent_id,
        rule_version=ruleset.version,
        smart_model=body.smart_model or settings.kentro_llm_smart_model,
        source_class=body.source_class,
    )


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
