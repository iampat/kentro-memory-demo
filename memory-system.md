# Memory System — Handoff Document

> A comprehensive design document for a multi-user memory system exposed as an MCP server. Captures the problem, the reasoning behind every design decision, the open questions, and the path from v1 to v3.

---

## 1. Problem Statement

### What We're Building

A **memory system** exposed as an MCP server that ingests documents — primarily emails and similar conversational artifacts — and stores not just their raw content but the **reasoning derived from them**. Multiple users interact with this memory via agents, and the memory must respect fine-grained access control across both data sources and schema fields.

### The Core Insight

The valuable bit is **memorizing reasoning**, not raw documents. Storing three emails verbatim is far less useful than storing the synthesized insight from them ("we decided to use approach X because of constraint Y"). Reasoning compresses context and surfaces meaning that no individual document contains on its own.

### MCP Operations Exposed

- `ingest` — add a new document to the system
- `memorize` — store reasoning derived from one or more documents
- `forget` — remove a document or memory
- `update` — modify existing memory

### The Hard Problem

When reasoning is built from multiple documents and a user can only access a subset of those documents, the system must present a view of the reasoning that's consistent with what the user is allowed to see — without recomputing the entire reasoning graph on every query.

LLM reasoning is **non-linear and emergent**: Doc A + Doc B → insight C is not just the sum of A and B. So removing Doc B doesn't cleanly remove parts of C. You don't know if C still holds without B, or if B was essential to deriving C.

---

## 2. Worked Examples (Why This Is Hard)

### Example 1: The Reasoning Changes Per User

Three documents arrive in sequence:

- **Doc 1**: "I live in Vancouver"
- **Doc 2**: "Sorry — Vancouver, Washington, not BC"
- **Doc 3**: "I live at [address] in Vancouver"

If Alex can't see Doc 2, Alex's reasoning should reflect "lives in Vancouver, BC" — without the correction. **The reasoning output literally changes based on who's viewing it.** This isn't just filtering — it's recomputing what the reasoning would be if only the accessible documents existed.

### Example 2: Redundancy Breaks Naive Lineage

Two documents both say "my name is Ali." If we tag Doc 1 as "core" (the source of the insight) and Doc 2 as "supporting" (corroborating), then removing Doc 1 would erase the fact about the name — even though Doc 2 still says it. **Core/supporting tagging breaks under redundancy.** What we actually need is **corroboration count** — how many independent sources back each fact.

### Example 3: Lineage Across an Access Timeline

A user receives 5 emails, then is shared a document, then receives 2 more emails, then is unshared the document, then receives 1 more email. The lineage must track:

- Which documents contributed to each reasoning fragment
- When access changed for each document
- Which reasoning fragments become invalid when access is revoked
- Which fragments survive because their conclusions are corroborated elsewhere

---

## 3. North Star

A memory system where:

1. **Reasoning is the primary asset**, not raw documents. The system stores synthesized insights with full lineage back to source documents.
2. **Access control is consistent and correct** across documents, schema fields, and the reasoning derived from them.
3. **Retrieval is fast and cheap** — sub-second for agents, semantic search via token-level embeddings.
4. **Storage is cloud-native and stateless** — everything lives in object storage; no reserved compute for databases.
5. **Schema evolves safely** — append-only, backwards-compatible, no breaking migrations.
6. **Eventual consistency for reasoning** — fast at query time, with periodic background reconciliation to repair the graph after access or source changes.
7. **Lineage is multi-dimensional** — tracks not just which sources contributed but how (corroboration count, contribution weight, schema version).

---

## 4. Constraints & Assumptions

### Scale
- Small organization: thousands of users, 20–100 products
- Data volume: megabytes to low gigabytes of memory data
- Not designed for enterprise (TB) scale — different architecture would be needed there
- Concrete framing: customer service use case with ~20–100 products and a few thousand customers interacting with the system

### Performance
- Fast retrieval is critical — agents query frequently
- Approximate load: ~3 reads/sec for ~10 concurrent agents (very modest)
- Writes are naturally slow (bounded by LLM latency, ~seconds per operation)
- Minimize internal LLM calls to keep cost down
- Optionally use a cheap long-context model for bulk/fallback retrieval

### Storage Preferences
- Cloud-native: S3 / GCS as primary store
- Avoid reserved infrastructure (no dedicated Postgres machines)
- Avoid graph databases — relationships can be modeled in SQL
- Single-writer SQLite is acceptable at this scale (3 writes/sec is well within its comfort zone)

### Access Control
- Changes infrequently (a few times per resource over its lifetime — share/unshare a doc once or twice, not constantly)
- Two dimensions:
  1. **Source-level**: which documents/entities a user can see
  2. **Schema-level**: which fields a user can see, and at what tier (direct / bucketed / aggregate)

