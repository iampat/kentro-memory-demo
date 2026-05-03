"""Smoke test: kentro-server's /healthz endpoint responds."""

from fastapi.testclient import TestClient

from kentro_server.main import app

client = TestClient(app)


def test_healthz_returns_ok() -> None:
    r = client.get("/healthz")
    if r.status_code != 200:
        raise AssertionError(f"expected 200, got {r.status_code}: {r.text}")
    body = r.json()
    if body.get("status") != "ok":
        raise AssertionError(f"expected status=ok, got {body!r}")
