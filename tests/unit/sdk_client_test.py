"""Tests for the `kentro.Client` SDK over a TestClient(app) backend.

We construct a real `kentro.Client` whose underlying httpx is rerouted to an
in-process `TestClient(app)`. Same pattern as `cli_test.py`. This proves the
SDK speaks the actual route shapes without binding a port or making real
LLM calls.
"""

from collections.abc import Iterator
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import kentro
import pytest
from fastapi.testclient import TestClient
from kentro.types import (
    EntityTypeDef,
    EntityVisibilityRule,
    FieldDef,
    FieldReadRule,
    NLIntent,
    RawResolverSpec,
    RuleSet,
    WriteRule,
)
from kentro_server.api.deps import get_llm_client
from kentro_server.main import app
from kentro_server.skills.llm_client import (
    ExtractedEntity,
    ExtractedField,
    ExtractionResult,
    NLIntentItem,
    NLIntentList,
    ParsedRules,
)

from tests.unit._helpers import ADMIN_KEY, AGENT_KEY, FakeLLM


@pytest.fixture
def routed_test_client(isolated_state: None, fake_llm: FakeLLM) -> Iterator[TestClient]:
    """Bring up the in-process FastAPI app with overridden LLM. Yields the TestClient."""
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_llm_client, None)


def _make_transport(test_client: TestClient) -> httpx.MockTransport:
    """An httpx transport that delegates every request to the in-process TestClient.

    This is the cleanest way to drive `kentro.Client` against the real FastAPI
    app without binding a port. The SDK exposes a `transport=` kwarg precisely
    for this use case (and for production callers who want auth/logging
    interceptors)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path or "/"
        resp = test_client.request(
            request.method,
            path,
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            status_code=resp.status_code,
            headers=resp.headers,
            content=resp.content,
        )

    return httpx.MockTransport(handler)


def _admin_client(test_client: TestClient) -> kentro.Client:
    return kentro.Client(
        base_url="http://127.0.0.1:8000",
        api_key=ADMIN_KEY,
        transport=_make_transport(test_client),
    )


def _agent_client(test_client: TestClient) -> kentro.Client:
    return kentro.Client(
        base_url="http://127.0.0.1:8000",
        api_key=AGENT_KEY,
        transport=_make_transport(test_client),
    )


# === Health / ops ====================================================================


def test_healthz_works_without_auth(routed_test_client: TestClient) -> None:
    # healthz uses a separate httpx.get not the bearer-headered client; we still
    # need to route it through TestClient. Simplest: hit it via test_client directly.
    r = routed_test_client.get("/healthz")
    if r.status_code != 200 or r.json()["status"] != "ok":
        raise AssertionError(f"healthz failed: {r.status_code} {r.text}")


def test_llm_stats_via_sdk(routed_test_client: TestClient) -> None:
    with _admin_client(routed_test_client) as c:
        stats = c.llm_stats()
    if "hits" not in stats or "inner_calls" not in stats:
        raise AssertionError(f"unexpected llm_stats payload: {stats!r}")


# === Auth-error mapping ==============================================================


def test_invalid_key_raises_auth_error(routed_test_client: TestClient) -> None:
    bad = kentro.Client(
        base_url="http://127.0.0.1:8000",
        api_key="not-real",
        transport=_make_transport(routed_test_client),
    )
    with pytest.raises(kentro.AuthError), bad:
        bad.list_schema()


def test_non_admin_raises_admin_required_on_apply_rules(routed_test_client: TestClient) -> None:
    """The Critical-#1 contract surfaces as a typed exception in the SDK."""
    empty = RuleSet(rules=(), version=0)
    with _agent_client(routed_test_client) as c, pytest.raises(kentro.AdminRequiredError):
        c.apply_ruleset(empty, summary="non-admin attempts apply")


def test_non_admin_raises_admin_required_on_register_schema(
    routed_test_client: TestClient,
) -> None:
    with _agent_client(routed_test_client) as c, pytest.raises(kentro.AdminRequiredError):
        c.register_schema(
            [EntityTypeDef(name="Customer", fields=(FieldDef(name="name", type_str="str"),))]
        )