### Schema
- Append-only evolution (protobuf-style)
- Only add fields; never remove or change types
- Deprecate via parallel fields (e.g., `email` → `emails`), then ignore old fields after migration
- Schema can be human-defined and evolves as the system learns what's needed

---

## 5. Conceptual Foundations

### 5.1 Token-Level Semantic Retrieval

The retrieval substrate is a **ColBERT-style late interaction** model (specifically the XTR / WARP family from Stanford):

- Each token in a document gets its own embedding (not one big document vector)
- At query time, each query token's embedding is matched against document token embeddings
- **Chamfer-style scoring**: for each query token, find the closest matching document token; sum those distances
- **Implicit proximity**: scattered query tokens in a document score lower than tokens clustered together, because clustered tokens are likely more semantically coherent
- **Semantic similarity beats exact word matching**: a query "I love coffee" matches "I enjoy drinking espresso" higher than scattered occurrences of "I", "love", "coffee" in a dictionary, because the closest-match distances are smaller

**Why it's fast**: token-level comparisons are smaller than full-vector similarity; local execution avoids network latency; the indexing structures are tuned for this access pattern.

**Structured data caveat**: the model is built for unstructured text. For JSON or structured data, you serialize keys + values as text and index per object; it's a workaround, not a native capability.

### 5.2 Reasoning Lineage Concepts

#### What lineage tracks
For each reasoning fragment, we need:
- **Source documents** that contributed to it
- **Schema version** under which it was computed
- **Corroboration count** per fact within the fragment
- **Timestamp** of computation
- Optionally: which agent/role produced it, what prompt/model was used (for reproducibility)

#### Why core/supporting fails
The natural instinct is to tag each source as "core" or "supporting" — remove core, fragment dies; remove supporting, fragment survives. But **redundancy breaks this**: two sources can independently establish the same fact, and either one alone is sufficient. The right primitive is **corroboration count per fact**, not core/supporting per source.

#### Reasoning that changes per user
The deep challenge: when a user has partial access, the reasoning they should see may differ from what was originally computed. Three approaches:

1. **Strict filter** — drop any fragment whose source set isn't fully accessible. Simple, conservative, may hide too much.
2. **Fact-level lineage** — each fact in a fragment tagged with its supporting sources. Filter facts, not whole fragments. More work, more accurate.
3. **Per-user re-derivation** — recompute reasoning per user's accessible subset. Most accurate, most expensive.

v1 uses approach 1; v2 introduces approach 2; approach 3 is reserved for cases where it's strictly necessary.

### 5.3 Shift Reasoning Left or Right

Like database indexing — choose where to pay the cost:

| Phase | What Happens | Trade-off |
|---|---|---|
| **Ingestion-time** | Heavy reasoning, lock in lineage | Slower writes, faster reads |
| **Retrieval-time** | Filter by ACL, derive on demand | Faster writes, slower reads |
| **Background reconciliation** | Periodic graph repair after changes | Eventual consistency |

The recommended split: **heavy at ingestion, light at retrieval, periodic reconciliation in the background**. This gives **eventual consistency for reasoning** — fast at query time, with the graph repaired when access changes or sources are removed.

### 5.4 Schema Evolution as Append-Only Migration

Inspired by protobuf:

- Only add fields; never remove or change types
- To deprecate a field, introduce a parallel one (`email` → `emails`) and migrate data over time
- After migration, ignore the old field on read
- Each reasoning fragment is tagged with the schema version it was computed under

**Why this matters for lineage**: if you never remove fields, you never have to ask "which reasoning depended on this removed field?" The lineage graph just keeps accumulating context monotonically.

---

## 6. Schema-Level Access Control

Schema ACLs operate on **fields**, not just whole records or sources. The model has multiple tiers of access per field, mirroring concepts from SQL (column-level security, views, statistical databases) and modern data platforms (Snowflake/BigQuery row-access and column-masking policies).

### 6.1 Access Tiers

For each field, a policy can grant access at one or more of these levels:

