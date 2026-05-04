"""Visualization helpers — pure data transformations over SDK types.

These power Step 10's two-pane policy editor (right pane = matrix view +
sectioned-by-rule-type panel + diff highlights), the CLI's read-only views,
and any notebook walking the demo. All functions are pure — they take SDK
types in and return SDK-shaped dataclasses out. **No I/O.** The CLI
formatters live in `kentro_server.viz_cli` (Rich-based renderers); the SDK
keeps the data shapes.

Per IMPLEMENTATION_PLAN.md "Decisions locked since the handoff" §2:
- `access_matrix(ruleset, agents, entity_type_defs) -> AccessMatrix` is the
  default right-pane data shape. Rows × cols × cells; the UI renders it as a
  table with iconography. No PBAC syntax — the matrix IS the language.
- `rule_diff(old, new) -> RuleDiffView` wraps `kentro.rules.ruleset_diff` and
  groups the result by rule type for sectioned diff highlights.
- `lineage(record)` flattens an `EntityRecord`'s field-level lineage into a
  per-field grouping suitable for "where did this value come from?" tooltips.
- `conflicts_from_records(records)` scans for UNRESOLVED fields and surfaces
  them as a flat list — the demo's "two writes disagree" panel.

Why "viz" lives in the SDK:
- Pure functions over SDK Pydantic types; no DB, no HTTP.
- Notebooks/UI/CLI all want them.
- The cost of duplicating between SDK and server would be a maintenance
  trap — these are presentation-layer transformations and presentation
  belongs near the types.

Note: `entity_graph` is intentionally NOT in this v0 — see plan §"Deferred
to the very end".
"""

from dataclasses import dataclass
from typing import Literal

from kentro.acl import (
    evaluate_entity_visibility,
    evaluate_field_read,
    evaluate_write,
)
from kentro.rules import RuleSetDiff, ruleset_diff
from kentro.types import (
    EntityRecord,
    EntityTypeDef,
    FieldStatus,
    FieldValueCandidate,
    LineageRecord,
    Rule,
    RuleSet,
)

# === Access matrix ===================================================================
#
# The right-pane default view. Rows = agents, cols = (entity_type, field_name).
# Each cell carries three independent statuses: read, write, visibility. The UI
# renders them as icons in one cell; the data shape keeps them separable so a
# CLI table can emit them as separate columns if it prefers.


CellStatus = Literal["allow", "deny", "hidden"]
"""One of three values: `allow`, `deny`, or `hidden`. `hidden` is reserved for
visibility (entity-level deny doesn't have a "deny" semantics distinct from
"hidden") — for read/write it's always `allow` or `deny`."""


@dataclass(frozen=True)
class AccessMatrixCell:
    """One cell in the access matrix: read, write, visibility status for
    a specific (agent, entity_type, field_name) tuple.

    Visibility is per (agent, entity_type) — the same value is repeated across
    every field cell for that (agent, entity_type) pair. Carrying it in the
    cell keeps the rendering side-effect-free; the UI doesn't have to look up
    visibility separately.
    """

    read: CellStatus
    write: CellStatus
    visibility: CellStatus


@dataclass(frozen=True)
class AccessMatrix:
    """Tabular access decision for every (agent × entity_type × field) tuple.

    `rows` = agent_ids. `cols` = (entity_type, field_name) pairs in declaration
    order. `cells` = `{(agent_id, entity_type, field_name): AccessMatrixCell}`.
    """

    rows: tuple[str, ...]
    cols: tuple[tuple[str, str], ...]
    cells: dict[tuple[str, str, str], AccessMatrixCell]


