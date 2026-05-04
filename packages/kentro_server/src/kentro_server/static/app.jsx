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
// Empty tenant: when /documents and /schema both return empty, the App renders
// a "seed demo data" overlay that POSTs /demo/seed (admin + opt-in gated).
// `data.js` was retired in PR 10-4 — every byte the UI shows now comes from the
// server. The two-pane policy editor lives in the right column (PR 10-3).

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

function IngestForm({ onIngested }) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  const [label, setLabel] = useState("");
  const [sourceClass, setSourceClass] = useState("written");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  const submit = async () => {
    if (!content.trim()) return;
    setPending(true);
    setError(null);
    try {
      await K.api.ingestDocument(content, label || `inline-${Date.now()}.md`, sourceClass);
      setContent("");
      setLabel("");
      setOpen(false);
      onIngested?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="ghost-btn"
        style={{ margin: "10px 14px", width: "calc(100% - 28px)" }}
      >
        + ingest a document
      </button>
    );
  }
  return (
    <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--line)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
        <input
          type="text"
          placeholder="label (e.g. acme-call-2026-04-15.md)"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          style={{
            flex: 1,
            background: "var(--bg)",
            color: "var(--ink-1)",
            border: "1px solid var(--line)",
            padding: "4px 8px",
            fontFamily: "var(--mono)",
            fontSize: 11,
          }}
        />
        <select
          value={sourceClass}
          onChange={(e) => setSourceClass(e.target.value)}
          style={{
            background: "var(--bg)",
            color: "var(--ink-1)",
            border: "1px solid var(--line)",
            padding: "4px 8px",
            fontFamily: "var(--mono)",
            fontSize: 11,
          }}
        >
          <option value="written">written</option>
          <option value="verbal">verbal</option>
          <option value="system">system</option>
        </select>
      </div>
      <textarea
        placeholder="paste markdown content…"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={5}
        style={{
          width: "100%",
          background: "var(--bg)",
          color: "var(--ink-1)",
          border: "1px solid var(--line)",
          padding: 8,
          fontFamily: "var(--mono)",
          fontSize: 11,
          marginBottom: 6,
          resize: "vertical",
        }}
      />
      {error && (
        <div style={{ color: "#ef4444", fontSize: 10, fontFamily: "var(--mono)", marginBottom: 6 }}>
          {error}
        </div>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={submit} className="ghost-btn" disabled={pending || !content.trim()}>
          {pending ? "ingesting…" : "ingest"}
        </button>
        <button onClick={() => setOpen(false)} className="ghost-btn" disabled={pending}>
          cancel
        </button>
      </div>
    </div>
  );
}

function DocumentsPanel({ documents, loading, error, onPick, activeDocId, onChange }) {
  const [deleting, setDeleting] = useState(null); // doc id while pending

  const remove = async (e, doc) => {
    e.stopPropagation();
    if (
      !window.confirm(`Delete "${doc.label || doc.id}"?\n\nThis cascades through writes and reopens any conflicts the doc was part of.`)
    ) {
      return;
    }
    setDeleting(doc.id);
    try {
      await K.api.deleteDocument(doc.id);
      onChange?.();
    } catch (err) {
      alert(`delete failed: ${err.message}`);
    } finally {
      setDeleting(null);
    }
  };

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
      <IngestForm onIngested={onChange} />
      <div style={{ overflowY: "auto", flex: 1 }}>
        {loading && <p style={{ padding: 14, color: "var(--ink-3)" }}>loading…</p>}
        {error && (
          <p style={{ padding: 14, color: "#ef4444", fontFamily: "var(--mono)", fontSize: 11 }}>
            error: {error}
          </p>
        )}
        {!loading && !error && documents.length === 0 && (
          <p style={{ padding: 14, color: "var(--ink-3)", fontSize: 12 }}>
            No documents yet. Use the ingest form above or run <code>task reset-and-seed</code>.
          </p>
        )}
        {documents.map((d) => (
          <div
            key={d.id}
            onClick={() => onPick(d)}
            style={{
              display: "block",
              padding: "10px 14px",
              borderBottom: "1px solid var(--line)",
              background: activeDocId === d.id ? "var(--surface-2, #1f2937)" : "transparent",
              cursor: "pointer",
              position: "relative",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ fontWeight: 500, fontSize: 13, flex: 1 }}>{d.label || d.id}</div>
              <button
                onClick={(e) => remove(e, d)}
                title="DELETE /documents/{id} — admin-elevated"
                disabled={deleting === d.id}
                className="ghost-btn"
                style={{ fontSize: 9, padding: "2px 6px" }}
              >
                {deleting === d.id ? "…" : "× del"}
                <span style={{ marginLeft: 4, color: "var(--accent, #4ade80)" }}>↑admin</span>
              </button>
            </div>
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
          </div>
        ))}
      </div>
    </section>
  );
}

