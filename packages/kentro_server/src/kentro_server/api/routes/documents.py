"""POST /documents — ingest a markdown source; DELETE /documents/{id} — remove + re-resolve."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from kentro.types import IngestionResult

from kentro_server.api.auth import AdminPrincipalDep, PrincipalDep
from kentro_server.api.deps import LLMClientDep, SchemaRegistryDep, SettingsDep
from kentro_server.api.dtos import IngestRequest
from kentro_server.core.rules import load_active_ruleset
from kentro_server.core.source_removal import remove_document
from kentro_server.extraction import ingest_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


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
