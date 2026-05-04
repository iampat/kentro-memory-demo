/* global React, ReactDOM, K */
// PR 14 — full prototype-match layout. Everything driven by live server data.
//
// Topbar:        brand · KENTRO · rule-version chip · conflict-policy chip
// Main grid:     ┌────────────────┬────────────────┬────────────────┐
//                │ Sales agent    │ CS agent       │ Policy editor  │
//                ├────────────────┼────────────────┼────────────────┤
//                │ Extraction     │ Reasoning graph│ Access matrix  │
//                └────────────────┴────────────────┴────────────────┘
// Drawer:        slide-over from the right, opened by per-field click
//                in either AgentPanel.
// EscalationToast: bottom-right SSE-driven toast, kept from PR 10-5.
//
// Data shape comes from the kentro-server endpoints documented in api.js;
// no canned data anywhere. The `data.js` and prototype `KENTRO_DATA` blob
// are GONE — never imported.

const { useState, useEffect, useCallback } = React;

// === Bootstrap silently — agent-switcher.jsx is retired (PR 14). =============
//
// The prototype's old AgentSwitcher dropdown is gone; the demoer now sees both
// Sales and Customer Service panels side-by-side. We still need to fetch all
// three bearer tokens at boot, so the bootstrap call lives here.

function useBootstrap() {
  const [ready, setReady] = useState(K.api.getAgentList().length > 0);
  const [bootError, setBootError] = useState(null);
  const [tenantId, setTenantId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const result = await K.api.bootstrap();
      if (cancelled) return;
      if (result.ok) {
        setTenantId(result.payload.tenant_id);
        setReady(true);
        window.dispatchEvent(
          new CustomEvent("kentro:bootstrapped", { detail: result.payload })
        );
      } else {
        setBootError(result.error || `bootstrap failed: ${result.status}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Recover tenant_id from cache if bootstrap was already done in a prior load.
  useEffect(() => {
    if (tenantId) return;
    const list = K.api.getAgentList();
    if (list.length > 0) {
      // The cache stores it on the parent payload; refetch lightly.
      K.api.bootstrap().then((r) => {
        if (r.ok) setTenantId(r.payload.tenant_id);
      });
    }
  }, [tenantId]);

  return { ready, bootError, tenantId };
}

// === Small shared bits =====================================================

function StatusPill({ status }) {
  const u = status.toUpperCase();
  return <span className={`field-status status-${status}`}>{u}</span>;
}

const fmtFieldValue = K.fmtFieldValue;

// === <AgentPanel> ===========================================================
// One panel per agent. Each fetches its own GET /entities/{type}/{key} using
// THAT agent's bearer (so Sales and Customer Service really see different
// memory states side-by-side).

function AgentPanel({
  agentId,
  label,
  description,
  query,
  onQuery,
  onFieldClick,
  refresh,
  lastWriteResult,
  onAttemptWrite,
}) {
  const [record, setRecord] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    K.api
      .readEntityAs(agentId, query.type, query.key)
      .then((r) => {
        if (!cancelled) setRecord(r);
      })
      .catch((err) => {
        if (!cancelled) {
          setRecord(null);
          setError(err.message || String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, query.type, query.key, refresh]);

  // Tag class — sales|cs match the prototype CSS; everything else falls back
  // to a generic .agent-tag.
  const tagCls = agentId === "customer_service" ? "cs" : agentId;
  // The "all fields hidden" case happens when EntityVisibilityRule denies the
  // entity entirely; the server returns 200 with an empty fields dict.
  const fieldNames = record ? Object.keys(record.fields || {}) : [];

  return (
    <div className="panel">
      <div className="panel-head">
        <span className={K.cls("agent-tag", tagCls)}>
          <span className="swatch"></span>
          {label}
        </span>
        <span className="panel-sub">{description}</span>
      </div>
      <div className="panel-body">
        <div className="query-row">
          <select
            value={query.type}
            onChange={(e) => onQuery({ ...query, type: e.target.value })}
          >
            <option>Customer</option>
            <option>Person</option>
            <option>Deal</option>
            <option>AuditLog</option>
            <option>Note</option>
          </select>
          <input
            value={query.key}
            onChange={(e) => onQuery({ ...query, key: e.target.value })}
          />
          <button onClick={() => onQuery({ ...query })}>read</button>
        </div>

        {lastWriteResult && lastWriteResult.agent === agentId && (
          <div className={K.cls("banner", lastWriteResult.allowed ? "ok" : "deny")}>
            <span className="banner-icon">{lastWriteResult.allowed ? "✓" : "⊘"}</span>
            <span>{lastWriteResult.message}</span>
          </div>
        )}

        {loading && <p style={{ padding: 14, color: "var(--ink-3)", fontSize: 11 }}>loading…</p>}

        {!loading && error && (
          <div className="invisible-entity">
            ⊘ {query.type}.{query.key} — {error}
          </div>
        )}

        {!loading && !error && record && fieldNames.length === 0 && (
          <div className="invisible-entity">
            ⊘ {query.type}.{query.key} — no fields visible to this agent
          </div>
        )}

        {!loading && !error && record && fieldNames.length > 0 && (
          <div className="record">
            <div className="record-head">
              <span className="key">
                {query.type}.{query.key}
              </span>
              <span className="type">{query.type}</span>
            </div>
            {fieldNames.map((fname) => {
              const f = record.fields[fname];
              const status = f.status;
              return (
                <div
                  key={fname}
                  className={K.cls(
                    "field",
                    status === "hidden" && "hidden",
                    status === "unresolved" && "unresolved"
                  )}
                  onClick={() => onFieldClick(agentId, query.type, query.key, fname)}
                >
                  <span className="field-name">{fname}</span>
                  <span className="field-value">{fmtFieldValue(f)}</span>
                  <StatusPill status={status} />
                </div>
              );
            })}
          </div>
        )}

        {/* Quick action buttons mirror the prototype's per-agent demo writes. */}
        <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {agentId === "sales" && (
            <button
              className="ghost-btn"
              onClick={() =>
                onAttemptWrite("sales", "Customer", "Acme", "sales_notes", "follow up Mon")
              }
            >
              + write sales_notes
            </button>
          )}
          {agentId === "customer_service" && (
            <>
              <button
                className="ghost-btn"
                onClick={() =>
                  onAttemptWrite("customer_service", "Customer", "Acme", "deal_size", "$275K")
                }
              >
                attempt write deal_size
              </button>
              <button
                className="ghost-btn"
                onClick={() =>
                  onAttemptWrite(
                    "customer_service",
                    "Customer",
                    "Acme",
                    "support_tickets",
                    "#170 (refund)"
                  )
                }
              >
                + add support_ticket
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// === <PolicyEditor> ========================================================
// Live ruleset rendered via the new GET /rules/active/rendered endpoint, with
// the prototype's NL-chat → parse → apply flow on top.

const SUGGESTIONS = [
  {
    label: "Hide deal_size from CS",
    text: "Hide deal_size from Customer Service.",
  },
  {
    label: "Prefer written over verbal",
    text: "On Customer.deal_size, written sources outweigh verbal.",
  },
  {
    label: "CS reads support_tickets only",
    text: "Customer Service can read support_tickets but not edit them.",
  },
  {
    label: "Hide AuditLog from Sales",
    text: "Sales cannot see AuditLog.",
  },
];

function policyKindOf(rule) {
  switch (rule.type) {
    case "field_read":
      return "access";
    case "entity_visibility":
      return "access";
    case "write":
      return rule.requires_approval ? "condition" : "access";
    case "conflict":
      return "conflict";
    default:
      return "access";
  }
}

function PolicyEditor({ refresh, onApplied }) {
  const [rendered, setRendered] = useState({ version: 0, rules: [] });
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [parsed, setParsed] = useState(null);
  const [parsing, setParsing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [expanded, setExpanded] = useState({});
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const r = await K.api.getRulesRendered();
      setRendered(r);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload, refresh]);

  const onParse = async () => {
    if (!draft.trim()) return;
    setParsing(true);
    setError(null);
    try {
      const r = await K.api.parseNL(draft);
      setParsed(r);
    } catch (err) {
      setError(err.message);
      setParsed(null);
    } finally {
      setParsing(false);
    }
  };

  const onApply = async () => {
    if (!parsed?.parsed_ruleset) return;
    setApplying(true);
    setError(null);
    try {
      // Append parsed rules onto the current ruleset and apply atomically.
      // The current rules come from the active ruleset (need raw form, not
      // rendered). Pull a fresh copy via getRules to be safe.
      const current = await K.api.getRules();
      const merged = {
        version: 0,
        rules: [...(current.rules || []), ...(parsed.parsed_ruleset.rules || [])],
      };
      const result = await K.api.applyRules(merged, draft);
      setDraft("");
      setParsed(null);
      await reload();
      onApplied?.(result.version);
    } catch (err) {
      setError(err.message);
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Policies</span>
        <span className="panel-sub">PBAC · Rego</span>
        <span className="spacer" />
        <span
          className="rule-chip"
          title="Active policy version — increments each time policies are applied"
        >
          <span className="dot"></span>Policies · version {rendered.version}
        </span>
      </div>
      <div className="panel-body">
        <div className="policy-section">
          <div className="policy-list">
            {loading && <p style={{ padding: 8, color: "var(--ink-3)", fontSize: 11 }}>loading…</p>}
            {!loading && rendered.rules.length === 0 && (
              <p style={{ padding: 8, color: "var(--ink-3)", fontSize: 11 }}>
                no rules — apply some via the chat below
              </p>
            )}
            {!loading &&
              (() => {
                // Group rules by their Rego package so the redundant
                // `package kentro.access` / `package kentro.resolve` header
                // appears once per group instead of repeated per rule.
                const groups = new Map();
                rendered.rules.forEach((r, i) => {
                  const pkg = r.package || "kentro.access";
                  if (!groups.has(pkg)) groups.set(pkg, []);
                  groups.get(pkg).push({ rule: r, idx: i });
                });
                return Array.from(groups.entries()).map(([pkg, items]) => (
                  <div key={pkg} className="policy-package-group">
                    <div className="policy-package-header">
                      <span className="policy-package-tag">package</span>
                      <span className="policy-package-name">{pkg}</span>
                      <span className="policy-package-count">
                        {items.length} {items.length === 1 ? "rule" : "rules"}
                      </span>
                    </div>
                    {items.map(({ rule: r, idx: i }) => {
                      const kind = policyKindOf(parseRuleSummary(r.summary));
                      const isExpanded = expanded[i];
                      return (
                        <div
                          key={i}
                          className={K.cls("policy-row", `kind-${kind}`)}
                          onClick={() =>
                            setExpanded({ ...expanded, [i]: !isExpanded })
                          }
                        >
                          <div className="policy-row-main">
                            <span className={K.cls("policy-kind", `kind-${kind}`)}>
                              {kind}
                            </span>
                            <span className="policy-summary">{r.summary}</span>
                            <span className="policy-toggle">
                              {isExpanded ? "▾" : "▸"}
                            </span>
                          </div>
                          {isExpanded && (
                            <pre className="policy-rego">
                              {r.rego_body || r.rego}
                            </pre>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ));
              })()}
          </div>

          <div className="suggestion-row">
            <span className="suggestion-label">Try:</span>
            {SUGGESTIONS.map((s, i) => (
              <button
                key={i}
                className="suggestion-chip kind-edit"
                onClick={() => {
                  setDraft(s.text);
                  setParsed(null);
                }}
                title={s.text}
              >
                <span className="chip-kind">edit</span>
                {s.label}
              </button>
            ))}
          </div>

          <div className="chat-box">
            <textarea
              className="chat-input"
              placeholder="describe a change in plain English, or pick a suggestion"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={2}
            />
            <div className="chat-actions">
              <span className={K.cls("parse-status", parsed && "parsed")}>
                {parsing
                  ? "reading your request…"
                  : parsed
                    ? `${parsed.parsed_ruleset?.rules?.length || 0} change(s) ready — review, then apply`
                    : "describe a change, then parse"}
              </span>
              <button
                className="primary"
                onClick={onParse}
                disabled={parsing || !draft.trim()}
              >
                parse
              </button>
            </div>
          </div>

          {parsed && parsed.parsed_ruleset?.rules?.length > 0 && (
            <div className="edit-preview">
              <div className="edit-preview-head">Pending changes</div>
              {parsed.parsed_ruleset.rules.map((r, i) => (
                <div key={i} className="edit-row op-add">
                  <span className="edit-op op-add">+ add</span>
                  <span className="edit-summary">
                    {describeRule(r)}
                    {parsed.notes && i === 0 && (
                      <span className="edit-diff">{parsed.notes}</span>
                    )}
                  </span>
                </div>
              ))}
              <div className="apply-row inline">
                <button
                  className="secondary"
                  onClick={() => {
                    setParsed(null);
                    setDraft("");
                  }}
                >
                  cancel
                </button>
                <button onClick={onApply} disabled={applying}>
                  {applying ? "applying…" : "apply changes"}
                </button>
              </div>
            </div>
          )}

          {error && (
            <div
              style={{
                color: "var(--bad)",
                fontFamily: "var(--mono)",
                fontSize: 10,
                padding: "6px 0",
              }}
            >
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Mirror of kentro.rules.render_rule shape so we can reverse-look at the kind.
// The rendered summary doesn't carry a discriminator, so we infer from the
// first token (`[allow]` / `[deny]` / `[hidden]` / `[skill]` / etc.).
function parseRuleSummary(summary) {
  const lower = (summary || "").toLowerCase();
  if (lower.includes("resolves")) return { type: "conflict" };
  if (lower.includes(" sees ")) return { type: "entity_visibility" };
  if (lower.includes(" writes ")) return { type: "write", requires_approval: lower.includes("requires_approval") };
  return { type: "field_read" };
}

function describeRule(r) {
  const a = r.agent_id || "*";
  switch (r.type) {
    case "field_read":
      return `${r.allowed ? "allow" : "deny"} ${a} reads ${r.entity_type}.${r.field_name}`;
    case "write":
      return `${r.allowed ? "allow" : "deny"} ${a} writes ${r.entity_type}.${r.field_name || "*"}${r.requires_approval ? " (approval)" : ""}`;
    case "entity_visibility":
      return `${r.allowed ? "allow" : "hide"} ${a} sees ${r.entity_type}${r.entity_key ? `/${r.entity_key}` : ""}`;
    case "conflict":
      return `${r.resolver?.type || "?"} resolves ${r.entity_type}.${r.field_name}`;
    default:
      return JSON.stringify(r);
  }
}

// === <App> =================================================================

function App() {
  const { ready, bootError } = useBootstrap();
  const [salesQuery, setSalesQuery] = useState({ type: "Customer", key: "Acme Corp" });
  const [csQuery, setCsQuery] = useState({ type: "Customer", key: "Acme Corp" });
  const [drawerPayload, setDrawerPayload] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Two right-edge overlays share the same slot — opening one closes the
  // other so we never end up with a stack. SourceOverlay shows the raw
  // document content; EntityOverlay shows global + per-agent views of an
  // entity record. Both live at app level so they can cover Policy / Access
  // Matrix, not just the panel they were triggered from.
  const [sourceDocId, setSourceDocId] = useState(null);
  const [entityPayload, setEntityPayload] = useState(null);
  const openSourceDoc = useCallback((id) => {
    setEntityPayload(null);
    setSourceDocId(id);
  }, []);
  const openEntity = useCallback((p) => {
    setSourceDocId(null);
    setEntityPayload(p);
  }, []);
  const [refresh, setRefresh] = useState(0);
  const bumpRefresh = useCallback(() => setRefresh((n) => n + 1), []);
  const [documents, setDocuments] = useState([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [conflictPolicy, setConflictPolicy] = useState("auto");
  const [ruleVersion, setRuleVersion] = useState(0);
  const [lastWriteResult, setLastWriteResult] = useState(null);
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState(null);
  const [pendingDoc, setPendingDoc] = useState(false);
  const [schemaTypes, setSchemaTypes] = useState([]);

  // Load documents + active ruleset whenever refresh bumps.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      setDocsLoading(true);
      try {
        const docs = await K.api.listDocuments();
        if (!cancelled) setDocuments(docs);
      } catch {
        if (!cancelled) setDocuments([]);
      } finally {
        if (!cancelled) setDocsLoading(false);
      }
      try {
        const r = await K.api.getRules();
        if (!cancelled) {
          setRuleVersion(r.version || 0);
          // Find conflict policy from active rules.
          const conflicts = (r.rules || []).filter((rl) => rl.type === "conflict");
          if (conflicts.length > 0) {
            const r0 = conflicts[0].resolver?.type || "auto";
            setConflictPolicy(
              r0 === "skill"
                ? "written outweighs verbal"
                : r0 === "latest_write"
                  ? "latest write wins"
                  : r0
            );
          } else {
            setConflictPolicy("auto");
          }
        }
      } catch {
        // ignore
      }
      try {
        const types = await K.api.listSchema();
        if (!cancelled) setSchemaTypes(types);
      } catch {
        if (!cancelled) setSchemaTypes([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, refresh]);

  const isEmpty = ready && !docsLoading && documents.length === 0 && schemaTypes.length <= 1;

  const onSeed = async () => {
    setSeeding(true);
    setSeedError(null);
    try {
      await K.api.seedDemo();
      bumpRefresh();
    } catch (err) {
      setSeedError(err.message);
    } finally {
      setSeeding(false);
    }
  };

  const onIngestEmail = async () => {
    setPendingDoc(true);
    try {
      await K.api.ingestDocument(
        // Minimal email payload; the LLM extractor decides which entities to
        // touch. The text is the same content the prototype's data.js used.
        `Subject: Acme renewal — updated number\n\nFrom: jane.doe@acme.example\nDate: 2026-04-17 14:08\n\nHi team,\n\nFollowing my call with finance, the renewal will be $300K — we got the additional uplift signed off. Please update the deal record.\n\nThanks,\nJane`,
        "email_jane_2026-04-17.md",
        "email"
      );
      bumpRefresh();
    } catch (err) {
      alert(`ingest failed: ${err.message}`);
    } finally {
      setPendingDoc(false);
    }
  };

  const onAttemptWrite = async (agentId, type, key, field, value) => {
    try {
      // Use the agent's own bearer (NOT admin) — the whole point of the
      // demo button is to surface the ACL check.
      const agentKey = K.api.getKeyFor(agentId);
      if (!agentKey) {
        setLastWriteResult({
          agent: agentId,
          allowed: false,
          message: `no api key cached for agent_id=${agentId}`,
        });
        return;
      }
      const result = await K.api._fetch(
        `/entities/${encodeURIComponent(type)}/${encodeURIComponent(key)}/${encodeURIComponent(field)}`,
        {
          method: "POST",
          body: JSON.stringify({ value_json: JSON.stringify(value) }),
          bearerKey: agentKey,
        }
      );
      const allowed = result.status === "applied" || result.status === "conflict_recorded";
      setLastWriteResult({
        agent: agentId,
        allowed,
        message: `WriteResult.${result.status?.toUpperCase()} → ${type}.${key}.${field}${
          result.reason ? ` (${result.reason})` : ""
        }`,
      });
      bumpRefresh();
    } catch (err) {
      setLastWriteResult({ agent: agentId, allowed: false, message: err.message });
    }
  };

  const onFieldClick = (agentId, type, key, fname) => {
    setDrawerPayload({
      agent_id: agentId,
      entity_type: type,
      entity_key: key,
      field_name: fname,
    });
    setDrawerOpen(true);
  };

  if (bootError) {
    return (
      <div className="app">
        <div style={{ padding: 40, color: "var(--bad)", fontFamily: "var(--mono)" }}>
          bootstrap failed: {bootError}
        </div>
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="app">
        <div style={{ padding: 40, color: "var(--ink-3)" }}>connecting…</div>
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="app">
        <div className="topbar">
          <span className="brand">
            <span className="brand-mark"></span>KENTRO
          </span>
          <span className="spacer" />
        </div>
        <div className="seed-overlay">
          <h2>empty tenant</h2>
          <p className="seed-blurb">
            No schemas registered, no documents ingested. Click below to seed the demo
            world: 4 entity types (<code>Customer</code>, <code>Person</code>, <code>Deal</code>,{" "}
            <code>AuditLog</code>), the canonical 29 ACL rules, and 8 markdown documents
            from the synthetic corpus.
          </p>
          <button onClick={onSeed} disabled={seeding} className="seed-btn">
            {seeding ? "seeding (LLM, ~30s)…" : "seed demo data"}
            <span className="admin-badge">↑admin</span>
          </button>
          {seedError && <div className="seed-error">{seedError}</div>}
          <p className="seed-tail">
            equivalent to <code>task reset-and-seed</code>; safe to re-run.
          </p>
        </div>
        <K.EscalationToast />
      </div>
    );
  }

  return (
    <div className="app" data-screen-label="Kentro Demo">
      <div className="topbar">
        <span className="brand">
          <span className="brand-mark"></span>KENTRO
        </span>
        <span className="spacer" />
        <span
          className="rule-chip"
          title="Active rule set version — increments on every /rules/apply"
        >
          <span className="dot"></span>Rules · version {ruleVersion}
        </span>
        <span className="rule-chip" title="How conflicting values are resolved at read time">
          Conflict policy: {conflictPolicy}
        </span>
      </div>

      <div className="main">
        <AgentPanel
          agentId="sales"
          label="Sales agent"
          description="reads & writes deal info"
          query={salesQuery}
          onQuery={setSalesQuery}
          onFieldClick={onFieldClick}
          refresh={refresh}
          lastWriteResult={lastWriteResult}
          onAttemptWrite={onAttemptWrite}
        />
        <AgentPanel
          agentId="customer_service"
          label="Customer Service agent"
          description="handles support tickets"
          query={csQuery}
          onQuery={setCsQuery}
          onFieldClick={onFieldClick}
          refresh={refresh}
          lastWriteResult={lastWriteResult}
          onAttemptWrite={onAttemptWrite}
        />
        <PolicyEditor refresh={refresh} onApplied={bumpRefresh} />

        <K.WorkPanel
          documents={documents}
          onIngestEmail={onIngestEmail}
          pendingDoc={pendingDoc}
          refresh={refresh}
          highlightField={drawerPayload}
          onOpenDoc={openSourceDoc}
          onOpenEntity={openEntity}
        />
        <K.AccessMatrixPanel
          entityType="Customer"
          refresh={refresh}
          changedKeys={[]}
        />
      </div>

      <K.LineageDrawer
        open={drawerOpen}
        payload={drawerPayload}
        onClose={() => setDrawerOpen(false)}
        shifted={!!entityPayload}
        documents={documents}
      />
      <K.SourceOverlay
        open={!!sourceDocId}
        documentId={sourceDocId}
        onClose={() => setSourceDocId(null)}
      />
      <K.EntityOverlay
        open={!!entityPayload}
        payload={entityPayload}
        onClose={() => setEntityPayload(null)}
        refresh={refresh}
        onFieldClick={(payload) => {
          setDrawerPayload(payload);
          setDrawerOpen(true);
        }}
      />
      <K.EscalationToast />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
