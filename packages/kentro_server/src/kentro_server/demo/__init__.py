"""Demo package — canonical schemas + corpus path used by the CLI seed command,
the smoke test, and any notebook walking the demo.

Importable from anywhere because it ships with `kentro_server`. The on-disk
synthetic corpus lives at `<repo>/examples/synthetic_corpus/` and is referenced
by `CORPUS_DIR` here so the path is computed once.

Four entity types match the demo prototype's seed (`Customer`, `Person`, `Deal`,
`AuditLog`); the prototype's UI columns expect all four to be registered.
"""

from kentro_server.demo.ruleset import initial_demo_ruleset
from kentro_server.demo.schemas import AuditLog, Customer, Deal, Person

__all__ = ["AuditLog", "Customer", "Deal", "Person", "initial_demo_ruleset"]
