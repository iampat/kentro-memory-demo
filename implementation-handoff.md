# Kentro Implementation Handoff

This document is for an implementing agent (Claude Code) building the Kentro demo system. It captures **how** to implement; the **what** and **why** live in the companion documents.

---

## Reference materials (read first, in order)

These describe the product, the strategy, and the architectural decisions that drive the implementation. Read them before this handoff and refer back to them throughout.

1. **`demo.md`** — the locked 3-minute recorded video plan, the Colab cell sequence, the synthetic corpus contract, and the Scene-by-scene UI choreography. This is the user-facing surface the implementation must serve.
2. **`memory.md`** — central project memory. The "SDK Design — locked decisions (v0)" section is the API contract; the "v0.1 roadmap" section is what we are *not* building. The "Key phrases" and "Writing Voice" sections are documentation tone.
3. **`memory-system.md`** — the technical design of the memory layer (token-level retrieval, lineage concepts, schema-level ACLs, storage architecture). Treat as the reference for engine-level decisions; do not invent your own architecture.

If anything in this handoff conflicts with the three documents above, the documents above win — flag the conflict, do not silently resolve it.

---

## Step 1 — Architecture & tech stack

This step answers "what shape is the building, and what materials are we using." Subsequent steps decompose modules and behaviors.

### 1.1 Topology — server + thin SDK

The system has two deployable artifacts and one demo UI.

- **`kentro_server`** — long-running HTTP server. Holds all engine state and business logic. Same binary runs locally for development, as a subprocess inside Colab for the live demo, on a host like Fly.io / Railway for production, or self-hosted in a customer VPC for the on-prem story. **No engine logic lives in the SDK.**
- **`kentro` (the SDK)** — Python package consumed by developers and agents. Thin HTTP client wrapping the server's API. Plus Pydantic types and Jupyter/Colab visualization helpers. **No persistence, no extraction logic, no LLM calls in the SDK** — every public method is a server call.
- **`kentro_demo_ui`** — a small Next.js web app for the recorded 3-minute video. Talks to the same `kentro_server` over its HTTP API. Not part of the SDK; not what end-users will run; exists only to make the demo legible on screen.

Why this split: the SDK has to remain installable, lightweight, and testable in isolation; the engine has to stay independently deployable so we can offer managed cloud + on-prem with the same binary. This is the architecture commitment in `memory.md` under "SDK Design — locked decisions (v0)".

### 1.2 Repository layout

Single monorepo, three packages, shared lockfile.

```
kentro/                           # repo root
├── pyproject.toml                # workspace root
├── uv.lock                       # shared lockfile
├── packages/
│   ├── kentro/                   # the SDK package (pip install kentro)
│   │   ├── pyproject.toml
│   │   └── src/kentro/
│   │       ├── __init__.py
│   │       ├── clients.py        # AdminClient, AgentClient
│   │       ├── types.py          # Pydantic models (mirror server contract)
│   │       ├── resolvers.py      # AutoResolver, RawResolver, LatestWriteResolver, SkillResolver
│   │       └── viz.py            # Jupyter / Colab visualization helpers
│   ├── kentro_server/            # the server package (kentro-server CLI)
│   │   ├── pyproject.toml
│   │   └── src/kentro_server/
│   │       ├── __init__.py
│   │       ├── main.py           # FastAPI app entrypoint + CLI
│   │       ├── api/              # FastAPI routes — one module per resource
│   │       ├── core/             # business logic
│   │       │   ├── acl.py        # ACL evaluator (pure functions)
│   │       │   ├── conflict.py   # conflict detection + resolution dispatch
│   │       │   ├── lineage.py    # lineage tracking
│   │       │   └── rules.py      # rule application + propagation
│   │       ├── store/            # persistence
│   │       │   ├── sqlite.py     # SQLite for relational data
│   │       │   └── blobs.py      # filesystem (dev) / S3 (prod) for source docs
│   │       ├── extraction/       # ingestion + entity extraction
│   │       │   ├── ingestor.py
│   │       │   └── extractors/   # processor types (per memory.md v0.1 roadmap)
│   │       └── skills/           # LLM-backed skills with structured outputs
│   │           ├── nl_to_ruleset.py     # used by Scene 4 chat
│   │           └── skill_resolver.py    # SkillResolver implementation
│   └── kentro_demo_ui/           # Next.js demo web app
│       ├── package.json
│       └── src/
└── tests/
    ├── unit/                     # ACL, conflict, resolvers
    ├── integration/              # ingest → read → rule-change flows
    └── scenario/                 # end-to-end demo scenario reproduction
```

