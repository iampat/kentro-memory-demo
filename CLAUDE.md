# Project Instructions

Python project using **UV** for environment/dependency management, **FastAPI** for HTTP, and **SQLModel** (on SQLAlchemy 2.x) for the database layer.

## Codex review default — `--wait`

Whenever you run `/codex:review` or `/codex:adversarial-review`, default to
`--wait` (foreground). Do not ask "wait or background" and do not background by
default. The user has decided they want to see the review output inline as part
of the same turn, even when the scope is large.

Override only if the user explicitly types `--background` (or asks you to run
it in the background) for that specific invocation.

## SDK and server share types via `kentro.types` (no duplication)

`packages/kentro/src/kentro/types.py` is the single source of truth for every
wire-form Pydantic type. The server (`kentro_server`) depends on the `kentro`
SDK package and imports types directly. There is **no** parallel mirror in
`kentro_server.api.types`, no parity test, no sync skill — those existed
earlier and were retired in Step 7 because they paid maintenance cost without
catching any real bug.

If a type ever needs to diverge between the SDK and the server (e.g. an
MCP-facing variant with stringified statuses), introduce a server-only
subclass at that point. Don't pre-emptively duplicate.

## The handoff is ground truth — DO NOT DIVERGE WITHOUT EXPLICIT APPROVAL

`implementation-handoff.md` (and the three documents it references — `demo.md`, `memory.md`, `memory-system.md`) are the authoritative spec for this project. Treat them as locked.

**Hard rules:**

1. **Never** add, remove, rename, or restructure a feature, package, dependency, file path, API method, type, or behavior that diverges from the handoff. If the handoff says "use X," use X.
2. **Never** invent APIs, methods, CLI flags, output formats, or library behavior. If you don't know it for certain, **stop and confirm with the user** — do not guess from naming, vibes, or training-data familiarity. Cite the source you verified it from.
3. **Never** silently substitute a "similar" library, change a tech-stack choice, swap a default, skip a step, or merge two steps. Each is a divergence requiring approval.
4. **Never** "improve," refactor, or clean up something the handoff specifies a particular way, even if you think the alternative is better. Surface the suggestion as a question; act only after a yes.
5. If something in the handoff is ambiguous, **incorrect**, or contradicts another reference doc — flag it explicitly to the user, present the conflict, and wait for resolution. Do not silently pick a side.
6. If a step requires a decision the handoff defers (e.g., "pick during Step 2"), present the options and **wait** for the user's pick. Do not pre-decide and ask forgiveness later.

**When divergence is approved**, record it in `CHANGE_LOG.md` AND update the corresponding section of `implementation-handoff.md` AND `IMPLEMENTATION_PLAN.md` so the spec stays the single source of truth. The plan and log are not allowed to drift from the handoff.

## Dependency injection &amp; composition over inheritance

When a piece of behavior needs to swap (different LLM provider, real-vs-fake DB,
different cache backend), reach for **composition + dependency injection** before
you reach for "make X an ABC and write a subclass per variant."

The canonical example in this repo is the LLM stack:

```
Provider (ABC)            ← low-level: complete(model, system, user, response_model)
  ├─ AnthropicProvider
  ├─ GeminiProvider
  └─ OfflineProvider

CachingProvider(Provider)  ← middleware: wraps a Provider, fingerprints the request
                              (model + system + user + response_class) and caches.

LLMClient (ABC)            ← high-level skill API (run_skill_resolver, extract_entities, …)

DefaultLLMClient(LLMClient) ← composes Providers via DI:
  __init__(*, fast_provider: Provider, smart_provider: Provider,
           fast_model: str, smart_model: str)
  - loads SKILL.md, formats user, calls the appropriate provider
```

Why this layering matters:

- **The cache key naturally describes the actual LLM request.** Because
  `CachingProvider` sits *under* the prompt-building layer, its fingerprint
  includes the rendered system prompt — which means editing a `SKILL.md` file
  changes the system text → changes the key → forces a fresh call. We never
  have to maintain a "method → skill file" lookup table inside the cache.
- **Mixed providers fall out of composition.** Want fast=Anthropic +
  smart=Gemini? Pass two different `Provider`s to `DefaultLLMClient`. No
  `RoutingLLMClient` subclass required.
- **Tests don't need real SDKs.** A `FakeProvider` with a counter
  replaces the inner provider; the same `CachingProvider` and
  `DefaultLLMClient` surround it.
- **No hidden inputs in the cached call.** Anything that influences the LLM
  must pass through the provider's `complete(...)` arguments. If a future
  feature needs to add tools/temperature/system-suffix, it's added there and
  the cache picks it up automatically.

