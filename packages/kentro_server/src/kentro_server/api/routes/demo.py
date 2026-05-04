"""Demo-only routes.

- `GET /demo/keys` returns the per-agent bearer tokens for the agent-switcher
  bootstrap (admin + demo-keys-opt-in gated).
- `POST /demo/seed` is the in-process equivalent of `kentro-server seed-demo`:
  registers the four demo schemas, applies the canonical 29-rule starting
  ruleset, and ingests every markdown in `examples/synthetic_corpus/`. Used by
  the UI's "seed demo" button on an empty tenant. Same gating as `/demo/keys`.

Both routes refuse to respond unless `KENTRO_ALLOW_DEMO_KEYS=true` is set —
the same opt-in that gates the boot guard. If you've rotated the keys for a
deployment these endpoints stay disabled regardless of role.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from kentro.schema import entity_type_def_from
from pydantic import BaseModel, ConfigDict

from kentro_server.api.auth import AdminPrincipalDep
from kentro_server.api.deps import SchemaRegistryDep, SettingsDep, TenantRegistryDep
from kentro_server.core.catalog import register_ingest_event
from kentro_server.core.rules import apply_ruleset
from kentro_server.demo import (
    AuditLog,
    Customer,
    Deal,
    Person,
    infer_source_class,
    initial_demo_ruleset,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoAgentKey(BaseModel):
    model_config = ConfigDict(frozen=True)
    agent_id: str
    api_key: str
    is_admin: bool
    display_name: str | None = None


class DemoKeysResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str
    agents: tuple[DemoAgentKey, ...] = ()


@router.get("/keys", response_model=DemoKeysResponse)
def get_demo_keys(
    principal: AdminPrincipalDep,
    settings: SettingsDep,
    registry: TenantRegistryDep,
) -> DemoKeysResponse:
    """Return every agent's bearer token for the principal's tenant.

    Admin-only AND opt-in-only. The combination is intentional — admin gates the
    operation against random non-admin agents, and the demo-keys opt-in gates
    the operation against any deployed instance with rotated keys.
    """
    if not settings.kentro_allow_demo_keys:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "/demo/keys is disabled when KENTRO_ALLOW_DEMO_KEYS is not set "
                "(this endpoint exists for the local-dev demo UI only)"
            ),
        )
    tenant_id = principal.store.tenant_id
    agents = tuple(
        DemoAgentKey(
            agent_id=acfg.id,
            api_key=acfg.api_key,
            is_admin=acfg.is_admin,
            display_name=acfg.display_name,
        )
        for acfg in registry.agents_for(tenant_id)
    )
    return DemoKeysResponse(tenant_id=tenant_id, agents=agents)


class DemoSeedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str
    schemas_registered: tuple[str, ...]
    rule_version: int
    rules_applied: int
    catalog_events_registered: int


def _ensure_opted_in(settings) -> None:
    if not settings.kentro_allow_demo_keys:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "/demo routes are disabled when KENTRO_ALLOW_DEMO_KEYS is not set "
                "(this endpoint exists for the local-dev demo UI only)"
            ),
        )


@router.post("/seed", response_model=DemoSeedResponse)
def seed_demo(
    principal: AdminPrincipalDep,
    settings: SettingsDep,
    schema: SchemaRegistryDep,
) -> DemoSeedResponse:
    """Register schemas + apply demo ruleset + register the corpus as catalog events.

    Schemas and rules are base infra (always live, never toggleable). The corpus
    is registered as `ingest_document` catalog entries with `active=False` — the
    viewer activates each one from the UI to drop documents into the world. First
    activation runs the LLM extraction; subsequent toggles are flag flips.

    Idempotent: re-running registers any missing entities and is a no-op for
    those already present.
    """
    _ensure_opted_in(settings)

    # 1. Register the four demo entity types (Customer, Person, Deal, AuditLog).
    type_defs = [
        entity_type_def_from(Customer),
        entity_type_def_from(Person),
        entity_type_def_from(Deal),
        entity_type_def_from(AuditLog),
    ]
    registered: list[str] = []
    for td in type_defs:
        schema.register(td)
        registered.append(td.name)

    # 2. Apply the canonical Scene-1 ruleset.
    ruleset = initial_demo_ruleset()
    rule_version = apply_ruleset(
        principal.store,
        rules=ruleset.rules,
        summary="initial demo ruleset (POST /demo/seed)",
    )

    # 3. Register every corpus markdown as an inactive catalog event. No
    # extraction runs here — the viewer activates events from the UI.
    # demo.py → routes → api → kentro_server → src → kentro_server → packages → repo
    # so parents[6] is the repo root.
    repo_root = Path(__file__).resolve().parents[6]
    corpus_dir = repo_root / "examples" / "synthetic_corpus"
    if not corpus_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"corpus dir not found: {corpus_dir}",
        )
    docs = sorted(corpus_dir.glob("*.md"))
    for catalog_order, path in enumerate(docs, start=1):
        register_ingest_event(
            principal.store,
            catalog_key=f"corpus:{path.name}",
            title=path.name,
            description=_describe_corpus_file(path.name),
            content=path.read_text(encoding="utf-8"),
            label=path.name,
            source_class=infer_source_class(path.name),
            catalog_order=catalog_order,
        )

    return DemoSeedResponse(
        tenant_id=principal.store.tenant_id,
        schemas_registered=tuple(registered),
        rule_version=rule_version,
        rules_applied=len(ruleset.rules),
        catalog_events_registered=len(docs),
    )


def _describe_corpus_file(filename: str) -> str | None:
    """Best-effort one-line description for the catalog UI.

    Hand-authored hints for the canonical corpus filenames; falls back to the
    inferred source class when the filename is unfamiliar.
    """
    descriptions = {
        "acme_call_2026-04-15.md": "Sales call transcript with Acme — initial $250K renewal.",
        "email_jane_2026-04-17.md": "Follow-up email from Jane after talking to finance — $300K.",
        "acme_ticket_142.md": "Customer support ticket against Acme.",
        "internal_slack_thread_2026-04-19.md": "Internal Slack thread about Acme renewal.",
        "ali_meeting_note_2026-03-10.md": "Meeting note from a 1:1 with the prospect.",
    }
    return descriptions.get(filename)
