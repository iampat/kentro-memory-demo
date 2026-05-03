"""Tests for `extraction.ingestor.ingest_document`.

Uses a fake LLMClient that returns canned ExtractionResult, so no network calls.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from kentro.types import EntityTypeDef, FieldDef, FieldStatus
from kentro_server.extraction import ingest_document
from kentro_server.skills.llm_client import (
    ExtractedEntity,
    ExtractedField,
    ExtractionResult,
    LLMClient,
    SkillResolverDecision,
)
from kentro_server.store import TenantConfig, TenantRegistry, TenantsConfig, TenantStore
from kentro_server.store.models import (
    AgentRow,
    ConflictRow,
    DocumentRow,
    EntityRow,
    ExtractionStepRow,
    FieldWriteRow,
    RuleVersionRow,
)
from sqlmodel import select


@dataclass
class _FakeExtractor(LLMClient):
    """Returns canned ExtractionResults from a queue."""

    queue: list[ExtractionResult] = field(default_factory=list)
    extract_call_count: int = 0

    def run_skill_resolver(self, *, prompt, candidates, model=None):
        return SkillResolverDecision(chosen_value_json=None, reason="not under test")

    def extract_entities(
        self, *, document_text, registered_schemas, document_label=None, model=None
    ):
        self.extract_call_count += 1
        if not self.queue:
            return ExtractionResult(entities=())
        return self.queue.pop(0)


@pytest.fixture
def store(tmp_path: Path) -> TenantStore:
    config = TenantsConfig(tenants=(TenantConfig(id="demo-1", api_key="demo-1-key"),))
    reg = TenantRegistry(tmp_path / "kentro_state", config)
    s = reg.get("demo-1")
    with s.session() as session:
        session.add(AgentRow(id="ingestion_agent"))
        session.add(RuleVersionRow(version=1))
        session.commit()
    return s


REGISTERED = [
    EntityTypeDef(
        name="Customer",
        fields=(
            FieldDef(name="name", type_str="str"),
            FieldDef(name="deal_size", type_str="float | None", required=False),
        ),
    ),
    EntityTypeDef(
        name="Person",
        fields=(
            FieldDef(name="name", type_str="str"),
            FieldDef(name="phone", type_str="str | None", required=False),
            FieldDef(name="email", type_str="str | None", required=False),
        ),
    ),
]


def test_single_doc_single_entity_writes_blob_doc_row_extraction_step_and_field(
    store: TenantStore,
) -> None:
    canned = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Customer",
                key="Acme",
                fields=(
                    ExtractedField(field_name="deal_size", value_json="250000", confidence=0.9),
                    ExtractedField(field_name="name", value_json='"Acme"'),
                ),
            ),
        )
    )
    llm = _FakeExtractor(queue=[canned])
    content = b"# Acme call notes\nRenewal floated at $250K."

    result = ingest_document(
        store=store,
        llm=llm,
        content=content,
        label="acme_call.md",
        registered_schemas=REGISTERED,
        written_by_agent_id="ingestion_agent",
        rule_version=1,
        smart_model="claude-sonnet-4-6",
    )

    if llm.extract_call_count != 1:
        raise AssertionError("extractor must be called exactly once")

    if len(result.entities) != 1 or result.entities[0].key != "Acme":
        raise AssertionError(f"unexpected entities in result: {result.entities!r}")

    fv = result.entities[0].fields["deal_size"]
    if fv.status != FieldStatus.KNOWN:
        raise AssertionError(f"expected KNOWN, got {fv.status}")
    if fv.value != 250000:
        raise AssertionError(f"value should be decoded as int 250000, got {fv.value!r}")
    if len(fv.lineage) != 1 or fv.lineage[0].source_document_id != result.source_document_id:
        raise AssertionError("lineage should point at the ingested document")

    # Persistence side-effects
    blob_key = f"{result.source_document_id}.md"
    if store.blobs.get(blob_key) != content:
        raise AssertionError("blob content must round-trip")

    with store.session() as session:
        docs = session.exec(select(DocumentRow)).all()
        if len(docs) != 1 or docs[0].id != result.source_document_id:
            raise AssertionError("DocumentRow not persisted correctly")

        entities = session.exec(select(EntityRow).where(EntityRow.type == "Customer")).all()
        if len(entities) != 1 or entities[0].key != "Acme":
            raise AssertionError(f"expected one Customer/Acme row, got {entities!r}")

        steps = session.exec(select(ExtractionStepRow)).all()
        if len(steps) != 1 or steps[0].model != "claude-sonnet-4-6":
            raise AssertionError(f"unexpected ExtractionStep rows: {steps!r}")

        writes = session.exec(select(FieldWriteRow)).all()
        if len(writes) != 2:
            raise AssertionError(
                f"expected 2 FieldWriteRows (deal_size + name), got {len(writes)}"
            )
        for w in writes:
            if w.source_document_id != result.source_document_id:
                raise AssertionError("write must back-reference the source document")
            if w.extraction_step_id != steps[0].id:
                raise AssertionError("write must back-reference its extraction step")


def test_two_docs_same_entity_different_fields_hydrates_one_entity(store: TenantStore) -> None:
    """Multi-document hydration: phone in doc 1, email in doc 2 → one Person.Ali."""
    doc1 = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Person",
                key="Ali",
                fields=(ExtractedField(field_name="phone", value_json='"778-968-1361"'),),
            ),
        )
    )
    doc2 = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Person",
                key="Ali",
                fields=(ExtractedField(field_name="email", value_json='"ali@kentro.ai"'),),
            ),
        )
    )
    llm = _FakeExtractor(queue=[doc1, doc2])

    ingest_document(
        store=store,
        llm=llm,
        content=b"meeting note 1: Ali phone 778-968-1361",
        label="ali_phone.md",
        registered_schemas=REGISTERED,
        written_by_agent_id="ingestion_agent",
        rule_version=1,
        smart_model="claude-sonnet-4-6",
    )
    ingest_document(
        store=store,
        llm=llm,
        content=b"meeting note 2: Ali email ali@kentro.ai",
        label="ali_email.md",
        registered_schemas=REGISTERED,
        written_by_agent_id="ingestion_agent",
        rule_version=1,
        smart_model="claude-sonnet-4-6",
    )

    with store.session() as session:
        entities = session.exec(select(EntityRow).where(EntityRow.type == "Person")).all()
        if len(entities) != 1:
            raise AssertionError(
                f"strict-key resolution should yield 1 Person.Ali, got {len(entities)}"
            )
        writes = session.exec(
            select(FieldWriteRow).where(FieldWriteRow.entity_id == entities[0].id)
        ).all()
        if {w.field_name for w in writes} != {"phone", "email"}:
            raise AssertionError(
                f"expected phone+email field writes, got {[w.field_name for w in writes]}"
            )
        conflicts = session.exec(select(ConflictRow)).all()
        if conflicts:
            raise AssertionError("different fields must not produce a conflict")


def test_two_docs_same_field_different_values_creates_conflict(store: TenantStore) -> None:
    doc1 = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Customer",
                key="Acme",
                fields=(ExtractedField(field_name="deal_size", value_json="250000"),),
            ),
        )
    )
    doc2 = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Customer",
                key="Acme",
                fields=(ExtractedField(field_name="deal_size", value_json="300000"),),
            ),
        )
    )
    llm = _FakeExtractor(queue=[doc1, doc2])

    for content, label in [
        (b"transcript with $250K", "call.md"),
        (b"email with $300K", "email.md"),
    ]:
        ingest_document(
            store=store,
            llm=llm,
            content=content,
            label=label,
            registered_schemas=REGISTERED,
            written_by_agent_id="ingestion_agent",
            rule_version=1,
            smart_model="claude-sonnet-4-6",
        )

    with store.session() as session:
        conflicts = session.exec(select(ConflictRow)).all()
        if len(conflicts) != 1:
            raise AssertionError(f"expected one open ConflictRow, got {len(conflicts)}")
        if conflicts[0].field_name != "deal_size":
            raise AssertionError("conflict should be on deal_size")


def test_extractor_returning_unregistered_entity_type_is_skipped(store: TenantStore) -> None:
    canned = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Spaceship",
                key="Voyager",  # not in REGISTERED
                fields=(ExtractedField(field_name="mass_kg", value_json="721000"),),
            ),
        )
    )
    llm = _FakeExtractor(queue=[canned])

    result = ingest_document(
        store=store,
        llm=llm,
        content=b"sci-fi notes",
        label="not_relevant.md",
        registered_schemas=REGISTERED,
        written_by_agent_id="ingestion_agent",
        rule_version=1,
        smart_model="claude-sonnet-4-6",
    )

    if result.entities:
        raise AssertionError("unregistered entity types must be skipped, not returned")
    with store.session() as session:
        if session.exec(select(EntityRow)).all():
            raise AssertionError("no EntityRow should be created for an unregistered type")
        if not session.exec(select(DocumentRow)).all():
            raise AssertionError("DocumentRow must still be persisted (we kept the blob)")
