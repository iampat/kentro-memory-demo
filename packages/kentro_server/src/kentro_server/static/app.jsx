/* global React, ReactDOM, K */
// Stage A — Live view rendered entirely from kentro-server data.
//
// Layout:
//   ┌──────────────────── header (AgentSwitcher) ────────────────────┐
//   │ left col            │  center col              │ right col      │
//   │ Documents           │  Entities (per type)     │ Active Ruleset │
//   │ (from /documents)   │  (from /entities/{type}) │ (from /rules)  │
//   │                     │                          │                │
//   │ click row → drawer  │  click row → drawer      │  read-only     │
//   └────────────────────────────────────────────────────────────────┘
//
// `data.js` is loaded as a fallback ONLY: if the tenant is empty (no docs, no
// entities) we render a "Seed demo data" hint pointing at `task reset-and-seed`.
// PR 10-4 wires that to a button. PR 10-3 turns this view's read-only ruleset
// pane into a full two-pane policy editor.

const { useState, useEffect, useCallback } = React;

const ENTITY_TYPES = ["Customer", "Person", "Deal", "AuditLog", "Note"];

// ── helpers ─────────────────────────────────────────────────────────────────

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function describeRule(r) {
  // Mirror render_rule(...) on the server — rendered as a single line.
  const tag = (label) => `[${label}]`;
  const a = r.agent_id || "*";
  const t = r.entity_type;
  if (r.field_name === undefined) {
    /* fall through */
  }
  switch (r.type) {
    case "field_read":
      return `${tag(r.allowed ? "allow" : "deny")} ${a} reads ${t}.${r.field_name}`;
    case "write": {
      const f = r.field_name || "*";
      const ap = r.requires_approval ? " (requires_approval)" : "";
      return `${tag(r.allowed ? "allow" : "deny")} ${a} writes ${t}.${f}${ap}`;
    }
    case "entity_visibility": {
      const target = r.entity_key ? `${t}/${r.entity_key}` : `${t}.*`;
      return `${tag(r.allowed ? "allow" : "hidden")} ${a} sees ${target}`;
    }
    case "conflict": {
      const resolver = r.resolver?.type || "?";
      return `[${resolver}] ${t}.${r.field_name} resolved`;
    }
    default:
      return `[?] ${JSON.stringify(r)}`;
  }
}

// ── components ──────────────────────────────────────────────────────────────

function StatusPill({ status }) {
  const color = {
    known: "var(--accent, #4ade80)",
    unknown: "#9ca3af",
    hidden: "#7c3aed",
    unresolved: "#f59e0b",
  }[status] || "#9ca3af";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 6px",
        borderRadius: 3,
        fontSize: 9,
        fontFamily: "var(--mono)",
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        background: color,
        color: "white",
      }}
    >
      {status}
    </span>
  );
}

