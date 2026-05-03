"""Document ingestion — store blob, run smart-tier extraction, write fields, return IngestionResult.

Flow:
1. Store the markdown bytes in the tenant's blob store.
2. Persist a `DocumentRow` so the blob is discoverable by id.
3. Run the smart-tier `extract_entities` LLM call (timed; model recorded).
4. Persist an `ExtractionStepRow` so the lineage records can point back at the call.
5. For each extracted entity: get-or-create the `EntityRow` via strict-key (type, key).
6. For each extracted field: write via `core/conflict.record_field_write` so conflicts
   are detected and corroboration accumulates correctly.
7. Build and return the SDK-shaped `IngestionResult` so the API handler can pass it
   straight back to the caller.

Token-count telemetry is intentionally `0/0` for v0 — instructor's structured-output
responses don't expose usage uniformly across providers, and the demo doesn't need
it. v0.1 should plumb it through for the cost-at-scale narrative.
"""

import hashlib
import json
import logging
import time
from uuid import uuid4

from sqlmodel import select

from kentro.types import (
    EntityRecord,
    ExtractionStep,
    FieldStatus,
    FieldValue,
    IngestionResult,
    LineageRecord,
)
from kentro_server.core.conflict import record_field_write
from kentro_server.skills.llm_client import LLMClient
from kentro_server.store import TenantStore
from kentro_server.store.models import (
    DocumentRow,
    EntityRow,
    ExtractionStepRow,
)

logger = logging.getLogger(__name__)

_INPUT_EXCERPT_CHARS = 240


def ingest_document(
    *,
    store: TenantStore,
    llm: LLMClient,
    content: bytes,
    label: str | None,
    registered_entity_types: list[str],
    written_by_agent_id: str,
    rule_version: int,
    smart_model: str,
) -> IngestionResult:
    """Ingest one document end-to-end. Returns the SDK-shaped `IngestionResult`."""
    text = content.decode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()
    doc_id = uuid4()
    blob_key = f"{doc_id}.md"
    store.blobs.put(blob_key, content)

    started = time.perf_counter()
    extraction = llm.extract_entities(
        document_text=text,
        registered_entity_types=registered_entity_types,
        document_label=label,
        model=smart_model,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    extraction_step_id = uuid4()
    output_summary = (
        f"{len(extraction.entities)} entities; "
        f"{sum(len(e.fields) for e in extraction.entities)} fields"
    )
    if extraction.notes:
        output_summary += f"; notes: {extraction.notes[:120]}"

    extraction_step_dto = ExtractionStep(
        id=extraction_step_id,
        name="extract_entities",
        model=smart_model,
        input_excerpt=text[:_INPUT_EXCERPT_CHARS],
        output_summary=output_summary,
        tokens_in=0,
        tokens_out=0,
        latency_ms=latency_ms,
    )

    entity_records: list[EntityRecord] = []
    with store.session() as session:
        session.add(DocumentRow(
            id=doc_id,
            blob_key=blob_key,
            content_hash=content_hash,
            label=label,
        ))
        session.add(ExtractionStepRow(
            id=extraction_step_dto.id,
            name=extraction_step_dto.name,
            model=extraction_step_dto.model,
            input_excerpt=extraction_step_dto.input_excerpt,
            output_summary=extraction_step_dto.output_summary,
            tokens_in=extraction_step_dto.tokens_in,
            tokens_out=extraction_step_dto.tokens_out,
            latency_ms=extraction_step_dto.latency_ms,
        ))
        session.flush()

        for ext_entity in extraction.entities:
            if ext_entity.entity_type not in registered_entity_types:
                logger.warning(
                    "ingestor: extractor returned unregistered entity_type=%r — skipping",
                    ext_entity.entity_type,
                )
                continue

            entity_id = _get_or_create_entity_id(
                session, entity_type=ext_entity.entity_type, key=ext_entity.key,
            )
            field_values: dict[str, FieldValue] = {}
            for ef in ext_entity.fields:
                write, _conflict = record_field_write(
                    session,
                    entity_id=entity_id,
                    field_name=ef.field_name,
                    value_json=ef.value_json,
                    confidence=ef.confidence,
                    written_by_agent_id=written_by_agent_id,
                    rule_version_at_write=rule_version,
                    source_document_id=doc_id,
                    extraction_step_id=extraction_step_id,
                )
                lineage = LineageRecord(
                    source_document_id=doc_id,
                    written_at=write.written_at,
                    written_by_agent_id=written_by_agent_id,
                    rule_version=rule_version,
                    extraction_step_id=extraction_step_id,
                )
                field_values[ef.field_name] = FieldValue(
                    status=FieldStatus.KNOWN,
                    value=_decode_value(ef.value_json),
                    confidence=ef.confidence,
                    lineage=(lineage,),
                )

            entity_records.append(EntityRecord(
                entity_type=ext_entity.entity_type,
                key=ext_entity.key,
                fields=field_values,
            ))

        session.commit()

    return IngestionResult(
        source_document_id=doc_id,
        entities=tuple(entity_records),
        extraction_steps=(extraction_step_dto,),
    )


def _get_or_create_entity_id(session, *, entity_type: str, key: str):
    """Strict-key lookup: one EntityRow per (type, key). Per memory.md v0 decision."""
    existing = session.exec(
        select(EntityRow).where(
            EntityRow.type == entity_type,
            EntityRow.key == key,
        )
    ).first()
    if existing is not None:
        return existing.id
    new_entity = EntityRow(type=entity_type, key=key)
    session.add(new_entity)
    session.flush()
    return new_entity.id


def _decode_value(value_json: str):
    """Best-effort JSON decode; fall back to the raw string if the LLM returned non-JSON."""
    try:
        return json.loads(value_json)
    except json.JSONDecodeError:
        logger.warning("ingestor: extractor returned non-JSON value, keeping as string: %r", value_json[:80])
        return value_json


__all__ = ["ingest_document"]
