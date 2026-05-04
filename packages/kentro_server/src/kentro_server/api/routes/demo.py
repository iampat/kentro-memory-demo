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
from kentro_server.api.deps import LLMClientDep, SchemaRegistryDep, SettingsDep, TenantRegistryDep
from kentro_server.core.rules import apply_ruleset
from kentro_server.demo import (
    AuditLog,
    Customer,
    Deal,
    Person,
    infer_source_class,
    initial_demo_ruleset,
)
from kentro_server.extraction import ingest_document

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
    documents_ingested: int


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
    llm: LLMClientDep,
) -> DemoSeedResponse:
    """Register schemas + apply demo ruleset + ingest the corpus, all server-side.

    Used by the UI's "Seed demo data" button on an empty tenant. Equivalent to
    running the `kentro-server seed-demo` CLI but in-process — no second curl
    loop, no duplicate Anthropic-key wiring.

    Idempotent: re-registering existing schemas is a no-op for unchanged defs;
    re-ingesting the same blob produces a new document row but the field writes
    are corroboration on top of the prior ones.
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

    # 3. Ingest every markdown file in examples/synthetic_corpus/.
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
    for path in docs:
        ingest_document(
            store=principal.store,
            llm=llm,
            content=path.read_text(encoding="utf-8").encode("utf-8"),
            label=path.name,
            registered_schemas=schema.list_all(),
            written_by_agent_id=principal.agent_id,
            rule_version=rule_version,
            smart_model=settings.kentro_llm_smart_model,
            source_class=infer_source_class(path.name),
        )

    return DemoSeedResponse(
        tenant_id=principal.store.tenant_id,
        schemas_registered=tuple(registered),
        rule_version=rule_version,
        rules_applied=len(ruleset.rules),
        documents_ingested=len(docs),
    )
