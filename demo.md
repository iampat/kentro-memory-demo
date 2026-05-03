# Kentro Demo — Plan & Decisions

Living document. Captures the strategy, beat sheet, and build plan for the YC demo (recorded 3-min video) and the companion live Colab.

---

## Goals

1. **3-minute recorded video** for the YC application's demo upload slot.
2. **Live, interactive Colab** anyone (reviewer, design partner, friendly developer) can try on their own.
3. The two walk through the same world, same vocabulary, same UI shapes. The recorded video is the guided tour. The Colab is where the reviewer plays.

The strategic goal is to land **one** "aha moment" — the moment a viewer leaves remembering one thing only Kentro does. Everything else is supporting cast.

---

## The wedge

The aha moment is **rule-change invalidation across multi-dimensional governance, with no re-ingestion**, on a memory architecture that's resilient to source churn.

The May 2026 competitive landscape (see `market-research.md`) confirms: nobody else ships this combination.

- Mem0, Letta, Cognee, LangMem — none have field-level + entity-level + write-level governance with rule-change propagation.
- Zep / Graphiti — closest on lineage and bi-temporal validity, but no field-level ACL and no policy-driven conflict resolution.
- AWS Bedrock, Microsoft Foundry, Google Vertex Memory Bank, Anthropic — all single-cloud, none multi-dimensional.
- Vector stores — definitely none.

If the reviewer remembers one thing, it has to be the rule-change moment.

---

## The four governance dimensions

Kentro governs memory across four dimensions. The demo must show all four to land the wedge — any one of them alone is a feature, the combination is the system.

1. **Field-level read ACL** — different agents read different fields of the same entity. (Commodity baseline. Snowflake column masking does this.)
2. **Entity-level ACL** — some entities are entirely invisible to some agents. (Structurally different from field masking.)
3. **Write ACL** — two faces:
   - **Permission:** which agents can add, update, or remove which fields and entities.
   - **Conflict resolution:** what happens when two allowed agents write the same thing at the same time. Kentro records both as memory; resolution is policy (rule-based, or LLM-driven for ambiguous cases). Default is *not* "last writer wins."
4. **Rule-change invalidation across all three** — change a rule on any dimension, every agent's next operation reflects it without re-ingestion.

### Why conflict-as-memory is architectural, not cosmetic

Conflict-as-memory is a **correctness property under source churn**, not a UX preference.

- Documents in the real world get added, updated, and deleted over time.
- Memory systems that eagerly resolve conflicts and discard the losing value are correct only in the steady state.
- The moment a source document changes (especially a delete), an eager-resolution system loses the ability to fall back to surviving evidence. The "winner" now has no source. Memory state is invalid until you do a full backfill — re-extract from every surviving document, re-derive every fact.
- Conflict-as-memory + late-bound resolution stays correct under arbitrary source operations. When a source disappears, the resolution re-evaluates against surviving evidence without re-ingesting anything.

This is structurally different from any "merge on write" approach. To copy it, a competitor has to redesign their write path *and* their lineage model.

---

## What we're NOT doing

- **No naive baseline for comparison.** Removed for simplicity. Competitive contrast lives in the YC application narrative (Q9), not in the demo.
- **No multi-tenancy, sign-up flow, pricing page, production error handling, or docs site.** The demo shows capability, not product polish.
- **No auto-extraction in the recorded video.** Auto-extraction is a feature; nobody else doing it doesn't make it commodity, but it's not the wedge. It belongs in the Colab where reviewer interaction makes it shine.
- **No cost counter on screen.** Mentioned in voice-over at the close if useful; not visualized.
- **No reasoning-graph visualization in the recorded video.** The access matrix carries the structural story in 3 minutes; the graph view goes in the Colab.

---

## The scenario

Two agents, three entity types:

- **Customer** entity — name, contact, deal_size, sales_notes, support_tickets
- **Deal** entity — linked to Customer; sales-only
- **AuditLog** entity — compliance-only by default; invisible to both demo agents at start

**Initial access matrix:**

| | Customer | Deal | AuditLog |
|---|---|---|---|
| **Sales agent** | read all fields, write deal_size + sales_notes, can create new Deals | read + write | invisible |
| **Customer Service agent** | read name + contact + support_tickets, write support_tickets only | invisible | invisible |

This matrix is the on-screen anchor. Reviewer can see all four dimensions in one panel.

---

## 3-minute beat sheet

