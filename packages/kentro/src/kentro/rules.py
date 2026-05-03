"""Pure helpers over `RuleSet` / `Rule` — diff and one-line rendering.

These live in the SDK package so any consumer (notebook, CLI, future UI) can
use them without a server roundtrip. The server can also `from kentro.rules
import ...` if it wants to render in CLI/logs — they're pure functions over
SDK-defined Pydantic types.

Per IMPLEMENTATION_PLAN.md "Decisions locked since the handoff" §2.7, these
two helpers are the substrate for the policy editor's:
- *diff highlights* (added/removed rules between versions) → `ruleset_diff`
- *human-readable rendering* (one line per rule, no DSL needed) → `render_rule`
"""

from dataclasses import dataclass

from kentro.types import (
    ConflictRule,
    EntityVisibilityRule,
    FieldReadRule,
    Rule,
    RuleSet,
    WriteRule,
)


@dataclass(frozen=True)
class RuleSetDiff:
    """Set-difference between two `RuleSet`s.

    Compared by canonical JSON (frozen Pydantic models, `model_dump_json()`
    is stable). Two rules with identical content are "unchanged" regardless of
    their position in `RuleSet.rules`.

    Use for: rendering version-to-version diffs in the policy editor (Step 10
    UI), and the `kentro-server rules diff vN vM` CLI (when added).
    """

    added: tuple[Rule, ...]
    removed: tuple[Rule, ...]
    unchanged: tuple[Rule, ...]


def ruleset_diff(old: RuleSet, new: RuleSet) -> RuleSetDiff:
    """Return the set-difference of two RuleSets.

    Rules are compared by their canonical JSON serialization, not by tuple
    position. Order within `RuleSet.rules` is irrelevant for the diff.
    """
    old_keys = {r.model_dump_json(): r for r in old.rules}
    new_keys = {r.model_dump_json(): r for r in new.rules}

    added = tuple(new_keys[k] for k in new_keys.keys() - old_keys.keys())
    removed = tuple(old_keys[k] for k in old_keys.keys() - new_keys.keys())
    unchanged = tuple(new_keys[k] for k in new_keys.keys() & old_keys.keys())
    return RuleSetDiff(added=added, removed=removed, unchanged=unchanged)


def render_rule(rule: Rule) -> str:
    """One-line, human-readable rendering of a single Rule.

    Output convention (designed in IMPLEMENTATION_PLAN.md Decision 2.5):
        [allow]  sales reads  Customer.deal_size
        [deny]   cs    reads  Customer.deal_size
        [allow]  sales writes Customer.contact (requires_approval)
        [deny]   cs    writes Customer.*
        [hidden] sales sees   Customer/Acme
        [allow]  sales sees   Customer.*
        [skill]  resolves     Customer.deal_size → "written outweighs verbal"
        [auto]   resolves     Customer.contact (delegates to active config)

    Stable-format: each line is `[<status>] <subject> <verb> <object> [<extra>]`.
    Suitable for CLI output, audit logs, and as a fallback when the matrix view
    can't render (deep nesting, very small viewports).
    """
    match rule:
        case FieldReadRule(agent_id=agent, entity_type=etype, field_name=fname, allowed=allowed):
            tag = "[allow]" if allowed else "[deny] "
            return f"{tag} {agent} reads  {etype}.{fname}"

        case WriteRule(
            agent_id=agent,
            entity_type=etype,
            field_name=fname,
            allowed=allowed,
            requires_approval=approval,
        ):
            tag = "[allow]" if allowed else "[deny] "
            target = f"{etype}.{fname}" if fname is not None else f"{etype}.*"
            suffix = " (requires_approval)" if approval else ""
            return f"{tag} {agent} writes {target}{suffix}"

        case EntityVisibilityRule(
            agent_id=agent, entity_type=etype, entity_key=key, allowed=allowed
        ):
            tag = "[allow] " if allowed else "[hidden]"
            target = f"{etype}/{key}" if key is not None else f"{etype}.*"
            return f"{tag} {agent} sees   {target}"

        case ConflictRule(entity_type=etype, field_name=fname, resolver=resolver):
            # ResolverSpec is a discriminated union with `type` field.
            rtype = resolver.type
            target = f"{etype}.{fname}"
            match rtype:
                case "skill":
                    # SkillResolverSpec has prompt: str
                    prompt = getattr(resolver, "prompt", "?")
                    return f"[skill]  resolves     {target} → {prompt!r}"
                case "prefer_agent":
                    agent = getattr(resolver, "agent_id", "?")
                    return f"[prefer] resolves     {target} (agent={agent})"
                case "latest_write":
                    return f"[latest] resolves     {target} (newest write wins)"
                case "raw":
                    return f"[raw]    resolves     {target} (always UNRESOLVED — caller decides)"
                case "auto":
                    return f"[auto]   resolves     {target} (delegates to active config)"
                case _:
                    return f"[?]      resolves     {target} (unknown resolver: {rtype})"


__all__ = ["RuleSetDiff", "render_rule", "ruleset_diff"]
