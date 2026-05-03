"""HTTP route smoke tests via FastAPI's TestClient.

What's covered:
- Bearer auth: missing / wrong-scheme / unknown-key → 401
- /schema/register + /schema → round-trip through the SchemaRegistry
- /entities/{type}/{key}/{field} write + GET /entities/{type}/{key} read
- /memory/remember writes onto the auto-seeded Note entity
- /rules/apply + /rules/active round-trip
- /rules/parse: full path with a fake LLMClient (no real API calls)
- /documents: full path with a fake LLMClient returning canned ExtractionResult

The fake LLMClient is injected via FastAPI's `app.dependency_overrides`, which
replaces the cached LLM client at the dependency seam without touching app state.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kentro.types import EntityTypeDef, FieldDef, FieldReadRule, RuleSet, WriteRule
from kentro_server.api.deps import get_llm_client
from kentro_server.main import app
from kentro_server.settings import Settings
from kentro_server.skills.llm_client import (
    ExtractedEntity,
    ExtractedField,
    ExtractionResult,
    LLMClient,
    SkillResolverDecision,
    _NLIntentItem,
    _NLIntentList,
    _ParsedRule,
)


@dataclass
class _FakeLLM(LLMClient):
    """Multi-purpose fake — handlers script its responses per test."""

    extraction_result: ExtractionResult | None = None
    nl_intents: _NLIntentList = field(default_factory=lambda: _NLIntentList(intents=()))
    nl_rules: list[_ParsedRule] = field(default_factory=list)

    def run_skill_resolver(self, *, prompt, candidates, model=None):
        return SkillResolverDecision(chosen_value_json=None, reason="not under test")

    def extract_entities(
        self, *, document_text, registered_schemas, document_label=None, model=None
    ):
        return self.extraction_result or ExtractionResult(entities=())

    def identify_nl_intents(self, *, text, model=None):
        return self.nl_intents

    def parse_nl_rule(
        self,
        *,
        intent_description,
        intent_kind,
        registered_schemas,
        known_agent_ids,
        model=None,
    ):
        if not self.nl_rules:
            return _ParsedRule(rule_json=None, reason="fake LLM out of scripted rules")
        return self.nl_rules.pop(0)


_API_KEY = "test-ingestion-key"
_BAD_KEY = "this-key-does-not-exist"


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the lifespan at a tmp state dir + tenants.json with a known API key.

    The tenants.json carries one tenant `local` with one agent `ingestion_agent`
    whose api_key is `_API_KEY`. Tests authenticate with that key.
    """
    state_dir = tmp_path / "kentro_state"
    tenants_json = tmp_path / "tenants.json"
    tenants_json.write_text(
        f"""{{
          "tenants": [
            {{
              "id": "local",
              "display_name": "Local",
              "agents": [
                {{"id": "ingestion_agent", "api_key": "{_API_KEY}"}}
              ]
            }}
          ]
        }}""",
        encoding="utf-8",
    )
    monkeypatch.setenv("KENTRO_STATE_DIR", str(state_dir))
    monkeypatch.setenv("KENTRO_TENANTS_JSON", str(tenants_json))
    real = Settings()
    if not real.anthropic_api_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used-here")


@pytest.fixture
def fake_llm() -> _FakeLLM:
    return _FakeLLM()


@pytest.fixture
def client(isolated_state: None, fake_llm: _FakeLLM) -> Iterator[TestClient]:
    """TestClient with `get_llm_client` overridden to return `fake_llm`."""
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_llm_client, None)


def _auth() -> dict:
    return {"Authorization": f"Bearer {_API_KEY}"}


def _permissive_ruleset_body() -> dict:
    """ACL is default-deny, so tests that exercise reads/writes need to grant access first.

    We write one wildcard FieldReadRule per (Customer, Note) field-name we touch and
    one WriteRule per type. This keeps the auth surface honest — the Bearer key is
    *necessary* to make a request, but it's the ruleset that decides what each agent
    can do once authenticated.
    """
    rules = (
        WriteRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True),
        WriteRule(agent_id="ingestion_agent", entity_type="Note", allowed=True),
        FieldReadRule(
            agent_id="ingestion_agent", entity_type="Customer", field_name="name", allowed=True
        ),
        FieldReadRule(
            agent_id="ingestion_agent",
            entity_type="Customer",
            field_name="deal_size",
            allowed=True,
        ),
        FieldReadRule(
            agent_id="ingestion_agent",
            entity_type="Customer",
            field_name="contact",
            allowed=True,
        ),
        FieldReadRule(
            agent_id="ingestion_agent",
            entity_type="Customer",
            field_name="sales_notes",
            allowed=True,
        ),
        FieldReadRule(
            agent_id="ingestion_agent",
            entity_type="Customer",
            field_name="support_tickets",
            allowed=True,
        ),
        FieldReadRule(
            agent_id="ingestion_agent", entity_type="Note", field_name="subject", allowed=True
        ),
        FieldReadRule(
            agent_id="ingestion_agent", entity_type="Note", field_name="predicate", allowed=True
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
    )
    return {
        "ruleset": RuleSet(rules=rules, version=0).model_dump(mode="json"),
        "summary": "test setup: grant ingestion_agent broad access",
    }