**0:00–0:25 — Setup.**

> "Two agents — Sales and Customer Service. Three kinds of memory: Customer, Deal, AuditLog. Here's who can do what."

On screen: the access matrix above. Voice-over names the four dimensions.

Address the auto-extraction concern in voice-over: "In production Kentro extracts these types from your docs automatically — you'll see that in the live version. Here we're using a simple schema you'd write in 30 seconds."

**0:25–1:10 — Steady state.** All four dimensions visible.

Choreographed sequence:

- Sales reads Customer → sees full record including deal_size.
- CS reads Customer → fields hidden by ACL banner.
- Sales writes a sales_note → succeeds.
- CS attempts to update deal_size → write blocked banner.
- **A second source document drops** — Doc 1 was Monday's meeting transcript (`"...renewal at around $250K..."`), Doc 2 is Wednesday's follow-up email from Jane (`"...after speaking with finance, $300K..."`). Both extract a `deal_size` for `Customer.Acme`. Kentro records both. A "conflict detected" banner appears with both values, both lineage trails (transcript icon + email icon).
- Both agents attempt to read AuditLog → "entity not visible to this agent."

Voice-over names all four dimensions in one beat:

> "Different agents, different views. Different agents, different write rights. Some memory is invisible entirely. And when sources disagree — Kentro records both. In Monday's call, the prospect floated $250K. In Wednesday's email, after talking with finance, they revised to $300K. Both are real, both have lineage to specific moments. Kentro doesn't pick a winner blindly. Conflict isn't an event; it's a memory record. We record both values for two reasons — transparency, and resilience. If a source document gets deleted later, memory falls back to surviving evidence automatically. Eager resolution loses that option."

**1:10–2:00 — The rule change. The aha.**

Compliance panel slides in. **Two input modes are visible: a chat box at the top, four toggles below.** Compliance officer can use either; the demo uses the chat to show off the NL → structured-config flow.

The compliance officer types into the chat:

> "Redact deal_size from Customer Service. Give Sales read access to AuditLog for Acme. CS tickets now require manager approval. For deal_size conflicts, written sources outweigh verbal sources, latest among written wins. If nothing's written, mark unresolved."

Kentro parses the NL → produces a structured `RuleSet` of four edits. **The toggles below update visibly** to reflect the parsed output. A small banner: "Parsed 4 rule changes — review below." This is the key — the chat *demystifies itself*. The LLM-parsed result is still a transparent, structured config the user can audit before applying.

The user clicks `Apply`. All four rules propagate atomically:

1. **Field read:** "Redact deal_size from all support contexts."
2. **Entity visibility:** "Sales agents gain read access to AuditLog for Acme Corp."
3. **Write permission:** "CS agents can no longer create tickets without manager approval."
4. **Conflict resolution:** A `SkillResolver` is installed for `deal_size` conflicts. **The skill is a markdown file** that carries BOTH the policy ("written outweighs verbal, latest among written wins") AND the workflow ("for deals above $200K, also create a Ticket entity in Kentro and ping the sales lead in Slack — non-blocking, for awareness"). Admin authors policy and workflow by editing markdown, not Python — there is no separate "human review" resolver class; "human review" is just one shape a Skill can take.

The previously-recorded conflict from the steady state resolves itself live under the new domain policy. Both values stay in lineage; the canonical becomes the Wednesday email's $300K *because written outweighs verbal*, not just because it was newer. **Because $300K is over the threshold the skill defined as high-stakes, the same skill that picked the winner also creates a Ticket entity (visible right there in the lineage panel) and fires a notification toast — "Sales lead notified · Slack #deals-review." The skill carries both decisions: pick AND escalate.**

Then the same agents run the same operations they ran 30 seconds ago:

- Sales reads Customer → still sees deal_size (Sales is allowed). Resolved value is canonical.
- Sales now reads AuditLog for Acme → previously invisible, now returns the audit history.
- CS reads Customer → deal_size still redacted; banner: "fields hidden after rule update."
- CS attempts to add a support ticket → blocked. Banner: "write blocked: manager approval required."

A small status panel: "0 records re-ingested. 0 documents re-processed. Four governance rules evaluated against existing memory in <100ms."

Voice-over names the wedge plainly, then closes with the workflow-trigger coda:

