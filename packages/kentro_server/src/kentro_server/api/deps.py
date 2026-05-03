"""Shared FastAPI dependencies used across the route modules.

Centralized here so route modules don't all reach into `main.py` (which would
make `main.py` import the routers and route modules import `main`, a cycle).
"""

from typing import Annotated

from fastapi import Depends, Request

from kentro_server.api.auth import Principal, current_principal
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.settings import Settings
from kentro_server.skills.llm_client import LLMClient
from kentro_server.store import TenantRegistry


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm_client


def get_tenant_registry(request: Request) -> TenantRegistry:
    return request.app.state.tenant_registry


def get_schema_registry(
    principal: Annotated[Principal, Depends(current_principal)],
) -> SchemaRegistry:
    """SchemaRegistry is per-tenant; resolved from the authenticated principal's store."""
    return SchemaRegistry(principal.store)


SettingsDep = Annotated[Settings, Depends(get_settings)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
TenantRegistryDep = Annotated[TenantRegistry, Depends(get_tenant_registry)]
SchemaRegistryDep = Annotated[SchemaRegistry, Depends(get_schema_registry)]


__all__ = [
    "LLMClientDep",
    "SchemaRegistryDep",
    "SettingsDep",
    "TenantRegistryDep",
    "get_llm_client",
    "get_schema_registry",
    "get_settings",
    "get_tenant_registry",
]
