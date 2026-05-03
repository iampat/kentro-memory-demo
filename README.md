# Kentro

Multi-agent memory governance with conflict-as-memory, ACL-gated reads, and a
read-time resolver pipeline. Single-process FastAPI server (no Vercel) with an
HTML/JSX prototype demo UI served from the same process.

## Layout

- `packages/kentro` — SDK: `Client`, `Entity`, types, ACL evaluators, viz
  helpers, resolver wrappers, rule renderers (`render_rule`,
  `render_rule_as_rego`).
- `packages/kentro_server` — engine: FastAPI app, MCP mount, per-tenant SQLite
  store, extraction + skill pipelines, demo CLI, static UI bundle at
  `kentro_server/static/`.
- `examples/synthetic_corpus/` — 8 markdown docs the `seed-demo` CLI ingests.
- `tests/{unit,integration}/` — hermetic unit tests (no LLM); integration tests
  hit the Anthropic cache, gated on `ANTHROPIC_API_KEY`.

## Local dev — Taskfile

[Task](https://taskfile.dev) (`brew install go-task`) wraps the common
commands. `task --list` shows all of them.

```bash
task dev               # uvicorn with --reload on 127.0.0.1:8000
task open              # open the demo UI in the browser
task seed              # register schemas + ingest the corpus (server must be running)
task gates             # ruff lint + format check + ty + unit tests (the pre-commit hook)
task test              # unit tests only
task reset             # wipe local state
```

The demo UI is served at `/` (React via CDN + Babel JSX, no build step). The
MCP endpoint is at `/mcp/`. `/healthz` and `/llm/stats` are the smoke endpoints.

`task seed` requires `KENTRO_API_KEY` — use the `local:ingestion_agent` key from
`tenants.json` (auto-created on first run).

## Without Task

```bash
uv sync                                                  # install deps
uv run uvicorn kentro_server.main:app --reload           # run the server
uv run pytest tests/unit/                                # run unit tests
uv run kentro-server seed-demo --base-url http://127.0.0.1:8000
```

## See also

- `implementation-handoff.md` — locked spec (do not diverge without approval; see `CLAUDE.md`).
- `IMPLEMENTATION_PLAN.md` — live plan, mirrors handoff steps with status.
- `CHANGE_LOG.md` — append-only, reverse-chronological.
- `demo.md`, `memory.md`, `memory-system.md` — design references.