function EntitiesPanel({ acting, ready, refresh, onPickEntity }) {
  const [byType, setByType] = useState({}); // {Customer: [...], Person: [...], ...}
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
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
    reload();
  }, [reload, ready, refresh]);

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
          onClick={reload}
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

function ruleKey(r) {
  // Order-independent identity for a rule. Mirrors `kentro.rules.ruleset_diff`
  // which compares by canonical JSON over the discriminated-union fields.
  // Used here to detect added/removed/unchanged on apply.
  return JSON.stringify(
    {
      type: r.type,
      agent_id: r.agent_id ?? null,
      entity_type: r.entity_type ?? null,
      field_name: r.field_name ?? null,
      entity_key: r.entity_key ?? null,
      allowed: r.allowed ?? null,
      requires_approval: r.requires_approval ?? null,
      resolver: r.resolver ?? null,
    },
    Object.keys({}).sort()
  );
}

function diffRulesets(oldRules, newRules) {
  const oldKeys = new Map(oldRules.map((r) => [ruleKey(r), r]));
  const newKeys = new Map(newRules.map((r) => [ruleKey(r), r]));
  const added = [];
  const removed = [];
  for (const [k, r] of newKeys) if (!oldKeys.has(k)) added.push(r);
  for (const [k, r] of oldKeys) if (!newKeys.has(k)) removed.push(r);
  return { added, removed };
}

