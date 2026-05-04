"""SDK resolver wrapper classes.

These are the user-facing handles the SDK accepts:

    agent.read("Customer", "Acme", resolver=SkillResolver(prompt="..."))

Each class wraps a `*ResolverSpec` from `kentro.types`. The actual resolution logic
runs server-side; the SDK serializes the spec into the wire request via `.to_spec()`.
"""

from kentro.types import (
    AutoResolverSpec,
    LatestWriteResolverSpec,
    PreferAgentResolverSpec,
    RawResolverSpec,
    ResolverSpec,
    SkillResolverSpec,
)


class Resolver:
    """Base — every resolver knows how to express itself as a wire-form spec."""

    def to_spec(self) -> ResolverSpec:
        raise NotImplementedError


class RawResolver(Resolver):
    """Return all candidates without picking a winner.

    The server will return `FieldValue(status=UNRESOLVED, candidates=...)`. Useful when
    the caller wants to render both values and let a human decide (the demo's `viz.conflicts()`
    panel uses this).
    """

    def to_spec(self) -> RawResolverSpec:
        return RawResolverSpec()


class LatestWriteResolver(Resolver):
    """Pick the most recent write by `written_at`. Mechanical default."""

    def to_spec(self) -> LatestWriteResolverSpec:
        return LatestWriteResolverSpec()


class PreferAgent(Resolver):
    """Prefer writes from `agent_id`; latest of those wins. UNRESOLVED if no match."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    def to_spec(self) -> PreferAgentResolverSpec:
        return PreferAgentResolverSpec(agent_id=self.agent_id)


class SkillResolver(Resolver):
    """LLM-driven domain-policy resolution.

    `prompt` is the policy ("written outweighs verbal, latest among written wins").
    `model` is an optional override; defaults to the server's `fast` LLM tier.

    If the LLM cannot decide, the server returns `FieldValue(status=UNRESOLVED, reason=...)`.
    The SDK never asks back — agent layers handle the unresolved case.
    """

    def __init__(self, prompt: str, model: str | None = None) -> None:
        self.prompt = prompt
        self.model = model

    def to_spec(self) -> SkillResolverSpec:
        return SkillResolverSpec(prompt=self.prompt, model=self.model)


class AutoResolver(Resolver):
    """Use whatever resolver the active `ResolverPolicy` specifies for this field.

    If no `ResolverPolicy` matches the (entity_type, field_name), the server
    falls back to `LatestWriteResolver`. This is the SDK's default
    `resolver=` argument on `agent.read(...)`.
    """

    def to_spec(self) -> AutoResolverSpec:
        return AutoResolverSpec()


__all__ = [
    "AutoResolver",
    "LatestWriteResolver",
    "PreferAgent",
    "RawResolver",
    "Resolver",
    "SkillResolver",
]
