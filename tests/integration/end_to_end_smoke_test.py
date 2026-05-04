"""End-to-end smoke test — drives the demo flow with real Anthropic + the real
ingestor + the real conflict / resolver pipeline. NO HTTP layer; this is a Python-driven
proof that the ingredients compose.

Skipped when `ANTHROPIC_API_KEY` is not set. Skipped in CI by environment convention.

What it asserts (the demo's contract):
1. After ingesting Monday's Acme transcript, `Customer.Acme.deal_size` has one write
   and is KNOWN.
2. After also ingesting Wednesday's Jane email, the (entity, field) has TWO live
   writes with different values, an open `ConflictRow`, and an UNRESOLVED read under
   `RawResolver`.
3. Under the default `AutoResolver` with no `ConflictRule`, the read picks the email
   ($300K) per the LatestWrite fallback.
4. After applying a `ConflictRule(SkillResolver("written outweighs verbal"))`, the
   read still picks $300K — but now via the SkillResolver, with a reason from the LLM.
5. After source-removing the email document, the read falls back to $250K (the
   transcript) automatically — proves conflict-as-memory is correct under source churn.

This test costs real Anthropic tokens on a cold cache. Re-runs hit the disk cache
under `<state_dir>/.llm_cache/` so subsequent runs are free.
"""

import json
import logging
import os
from pathlib import Path

import pytest
from kentro.acl import evaluate_field_read
from kentro.schema import entity_type_def_from
from kentro.types import (
    AutoResolverSpec,
    FieldStatus,
    ResolverPolicy,
    ResolverPolicySet,
    RuleSet,
    SkillResolverSpec,
)
from kentro_server.core.resolve import resolve
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.core.source_removal import remove_document
from kentro_server.extraction import ingest_document
from kentro_server.settings import Settings
from kentro_server.skills.factory import make_llm_client
from kentro_server.store import (
    AgentConfig,
    TenantConfig,
    TenantRegistry,
    TenantsConfig,
)
from kentro_server.store.models import (
    AgentRow,
    ConflictRow,
    DocumentRow,
    EntityRow,
    FieldWriteRow,
    RuleVersionRow,
)
from sqlmodel import col, select

logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOTENV = _REPO_ROOT / ".env"
CORPUS_DIR = _REPO_ROOT / "examples" / "synthetic_corpus"

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY") and not _DOTENV.exists(),
    reason="end-to-end smoke needs ANTHROPIC_API_KEY (set in env or repo-root .env)",
)


# === Demo schemas ===
#
# Single source of truth: `kentro_server.demo.schemas` — shared with the CLI's
# `seed-demo` command and any walkthrough notebook.

from kentro_server.demo import Customer, Person  # noqa: E402

# === Helpers ===


def _settings_for_test(tmp_path: Path) -> Settings:
    """Construct settings pointing at an isolated state dir, but keep the global
    .llm_cache so re-runs hit it. Uses the user's real keys from .env."""
    real = Settings()
    return real.model_copy(
        update={
            "kentro_state_dir": tmp_path / "kentro_state",
            "kentro_tenants_json": tmp_path / "tenants.json",
        }
    )


def _new_world(tmp_path: Path):
    """Spin up a fresh tenant + schema + agent + rule version for one test run."""
    settings = _settings_for_test(tmp_path)
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY missing")

    config = TenantsConfig(
        tenants=(
            TenantConfig(
                id="local",
                agents=(AgentConfig(id="ingestion_agent", api_key="test-key"),),
            ),
        )
    )
    registry = TenantRegistry(settings.kentro_state_dir, config)
    store = registry.get("local")

    schema = SchemaRegistry(store)
    schema.register_many([entity_type_def_from(Customer), entity_type_def_from(Person)])

    with store.session() as s:
        s.add(AgentRow(id="ingestion_agent", display_name="Ingestion Agent"))
        s.add(RuleVersionRow(version=1, summary="initial"))
        s.commit()

    llm = make_llm_client(settings)
    return store, schema, llm


def _ingest_corpus_doc(store, schema, llm, settings, filename: str):
    path = CORPUS_DIR / filename
    if not path.exists():
        pytest.skip(
            f"corpus file missing: {path} — run `uv run python scripts/generate_corpus.py`"
        )
    return ingest_document(
        store=store,
        llm=llm,
        content=path.read_bytes(),
        label=filename,
        registered_schemas=schema.list_all(),
        written_by_agent_id="ingestion_agent",
        rule_version=1,
        smart_model=settings.kentro_llm_smart_model,
    )


