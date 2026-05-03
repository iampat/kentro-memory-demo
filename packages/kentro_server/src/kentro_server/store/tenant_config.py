"""Tenant configuration model — `tenants.json` at the repo root.

Each tenant has one or more agents, each with its own raw API key. The Bearer
token in `Authorization: Bearer <key>` resolves to a (tenant, agent) pair in one
step — there's no separate `X-Agent-Id` header. This makes the (Sales vs CS)
distinction the demo demands actual auth boundaries, not a freeform string.

This is **not production code.** API keys are stored raw — the file is committed
to git intentionally for demo reproducibility (defaults are clearly demo-only,
e.g. `local-sales-do-not-share`). For a real deployment, add hashing + rotation
on top.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentConfig(BaseModel):
    """One agent identity within a tenant. `api_key` is the bearer token.

    `is_admin=True` grants the control-plane role: only admin agents can change
    rules (`POST /rules/apply`), evolve schemas (`POST /schema/register`), or
    delete documents. The Sales-vs-CS demo boundary depends on this — without
    it, any tenant key could re-grant itself anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    display_name: str | None = None
    is_admin: bool = False


class TenantConfig(BaseModel):
    """One tenant + its agents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    display_name: str | None = None
    agents: tuple[AgentConfig, ...] = ()

    @model_validator(mode="after")
    def _agents_unique(self) -> Self:
        seen: set[str] = set()
        for a in self.agents:
            if a.id in seen:
                raise ValueError(f"tenant {self.id!r}: duplicate agent id {a.id!r}")
            seen.add(a.id)
        return self


class TenantsConfig(BaseModel):
    """Top-level shape of `tenants.json`. Validates uniqueness on load."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenants: tuple[TenantConfig, ...] = ()

    @model_validator(mode="after")
    def _no_duplicate_ids_or_keys(self) -> Self:
        """Reject duplicate tenant IDs or duplicate api_keys at load time.

        A duplicate api_key — even across tenants — is an auth/isolation hole:
        a request authed with the duplicated key would route to whichever tenant
        was loaded last, silently. Fail loudly at config load instead.
        """
        seen_tenant_ids: set[str] = set()
        seen_keys: set[str] = set()
        for t in self.tenants:
            if t.id in seen_tenant_ids:
                raise ValueError(f"duplicate tenant id: {t.id!r}")
            seen_tenant_ids.add(t.id)
            for a in t.agents:
                if a.api_key in seen_keys:
                    raise ValueError(
                        f"duplicate api_key in tenant {t.id!r} agent {a.id!r} "
                        "(api keys must be unique across ALL agents in ALL tenants)"
                    )
                seen_keys.add(a.api_key)
        return self


__all__ = ["AgentConfig", "TenantConfig", "TenantsConfig"]
