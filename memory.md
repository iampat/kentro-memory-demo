# Session Memory — Kentro AI Labs Fundraising

<!-- Living memory file for the VC application collection effort. -->
<!-- Updated as new information is shared. -->

---

## Company

- **Company name:** Kentro AI Labs
- **Website:** https://kentrolabs.ai/
- **Legal entity:** Formed (yes)
- **Location:** Vancouver, Canada (open to Vancouver / SF after YC)
- **Currently fundraising:** No (preparing applications)
- **Funding raised to date:** $0
- **Pitch deck:** Work in progress
- **Product/prototype state:** Work in progress
- **Users:** 0
- **Revenue:** 0
- **Design partners / LOIs:** None
- **Customer/user interviews completed:** 0

## Product / Idea

- **One-liner:** Kentro is a governable memory layer for multi-agent AI systems.
- **Core technical wedge:** Rule-change invalidation of stale memory. Existing memory systems (vector stores, MemGPT-style buffers) are append-only — when access rules, entity definitions, or business logic change, agents keep recalling stale or now-forbidden facts. Kentro re-derives what each agent can see when rules change.
- **GTM:** Developer-first, Supabase/Stripe-style self-serve. Not enterprise sales.
- **Why now:** Multi-agent deployments going mainstream; small models (Haiku 4.5, Gemini Flash) make reasoning-aware memory economically viable; founder has watched enterprise teams rebuild this stack repeatedly.
- **Background:** Spinoff from a personal-project agentic assistant for GCs / Product Managers in the construction industry (on-device AI focus). No IP encumbrance.
- **Competitors:** Mem0, Letta / MemGPT, Zep, Cognee, LangMem, Graphiti.
- **Detailed design docs:** See `demo_idea_evolution.md` and `memory-system.md` in this folder.

## Founders

### Ali (CEO)

- **Full legal name:** Seyed Mohsen Amiri (goes by Ali in all professional and application contexts)
- **Email:** ali+claude@amiri.dev
- **LinkedIn:** https://www.linkedin.com/in/ali-amiri-9a0bb15/
- **Phone:** 778-968-1361
- **Student?:** No
- **Commitment:** Part-time on Kentro currently (full-time day job)
- **Role:** Technical founder, CEO. ML/AI infrastructure, search, retrieval.
- **Background:** Staff-level software engineer; AI/ML infrastructure
  - Google
  - Facebook (Meta)
  - Uber (Staff Engineer)
  - Postmates
  - Dropbox
  - Serve Robotics

### Mohammad (cofounder)

- **Full name:** [pending]
- **LinkedIn:** [pending]
- **Joined Kentro:** ~Mar 2026 (~2 months working together)
- **Commitment:** Part-time on Kentro currently (full-time at another company — pending details)
- **Role:** Cofounder. Business-oriented complement to Ali's technical / ML focus.
- **Background:**
  - Long track record building SaaS, PaaS, and Telecom software products.
  - Real-time systems at scale (Telecom-grade requirements).
  - Specifically relevant to Kentro: shipped access-escalation pattern in Telecom support — customer service agents could not see log or conversation contents, but on escalation, engineering support could. Different roles, different views, rules change. This is the same pattern Kentro generalizes.
- **IP situation:** [pending — has Mohammad signed an invention-assignment agreement at his current employer? does it overlap with Kentro?]

### Cofounder dynamics

- **Equity split:** [pending]
- **Cofounder status:** Not actively recruiting. Mohammad is the cofounder.
- **How we know each other:** Friends through professional networks for ~7-8 years. Met in person many times.
- **Built Kentro to date:** Both founders, ~2 months. Started by exploring different ideas, recently pivoted to Kentro.

## Tech Stack

- Heavy user of Claude Code for development
- Using Gemini and Gemma in the product
- (Architecture details: SQLite + S3 hybrid, ColBERT-style token-level retrieval, ingestion-time reasoning. See `memory-system.md`.)

## Application History