**Heuristic:** if you find yourself writing two near-identical subclasses that
differ only in *what they call* or *what they wrap*, stop and refactor to a
shared concrete class that takes the variant as a constructor parameter. The
mental test: "if I need to add a third variant, do I need to write a new
subclass, or can I just instantiate the existing class with different args?"
The second answer is the goal.

This is the same impulse as the **No singletons** rule below — both are about
keeping state and behavior **explicit at the call site** rather than hidden
in module/class globals.

## No singletons

Do not introduce module-level singletons (`_singleton: T | None = None` patterns or
class-level shared instances). They make state hidden, ordering brittle, and tests
hard to isolate.

Use one of these instead:

- **FastAPI:** dependency injection via `Depends(...)`. Construct expensive resources
  (LLM clients, store registries, settings) once at app startup in a lifespan handler,
  attach them to `app.state.<name>`, and access them in routes through a `Depends`
  helper. Tests override the dependency.
- **Plain Python:** pass the dependency in explicitly. `def some_fn(*, llm: LLMClient, ...)`
  beats `def some_fn(): llm = get_llm_client()`. The cost is one extra parameter in a
  signature; the gain is testability and explicit ownership.
- **Configuration:** read settings (`Settings()`) at the call site that needs them, or
  pass a `Settings` instance through. Don't hide settings behind a getter that caches.

If a place truly needs process-wide state (e.g. a counter, a connection pool), wire it
explicitly through dependency injection or pass it through call chains. The mental
test: "if I needed to spin up two of these in one test, could I?" — if the answer is
no, the design is wrong.

(Existing singletons in the codebase are flagged for cleanup in `IMPLEMENTATION_PLAN.md`
under "Tech debt".)

## Run tests before opening or updating a PR

Before `git push` of any branch with code changes, ALWAYS run `uv run pytest tests/unit/`
and confirm it's green. If you skipped this, do not open a PR; do not push the
update. The current Python suite is hermetic (no LLM calls) and runs in well under
five seconds. There is no excuse to skip it.

If a test fails, do NOT modify the test to make it pass unless you are intentionally
changing observed behavior — fix the implementation. If the failure is genuinely a
test bug, fix the test in the same commit and call it out in the commit message.

CI is intentionally not configured yet; this manual rule + the local pre-commit hook
(below) are the substitute. CI setup is on the IMPLEMENTATION_PLAN.md "deferred to
the very end" list.

### Local pre-commit hook

A tracked git hook at `scripts/git-hooks/pre-commit` runs **ruff (lint + format),
ty, and the unit test suite** on every commit. Install once per clone:

```bash
git config --local core.hooksPath scripts/git-hooks
```

The hook is fast (~5s combined). Bypass with `git commit --no-verify` only when
you genuinely understand why and you'll fix the failure in the next commit — never
as a way to ship a broken commit.

## Tracking work — `IMPLEMENTATION_PLAN.md` and `CHANGE_LOG.md`

The repo holds two living documents that you MUST keep current:

- **`IMPLEMENTATION_PLAN.md`** — the live plan. Each step from `implementation-handoff.md` has a section here with status (`pending` / `in_progress` / `done`), notes, and any open questions. Update the status before starting a step and again when finishing it. If the plan changes (scope, ordering, dependencies), edit the plan first, then act.
- **`CHANGE_LOG.md`** — append-only, reverse-chronological. One entry per discrete change (`YYYY-MM-DD | scope | summary`). Record code changes, doc edits, dependency adds, decision flips. Newest entries on top.

When a step (or any non-trivial change) finishes, record it in **both** files in the same turn — first flip the plan entry to `done` with a one-line "what was built and where it lives", then prepend a `CHANGE_LOG.md` entry. Do not let one file lead the other.

Also update the relevant section in `implementation-handoff.md` with the same one-paragraph "what was built" summary, as the handoff itself instructs.

## UV — Environment & Dependencies

UV is the only supported way to manage the Python environment. Do not use `pip`, `pip-tools`, `poetry`, `pyenv`, or `python -m venv` directly.

### Daily commands
```bash
uv sync                          # install/refresh deps from uv.lock into .venv
uv add <pkg>                     # add runtime dep (updates pyproject.toml + uv.lock)
uv add --dev <pkg>               # add dev-only dep (e.g. pytest, ruff, ty)
uv remove <pkg>                  # remove a dep
uv run <cmd>                     # run a command inside the project venv
uv run python -m <module>        # e.g. uv run python -m app
uv run pytest                    # run tests
uv run ruff check . && uv run ruff format .
uv run ty check                  # type checking
uv lock                          # regenerate the lock file from pyproject.toml
uv lock --upgrade                # upgrade all deps within constraints
uv lock --upgrade-package <pkg>  # upgrade a single dep
uv python pin 3.13               # pin the Python version (writes .python-version)
```