The two Python packages are independently installable. The SDK package has zero runtime dependency on the server package.

**Type sharing — locked 2026-05-02.** No codegen, no shared `kentro_proto` package. Both packages keep **manually duplicated** Pydantic v2 type definitions (SDK in `packages/kentro/src/kentro/types.py`; server in `packages/kentro_server/src/kentro_server/api/types.py`). Parity is enforced by:

1. A **Claude skill** at `.claude/skills/sync-types/` — given a change in one file, it produces the matching edit for the other and explains any intentional divergence.
2. A **parity unit test** at `tests/unit/types_parity_test.py` — imports both modules, walks every model, and asserts identical field names, types, defaults, and discriminator tags. CI fails if the two drift.

Rationale: codegen adds tooling complexity and obscures the human-written contract; a shared `kentro_proto` package adds a third installable artifact whose only job is to ferry types. The duplicated-with-skill+test approach keeps both files readable, lets the SDK and server diverge intentionally when needed (e.g., MCP-facing string statuses on the server), and surfaces drift loudly in CI.

### 1.3 Tech stack

| Layer | Choice | Reason |
|---|---|---|
| Python version | 3.11+ | `match` statement for status enums; `StrEnum`; `typing.Annotated` |
| Package / dep mgmt | `uv` | Fast, modern, monorepo-native |
| Web framework (server) | FastAPI | Pydantic v2 native; auto-OpenAPI; async-ready; LLM ecosystem familiarity |
| HTTP style | REST with JSON bodies | FastAPI default; OpenAPI doc free; future MCP wrapper consumes the OpenAPI |
| HTTP client (SDK) | `httpx` | Sync + async, modern, well-maintained |
| Type system | Pydantic v2 | Locked in `memory.md`. Used for all DTOs, request/response bodies, LLM structured outputs |
| Engine state storage | **SQLite** via **SQLModel** (Pydantic v2-native ORM) | ACID transactions, atomic rule changes, transactional source-delete + re-evaluation, robust under unexpected Colab use (out-of-order cell execution). SQLModel reuses the same Pydantic models as the SDK DTOs — no duplicate type system |
| Source documents | Markdown files in `docs/` | Easy to seed the synthetic corpus, easy for a Colab reviewer to drop in a new `.md` and call `admin.documents.add(path=...)`. The source-delete moment is a real `os.remove` + SQLite transaction |
| Semantic index | **Witchcraft** (Apache 2.0, single-file SQLite, XTR-WARP retrieval) | Drop-in match for the token-level retrieval `memory-system.md` § 5.1 commits to. 21ms p95 search latency, 33% NDCG@10. No vector DB to host |
| LLM provider | **Gemini 3.1 Flash Lite** (default) and **Anthropic Haiku 4.5** (tested fallback) via **Instructor** | Both have native structured-output support. Instructor binds LLM responses to Pydantic v2 models across providers. Open-weight / on-prem deferred — not a v0 concern |
| Structured-output binding | **Instructor** | Pydantic v2-typed outputs with retries on parse failure. Lighter than DSPy. No DSPy in v0 |
| CLI (server) | `typer` + `rich` | `kentro-server start`, plus operational commands needed for the demo: `kentro-server seed-demo` (wipes and re-seeds all 5 tenants with the canonical synthetic corpus), `kentro-server reset-tenant <id>` (between-take reset), `kentro-server smoke-test` (runs every demo beat in <10s, asserts each lands). Install `typer[all]` plus an explicit `rich` pin. Typer auto-detects Rich for pretty `--help` and tracebacks; CLI visualization commands (access matrix, lineage tree, conflict view) use the same `rich.Console` for terminal rendering. Mirrors the Jupyter `viz.*` API in a `viz_cli.*` namespace |
| Semantic index fallback | BM25 + simple cosine over markdown | If Witchcraft's `warp-cli` integration blocks (output format issues, build problems, weights unavailable), implement a 50-line BM25 fallback in `kentro_server/semantic_index/bm25.py`. Same interface; demo scale doesn't need XTR-WARP quality. Witchcraft becomes the v0.1 retrieval upgrade |
| Testing | `pytest` + `pytest-asyncio` | Standard |
| Lint / format | `ruff` | Fast, opinionated |
| Logging | stdlib `logging` to stdout | Demo-grade. Docker captures stdout. No structlog, no JSON logs, no log aggregation in v0 |
| Demo UI framework | Next.js (App Router) + Tailwind + shadcn/ui | Fast iteration; clean visual story; cheap to host |
| Visualization in SDK | `IPython.display` + raw HTML/SVG; `ipywidgets` for interactive cells | No external hosting needed for Colab |
| SDK distribution | **`uv build` → PyPI**, two packages (`kentro` + `kentro-server`), **hatchling** build backend | `pip install kentro` and `uv add kentro` both work. Monorepo workspace via `[tool.uv.workspace]`. No bazel, no shiv |

