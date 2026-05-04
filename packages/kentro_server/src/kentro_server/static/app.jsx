/* global React, ReactDOM, K */
// Live view rendered entirely from kentro-server data, styled with the design
// tokens shipped in styles.css (the prototype's `oklch(...)` palette + Inter +
// JetBrains Mono + soft glassmorphism). Every layout primitive uses class-based
// styling — inline styles only where dynamic values matter.
//
// Layout:
//   ┌──────────────────── header (AgentSwitcher) ────────────────────┐
//   │ left col            │  center col              │ right col      │
//   │ Documents           │  Entities (per type)     │ Policy editor  │
//   │ + ingest form       │  filtered by acting ACL  │ NL chat + view │
//   │ + ↑admin delete     │                          │ + apply ↑admin │
//   └────────────────────────────────────────────────────────────────┘
//
// Empty tenant: when /documents and /schema both return empty, the App renders
// a "seed demo data" overlay that POSTs /demo/seed (admin + opt-in gated).

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
  // Mirror render_rule(...) on the server — single line per Rule.
  const tag = (label) => `[${label}]`;
  const a = r.agent_id || "*";
  const t = r.entity_type;
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

function ruleKey(r) {
  return JSON.stringify({
    type: r.type,
    agent_id: r.agent_id ?? null,
    entity_type: r.entity_type ?? null,
    field_name: r.field_name ?? null,
    entity_key: r.entity_key ?? null,
    allowed: r.allowed ?? null,
    requires_approval: r.requires_approval ?? null,
    resolver: r.resolver ?? null,
  });
}

function diffRulesets(oldRules, newRules) {
  const oldKeys = new Map(oldRules.map((r) => [ruleKey(r), r]));
  const newKeys = new Map(newRules.map((r) => [ruleKey(r), r]));
  const added = [];
  for (const [k, r] of newKeys) if (!oldKeys.has(k)) added.push(r);
  return { added };
}

// ── components ──────────────────────────────────────────────────────────────

function AdminBadge() {
  return <span className="admin-badge">↑admin</span>;
}

function StatusPill({ status }) {
  const cls = `field-status status-${status}`;
  return <span className={cls}>{status}</span>;
}

