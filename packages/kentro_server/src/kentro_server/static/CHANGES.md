# Kentro Demo — Changes from Original Design

A running log of edits made on top of the initial Kentro Memory demo prototype.
Each entry: **what changed**, **why**, and **where** it lives in the codebase.

---

## 1. Policies panel — split into two stacked sub-editors

**What changed**
The single Policies panel with one chat box and one parse button is now two
sections, each with its own list, chat box, suggestions, and apply flow:

- **Access & condition policies** — covers role/field reads, writes, and
  approval requirements (everything where `kind !== "conflict"`).
- **Conflict policy** — covers how the resolver picks a winner when a field has
  multiple candidate values (`kind === "conflict"`).

The `PolicyEditor` component is now a thin shell that renders two
`PolicySubEditor` instances and the shared propagation log.

**Why**
Conflict resolution is conceptually different from access control — mixing
them in one editor made the prompt ambiguous ("modify policies" could mean
either) and forced users to read a long Pending Changes diff that spanned both
domains. Splitting them keeps each prompt scoped, each diff small, and lets
demo viewers reason about access changes and conflict changes independently.

**Where**
- `app.jsx` — `PolicyEditor`, `PolicySubEditor`, `EDIT_RECIPES`,
  `ACCESS_SUGGESTIONS`, `CONFLICT_SUGGESTIONS`
- `styles.css` — `.policy-section`, `.policy-section-head`, `.suggestion-row`,
  `.suggestion-chip`, `.apply-row.inline`

---

## 2. Suggestion chips above each chat box

**What changed**
Each sub-editor now shows a "Try:" row of clickable chips that pre-fill the
chat input with a sample prompt. Two flavours:

- **rewrite** (orange badge) — fills in a multi-edit prompt that exercises
  several policies at once. Currently used on the Access section to fire all
  three of the demo's headline access changes (hide `deal_size`, show
  `AuditLog`, require approval on `support_tickets` writes).
- **edit** (grey badge) — single-policy prompts. Access has *Hide deal_size
  from CS* and *Make AuditLog visible to Sales*. Conflict has *Prefer written
  over verbal* and *Latest write wins*.

The previous demo flow relied on a hidden `SCENE3_PROMPT` constant; that has
been removed. The same behaviour is reachable by clicking the rewrite chip
plus the conflict chip, which composes cleanly because the two sections apply
independently.

**Why**
Demo viewers needed a discoverable on-ramp without making the prompts feel
canned. Chips also let us show the parser handles both broad rewrites and
narrow edits — without wiring scripted scene buttons that look like cheating.

**Where**
- `app.jsx` — `ACCESS_SUGGESTIONS`, `CONFLICT_SUGGESTIONS`, `EDIT_RECIPES`,
  `PolicySubEditor.onPickSuggestion`, `PolicySubEditor.onParse` (recipe
  matching by regex before falling through to `claude.complete`).
- `styles.css` — `.suggestion-row`, `.suggestion-chip`, `.chip-kind`,
  `.suggestion-chip.kind-rewrite`.

---

## 3. Inline apply / cancel inside the diff card

**What changed**
The "apply changes" / "cancel" buttons used to sit below the chat box at all
times (disabled when nothing was pending). They now live **inside** the
Pending Changes preview card, only visible while there are edits queued.

**Why**
Reduces dead chrome when the panel is idle and ties the Apply action visually
to the diff it's about to commit — clearer cause and effect.

**Where**
- `app.jsx` — bottom of `PolicySubEditor` render
- `styles.css` — `.apply-row.inline`

---

## 4. "Auto-extraction" → "Ingestion pipeline · Events become memory"

**What changed**
The bottom-left panel header was relabelled:

- Title: `Auto-extraction` → `Ingestion pipeline`
- Subtitle: `Documents become memory` → `Events become memory`
- Count: `N documents` → `N events`
- Drop-source button: `+ drop ✉️ email_jane_2026-04-17.md` →
  `+ drop ✉️ email from Jane Doe`

**Why**
"Auto-extraction" describes the implementation; "Ingestion pipeline" describes
the role this stage plays in the memory architecture, which is what the demo
is actually about. "Events" generalises beyond static documents to the streams
(calls, tickets, emails, integrations) the system is meant to process — and
matches the live, scrolling feel of the panel.

The button label dropped the filename in favour of a human description because
the demo is positioned as a memory system, not a file viewer.

**Where**
- `panels.jsx` — `ExtractionPanel` panel head + add-doc button.

---

## 5. Document rows use type + date instead of filename

**What changed**
Each row in the ingestion list previously showed:

```
📞  acme_call_2026-04-15.md          2026-04-15 10:32
    Sales call — Acme Corp
```

It now shows:

```
📞  Call · 2026-04-15                 10:32
    Sales call — Acme Corp
```

The primary line is the **type label** (`Call` / `Email` / `Ticket`) plus the
date; the friendly title moves to the secondary line; the time-of-day moves to
the right rail when present.

**Why**
Filenames with `.md` extensions leaked the underlying mock through the UI and
made the demo read like a markdown viewer. Type-and-date framing matches how a
PM/eng audience thinks about ingest events.

**Where**
- `panels.jsx` — `ExtractionPanel` doc-list mapping (derives `typeLabel` from
  `d.type` and splits `d.timestamp` into date and time).

---

## 6. Extraction stream uses event labels, not filenames

**What changed**
The first line of the extraction trace used to read:

```
+000ms  read acme_call_2026-04-15.md · 2026-04-15 10:32
```

It now reads:

```
+000ms  read Sales call — Acme Corp · 2026-04-15 10:32
```

i.e. uses `d.label` instead of `d.name`.

**Why**
Same reasoning as #5 — keep the demo speaking in terms of events and
human-readable sources, not files.

**Where**
- `app.jsx` — extraction-log seeding inside the active-doc effect.