function DocumentsPanel({ documents, loading, error, onPick, activeDocId }) {
  return (
    <section style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <header
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--line)",
          fontFamily: "var(--mono)",
          fontSize: 11,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--ink-2)",
        }}
      >
        documents · {documents.length}
      </header>
      <div style={{ overflowY: "auto", flex: 1 }}>
        {loading && <p style={{ padding: 14, color: "var(--ink-3)" }}>loading…</p>}
        {error && (
          <p style={{ padding: 14, color: "#ef4444", fontFamily: "var(--mono)", fontSize: 11 }}>
            error: {error}
          </p>
        )}
        {!loading && !error && documents.length === 0 && (
          <p style={{ padding: 14, color: "var(--ink-3)", fontSize: 12 }}>
            No documents yet. Run <code>task reset-and-seed</code> to populate.
          </p>
        )}
        {documents.map((d) => (
          <button
            key={d.id}
            onClick={() => onPick(d)}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              padding: "10px 14px",
              borderBottom: "1px solid var(--line)",
              background: activeDocId === d.id ? "var(--surface-2, #1f2937)" : "transparent",
              color: "var(--ink-1)",
              border: "none",
              borderBottom: "1px solid var(--line)",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            <div style={{ fontWeight: 500, fontSize: 13 }}>{d.label || d.id}</div>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink-3)",
                marginTop: 4,
                display: "flex",
                gap: 8,
              }}
            >
              <span>{d.source_class || "—"}</span>
              <span>·</span>
              <span>{fmtDate(d.created_at)}</span>
              <span>·</span>
              <span>{d.field_write_count} writes</span>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function EntitiesPanel({ acting, ready, onPickEntity }) {
  const [byType, setByType] = useState({}); // {Customer: [...], Person: [...], ...}
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = {};
      for (const t of ENTITY_TYPES) {
        try {
          next[t] = await K.api.listEntities(t);
        } catch (err) {
          // 401 means the active agent has no auth — flag once.
          next[t] = [];
          if (err.status === 401) {
            setError("auth failed for this agent — re-bootstrap with admin");
            break;
          }
        }
      }
      setByType(next);
    } finally {
      setLoading(false);
    }
  }, [acting]);

  useEffect(() => {
    if (!ready) return;
    refresh();
  }, [refresh, ready]);

  return (
    <section style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <header
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--line)",
          fontFamily: "var(--mono)",
          fontSize: 11,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--ink-2)",
          display: "flex",
          gap: 12,
          alignItems: "center",
        }}
      >
        <span>entities · viewing as {acting}</span>
        <button
          onClick={refresh}
          className="ghost-btn"
          style={{ marginLeft: "auto" }}
          title="re-fetch /entities/{type} for every type"
        >
          refresh
        </button>
      </header>
      <div style={{ overflowY: "auto", flex: 1 }}>
        {loading && <p style={{ padding: 14, color: "var(--ink-3)" }}>loading…</p>}
        {error && (
          <p style={{ padding: 14, color: "#ef4444", fontFamily: "var(--mono)", fontSize: 11 }}>
            {error}
          </p>
        )}
        {!loading &&
          ENTITY_TYPES.map((t) => {
            const rows = byType[t] || [];
            return (
              <div key={t} style={{ marginBottom: 14 }}>
                <div
                  style={{
                    padding: "8px 14px 4px",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    letterSpacing: "0.06em",
                    color: "var(--ink-3)",
                    textTransform: "uppercase",
                  }}
                >
                  {t} · {rows.length} {rows.length === 1 ? "entity" : "entities"}
                  {rows.length === 0 && (
                    <span style={{ marginLeft: 8, color: "#7c3aed" }}>
                      (none visible to {acting})
                    </span>
                  )}
                </div>
                {rows.map((row) => (
                  <button
                    key={`${t}/${row.key}`}
                    onClick={() => onPickEntity(t, row.key)}
                    style={{
                      display: "flex",
                      width: "100%",
                      textAlign: "left",
                      padding: "8px 14px",
                      background: "transparent",
                      color: "var(--ink-1)",
                      border: "none",
                      cursor: "pointer",
                      fontFamily: "inherit",
                      borderLeft: "2px solid transparent",
                      gap: 8,
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-2)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <span style={{ flex: 1 }}>{row.key}</span>
                    <span
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        color: "var(--ink-3)",
                      }}
                    >
                      {row.field_count} {row.field_count === 1 ? "field" : "fields"}
                    </span>
                  </button>
                ))}
              </div>
            );
          })}
      </div>
    </section>
  );
}

function RulesPanel({ acting, ready }) {
  const [rules, setRules] = useState([]);
  const [version, setVersion] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const r = await K.api.getRules();
        if (cancelled) return;
        setRules(r.rules || []);
        setVersion(r.version);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [acting, ready]);

  // Group by rule type
  const groups = rules.reduce((acc, r) => {
    (acc[r.type] = acc[r.type] || []).push(r);
    return acc;
  }, {});

  return (
    <section style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <header
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--line)",
          fontFamily: "var(--mono)",
          fontSize: 11,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--ink-2)",
        }}
      >
        active ruleset · v{version ?? "—"} · {rules.length} rules
      </header>
      <div style={{ overflowY: "auto", flex: 1, padding: "10px 14px" }}>
        {loading && <p style={{ color: "var(--ink-3)" }}>loading…</p>}
        {error && (
          <p style={{ color: "#ef4444", fontFamily: "var(--mono)", fontSize: 11 }}>{error}</p>
        )}
        {!loading &&
          Object.entries(groups).map(([type, items]) => (
            <div key={type} style={{ marginBottom: 14 }}>
              <div
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 10,
                  letterSpacing: "0.06em",
                  color: "var(--ink-3)",
                  textTransform: "uppercase",
                  marginBottom: 6,
                }}
              >
                {type} · {items.length}
              </div>
              {items.map((r, i) => (
                <div
                  key={i}
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 11,
                    padding: "3px 0",
                    color: "var(--ink-2)",
                  }}
                >
                  {describeRule(r)}
                </div>
              ))}
            </div>
          ))}
      </div>
    </section>
  );
}