def _grant_access(client: TestClient) -> None:
    r = client.post("/rules/apply", headers=_auth(), json=_permissive_ruleset_body())
    if r.status_code != 200:
        raise AssertionError(f"could not apply permissive ruleset: {r.status_code} {r.text}")


# === Auth ============================================================================


def test_route_without_bearer_returns_401(client: TestClient) -> None:
    r = client.get("/schema")
    if r.status_code != 401:
        raise AssertionError(f"missing bearer must return 401, got {r.status_code}: {r.text}")


def test_route_with_wrong_scheme_returns_401(client: TestClient) -> None:
    r = client.get("/schema", headers={"Authorization": f"Basic {_API_KEY}"})
    if r.status_code != 401:
        raise AssertionError(f"non-Bearer scheme must return 401, got {r.status_code}")


def test_route_with_unknown_key_returns_401(client: TestClient) -> None:
    r = client.get("/schema", headers={"Authorization": f"Bearer {_BAD_KEY}"})
    if r.status_code != 401:
        raise AssertionError(f"unknown key must return 401, got {r.status_code}: {r.text}")


# === Schema ==========================================================================


def test_schema_register_and_list_roundtrip(client: TestClient) -> None:
    body = {
        "type_defs": [
            EntityTypeDef(
                name="Customer", fields=(FieldDef(name="name", type_str="str"),)
            ).model_dump(mode="json")
        ]
    }
    r = client.post("/schema/register", headers=_auth(), json=body)
    if r.status_code != 200:
        raise AssertionError(f"register failed: {r.status_code} {r.text}")
    names = {td["name"] for td in r.json()["type_defs"]}
    if "Customer" not in names:
        raise AssertionError(f"Customer missing from registered types: {names}")
    if "Note" not in names:
        raise AssertionError("Note must be auto-seeded into the registry")

    r = client.get("/schema", headers=_auth())
    if r.status_code != 200:
        raise AssertionError(f"GET /schema failed: {r.status_code}")
    listed = {td["name"] for td in r.json()["type_defs"]}
    if listed != names:
        raise AssertionError(f"GET /schema differs from register response: {listed} != {names}")


# === Write + read ====================================================================


def test_write_then_read_roundtrips(client: TestClient) -> None:
    # Register Customer
    client.post(
        "/schema/register",
        headers=_auth(),
        json={
            "type_defs": [
                EntityTypeDef(
                    name="Customer",
                    fields=(
                        FieldDef(name="name", type_str="str"),
                        FieldDef(name="deal_size", type_str="float | None"),
                    ),
                ).model_dump(mode="json")
            ]
        },
    )
    _grant_access(client)

    # Write
    r = client.post(
        "/entities/Customer/Acme/name",
        headers=_auth(),
        json={"value_json": '"Acme Corp"'},
    )
    if r.status_code != 200 or r.json()["status"] != "applied":
        raise AssertionError(f"write should be APPLIED: {r.status_code} {r.text}")

    # Read
    r = client.get("/entities/Customer/Acme", headers=_auth())
    if r.status_code != 200:
        raise AssertionError(f"read failed: {r.status_code} {r.text}")
    record = r.json()
    if record["fields"]["name"]["value"] != "Acme Corp":
        raise AssertionError(f"read-back mismatch: {record['fields']['name']!r}")
    if record["fields"]["deal_size"]["status"] != "unknown":
        raise AssertionError(
            f"unwritten field should be UNKNOWN, got {record['fields']['deal_size']!r}"
        )


# === Memory / Note ===================================================================


