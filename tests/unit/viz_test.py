"""Tests for `kentro.viz` — pure data transformations."""

from datetime import UTC, datetime
from uuid import uuid4

from kentro.types import (
    ConflictRule,
    EntityRecord,
    EntityTypeDef,
    EntityVisibilityRule,
    FieldDef,
    FieldReadRule,
    FieldStatus,
    FieldValue,
    FieldValueCandidate,
    LineageRecord,
    RuleSet,
    SkillResolverSpec,
    WriteRule,
)
from kentro.viz import (
    access_matrix,
    conflicts_from_records,
    lineage,
    rule_diff,
)

# === access_matrix ==================================================================


def _customer_def() -> EntityTypeDef:
    return EntityTypeDef(
        name="Customer",
        fields=(
            FieldDef(name="name", type_str="str"),
            FieldDef(name="deal_size", type_str="float | None"),
        ),
    )


def test_access_matrix_default_deny() -> None:
    """Empty ruleset → every cell is deny/deny/hidden (default-deny semantics)."""
    matrix = access_matrix(
        ruleset=RuleSet(rules=()),
        agents=["sales"],
        entity_type_defs=[_customer_def()],
    )
    if matrix.rows != ("sales",):
        raise AssertionError(f"unexpected rows: {matrix.rows}")
    if matrix.cols != (("Customer", "name"), ("Customer", "deal_size")):
        raise AssertionError(f"unexpected cols: {matrix.cols}")
    for cell in matrix.cells.values():
        if cell.read != "deny" or cell.write != "deny" or cell.visibility != "hidden":
            raise AssertionError(f"default-deny violated: {cell}")


def test_access_matrix_explicit_grants() -> None:
    """Allowed read/write/visibility rules show up correctly per cell."""
    rules = (
        EntityVisibilityRule(agent_id="sales", entity_type="Customer", allowed=True),
        FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True),
        WriteRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True),
    )
    matrix = access_matrix(
        ruleset=RuleSet(rules=rules),
        agents=["sales"],
        entity_type_defs=[_customer_def()],
    )

    cell_name = matrix.cells[("sales", "Customer", "name")]
    if cell_name.read != "allow" or cell_name.write != "allow" or cell_name.visibility != "allow":
        raise AssertionError(f"name cell wrong: {cell_name}")

    # deal_size has only visibility (the entity-level rule); read/write are still default-deny.
    cell_deal = matrix.cells[("sales", "Customer", "deal_size")]
    if cell_deal.read != "deny" or cell_deal.write != "deny":
        raise AssertionError(f"deal_size cell wrong: {cell_deal}")
    if cell_deal.visibility != "allow":
        raise AssertionError(f"visibility didn't propagate to deal_size cell: {cell_deal}")


def test_access_matrix_two_agents_two_types() -> None:
    """Cells are populated for every (agent × type × field) combination."""
    matrix = access_matrix(
        ruleset=RuleSet(rules=()),
        agents=["sales", "cs"],
        entity_type_defs=[
            _customer_def(),
            EntityTypeDef(name="Person", fields=(FieldDef(name="email", type_str="str"),)),
        ],
    )
    expected_keys = {
        ("sales", "Customer", "name"),
        ("sales", "Customer", "deal_size"),
        ("sales", "Person", "email"),
        ("cs", "Customer", "name"),
        ("cs", "Customer", "deal_size"),
        ("cs", "Person", "email"),
    }
    if set(matrix.cells.keys()) != expected_keys:
        raise AssertionError(
            f"missing or extra cells: got {set(matrix.cells.keys()) ^ expected_keys}"
        )


# === rule_diff =====================================================================


def test_rule_diff_groups_by_type() -> None:
    """`rule_diff` puts each Rule into the section matching its `.type`."""
    keep_field = FieldReadRule(
        agent_id="sales", entity_type="Customer", field_name="name", allowed=True
    )
    add_write = WriteRule(agent_id="sales", entity_type="Customer", allowed=True)
    drop_visibility = EntityVisibilityRule(agent_id="cs", entity_type="Customer", allowed=False)
    add_conflict = ConflictRule(
        entity_type="Customer",
        field_name="deal_size",
        resolver=SkillResolverSpec(prompt="x"),
    )

    diff = rule_diff(
        old=RuleSet(rules=(keep_field, drop_visibility)),
        new=RuleSet(rules=(keep_field, add_write, add_conflict)),
    )

    by_type = {s.rule_type: s for s in diff.sections}
    # field_read had keep_field unchanged on both sides
    if by_type["field_read"].unchanged != (keep_field,):
        raise AssertionError(f"field_read unchanged wrong: {by_type['field_read']}")
    # write had add_write added
    if by_type["write"].added != (add_write,):
        raise AssertionError(f"write added wrong: {by_type['write']}")
    # entity_visibility had drop_visibility removed
    if by_type["entity_visibility"].removed != (drop_visibility,):
        raise AssertionError(f"visibility removed wrong: {by_type['entity_visibility']}")
    # conflict had add_conflict added
    if by_type["conflict"].added != (add_conflict,):
        raise AssertionError(f"conflict added wrong: {by_type['conflict']}")

    if diff.total_added != 2 or diff.total_removed != 1:
        raise AssertionError(f"summary counts wrong: +{diff.total_added} -{diff.total_removed}")