def test_non_admin_raises_admin_required_on_delete_document(
    routed_test_client: TestClient,
) -> None:
    with _agent_client(routed_test_client) as c, pytest.raises(kentro.AdminRequiredError):
        c.delete_document(uuid4())


# === Schema ==========================================================================


def test_list_schema_returns_typed_models_with_note_seeded(routed_test_client: TestClient) -> None:
    with _admin_client(routed_test_client) as c:
        defs = c.list_schema()
    names = {d.name for d in defs}
    if "Note" not in names:
        raise AssertionError("Note must be auto-seeded into the registry")


def test_register_schema_roundtrip(routed_test_client: TestClient) -> None:
    with _admin_client(routed_test_client) as c:
        result = c.register_schema(
            [EntityTypeDef(name="Customer", fields=(FieldDef(name="name", type_str="str"),))]
        )
    names = {d.name for d in result}
    if "Customer" not in names or "Note" not in names:
        raise AssertionError(f"unexpected register response: {names}")


# === Rules ===========================================================================


def test_apply_then_get_active_ruleset(routed_test_client: TestClient) -> None:
    with _admin_client(routed_test_client) as c:
        new_version = c.apply_ruleset(RuleSet(rules=(), version=0), summary="empty baseline")
        if new_version < 1:
            raise AssertionError(f"expected positive version, got {new_version}")
        active = c.get_active_ruleset()
    if active.version != new_version:
        raise AssertionError(f"active.version mismatch: {active.version} vs {new_version}")


def test_parse_nl_to_ruleset_via_sdk(routed_test_client: TestClient, fake_llm: FakeLLM) -> None:
    with _admin_client(routed_test_client) as c:
        # Need a schema for the orchestrator's validation step.
        c.register_schema(
            [
                EntityTypeDef(
                    name="Customer", fields=(FieldDef(name="deal_size", type_str="float"),)
                )
            ]
        )
        fake_llm.nl_intents = NLIntentList(
            intents=(NLIntentItem(kind="field_read", description="redact deal_size"),)
        )
        fake_llm.nl_rules = [
            ParsedRules(
                rule_jsons=(
                    FieldReadRule(
                        agent_id="ingestion_agent",
                        entity_type="Customer",
                        field_name="deal_size",
                        allowed=False,
                    ).model_dump_json(),
                ),
                reason="ok",
            )
        ]
        nl = c.parse_nl_to_ruleset("redact deal_size from ingestion")

    if len(nl.parsed_ruleset.rules) != 1:
        raise AssertionError(f"expected 1 parsed rule, got {nl!r}")
    if not nl.intents or not isinstance(nl.intents[0], NLIntent):
        raise AssertionError(f"expected typed NLIntent, got {nl.intents!r}")


# === Entities + write/read roundtrip =================================================


def _grant_full_ingestion_access(c: kentro.Client) -> None:
    """Apply the same permissive ruleset api_smoke_test uses."""
    c.apply_ruleset(
        RuleSet(
            rules=(
                EntityVisibilityRule(
                    agent_id="ingestion_agent", entity_type="Customer", allowed=True
                ),
                EntityVisibilityRule(agent_id="ingestion_agent", entity_type="Note", allowed=True),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Customer",
                    field_name="name",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Customer",
                    field_name="contact",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Customer",
                    field_name="deal_size",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Customer",
                    field_name="sales_notes",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Customer",
                    field_name="support_tickets",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="subject",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="predicate",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="object_json",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="confidence",
                    allowed=True,
                ),
                WriteRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="source_label",
                    allowed=True,
                ),
                FieldReadRule(
                    agent_id="ingestion_agent",
                    entity_type="Customer",
                    field_name="name",
                    allowed=True,
                ),
                FieldReadRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="subject",
                    allowed=True,
                ),
                FieldReadRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="predicate",
                    allowed=True,
                ),
                FieldReadRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="object_json",
                    allowed=True,
                ),
                FieldReadRule(
                    agent_id="ingestion_agent",
                    entity_type="Note",
                    field_name="source_label",
                    allowed=True,
                ),
            ),
            version=0,
        ),
        summary="sdk-test grant",
    )