### Rules
- `pyproject.toml` is the source of truth for dependencies. `uv.lock` MUST be committed.
- Never manually edit `uv.lock`. Run `uv lock` (or `uv add` / `uv remove`) instead.
- Always invoke tools via `uv run <tool>` — do not activate `.venv` and call binaries directly. This guarantees the right interpreter and resolved deps.
- Pin Python with `uv python pin` so contributors and CI agree on the interpreter.
- Prefer `uv add <pkg>` over editing `pyproject.toml` by hand — it resolves and updates the lock atomically.
- For ad-hoc scripts, prefer `uv run --with <pkg> script.py` over polluting project deps.

## Code Style

- **Ruff** for formatting and linting. Line length 99, double quotes, 4-space indent.
- Import order: stdlib → third-party → local, separated by blank lines.
- No mid-code imports — never `import` inside a function or method. If it's needed to break a circular dependency, restructure the modules instead.
- **No quoted forward-reference annotations in regular code.** Don't write `def foo() -> "Bar":` — import `Bar` at module top and write `def foo() -> Bar:` directly. Quoted annotations were a workaround for the pre-`from __future__ import annotations` era; today they hide F821 (`Undefined name`) from ruff and the only reason to reach for them is to dodge an import you should have hoisted. The narrow legitimate use is genuine forward references inside the same module (a type defined later in the file). For everything else, hoist the import. (See the `_mcp_redirect` cleanup on 2026-05-03 for the canonical example: a quoted return annotation paired with a function-local `from fastapi.responses import RedirectResponse` violated both this rule and the no-mid-code-imports rule. Fix: one module-top import, no quotes.)

## Modern Python Idioms

- Use `T | None`, never `Optional[T]`. Do not import `Optional` from `typing` in new code.
- Prefer `match` / `case` (3.10+) over `if`/`elif` chains that branch on a single value, especially for discriminated unions and optional fields.
- Extract named variables instead of inline expressions when it improves readability (e.g. `paused = not config.enabled`).

## Error Handling

- **Never `raise Exception(...)`.** Always raise a specific subclass — `ValueError`,
  `RuntimeError`, `KeyError`, a domain-specific subclass, etc. `raise Exception(...)`
  is a code smell: it tells callers nothing about *what* went wrong and forces them
  to either re-raise blindly or `except Exception` (which is its own smell).
- **Never `except Exception:` or bare `except:`.** Catch the specific exception types
  you can actually handle. The single legitimate exception is a top-level cleanup
  handler that logs and re-raises (e.g., the `BaseException` clean-up-orphan-blob
  pattern in `extraction/ingestor.py` — and that one is `BaseException`, not
  `Exception`, deliberately, so KeyboardInterrupt also runs the cleanup).
- **Never nest `try`/`except` more than one level.** A nested try-except is almost
  always a sign that the function is doing too many distinct things — extract a
  helper. If you genuinely need both a primary and a recovery `try`, write them
  sequentially with explicit return paths, not nested.
- Never swallow exceptions silently. Always log before returning a fallback:
  ```python
  try:
      ...
  except SQLAlchemyError:
      logging.error("Failed to load user", exc_info=True)
      return None
  ```
- Validate at system boundaries (request handlers, external API calls). Trust internal code.

## Type Checking (ty)

Write code that narrows naturally — don't paper over warnings.

- **Never use `assert`** in production code or tests. `assert` is stripped under `python -O`, so any invariant silently disappears. Raise explicitly, or use test-framework assertions (`self.assertEqual`, `pytest.raises`, etc.).
- **Narrow discriminated unions with `isinstance`**, not equality on a tag field. ty does not narrow on `config.type == X`.
- **Guard `Optional` attributes** before dereferencing, even when you "know" they're populated:
  ```python
  if handler.transport is not None:
      handler.transport.flush()
  ```
- **Copy read-only `Mapping` to `dict` before mutation.** Many framework types (e.g. `HTTPException.headers`) are typed `Mapping[str, str]`:
  ```python
  headers = dict(exc.headers) if exc.headers else {}
  headers.setdefault("WWW-Authenticate", "Bearer")
  ```
- **Don't auto-fix ty warnings.** Surface them to the user and let them decide.

## FastAPI

