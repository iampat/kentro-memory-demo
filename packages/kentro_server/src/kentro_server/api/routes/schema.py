"""POST /schema/register — register entity types; GET /schema — list registered.

Schema-evolution rules (no rename, no type change, no removal — only add or
deprecate) live inside `SchemaRegistry.register_many` and surface as
`SchemaEvolutionError`. The route translates that to HTTP 409 Conflict.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from kentro_server.api.auth import PrincipalDep
from kentro_server.api.deps import SchemaRegistryDep
from kentro_server.api.dtos import SchemaListResponse, SchemaRegisterRequest
from kentro_server.core.schema_registry import SchemaEvolutionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schema", tags=["schema"])


@router.post("/register", response_model=SchemaListResponse)
def register_schema(
    body: SchemaRegisterRequest,
    schema: SchemaRegistryDep,
    principal: PrincipalDep,
) -> SchemaListResponse:
    """Register one or more entity types. Idempotent for unchanged definitions.

    Returns the post-registration list of all registered types (so callers can
    confirm the auto-seeded `Note` is present without a follow-up GET).
    """
    try:
        schema.register_many(body.type_defs)
    except SchemaEvolutionError as exc:
        logger.info("tenant=%s schema register rejected: %s", principal.tenant_id, exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return SchemaListResponse(type_defs=schema.list_all())


@router.get("", response_model=SchemaListResponse)
def list_schema(schema: SchemaRegistryDep, principal: PrincipalDep) -> SchemaListResponse:
    _ = principal  # auth-gate only; principal not otherwise needed.
    return SchemaListResponse(type_defs=schema.list_all())