1. **Direct access** — see the raw value (e.g., Ali's age is 35)
2. **Bucketed / anonymized access** — see a coarsened value (e.g., Ali's age is in 30–40)
3. **Aggregate access** — see only values derived from groups (e.g., team average age is 32), gated by `min_group_size` (k-anonymity-style threshold)
4. **Derived reasoning access** — see reasoning fragments that incorporate the field, possibly mixed with other sources

### 6.2 SQL-World Vocabulary

The patterns map onto established concepts:
- **Column-level security** — "can/cannot see this column"
- **Views** — exposing aggregates while hiding underlying rows
- **Cell-level security** — access depends on both column and row
- **Aggregate-only / statistical access** — raw values hidden, aggregates allowed; the foundation of statistical databases
- **Differential privacy** — modern formalization of how much an aggregate can leak about individuals
- **Polyinstantiation, multilevel security (MLS)** — older formal models
- **Row-access policies and column-masking policies** — the modern names in Snowflake / BigQuery / Databricks

### 6.3 Policy Primitives

- **Roles**: `hr_role`, `manager_role`, `finance_role`, etc.
- **Self**: a user can always see data about themselves (very common default)
- **Purpose**: a declared use (`medical_research`, `audit`) gating access on top of role
- **min_group_size**: minimum cohort size for aggregate queries (k-anonymity threshold)

### 6.4 Sample Policies

**Policy 1 — Public field**
```
field: Person.Name
direct_access: [everyone]
aggregate_access: [everyone]
```
Anyone can see anyone's name, directly or in aggregate.

**Policy 2 — Aggregate-only**
```
field: Person.Age
direct_access: [hr_role, self]
aggregate_access: [everyone]
min_group_size: 5
```
Only HR and the person themselves see specific ages. Anyone can see aggregates over groups of 5+. A query for "average age of a 3-person team" is denied or falls back to a larger cohort.

**Policy 3 — Tiered with bucketing**
```
field: Person.Salary
direct_access: [hr_role, self]
bucketed_access: [manager_role]    # e.g. "80-100k"
aggregate_access: [everyone]
min_group_size: 10
```
HR sees raw salary; managers see ranges; everyone else sees aggregates over 10+.

**Policy 4 — Source + schema combined**
```
source: email_thread_2024_acquisition
direct_access: [exec_role]
aggregate_access: [exec_role, finance_role]
derived_reasoning_access: [exec_role, finance_role, legal_role]
```
The raw thread is exec-only. Aggregate facts are visible to finance. Reasoning that mixes this source with others is visible to legal as well — once diluted, the sensitivity is lower.

**Policy 5 — Self-referential with purpose**
```
field: Person.MedicalNotes
direct_access: [self, doctor_role]
aggregate_access: [researcher_role]
min_group_size: 50
purpose: ["medical_research"]
```
Self and treating doctors see raw notes. Researchers see only aggregates over 50+ people, and only for declared research purposes.

### 6.5 Patterns Emerging
- Three access levels (direct / bucketed / aggregate) cover most real cases
- `self` is a powerful primitive — most personal data should default to self-readable
- `purpose` is a useful orthogonal dimension on top of role
- **Reasoning has its own ACL tier**, looser than the underlying source — mixing dilutes sensitivity, so reasoning derived from many sources can be more broadly visible than any single source

### 6.6 Open Questions on Schema ACLs
- How is "aggregate enough" enforced rigorously? Min group size is a start, but determined attackers can still extract individuals via repeated queries (the differential privacy problem)
- Do we need explicit support for **bucketing strategies** (fixed ranges, quantiles, learned bins)?
- How are policies authored and managed — declarative config, UI, derived from roles?
- How do we audit which policy was applied to a given query result?

---

## 7. Architecture

### 7.1 Layered Model

```
┌─────────────────────────────────────────────────────┐
│  MCP Server (ingest / memorize / forget / update)   │
├─────────────────────────────────────────────────────┤
│  Reasoning Engine (LLM-backed, ingestion-time)      │
├─────────────────────────────────────────────────────┤
│  Access Control Layer (source ACL + schema ACL)     │
├─────────────────────────────────────────────────────┤
│  Lineage Graph (SQL-modelable, in SQLite or similar)│
├─────────────────────────────────────────────────────┤
│  Semantic Index (token-level, ColBERT-style)        │
├─────────────────────────────────────────────────────┤
│  Object Store (S3 / GCS) — fragment blobs + index   │
└─────────────────────────────────────────────────────┘
```

The semantic index answers "**what's relevant?**" The access control layer answers "**what is this user allowed to see?**" Keeping these separate keeps the index simple and the ACL layer clean and auditable.

### 7.2 Data Separation

- **Reasoning fragments** (the bulk of the data) → blobs in S3
- **Lineage graph + ACL rules + schema** (smaller, queryable) → lightweight SQL store (SQLite is fine at our scale)
- **Semantic index** → managed by the retrieval substrate (implementation detail)

This split is the key insight: the heavy data goes in cheap blob storage; the queryable metadata goes in a small relational store. Together they fit cloud-native goals.

### 7.3 Implementation Options Considered

#### Option 1: Pure Blob Store
Fragments + lineage all as JSON blobs in S3. Semantic index on top.
- ✅ Cheapest, simplest, fully cloud-native
- ❌ Hard to query by ACL or schema without scanning everything

#### Option 2: Hybrid (Recommended)
SQLite/DuckDB file in S3 for lineage + schema + ACL metadata. Fragments as blobs in S3. Semantic index on top.
- ✅ Relational queries for filtering without a server
- ✅ SQLite concurrency is fine at this scale (~3 writes/sec)
- ❌ Single-writer SQLite needs care around concurrent updates

#### Option 3: Serverless Database
DynamoDB / Firestore for fragments + lineage. Semantic index on top.
- ✅ Handles concurrency natively, scales easily
- ❌ More expensive, vendor lock-in, harder lineage queries across fragments

**Decision**: Option 2 (Hybrid) for v1 and v2. Revisit Option 3 if scale grows beyond what SQLite handles comfortably.

### 7.4 The Cloud-Native Caveat

The semantic index is typically backed by a SQLite file on local disk. To stay cloud-native, options include:
- Mount the SQLite file from S3 via something like `s3fs` or pull-and-push around operations (works at low write rates)
- Run a thin stateful service that holds the SQLite locally and syncs periodically
- Use Litestream or similar for SQLite replication to S3

This is not specific to any one library — it's a general property of file-backed indexes.

---

## 8. Roadmap

### v1 — Minimum Viable Memory

**Goal**: prove the core loop works end-to-end at small scale.

- Hybrid architecture: SQLite (in S3) + blob fragments + semantic index
- **Source-level access control only** (skip schema-level for v1)
- Simple lineage: each fragment records the set of source document IDs it was derived from
- Append-only schema with hand-defined types
- **Ingestion-time reasoning only** — no retrieval-time derivation, no background reconciliation
- ACL filtering at query time: drop reasoning whose source set isn't fully accessible
- No core/supporting distinction yet — a fragment is either fully visible or fully filtered

**Open question for v1**: how strict is "fully accessible"? Probably strict (any inaccessible source → fragment hidden) for simplicity.

### v2 — Smarter Lineage & Reconciliation

**Goal**: handle the hard cases — partial access, source removal, redundancy.

- **Schema-level access control** added on top of source-level, with tiered access (direct / bucketed / aggregate) and `min_group_size` for aggregates
- **Corroboration tracking**: each fact in a fragment records how many independent sources support it
- **Background reconciliation job**: periodically scan the graph, identify holes from removed/inaccessible sources, recompute affected fragments
- **Partial visibility**: when a user can see some but not all sources of a fragment, derive a user-specific view (either by re-running reasoning on the visible subset or by storing fact-level lineage)
- **Schema versioning**: each fragment tagged with the schema version it was computed under
- Optional: cheap long-context model as a fallback for bulk retrieval queries

### v3 / Later

- DBOS-style durable workflows for the reconciliation pipeline (to investigate)
- Multi-tenant deployments
- Cross-user shared memory with provenance (cf. Collaborative Memory paper)
- Richer schema evolution tooling (deprecation flows, migration helpers)
- Differential-privacy-aware aggregate queries

---

## 9. Things to Forget

- Modeling everything as a graph database — relational works fine at this scale
- Strict per-user, per-query reasoning recomputation as the default — too expensive
- Treating the semantic index as natively structured-data aware — it's not
- Trying to solve schema-level ACL and source-level ACL simultaneously in v1
- Core/supporting tagging as the lineage primitive — corroboration count is better

## 10. Things to Go Deeper On

- **DBOS** (Berkeley) — database-primitive-based agent orchestration; likely relevant to the reconciliation pipeline
- **Collaborative Memory** paper — multi-user memory with dynamic access control and provenance attributes on fragments
- Exact metadata schema for lineage records: source IDs, timestamps, corroboration count, contribution weight, schema version, agent/model identity
- Concrete benchmarks at our target scale (small org, 3 reads/sec, low GB of data)
- Differential privacy primitives for the aggregate-access tier
- Bucketing strategies for the bucketed-access tier (fixed ranges, quantiles, learned bins)
- Audit logging — which policy fired for which query, for compliance

---

## 11. Glossary

- **Fragment** — a stored unit of reasoning, derived from one or more documents
- **Source** — a document (email, file, etc.) that contributed to one or more fragments
- **Lineage** — the metadata tying a fragment back to its sources, with attributes (corroboration count, schema version, etc.)
- **Corroboration count** — for a given fact in a fragment, how many independent sources back it
- **Schema** — the typed structure of memorable entities (Person, Conversation, Decision, etc.)
- **Direct access** — see raw field values
- **Bucketed access** — see coarsened/anonymized field values
- **Aggregate access** — see only values computed over groups of size ≥ k
- **Derived reasoning access** — see reasoning that incorporated a field, possibly mixed with other sources
- **Reconciliation** — periodic background process to repair the lineage graph after access or source changes
