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
              predicate     = json.dumps(predicate)
              object_json   = json.dumps(object_json)   # single dumps; see below
              source_label  = json.dumps(source_label)

Encoding contract for `object_json`:
    The persisted `value_json` is the canonical JSON encoding of the caller's
    `object_value`. On read, `core/read.py::_decode` calls `json.loads(value_json)`
    once, so the caller gets back the *original Python value*. The previous
    implementation called `json.dumps` twice, which left the read-side returning
    the inner JSON as an opaque string. Bug fixed; see CHANGE_LOG 2026-05-03.

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
from kentro_server.core.acl import evaluate_write
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

    ACL is evaluated once up-front against the *Note entity* (using `field_name=None`
    to ask "may this agent write to Note at all?"). If denied, return the denial
    immediately without attempting any per-field writes — eliminates the wasted
    round-trip of the previous implementation.

    On allow, two-or-three field writes follow. Each is independent and recorded
    via the standard `write_field` path so corroboration / conflict semantics
    apply uniformly with structured ingestion.
    """
    if not body.subject.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="subject must be a non-empty string",
        )
    # Trigger Note auto-seed if this is the tenant's first use.
    if schema.get(_NOTE_TYPE) is None:
        schema.list_all()

    # ACL once, against the wildcard Note write. If the agent has zero write
    # permission on Note, bail before issuing any writes.
    ruleset = load_active_ruleset(principal.store)
    acl = evaluate_write(
        entity_type=_NOTE_TYPE,
        field_name=None,
        agent_id=principal.agent_id,
        ruleset=ruleset,
    )
    if not acl.allowed:
        return WriteResult(
            status=WriteStatus.PERMISSION_DENIED,
            entity_type=_NOTE_TYPE,
            entity_key=body.subject,
            reason=acl.reason,
        )

    fields_to_write: dict[str, str] = {
        "predicate": json.dumps(body.predicate),
        # Single dumps: stores canonical JSON; one decode on read returns original.
        "object_json": json.dumps(body.object_json),
    }
    if body.source_label is not None:
        fields_to_write["source_label"] = json.dumps(body.source_label)

    last_result: WriteResult | None = None
    for field_name, value_json in fields_to_write.items():
        last_result = write_field(
            store=principal.store,
            schema=schema,
            agent_id=principal.agent_id,
            entity_type=_NOTE_TYPE,
            entity_key=body.subject,
            field_name=field_name,
            value_json=value_json,
            confidence=body.confidence,
        )
        # Per-field PD can still happen if a more specific FieldReadRule for one
        # of the Note fields denies this agent — bail then too, same reasoning.
        if last_result.status == WriteStatus.PERMISSION_DENIED:
            break

    # last_result is non-None: fields_to_write always has predicate + object_json.
    if last_result is None:  # pragma: no cover — defensive, fields_to_write is non-empty
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="remember: no fields written (internal logic error)",
        )
    return last_result
