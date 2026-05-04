"""Demo package — canonical schemas + corpus path used by the CLI seed command,
the smoke test, and any notebook walking the demo.

Importable from anywhere because it ships with `kentro_server`. The on-disk
synthetic corpus lives at `<repo>/examples/synthetic_corpus/` and is referenced
by `CORPUS_DIR` here so the path is computed once.

Four entity types match the demo prototype's seed (`Customer`, `Person`, `Deal`,
`AuditLog`); the prototype's UI columns expect all four to be registered.
"""

from kentro_server.demo.ruleset import initial_demo_resolvers, initial_demo_ruleset
from kentro_server.demo.schemas import AuditLog, Customer, Deal, Person


def infer_source_class(label: str) -> str | None:
    """Map a corpus filename to the demo's `source_class` bucket.

    Drives the UI's per-doc icon + readable type (📞 Call / ✉️ Email / 🎫 Ticket /
    📝 Note) AND the SkillResolver's "written outweighs verbal" demo rule. The
    inference is filename-pattern based — appropriate for the synthetic corpus
    which uses stable conventions (`acme_call_*` for transcripts, `email_*` for
    emails, `*_ticket_*` for tickets, `*_meeting_note_*` / `*slack*` for notes).

    Buckets are deliberately shorter than the prototype's `type` field — the
    backend only needs the verbal-vs-written distinction; the UI maps further
    to a presentation icon.

    Returns:
      - "verbal"  for call transcripts
      - "email"   for emails
      - "ticket"  for support tickets
      - "note"    for chat threads + meeting notes
      - None      for anything else (renders as a generic 📄 doc)
    """
    name = label.lower()
    if "call" in name or "transcript" in name:
        return "verbal"
    if "email" in name:
        return "email"
    if "ticket" in name:
        return "ticket"
    if "meeting_note" in name or "slack" in name or "note" in name:
        return "note"
    return None


__all__ = [
    "AuditLog",
    "Customer",
    "Deal",
    "Person",
    "infer_source_class",
    "initial_demo_resolvers",
    "initial_demo_ruleset",
]
