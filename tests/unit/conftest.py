"""Shared pytest fixtures for unit tests.

Lives at `tests/unit/conftest.py` so it auto-loads for every test in the
package without explicit imports. The `FakeLLM` class and `ADMIN_KEY` /
`AGENT_KEY` constants live in `_helpers.py` next door — tests `import` those
by name; conftest is for fixtures only.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.unit._helpers import ADMIN_KEY, AGENT_KEY, FakeLLM


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def tenants_json_with_admin(tmp_path: Path) -> Path:
    """Write a tenants.json with one admin and one non-admin agent.

    Returns the file path. Tests that need the standard admin/non-admin pair
    point `KENTRO_TENANTS_JSON` at this file via `monkeypatch.setenv`.
    """
    path = tmp_path / "tenants.json"
    path.write_text(
        f"""{{
          "tenants": [
            {{
              "id": "local",
              "display_name": "Local",
              "agents": [
                {{"id": "ingestion_agent", "api_key": "{ADMIN_KEY}", "is_admin": true}},
                {{"id": "sales", "api_key": "{AGENT_KEY}"}}
              ]
            }}
          ]
        }}""",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def isolated_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tenants_json_with_admin: Path,
) -> Iterator[None]:
    """Point the lifespan at an isolated state dir + the admin/non-admin tenants.json.

    Sets a dummy `ANTHROPIC_API_KEY` if none is present so `make_llm_client`
    inside the lifespan doesn't fail on missing-key during tests that override
    the LLM client via `app.dependency_overrides` anyway.
    """
    monkeypatch.setenv("KENTRO_STATE_DIR", str(tmp_path / "kentro_state"))
    monkeypatch.setenv("KENTRO_TENANTS_JSON", str(tenants_json_with_admin))
    # Tests opt into the demo-keys path so /demo/keys returns 200 instead of
    # 404. The conftest tenants.json doesn't actually contain real demo keys,
    # so the boot guard never trips — the env var only flips the route's gate.
    monkeypatch.setenv("KENTRO_ALLOW_DEMO_KEYS", "true")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used-here")
    yield