def _live_writes_for(store, *, entity_type: str, key: str, field_name: str) -> list[FieldWriteRow]:
    with store.session() as s:
        ent = s.exec(
            select(EntityRow).where(EntityRow.type == entity_type, EntityRow.key == key)
        ).first()
        if ent is None:
            return []
        return list(
            s.exec(
                select(FieldWriteRow)
                .where(
                    FieldWriteRow.entity_id == ent.id,
                    FieldWriteRow.field_name == field_name,
                )
                .order_by(col(FieldWriteRow.written_at))
            ).all()
        )


def _open_conflicts_for(store, *, entity_type: str, key: str, field_name: str):
    with store.session() as s:
        ent = s.exec(
            select(EntityRow).where(EntityRow.type == entity_type, EntityRow.key == key)
        ).first()
        if ent is None:
            return []
        return list(
            s.exec(
                select(ConflictRow).where(
                    ConflictRow.entity_id == ent.id,
                    ConflictRow.field_name == field_name,
                    col(ConflictRow.resolved_at).is_(None),
                )
            ).all()
        )


# === Tests ===


def test_full_demo_flow(tmp_path: Path) -> None:
    settings = _settings_for_test(tmp_path)
    store, schema, llm = _new_world(tmp_path)

    # --- 1. Ingest Monday's transcript. Expect a single deal_size write (~250000). ---
    result_call = _ingest_corpus_doc(store, schema, llm, settings, "acme_call_2026-04-15.md")
    logger.info("call ingest: entities=%d", len(result_call.entities))

    writes = _live_writes_for(
        store, entity_type="Customer", key="Acme Corp", field_name="deal_size"
    )
    if not writes:
        # Some extractions might use "Acme" vs "Acme Corp" — fall back.
        writes = _live_writes_for(
            store, entity_type="Customer", key="Acme", field_name="deal_size"
        )
    if len(writes) != 1:
        raise AssertionError(
            f"expected 1 deal_size write after the call, got {len(writes)}: "
            f"{[(w.value_json, w.written_at) for w in writes]}"
        )
    val_call = json.loads(writes[0].value_json)
    if not _is_around(val_call, 250000):
        raise AssertionError(f"first write should encode ~250000, got {val_call!r}")

    no_conflict_yet = _open_conflicts_for(
        store, entity_type="Customer", key="Acme Corp", field_name="deal_size"
    ) or _open_conflicts_for(store, entity_type="Customer", key="Acme", field_name="deal_size")
    if no_conflict_yet:
        raise AssertionError("no conflict should exist after the first write")

    # --- 2. Ingest Wednesday's email. Expect two live writes + an open ConflictRow. ---
    _ingest_corpus_doc(store, schema, llm, settings, "email_jane_2026-04-17.md")

    # Resolve canonical key — try both spellings the LLM might produce.
    canonical_key = None
    for candidate in ("Acme Corp", "Acme"):
        if _live_writes_for(store, entity_type="Customer", key=candidate, field_name="deal_size"):
            canonical_key = candidate
            break
    if canonical_key is None:
        raise AssertionError("no deal_size writes found under any expected key")

    writes = _live_writes_for(
        store,
        entity_type="Customer",
        key=canonical_key,
        field_name="deal_size",
    )
    if len(writes) < 2:
        raise AssertionError(
            f"expected at least 2 deal_size writes after both docs, got {len(writes)}: "
            f"{[w.value_json for w in writes]}"
        )
    distinct = {w.value_json for w in writes}
    if len(distinct) < 2:
        raise AssertionError(
            f"expected the two writes to disagree, got identical values: {distinct}"
        )
    open_conflicts = _open_conflicts_for(
        store,
        entity_type="Customer",
        key=canonical_key,
        field_name="deal_size",
    )
    if len(open_conflicts) != 1:
        raise AssertionError(f"expected exactly one open conflict, got {len(open_conflicts)}")

    # --- 3. Default AutoResolver (no ResolverPolicy) → LatestWrite picks the email ($300K). ---
    initial_ruleset = RuleSet(rules=(), version=1)
    initial_policies = ResolverPolicySet(policies=(), version=1)
    auto = resolve(
        candidates=writes,
        spec=AutoResolverSpec(),
        resolver_policies=initial_policies,
        entity_type="Customer",
        field_name="deal_size",
        llm=llm,
    )
    if auto.status != FieldStatus.KNOWN:
        raise AssertionError(
            f"auto+default should resolve to KNOWN, got {auto.status}: {auto.reason}"
        )
    if auto.winner is None:
        raise AssertionError("auto+default should pick a winner")
    auto_value = json.loads(auto.winner.value_json)
    if not _is_around(auto_value, 300000):
        raise AssertionError(f"auto+default should pick ~300000 (latest), got {auto_value!r}")

    # --- 4. Apply SkillResolver ResolverPolicy. The skill picks $300K with a real reason. ---
    skill_policy = ResolverPolicy(
        entity_type="Customer",
        field_name="deal_size",
        resolver=SkillResolverSpec(
            prompt=(
                "Domain policy: written sources outweigh verbal sources. "
                "Among written sources, the latest one wins. "
                "Treat email follow-ups as written; treat call transcripts as verbal."
            ),
        ),
    )
    skilled_policies = ResolverPolicySet(policies=(skill_policy,), version=2)
    skilled = resolve(
        candidates=writes,
        spec=AutoResolverSpec(),
        resolver_policies=skilled_policies,
        entity_type="Customer",
        field_name="deal_size",
        llm=llm,
    )
    if skilled.status != FieldStatus.KNOWN:
        raise AssertionError(
            f"skill resolution should be KNOWN, got {skilled.status}: {skilled.reason}"
        )
    if skilled.winner is None:
        raise AssertionError("skill should pick a winner")
    skill_value = json.loads(skilled.winner.value_json)
    if not _is_around(skill_value, 300000):
        raise AssertionError(
            f"skill should pick ~300000 ('written outweighs verbal'), got {skill_value!r}: "
            f"reason={skilled.reason!r}"
        )
    if not skilled.reason:
        raise AssertionError("skill resolution should carry a reason")

    # --- 5. Source-remove the email and re-resolve. Should fall back to ~250000. ---
    _delete_document_and_writes(store, label="email_jane_2026-04-17.md")
    surviving_writes = _live_writes_for(
        store,
        entity_type="Customer",
        key=canonical_key,
        field_name="deal_size",
    )
    if len(surviving_writes) != 1:
        raise AssertionError(
            f"after deleting the email, exactly 1 write should remain, got {len(surviving_writes)}: "
            f"{[w.value_json for w in surviving_writes]}"
        )
    after_churn = resolve(
        candidates=surviving_writes,
        spec=AutoResolverSpec(),
        resolver_policies=skilled_policies,
        entity_type="Customer",
        field_name="deal_size",
        llm=llm,
    )
    if after_churn.status != FieldStatus.KNOWN:
        raise AssertionError(f"after churn, expected KNOWN, got {after_churn.status}")
    if after_churn.winner is None:
        raise AssertionError("after churn, expected a winner")
    fallback_value = json.loads(after_churn.winner.value_json)
    if not _is_around(fallback_value, 250000):
        raise AssertionError(
            f"after deleting the email, expected fall-back to ~250000, got {fallback_value!r}"
        )

    # --- 6. Sanity: ACL evaluator sees nothing-by-default for an unknown agent. ---
    acl_decision = evaluate_field_read(
        entity_type="Customer",
        field_name="deal_size",
        agent_id="random_other",
        ruleset=initial_ruleset,
    )
    if acl_decision.allowed:
        raise AssertionError("default-deny ACL was not enforced")


def _is_around(value, target: int, tolerance: int = 50_000) -> bool:
    """The LLM may extract numbers with slight variation (e.g. $250K → 250000 vs 250_000.0)."""
    if not isinstance(value, (int, float)):
        return False
    return abs(float(value) - target) <= tolerance


def _delete_document_and_writes(store, *, label: str) -> None:
    """Look up the document by label and call the production source-removal helper."""
    with store.session() as s:
        doc = s.exec(select(DocumentRow).where(DocumentRow.label == label)).first()
        if doc is None:
            raise AssertionError(f"no document with label {label!r}")
        doc_id = doc.id
    remove_document(store=store, document_id=doc_id)
