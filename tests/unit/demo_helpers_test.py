"""Unit tests for `kentro_server.demo` helpers used by the seed CLI + UI.

`infer_source_class` powers the per-doc icon (📞 Call / ✉️ Email / 🎫 Ticket /
📝 Note) the prototype shows for each ingested document. The seed CLI passes
its return value into `POST /documents`, so an off-by-one mapping here would
surface as the UI rendering every doc as the generic 📄 fallback.
"""

from kentro_server.demo import infer_source_class


def test_infer_source_class_call_transcript() -> None:
    if infer_source_class("acme_call_2026-04-15.md") != "verbal":
        raise AssertionError("call file should map to verbal")
    if infer_source_class("client_call_transcript.md") != "verbal":
        raise AssertionError("'transcript' keyword should also bucket as verbal")


def test_infer_source_class_email() -> None:
    if infer_source_class("email_jane_2026-04-17.md") != "email":
        raise AssertionError("email file should map to email")


def test_infer_source_class_ticket() -> None:
    if infer_source_class("acme_ticket_142.md") != "ticket":
        raise AssertionError("ticket file should map to ticket")


def test_infer_source_class_note_meeting() -> None:
    if infer_source_class("ali_meeting_note_2026-03-10.md") != "note":
        raise AssertionError("meeting_note file should map to note")
    if infer_source_class("internal_slack_thread_2026-04-19.md") != "note":
        raise AssertionError("slack thread should map to note (chat-like)")


def test_infer_source_class_unknown_returns_none() -> None:
    if infer_source_class("random_file.md") is not None:
        raise AssertionError("unknown filenames should return None (UI fallback to 📄)")
