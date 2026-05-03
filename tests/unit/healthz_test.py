"""Smoke test: kentro-server's /healthz endpoint responds AFTER the lifespan boots.

Critical: enter the `with TestClient(app)` context so the FastAPI lifespan handler
runs. A bare module-level `client = TestClient(app)` does NOT trigger the lifespan,
which silently masks startup bugs (e.g. a Settings field referenced by the lifespan
but missing from the class).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kentro_server.main import app
from kentro_server.settings import Settings


@pytest.fixture
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the lifespan at a tmp state dir + tenants.json so the test doesn't touch real state.

    Cache stays on (production default) — the test never invokes the LLM, so no
    real cache I/O happens, and the test reflects the production shape.
    """
    monkeypatch.setenv("KENTRO_STATE_DIR", str(tmp_path / "kentro_state"))
    monkeypatch.setenv("KENTRO_TENANTS_JSON", str(tmp_path / "tenants.json"))
    # Also set a dummy Anthropic key so make_llm_client() inside the lifespan succeeds.
    real = Settings()
    if not real.anthropic_api_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used-here")


def test_healthz_returns_ok_with_lifespan(isolated_state: None) -> None:
    """Construct TestClient inside `with` so the lifespan startup actually runs."""
    with TestClient(app) as client:
        r = client.get("/healthz")
        if r.status_code != 200:
            raise AssertionError(f"expected 200, got {r.status_code}: {r.text}")
        body = r.json()
        if body.get("status") != "ok":
            raise AssertionError(f"expected status=ok, got {body!r}")


def test_llm_stats_endpoint_responds(isolated_state: None) -> None:
    """Lifespan must build the LLMClient too — exercise /llm/stats end-to-end."""
    with TestClient(app) as client:
        r = client.get("/llm/stats")
        if r.status_code != 200:
            raise AssertionError(f"expected 200 from /llm/stats, got {r.status_code}: {r.text}")
        payload = r.json()
        if "hits" not in payload or "inner_calls" not in payload:
            raise AssertionError(f"unexpected /llm/stats payload: {payload!r}")