> "Compliance writes the change in plain English — including the conflict-resolution policy: written outweighs verbal, with high-stakes deals routed for human review. Kentro parses it to structured rule edits and the toggles update so the change is auditable before it lands. Then apply. Field reads, entity visibility, write permissions, and how conflicts resolve — all four changed in one edit. Every agent's next operation reflected the new world. The recorded conflict resolved under the new domain policy: the Wednesday email outranks the Monday transcript because written outweighs verbal. Nothing was re-indexed. The data didn't move; the rules did.
>
> And because this is a high-stakes deal, Kentro also logs it for the sales lead. Ticket #142 created. Sales lead notified in Slack. Resolution happened automatically, but humans stay in the loop. **Memory is the workflow trigger.**"

**This is the demo.** If this lands, the reviewer remembers Kentro forever.

**2:00–2:30 — Lineage moment.**

Click on the resolved `deal_size` field. A drawer opens:

- **Sources:**
  - 📞 `acme_call_2026-04-15` (transcript, value: $250K)
  - ✉️ `email_jane_2026-04-17` (written, value: $300K)
- **Written by:** `ingestion_agent` (both)
- **Active rules at write time** (per source, expanded)
- **Resolution:** `SkillResolver` — skill `acme_deals_with_human_review` → email wins (policy: "written outweighs verbal")
- **Workflow fired:** 📨 `Ticket #142` created · sales-lead notified · Slack #deals-review (skill emitted these workflow actions alongside its decision)
- **Corroboration:** 2 sources

Voice-over: "Click any field — full lineage. Both source documents, the agent that wrote them, the rules in effect at the time, the policy that resolved the conflict, and the human-review trail. Audit isn't a bolt-on — it's how memory works."

**2:30–3:00 — Close.**

Calm screen showing the access matrix again, recent rule edits highlighted.

> "Multi-agent memory across four governance dimensions — field reads, entity visibility, write permissions, and policy-driven conflict resolution. Rule changes propagate against existing memory in milliseconds. Lineage records source, agent, and active rules. Kentro is the infrastructure layer that makes Company Brains possible across many companies, on a common governable substrate."

End. No music, no logo, no transitions.

---

## What this demo says about Kentro

After 3 minutes the reviewer leaves with:

1. Memory in multi-agent systems isn't shared storage — it's a **multi-dimensional governance problem**.
2. Kentro governs **all four dimensions** — read fields, entity visibility, write permissions, conflict resolution.
3. **Rule changes propagate without re-ingestion** across all four dimensions.
4. **Conflict is a memory record, not an event** — and that's not a UX choice, it's what's required for memory to stay correct under source churn.
5. **Lineage is enforced** — every fact knows where it came from, who wrote it, and what rules were active.
6. **Memory is the workflow trigger.** Skills can carry workflow steps alongside policy — create tickets, ping humans, route to other agents. Admin authors workflow logic by writing a Skill (a markdown file), not Python. Kentro is the data layer *and* the event source for the broader agent stack.
7. The wedge is a system, not a feature.

---

## What to build (minimum viable for the recorded video)

Buildable in days, not weeks, if scoped correctly:

- **Four entity types** in the seeded schema: `Customer`, `Deal`, `AuditLog`, `Ticket`. Hand-coded Pydantic classes (see `implementation-handoff.md` Step 2). The `Ticket` entity is what a workflow-aware Skill writes when it fires a workflow action alongside its resolution decision.
- **Two agent clients** — Sales and Customer Service. Visually distinct (different colors / labels). Either can be Claude Code under the hood.
- **Field-level read ACL evaluator.** Commodity but needed.
- **Entity-level visibility** — derives from the same rule engine; entity-level is "all fields hidden + entity not enumerable."
- **Write ACL evaluator.** Real engine work. Every write checked against current rule set, with a clear permission-denied path.
- **Conflict detection on writes** — when two writes target the same field within a short window, both get recorded with full lineage. No silent overwrite, no rejection.
- **Two conflict resolvers** — `LatestWriteResolver` (built-in, mechanical) and `SkillResolver` (LLM call with structured Pydantic output). The Skill itself is a **markdown file** that describes both the policy ("how to pick a winner among conflicting values") AND optional workflow steps ("what else to do alongside the pick — e.g. create a Ticket entity, fire a notification"). The demo uses a `SkillResolver` whose skill defines both written-vs-verbal policy AND a $200K-threshold workflow as the Scene 4 centerpiece. **There is no separate `HumanReviewResolver` class — "human review" is one shape a Skill can take, authored in markdown.**
- **NL → RuleSet parser** — `admin.rules.parse(text)` runs an LLM with structured output to convert plain-English rule text into a typed `RuleSet`. Used by Scene 4's chat input.
- **Rule-change propagation** — atomic application of multiple rule edits, with all subsequent operations reflecting the new rules.
- **Notification primitive** — for v0, a workflow-aware Skill emits actions (e.g. `{type: "notify", channel: "#deals-review"}`) that the resolver executes. Console log + a websocket event the frontend renders as a fade-in toast. Real Slack integration is v0.1.
- **The UI:**
  - Top half: two agents, side by side, query inputs and outputs.
  - Bottom-left: access matrix panel.
  - Bottom-right: rule editor — chat input on top, four toggles below (toggles update visibly when chat is parsed).
  - Bottom strip: lineage drawer (collapsed; opens on click). Conflict-record drawer same surface. The drawer renders the `Ticket` reference inline when the resolver's skill emitted a workflow action.
  - **Two new tiny components:** `<TicketBadge ticketId={...} />` rendered next to a resolved field when it has an attached ticket; `<EscalationToast />` slides in for ~3 seconds when the resolver's skill fires a workflow action. Combined ~50 lines.

