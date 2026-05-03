"""kentro-server API surface — FastAPI routes (Step 7+).

Wire-form types for requests and responses live in `kentro.types` (the SDK's
source of truth). The server imports them directly — there is no parallel mirror.
If a server-side type ever needs to diverge (e.g. MCP-facing string statuses),
introduce a server-only subclass at that point; do not pre-emptively duplicate.
"""
