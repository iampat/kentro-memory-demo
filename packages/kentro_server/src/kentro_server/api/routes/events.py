"""GET /events — Server-Sent Events stream of EventBus events for the active tenant.

Used by the demo UI's `<EscalationToast>` to render `notify` events fired by
SkillResolver `NotifyAction`s. Tenant-scoped via the bearer auth — events for
other tenants are filtered out before they hit the wire.

SSE rather than websocket: simpler, works through the same HTTP path as every
other route, no upgrade handshake, plays nice with FastAPI's StreamingResponse.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from kentro_server.api.auth import PrincipalDep
from kentro_server.api.deps import EventBusDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])


def _format_sse(data: str, *, event: str | None = None) -> str:
    """Format a single SSE message. Spec: each line prefixed with `event:` or
    `data:`, terminated by a blank line."""
    out = ""
    if event:
        out += f"event: {event}\n"
    for line in data.splitlines() or [""]:
        out += f"data: {line}\n"
    out += "\n"
    return out


@router.get("")
async def stream_events(principal: PrincipalDep, bus: EventBusDep) -> StreamingResponse:
    """Stream EventBus events for the principal's tenant as SSE.

    Heartbeat every 30s (a `: ping` comment) to keep proxies from idling the
    connection out. Subscriber unsubscribes automatically when the client
    disconnects (FastAPI cancels the generator).
    """
    queue = await bus.subscribe()
    tenant_id = principal.store.tenant_id

    async def gen() -> AsyncIterator[str]:
        # Initial hello so the client knows the stream is live before any
        # actual event fires.
        yield _format_sse(json.dumps({"kind": "hello", "tenant_id": tenant_id}), event="hello")
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except TimeoutError:
                    # Heartbeat — SSE comments start with `:` and are ignored
                    # by EventSource clients but keep TCP alive.
                    yield ": ping\n\n"
                    continue
                if event.tenant_id != tenant_id:
                    continue  # not our tenant
                payload = {
                    "kind": event.kind,
                    "tenant_id": event.tenant_id,
                    "ts": event.ts,
                    **event.payload,
                }
                yield _format_sse(json.dumps(payload), event=event.kind)
        finally:
            await bus.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx response buffering if proxied
        },
    )