def test_rule_diff_no_changes() -> None:
    rule = FieldReadRule(agent_id="sales", entity_type="Customer", field_name="name", allowed=True)
    diff = rule_diff(RuleSet(rules=(rule,)), RuleSet(rules=(rule,)))
    if diff.total_added != 0 or diff.total_removed != 0:
        raise AssertionError(f"expected zero changes, got {diff!r}")


# === lineage =======================================================================


def _line(agent: str, when: datetime, doc: str | None = None) -> LineageRecord:
    return LineageRecord(
        source_document_id=uuid4() if doc else None,
        written_at=when,
        written_by_agent_id=agent,
        rule_version=3,
    )


def test_lineage_known_field_one_entry() -> None:
    """KNOWN field → one LineageEntry per LineageRecord (typically just one)."""
    when = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    record = EntityRecord(
        entity_type="Customer",
        key="Acme",
        fields={
            "name": FieldValue(
                status=FieldStatus.KNOWN,
                value="Acme",
                lineage=(_line("ingestion_agent", when),),
            ),
        },
    )
    view = lineage(record)
    if len(view.fields) != 1:
        raise AssertionError(f"expected 1 field view, got {len(view.fields)}")
    fv = view.fields[0]
    if fv.field_name != "name" or fv.status != FieldStatus.KNOWN:
        raise AssertionError(f"unexpected field view: {fv!r}")
    if len(fv.entries) != 1:
        raise AssertionError(f"expected 1 entry for KNOWN, got {len(fv.entries)}")
    if fv.entries[0].value != "Acme":
        raise AssertionError(f"entry value wrong: {fv.entries[0]!r}")


def test_lineage_unresolved_field_one_entry_per_candidate() -> None:
    """UNRESOLVED field → one LineageEntry per candidate (each carries its own lineage)."""
    a = _line("ingestion_agent", datetime(2026, 5, 3, 12, 0, tzinfo=UTC))
    b = _line("ingestion_agent", datetime(2026, 5, 3, 13, 0, tzinfo=UTC))
    record = EntityRecord(
        entity_type="Customer",
        key="Acme",
        fields={
            "deal_size": FieldValue(
                status=FieldStatus.UNRESOLVED,
                candidates=(
                    FieldValueCandidate(value=250000, lineage=(a,)),
                    FieldValueCandidate(value=300000, lineage=(b,)),
                ),
                reason="raw resolver requested",
            ),
        },
    )
    view = lineage(record)
    fv = view.fields[0]
    if fv.status != FieldStatus.UNRESOLVED:
        raise AssertionError(f"expected UNRESOLVED, got {fv.status}")
    if len(fv.entries) != 2:
        raise AssertionError(f"expected 2 entries (one per candidate), got {len(fv.entries)}")
    values = {e.value for e in fv.entries}
    if values != {250000, 300000}:
        raise AssertionError(f"expected both candidate values, got {values}")


def test_lineage_hidden_and_unknown_fields_have_no_entries() -> None:
    record = EntityRecord(
        entity_type="Customer",
        key="Acme",
        fields={
            "secret": FieldValue(status=FieldStatus.HIDDEN, reason="redacted"),
            "missing": FieldValue(status=FieldStatus.UNKNOWN),
        },
    )
    view = lineage(record)
    for fv in view.fields:
        if fv.entries:
            raise AssertionError(f"non-KNOWN/UNRESOLVED should have no entries, got {fv!r}")


# === conflicts_from_records ========================================================


def test_conflicts_from_records_surfaces_only_unresolved() -> None:
    """Only UNRESOLVED fields end up in the ConflictsView."""
    when = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    records = [
        EntityRecord(
            entity_type="Customer",
            key="Acme",
            fields={
                "name": FieldValue(
                    status=FieldStatus.KNOWN, value="Acme", lineage=(_line("a", when),)
                ),
                "deal_size": FieldValue(
                    status=FieldStatus.UNRESOLVED,
                    candidates=(
                        FieldValueCandidate(value=250000, lineage=(_line("a", when),)),
                        FieldValueCandidate(value=300000, lineage=(_line("a", when),)),
                    ),
                    reason="raw",
                ),
            },
        ),
        EntityRecord(
            entity_type="Customer",
            key="Globex",
            fields={
                "name": FieldValue(
                    status=FieldStatus.KNOWN, value="Globex", lineage=(_line("a", when),)
                ),
            },
        ),
    ]
    view = conflicts_from_records(records)
    if len(view.rows) != 1:
        raise AssertionError(f"expected 1 conflict row, got {len(view.rows)}")
    row = view.rows[0]
    if row.entity_type != "Customer" or row.entity_key != "Acme" or row.field_name != "deal_size":
        raise AssertionError(f"wrong conflict row: {row!r}")
    if len(row.candidates) != 2:
        raise AssertionError(f"expected 2 candidates, got {len(row.candidates)}")


def test_conflicts_from_records_empty() -> None:
    view = conflicts_from_records([])
    if view.rows != ():
        raise AssertionError(f"expected empty rows, got {view!r}")