def access_matrix(
    *,
    ruleset: RuleSet,
    agents: list[str],
    entity_type_defs: list[EntityTypeDef],
) -> AccessMatrix:
    """Build the access matrix for the given (agents × entity_types × fields).

    Pure function — given the same inputs, identical output. Useful for both
    the live UI right pane (after each `apply_ruleset`) and offline analysis
    (e.g. "show me what the v3 ruleset granted vs v4").
    """
    cols: list[tuple[str, str]] = []
    for td in entity_type_defs:
        for f in td.fields:
            cols.append((td.name, f.name))

    cells: dict[tuple[str, str, str], AccessMatrixCell] = {}
    for agent in agents:
        for td in entity_type_defs:
            # Visibility is per (agent, entity_type) — the entity_key is None
            # because we're asking about visibility-of-the-type-in-general.
            # The SDK's `evaluate_entity_visibility` API requires a key, so we
            # pass an empty string; rules with `entity_key=None` (wildcard)
            # match it correctly. Rules with a specific entity_key won't match
            # an empty string — which is fine: the matrix shows the
            # type-level default, not per-entity overrides.
            visibility_decision = evaluate_entity_visibility(
                entity_type=td.name,
                entity_key="",
                agent_id=agent,
                ruleset=ruleset,
            )
            visibility: CellStatus = "allow" if visibility_decision.allowed else "hidden"

            for f in td.fields:
                read_decision = evaluate_field_read(
                    entity_type=td.name,
                    field_name=f.name,
                    agent_id=agent,
                    ruleset=ruleset,
                )
                write_decision = evaluate_write(
                    entity_type=td.name,
                    field_name=f.name,
                    agent_id=agent,
                    ruleset=ruleset,
                )
                cells[(agent, td.name, f.name)] = AccessMatrixCell(
                    read="allow" if read_decision.allowed else "deny",
                    write="allow" if write_decision.allowed else "deny",
                    visibility=visibility,
                )

    return AccessMatrix(
        rows=tuple(agents),
        cols=tuple(cols),
        cells=cells,
    )


# === Rule diff (grouped by type) =====================================================
#
# Wraps the raw `ruleset_diff` set-difference with a grouping by rule type.
# That's what the policy editor's diff panel renders: "+ 2 FieldReadRule, − 1
# WriteRule, ~ 0 ConflictRule changed."


@dataclass(frozen=True)
class RuleDiffSection:
    """One section of a rule diff: one rule type's added/removed/unchanged."""

    rule_type: str  # the discriminator value: "field_read", "write", etc.
    added: tuple[Rule, ...]
    removed: tuple[Rule, ...]
    unchanged: tuple[Rule, ...]


@dataclass(frozen=True)
class RuleDiffView:
    """Rule diff grouped by rule type. Sections appear in a fixed order:
    field_read, entity_visibility, write. (Resolver policies live in
    `ResolverPolicySet`, not the rule diff — see PR 35.)

    `total_added`/`total_removed` are convenient summary counts — UI shows them
    in the panel header ("v3 → v4: +2 −1").
    """

    sections: tuple[RuleDiffSection, ...]
    total_added: int
    total_removed: int


_RULE_TYPE_ORDER = ("field_read", "entity_visibility", "write")
"""Display order for the by-type panel."""


def rule_diff(old: RuleSet, new: RuleSet) -> RuleDiffView:
    """Group a `ruleset_diff` result by rule type for the policy editor's diff panel."""
    raw: RuleSetDiff = ruleset_diff(old, new)

    by_type_added: dict[str, list[Rule]] = {t: [] for t in _RULE_TYPE_ORDER}
    by_type_removed: dict[str, list[Rule]] = {t: [] for t in _RULE_TYPE_ORDER}
    by_type_unchanged: dict[str, list[Rule]] = {t: [] for t in _RULE_TYPE_ORDER}

    for r in raw.added:
        by_type_added.setdefault(r.type, []).append(r)
    for r in raw.removed:
        by_type_removed.setdefault(r.type, []).append(r)
    for r in raw.unchanged:
        by_type_unchanged.setdefault(r.type, []).append(r)

    sections = tuple(
        RuleDiffSection(
            rule_type=t,
            added=tuple(by_type_added.get(t, ())),
            removed=tuple(by_type_removed.get(t, ())),
            unchanged=tuple(by_type_unchanged.get(t, ())),
        )
        for t in _RULE_TYPE_ORDER
    )
    return RuleDiffView(
        sections=sections,
        total_added=len(raw.added),
        total_removed=len(raw.removed),
    )


# === Lineage view ====================================================================
#
# An EntityRecord carries `fields: dict[str, FieldValue]`, and each `FieldValue`
# has a `lineage: tuple[LineageRecord, ...]`. For UI tooltips and "where did this
# come from" panels we want a flat-ish per-field grouping. The "candidates"
# (UNRESOLVED case) carry their own per-candidate lineage; we surface those too.


