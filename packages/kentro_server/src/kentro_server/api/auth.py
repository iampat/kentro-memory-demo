"""Bearer-token auth for the HTTP routes (and indirectly for the MCP server).

The Bearer key is the per-(tenant, agent) API key configured in `tenants.json`.
A single header lookup resolves both the tenant (for routing) and the agent
identity (for ACL checks downstream). There is no separate `X-Tenant-Id` header
— a key uniquely identifies a (tenant, agent) pair.

Tests override `current_principal` via `app.dependency_overrides` to inject a
synthetic principal without going through real config.
"""

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kentro_server.store import TenantRegistry, TenantStore

logger = logging.getLogger(__name__)


_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Resolved (tenant, agent) pair for the current request."""

    tenant_id: str
    agent_id: str
    store: TenantStore


def _get_tenant_registry(request: Request) -> TenantRegistry:
    """Mirror of `main.get_tenant_registry`, reproduced here so this module can be
    imported from `api/routes/*` without a runtime dependency on `main`."""
    return request.app.state.tenant_registry


def current_principal(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Resolve the request's Bearer key to a `Principal` or raise 401.

    Returns 401 (not 403) for both missing-and-malformed headers and unknown-key
    cases. Production never distinguishes them — we want auth failures to look
    identical from the client's side so they leak no information about which keys
    might be valid.
    """
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header (expected 'Bearer <api-key>')",
            headers={"WWW-Authenticate": "Bearer"},
        )
    registry = _get_tenant_registry(request)
    try:
        store, agent_id = registry.by_api_key(creds.credentials)
    except KeyError:
        # Don't log the key itself — even partial fingerprints would help an
        # attacker confirm guesses.
        logger.info("auth: unknown bearer key (length=%d)", len(creds.credentials))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid api key",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return Principal(tenant_id=store.tenant_id, agent_id=agent_id, store=store)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


__all__ = ["Principal", "PrincipalDep", "current_principal"]
