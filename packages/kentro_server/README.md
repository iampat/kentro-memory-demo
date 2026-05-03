# kentro-server

FastAPI engine for Kentro. Holds all engine state and business logic. Same binary runs locally for dev, as a subprocess in Colab, on a host like Fly.io / Railway, or self-hosted in a customer VPC.

## CLI

Currently implemented (run with `uv run kentro-server <cmd>`):

- `start [--host HOST] [--port PORT]` — start the FastAPI app.
- `version` — print the kentro-server version.
- `llm-stats [--base-url URL]` — query a running server for its LLM cache hit/miss counters.

Planned (per `implementation-handoff.md` §1.7) but **not yet implemented**:

- `seed-demo` — wipe and re-seed all hardcoded demo tenants with the canonical synthetic corpus.
- `reset-tenant <id>` — between-take reset for a single tenant.
- `smoke-test` — runs every demo beat in <10s and asserts each lands.

These will land alongside Step 7 (HTTP API) and Step 11 (scenario test).

See `implementation-handoff.md` at the repo root for the full architecture.