@dataclass(frozen=True)
class LineageEntry:
    """One lineage record annotated with the value it produced.

    For KNOWN fields, there's typically one entry per field (the winner).
    For UNRESOLVED fields, there's one entry per candidate — `value` is the
    candidate's value, lineage is the LineageRecord that produced it.
    """

    value: object | None
    confidence: float | None
    record: LineageRecord


@dataclass(frozen=True)
class LineageFieldView:
    """All lineage entries for one field, plus its current status."""

    field_name: str
    status: FieldStatus
    entries: tuple[LineageEntry, ...]


@dataclass(frozen=True)
class LineageView:
    """Per-field lineage for an entire EntityRecord."""

    entity_type: str
    entity_key: str
    fields: tuple[LineageFieldView, ...]


def lineage(record: EntityRecord) -> LineageView:
    """Flatten an EntityRecord's lineage into a per-field grouping.

    For KNOWN fields → one LineageEntry per record in `FieldValue.lineage`
    (typically one — the winner).

    For UNRESOLVED fields → one LineageEntry per candidate, carrying the
    candidate's value and its lineage. The UI renders these stacked.

    For HIDDEN/UNKNOWN fields → empty entries tuple. The status itself is
    the interesting bit; lineage doesn't apply.
    """
    fields_views: list[LineageFieldView] = []
    for fname, fv in record.fields.items():
        entries: list[LineageEntry] = []
        if fv.status == FieldStatus.KNOWN:
            for ln in fv.lineage:
                entries.append(LineageEntry(value=fv.value, confidence=fv.confidence, record=ln))
        elif fv.status == FieldStatus.UNRESOLVED:
            for cand in fv.candidates:
                entries.extend(_entries_from_candidate(cand))
        # HIDDEN / UNKNOWN → leave entries empty.
        fields_views.append(
            LineageFieldView(
                field_name=fname,
                status=fv.status,
                entries=tuple(entries),
            )
        )

    return LineageView(
        entity_type=record.entity_type,
        entity_key=record.key,
        fields=tuple(fields_views),
    )


def _entries_from_candidate(cand: FieldValueCandidate) -> list[LineageEntry]:
    return [
        LineageEntry(value=cand.value, confidence=cand.confidence, record=ln)
        for ln in cand.lineage
    ]


# === Conflicts view ==================================================================
#
# Scans a list of EntityRecord for UNRESOLVED fields. The "active resolver hint"
# is best-effort — we don't have access to the live ConflictRule from a record
# alone, so the hint is left empty. UI consumers that want "if you applied
# LatestWriteResolver, here's what would win" can call `read_with(resolver=...)`.


@dataclass(frozen=True)
class ConflictRow:
    """One unresolved field surfaced from an entity read."""

    entity_type: str
    entity_key: str
    field_name: str
    candidates: tuple[FieldValueCandidate, ...]
    reason: str | None


@dataclass(frozen=True)
class ConflictsView:
    """Flat list of unresolved fields across some set of records."""

    rows: tuple[ConflictRow, ...]


def conflicts_from_records(records: list[EntityRecord]) -> ConflictsView:
    """Surface every UNRESOLVED field from a batch of EntityRecords as a flat list.

    The UI typically calls `client.read_with(..., RawResolverSpec())` for each
    entity it wants to inspect, then passes the records here for the conflicts
    panel. Server-side conflict listing (`GET /conflicts` route + DB query)
    is deferred — see plan §"Deferred to the very end".
    """
    rows: list[ConflictRow] = []
    for record in records:
        for fname, fv in record.fields.items():
            if fv.status == FieldStatus.UNRESOLVED:
                rows.append(
                    ConflictRow(
                        entity_type=record.entity_type,
                        entity_key=record.key,
                        field_name=fname,
                        candidates=fv.candidates,
                        reason=fv.reason,
                    )
                )
    return ConflictsView(rows=tuple(rows))


__all__ = [
    "AccessMatrix",
    "AccessMatrixCell",
    "CellStatus",
    "ConflictRow",
    "ConflictsView",
    "LineageEntry",
    "LineageFieldView",
    "LineageView",
    "RuleDiffSection",
    "RuleDiffView",
    "access_matrix",
    "conflicts_from_records",
    "lineage",
    "rule_diff",
]