What is explicitly **not** in scope for the recorded demo:

- Auto-extraction (lives in the Colab)
- Reasoning graph visualization (lives in the Colab)
- Real Slack integration (mock for v0)
- Custom user-defined resolvers beyond `SkillResolver` and `HumanReviewResolver` (post-YC)
- Human approval flow / ticket-resolution UI (the demo only shows the *trigger*, not the response)
- Cost counter on screen (mention in voice-over only)
- Sign-up, anything else operational

---

## The Colab live demo (companion to the video)

The Colab walks the same world, same vocabulary, but tells the parts the recorded video defers — auto-extraction, entity merging, graph traversal, the SkillResolver `UNRESOLVED` path, and conflict resilience under source churn.

### Cells

1. **Install + connect.** `!pip install kentro` → spawn `kentro-server` as a subprocess → connect `AdminClient` and two `AgentClient` instances.
2. **Define schema** as Pydantic classes (`Customer`, `Person`, `Deal`, `AuditLog`); register with `admin.schema.register([...])`.
3. **Apply initial rules.** `admin.rules.apply(RuleSet([...]))`.
4. **Render `viz.access_matrix()`** inline; reviewer sees the matrix.
5. **Drop in the first source** — Monday's meeting transcript. `admin.documents.add(...)`. Ingestion steps render. Render the entity graph.
6. **Sales reads Customer.Acme** — full record.
7. **CS reads Customer.Acme** — fields shown as `FieldValue(status=HIDDEN, ...)` for protected fields.
8. **Sales writes a sales_note** — `WriteResult.APPLIED`.
9. **CS writes deal_size** — `WriteResult.PERMISSION_DENIED`.
10. **Drop in the second source** — Wednesday's follow-up email. Conflict registered.
11. **Read with `RawResolver()`** — see both candidates with lineage. `viz.conflicts()` renders them.
12. **Read with default `AutoResolver()`** — under the initial mechanical rule, latest-write wins.
13. **The rule change.** `admin.rules.apply(...)` swaps the conflict rule for a `SkillResolver` with the domain policy ("written outweighs verbal"). `viz.rule_diff(before, after)` highlights what changed.
14. **Re-run the read.** Same conflict; new resolution. Now the email wins because of the policy, not the timestamp. `viz.lineage(...)` shows the resolution annotation. **The deal_size also shows a `Ticket` reference (created by the `HumanReviewResolver` because the deal is over the high-stakes threshold) and the reviewer can `runtime.read("Ticket", "142")` to inspect the auto-created ticket entity.**
15. **The `UNRESOLVED` demonstration.** Drop in a third document where the source type can't be classified (e.g., a stripped text snippet with no metadata). The SkillResolver returns `FieldStatus.UNRESOLVED` with reason `"cannot determine source type"`. The agent's read returns both candidates and the reason — and the cell shows what an agent layer would do with that signal (the SDK never asks back).
15a. **The blocking HumanReviewResolver demonstration.** Configure a different field (e.g., `Customer.account_tier` with values "strategic" vs "transactional") to use `HumanReviewResolver` in *blocking* mode — when the inner resolver returns `UNRESOLVED`, the human review is mandatory. Reviewer sees the field stay `UNRESOLVED` with a `Ticket` reference. They can `admin.tickets.resolve(142, value="strategic")` to simulate the human responding, then re-read the field to see it become `KNOWN`. **This is the cleanest demonstration of "memory is the workflow trigger" — the reviewer literally walks the loop from conflict → escalation → ticket → human resolution → memory updated.**
16. **Hydration moment.** Drop two meeting notes about Ali (one with phone, one with email). `runtime.read("Person", "Ali")` returns a hydrated `Person` with each field carrying its own lineage.
17. **Graph traversal.** Drop docs establishing `Ali works_at Kentro` and `Kentro located_in Vancouver`. `runtime.query("Where is Ali?")` returns the path with lineage.
18. **Source-churn moment.** `admin.documents.remove("email_jane_2026-04-17")`. The previously-resolved deal_size falls back to $250K from the transcript automatically — no backfill. Reviewer sees the canonical value flip live.
19. **Free-play prompts.** "Try editing a rule. Try writing as a different agent. Try removing the transcript instead. What changes?"

