"""Tenant configuration model — `<state_root>/tenants.json`.

Each tenant is one folder under `<state_root>/<tenant_id>/`. The JSON file is the
source of truth for which tenants exist; the server eagerly constructs the per-tenant
state directories from it at startup.

This is **not production code.** Per the v0 design, API keys are stored raw in
`tenants.json`; the file is gitignored alongside `.env`. Do not deploy a hashed-keys
or encrypted store on top of this without the broader auth/secrets review the demo
explicitly defers.
"""

from pydantic import BaseModel, ConfigDict, Field


class TenantConfig(BaseModel):
    """One tenant's configuration entry. `api_key` is raw (v0 demo only)."""

    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str | None = None
    api_key: str = Field(min_length=1)


class TenantsConfig(BaseModel):
    """Top-level shape of `tenants.json`."""

    model_config = ConfigDict(frozen=True)

    tenants: tuple[TenantConfig, ...] = ()


__all__ = ["TenantConfig", "TenantsConfig"]