function EntityDrawer({ entity_type, entity_key, acting, onClose }) {
  const [record, setRecord] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!entity_type || !entity_key) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const r = await K.api.readEntity(entity_type, entity_key);
        if (!cancelled) setRecord(r);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Refetch on agent switch — same entity, different ACL slice.
  }, [entity_type, entity_key, acting]);

  if (!entity_type) return null;
  return (
    <div
      style={{
        position: "fixed",
        right: 0,
        top: 0,
        bottom: 0,
        width: 480,
        background: "var(--surface)",
        borderLeft: "1px solid var(--line)",
        zIndex: 100,
        display: "flex",
        flexDirection: "column",
        boxShadow: "-12px 0 32px rgba(0,0,0,0.4)",
      }}
    >
      <header
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--line)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-3)" }}>
          {entity_type}
        </span>
        <span style={{ fontWeight: 600 }}>{entity_key}</span>
        <button onClick={onClose} className="ghost-btn" style={{ marginLeft: "auto" }}>
          close
        </button>
      </header>
      <div style={{ overflowY: "auto", flex: 1, padding: "12px 16px" }}>
        {loading && <p style={{ color: "var(--ink-3)" }}>loading…</p>}
        {error && (
          <p style={{ color: "#ef4444", fontFamily: "var(--mono)", fontSize: 11 }}>{error}</p>
        )}
        {!loading &&
          record &&
          Object.entries(record.fields || {}).map(([fname, fval]) => (
            <div
              key={fname}
              style={{
                marginBottom: 12,
                padding: 10,
                border: "1px solid var(--line)",
                borderRadius: 4,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, fontWeight: 500 }}>
                  {fname}
                </span>
                <StatusPill status={fval.status} />
              </div>
              <div
                style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-2)" }}
              >
                {fval.status === "known" && (
                  <span>{JSON.stringify(fval.value)}</span>
                )}
                {fval.status === "unknown" && (
                  <span style={{ color: "var(--ink-3)" }}>(no writes yet)</span>
                )}
                {fval.status === "hidden" && (
                  <span style={{ color: "#7c3aed" }}>{fval.reason}</span>
                )}
                {fval.status === "unresolved" && (
                  <span>
                    UNRESOLVED — {(fval.candidates || []).length} candidates · {fval.reason}
                  </span>
                )}
              </div>
              {fval.lineage && fval.lineage.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 9,
                      color: "var(--ink-3)",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                      marginBottom: 2,
                    }}
                  >
                    lineage
                  </div>
                  {fval.lineage.map((l, i) => (
                    <div
                      key={i}
                      style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-2)" }}
                    >
                      ← {l.agent_id} · {l.source_document_label || l.source_document_id || "—"}{" "}
                      · v{l.rule_version_at_write}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}

// ── App root ────────────────────────────────────────────────────────────────

function App() {
  // `ready` flips to true once AgentSwitcher's bootstrap call resolves and the
  // bearer-token cache is populated. Without this, panels fire fetches with no
  // bearer token and render error UI on first paint.
  const [ready, setReady] = useState(K.api.getAgentList().length > 0);
  const [acting, setActing] = useState(K.api.getActingAs());
  const [documents, setDocuments] = useState([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [docsError, setDocsError] = useState(null);
  const [activeDocId, setActiveDocId] = useState(null);
  const [drawer, setDrawer] = useState(null); // {entity_type, entity_key} | null

  // Listen for bootstrap + agent-switcher events.
  useEffect(() => {
    const onBoot = () => setReady(true);
    const onSwitch = (e) => setActing(e.detail);
    window.addEventListener("kentro:bootstrapped", onBoot);
    window.addEventListener("kentro:actingAsChanged", onSwitch);
    return () => {
      window.removeEventListener("kentro:bootstrapped", onBoot);
      window.removeEventListener("kentro:actingAsChanged", onSwitch);
    };
  }, []);

  // Documents are tenant-scoped (not per-agent today), but refetch on agent
  // switch anyway so a future visibility filter on /documents takes effect.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      setDocsLoading(true);
      try {
        const docs = await K.api.listDocuments();
        if (!cancelled) {
          setDocuments(docs);
          setDocsError(null);
        }
      } catch (err) {
        if (!cancelled) setDocsError(err.message);
      } finally {
        if (!cancelled) setDocsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [acting, ready]);

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <K.AgentSwitcher />
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "320px 1fr 380px",
          minHeight: 0,
        }}
      >
        <div style={{ borderRight: "1px solid var(--line)", overflow: "hidden" }}>
          <DocumentsPanel
            documents={documents}
            loading={docsLoading}
            error={docsError}
            onPick={(d) => setActiveDocId(d.id)}
            activeDocId={activeDocId}
          />
        </div>
        <div style={{ overflow: "hidden" }}>
          <EntitiesPanel
            acting={acting}
            ready={ready}
            onPickEntity={(t, k) => setDrawer({ entity_type: t, entity_key: k })}
          />
        </div>
        <div style={{ borderLeft: "1px solid var(--line)", overflow: "hidden" }}>
          <RulesPanel acting={acting} ready={ready} />
        </div>
      </div>
      <EntityDrawer
        entity_type={drawer?.entity_type}
        entity_key={drawer?.entity_key}
        acting={acting}
        onClose={() => setDrawer(null)}
      />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
