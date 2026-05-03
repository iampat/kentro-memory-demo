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

from kentro.types import (
    EntityRecord,
    ExtractionStep,
    FieldStatus,
    FieldValue,
    IngestionResult,
    LineageRecord,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

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
    registered_schemas: list,
    written_by_agent_id: str,
    rule_version: int,
    smart_model: str,
    source_class: str | None = None,
) -> IngestionResult:
    """Ingest one document end-to-end. Returns the SDK-shaped `IngestionResult`.

    `registered_schemas` is a list of `kentro.types.EntityTypeDef` (typed loosely
    here to avoid the SDK→server import); the LLM sees the full field declarations
    so it emits canonical field names with values matching the declared types.

    `source_class` is an optional string the caller can attach to the document
    (e.g. `"verbal"` for call transcripts, `"written"` for emails). Persisted
    on `DocumentRow.source_class`; consumed by SkillResolvers and the demo UI.
    """
    text = content.decode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()
    doc_id = uuid4()
    blob_key = f"{doc_id}.md"

    registered_names = {td.name for td in registered_schemas}
    registered_fields_by_type: dict[str, set[str]] = {
        td.name: {f.name for f in td.fields} for td in registered_schemas
    }

    # Stage the blob first so the subsequent DB rows can reference it. If anything
    # below fails (extraction, DB commit), `_cleanup_blob_on_failure` removes the
    # orphaned blob so retries don't accumulate unreachable data.
    store.blobs.put(blob_key, content)
    try:
        started = time.perf_counter()
        extraction = llm.extract_entities(
            document_text=text,
            registered_schemas=registered_schemas,
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
            session.add(
                DocumentRow(
                    id=doc_id,
                    blob_key=blob_key,
                    content_hash=content_hash,
                    label=label,
                    source_class=source_class,
                )
            )
            session.add(
                ExtractionStepRow(
                    id=extraction_step_dto.id,
                    name=extraction_step_dto.name,
                    model=extraction_step_dto.model,
                    input_excerpt=extraction_step_dto.input_excerpt,
                    output_summary=extraction_step_dto.output_summary,
                    tokens_in=extraction_step_dto.tokens_in,
                    tokens_out=extraction_step_dto.tokens_out,
                    latency_ms=extraction_step_dto.latency_ms,
                )
            )
            session.flush()

            for ext_entity in extraction.entities:
                if ext_entity.entity_type not in registered_names:
                    logger.warning(
                        "ingestor: extractor returned unregistered entity_type=%r — skipping",
                        ext_entity.entity_type,
                    )
                    continue

                entity_id = _get_or_create_entity_id(
                    session,
                    entity_type=ext_entity.entity_type,
                    key=ext_entity.key,
                )
                allowed_fields = registered_fields_by_type.get(ext_entity.entity_type, set())
                field_values: dict[str, FieldValue] = {}
                for ef in ext_entity.fields:
                    if allowed_fields and ef.field_name not in allowed_fields:
                        logger.warning(
                            "ingestor: extractor returned unregistered field %s.%s — skipping",
                            ext_entity.entity_type,
                            ef.field_name,
                        )
                        continue
                    write, _ = record_field_write(
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

                entity_records.append(
                    EntityRecord(
                        entity_type=ext_entity.entity_type,
                        key=ext_entity.key,
                        fields=field_values,
                    )
                )

            session.commit()
    except BaseException:
        # Extraction or DB persistence failed after the blob was written. Best-effort
        # cleanup of the orphan blob, then re-raise the original failure.
        # `BaseException` (not `Exception`) so KeyboardInterrupt also cleans up.
        _delete_orphan_blob(store, blob_key)
        raise

    return IngestionResult(
        source_document_id=doc_id,
        entities=tuple(entity_records),
        extraction_steps=(extraction_step_dto,),
    )


def _get_or_create_entity_id(session, *, entity_type: str, key: str):
    """Strict-key get-or-create: one EntityRow per (type, key).

    Race-safe under the `uq_entity_type_key` UNIQUE constraint: if two concurrent
    callers both miss the initial SELECT and both INSERT, the second INSERT raises
    `IntegrityError`; we roll back to a SAVEPOINT and re-SELECT to find the winner.

    Without the constraint + retry, the system would silently split one logical entity
    into two rows under retries / concurrent ingest, fragmenting conflict detection.
    """
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
    try:
        # SAVEPOINT so the IntegrityError doesn't poison the outer transaction.
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        # Lost the race — another writer beat us to the insert. Find their row.
        winner = session.exec(
            select(EntityRow).where(
                EntityRow.type == entity_type,
                EntityRow.key == key,
            )
        ).first()
        if winner is None:
            # Should be unreachable: IntegrityError on this UNIQUE means a row exists.
            raise
        return winner.id
    return new_entity.id


def _delete_orphan_blob(store: TenantStore, blob_key: str) -> None:
    """Best-effort delete of a blob whose owning DB rows never landed. Never raises;
    logs and swallows OS-level errors so it can run safely from an exception handler."""
    try:
        store.blobs.delete(blob_key)
    except OSError:
        logger.warning("ingestor: failed to clean up orphaned blob %r", blob_key, exc_info=True)


def _decode_value(value_json: str):
    """Best-effort JSON decode; fall back to the raw string if the LLM returned non-JSON."""
    try:
        return json.loads(value_json)
    except json.JSONDecodeError:
        logger.warning(
            "ingestor: extractor returned non-JSON value, keeping as string: %r", value_json[:80]
        )
        return value_json


__all__ = ["ingest_document"]
