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


def test_static_ui_served_at_root(isolated_state: None) -> None:
    """`GET /` returns the prototype's index.html — proves the StaticFiles mount is wired
    AND that it doesn't shadow the explicit routes (healthz / llm_stats above still work).
    """
    with TestClient(app) as client:
        r = client.get("/")
        if r.status_code != 200:
            raise AssertionError(f"expected 200 at /, got {r.status_code}: {r.text[:200]}")
        body = r.text
        if "<title>Kentro · Demo</title>" not in body:
            raise AssertionError(f"index.html title missing — got body[:200]: {body[:200]!r}")


def test_static_ui_serves_named_files(isolated_state: None) -> None:
    """The bundled `app.jsx` is reachable as `/app.jsx` so the in-page <script>
    tags resolve. Also covers `styles.css`, `data.js`, etc. via one path."""
    with TestClient(app) as client:
        r = client.get("/app.jsx")
        if r.status_code != 200:
            raise AssertionError(f"expected 200 at /app.jsx, got {r.status_code}")
        # The prototype's app.jsx defines window.K = {...} helpers; sanity check
        # we got the JS content, not an HTML 404 page.
        if "React" not in r.text and "function" not in r.text:
            raise AssertionError("/app.jsx returned non-JS content")


def test_mcp_mount_still_works_after_static(isolated_state: None) -> None:
    """Regression guard: with StaticFiles mounted at `/`, `/mcp` must still
    route to the MCP sub-app (307 → /mcp/), not be served as a static path."""
    with TestClient(app) as client:
        # follow_redirects=False so we observe the 307 explicitly
        r = client.get("/mcp", follow_redirects=False)
        # FastMCP redirects /mcp → /mcp/. If StaticFiles were shadowing /mcp,
        # we'd get a 404 (file not found) instead of the 307.
        if r.status_code not in {200, 307}:
            raise AssertionError(
                f"expected 200 or 307 at /mcp (MCP sub-app), got {r.status_code} "
                "— StaticFiles may be shadowing the /mcp mount"
            )
