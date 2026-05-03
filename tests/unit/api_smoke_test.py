"""HTTP route smoke tests via FastAPI's TestClient.

What's covered:
- Bearer auth: missing / wrong-scheme / unknown-key → 401
- /schema/register: admin-only (403 for non-admin)
- /entities/{type}/{key}/{field} write + GET /entities/{type}/{key} read
- /memory/remember: writes onto the auto-seeded Note entity, with object_json
  roundtrip-correctness (the previous implementation double-encoded; fixed)
- /rules/apply: admin-only (403 for non-admin); non-admin cannot self-grant
- /rules/parse: full path with a fake LLMClient (no real API calls)
- /documents POST + DELETE: ingest succeeds with fake LLM; DELETE is admin-only
- read_entity with EntityVisibilityRule denial → all fields HIDDEN
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from kentro.types import (
    EntityTypeDef,
    EntityVisibilityRule,
    FieldDef,
    FieldReadRule,
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
    ParsedRule,
)

from tests.unit._helpers import ADMIN_KEY, AGENT_KEY, FakeLLM


@pytest.fixture
def client(isolated_state: None, fake_llm: FakeLLM) -> Iterator[TestClient]:
    """TestClient with `get_llm_client` overridden to return `fake_llm`.

    `isolated_state` and `fake_llm` are conftest fixtures: tmp tenants.json
    with an admin + non-admin agent, and a scriptable LLM stub.
    """
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_llm_client, None)


def _admin() -> dict:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def _agent() -> dict:
    return {"Authorization": f"Bearer {AGENT_KEY}"}


def _grant_access_for_ingestion(client: TestClient) -> None:
    """Apply a permissive ruleset for ingestion_agent so non-control-plane writes succeed.

    Entity visibility is default-deny (least privilege, like field reads), so we
    grant it here for the entity types these tests touch.
    """
    rules = (
        EntityVisibilityRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True),
        EntityVisibilityRule(agent_id="ingestion_agent", entity_type="Note", allowed=True),
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
            field_name="confidence",
            allowed=True,
        ),
        FieldReadRule(
            agent_id="ingestion_agent",
            entity_type="Note",
            field_name="source_label",
            allowed=True,
        ),
    )
    body = {
        "ruleset": RuleSet(rules=rules, version=0).model_dump(mode="json"),
        "summary": "test setup: grant ingestion_agent broad access",
    }
    r = client.post("/rules/apply", headers=_admin(), json=body)
    if r.status_code != 200:
        raise AssertionError(f"could not apply permissive ruleset: {r.status_code} {r.text}")


# === Auth ============================================================================


def test_route_without_bearer_returns_401(client: TestClient) -> None:
    r = client.get("/schema")
    if r.status_code != 401:
        raise AssertionError(f"missing bearer must return 401, got {r.status_code}: {r.text}")


def test_route_with_wrong_scheme_returns_401(client: TestClient) -> None:
    r = client.get("/schema", headers={"Authorization": f"Basic {ADMIN_KEY}"})
    if r.status_code != 401:
        raise AssertionError(f"non-Bearer scheme must return 401, got {r.status_code}")


def test_route_with_unknown_key_returns_401(client: TestClient) -> None:
    r = client.get("/schema", headers={"Authorization": "Bearer not-a-real-key"})
    if r.status_code != 401:
        raise AssertionError(f"unknown key must return 401, got {r.status_code}: {r.text}")


# === Admin gate ======================================================================


def test_non_admin_cannot_apply_rules(client: TestClient) -> None:
    """The Critical-#1 regression: a non-admin agent must NOT be able to mutate the ruleset."""
    body = {
        "ruleset": RuleSet(
            rules=(
                FieldReadRule(
                    agent_id="sales", entity_type="Customer", field_name="name", allowed=True
                ),
            ),
            version=0,
        ).model_dump(mode="json"),
        "summary": "sales attempts to self-grant",
    }
    r = client.post("/rules/apply", headers=_agent(), json=body)
    if r.status_code != 403:
        raise AssertionError(f"non-admin /rules/apply must be 403, got {r.status_code}: {r.text}")


def test_non_admin_cannot_register_schema(client: TestClient) -> None:
    """Schema is part of the trust boundary; non-admin must be denied."""
    body = {
        "type_defs": [
            EntityTypeDef(
                name="Customer", fields=(FieldDef(name="name", type_str="str"),)
            ).model_dump(mode="json")
        ]
    }
    r = client.post("/schema/register", headers=_agent(), json=body)
    if r.status_code != 403:
        raise AssertionError(
            f"non-admin /schema/register must be 403, got {r.status_code}: {r.text}"
        )