function PanelHead({ title, sub, right }) {
  return (
    <header className="panel-head">
      <span className="panel-title">{title}</span>
      {sub && <span className="panel-sub">{sub}</span>}
      {right && <span style={{ marginLeft: "auto" }}>{right}</span>}
    </header>
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
    <div className="ingest-form">
      <div className="row">
        <input
          type="text"
          placeholder="label (e.g. acme-call.md)"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <select value={sourceClass} onChange={(e) => setSourceClass(e.target.value)}>
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
      />
      {error && (
        <div style={{ color: "var(--bad)", fontSize: 10, fontFamily: "var(--mono)" }}>
          {error}
        </div>
      )}
      <div className="row">
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
  const [deleting, setDeleting] = useState(null);

  const remove = async (e, doc) => {
    e.stopPropagation();
    if (
      !window.confirm(
        `Delete "${doc.label || doc.id}"?\n\nThis cascades through writes and reopens any conflicts the doc was part of.`
      )
    )
      return;
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
    <section className="panel">
      <PanelHead title="documents" sub={`${documents.length} sources`} />
      <IngestForm onIngested={onChange} />
      <div className="panel-body" style={{ padding: 0 }}>
        {loading && <p style={{ padding: 14, color: "var(--ink-3)" }}>loading…</p>}
        {error && (
          <p style={{ padding: 14, color: "var(--bad)", fontFamily: "var(--mono)", fontSize: 11 }}>
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
            className={`doc-row${activeDocId === d.id ? " active" : ""}`}
          >
            <div className="doc-title">
              <span style={{ flex: 1 }}>{d.label || d.id}</span>
              <button
                onClick={(e) => remove(e, d)}
                title="DELETE /documents/{id} — admin-elevated"
                disabled={deleting === d.id}
                className="ghost-btn"
                style={{ fontSize: 9, padding: "2px 6px" }}
              >
                {deleting === d.id ? "…" : "× del"}
                <AdminBadge />
              </button>
            </div>
            <div className="doc-meta">
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
  const [byType, setByType] = useState({});
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
    <section className="panel">
      <PanelHead
        title="entities"
        sub={`viewing as ${acting}`}
        right={
          <button onClick={reload} className="ghost-btn" title="re-fetch /entities/{type}">
            refresh
          </button>
        }
      />
      <div className="panel-body" style={{ padding: "10px 0" }}>
        {loading && <p style={{ padding: 14, color: "var(--ink-3)" }}>loading…</p>}
        {error && (
          <p style={{ padding: 14, color: "var(--bad)", fontFamily: "var(--mono)", fontSize: 11 }}>
            {error}
          </p>
        )}
        {!loading &&
          ENTITY_TYPES.map((t) => {
            const rows = byType[t] || [];
            return (
              <div key={t} className="entities-section">
                <div className="label">
                  {t} · {rows.length} {rows.length === 1 ? "entity" : "entities"}
                  {rows.length === 0 && <span className="none">(none visible to {acting})</span>}
                </div>
                {rows.map((row) => (
                  <button
                    key={`${t}/${row.key}`}
                    onClick={() => onPickEntity(t, row.key)}
                    className="entity-row"
                  >
                    <span className="entity-key">{row.key}</span>
                    <span className="entity-fields">
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

function PolicyEditor({ acting, ready, onApplied }) {
  const [rules, setRules] = useState([]);
  const [version, setVersion] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState("");
  const [parsed, setParsed] = useState(null);
  const [parsing, setParsing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [highlight, setHighlight] = useState({ added: [] });

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
      const newRules = [...oldRules, ...(parsed.parsed_ruleset.rules || [])];
      const result = await K.api.applyRules({ version: 0, rules: newRules }, draft);
      const diff = diffRulesets(oldRules, newRules);
      setHighlight(diff);
      setDraft("");
      setParsed(null);
      await loadRules();
      onApplied?.(result.version);
      setTimeout(() => setHighlight({ added: [] }), 4000);
    } catch (err) {
      setError(err.message);
    } finally {
      setApplying(false);
    }
  };

  const groups = rules.reduce((acc, r) => {
    (acc[r.type] = acc[r.type] || []).push(r);
    return acc;
  }, {});
  const addedKeys = new Set(highlight.added.map(ruleKey));

  return (
    <section className="panel policy-editor">
      <PanelHead title="policy editor" sub={`v${version ?? "—"} · ${rules.length} rules`} />

      {/* NL chat input */}
      <div className="editor-input">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder='Plain English — e.g. "Hide deal_size from customer service"'
          rows={3}
        />
        <div className="editor-actions">
          <button onClick={onParse} disabled={parsing || !draft.trim()} className="ghost-btn">
            {parsing ? "parsing…" : "parse"}
          </button>
          <button
            onClick={onApply}
            disabled={applying || !parsed?.parsed_ruleset?.rules?.length}
            className="ghost-btn"
            title="POST /rules/apply (admin-elevated)"
          >
            {applying ? "applying…" : "apply"}
            <AdminBadge />
          </button>
          {error && (
            <span style={{ color: "var(--bad)", fontSize: 10, fontFamily: "var(--mono)" }}>
              {error}
            </span>
          )}
        </div>
        {parsed && (
          <div className="parsed-preview">
            <div className="label">
              parsed · {parsed.parsed_ruleset?.rules?.length || 0} new rules ·{" "}
              {parsed.intents?.length || 0} intents
            </div>
            {(parsed.parsed_ruleset?.rules || []).map((r, i) => (
              <div key={i} className="parsed-rule">
                + {describeRule(r)}
              </div>
            ))}
            {parsed.notes && <div className="parsed-note">note: {parsed.notes}</div>}
          </div>
        )}
      </div>

      {/* Active ruleset (structured view) */}
      <div className="ruleset-view">
        {loading && <p style={{ color: "var(--ink-3)" }}>loading…</p>}
        {!loading &&
          Object.entries(groups).map(([type, items]) => (
            <div key={type} className="rule-group">
              <div className="group-label">
                {type} · {items.length}
              </div>
              {items.map((r, i) => {
                const wasAdded = addedKeys.has(ruleKey(r));
                return (
                  <div key={i} className={`rule-line${wasAdded ? " added" : ""}`}>
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
  }, [entity_type, entity_key, acting]);

  if (!entity_type) return null;
  return (
    <div className="entity-drawer">
      <header className="drawer-head">
        <span className="type-tag">{entity_type}</span>
        <span className="key">{entity_key}</span>
        <button onClick={onClose} className="ghost-btn" style={{ marginLeft: "auto" }}>
          close
        </button>
      </header>
      <div className="drawer-body">
        {loading && <p style={{ color: "var(--ink-3)" }}>loading…</p>}
        {error && (
          <p style={{ color: "var(--bad)", fontFamily: "var(--mono)", fontSize: 11 }}>{error}</p>
        )}
        {!loading &&
          record &&
          Object.entries(record.fields || {}).map(([fname, fval]) => (
            <div key={fname} className="field-card">
              <div className="field-head">
                <span className="field-card-name">{fname}</span>
                <StatusPill status={fval.status} />
              </div>
              <div className="field-card-value">
                {fval.status === "known" && <span>{JSON.stringify(fval.value)}</span>}
                {fval.status === "unknown" && (
                  <span style={{ color: "var(--ink-3)" }}>(no writes yet)</span>
                )}
                {fval.status === "hidden" && (
                  <span style={{ color: "var(--hidden)", fontStyle: "italic" }}>
                    {fval.reason}
                  </span>
                )}
                {fval.status === "unresolved" && (
                  <span>
                    UNRESOLVED — {(fval.candidates || []).length} candidates · {fval.reason}
                  </span>
                )}
              </div>
              {fval.lineage && fval.lineage.length > 0 && (
                <div className="lineage">
                  <div className="lineage-label">lineage</div>
                  {fval.lineage.map((l, i) => (
                    <div key={i} className="lineage-row">
                      ← {l.written_by_agent_id} ·{" "}
                      {l.source_document_label || l.source_document_id || "—"} · v
                      {l.rule_version_at_write}
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
  const [ready, setReady] = useState(K.api.getAgentList().length > 0);
  const [acting, setActing] = useState(K.api.getActingAs());
  const [documents, setDocuments] = useState([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [docsError, setDocsError] = useState(null);
  const [activeDocId, setActiveDocId] = useState(null);
  const [drawer, setDrawer] = useState(null);

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

  const [refresh, setRefresh] = useState(0);
  const bumpRefresh = useCallback(() => setRefresh((n) => n + 1), []);

  // Empty-tenant detection (PR 10-4)
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState(null);
  const [schemaCount, setSchemaCount] = useState(null);
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

  // Documents fetch
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
      <div className="live-shell">
        <K.AgentSwitcher />
        <div className="seed-overlay">
          <h2>empty tenant</h2>
          <p className="seed-blurb">
            No schemas registered, no documents ingested. Click below to seed the demo
            world: 4 entity types (<code>Customer</code>, <code>Person</code>,{" "}
            <code>Deal</code>, <code>AuditLog</code>), 29 ACL rules, and 8 markdown
            documents from the synthetic corpus.
          </p>
          <button onClick={onSeed} disabled={seeding} className="seed-btn">
            {seeding ? "seeding (LLM, ~30s)…" : "seed demo data"}
            <AdminBadge />
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
    <div className="live-shell">
      <K.AgentSwitcher />
      <div className="live-main">
        <DocumentsPanel
          documents={documents}
          loading={docsLoading}
          error={docsError}
          onPick={(d) => setActiveDocId(d.id)}
          activeDocId={activeDocId}
          onChange={bumpRefresh}
        />
        <EntitiesPanel
          acting={acting}
          ready={ready}
          refresh={refresh}
          onPickEntity={(t, k) => setDrawer({ entity_type: t, entity_key: k })}
        />
        <PolicyEditor acting={acting} ready={ready} onApplied={bumpRefresh} />
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
