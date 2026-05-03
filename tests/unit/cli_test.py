"""CLI command tests — invoked through Typer's CliRunner.

`seed-demo` and `smoke-test` are HTTP clients that talk to a running server.
We stand up an in-process server via a background uvicorn worker is overkill;
instead we monkey-patch `httpx` calls to route into a `TestClient` against the
same FastAPI app the CLI would normally hit. This proves wiring + JSON shape
without binding a port.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from kentro_server.api.deps import get_llm_client
from kentro_server.main import app, cli
from kentro_server.settings import Settings
from kentro_server.skills.llm_client import (
    ExtractionResult,
    LLMClient,
    SkillResolverDecision,
)
from typer.testing import CliRunner

_API_KEY = "cli-test-key"


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


class _FakeIngestLLM(LLMClient):
    """LLM that returns a single-entity ExtractionResult so seed-demo can finish ingest."""

    def run_skill_resolver(self, *, prompt, candidates, model=None):
        return SkillResolverDecision(chosen_value_json=None, reason="not under test")

    def extract_entities(
        self, *, document_text, registered_schemas, document_label=None, model=None
    ):
        return ExtractionResult(entities=())

    def identify_nl_intents(self, *, text, model=None):
        raise NotImplementedError("not exercised here")

    def parse_nl_rule(
        self,
        *,
        intent_description,
        intent_kind,
        registered_schemas,
        known_agent_ids,
        model=None,
    ):
        raise NotImplementedError("not exercised here")


@pytest.fixture
def patched_httpx(isolated_state: None) -> Iterator[TestClient]:
    """Route the CLI's `httpx.get` / `httpx.post` calls into a TestClient.

    `httpx.Client` and `TestClient` share a near-identical surface; we patch the
    module-level `get` and `post` to delegate. The CLI never imports httpx.Client
    directly, so this works without breaking other code.
    """
    app.dependency_overrides[get_llm_client] = lambda: _FakeIngestLLM()
    with TestClient(app) as test_client:

        def _post(url, headers=None, json=None, timeout=None):
            path = url.split("://", 1)[-1].split("/", 1)[1] if "://" in url else url
            return test_client.post(f"/{path}", headers=headers or {}, json=json)

        def _get(url, headers=None, timeout=None):
            path = url.split("://", 1)[-1].split("/", 1)[1] if "://" in url else url
            return test_client.get(f"/{path}", headers=headers or {})

        with (
            patch.object(httpx, "post", side_effect=_post),
            patch.object(httpx, "get", side_effect=_get),
        ):
            yield test_client
    app.dependency_overrides.pop(get_llm_client, None)


def test_seed_demo_command_succeeds_with_skip_ingest(patched_httpx: TestClient) -> None:
    """`seed-demo --skip-ingest` registers schemas and exits cleanly."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["seed-demo", "--api-key", _API_KEY, "--skip-ingest"],
    )
    if result.exit_code != 0:
        raise AssertionError(f"seed-demo failed: exit={result.exit_code} output={result.stdout}")
    if "registered schemas" not in result.stdout:
        raise AssertionError(f"expected 'registered schemas' in output, got: {result.stdout}")


def test_smoke_test_command_succeeds(patched_httpx: TestClient) -> None:
    """`smoke-test` round-trips schema → write → read."""
    # Grant the ingestion_agent access first (smoke-test does write+read which need ACL).
    test_client = patched_httpx
    from kentro.types import EntityTypeDef, FieldDef, FieldReadRule, RuleSet, WriteRule

    test_client.post(
        "/schema/register",
        headers={"Authorization": f"Bearer {_API_KEY}"},
        json={
            "type_defs": [
                EntityTypeDef(
                    name="Customer", fields=(FieldDef(name="name", type_str="str"),)
                ).model_dump(mode="json")
            ]
        },
    )
    test_client.post(
        "/rules/apply",
        headers={"Authorization": f"Bearer {_API_KEY}"},
        json={
            "ruleset": RuleSet(
                rules=(
                    WriteRule(agent_id="ingestion_agent", entity_type="Customer", allowed=True),
                    FieldReadRule(
                        agent_id="ingestion_agent",
                        entity_type="Customer",
                        field_name="name",
                        allowed=True,
                    ),
                ),
                version=0,
            ).model_dump(mode="json"),
            "summary": "test setup",
        },
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["smoke-test", "--api-key", _API_KEY])
    if result.exit_code != 0:
        raise AssertionError(f"smoke-test failed: exit={result.exit_code} output={result.stdout}")
    if "smoke-test passed" not in result.stdout:
        raise AssertionError(f"expected 'smoke-test passed' in output, got: {result.stdout}")
