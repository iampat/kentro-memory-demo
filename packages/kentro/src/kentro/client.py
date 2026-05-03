"""Sync HTTP client for kentro-server.

The SDK is a thin wrapper over `kentro-server`'s HTTP routes — one method
per route, returning the typed Pydantic responses defined in `kentro.types`.

Per IMPLEMENTATION_PLAN.md "Decisions locked since the handoff" §2.5:
- Single `Client` class — admin-ness is the server's call. Methods that
  hit admin-gated routes (apply_ruleset, register_schema, delete_document)
  raise `AdminRequiredError` on a 403 response, but the client itself does
  no role-checking; it just maps status codes to typed exceptions.
- Sync only for v0. Async is deferred to v0.1.
- No retries, no backoff, no circuit breaker. Caller decides.

Construction:

    import kentro

    client = kentro.Client(
        base_url="http://127.0.0.1:8000",
        api_key="local-ingestion-do-not-share",
    )

    # one-shot
    record = client.read("Customer", "Acme")

    # context manager (closes the underlying httpx.Client cleanly)
    with kentro.Client(base_url=..., api_key=...) as client:
        record = client.read("Customer", "Acme")
"""

from __future__ import annotations

from types import TracebackType
from typing import Any
from uuid import UUID

import httpx
from pydantic import TypeAdapter

from kentro.types import (
    EntityRecord,
    EntityTypeDef,
    NLResponse,
    ResolverSpec,
    Rule,
    RuleSet,
    WriteResult,
)

# === Exceptions ===========================================================


class KentroError(Exception):
    """Base for all kentro client errors."""


class AuthError(KentroError):
    """401 — bearer token missing, malformed, or unknown."""


class AdminRequiredError(KentroError):
    """403 — caller is authenticated but lacks the admin role for this route."""


class NotFoundError(KentroError):
    """404 — route or resource not found (e.g. unknown document_id on delete)."""


class SchemaEvolutionError(KentroError):
    """409 — schema-evolution rule rejected the request (rename / type change /
    removal of a non-deprecated field). Server returns a `detail` string
    explaining which constraint was violated."""


class ServerError(KentroError):
    """5xx — the server failed in a way that's not the client's fault. Single
    catch-all class for all 500-class responses; v0 doesn't distinguish 500
    from 502 etc."""


# === Client ===============================================================


