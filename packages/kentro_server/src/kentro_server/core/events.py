"""In-process event bus for SkillResolver workflow notifications.

Why this exists: a `SkillResolver` can emit `NotifyAction(channel, message)`
items as part of its decision. The UI's `<EscalationToast>` listens for them
via Server-Sent Events on `GET /events`. This module is the in-process
broadcaster — async asyncio.Queue per subscriber, one publisher feeding all.

For v0 this is intentionally per-process (single uvicorn worker) and tenant-
scoped (every event carries `tenant_id` so the SSE endpoint can filter).
v0.1 swaps to Redis pub/sub if multi-worker becomes a thing — until then,
in-process is the right size.

The bus is attached to FastAPI app state in the lifespan, NOT a module
singleton. Per CLAUDE.md "no singletons" — explicit dependency injection.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    """One event sent over /events SSE.

    `kind` is a stable identifier the UI dispatches on:
      - "notify": a NotifyAction fired (channel, message in payload)
      - "rule_applied": admin posted a new ruleset (version, applied_by in payload)
      - "entity_written": an entity_field write landed (entity_type, key, field in payload)

    `tenant_id` is set so SSE subscribers can filter events to their tenant
    only — multi-tenant safety even though we're single-process.
    """

    kind: str
    tenant_id: str
    payload: dict[str, Any]
    ts: str = field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat(timespec="milliseconds")
    )


class EventBus:
    """Fan-out broadcaster: each subscriber gets its own asyncio.Queue.

    Publishers call `publish(Event)` synchronously (no await). The bus puts
    the event onto every active subscriber queue best-effort — a slow
    subscriber gets dropped events when its queue fills (queue maxsize=64).
    Demo pattern, not production durability.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Event]:
        """Register a new subscriber and return its queue."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        async with self._lock:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(q)

    def publish(self, event: Event) -> int:
        """Synchronously fan-out. Returns the number of subscribers reached.

        Best-effort: if a subscriber queue is full, the event is dropped for
        that subscriber and a warning logs (the subscriber missed an event;
        the rest still got it). Use `asyncio.create_task` to call this from
        sync code by wrapping in `loop.call_soon_threadsafe(...)` if needed.
        """
        delivered = 0
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(
                    "EventBus: subscriber queue full, dropping event kind=%s ts=%s",
                    event.kind,
                    event.ts,
                )
        return delivered


__all__ = ["Event", "EventBus"]
