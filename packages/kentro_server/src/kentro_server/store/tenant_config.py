"""Tenant configuration model — `<state_root>/tenants.json`.

Each tenant is one folder under `<state_root>/<tenant_id>/`. The JSON file is the
source of truth for which tenants exist; the server eagerly constructs the per-tenant
state directories from it at startup.

This is **not production code.** Per the v0 design, API keys are stored raw in
`tenants.json`; the file is gitignored alongside `.env`. Do not deploy a hashed-keys
or encrypted store on top of this without the broader auth/secrets review the demo
explicitly defers.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TenantConfig(BaseModel):
    """One tenant's configuration entry. `api_key` is raw (v0 demo only)."""

    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str | None = None
    api_key: str = Field(min_length=1)


class TenantsConfig(BaseModel):
    """Top-level shape of `tenants.json`. Validates uniqueness on load."""

    model_config = ConfigDict(frozen=True)

    tenants: tuple[TenantConfig, ...] = ()

    @model_validator(mode="after")
    def _no_duplicate_ids_or_keys(self) -> "TenantsConfig":
        """Reject duplicate tenant IDs or duplicate api_keys at load time.

        Without this check, two tenants with the same api_key would silently
        overwrite the routing table — one tenant becomes unreachable through
        that credential, and a request authed with the duplicated key routes
        to whichever tenant happened to be loaded last. Auth/isolation hole.
        """
        seen_ids: set[str] = set()
        seen_keys: set[str] = set()
        for t in self.tenants:
            if t.id in seen_ids:
                raise ValueError(f"duplicate tenant id: {t.id!r}")
            if t.api_key in seen_keys:
                raise ValueError(
                    f"duplicate tenant api_key for tenant {t.id!r} "
                    "(api keys must be unique across tenants — fix `tenants.json`)"
                )
            seen_ids.add(t.id)
            seen_keys.add(t.api_key)
        return self


__all__ = ["TenantConfig", "TenantsConfig"]
