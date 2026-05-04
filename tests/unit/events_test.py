"""EventBus thread-safety tests.

Codex 2026-05-03 medium finding: `EventBus.publish()` is invoked from sync
FastAPI route handlers, which run in a worker thread. Direct
`asyncio.Queue.put_nowait` from a non-loop thread is unsafe; the bus must
route per-subscriber puts through `loop.call_soon_threadsafe`.

These tests construct an `EventBus` on a real running loop and publish from
a worker thread (`asyncio.to_thread`). Each subscriber must receive the
event without race-induced loss.
"""

import asyncio

import pytest
from kentro_server.core.events import Event, EventBus


@pytest.mark.asyncio
async def test_publish_from_worker_thread_delivers_to_subscribers() -> None:
    """`publish()` invoked off the loop must still enqueue events safely."""
    bus = EventBus()
    q1 = await bus.subscribe()
    q2 = await bus.subscribe()

    event = Event(kind="notify", tenant_id="local", payload={"msg": "hi"})

    # Publish from a worker thread to exercise the cross-thread path.
    delivered = await asyncio.to_thread(bus.publish, event)
    if delivered != 2:
        raise AssertionError(f"expected 2 subscribers targeted, got {delivered}")

    received1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    received2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    if received1.kind != "notify" or received2.kind != "notify":
        raise AssertionError(
            f"both subscribers should receive the event, got {received1!r} and {received2!r}"
        )
    if received1.payload.get("msg") != "hi":
        raise AssertionError(f"payload mismatch: {received1.payload!r}")


@pytest.mark.asyncio
async def test_publish_burst_from_worker_thread_no_loss() -> None:
    """Burst-publishing from a worker thread should land every event on each subscriber.

    A flaky thread-unsafe implementation would occasionally drop or duplicate
    when N events race; we verify exact delivery for a small burst.
    """
    bus = EventBus()
    q = await bus.subscribe()

    n = 16
    events = [Event(kind="notify", tenant_id="local", payload={"i": i}) for i in range(n)]

    def burst() -> None:
        for e in events:
            bus.publish(e)

    await asyncio.to_thread(burst)

    received = []
    for _ in range(n):
        received.append(await asyncio.wait_for(q.get(), timeout=1.0))

    received_indices = [r.payload["i"] for r in received]
    if received_indices != list(range(n)):
        raise AssertionError(f"expected {list(range(n))} in order, got {received_indices!r}")


@pytest.mark.asyncio
async def test_publish_from_loop_thread_still_works() -> None:
    """Same-loop publish should still deliver via `call_soon_threadsafe` scheduling."""
    bus = EventBus()
    q = await bus.subscribe()

    bus.publish(Event(kind="rule_applied", tenant_id="local", payload={"version": 3}))

    received = await asyncio.wait_for(q.get(), timeout=1.0)
    if received.kind != "rule_applied":
        raise AssertionError(f"expected rule_applied, got {received!r}")
    if received.payload.get("version") != 3:
        raise AssertionError(f"payload mismatch: {received.payload!r}")


def test_constructing_eventbus_outside_loop_raises() -> None:
    """Constructing without a running loop must fail loudly — captured loop is required."""
    try:
        EventBus()
    except RuntimeError as exc:
        if "running event loop" not in str(exc):
            raise AssertionError(f"unexpected error message: {exc!r}") from exc
        return
    raise AssertionError("EventBus() outside a running loop should raise RuntimeError")