def test_remember_writes_to_note_entity(client: TestClient) -> None:
    # Trigger Note auto-seed and grant access.
    client.get("/schema", headers=_auth())
    _grant_access(client)
    r = client.post(
        "/memory/remember",
        headers=_auth(),
        json={
            "subject": "demo-prep",
            "predicate": "scheduled_at",
            "object_json": "2026-05-10T14:00:00Z",
            "source_label": "kentro-cli",
        },
    )
    if r.status_code != 200:
        raise AssertionError(f"remember failed: {r.status_code} {r.text}")
    if r.json()["status"] not in {"applied", "conflict_recorded"}:
        raise AssertionError(f"unexpected status: {r.json()!r}")

    # Read it back via the Note entity (Note is auto-seeded; we never registered it).
    r = client.get("/entities/Note/demo-prep", headers=_auth())
    if r.status_code != 200:
        raise AssertionError(f"reading Note back failed: {r.status_code} {r.text}")
    fields = r.json()["fields"]
    if fields["predicate"]["value"] != "scheduled_at":
        raise AssertionError(f"predicate mismatch: {fields['predicate']!r}")
    if fields["source_label"]["value"] != "kentro-cli":
        raise AssertionError(f"source_label mismatch: {fields['source_label']!r}")


# === Rules ===========================================================================


def test_apply_rules_then_get_active(client: TestClient) -> None:
    # Need a registered schema for the rule to validate against later — but apply
    # itself doesn't validate, so this is just to keep the world coherent.
    client.post(
        "/schema/register",
        headers=_auth(),
        json={
            "type_defs": [
                EntityTypeDef(
                    name="Customer",
                    fields=(FieldDef(name="deal_size", type_str="float"),),
                ).model_dump(mode="json")
            ]
        },
    )
    rule = FieldReadRule(
        agent_id="ingestion_agent",
        entity_type="Customer",
        field_name="deal_size",
        allowed=False,
    )
    body = {
        "ruleset": RuleSet(rules=(rule,), version=0).model_dump(mode="json"),
        "summary": "redact deal_size from ingestion_agent",
    }
    r = client.post("/rules/apply", headers=_auth(), json=body)
    if r.status_code != 200:
        raise AssertionError(f"apply failed: {r.status_code} {r.text}")
    payload = r.json()
    if payload["version"] != 1 or payload["rules_applied"] != 1:
        raise AssertionError(f"unexpected apply payload: {payload!r}")

    r = client.get("/rules/active", headers=_auth())
    if r.status_code != 200:
        raise AssertionError(f"get active failed: {r.status_code}")
    active = r.json()
    if active["version"] != 1 or len(active["rules"]) != 1:
        raise AssertionError(f"unexpected active ruleset: {active!r}")


def test_rules_parse_via_fake_llm(client: TestClient, fake_llm: _FakeLLM) -> None:
    # Register a schema so the orchestrator's validation passes.
    client.post(
        "/schema/register",
        headers=_auth(),
        json={
            "type_defs": [
                EntityTypeDef(
                    name="Customer",
                    fields=(FieldDef(name="deal_size", type_str="float"),),
                ).model_dump(mode="json")
            ]
        },
    )

    fake_llm.nl_intents = _NLIntentList(
        intents=(_NLIntentItem(kind="field_read", description="redact deal_size from ingestion"),)
    )
    fake_llm.nl_rules = [
        _ParsedRule(
            rule_json=FieldReadRule(
                agent_id="ingestion_agent",
                entity_type="Customer",
                field_name="deal_size",
                allowed=False,
            ).model_dump_json(),
            reason="ok",
        )
    ]

    r = client.post(
        "/rules/parse",
        headers=_auth(),
        json={"text": "redact deal_size from ingestion"},
    )
    if r.status_code != 200:
        raise AssertionError(f"parse failed: {r.status_code} {r.text}")
    body = r.json()
    if len(body["parsed_ruleset"]["rules"]) != 1:
        raise AssertionError(f"expected 1 parsed rule, got {body!r}")


# === Documents =======================================================================


def test_ingest_document_via_fake_llm(client: TestClient, fake_llm: _FakeLLM) -> None:
    client.post(
        "/schema/register",
        headers=_auth(),
        json={
            "type_defs": [
                EntityTypeDef(
                    name="Customer",
                    fields=(
                        FieldDef(name="name", type_str="str"),
                        FieldDef(name="deal_size", type_str="float | None"),
                    ),
                ).model_dump(mode="json")
            ]
        },
    )
    _grant_access(client)
    fake_llm.extraction_result = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Customer",
                key="Acme",
                fields=(
                    ExtractedField(field_name="name", value_json='"Acme"'),
                    ExtractedField(field_name="deal_size", value_json="250000"),
                ),
            ),
        )
    )
    r = client.post(
        "/documents",
        headers=_auth(),
        json={"content": "Acme renewal at $250K. Talked to Jane.", "label": "call.md"},
    )
    if r.status_code != 200:
        raise AssertionError(f"ingest failed: {r.status_code} {r.text}")
    result = r.json()
    if not result["entities"]:
        raise AssertionError(f"ingest should produce entities: {result!r}")
