"""split_resolvers_per_type_rows_no_wildcards

PR 35 — separate resolvers from ACL, store one row per (version, entity_type),
remove wildcard write rules.

Revision ID: c1a83f240d92
Revises: bac115dd4042
Create Date: 2026-05-04 19:00:00.000000

Steps:
  1. Create `entity_type_rules` and `entity_type_resolvers` tables (each one
     row per (rule_version, entity_type) holding a JSON array).
  2. Walk every existing `rule_record` row, group by (rule_version,
     entity_type, rule_type-bucket):
       - rules with rule_type in {field_read, entity_visibility, write} go
         into `entity_type_rules`. Wildcard `write` rows (field_name IS NULL)
         are expanded against the schema's field list, one new row per field.
       - rules with rule_type='conflict' go into `entity_type_resolvers`,
         payload kept as `{"entity_type", "field_name", "resolver"}`.
  3. Drop `rule_record`.

Idempotency note: alembic only runs this migration once per DB (versioned by
revision id). On a fresh DB the migration just creates the new tables — no
rule_record rows to walk.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy import text

revision: str = "c1a83f240d92"
down_revision: str | Sequence[str] | None = "bac115dd4042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create the two new tables.
    op.create_table(
        "entity_type_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("entity_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("rules_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["rule_version"], ["rule_version.version"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_version", "entity_type", name="uq_entity_type_rules"),
    )
    with op.batch_alter_table("entity_type_rules", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_entity_type_rules_rule_version"), ["rule_version"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_entity_type_rules_entity_type"), ["entity_type"], unique=False
        )

    op.create_table(
        "entity_type_resolvers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("entity_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("resolvers_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["rule_version"], ["rule_version.version"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_version", "entity_type", name="uq_entity_type_resolvers"),
    )
    with op.batch_alter_table("entity_type_resolvers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_entity_type_resolvers_rule_version"), ["rule_version"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_entity_type_resolvers_entity_type"), ["entity_type"], unique=False
        )

    # 2. Walk existing data — only matters for non-empty DBs (existing tenants).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rule_record" not in inspector.get_table_names():
        # Fresh DB — nothing to migrate.
        return

    # Pull schema definitions so we can expand wildcard write rules.
    schema_rows = bind.execute(text("SELECT name, definition_json FROM schema_type")).fetchall()
    fields_by_type: dict[str, list[str]] = {}
    for name, def_json in schema_rows:
        try:
            def_obj = json.loads(def_json)
            fields_by_type[name] = [f["name"] for f in def_obj.get("fields", [])]
        except (json.JSONDecodeError, KeyError, TypeError):
            fields_by_type[name] = []

    # Group existing rule_record rows.
    rule_rows = bind.execute(
        text("SELECT rule_version, rule_type, payload_json FROM rule_record")
    ).fetchall()

    # (version, entity_type) -> list of rule dicts (ACL only)
    rules_buckets: dict[tuple[int, str], list[dict]] = {}
    # (version, entity_type) -> list of resolver-policy dicts
    resolvers_buckets: dict[tuple[int, str], list[dict]] = {}

    for rule_version, rule_type, payload_json in rule_rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        entity_type = payload.get("entity_type")
        if not entity_type:
            continue

        if rule_type == "conflict":
            # Move to resolver_policy. Drop the `type` discriminator (it was
            # the Rule discriminator, not part of the new shape).
            policy = {
                "entity_type": entity_type,
                "field_name": payload.get("field_name"),
                "resolver": payload.get("resolver"),
            }
            resolvers_buckets.setdefault((rule_version, entity_type), []).append(policy)
        elif rule_type == "write" and payload.get("field_name") is None:
            # Expand wildcard write rule into one rule per field.
            for fname in fields_by_type.get(entity_type, []):
                expanded = dict(payload)
                expanded["field_name"] = fname
                rules_buckets.setdefault((rule_version, entity_type), []).append(expanded)
        else:
            rules_buckets.setdefault((rule_version, entity_type), []).append(payload)

    # 3. Insert into the new tables.
    import uuid as _uuid

    for (rule_version, entity_type), rules in rules_buckets.items():
        bind.execute(
            text(
                "INSERT INTO entity_type_rules (id, rule_version, entity_type, rules_json) "
                "VALUES (:id, :v, :t, :j)"
            ),
            {
                "id": str(_uuid.uuid4()),
                "v": rule_version,
                "t": entity_type,
                "j": json.dumps(rules),
            },
        )
    for (rule_version, entity_type), policies in resolvers_buckets.items():
        bind.execute(
            text(
                "INSERT INTO entity_type_resolvers (id, rule_version, entity_type, resolvers_json) "
                "VALUES (:id, :v, :t, :j)"
            ),
            {
                "id": str(_uuid.uuid4()),
                "v": rule_version,
                "t": entity_type,
                "j": json.dumps(policies),
            },
        )

    # 4. Drop the old rule_record table.
    with op.batch_alter_table("rule_record", schema=None) as batch_op:
        batch_op.drop_index("ix_rule_record_rule_type")
        batch_op.drop_index("ix_rule_record_rule_version")
    op.drop_table("rule_record")


def downgrade() -> None:
    """Downgrade schema. Lossy — drops the new tables and recreates the old
    `rule_record` shape. Not a full data restoration."""
    op.create_table(
        "rule_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("rule_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("payload_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["rule_version"], ["rule_version.version"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("rule_record", schema=None) as batch_op:
        batch_op.create_index("ix_rule_record_rule_type", ["rule_type"], unique=False)
        batch_op.create_index("ix_rule_record_rule_version", ["rule_version"], unique=False)

    with op.batch_alter_table("entity_type_resolvers", schema=None) as batch_op:
        batch_op.drop_index("ix_entity_type_resolvers_entity_type")
        batch_op.drop_index("ix_entity_type_resolvers_rule_version")
    op.drop_table("entity_type_resolvers")

    with op.batch_alter_table("entity_type_rules", schema=None) as batch_op:
        batch_op.drop_index("ix_entity_type_rules_entity_type")
        batch_op.drop_index("ix_entity_type_rules_rule_version")
    op.drop_table("entity_type_rules")
