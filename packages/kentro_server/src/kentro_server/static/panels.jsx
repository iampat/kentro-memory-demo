/* global React, K */
// PR 14 — bottom row of the prototype layout: Extraction (live doc list +
// per-doc step trace), Graph (SVG bipartite doc↔entity), Lineage drawer
// (slide-over per-field).
//
// Server endpoints consumed:
//   GET /documents                          → doc list (left of bottom row)
//   GET /documents/{id}/extraction-steps   → per-doc step log
//   GET /viz/graph                          → nodes + edges for SVG
//   GET /entities/{type}/{key}             → already used by AgentPanel,
//                                             reused by LineageDrawer to read
//                                             the field's per-source lineage.

const { useEffect, useState, useRef, useCallback } = React;

// ── Extraction Panel ────────────────────────────────────────────────────────
window.K.ExtractionPanel = function ExtractionPanel({
  documents,
  activeDocId,
  onPickDoc,
  onIngestEmail,
  pendingDoc,
}) {
  const [steps, setSteps] = useState([]);
  const [stepsLoading, setStepsLoading] = useState(false);
  const streamRef = useRef(null);

  useEffect(() => {
    if (!activeDocId) {
      setSteps([]);
      return;
    }
    let cancelled = false;
    setStepsLoading(true);
    K.api
      .listExtractionSteps(activeDocId)
      .then((s) => {
        if (!cancelled) setSteps(s || []);
      })
      .catch(() => {
        if (!cancelled) setSteps([]);
      })
      .finally(() => {
        if (!cancelled) setStepsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeDocId]);

  useEffect(() => {
    if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight;
  }, [steps]);

  const activeDoc = documents.find((d) => d.id === activeDocId);
  const emailLabel = "email_jane_2026-04-17.md";
  const hasEmail = documents.some((d) => d.label === emailLabel);

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Ingestion pipeline</span>
        <span className="panel-sub">Events become memory</span>
        <span className="spacer" />
        <span className="panel-sub">
          {documents.length} {documents.length === 1 ? "event" : "events"}
        </span>
      </div>
      <div className="panel-body">
        <div className="add-doc">
          <button onClick={onIngestEmail} disabled={pendingDoc || hasEmail}>
            {pendingDoc
              ? "ingesting…"
              : hasEmail
                ? "✓ Jane Doe email already ingested"
                : "+ drop ✉️ email from Jane Doe"}
          </button>
        </div>
        <div className="doc-list">
          {documents.length === 0 && (
            <div style={{ padding: 12, color: "var(--ink-3)", fontSize: 11 }}>
              No documents in this tenant yet.
            </div>
          )}
          {documents.map((d) => {
            const icon = d.source_class === "email" ? "✉️" : d.source_class === "verbal" ? "📞" : "📄";
            const ts = (d.created_at || "").split("T");
            return (
              <div
                key={d.id}
                className={K.cls("doc-item", activeDocId === d.id && "active")}
                onClick={() => onPickDoc(d.id)}
              >
                <span className="doc-icon">{icon}</span>
                <span style={{ flex: 1 }}>
                  <div className="doc-name">
                    {d.source_class || "doc"} · {ts[0] || "—"}
                  </div>
                  <div className="doc-meta">{d.label || d.id.slice(0, 8)}</div>
                </span>
                <span className="doc-meta">{d.field_write_count} writes</span>
              </div>
            );
          })}
        </div>
        <div className="extraction-stream" ref={streamRef}>
          {!activeDocId && (
            <div style={{ color: "var(--ink-3)", fontSize: 11 }}>
              Pick a document above to inspect its extraction trace.
            </div>
          )}
          {activeDocId && stepsLoading && (
            <div style={{ color: "var(--ink-3)", fontSize: 11 }}>loading…</div>
          )}
          {activeDocId && !stepsLoading && steps.length === 0 && (
            <div style={{ color: "var(--ink-3)", fontSize: 11 }}>
              No extraction steps recorded for this document.
            </div>
          )}
          {activeDocId && !stepsLoading && steps.length > 0 && (
            <>
              <div className="ext-step">
                <span className="ts">+000ms</span>
                <span className="msg">
                  read <span className="val">{activeDoc?.label || activeDocId.slice(0, 8)}</span>
                </span>
              </div>
              {steps.map((s, i) => (
                <div key={s.id} className="ext-step">
                  <span className="ts">+{(80 + i * 80).toString().padStart(3, "0")}ms</span>
                  <span className="msg">
                    extracted <span className="ent">{s.produced_writes}</span>{" "}
                    <span className="field">facts</span> via{" "}
                    <span className="val">{s.model}</span>{" "}
                    <span style={{ color: "var(--ink-3)" }}>({s.latency_ms}ms)</span>
                  </span>
                </div>
              ))}
              <div className="ext-step">
                <span className="ts">+{(80 + steps.length * 80).toString().padStart(3, "0")}ms</span>
                <span className="msg">
                  saved facts with lineage back to the source
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

// ── Graph Panel ──────────────────────────────────────────────────────────────
// Bipartite layout: documents on the left, entities on the right.
window.K.GraphPanel = function GraphPanel({ refresh, highlightField }) {
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    K.api
      .getViewGraph()
      .then((g) => {
        if (!cancelled) setGraph(g || { nodes: [], edges: [] });
      })
      .catch(() => {
        if (!cancelled) setGraph({ nodes: [], edges: [] });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const W = 700;
  const H = 360;
  const docX = 70;
  const entX = 600;

  const docs = graph.nodes.filter((n) => n.kind === "document");
  const ents = graph.nodes.filter((n) => n.kind === "entity");

  const docPos = {};
  docs.forEach((d, i) => {
    docPos[d.id] = {
      x: docX,
      y: 50 + i * ((H - 100) / Math.max(1, docs.length - 1 || 1)),
    };
  });
  const entPos = {};
  ents.forEach((e, i) => {
    entPos[e.id] = {
      x: entX,
      y: 50 + i * ((H - 100) / Math.max(1, ents.length - 1 || 1)),
    };
  });

  const edgePath = (from, to) => {
    const fx = from.x + 50;
    const fy = from.y;
    const tx = to.x - 60;
    const ty = to.y;
    const cx = (fx + tx) / 2;
    return `M ${fx} ${fy} C ${cx} ${fy}, ${cx} ${ty}, ${tx} ${ty}`;
  };

  const isEdgeHighlighted = (e) => {
    if (!highlightField) return false;
    const targetId = `ent:${highlightField.entity_type}:${highlightField.entity_key}`;
    return e.target === targetId && e.field_name === highlightField.field_name;
  };

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Reasoning graph</span>
        <span className="panel-sub">all memory</span>
        <span className="spacer" />
        <span className="panel-sub">
          {docs.length} sources · {ents.length} entities · {graph.edges.length} edges
        </span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        <div className="graph-wrap">
          {loading && (
            <div style={{ padding: 14, color: "var(--ink-3)", fontSize: 11 }}>loading graph…</div>
          )}
          {!loading && (docs.length === 0 || ents.length === 0) && (
            <div style={{ padding: 14, color: "var(--ink-3)", fontSize: 11 }}>
              No documents or entities yet.
            </div>
          )}
          {!loading && docs.length > 0 && ents.length > 0 && (
            <svg
              className="graph-svg"
              viewBox={`0 0 ${W} ${H}`}
              preserveAspectRatio="xMidYMid meet"
            >
              <defs>
                <linearGradient id="entGrad" x1="0" x2="1">
                  <stop offset="0%" stopColor="oklch(0.55 0.18 255)" />
                  <stop offset="100%" stopColor="oklch(0.6 0.20 285)" />
                </linearGradient>
                <linearGradient id="docGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="oklch(1 0 0)" />
                  <stop offset="100%" stopColor="oklch(0.96 0.01 260)" />
                </linearGradient>
                <filter id="softShadow" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur in="SourceAlpha" stdDeviation="2" />
                  <feOffset dy="2" />
                  <feComponentTransfer>
                    <feFuncA type="linear" slope="0.15" />
                  </feComponentTransfer>
                  <feMerge>
                    <feMergeNode />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>
              <text
                x={docX}
                y={18}
                textAnchor="middle"
                fontFamily="Inter, sans-serif"
                fontWeight="700"
                fontSize="9"
                fill="var(--ink-3)"
                letterSpacing="1.5"
              >
                SOURCES
              </text>
              <text
                x={entX}
                y={18}
                textAnchor="middle"
                fontFamily="Inter, sans-serif"
                fontWeight="700"
                fontSize="9"
                fill="var(--ink-3)"
                letterSpacing="1.5"
              >
                ENTITIES
              </text>
              {graph.edges.map((e, i) => {
                const from = docPos[e.source];
                const to = entPos[e.target];
                if (!from || !to) return null;
                const hl = isEdgeHighlighted(e);
                return (
                  <g key={i}>
                    <path
                      d={edgePath(from, to)}
                      stroke={hl ? "oklch(0.68 0.18 55)" : "oklch(0.78 0.02 260)"}
                      strokeWidth={hl ? 2 : 1.2}
                      fill="none"
                    />
                    <circle r={hl ? 3 : 2} className="flow-particle">
                      <animateMotion
                        dur={`${2 + (i % 3) * 0.5}s`}
                        repeatCount="indefinite"
                        begin={`${(i % 5) * 0.3}s`}
                        path={edgePath(from, to)}
                      />
                      <animate
                        attributeName="opacity"
                        values="0;1;1;0"
                        keyTimes="0;0.1;0.9;1"
                        dur={`${2 + (i % 3) * 0.5}s`}
                        repeatCount="indefinite"
                        begin={`${(i % 5) * 0.3}s`}
                      />
                    </circle>
                  </g>
                );
              })}
              {docs.map((d) => {
                const p = docPos[d.id];
                return (
                  <g
                    key={d.id}
                    transform={`translate(${p.x - 50}, ${p.y - 16})`}
                    filter="url(#softShadow)"
                  >
                    <rect width="100" height="32" rx="6" fill="url(#docGrad)" stroke="var(--line)" />
                    <text
                      x="10"
                      y="14"
                      fontFamily="Inter, sans-serif"
                      fontWeight="600"
                      fontSize="10"
                      fill="var(--ink)"
                    >
                      {d.sub || "doc"}
                    </text>
                    <text
                      x="10"
                      y="25"
                      fontFamily="JetBrains Mono, monospace"
                      fontSize="8"
                      fill="var(--ink-3)"
                    >
                      {(d.label || "").slice(0, 18)}
                    </text>
                  </g>
                );
              })}
              {ents.map((e) => {
                const p = entPos[e.id];
                const hl =
                  highlightField &&
                  e.id === `ent:${highlightField.entity_type}:${highlightField.entity_key}`;
                return (
                  <g
                    key={e.id}
                    transform={`translate(${p.x - 60}, ${p.y - 16})`}
                    className={K.cls("node-entity", hl && "node-highlight")}
                  >
                    <rect
                      width="120"
                      height="32"
                      rx="6"
                      fill="url(#entGrad)"
                      stroke="oklch(0.5 0.2 255)"
                    />
                    <text
                      x="12"
                      y="14"
                      fontFamily="Inter, sans-serif"
                      fontWeight="700"
                      fontSize="10"
                      fill="white"
                    >
                      {e.sub}
                    </text>
                    <text
                      x="12"
                      y="25"
                      fontFamily="JetBrains Mono, monospace"
                      fontSize="9"
                      fill="oklch(1 0 0 / 0.85)"
                    >
                      .{e.label}
                    </text>
                  </g>
                );
              })}
            </svg>
          )}
          <div className="graph-legend">
            <div className="row">
              <span
                className="swatch"
                style={{
                  background: "white",
                  border: "1px solid var(--line)",
                  borderRadius: 3,
                }}
              ></span>{" "}
              source doc
            </div>
            <div className="row">
              <span
                className="swatch"
                style={{
                  background: "linear-gradient(135deg, var(--accent), var(--pop))",
                  borderRadius: 3,
                }}
              ></span>{" "}
              entity
            </div>
            <div className="row" style={{ marginTop: 4, paddingTop: 4, borderTop: "1px dashed var(--line)" }}>
              <span
                className="swatch"
                style={{
                  background: "var(--accent)",
                  borderRadius: "50%",
                  width: 6,
                  height: 6,
                  boxShadow: "0 0 6px var(--accent-glow)",
                }}
              ></span>{" "}
              data flow
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ── Access Matrix Panel ─────────────────────────────────────────────────────
// Reads `GET /viz/access-matrix?entity_type=Customer` and renders the
// agents × fields permission grid.
window.K.AccessMatrixPanel = function AccessMatrixPanel({ entityType, refresh, changedKeys }) {
  const [matrix, setMatrix] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    K.api
      .getViewAccessMatrix(entityType)
      .then((m) => {
        if (!cancelled) setMatrix(m);
      })
      .catch(() => {
        if (!cancelled) setMatrix(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [entityType, refresh]);

  const cellFor = (agent, field) => {
    if (!matrix) return null;
    return matrix.cells.find((c) => c.agent_id === agent && c.field_name === field);
  };

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Access matrix</span>
        <span className="panel-sub">{entityType} · per-agent permissions</span>
      </div>
      <div className="panel-body">
        {loading && <p style={{ color: "var(--ink-3)", fontSize: 11 }}>loading…</p>}
        {!loading && !matrix && (
          <p style={{ color: "var(--ink-3)", fontSize: 11 }}>
            No matrix data — register the {entityType} schema first.
          </p>
        )}
        {!loading && matrix && (
          <table className="matrix">
            <thead>
              <tr>
                <th></th>
                {matrix.fields.map((f) => (
                  <th key={f}>{f}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.agents.map((agent) => (
                <tr key={agent}>
                  <td className="row-head">{agent}</td>
                  {matrix.fields.map((f) => {
                    const c = cellFor(agent, f);
                    if (!c) return <td key={f} className="cell">—</td>;
                    if (!c.visible) {
                      return (
                        <td key={f} className="invisible">
                          invisible
                        </td>
                      );
                    }
                    const k = `${agent}:${f}`;
                    const isChanged = changedKeys?.includes(k);
                    return (
                      <td key={f} className={K.cls("cell", isChanged && "changed")}>
                        <div className="perm-line">
                          <span className="perm-tag r">R</span>
                          <span>{c.read ? "✓" : "—"}</span>
                        </div>
                        <div className="perm-line">
                          <span className="perm-tag w">W</span>
                          <span>{c.write ? "✓" : "—"}</span>
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

// ── Lineage Drawer ──────────────────────────────────────────────────────────
// Reads `GET /entities/{type}/{key}` and shows per-source lineage for the
// chosen field. Closes on overlay click or × button.
window.K.LineageDrawer = function LineageDrawer({ open, payload, onClose }) {
  const [record, setRecord] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !payload) {
      setRecord(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    // Read AS the agent that opened the drawer (so we get the same view they
    // saw on their panel).
    K.api
      .readEntityAs(payload.agent_id, payload.entity_type, payload.entity_key)
      .then((r) => {
        if (!cancelled) setRecord(r);
      })
      .catch(() => {
        if (!cancelled) setRecord(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, payload]);

  if (!payload) return null;
  const fname = payload.field_name;
  const fval = record?.fields?.[fname];
  const lineage = fval?.lineage || [];
  const candidates = fval?.candidates || [];

  return (
    <>
      <div className={K.cls("drawer-overlay", open && "open")} onClick={onClose} />
      <aside className={K.cls("drawer", open && "open")}>
        <div className="drawer-head">
          <span className="title">
            lineage · {payload.entity_type}.{payload.entity_key}.{fname}
          </span>
          <button onClick={onClose}>esc</button>
        </div>
        <div className="drawer-body">
          {loading && <p style={{ color: "var(--ink-3)" }}>loading…</p>}
          {!loading && fval && (
            <>
              <div className="lineage-section">
                <h4>Field</h4>
                <div className="kv-list">
                  <div className="kv">
                    <span className="k">entity</span>
                    <span className="v">
                      {payload.entity_type}.{payload.entity_key}
                    </span>
                  </div>
                  <div className="kv">
                    <span className="k">field</span>
                    <span className="v">{fname}</span>
                  </div>
                  <div className="kv">
                    <span className="k">status</span>
                    <span className="v">{fval.status}</span>
                  </div>
                  <div className="kv">
                    <span className="k">resolved value</span>
                    <span className="v">
                      {fval.status === "known"
                        ? JSON.stringify(fval.value)
                        : fval.status === "unknown"
                          ? "—"
                          : fval.status === "hidden"
                            ? "(hidden by ACL)"
                            : "(unresolved)"}
                    </span>
                  </div>
                  <div className="kv">
                    <span className="k">corroboration</span>
                    <span className="v">
                      {Math.max(lineage.length, candidates.length)} source(s)
                    </span>
                  </div>
                </div>
              </div>
              {fval.status === "unresolved" && candidates.length > 0 && (
                <div className="lineage-section">
                  <h4>Candidates</h4>
                  {candidates.map((c, i) => (
                    <div key={i} className="source-row">
                      <span className="source-icon">⇄</span>
                      <span className="source-meta">
                        <span className="name">candidate #{i + 1}</span>
                        <span className="ts">
                          {(c.lineage || [])
                            .map((l) => l.written_by_agent_id)
                            .join(", ") || "—"}
                        </span>
                      </span>
                      <span className="source-value">{JSON.stringify(c.value)}</span>
                    </div>
                  ))}
                </div>
              )}
              {lineage.length > 0 && (
                <div className="lineage-section">
                  <h4>Sources</h4>
                  {lineage.map((l, i) => (
                    <div key={i} className="source-row">
                      <span className="source-icon">📄</span>
                      <span className="source-meta">
                        <span className="name">
                          {l.source_document_id
                            ? `doc:${l.source_document_id.slice(0, 8)}`
                            : l.written_by_agent_id}
                        </span>
                        <span className="ts">
                          {l.written_by_agent_id} · v{l.rule_version} ·{" "}
                          {l.written_at?.split("T")[0] || "—"}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {fval.reason && (
                <div className="lineage-section">
                  <h4>Reason</h4>
                  <div className="resolution">{fval.reason}</div>
                </div>
              )}
            </>
          )}
          {!loading && !fval && (
            <p style={{ color: "var(--ink-3)" }}>
              No lineage available for this field (you may not have read access).
            </p>
          )}
        </div>
      </aside>
    </>
  );
};