class Client:
    """Sync HTTP client. Methods are 1:1 with kentro-server routes."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Construct a client.

        - `base_url`: e.g. `"http://127.0.0.1:8000"`. Trailing slash optional.
        - `api_key`: per-(tenant, agent) Bearer key from `tenants.json`. The
          server resolves it to a `Principal`; the client never decides which
          methods are "admin-only" on its own.
        - `timeout`: per-request timeout in seconds. Ingest and NL parse can
          legitimately take 20+s on cold cache; 30 is a reasonable default.
        - `transport`: optional `httpx.BaseTransport` (typically `httpx.MockTransport`)
          for in-process testing. When provided, the client doesn't make real
          network calls — useful for routing through `fastapi.testclient` or
          for unit-testing client logic in isolation. Production callers leave
          this `None`.
        """
        self._base = base_url.rstrip("/")
        kwargs: dict = {
            "base_url": self._base,
            "headers": {"Authorization": f"Bearer {api_key}"},
            "timeout": timeout,
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._http = httpx.Client(**kwargs)

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # === Public surface ===================================================

    # --- health / ops ---

    def healthz(self) -> dict:
        """`GET /healthz` — does NOT require auth (server returns 200 unauth'd)."""
        # Use the same client so an injected transport (test mode) catches it too.
        # The bearer header is harmless on /healthz.
        return self._get("/healthz")

    def llm_stats(self) -> dict:
        """`GET /llm/stats` — cache hit/miss counters."""
        return self._get("/llm/stats")

    # --- schema ---

    def list_schema(self) -> list[EntityTypeDef]:
        """`GET /schema` — every registered entity type for this tenant.

        Triggers the `Note` auto-seed on first call (server-side; transparent)."""
        payload = self._get("/schema")
        return [EntityTypeDef.model_validate(td) for td in payload["type_defs"]]

    def register_schema(self, type_defs: list[EntityTypeDef]) -> list[EntityTypeDef]:
        """`POST /schema/register` — ADMIN. Idempotent for unchanged definitions.

        Schema-evolution rules (no rename / no type-change / no removal) are
        enforced server-side; violations raise `SchemaEvolutionError`."""
        payload = self._post(
            "/schema/register",
            json={"type_defs": [td.model_dump(mode="json") for td in type_defs]},
        )
        return [EntityTypeDef.model_validate(td) for td in payload["type_defs"]]

    # --- rules ---

    def get_active_ruleset(self) -> RuleSet:
        """`GET /rules/active` — current RuleSet at the latest applied version."""
        return RuleSet.model_validate(self._get("/rules/active"))

    def apply_ruleset(self, ruleset: RuleSet, summary: str | None = None) -> int:
        """`POST /rules/apply` — ADMIN. Atomic version bump. Returns new version."""
        payload = self._post(
            "/rules/apply",
            json={
                "ruleset": ruleset.model_dump(mode="json"),
                "summary": summary,
            },
        )
        return int(payload["version"])

    def parse_nl_to_ruleset(self, text: str) -> NLResponse:
        """`POST /rules/parse` — multi-step NL parse. Does NOT apply.

        Returns the partial-success NLResponse: `parsed_ruleset` (compilable
        rules), `intents` (every intent identified, even ones we skipped),
        `notes` (skip-reasons + step-1 splitter notes)."""
        payload = self._post("/rules/parse", json={"text": text})
        return NLResponse.model_validate(payload)

    # --- entities ---

    def read(self, entity_type: str, entity_key: str) -> EntityRecord:
        """`GET /entities/{type}/{key}` — read with the default `AutoResolver`."""
        return EntityRecord.model_validate(self._get(f"/entities/{entity_type}/{entity_key}"))

    def read_with(self, entity_type: str, entity_key: str, resolver: ResolverSpec) -> EntityRecord:
        """`POST /entities/{type}/{key}/read` — read with an explicit ResolverSpec.

        Use when you want `RawResolver` (surface every candidate),
        `PreferAgent`, `SkillResolver` with a prompt, etc."""
        # ResolverSpec is a Pydantic discriminated union; it's a Pydantic model
        # variant, so model_dump works directly.
        adapter: TypeAdapter[ResolverSpec] = TypeAdapter(ResolverSpec)
        payload = self._post(
            f"/entities/{entity_type}/{entity_key}/read",
            json={"resolver": adapter.dump_python(resolver, mode="json")},
        )
        return EntityRecord.model_validate(payload)

    def write(
        self,
        entity_type: str,
        entity_key: str,
        field_name: str,
        value_json: str,
        confidence: float | None = None,
    ) -> WriteResult:
        """`POST /entities/{type}/{key}/{field}` — write one field. Typed result."""
        body: dict = {"value_json": value_json}
        if confidence is not None:
            body["confidence"] = confidence
        return WriteResult.model_validate(
            self._post(f"/entities/{entity_type}/{entity_key}/{field_name}", json=body)
        )

    # --- documents ---

    def ingest(
        self,
        content: str,
        label: str | None = None,
        smart_model: str | None = None,
    ) -> dict:
        """`POST /documents` — ingest a markdown document; runs smart-tier extraction.

        Returns the JSON `IngestionResult` (Pydantic-validated server-side; the
        SDK returns it as a dict to avoid a circular dep with the heavier
        IngestionResult shape — callers can `IngestionResult.model_validate(...)`
        if they want the typed version)."""
        body: dict = {"content": content}
        if label is not None:
            body["label"] = label
        if smart_model is not None:
            body["smart_model"] = smart_model
        return self._post("/documents", json=body)

    def delete_document(self, document_id: UUID | str) -> dict:
        """`DELETE /documents/{id}` — ADMIN. Removes blob + writes; re-resolves.

        Returns `{"removed_writes": int, "closed_conflicts": [{...}]}`."""
        return self._delete(f"/documents/{document_id}")

    # --- memory shortcut ---

    def remember(
        self,
        subject: str,
        predicate: str,
        object_value: Any,
        confidence: float | None = None,
        source_label: str | None = None,
    ) -> WriteResult:
        """`POST /memory/remember` — Note shortcut for free-form facts.

        `subject` becomes the entity_key (and is also written into the
        `Note.subject` field as of 2026-05-03). `object_value` is anything
        JSON-serializable; the server stores it as canonical JSON, the read
        path decodes once, so non-string values roundtrip cleanly."""
        body: dict = {
            "subject": subject,
            "predicate": predicate,
            "object_json": object_value,
        }
        if confidence is not None:
            body["confidence"] = confidence
        if source_label is not None:
            body["source_label"] = source_label
        return WriteResult.model_validate(self._post("/memory/remember", json=body))

    # === Private ==========================================================

    def _get(self, path: str) -> Any:
        return self._handle(self._http.get(path))

    def _post(self, path: str, *, json: Any) -> Any:
        return self._handle(self._http.post(path, json=json))

    def _delete(self, path: str) -> Any:
        return self._handle(self._http.delete(path))

    def _handle(self, response: httpx.Response) -> Any:
        """Map status code → typed exception or return parsed JSON.

        Single point for the status-code dispatch so every method gets the
        same error semantics. Pulled out so tests can target the dispatch
        logic directly if needed."""
        status = response.status_code
        if 200 <= status < 300:
            # 204 No Content has empty body — return {} instead of crashing on json().
            if status == 204 or not response.content:
                return {}
            return response.json()
        # Error path — try to extract `detail` from the standard FastAPI shape.
        detail = _extract_detail(response)
        if status == 401:
            raise AuthError(detail)
        if status == 403:
            raise AdminRequiredError(detail)
        if status == 404:
            raise NotFoundError(detail)
        if status == 409:
            raise SchemaEvolutionError(detail)
        if 500 <= status < 600:
            raise ServerError(f"{status}: {detail}")
        # Anything else (4xx not handled above) — generic KentroError so a
        # client app can still catch it without a bare except.
        raise KentroError(f"unexpected status {status}: {detail}")


def _extract_detail(response: httpx.Response) -> str:
    """Best-effort extraction of FastAPI's `{"detail": "..."}` body.

    Falls back to the raw text (truncated) when the body isn't JSON or doesn't
    have a `detail` key — error responses from middleware (e.g. uvicorn-level
    failures) are plain text."""
    try:
        body = response.json()
    except (ValueError, httpx.DecodingError):
        return (
            response.text[:200]
            if response.text
            else f"<empty body, status {response.status_code}>"
        )
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


__all__ = [
    "AdminRequiredError",
    "AuthError",
    "Client",
    "KentroError",
    "NotFoundError",
    "SchemaEvolutionError",
    "ServerError",
]


# Tiny helper exposed for explicit Rule list parsing if needed (e.g. the
# `apply_ruleset` payload echo). The discriminated-union TypeAdapter at module
# scope is a tiny perf optimization (cached compile).
_RULE_ADAPTER: TypeAdapter[Rule] = TypeAdapter(Rule)