def test_non_admin_cannot_delete_documents(client: TestClient) -> None:
    """Source removal is destructive; non-admin must be denied."""
    r = client.delete(f"/documents/{uuid4()}", headers=_agent())
    if r.status_code != 403:
        raise AssertionError(f"non-admin DELETE /documents must be 403, got {r.status_code}")


def test_admin_can_apply_rules(client: TestClient) -> None:
    body = {
        "ruleset": RuleSet(rules=(), version=0).model_dump(mode="json"),
        "summary": "admin baseline",
    }
    r = client.post("/rules/apply", headers=_admin(), json=body)
    if r.status_code != 200:
        raise AssertionError(f"admin /rules/apply should succeed, got {r.status_code}: {r.text}")


# === Schema ==========================================================================


def test_schema_register_and_list_roundtrip(client: TestClient) -> None:
    body = {
        "type_defs": [
            EntityTypeDef(
                name="Customer", fields=(FieldDef(name="name", type_str="str"),)
            ).model_dump(mode="json")
        ]
    }
    r = client.post("/schema/register", headers=_admin(), json=body)
    if r.status_code != 200:
        raise AssertionError(f"register failed: {r.status_code} {r.text}")
    names = {td["name"] for td in r.json()["type_defs"]}
    if "Customer" not in names:
        raise AssertionError(f"Customer missing from registered types: {names}")
    if "Note" not in names:
        raise AssertionError("Note must be auto-seeded into the registry")

    # GET /schema is read-only — non-admin can list.
    r = client.get("/schema", headers=_agent())
    if r.status_code != 200:
        raise AssertionError(f"GET /schema (as agent) failed: {r.status_code}")


# === Write + read ====================================================================


def test_write_then_read_roundtrips(client: TestClient) -> None:
    client.post(
        "/schema/register",
        headers=_admin(),
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
    _grant_access_for_ingestion(client)

    r = client.post(
        "/entities/Customer/Acme/name",
        headers=_admin(),
        json={"value_json": '"Acme Corp"'},
    )
    if r.status_code != 200 or r.json()["status"] != "applied":
        raise AssertionError(f"write should be APPLIED: {r.status_code} {r.text}")

    r = client.get("/entities/Customer/Acme", headers=_admin())
    if r.status_code != 200:
        raise AssertionError(f"read failed: {r.status_code} {r.text}")
    record = r.json()
    if record["fields"]["name"]["value"] != "Acme Corp":
        raise AssertionError(f"read-back mismatch: {record['fields']['name']!r}")
    if record["fields"]["deal_size"]["status"] != "unknown":
        raise AssertionError(
            f"unwritten field should be UNKNOWN, got {record['fields']['deal_size']!r}"
        )


def test_list_entities_returns_only_visible_keys(client: TestClient) -> None:
    """`GET /entities/{type}` lists keys of that type, filtered by EntityVisibilityRule.

    Setup: register Customer, write two rows (Acme, Globex). Apply a ruleset
    that hides Globex from `sales` but allows Acme. Asserts:
      - admin sees both
      - sales sees only Acme
    """
    client.post(
        "/schema/register",
        headers=_admin(),
        json={
            "type_defs": [
                EntityTypeDef(
                    name="Customer",
                    fields=(FieldDef(name="name", type_str="str"),),
                ).model_dump(mode="json")
            ]
        },
    )
    # Permissive ruleset for ingestion + visibility on Acme for sales but not Globex.
    rules = (
        EntityVisibilityRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True),
        WriteRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True),
        FieldReadRule(
            agent_id="ingestion_agent", entity_type="Customer", field_name="name", allowed=True
        ),
        EntityVisibilityRule(
            agent_id="sales", entity_type="Customer", entity_key="Acme", allowed=True
        ),
        EntityVisibilityRule(
            agent_id="sales", entity_type="Customer", entity_key="Globex", allowed=False
        ),
        FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True),
    )
    client.post(
        "/rules/apply",
        headers=_admin(),
        json={"ruleset": RuleSet(rules=rules).model_dump(mode="json")},
    )
    for key in ("Acme", "Globex"):
        client.post(
            f"/entities/Customer/{key}/name",
            headers=_admin(),
            json={"value_json": f'"{key}"'},
        )

    admin_view = client.get("/entities/Customer", headers=_admin()).json()
    if admin_view["entity_type"] != "Customer" or len(admin_view["entities"]) != 2:
        raise AssertionError(f"admin should see both keys, got {admin_view!r}")
    keys_admin = {e["key"] for e in admin_view["entities"]}
    if keys_admin != {"Acme", "Globex"}:
        raise AssertionError(f"admin keys mismatch: {keys_admin}")

    sales_view = client.get("/entities/Customer", headers=_agent()).json()
    sales_keys = {e["key"] for e in sales_view["entities"]}
    if sales_keys != {"Acme"}:
        raise AssertionError(f"sales should see only Acme (Globex hidden), got {sales_keys}")


