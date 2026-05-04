/* global React, ReactDOM, K */
// App shell — post PR 33/34, the live demo is overlay-driven:
//
//   Topbar:    brand · KENTRO · rule-version chip · conflict-policy chip
//   Main grid: ┌────────────────────────────────────────────────────────┐
//              │  Reasoning graph (full canvas)                         │
//              └────────────────────────────────────────────────────────┘
//   Right slot (mutex):
//     • SourceOverlay  — raw doc content, opened by clicking a doc node
//     • EntityOverlay  — Global + per-agent cards, opened by clicking an
//                        entity node; carries an [ACL] chip in its header
//   Left-of-overlay slot (mutex):
//     • LineageDrawer  — per-field SOURCES → RESOLVER → RESULT pipeline,
//                        opened by clicking a field row in EntityOverlay
//     • PolicyOverlay  — type-scoped Access matrix + Rules + NL editor,
//                        opened by clicking the [ACL] chip
//
// AgentPanels (Sales / CS) and the standalone PolicyEditor / AccessMatrix
// panels were retired in PR 34: their data is now reachable via the
// EntityOverlay → drawer / policy flow, and removing them gives the
// reasoning graph the full canvas.

const { useState, useEffect, useCallback } = React;

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

  useEffect(() => {
    if (tenantId) return;
    const list = K.api.getAgentList();
    if (list.length > 0) {
      K.api.bootstrap().then((r) => {
        if (r.ok) setTenantId(r.payload.tenant_id);
      });
    }
  }, [tenantId]);

  return { ready, bootError, tenantId };
}

function App() {
  const { ready, bootError } = useBootstrap();
  // Right-edge overlay slot (mutex): SourceOverlay (raw doc) and
  // EntityOverlay (Global + per-agent cards). Opening one closes the other.
  const [sourceDocId, setSourceDocId] = useState(null);
  const [entityPayload, setEntityPayload] = useState(null);
  // Left-of-overlay drawer slot (mutex): LineageDrawer (per-field flow) and
  // PolicyOverlay (type-scoped rules + matrix + NL editor). Both are 520px
  // wide and shift left of EntityOverlay when it's open.
  const [drawerPayload, setDrawerPayload] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [policyEntityType, setPolicyEntityType] = useState(null);
  // Third stacked slot, sitting one drawer deeper than LineageDrawer (at
  // right: 960px). Opens when the user clicks the RESOLVER chip in the
  // lineage flow; the lineage drawer stays visible behind it so the
  // candidate flow remains the editing context.
  const [resolverTarget, setResolverTarget] = useState(null);
  const [lineageRefreshKey, setLineageRefreshKey] = useState(0);
  const openSourceDoc = useCallback((id) => {
    setEntityPayload(null);
    setSourceDocId(id);
  }, []);
  const openEntity = useCallback((p) => {
    setSourceDocId(null);
    setEntityPayload(p);
  }, []);
  const openLineage = useCallback((payload) => {
    setPolicyEntityType(null);
    setResolverTarget(null);
    setDrawerPayload(payload);
    setDrawerOpen(true);
  }, []);
  const openPolicy = useCallback((entityType) => {
    setDrawerOpen(false);
    setResolverTarget(null);
    setPolicyEntityType(entityType);
  }, []);
  const openResolver = useCallback((target) => {
    setResolverTarget(target);
  }, []);
  const closeResolver = useCallback(() => setResolverTarget(null), []);
  // Closing the lineage drawer also dismisses the (deeper) resolver drawer —
  // it's a child of the lineage flow, so leaving it orphaned when its parent
  // closes is confusing. Covers ESC button click, backdrop click, etc.
  // (The ESC keypath is also handled inside LineageDrawer to ensure the
  // resolver drawer closes FIRST when both are open.)
  const closeLineage = useCallback(() => {
    setDrawerOpen(false);
    setResolverTarget(null);
  }, []);

  const [refresh, setRefresh] = useState(0);
  const bumpRefresh = useCallback(() => setRefresh((n) => n + 1), []);
  const [documents, setDocuments] = useState([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [ruleVersion, setRuleVersion] = useState(0);
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState(null);
  const [pendingDoc, setPendingDoc] = useState(false);
  const [schemaTypes, setSchemaTypes] = useState([]);

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
        if (!cancelled) setRuleVersion(r.version || 0);
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
      </div>

      <div className="main main-graph-only">
        <K.WorkPanel
          documents={documents}
          onIngestEmail={onIngestEmail}
          pendingDoc={pendingDoc}
          refresh={refresh}
          highlightField={drawerPayload}
          onOpenDoc={openSourceDoc}
          onOpenEntity={openEntity}
        />
      </div>

      <K.LineageDrawer
        open={drawerOpen}
        payload={drawerPayload}
        onClose={closeLineage}
        documents={documents}
        onEditResolver={openResolver}
        refreshKey={lineageRefreshKey}
      />
      <K.ResolverDrawer
        open={!!resolverTarget}
        target={resolverTarget}
        onClose={closeResolver}
        onApplied={() => {
          closeResolver();
          setLineageRefreshKey((k) => k + 1);
          bumpRefresh();
        }}
      />
      <K.PolicyOverlay
        open={!!policyEntityType}
        entityType={policyEntityType}
        onClose={() => setPolicyEntityType(null)}
        refresh={refresh}
        onApplied={bumpRefresh}
      />
      {/* Always-reserved right rail — exactly one of (SourceOverlay,
       *  EntityOverlay, EmptyRightRail) renders at a time so switching
       *  selections updates content in place rather than animating
       *  close + reopen. */}
      {sourceDocId ? (
        <K.SourceOverlay
          open
          documentId={sourceDocId}
          onClose={() => setSourceDocId(null)}
        />
      ) : entityPayload ? (
        <K.EntityOverlay
          open
          payload={entityPayload}
          onClose={() => setEntityPayload(null)}
          refresh={refresh}
          onFieldClick={openLineage}
          onOpenPolicy={openPolicy}
        />
      ) : (
        <K.EmptyRightRail />
      )}
      <K.EscalationToast />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
