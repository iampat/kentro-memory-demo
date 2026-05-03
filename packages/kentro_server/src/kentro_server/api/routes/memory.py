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
from kentro_server.core.write import write_fields_bulk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

_NOTE_TYPE = "Note"


@router.post("/remember", response_model=WriteResult)
def remember(
    body: RememberRequest,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
) -> WriteResult:
    """Write `(subject, predicate, object_json, source_label)` onto Note atomically.

    All three or four field writes commit in **one transaction** via
    `write_fields_bulk`. ACL is pre-evaluated for every field against the same
    loaded ruleset; if any field would be denied, none are written, and the
    denial reason is returned. Codex 2026-05-03 high finding fix: the previous
    per-field loop could persist subject+predicate then fail on object_json,
    leaving a half-written Note that read as real state.

    Returns the last `WriteResult` (object_json, OR source_label when present)
    so the response shape is unchanged from before. Callers checking
    `status==CONFLICT_RECORDED` and the `conflict_id` should be aware: with
    multiple fields written atomically, EACH field can independently produce
    a conflict; here we only return the last one. The HTTP shape is unchanged
    for v0; a future `/memory/remember` endpoint can return all results if
    needed.
    """
    if not body.subject.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="subject must be a non-empty string",
        )
    # Trigger Note auto-seed if this is the tenant's first use.
    if schema.get(_NOTE_TYPE) is None:
        schema.list_all()

    # Populate `subject` so reads have a value (the v0.1 follow-up from the
    # 2026-05-03 lineage walkthrough). Subject is also the entity_key, so this
    # is technically redundant — but having both lets a UI render the Note
    # without having to know that "the entity_key IS the subject."
    fields: list[tuple[str, str, float | None]] = [
        ("subject", json.dumps(body.subject), body.confidence),
        ("predicate", json.dumps(body.predicate), body.confidence),
        # Single dumps: stores canonical JSON; one decode on read returns original.
        ("object_json", json.dumps(body.object_json), body.confidence),
    ]
    if body.source_label is not None:
        fields.append(("source_label", json.dumps(body.source_label), body.confidence))

    results = write_fields_bulk(
        store=principal.store,
        schema=schema,
        agent_id=principal.agent_id,
        entity_type=_NOTE_TYPE,
        entity_key=body.subject,
        fields=fields,
    )
    if not results:  # pragma: no cover — fields is always non-empty
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="remember: no fields written (internal logic error)",
        )
    # If anything denied, return the first PD (with the meaningful reason).
    for r in results:
        if r.status == WriteStatus.PERMISSION_DENIED:
            return r
    return results[-1]
