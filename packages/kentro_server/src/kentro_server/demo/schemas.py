"""Demo entity schemas — shared by CLI seed-demo, smoke test, and any walkthrough.

Mirrors the four entity types the demo prototype seeds (see
`packages/kentro_server/src/kentro_server/static/data.js`):

- `Customer` — Acme: deal_size conflict (transcript $250K vs email $300K),
  contact, sales_notes, support_tickets.
- `Person` — internal directory (Ali, Jane).
- `Deal` — the deal-shaped projection (`acme-renewal-2026`) — same `size`
  conflict surfaces here too because the same writes touch both entities.
- `AuditLog` — the visibility-toggle target for Scene 4 ("Sales gains
  AuditLog access"). Hidden from Sales by default; one rule edit grants it.

Each is a thin Pydantic model used by `entity_type_def_from(cls)` to produce
the wire-form `EntityTypeDef`. Server-side schema evolution rules apply:
fields can be added or deprecated, never renamed/removed/type-changed.
"""

from kentro import Entity


class Customer(Entity):
    name: str
    contact: str | None = None
    deal_size: float | None = None
    sales_notes: str = ""
    support_tickets: list[str] = []


class Person(Entity):
    name: str
    phone: str | None = None
    email: str | None = None


class Deal(Entity):
    """The deal-shaped projection of a Customer renewal/expansion.

    `size` is the demo's conflict centerpiece on the Deal side (mirrors
    `Customer.deal_size`). `customer` is a string key reference, not a
    foreign key — the data model has no FK enforcement; entity identity
    is by `(type, key)` strict-key resolution.
    """

    customer: str | None = None
    size: float | None = None
    stage: str | None = None


class AuditLog(Entity):
    """The Scene-4 visibility-toggle target. Hidden from Sales by default;
    one EntityVisibilityRule edit during the demo grants Sales read access.

    `events` is a free-form summary string for v0 (e.g. "12 access events
    in the last 90 days"); v0.1 might split this into a list of typed events.
    """

    events: str | None = None


__all__ = ["AuditLog", "Customer", "Deal", "Person"]