function PolicyEditor({ acting, ready, onApplied }) {
  const [rules, setRules] = useState([]);
  const [version, setVersion] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // NL chat state
  const [draft, setDraft] = useState("");
  const [parsed, setParsed] = useState(null); // {parsed_ruleset, intents, notes}
  const [parsing, setParsing] = useState(false);
  const [applying, setApplying] = useState(false);
  // Diff highlight (set after a successful apply)
  const [highlight, setHighlight] = useState({ added: [], removed: [] });

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const r = await K.api.getRules();
      setRules(r.rules || []);
      setVersion(r.version);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!ready) return;
    loadRules();
  }, [loadRules, ready, acting]);

  const onParse = async () => {
    if (!draft.trim()) return;
    setParsing(true);
    setError(null);
    try {
      const r = await K.api.parseNL(draft);
      setParsed(r);
    } catch (err) {
      setError(err.message);
    } finally {
      setParsing(false);
    }
  };

  const onApply = async () => {
    if (!parsed?.parsed_ruleset) return;
    setApplying(true);
    setError(null);
    const oldRules = rules;
    try {
      // Merge parsed rules into the current ruleset (additive). Demoer can
      // remove rules later via a future "drop rule" affordance.
      const newRules = [...oldRules, ...(parsed.parsed_ruleset.rules || [])];
      const result = await K.api.applyRules({ version: 0, rules: newRules }, draft);
      const diff = diffRulesets(oldRules, newRules);
      setHighlight(diff);
      setDraft("");
      setParsed(null);
      await loadRules();
      onApplied?.(result.version);
      // Fade highlight after 4s
      setTimeout(() => setHighlight({ added: [], removed: [] }), 4000);
    } catch (err) {
      setError(err.message);
    } finally {
      setApplying(false);
    }
  };

  // Group active rules by type
  const groups = rules.reduce((acc, r) => {
    (acc[r.type] = acc[r.type] || []).push(r);
    return acc;
  }, {});

  const addedKeys = new Set(highlight.added.map(ruleKey));

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
        policy editor · v{version ?? "—"} · {rules.length} rules
      </header>

      {/* NL chat input */}
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--line)" }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder='Plain English — e.g. "Hide deal_size from customer service" or "On Customer.deal_size conflicts, written outweighs verbal"'
          rows={3}
          style={{
            width: "100%",
            background: "var(--bg)",
            color: "var(--ink-1)",
            border: "1px solid var(--line)",
            padding: 8,
            fontFamily: "inherit",
            fontSize: 12,
            resize: "vertical",
            marginBottom: 6,
          }}
        />
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={onParse}
            disabled={parsing || !draft.trim()}
            className="ghost-btn"
          >
            {parsing ? "parsing…" : "parse"}
          </button>
          <button
            onClick={onApply}
            disabled={applying || !parsed?.parsed_ruleset?.rules?.length}
            className="ghost-btn"
            title="POST /rules/apply (admin-elevated)"
          >
            {applying ? "applying…" : "apply"}
            <span style={{ marginLeft: 6, color: "var(--accent, #4ade80)" }}>↑admin</span>
          </button>
          {error && (
            <span style={{ color: "#ef4444", fontSize: 10, fontFamily: "var(--mono)" }}>
              {error}
            </span>
          )}
        </div>
        {parsed && (
          <div style={{ marginTop: 8, fontSize: 11 }}>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink-3)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 4,
              }}
            >
              parsed · {parsed.parsed_ruleset?.rules?.length || 0} new rules ·{" "}
              {parsed.intents?.length || 0} intents
            </div>
            {(parsed.parsed_ruleset?.rules || []).map((r, i) => (
              <div
                key={i}
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                  color: "var(--accent, #4ade80)",
                  padding: "2px 0",
                }}
              >
                + {describeRule(r)}
              </div>
            ))}
            {parsed.notes && (
              <div style={{ fontSize: 10, color: "var(--ink-3)", padding: "2px 0" }}>
                note: {parsed.notes}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Active ruleset (right pane structured view) */}
      <div style={{ overflowY: "auto", flex: 1, padding: "10px 14px" }}>
        {loading && <p style={{ color: "var(--ink-3)" }}>loading…</p>}
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
              {items.map((r, i) => {
                const wasAdded = addedKeys.has(ruleKey(r));
                return (
                  <div
                    key={i}
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 11,
                      padding: "3px 4px",
                      color: wasAdded ? "var(--accent, #4ade80)" : "var(--ink-2)",
                      background: wasAdded ? "rgba(74,222,128,0.1)" : "transparent",
                      borderLeft: wasAdded
                        ? "2px solid var(--accent, #4ade80)"
                        : "2px solid transparent",
                      transition: "background 1s ease, color 1s ease",
                    }}
                  >
                    {wasAdded ? "+ " : "  "}
                    {describeRule(r)}
                  </div>
                );
              })}
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

  // `refresh` increments to force re-fetches after writes (ingest, delete, apply).
  const [refresh, setRefresh] = useState(0);
  const bumpRefresh = useCallback(() => setRefresh((n) => n + 1), []);

  // Empty-tenant gate: when both /documents and /schema are empty, render the
  // seed overlay instead of the populated layout. Cleared on first non-empty
  // refresh (post-seed). PR 10-4 deletion: this replaces the data.js fallback.
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState(null);
  const [schemaCount, setSchemaCount] = useState(null); // null = unknown
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const types = await K.api.listSchema();
        if (!cancelled) setSchemaCount(types.length);
      } catch {
        if (!cancelled) setSchemaCount(0);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, refresh]);
  // Note: Note auto-seeds on first /schema call (so 1 schema isn't really
  // "empty"). Empty = no documents AND <=1 schema (the auto-seeded Note).
  const isEmpty = !docsLoading && documents.length === 0 && (schemaCount ?? 0) <= 1;

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
  }, [acting, ready, refresh]);

  if (ready && isEmpty) {
    return (
      <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
        <K.AgentSwitcher />
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: 40,
            textAlign: "center",
            background: "var(--bg)",
          }}
        >
          <h2
            style={{
              fontFamily: "var(--mono)",
              fontSize: 14,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: "var(--ink-2)",
              marginBottom: 14,
            }}
          >
            empty tenant
          </h2>
          <p style={{ color: "var(--ink-2)", maxWidth: 480, marginBottom: 24, fontSize: 14 }}>
            No schemas registered, no documents ingested. Click below to seed the demo
            world: 4 entity types (<code>Customer</code>, <code>Person</code>, <code>Deal</code>,
            <code>AuditLog</code>), 29 ACL rules, and 8 markdown documents from the synthetic
            corpus.
          </p>
          <button
            onClick={onSeed}
            disabled={seeding}
            className="ghost-btn"
            style={{ fontSize: 12, padding: "8px 18px" }}
          >
            {seeding ? "seeding (this calls the LLM, ~30s)…" : "seed demo data"}
            <span style={{ marginLeft: 8, color: "var(--accent, #4ade80)" }}>↑admin</span>
          </button>
          {seedError && (
            <div
              style={{
                marginTop: 14,
                color: "#ef4444",
                fontFamily: "var(--mono)",
                fontSize: 11,
              }}
            >
              {seedError}
            </div>
          )}
          <p
            style={{
              marginTop: 28,
              color: "var(--ink-3)",
              fontFamily: "var(--mono)",
              fontSize: 10,
            }}
          >
            equivalent to <code>task reset-and-seed</code>; safe to re-run.
          </p>
        </div>
      </div>
    );
  }

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
            onChange={bumpRefresh}
          />
        </div>
        <div style={{ overflow: "hidden" }}>
          <EntitiesPanel
            acting={acting}
            ready={ready}
            refresh={refresh}
            onPickEntity={(t, k) => setDrawer({ entity_type: t, entity_key: k })}
          />
        </div>
        <div style={{ borderLeft: "1px solid var(--line)", overflow: "hidden" }}>
          <PolicyEditor acting={acting} ready={ready} onApplied={bumpRefresh} />
        </div>
      </div>
      <EntityDrawer
        entity_type={drawer?.entity_type}
        entity_key={drawer?.entity_key}
        acting={acting}
        onClose={() => setDrawer(null)}
      />
      <K.EscalationToast />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