- **YC:** Has applied to YC before (with a different idea)
- **PearX:** Has NOT applied to PearX before — first time
- **Other accelerators/incubators:** None
- **Other ideas considered for this round:** Kentro App (the original construction agentic assistant)
- **How heard about YC / PearX:** X (Twitter)
- **Why apply to YC now:** Was already thinking about memory as a problem space and following Garry Tan's content; YC's RFS post on "Company Brain" (https://x.com/ycombinator/status/2048834293779378437) was the prompt to apply
- **YC batch preference:** Summer 2026

## Video Status

- **Founder intro video (1 min):** Need to prepare narration
- **Product demo video:** Not yet recorded

## Writing Voice (applies to all application answers)

- Simple English, short sentences. English is the founder's second language; the answers should sound like him, not like a polished native speaker.
- Concrete words over fancy ones.
- Plain punctuation: periods, colons, commas. Avoid em-dashes and ornate constructions when a period or colon will do.
- No marketing puff. State things directly.
- **Prefer bullet-style over prose** when listing reasons, options, or anything enumerable. Bullets are easier to read. Default to bullets unless the question explicitly asks for prose.

## Rubric for Refining Answers (use whenever revisiting a locked answer)

YC partners read 1000+ applications a season. Strong, declarative sentences land. Hedged, qualified sentences lose. When refining any locked answer, run it through this rubric:

**Be opinionated about:**
- Your core insight / thesis (the wedge — e.g., "memory is a governance problem, not a storage problem")
- Your customer (name them — "developers building multi-agent systems")
- Your GTM motion (pick one and own it — "usage-based PaaS, not enterprise sales")
- What you are NOT building (clarifying, signals discipline)
- Why now (specific reasons, not vague trends)
- Why competitors are wrong (with evidence)

**Don't be opinionated about:**
- Unvalidated revenue numbers — keep them ranged
- Specific pricing you haven't tested
- Conversion rates and unit economics you haven't measured
- Categorical predictions you can't defend

**Be honest about:**
- Where the moat is thin
- What you don't know yet
- Risks (preempt the hard question — e.g., "what if AWS ships this?")

**Sentence-level test:** "Memory is a governance problem" beats "Memory might be best understood as a governance problem." Use the first form except when honesty demands the second.

## Key phrases / lines to deploy

These are sharp lines we've crafted that work hard. Place where they fit best.

- **"Kentro is the infrastructure layer that makes Company Brains possible across many companies, on a common governable substrate."** — Use as the strategic-shape line. Currently lives in Q10 (refined). Strong candidates for reuse: the "What convinced you to apply to YC?" question, pitch deck, founder video script. Plays directly off YC's "Company Brain" RFS without us pretending to be the application — we are the substrate that makes many Company Brains possible.
- **"Memory in multi-agent systems is a governance problem, not a storage problem."** — The thesis sentence. Currently in Q9. Reuse in pitch deck, founder video, anywhere the wedge needs to be stated in one breath.
- **"Multi-agent means multiple levels of access: who reads what, who can add, update, or remove memory."** — The "why does this matter" sentence. Currently in Neo Q3. Reuse anywhere we need to land the multi-agent insight quickly.
- **"Memory has to stay correct under source churn — that's why conflicts are first-class records, not events."** — The architectural argument for conflict-as-memory. Eager resolution discards information that becomes load-bearing the moment a source document is deleted or updated. Late-bound resolution against surviving evidence is the only correct architecture under realistic source operations. Use in technical conversations, pitch deck, founder video Q&A, and any place a competitor's write semantics needs to be contrasted.
- **"Field reads, entity visibility, write permissions, and conflict resolution — Kentro governs all four. Rule changes propagate without re-ingestion."** — The four-dimensions framing. Use as the demo voice-over anchor and as the technical pitch in any deep-dive conversation.
- **"The SDK never asks back. It returns ambiguity as a result."** — Design principle for the public SDK. When natural-language input is unclear, ambiguous, or unparseable, the SDK returns a typed status (`ambiguous`, `no_actionable_content`, etc.) — it never opens a chat or prompts the caller. Multi-round behavior is the agent layer's job, not the infrastructure's. Use as a documentation principle and a positioning line in technical pitches: distinguishes Kentro from chatbot frameworks. **The same principle extends to SkillResolver — when a skill cannot decide a conflict, it signals `UNRESOLVED` with a reason; it does not prompt the caller.**
- **"Conflict resolution is a domain policy, not a default."** — Use when explaining SkillResolver. Other systems pick a winner mechanically (latest, longest, LLM-merge). Kentro lets you express resolution as a policy — "written outweighs verbal," "finance signoff outweighs sales claim," "if you can't decide, mark unresolved" — and applies the same policy consistently across all conflicts in that field.
- **"Memory is the workflow trigger, not just storage."** — The workflow-aware-Skill line. Kentro doesn't only hold data and govern access; **Skills (markdown files) can carry workflow steps alongside policy** — when a SkillResolver picks a winner, the same Skill can also create a Ticket entity, ping a human, route to other agents. Admin authors workflow logic by writing a Skill, not Python. Reads, writes, conflicts, and rule changes can all become triggers. Memory becomes the kernel of the agentic operating system. Use in the YC pitch (Q9 "what we understand"), the founder video, the pitch deck. This is the line that connects "data layer" to "workflow primitive" in one sentence.

## SDK Design — locked decisions (v0)

These are the API-contract calls already made. Implementation follows separately.

- **Two clients:** `AdminClient` (governance: schema, rules, agents, documents) and `AgentClient` (runtime: read, write, query, write_natural, lineage). Mirror Postgres DBA-vs-app-developer separation.
- **Server-based architecture from day one.** SDK is a thin client over a `kentro-server` HTTP/JSON-RPC API. Same binary runs locally (dev), inside Colab (demo), and hosted/self-hosted (production). This is what makes Kentro a PaaS, not a library — and is the only architecture compatible with the metered pricing in Q10 and the on-prem story in Q7.
- **Pydantic v2 throughout** — every public type is a Pydantic v2 model. JSON Schema for MCP tools auto-generates. IDE autocomplete is part of the developer-experience pitch.
- **Schema is declarative (Pydantic class), rules are imperative.** `class Customer(kentro.Entity)` defines structure. ACLs, visibility, and conflict rules are managed at runtime by `AdminClient` because they have to change live (the demo's centerpiece).
- **`FieldValue` wrapper type** for every field on read. Four statuses: `KNOWN` (with confidence + lineage), `UNKNOWN` (never written), `HIDDEN` (exists but ACL-blocked), `UNRESOLVED` (conflict exists, resolver couldn't decide — both candidates and reason returned). Forces callers to handle all four.
- **Conflicts are stored, not resolved at write time. Resolution is a read-time concern via the `resolver=` parameter.** Default `resolver=AutoResolver()` uses the rule from the schema. Built-in resolvers: `RawResolver()`, `LatestWriteResolver()`, and **`SkillResolver`** for any LLM-driven policy. The Skill itself is a **markdown file** that describes both the policy ("how to pick a winner") AND optional workflow steps ("what else to do alongside the pick"). `SkillResolverDecision` carries an optional `actions` tuple — each action is something like `{type: "write_entity", entity_type: "Ticket", ...}` or `{type: "notify", channel: "#deals-review", ...}`, executed by the orchestrator through the same ACL gate as a regular write. **No separate `HumanReviewResolver` class** — "human review" is one shape a Skill can take, authored entirely in the Skill's markdown. If the skill cannot decide, it returns `UNRESOLVED` with a reason — the SDK never asks back. The agent layer handles the unresolved case.
- **`Ticket` is a seeded entity type in the demo schema.** When a workflow-aware Skill emits a "write_entity Ticket" action, the resolver creates a `Ticket` entity (status, conflict_ref, created_at, notified channel, resolution). This is the architectural point worth defending in the pitch: **memory is the workflow trigger, not just storage** — Skills are the unit that carries policy AND workflow, and Kentro is the audit trail of its own escalations.
- **`write_natural` returns typed statuses.** Python SDK exposes them as a typed enum (`WriteStatus.APPLIED`, `WriteStatus.PERMISSION_DENIED`, `WriteStatus.NO_ACTIONABLE_CONTENT`, `WriteStatus.AMBIGUOUS`, `WriteStatus.CONFLICT_RECORDED`). MCP consumers get the same statuses serialized as human-readable messages with context — e.g., "Permission denied: customer_service does not have write access to deal_size on Customer.Acme." No exceptions for ambiguity or missing content. The SDK never asks back.
- **Dual-layer typing pattern: typed enums in the SDK, human-readable strings in MCP.** Same principle applies to `FieldValue.status` (`known`/`unknown`/`hidden`), `WriteStatus`, any other status enum. Python developers get type safety and pattern-matching; LLMs consuming MCP tools get rich, self-explaining responses without our wrapper having to translate codes.
- **Graph traversal at query time, not write time.** `runtime.query()` walks the graph internally for NL queries. Explicit graph methods (`runtime.graph.path()`, `runtime.graph.neighbors()`) are documented as Coming Soon, not v0.
- **Entity resolution is strict-key-based for v0.** Two extractions producing the same canonical key merge into one entity, with respective lineage on each field. Fuzzy resolution (name normalization, embedding-similarity) is v0.1.
- **Sync v0, async v0.1.** `Client` and `AsyncClient` will mirror surfaces.

### v0.1 roadmap (deferred features, all documented)

- Explicit graph API: `runtime.graph.path()`, `runtime.graph.neighbors()`.
- **Processors** — registerable extension types for domain-specific extraction. `EmailProcessor`, `TicketProcessor`, `RandomFactProcessor`, etc. Documented as a public extension point so developers see the system is extensible from day one. Names already chosen for the docs.
- Fuzzy entity resolution.
- `kentro-mcp-server` — thin MCP wrapper over the SDK.
- Async clients.

(Note: `SkillResolver` was originally on this v0.1 list as `LLMResolver`. Promoted to v0 on 2026-05-01 because the demo's domain-aware resolution moment ("written outweighs verbal") is materially more compelling with a real skill than with a mechanical built-in resolver.)

## Open Items
- Founder video narration draft
- Pitch deck draft
- Pick one design partner conversation to start before submission
- Develop the "outlier" / novel-problem-solving story (see Outlier Angle below)
- **Cofounder intake — Mohammad just joined.** Need: full name, LinkedIn, role (technical/GTM), background, when he joined, full-time/part-time, equity split, his own day-job IP situation.
- **REMINDER: After YC application is submitted, revisit and update the Neo application.** Mohammad joining changes Neo Q1 ("Solo technical founder. I do everything that ships..."), Q5 technical experience opener, and the cofounder-search line. Also revisit memory file's Founder section (currently still says "Solo technical founder, CEO" and "100% equity, solo founder").

## PaaS Reframing (under consideration, decided 2026-05-01)

Founder is rethinking positioning from "developer tool" toward "developer-facing PaaS / managed service" — following the Vercel / Supabase / Replit pattern where the dev-tool surface (SDK, CLI, docs) is the on-ramp and revenue comes from metered usage of the underlying service.

This is a clarification of how money flows, not a pivot of the product. The "developer product" framing describes WHO buys; the PaaS framing describes HOW we charge. They are complementary.

**Answers to revisit if/when this reframing is adopted:**
- **Neo Q1 (responsibilities):** No change.
- **Neo Q2 (one-line pitch):** Could clarify "developer-facing PaaS for multi-agent AI memory." Currently says "developer product."
- **Neo Q3 (conviction):** No change.
- **Neo Q5 (technical experience):** No change.
- **YC Q1 (50-char):** Both candidates ("infrastructure" / "infra at scale") are already PaaS-friendly. May reconsider in light of PaaS framing.
- **YC Q2 (what we make):** Already mentions usage-based pricing and Supabase/Vercel shape. Could make "PaaS" explicit.
- **YC Q7 (tech stack):** Already aligned (managed cloud + on-prem + SOC2/GDPR). No change.
- **YC Q10 (how will you make money):** **Biggest impact here.** Build the answer around PaaS economics: storage ($/GB), compute ($/operation), reasoning calls ($/call), all metered. Free dev tier as on-ramp; usage-based pricing on top; enterprise tier later.

## Outlier Angle (seed)

- Founder and wife are both immigrants — likely the spine of the "what makes you an outlier" answer for Pear
- Needs crafting: pair the immigrant arc with concrete career proof points (Staff Engineer at Uber, AI/ML infra at FAANG-scale companies) so the answer reads as "outlier in execution, not just biography"
- TODO when drafting: gather 1–2 specific moments — a hard technical call, a project where founder went against the grain and was right, a shipped result with measurable impact

---

## Form Questions Collected (Pear, YC, generic)

The following questions are being collected from various VC application forms. Drafting will begin once the user signals "go."

### Generic VC form (top of conversation)
- Q1. Description of Responsibilities (optional)
- Q2. In one sentence, what are you building?
- Q3. Most impressive metric, story, or data point that gives conviction (200 words)
- How much funding have you raised to date? — Answer: $0
- Industry (Primary): one of B2B / Biotech / Consumer / Deep Tech / Education / Fintech / Government / Healthcare / Real Estate & Property Tech

### Pear / PearX
- One-line description
- 1-min founder intro video (unlisted YouTube)
- Product demo video URL (unlisted YouTube)
- Pitch deck (PDF)
- What are you building, and why?
- What unique insight do you have into this problem?
- How far along are you?
- Customer interviews? Live product?
- Users? Revenue?
- Competitors and differentiation
- Market size — bottoms-up calculation
- Number of founders
- How long working on the company (<6mo / 6mo–1yr / >1yr)
- Equity split
- CEO / Founder #1 contact info
- Full-time or part-time?
- Currently a student?
- Example of a time you tackled a problem in a novel way
- What makes you an outlier?
- Have you applied to PearX before?
- How did you first hear about PearX?

### Y Combinator (Summer 2026)
- Who writes code / does technical work? Any non-founder contributors?
- Looking for cofounder?
- Founder video (1 min, ≤100MB)
- Company name
- 50-character company description
- Company URL
- Demo video / product link / login credentials
- What is your company going to make?
- Where do you live, and where will the company be based after YC?
- Explain location decision
- How far along are you?
- How long working on this; how much full-time?
- Tech stack (incl. AI models and AI coding tools)
- Optional: attached coding agent session (e.g., Claude Code `/export`)
- Are people using your product? Revenue?
- Same idea as previous batch — what changed? If pivot, why?
- Incubator / accelerator participation
- Why this idea? Domain expertise?
- Competitors — what do you understand that they don't?
- How will you make money? Estimate.
- Category
- Other ideas considered
- Have you formed a legal entity?
- Have you taken any investment?
- Currently fundraising?
- What convinced you to apply to YC? Encouraged by anyone? Been to YC events?
- How did you hear about YC?
- Batch preference (Summer 2026 / later)

---

## Log

- 2026-04-30 | Initial collection of forms (generic VC, Pear, YC) | User pasting form questions, no drafting yet
- 2026-04-30 | Founder + company facts captured | Locked into memory file before drafting
- 2026-04-30 | YC RFS "Company Brain" post is the trigger to apply | Aligns Kentro's pitch with YC's stated interest area
- 2026-05-01 | Demo direction locked | Four governance dimensions (field read, entity visibility, write permission, conflict resolution) shown together, with a four-dimensional rule-change as the centerpiece. Conflict-as-memory framed as a correctness property under source churn, not a UX feature. Recorded video stays focused on the wedge; auto-extraction and source-churn resilience demonstrated in the Colab live demo
- 2026-05-01 | Q9 refinement direction noted | Add fourth "what we understand" point about resilience to source churn. Refinement parked in application-answers.md; not yet folded into the locked text
- 2026-05-01 | SDK API contract frozen for v0 | Two clients (AdminClient, AgentClient), server-based architecture, Pydantic v2 throughout, declarative schema + imperative rules, FieldValue with four statuses (KNOWN/UNKNOWN/HIDDEN/UNRESOLVED), conflicts stored not resolved at write, SkillResolver promoted to v0, dual-layer typing (typed enums in SDK / human-readable strings in MCP), graph traversal at query time, strict-key entity resolution, sync v0 / async v0.1
- 2026-05-01 | Demo Scene 4 upgrade | NL chat input for rule authoring (parses to RuleSet, toggles update visibly before apply); conflict resolver becomes a SkillResolver with domain policy ("written outweighs verbal, latest among written wins") instead of mechanical prefer_latest_write
- 2026-05-01 | Workflow-aware Skills added to v0 SDK + demo | `SkillResolverDecision.actions` carries optional workflow steps a Skill can emit alongside its winner pick (create Ticket entity, fire notification). No new resolver class — "human review" is just a shape a Skill can take, authored in markdown. Surfaces in the demo as a small badge on the resolved field + a fade-in toast. Lands "memory is the workflow trigger" as the third pitch narrative thread alongside data-layer and governance. Net cost: ~150 LOC + ~half day of work
- 2026-05-03 | HumanReviewResolver retired as a class; reframed as workflow-aware Skill | Original 2026-05-01 design used a `HumanReviewResolver(inner_resolver=..., trigger=...)` Python wrapper class. Replaced by extending `SkillResolverDecision` with optional `actions` so the Skill itself (markdown) carries the workflow logic. Cleaner conceptually (admin authors workflow without Python), aligned with the existing skill-loader markdown pattern. Code change deferred — TODO in `kentro_server/skills/llm_client.py`, tracked in `IMPLEMENTATION_PLAN.md` "Deferred to the very end". Demo script + handoff updated.