### 1.4 LLM-call discipline (applies to every LLM-backed feature)

Every place we call an LLM, the call must obey these rules:

- **Structured Pydantic output, always.** Use `instructor` (or the provider's native structured output) to bind the LLM response to a Pydantic v2 model. No string parsing. No regex over LLM responses.
- **Validation retries.** If the parse fails, retry up to 3× with exponential backoff. If all retries fail, return a typed error status to the caller, never an exception that crosses the API boundary.
- **Determinism for the demo path.** Set `temperature=0` and a fixed seed where the provider supports it. For the *recorded* 3-minute demo, also cache the canonical LLM responses for each scenario beat into a fixture file (`tests/fixtures/llm_responses.json` or similar). The demo recording mode replays from cache; the live Colab and the hosted demo make real LLM calls. This guarantees bit-identical output across recording retakes even if a provider drifts.
- **Cost / token logging.** Log per-call token counts with the operation name. Used later for the "affordability at scale" pitch and for catching expensive mistakes in development.
- **No prompt injection paths.** User-supplied document text never appears unescaped in the system prompt of an LLM call. Documents go in the user-message slot; the policy goes in the system slot.

#### Tiered model selection — cheap default, smart for ingestion

Memory operations are high-frequency and cost-sensitive. Ingestion is rare per document and quality-sensitive. The same `LLMClient` abstraction supports both with two named tiers:

- **`fast` tier** — used for the high-frequency calls: NL → RuleSet parsing, SkillResolver evaluation. Defaults to **Gemini 3.1 Flash Lite** (cheap, fast, structured-output native). Fallback: **Anthropic Haiku 4.5**.
- **`smart` tier** — used for ingestion / entity extraction, where you only run once per document and want the best extraction quality. Defaults to **Gemini 3.1 Pro** (or **Claude Sonnet 4.6** / **Opus 4.6** as drop-in alternatives). The cost premium is amortized across all subsequent reads of the extracted memory.

The tier is a parameter to `LLMClient.complete(...)`, not a separate client class. Switching providers within a tier is one config change.

#### Three places LLMs are called in v0

1. **Entity extraction** during ingestion (`extraction/ingestor.py`) — uses the **smart** tier.
2. **NL → `RuleSet` parsing** for Scene 4's chat input (`skills/nl_to_ruleset.py`) — uses the **fast** tier.
3. **`SkillResolver` evaluation** at read time (`skills/skill_resolver.py`) — uses the **fast** tier.

All three share the same LLM client abstraction. Adding a new LLM-call site later (e.g., processor-specific extractors, v0.1 `LLMResolver` variant) reuses the abstraction and the tier model.

### 1.5 Data flow contract (high-level)

The high-level paths through the system, in plain English. Detailed schemas live in Step 2.

- **Ingestion path:** SDK → `POST /documents` → server stores blob → extractor LLM call (structured output) → entities and edges written to SQLite with lineage edges to the document → response carries the `IngestionResult`.
- **Read path:** SDK → `GET /entities/{type}/{key}` → server fetches all field values + their lineage → ACL evaluator filters the field set against the calling agent's rules → conflict-resolver resolves any conflicts (using the `resolver` parameter). If the resolver is a `SkillResolver` whose skill emits workflow actions alongside the winner pick (e.g. `{type: "write_entity", entity_type: "Ticket", ...}` or `{type: "notify", channel: "#deals-review", ...}`), the orchestrator executes those actions through the same ACL gate as a regular write — Skills cannot bypass governance. Response carries the `EntityRecord` with `FieldValue` per field; resolved fields with attached tickets carry the ticket reference inline.
- **Write path:** SDK → `POST /entities/{type}/{key}` → server checks write ACL → if conflict with existing value, both stored → response carries `WriteResult` with typed status.
- **Rule-change path:** SDK → `POST /rules` (atomic) → server applies the new `RuleSet` → no record-level re-ingestion happens, just a rule-version bump. Subsequent reads/writes evaluate against the new rules.
- **NL rule parse:** SDK → `POST /rules/parse` → server runs `nl_to_ruleset` skill → returns parsed `RuleSet`. Caller reviews, then calls `POST /rules` to apply.
- **Source removal:** SDK → `DELETE /documents/{source_id}` → server removes the blob, removes the document's lineage edges, re-runs conflict resolution against surviving evidence for any affected fields → response carries `ReevaluationReport`.

### 1.6 Local development & demo deployment

- **Dev mode (founder's machine):** `uv sync` to install everything; `kentro-server start` runs the FastAPI server on `localhost:8000`. SDK connects via `KENTRO_BASE_URL=http://localhost:8000`. Web demo UI runs via `pnpm dev` on `localhost:3000`, talking to the same server.
- **Colab live demo:** notebook calls `!pip install kentro`, then a setup cell starts the server as a background subprocess (`subprocess.Popen(["kentro-server", "start"])`) and waits for `/healthz` to return 200. SDK connects to `localhost:8000`. No external hosting required for that path.

### 1.7 Hosted demo on GCP (v0) — built last

Real working demo a YC reviewer can click and play with. **Single small GCP VM, no Kubernetes, no Cloud Run, no fancy infra. Demo-grade, not production-grade. This work happens last (Step 12) — only after Steps 0–11 are green and the scenario test passes locally.**

- **Frontend:** **served by `kentro-server` itself.** The demo UI ships as a static build (see Step 10) that the FastAPI app mounts via `StaticFiles` at `/`. No Vercel, no separate frontend deployment. One origin, one TLS cert, one container to operate. Domain: `demo.kentro.ai`.
- **Server:** GCP Compute Engine **e2-medium** VM (Ubuntu 24.04, 2 vCPU shared, 4GB RAM, ~$25/month). Docker runs `kentro-server` (which serves both the API and the embedded static UI). Caddy reverse-proxies `:443` with auto Let's Encrypt certs. Static IP. ~$30/month all-in for infrastructure; ~$50–80/month including LLM API budget for demo traffic.
- **Persistent state:** mounted GCP persistent disk holding `kentro_state/`. SQLite, Witchcraft, source markdown files all live here. Disk snapshots are the backup story.
- **Tenancy: 5 hardcoded demo tenants, no garbage collection.** Tenants 1–5 are seeded at server start (each with a fresh copy of the canonical synthetic corpus). Each frontend session is assigned to a tenant on first visit, in round-robin order. **No idle GC, no tenant creation flow, no auth system** — manually re-seed all five via `kentro-server seed-demo` if state gets crusty during the demo period. Five tenants is enough headroom for concurrent reviewers; if one tenant's state gets messy, others still work.
- **Auth for the demo:** the assigned tenant's API key. No login flow, no account system. Tenant-to-key mapping is a config file, not a DB table.
- **Deployment:** manual `gcloud compute ssh` + `docker compose up -d` for v0. `deploy.sh` script lives in the repo. No Terraform, no GitHub Actions wiring yet — bring those in when we need them.
- **Observability:** stdlib `logging` to stdout, captured by Docker logs. No structured logging, no metrics, no alerting. If something breaks, SSH in and tail the container logs.
- **Out of scope for the hosted demo:** rate limiting, billing, CDN, multi-region, automated scaling, blue/green deploys, idle cleanup, tenant lifecycle, anything operational. **This is for a YC reviewer to play with for an afternoon, not for a paying customer.**

### 1.8 Principles to enforce throughout

These are reflected throughout `memory.md` and `demo.md`. They apply to every module:

- **The SDK never asks back.** Ambiguity returns as a typed status.
- **Conflicts are stored, not resolved at write time.** Resolution happens on read, via the resolver parameter.
- **Lineage is enforced, not optional.** Every fact has a lineage record. No "internal" facts without lineage.
- **Rule changes propagate against existing memory without re-ingestion.** Applies to all four governance dimensions.
- **Dual-layer typing.** Status enums in the SDK; human-readable strings in the MCP / API surface (when added).
- **Strict-key entity resolution for v0.** Two extractions producing the same canonical key merge naturally. Fuzzy resolution is v0.1 — do not implement.

---

## Step 2 — Data models

_To be written next. Will define the Pydantic v2 types used as DTOs across the API boundary, persisted in SQLite, and exported through the SDK. Includes: `Entity`, `Field`, `Agent`, `Rule` variants, `RuleSet`, `EntityRecord`, `FieldValue`, `LineageRecord`, `Conflict`, `WriteResult`, `NLResponse`, `IngestionResult`, `ExtractionStep`, status enums (`FieldStatus`, `WriteStatus`, etc.)._

## Step 3 — Persistence layer (SQLite + blobs)

_To be written. Schema for SQLite tables (entities, fields, lineage edges, ACL rules, agents, conflict records). Blob storage abstraction. Migration strategy._

## Step 4 — ACL evaluator (the foundational pure function)

_To be written. Inputs / outputs / contract for the function that decides whether a given (entity, field, operation, agent, rules) tuple is allowed. Test corpus._

## Step 5 — Conflict detection & resolvers

_To be written. Detection logic on writes. Resolver interface. Implementations of `LatestWriteResolver`, `SkillResolver`. The `UNRESOLVED` path. **`SkillResolverDecision` carries an optional `actions` tuple** so a Skill can emit workflow steps alongside its winner pick (e.g. create a `Ticket` entity, fire a notification). The orchestrator executes each action through the same ACL gate as a regular write — Skills can't bypass governance. The notification primitive is a console log + websocket event for v0; real Slack integration is v0.1. **No separate `HumanReviewResolver` class** — "human review" is one shape a Skill can take, authored entirely in the Skill's markdown file (no Python required for new policies)._

**Status (2026-05-03):** core resolvers shipped; `SkillResolverDecision.actions` extension is logged as a v0-follow-up TODO in `kentro_server/skills/llm_client.py` and tracked in `IMPLEMENTATION_PLAN.md` "Deferred to the very end" — needs to land before the Step 10 UI exercises the workflow-trigger demo beat.

## Step 6 — Ingestion & entity extraction

_To be written. Document ingestion API. Extractor LLM call structure. Strict-key entity resolution. Lineage edge creation._

## Step 7 — HTTP API endpoints

_To be written. FastAPI routes mapping 1:1 to SDK methods. Request/response shapes. Error handling._

## Step 8 — SDK clients (`AdminClient`, `AgentClient`)

_To be written. Method signatures. Error mapping. Sync v0 / async v0.1 split._

## Step 9 — Visualization helpers

_To be written. `viz.access_matrix()`, `viz.entity_graph()`, `viz.lineage(...)`, `viz.conflicts()`, `viz.rule_diff(...)`. Inline-rendering strategy._

## Step 10 — Demo web UI (served by `kentro-server`)

_To be written._ The UI is a single-page app for the recorded video, scene-by-scene component layout, with the four-dimensional rule-change animation. **It does not deploy separately.** The build output is copied into `packages/kentro_server/src/kentro_server/static/` and mounted by FastAPI's `StaticFiles` so the entire demo runs from one origin (locally and on GCP).

Open sub-decision (resolve at the start of Step 10): keep Next.js with `output: 'export'` (static export) OR drop Next.js for **Vite + React + Tailwind + shadcn/ui**. Recommendation: Vite — none of the SSR / middleware / server-action features Next.js gives are useful here, and the Vite build is a clean static-asset emit with no Next.js runtime to wrangle.

Includes two small components for the workflow-trigger story: **`<TicketBadge ticketId={...} />`** rendered inline next to a resolved field when an attached ticket exists, and **`<EscalationToast />`** that slides in for ~3 seconds when a Skill emits a workflow action (renders "Ticket #X created · sales-lead notified · Slack #deals-review"). Both components are minimal — combined ~50 lines — and reuse the existing field-rendering and toast-notification patterns. They depend on the `SkillResolverDecision.actions` server-side extension (see Step 5 status note) — must land before Step 10 begins.

## Step 11 — Synthetic corpus & scenario test

The synthetic corpus is the demo's credibility surface: the source documents the ingestion pipeline reads. Quality matters because reviewers will read these.

**Instruction to the implementing agent (Claude Code):** generate the corpus content via LLM calls. Don't hand-author it; don't ship placeholder lorem-ipsum. Use the `smart` LLM tier (Gemini 3.1 Pro / Claude Sonnet 4.6 / Opus 4.6) to write each document, then commit the generated content into `examples/synthetic_corpus/` so it ships with the package and seeds reproducibly.

### Corpus contract (authoritative source: `demo.md` § Synthetic Corpus Design)

The corpus must contain at minimum:

1. **`acme_call_2026-04-15.md`** — meeting transcript snippet from a sales call with Jane Doe of Acme Corp. Body should be ~400–600 words of natural conversational dialogue. Must include the line that Jane floats a renewal at **$250K** (verbal, conditional, "let me confirm with finance").
2. **`email_jane_2026-04-17.md`** — follow-up email from Jane to the sales team, dated two days after the call. Body ~150–250 words, professional tone. Must revise the deal to **$300K** "after speaking with finance" — explicitly contradicting the call, but plausibly so.
3. **`acme_ticket_142.md`**, **`acme_ticket_157.md`**, **`acme_ticket_162.md`** — three Customer Service tickets for Acme Corp, varied in topic and urgency. ~80–150 words each. All in "open" status. Used to populate `support_tickets`.
4. **`internal_slack_thread_2026-04-19.md`** — internal Slack thread between two AEs discussing Acme. ~200–300 words. Optional: include something that an Ali-style hydration test could pull (e.g., a name + role mention).
5. **`ali_meeting_note_2026-03-10.md`** — meeting note mentioning Ali's phone number `778-968-1361`. ~100–150 words.
6. **`ali_meeting_note_2026-04-02.md`** — second meeting note mentioning Ali's email `ali@kentro.ai`. ~100–150 words. The two together drive the entity-merging hydration moment in the Colab.

### Generation prompts (skeletons for the smart-tier LLM call)

Each document gets generated with a prompt that specifies:
- The **persona** (e.g., "You are writing a meeting transcript snippet")
- The **structural constraints** (length, format, tone)
- The **required facts** that must be embedded verbatim (`$250K`, `Jane Doe`, etc. — we cannot let the LLM hallucinate different numbers because the demo's conflict moment depends on them)
- A **stylistic seed** (e.g., "make this read like a real sales call, with hedges and asides")

The implementing agent: write one Python script `scripts/generate_corpus.py` that produces all six files. Idempotent — re-running it should produce the same files (cache outputs in `tests/fixtures/llm_responses.json`). Commit the *generated* output into the repo so re-generation is optional.

### Scenario test

End-to-end pytest that walks every beat from `demo.md`'s 3-minute beat sheet:

1. Seed corpus via `admin.documents.add(...)` for each file.
2. Assert the access matrix has the expected shape.
3. Run Sales and CS reads; assert correct field visibility per agent.
4. Run a CS write attempt; assert `WriteStatus.PERMISSION_DENIED`.
5. Add the second source; assert conflict is recorded with both candidates.
6. Apply the 4-rule update via `admin.rules.parse(...)` then `admin.rules.apply(...)`; assert all four propagate.
7. Re-run the reads; assert post-rule-change behavior.
8. Click into lineage; assert source documents and active rules are present.
9. Source-delete the email; assert resolver re-evaluates against surviving evidence (Colab-only beat, but include in the test).

If every assertion passes, the demo recording can proceed with confidence. CI runs this on every commit.

---

## Step 12 — Hosted GCP deployment (final step)

_To be written._ Realizes §1.7 on GCP: e2-medium VM, Docker, Caddy + Let's Encrypt, persistent disk, `deploy.sh`, 5 hardcoded tenants seeded at boot, manual operations only. Do not start this step until every prior step is `done` in `IMPLEMENTATION_PLAN.md` and the scenario test passes locally.

---

## Implementation principles for the agent

A few standing instructions that apply to every step:

- **Prefer adding tests over adding implementation.** When in doubt about behavior, write the test first against this handoff and `demo.md`, then implement.
- **Do not invent features not in the references.** If a behavior isn't in `memory.md`, `demo.md`, or this handoff, surface the gap and ask before implementing.
- **Match the existing module structure.** Don't create new top-level packages without a strong reason.
- **Keep the SDK small.** Every new SDK method must map to a server endpoint. No client-side business logic.
- **Keep the LLM surface narrow.** Every LLM call goes through the `skills/` module's `LLMClient` abstraction with structured Pydantic output.
- **Idiomatic Python over clever Python.** `match` statements are idiomatic; metaclass tricks are not. Aim for code another engineer can read in five minutes.

When a step is finished, update its section in this handoff with a one-paragraph "what was built and where it lives" summary so the next step has accurate ground truth.
