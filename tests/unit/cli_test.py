"""CLI command tests — invoked through Typer's CliRunner.

`seed-demo` and `smoke-test` are HTTP clients that talk to a running server.
We stand up an in-process server via a background uvicorn worker is overkill;
instead we monkey-patch `httpx` calls to route into a `TestClient` against the
same FastAPI app the CLI would normally hit. This proves wiring + JSON shape
without binding a port.
"""

from collections.abc import Iterator
from unittest.mock import patch
from urllib.parse import urlparse

import httpx
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
from kentro_server.main import app, cli
from typer.testing import CliRunner

from tests.unit._helpers import ADMIN_KEY, FakeLLM


@pytest.fixture
def patched_httpx(isolated_state: None, fake_llm: FakeLLM) -> Iterator[TestClient]:
    """Route the CLI's `httpx.get` / `httpx.post` calls into a TestClient.

    `httpx.Client` and `TestClient` share a near-identical surface; we patch the
    module-level `get` and `post` to delegate. The CLI never imports httpx.Client
    directly, so this works without breaking other code.

    Uses `urlparse` rather than ad-hoc string slicing — robust against missing
    schemes, trailing slashes, query strings.
    """
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as test_client:

        def _path_only(url: str) -> str:
            parsed = urlparse(url)
            # `parsed.path` always starts with "/" for absolute URLs; if `url` is
            # already a bare path it survives unchanged.
            return parsed.path or "/"

        def _post(url, headers=None, json=None, timeout=None):
            return test_client.post(_path_only(url), headers=headers or {}, json=json)

        def _get(url, headers=None, timeout=None):
            return test_client.get(_path_only(url), headers=headers or {})

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
        ["seed-demo", "--api-key", ADMIN_KEY, "--skip-ingest"],
    )
    if result.exit_code != 0:
        raise AssertionError(f"seed-demo failed: exit={result.exit_code} output={result.stdout}")
    if "registered schemas" not in result.stdout:
        raise AssertionError(f"expected 'registered schemas' in output, got: {result.stdout}")


def test_smoke_test_command_succeeds(patched_httpx: TestClient) -> None:
    """`smoke-test` round-trips schema → write → read."""
    test_client = patched_httpx

    test_client.post(
        "/schema/register",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
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
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        json={
            "ruleset": RuleSet(
                rules=(
                    EntityVisibilityRule(
                        agent_id="ingestion_agent", entity_type="Customer", allowed=True
                    ),
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
    result = runner.invoke(cli, ["smoke-test", "--api-key", ADMIN_KEY])
    if result.exit_code != 0:
        raise AssertionError(f"smoke-test failed: exit={result.exit_code} output={result.stdout}")
    if "smoke-test passed" not in result.stdout:
        raise AssertionError(f"expected 'smoke-test passed' in output, got: {result.stdout}")
