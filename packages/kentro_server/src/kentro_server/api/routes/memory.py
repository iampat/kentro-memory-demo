"""POST /memory/remember — agent-friendly shortcut for the catch-all `Note` entity.

The structured-extraction path (POST /documents) and per-field write path (POST
/entities/.../{field}) require schemas and pre-known fields. `remember` exists
because LLM agents and chat UIs frequently need to stash *facts that don't fit
the registered schema* — "the demo is at 2pm tomorrow", "Customer Acme uses
Postgres 16". The `Note` entity (auto-seeded into every tenant by
`SchemaRegistry`) is the catch-all home for those.

Wire shape:
    {subject, predicate, object_json, confidence?, source_label?}
        → writes to Note entity keyed by `subject`, three fields:
              predicate     = predicate
              object_json   = json.dumps(object_json)
              source_label  = source_label

Returns a regular `WriteResult`. The route is auth-gated like every other
write — the authenticated agent is the recorded writer.
"""

import json
import logging

from fastapi import APIRouter, HTTPException, status
from kentro.types import WriteResult, WriteStatus

from kentro_server.api.auth import PrincipalDep
from kentro_server.api.deps import SchemaRegistryDep
from kentro_server.api.dtos import RememberRequest
from kentro_server.core.rules import load_active_ruleset
from kentro_server.core.write import write_field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

_NOTE_TYPE = "Note"


@router.post("/remember", response_model=WriteResult)
def remember(
    body: RememberRequest,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
) -> WriteResult:
    """Write `(predicate, object_json, source_label)` onto Note keyed by `subject`.

    Three writes are issued in sequence; if any of them is denied or recorded as
    a conflict, the response carries that field's outcome — but the others are
    still attempted (the Note entity is meant to be additive, and partial
    success is observable through subsequent reads).
    """
    if not body.subject.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="subject must be a non-empty string",
        )
    if schema.get(_NOTE_TYPE) is None:
        # Note is auto-seeded by SchemaRegistry.list_all(); call it once to seed.
        schema.list_all()

    ruleset = load_active_ruleset(principal.store)
    fields_to_write = {
        "predicate": json.dumps(body.predicate),
        "object_json": json.dumps(json.dumps(body.object_json)),
    }
    if body.source_label is not None:
        fields_to_write["source_label"] = json.dumps(body.source_label)

    last_result: WriteResult | None = None
    for field_name, value_json in fields_to_write.items():
        last_result = write_field(
            store=principal.store,
            schema=schema,
            ruleset_version=ruleset.version,
            agent_id=principal.agent_id,
            entity_type=_NOTE_TYPE,
            entity_key=body.subject,
            field_name=field_name,
            value_json=value_json,
            confidence=body.confidence,
        )
        # Stop if the FIRST write was permission-denied — that means this agent
        # cannot write to Note at all; bailing avoids two more identical denials.
        if last_result.status == WriteStatus.PERMISSION_DENIED:
            break

    # last_result is non-None: fields_to_write always has predicate + object_json.
    if last_result is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="remember: no fields written (internal logic error)",
        )
    return last_result
