---
name: sync-types
description: Keep the SDK Pydantic types and the kentro-server API mirror in lockstep. Invoke whenever one of the two files changes so the other file and the parity test stay coherent.
---

# sync-types — keep `kentro.types` and `kentro_server.api.types` in lockstep

## Background

Per `implementation-handoff.md` §1.2, the two Python packages keep **manually duplicated** Pydantic v2 type definitions:

- SDK source of truth: `packages/kentro/src/kentro/types.py`
- Server mirror:       `packages/kentro_server/src/kentro_server/api/types.py`

The parity test `tests/unit/types_parity_test.py` fails on any drift in: public symbols (`__all__`), enum members, model field names, model field annotations (modulo the `kentro.types.` / `kentro_server.api.types.` prefix), or model field defaults.

This skill is the human-assisted process for keeping the two files in lockstep when one changes.

## When to invoke

Invoke when **any** of the following changes:

- A type is added, removed, or renamed in either file.
- A field is added, removed, renamed, or retyped on a model in either file.
- A default value changes.
- An enum member is added, removed, or has its value changed.
- A discriminator-tag literal changes.
- The `__all__` export list changes.

You should also invoke pre-emptively before opening a PR that touches either file, even if you believe the changes are mirrored, to catch drift the parity test would later flag.

## What to do

1. **Read both files end-to-end.** Don't skim — discriminated unions and `Annotated` aliases are easy to miss.
2. **Run `uv run pytest tests/unit/types_parity_test.py -v`.** If it passes, both files are already in lockstep — confirm and exit.
3. **If the test fails**, read the diff carefully. The failure message names every divergence: missing/extra symbols, enum-member drift, or model-field drift. Each is a concrete edit to make.
4. **Mirror the change** in whichever file is behind. Match wording, ordering, frozen-config, defaults, and discriminators exactly.
5. **Re-run the parity test.** Iterate until green.
6. **Sanity-check the rest of the unit tests** — `uv run pytest tests/unit/` — so you didn't accidentally break a downstream import.

## Intentional divergence

The handoff (§1.2) anticipates a small, principled set of intentional divergences over time — for example, the server may want **MCP-facing string statuses** layered on top of its enum types while the SDK keeps the raw `StrEnum` form. When introducing such a divergence:

1. Make the divergent edit on one side only.
2. Update `tests/unit/types_parity_test.py` to allowlist the specific divergence. **Never** broaden the test in a way that lets unrelated drift pass — narrow the allow-rule to the specific symbol or field.
3. Add a short comment in **both** files explaining the divergence and pointing at the test allowlist line.
4. Record the divergence in `CHANGE_LOG.md` as a `decision` entry.

If you cannot articulate why the divergence exists in one sentence, it isn't intentional — fix the code instead of widening the test.

## What this skill does NOT do

- It does not edit user-facing entity schemas (subclasses of `Entity` written by users of the SDK).
- It does not change request/response *route* shapes — those live in `packages/kentro_server/src/kentro_server/api/routes/` (Step 7+) and may legitimately use the mirrored types as building blocks but layer their own envelope models on top.
- It does not run any LLM or generation step. Human writes the change; the skill enforces the mirror.

## Quick reference — running the test

```bash
uv run pytest tests/unit/types_parity_test.py -v
```

The four checks the test enforces (all must pass):

| Check | Failure means |
|---|---|
| `test_public_symbols_match` | A name was added/removed from one `__all__` but not the other |
| `test_type_aliases_exist_on_both_sides` | One of the `Annotated` discriminated-union aliases (`Rule`, `ResolverSpec`) is missing on one side |
| `test_enum_members_match` | An enum gained or lost a member, or a value changed |
| `test_model_field_shapes_match` | A model's field set, annotations, or defaults diverged |
