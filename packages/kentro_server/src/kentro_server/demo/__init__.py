"""Demo package — canonical schemas + corpus path used by the CLI seed command,
the smoke test, and any notebook walking the demo.

Importable from anywhere because it ships with `kentro_server`. The on-disk
synthetic corpus lives at `<repo>/examples/synthetic_corpus/` and is referenced
by `CORPUS_DIR` here so the path is computed once.
"""

from kentro_server.demo.schemas import Customer, Person

__all__ = ["Customer", "Person"]
