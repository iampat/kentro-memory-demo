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
    """Resolved (tenant, agent) pair for the current request.

    `is_admin` carries the control-plane role. Authentication (key valid?) and
    authorization (role allowed?) are intentionally separate fields: `current_principal`
    establishes identity, `current_admin_principal` enforces the role. This way a
    route's signature documents which class of authority it requires.
    """

    tenant_id: str
    agent_id: str
    store: TenantStore
    is_admin: bool = False


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
        store, agent_id, is_admin = registry.by_api_key(creds.credentials)
    except KeyError:
        # Don't log the key itself — even partial fingerprints would help an
        # attacker confirm guesses.
        logger.info("auth: unknown bearer key (length=%d)", len(creds.credentials))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid api key",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return Principal(tenant_id=store.tenant_id, agent_id=agent_id, store=store, is_admin=is_admin)


def current_admin_principal(
    principal: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    """Authorize: caller must be an admin agent. 403 otherwise.

    Used by control-plane routes (POST /rules/apply, POST /schema/register,
    DELETE /documents/{id}). Without this gate, any tenant key could mutate the
    ruleset and re-grant itself anything — defeating the per-(tenant, agent)
    auth model.
    """
    if not principal.is_admin:
        logger.info(
            "auth: tenant=%s agent=%s attempted admin operation, denied",
            principal.tenant_id,
            principal.agent_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required for this operation",
        )
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]
AdminPrincipalDep = Annotated[Principal, Depends(current_admin_principal)]


__all__ = [
    "AdminPrincipalDep",
    "Principal",
    "PrincipalDep",
    "current_admin_principal",
    "current_principal",
]