def test_list_documents_after_ingest(client: TestClient, fake_llm: FakeLLM) -> None:
    """`GET /documents` returns ingested sources + field_write_count > 0 for each."""
    fake_llm.extraction_result = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Customer",
                key="Acme",
                fields=(ExtractedField(field_name="name", value_json='"Acme Corp"'),),
            ),
        )
    )
    client.post(
        "/schema/register",
        headers=_admin(),
        json={
            "type_defs": [
                EntityTypeDef(
                    name="Customer",
                    fields=(FieldDef(name="name", type_str="str"),),
                ).model_dump(mode="json")
            ]
        },
    )
    _grant_access_for_ingestion(client)
    r = client.post(
        "/documents",
        headers=_admin(),
        json={"content": "Acme update", "label": "doc1.md", "source_class": "email"},
    )
    if r.status_code != 200:
        raise AssertionError(f"ingest failed: {r.status_code} {r.text}")

    listing = client.get("/documents", headers=_admin()).json()
    if len(listing["documents"]) != 1:
        raise AssertionError(f"expected 1 doc, got {listing!r}")
    doc = listing["documents"][0]
    if doc["label"] != "doc1.md" or doc["source_class"] != "email":
        raise AssertionError(f"label/source_class mismatch: {doc!r}")
    if doc["field_write_count"] < 1:
        raise AssertionError(f"expected at least 1 field write per ingested doc, got {doc!r}")


def test_demo_keys_returns_all_agents_when_opted_in(client: TestClient) -> None:
    """`GET /demo/keys` returns the per-agent bearer tokens; admin-only + opt-in.

    Test fixture sets `KENTRO_ALLOW_DEMO_KEYS=true` via the parent conftest's
    isolated_state. Non-admin call → 403; missing opt-in → 404.
    """
    r = client.get("/demo/keys", headers=_admin())
    if r.status_code != 200:
        raise AssertionError(f"admin /demo/keys should be 200, got {r.status_code}: {r.text}")
    body = r.json()
    if body["tenant_id"] != "local":
        raise AssertionError(f"tenant_id should be 'local', got {body!r}")
    agent_ids = {a["agent_id"] for a in body["agents"]}
    if "ingestion_agent" not in agent_ids or "sales" not in agent_ids:
        raise AssertionError(f"expected admin+sales agents, got {agent_ids}")

    # Non-admin → 403.
    r = client.get("/demo/keys", headers=_agent())
    if r.status_code != 403:
        raise AssertionError(f"non-admin should be 403, got {r.status_code}")


