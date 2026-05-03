"""Demo entity schemas — shared by CLI seed-demo, smoke test, and any walkthrough.

Kept deliberately small and reflective of the demo's two named scenes:
- `Customer` — Acme: deal_size conflict (transcript $250K vs email $300K),
  contact, sales_notes, support_tickets.
- `Person` — internal directory (Ali, Jane).
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


__all__ = ["Customer", "Person"]