- Define request/response shapes with Pydantic / SQLModel models — never accept raw `dict` from the client.
- Use `Depends(...)` for shared concerns (auth, DB session, settings). Don't reach into module globals from inside a handler.
- Raise `HTTPException` with a specific status code; don't return error dicts with 200.
- Async handlers must not call blocking I/O. If a dependency is sync (e.g. some DB drivers), wrap with `run_in_threadpool` or use the async variant.
- Keep route functions thin — push business logic into services/modules so it's testable without spinning up the app.
- Configuration via Pydantic `BaseSettings`, loaded from env vars. No hard-coded secrets.

## SQLModel / SQLAlchemy 2.x

This project uses **SQLModel** on top of SQLAlchemy 2.x. Prefer the SQLModel API surface — the 1.x `Query` API is deprecated and flagged by ty.

### Quick rules
- `session.exec(select(...))` for `SELECT` — not `session.query(...)`, not `session.execute(select(...)).scalars()`.
- `session.execute(update(...))` / `session.execute(delete(...))` for DML — `exec()` is SELECT-only.
- Import `select` and `col` from `sqlmodel`, not `sqlalchemy`.
- Filter with `col(X)` operators; never write `X == False` / `X == True` with a `# noqa: E712`.

### SELECT
```python
from sqlmodel import col, select

row = session.exec(
    select(EmailMessage).where(EmailMessage.external_message_id == msg_id)
).first()

rows = session.exec(
    select(OrganizationSummary).where(
        OrganizationSummary.organization_id == org_id,
        ~col(OrganizationSummary.is_deleted),
    )
).all()
```

### DML
```python
from sqlalchemy import update
from sqlmodel import col

session.execute(
    update(ActionItem)
    .where(col(ActionItem.id).in_(to_delete))
    .values(is_deleted=True, deleted_at=now)
)
```

### `col(...)` cheat sheet

Wrap a model attribute in `col(X)` when you need typed column operators. It keeps both ruff and ty happy and produces real SQL (not Python-side boolean evaluation).

```python
# Booleans
~col(Model.is_deleted)                       # NOT is_deleted
col(Model.is_active)                         # positive case
col(X.flag).is_(True)                        # use .is_() for nullable booleans where NULL ≠ FALSE

# NULL
col(Token.revoked_at).is_(None)
col(User.email).is_not(None)

# Set membership
col(Opportunity.id).in_(ids)
col(Opportunity.id).not_in(ids)

# String matching
col(User.email).ilike("%@acme.com")
col(User.name).startswith("A")
col(User.name).icontains("oh")

# Ranges / ordering
col(Meeting.started_at).between(start, end)
col(Meeting.started_at).desc().nulls_last()

# Combining
col(A.x) & col(A.y)                          # AND
col(A.x) | col(A.y)                          # OR
~col(A.x)                                    # NOT
```

For plain equality on a mapped attribute, the bare attribute is fine: `Model.id == id_`. Reach for `col(X)` when ty can't see the operator on the raw attribute, or when you need any of the operators above.

### Anti-patterns
```python
# BAD — SQLAlchemy 1.x Query API; deprecated in 2.0
session.query(EmailMessage).filter_by(id=msg_id, is_deleted=False).first()

# BAD — verbose; exec() already returns scalars for SELECT
session.execute(select(EmailMessage).where(...)).scalars().first()

# BAD — Python-side boolean negation; doesn't push down to SQL
not Model.is_deleted
```

## Testing

- Test files end with `_test.py`.
- Descriptive names; group related tests in classes.
- `self.assertEqual(expected, actual)` — **expected first** for accurate failure logs.
- Mock external API calls (HTTP, third-party SDKs). Do **not** reflexively mock the database or other internal collaborators if doing so changes what the test actually verifies.
- Run with `uv run pytest` (or `uv run python -m pytest`).

## Security

### URL redirects & user-controlled URLs
- Validate the **final composed URL's** scheme, netloc, and path before passing to a redirect — never trust intermediate validation alone.
- Use **allowlists** for schemes (`https`, `mailto`) and hostnames; never blocklists.
- Sanitize any user-controlled segment with `urllib.parse.quote` before composing.
- Reject control characters in URL paths: `\x00`, `\n`, `\r`, `\`.

```python
from urllib.parse import urlparse

_ALLOWED_HOSTS = {"app.example.com"}
_ALLOWED_SCHEMES = {"https"}

parsed = urlparse(redirect_url)
if parsed.scheme not in _ALLOWED_SCHEMES or parsed.hostname not in _ALLOWED_HOSTS:
    redirect_url = f"{base_url}/default"
```

## Lint Suppression

Ignoring lint is highly discouraged — fix the underlying issue. If unavoidable, suppress the **specific** rule, never blanket:

```python
some_code()  # noqa: E501
some_code()  # type: ignore[unknown-argument]
```

Never write a bare `# noqa` or `# type: ignore`.
