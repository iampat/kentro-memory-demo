"""Demo-only routes — `GET /demo/keys` returns the per-agent bearer tokens.

Refuses to respond unless `KENTRO_ALLOW_DEMO_KEYS=true` is set (the same opt-in
that gates the boot guard). This is the SAME safety knob — no separate flag —
because if you've already opted into running with the public demo keys, then
returning them via API to the local UI is no leak (the UI is on the same
machine; the keys are committed to the repo). If you've rotated the keys for
deployment, this endpoint won't return rotated values either; it just returns
404 so the UI knows to fall back to manual entry.

The agent-switcher in the demo UI calls this once on first load and caches the
returned keys in localStorage. Without this endpoint, every demoer would need
to copy-paste 3 bearer tokens out of `tenants.json` into the UI by hand.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from kentro_server.api.auth import AdminPrincipalDep
from kentro_server.api.deps import SettingsDep, TenantRegistryDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoAgentKey(BaseModel):
    model_config = ConfigDict(frozen=True)
    agent_id: str
    api_key: str
    is_admin: bool
    display_name: str | None = None


class DemoKeysResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str
    agents: tuple[DemoAgentKey, ...] = ()


@router.get("/keys", response_model=DemoKeysResponse)
def get_demo_keys(
    principal: AdminPrincipalDep,
    settings: SettingsDep,
    registry: TenantRegistryDep,
) -> DemoKeysResponse:
    """Return every agent's bearer token for the principal's tenant.

    Admin-only AND opt-in-only. The combination is intentional — admin gates the
    operation against random non-admin agents, and the demo-keys opt-in gates
    the operation against any deployed instance with rotated keys.
    """
    if not settings.kentro_allow_demo_keys:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "/demo/keys is disabled when KENTRO_ALLOW_DEMO_KEYS is not set "
                "(this endpoint exists for the local-dev demo UI only)"
            ),
        )
    tenant_id = principal.store.tenant_id
    agents = tuple(
        DemoAgentKey(
            agent_id=acfg.id,
            api_key=acfg.api_key,
            is_admin=acfg.is_admin,
            display_name=acfg.display_name,
        )
        for acfg in registry.agents_for(tenant_id)
    )
    return DemoKeysResponse(tenant_id=tenant_id, agents=agents)
