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


def render_rule_as_rego(rule: Rule) -> str:
    """Render a `Rule` as a Rego-flavored snippet — presentation only, never parsed.

    The Rego output is for **display sophistication** in the policy editor (see
    IMPLEMENTATION_PLAN.md Decision 2.4 — we explicitly rejected adopting Cedar
    or OPA Rego as the policy language). The server still operates on typed
    `Rule` instances; this helper produces a string that *looks like* a Rego
    snippet so the UI can show "this is policy-engine territory" without
    committing to one. The string is never evaluated.

    Output shape per rule type (matches the conventions used in the demo
    prototype's `data.js`):

        FieldReadRule (allow):
            package kentro.access
            allow {
              input.role == "sales"
              input.action == "read"
              input.resource.type == "Customer"
              input.resource.field == "name"
            }

        FieldReadRule (deny):
            package kentro.access
            deny[msg] {
              input.role == "sales"
              input.action == "read"
              input.resource.type == "Customer"
              input.resource.field == "deal_size"
              msg := "field denied"
            }

        WriteRule:
            package kentro.access
            allow {
              input.role == "sales"
              input.action == "write"
              input.resource.type == "Customer"
              input.resource.field == "deal_size"
            }
            (or `input.resource.field` omitted when the rule's field_name is None)

        EntityVisibilityRule:
            package kentro.access
            deny[msg] {
              input.role == "sales"
              input.resource.type == "AuditLog"
              msg := "AuditLog not visible"
            }

        ConflictRule (skill resolver, written-outweighs-verbal flavor):
            package kentro.resolve
            resolved[field] = winner {
              candidates := input.field.values
              written := [c | c := candidates[_]; c.sourceClass == "written"]
              count(written) > 0
              winner := latest(written)
            }
    """
    match rule:
        case FieldReadRule(agent_id=agent, entity_type=etype, field_name=fname, allowed=True):
            return (
                "package kentro.access\n\n"
                "allow {\n"
                f'  input.role == "{agent}"\n'
                '  input.action == "read"\n'
                f'  input.resource.type == "{etype}"\n'
                f'  input.resource.field == "{fname}"\n'
                "}"
            )

        case FieldReadRule(agent_id=agent, entity_type=etype, field_name=fname, allowed=False):
            return (
                "package kentro.access\n\n"
                "deny[msg] {\n"
                f'  input.role == "{agent}"\n'
                '  input.action == "read"\n'
                f'  input.resource.type == "{etype}"\n'
                f'  input.resource.field == "{fname}"\n'
                '  msg := "field denied"\n'
                "}"
            )

        case WriteRule(agent_id=agent, entity_type=etype, field_name=fname, allowed=allowed):
            verb = "allow" if allowed else "deny[msg]"
            field_clause = f'\n  input.resource.field == "{fname}"' if fname is not None else ""
            msg_clause = '\n  msg := "write denied"' if not allowed else ""
            return (
                "package kentro.access\n\n"
                f"{verb} {{\n"
                f'  input.role == "{agent}"\n'
                '  input.action == "write"\n'
                f'  input.resource.type == "{etype}"'
                f"{field_clause}"
                f"{msg_clause}\n"
                "}"
            )

        case EntityVisibilityRule(
            agent_id=agent, entity_type=etype, entity_key=key, allowed=allowed
        ):
            verb = "allow" if allowed else "deny[msg]"
            key_clause = f'\n  input.resource.key == "{key}"' if key is not None else ""
            msg_clause = f'\n  msg := "{etype} not visible"' if not allowed else ""
            return (
                "package kentro.access\n\n"
                f"{verb} {{\n"
                f'  input.role == "{agent}"\n'
                f'  input.resource.type == "{etype}"'
                f"{key_clause}"
                f"{msg_clause}\n"
                "}"
            )

        case ConflictRule(entity_type=etype, field_name=fname, resolver=resolver):
            target = f"{etype}.{fname}"
            match resolver.type:
                case "skill":
                    prompt = getattr(resolver, "prompt", "")
                    # Heuristic: if the prompt mentions "written" explicitly,
                    # render the demo's canonical written-vs-verbal Rego shape.
                    # Otherwise fall back to a generic skill-driven snippet.
                    if "written" in prompt.lower() and "verbal" in prompt.lower():
                        return (
                            "package kentro.resolve\n\n"
                            f"# {target}: {prompt}\n"
                            "resolved[field] = winner {\n"
                            "  candidates := input.field.values\n"
                            '  written := [c | c := candidates[_]; c.sourceClass == "written"]\n'
                            "  count(written) > 0\n"
                            "  winner := latest(written)\n"
                            "}\n\n"
                            "resolved[field] = winner {\n"
                            "  candidates := input.field.values\n"
                            '  written := [c | c := candidates[_]; c.sourceClass == "written"]\n'
                            "  count(written) == 0\n"
                            "  winner := latest(candidates)\n"
                            "}"
                        )
                    return (
                        "package kentro.resolve\n\n"
                        f'# {target}: skill-driven resolution — "{prompt}"\n'
                        "resolved[field] = winner {\n"
                        "  candidates := input.field.values\n"
                        "  winner := skill_pick(candidates)\n"
                        "}"
                    )
                case "prefer_agent":
                    agent_id = getattr(resolver, "agent_id", "")
                    return (
                        "package kentro.resolve\n\n"
                        f"# {target}: prefer agent={agent_id}, fall back to latest\n"
                        "resolved[field] = winner {\n"
                        "  candidates := input.field.values\n"
                        f'  preferred := [c | c := candidates[_]; c.agent == "{agent_id}"]\n'
                        "  count(preferred) > 0\n"
                        "  winner := latest(preferred)\n"
                        "}"
                    )
                case "latest_write":
                    return (
                        "package kentro.resolve\n\n"
                        f"# {target}: latest write wins (mechanical)\n"
                        "resolved[field] = winner {\n"
                        "  candidates := input.field.values\n"
                        "  winner := candidates[_]\n"
                        "  not exists_newer(winner, candidates)\n"
                        "}"
                    )
                case "raw":
                    return (
                        "package kentro.resolve\n\n"
                        f"# {target}: raw — no winner picked, caller decides\n"
                        "unresolved[field] = candidates {\n"
                        "  candidates := input.field.values\n"
                        "  count(candidates) > 1\n"
                        "}"
                    )
                case "auto":
                    return (
                        "package kentro.resolve\n\n"
                        f"# {target}: auto — delegates to the active ConflictRule for this field;\n"
                        "# falls back to LatestWriteResolver if none is configured.\n"
                        "resolved[field] = winner {\n"
                        "  rule := active_conflict_rule(input.field.entity_type, input.field.name)\n"
                        "  winner := apply_resolver(rule.resolver, input.field.values)\n"
                        "}"
                    )
                case _:
                    return (
                        "package kentro.resolve\n\n"
                        f"# {target}: unknown resolver type {resolver.type!r}\n"
                    )
        case _:
            raise TypeError(f"render_rule_as_rego: unknown rule type {type(rule).__name__}")


__all__ = ["RuleSetDiff", "render_rule", "render_rule_as_rego", "ruleset_diff"]