def test_demo_keys_404_without_opt_in(
    isolated_state: None,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without KENTRO_ALLOW_DEMO_KEYS, /demo/keys returns 404 even for an admin.

    Constructs a fresh TestClient INSIDE the test body so the lifespan re-reads
    the (now-unset) env var. The conftest's `isolated_state` already set it,
    so we delenv before constructing the client.
    """
    monkeypatch.delenv("KENTRO_ALLOW_DEMO_KEYS", raising=False)
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    try:
        with TestClient(app) as c:
            r = c.get("/demo/keys", headers=_admin())
            if r.status_code != 404:
                raise AssertionError(
                    f"/demo/keys without opt-in should be 404, got {r.status_code}: {r.text}"
                )
    finally:
        app.dependency_overrides.pop(get_llm_client, None)


def test_entity_visibility_denial_hides_all_fields(client: TestClient) -> None:
    """A denying EntityVisibilityRule must HIDE every declared field on the entity."""
    client.post(
        "/schema/register",
        headers=_admin(),
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
    rules = (
        FieldReadRule(
            agent_id="ingestion_agent", entity_type="Customer", field_name="name", allowed=True
        ),
        FieldReadRule(
            agent_id="ingestion_agent",
            entity_type="Customer",
            field_name="deal_size",
            allowed=True,
        ),
        EntityVisibilityRule(
            agent_id="ingestion_agent",
            entity_type="Customer",
            entity_key="Acme",
            allowed=False,
        ),
        WriteRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True),
    )
    client.post(
        "/rules/apply",
        headers=_admin(),
        json={
            "ruleset": RuleSet(rules=rules, version=0).model_dump(mode="json"),
            "summary": "deny visibility on Customer:Acme",
        },
    )
    client.post(
        "/entities/Customer/Acme/name", headers=_admin(), json={"value_json": '"Acme Corp"'}
    )
    r = client.get("/entities/Customer/Acme", headers=_admin())
    fields = r.json()["fields"]
    for fname, fv in fields.items():
        if fv["status"] != "hidden":
            raise AssertionError(
                f"with visibility denied, field {fname!r} should be HIDDEN, got {fv!r}"
            )


# === Memory / Note ===================================================================


def test_remember_writes_to_note_entity(client: TestClient) -> None:
    client.get("/schema", headers=_admin())  # auto-seed Note
    _grant_access_for_ingestion(client)
    r = client.post(
        "/memory/remember",
        headers=_admin(),
        json={
            "subject": "demo-prep",
            "predicate": "scheduled_at",
            "object_json": "2026-05-10T14:00:00Z",
            "source_label": "kentro-cli",
        },
    )
    if r.status_code != 200:
        raise AssertionError(f"remember failed: {r.status_code} {r.text}")

    r = client.get("/entities/Note/demo-prep", headers=_admin())
    fields = r.json()["fields"]
    if fields["predicate"]["value"] != "scheduled_at":
        raise AssertionError(f"predicate mismatch: {fields['predicate']!r}")
    # Subject is now populated on write (post-2026-05-03 cleanup) — must equal
    # the entity_key, not be UNKNOWN like it was historically.
    if fields["subject"]["status"] != "known" or fields["subject"]["value"] != "demo-prep":
        raise AssertionError(
            f"subject should be populated from entity_key, got {fields['subject']!r}"
        )
    # Roundtrip correctness: stored as canonical JSON, decoded once on read.
    if fields["object_json"]["value"] != "2026-05-10T14:00:00Z":
        raise AssertionError(
            f"object_json should roundtrip to original value, got {fields['object_json']!r}"
        )
    if fields["source_label"]["value"] != "kentro-cli":
        raise AssertionError(f"source_label mismatch: {fields['source_label']!r}")


def test_remember_object_json_handles_non_string_values(client: TestClient) -> None:
    """The High-#5 regression: non-string object_json must roundtrip without
    leaving an opaque double-encoded string on read."""
    client.get("/schema", headers=_admin())
    _grant_access_for_ingestion(client)
    r = client.post(
        "/memory/remember",
        headers=_admin(),
        json={
            "subject": "acme-deal",
            "predicate": "details",
            "object_json": {"deal_size": 250000, "status": "open"},
        },
    )
    if r.status_code != 200:
        raise AssertionError(f"remember failed: {r.status_code} {r.text}")
    r = client.get("/entities/Note/acme-deal", headers=_admin())
    obj = r.json()["fields"]["object_json"]["value"]
    if obj != {"deal_size": 250000, "status": "open"}:
        raise AssertionError(
            f"non-string object_json should roundtrip as the original dict, got {obj!r}"
        )


def test_remember_returns_permission_denied_before_writing(client: TestClient) -> None:
    """The Low-#15 regression: ACL is checked once before issuing per-field writes."""
    client.get("/schema", headers=_admin())
    # No permissive ruleset applied → admin agent has no Note write permission either
    # (admin role gates control-plane routes; ACL still gates data-plane writes).
    r = client.post(
        "/memory/remember",
        headers=_admin(),
        json={"subject": "nope", "predicate": "x", "object_json": "y"},
    )
    if r.status_code != 200:
        raise AssertionError(f"remember should return 200 with PD payload: {r.status_code}")
    if r.json()["status"] != "permission_denied":
        raise AssertionError(f"expected permission_denied, got {r.json()!r}")


def test_remember_atomic_no_partial_writes_on_field_denial(client: TestClient) -> None:
    """Codex 2026-05-03 high finding: a per-field denial mid-loop must NOT persist
    the fields that were written before the denial.

    Setup: grant write on Note (so the wildcard write check passes) BUT explicitly
    DENY write on Note.object_json. The previous loop-and-commit-each implementation
    would persist `subject` and `predicate`, then PD on `object_json`, leaving a
    half-written Note that read as real state. The new `write_fields_bulk` short-
    circuits up-front: zero writes, zero entity rows.
    """
    _grant_access_for_ingestion(client)
    # Tighten: deny object_json write specifically.
    client.post(
        "/rules/apply",
        headers=_admin(),
        json={
            "ruleset": RuleSet(
                rules=(
                    EntityVisibilityRule(
                        agent_id="ingestion_agent", entity_type="Note", allowed=True
                    ),
                    WriteRule(agent_id="ingestion_agent", entity_type="Note", allowed=True),
                    WriteRule(
                        agent_id="ingestion_agent",
                        entity_type="Note",
                        field_name="object_json",
                        allowed=False,
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
                )
            ).model_dump(mode="json")
        },
    )

    r = client.post(
        "/memory/remember",
        headers=_admin(),
        json={"subject": "atomic_test", "predicate": "is", "object_json": "denied"},
    )
    if r.status_code != 200 or r.json()["status"] != "permission_denied":
        raise AssertionError(f"expected PD response, got {r.status_code}: {r.json()!r}")

    # No FIELD WRITES must have landed — atomicity means subject/predicate are NOT
    # stored even though they pre-pass ACL on their own. (The route returns a synthetic
    # EntityRecord for any (type, key) regardless of whether the entity row exists, so
    # we check field statuses rather than the response code.)
    read = client.get("/entities/Note/atomic_test", headers=_admin())
    if read.status_code != 200:
        raise AssertionError(
            f"GET /entities/Note/atomic_test should be 200, got {read.status_code}"
        )
    fields_seen = read.json()["fields"]
    for fname in ("subject", "predicate", "object_json"):
        f = fields_seen.get(fname)
        if f is None or f.get("status") != "unknown":
            raise AssertionError(
                f"atomic remember failure must leave field {fname!r} as 'unknown' "
                f"(no value written); got {f!r}"
            )


# === Rules ===========================================================================


def test_apply_rules_then_get_active(client: TestClient) -> None:
    client.post(
        "/schema/register",
        headers=_admin(),
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
    r = client.post("/rules/apply", headers=_admin(), json=body)
    if r.status_code != 200:
        raise AssertionError(f"apply failed: {r.status_code} {r.text}")
    payload = r.json()
    if payload["version"] != 1 or payload["rules_applied"] != 1:
        raise AssertionError(f"unexpected apply payload: {payload!r}")

    r = client.get("/rules/active", headers=_admin())
    if r.status_code != 200:
        raise AssertionError(f"get active failed: {r.status_code}")
    active = r.json()
    if active["version"] != 1 or len(active["rules"]) != 1:
        raise AssertionError(f"unexpected active ruleset: {active!r}")


def test_rules_parse_via_fake_llm(client: TestClient, fake_llm: FakeLLM) -> None:
    client.post(
        "/schema/register",
        headers=_admin(),
        json={
            "type_defs": [
                EntityTypeDef(
                    name="Customer",
                    fields=(FieldDef(name="deal_size", type_str="float"),),
                ).model_dump(mode="json")
            ]
        },
    )
    fake_llm.nl_intents = NLIntentList(
        intents=(NLIntentItem(kind="field_read", description="redact deal_size from ingestion"),)
    )
    fake_llm.nl_rules = [
        ParsedRule(
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
        headers=_admin(),
        json={"text": "redact deal_size from ingestion"},
    )
    if r.status_code != 200:
        raise AssertionError(f"parse failed: {r.status_code} {r.text}")
    body = r.json()
    if len(body["parsed_ruleset"]["rules"]) != 1:
        raise AssertionError(f"expected 1 parsed rule, got {body!r}")


# === Documents =======================================================================


def test_ingest_document_via_fake_llm(client: TestClient, fake_llm: FakeLLM) -> None:
    client.post(
        "/schema/register",
        headers=_admin(),
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
    _grant_access_for_ingestion(client)
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
        headers=_admin(),
        json={"content": "Acme renewal at $250K. Talked to Jane.", "label": "call.md"},
    )
    if r.status_code != 200:
        raise AssertionError(f"ingest failed: {r.status_code} {r.text}")
    if not r.json()["entities"]:
        raise AssertionError(f"ingest should produce entities: {r.json()!r}")