### Why this matters

The Colab does the work the recorded video can't fit. Three Colab-only beats land separately:

- **The `UNRESOLVED` cell** is the cleanest demonstration of "the SDK never asks back" — the SkillResolver has a real reason it couldn't decide, returns it as a typed status, and the caller (the agent) decides what to do.
- **The blocking `HumanReviewResolver` cell** is the cleanest demonstration of "memory is the workflow trigger" — the reviewer walks conflict → escalation → ticket → human resolution → memory updated, all inside Kentro itself.
- **The source-churn cell** is the cleanest demonstration of why conflict-as-memory is architectural, not cosmetic.

---

## Synthetic corpus design

Its own design problem. The corpus must:

1. **Be small** — 5–10 documents.
2. **Be internally consistent** — same customer ("Acme Corp") referenced across multiple documents.
3. **Be designed for the rule changes to have visible effects** — `deal_size` must appear in multiple places so redaction is visible.
4. **Contain the locked conflict scenario** — a Monday meeting transcript (`acme_call_2026-04-15`) where the prospect floated `$250K`, and a Wednesday follow-up email from Jane Doe (`email_jane_2026-04-17`) revising to `$300K` after talking with finance. Universally relatable; tells its own story; produces a real conflict on `deal_size`.
5. **Include the entity-merging Ali example for the Colab** — two meeting notes, one mentioning Ali's phone number, the other his email. Strict-key resolution merges them into one `Person.Ali` with respective lineage on each field.
6. **Be plausibly enterprise** — meeting transcript, follow-up email, support tickets, an internal Slack thread, a CRM export with the older deal_size. Reviewer should think "this looks like real company data."

Plan for half a day to draft and tune. This is where the demo's credibility lives.

---

## Production (recording)

- **Screen capture:** Loom, OBS, ScreenFlow, or QuickTime. Pick one and stick with it.
- **Microphone:** USB mic > AirPods > laptop mic. Audio quality > video quality.
- **Voice-over:** bullets, not script. Don't narrate what the viewer can already see; narrate the *meaning*.
- **Lower-thirds / labels:** small captions identifying entities, ACL changes, the conflict moment, the rule-change moment.
- **No music, no logo intro, no transitions.**
- **Length discipline:** 3-minute cap. If the story doesn't land in 3 minutes, the story is too complex.

---

## Open decisions

1. **Recording — live or pre-recorded with VO?** Recommend pre-recorded with voice-over for the polish needed to land four governance dimensions in 3 minutes. Live is more credible but harder to retake.
2. **Hosting for the Colab.** Public Colab notebook with a "Run All" cell + a Kentro Python SDK skel. No backend hosting needed for the v0 Colab — Kentro engine can run inline in the notebook.
3. **Synthetic corpus content** — needs writing. Half-day task.
4. **Voice-over bullet drafting** — needs to happen once the UI is buildable enough to walk through.

---

## Inputs to the build plan

- Story is settled: four governance dimensions, multi-dimensional rule change as the centerpiece, conflict-as-memory + source-churn resilience as the architectural backbone.
- UI shape: split panel with two agents (top), access matrix (bottom-left), rule editor (bottom-right), lineage/conflict drawer (bottom strip).
- Agent harness: Claude Code or similar, driven from a Next.js front-end via a thin API.
- Recording starts once the UI runs the choreographed sequence end-to-end.