def test_write_then_read_via_sdk(routed_test_client: TestClient) -> None:
    with _admin_client(routed_test_client) as c:
        c.register_schema(
            [EntityTypeDef(name="Customer", fields=(FieldDef(name="name", type_str="str"),))]
        )
        _grant_full_ingestion_access(c)
        result = c.write("Customer", "Acme", "name", '"Acme Corp"')
        if result.status.value != "applied":
            raise AssertionError(f"expected APPLIED, got {result!r}")
        record = c.read("Customer", "Acme")
    if record.fields["name"].value != "Acme Corp":
        raise AssertionError(f"read-back mismatch: {record.fields['name']!r}")


def test_read_with_explicit_resolver(routed_test_client: TestClient) -> None:
    """`read_with(RawResolverSpec)` returns UNRESOLVED with both candidates when conflicting writes exist."""
    with _admin_client(routed_test_client) as c:
        c.register_schema(
            [EntityTypeDef(name="Customer", fields=(FieldDef(name="name", type_str="str"),))]
        )
        _grant_full_ingestion_access(c)
        c.write("Customer", "Acme", "name", '"Acme Corp"')
        c.write("Customer", "Acme", "name", '"Acme Inc"')
        record = c.read_with("Customer", "Acme", RawResolverSpec())
    fv = record.fields["name"]
    if fv.status.value != "unresolved":
        raise AssertionError(f"expected unresolved with raw resolver, got {fv!r}")
    if len(fv.candidates) != 2:
        raise AssertionError(f"expected 2 candidates, got {len(fv.candidates)}")


# === Memory shortcut + Note.subject populate-on-write ===============================


def test_remember_populates_note_subject(routed_test_client: TestClient) -> None:
    """The 2026-05-03 cleanup: Note.subject is now KNOWN, not UNKNOWN."""
    with _admin_client(routed_test_client) as c:
        # Trigger Note auto-seed.
        c.list_schema()
        _grant_full_ingestion_access(c)
        result = c.remember(
            subject="ali",
            predicate="likes",
            object_value="tea",
            source_label="manual",
        )
        if result.status.value not in {"applied", "conflict_recorded"}:
            raise AssertionError(f"unexpected remember status: {result!r}")
        record = c.read("Note", "ali")

    fields = record.fields
    subject_fv = fields["subject"]
    if subject_fv.status.value != "known" or subject_fv.value != "ali":
        raise AssertionError(
            f"Note.subject should be populated from entity_key, got {subject_fv!r}"
        )
    if fields["predicate"].value != "likes":
        raise AssertionError(f"predicate mismatch: {fields['predicate']!r}")
    if fields["object_json"].value != "tea":
        raise AssertionError(f"object_json mismatch: {fields['object_json']!r}")


def test_remember_with_dict_object_value_roundtrips(routed_test_client: TestClient) -> None:
    """object_json stores any JSON-serializable value; one decode on read returns original."""
    with _admin_client(routed_test_client) as c:
        c.list_schema()
        _grant_full_ingestion_access(c)
        c.remember(subject="acme-deal", predicate="details", object_value={"size": 250000})
        record = c.read("Note", "acme-deal")
    obj = record.fields["object_json"].value
    if obj != {"size": 250000}:
        raise AssertionError(f"dict didn't roundtrip cleanly, got {obj!r}")


# === Ingest =========================================================================


def test_ingest_via_sdk_with_fake_llm(routed_test_client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.extraction_result = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Customer",
                key="Acme",
                fields=(ExtractedField(field_name="name", value_json='"Acme"'),),
            ),
        )
    )
    with _admin_client(routed_test_client) as c:
        c.register_schema(
            [EntityTypeDef(name="Customer", fields=(FieldDef(name="name", type_str="str"),))]
        )
        _grant_full_ingestion_access(c)
        result = c.ingest(content="Acme renewal at $250K.", label="call.md")
    if not result.get("entities"):
        raise AssertionError(f"expected entities in ingest result, got {result!r}")
