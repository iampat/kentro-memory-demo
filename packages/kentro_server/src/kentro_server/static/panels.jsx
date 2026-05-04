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
            const meta = K.docMeta(d);
            const ts = (d.created_at || "").split("T");
            return (
              <div
                key={d.id}
                className={K.cls("doc-item", activeDocId === d.id && "active")}
                onClick={() => onPickDoc(d.id)}
              >
                <span className="doc-icon">{meta.icon}</span>
                <span style={{ flex: 1 }}>
                  <div className="doc-name">
                    {meta.typeLabel} · {ts[0] || "—"}
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
// d3-sankey layout: source documents on the left, entity records on the right,
// ribbon thickness = number of field-writes between that (doc, entity) pair.
// Hover a node to dim everything that isn't connected to it (neighbor focus).
//
// Why sankey: the previous bipartite SVG rendered one path + one animated dot
// per field-write (~100 paths) and let the right column overflow. Sankey
// aggregates per-pair, so 100 raw edges collapse to ~30 visually distinct
// ribbons, and node y-positions are auto-computed to avoid overlap.
function renderSankey({ svg, width, height, graph, highlightField }) {
  const d3 = window.d3;
  const root = d3.select(svg);
  root.selectAll("*").remove();
  root.attr("viewBox", `0 0 ${width} ${height}`).attr("preserveAspectRatio", "xMidYMid meet");

  // Aggregate raw field-writes into one link per (source, target) with value =
  // count. Keep the field names so the lineage hover-tip can show what flowed.
  const linkMap = new Map();
  for (const e of graph.edges) {
    const key = `${e.source}→${e.target}`;
    const cur = linkMap.get(key);
    if (cur) {
      cur.value += 1;
      cur.fields.push(e.field_name);
    } else {
      linkMap.set(key, {
        source: e.source,
        target: e.target,
        value: 1,
        fields: [e.field_name],
      });
    }
  }
  const links = Array.from(linkMap.values());

  // d3-sankey mutates its input — give it a fresh copy each render.
  const nodes = graph.nodes.map((n) => ({ ...n }));

  const sankeyGen = d3
    .sankey()
    .nodeId((d) => d.id)
    .nodeAlign(d3.sankeyJustify)
    .nodeWidth(12)
    .nodePadding(4)
    .extent([
      [180, 18],
      [width - 240, height - 10],
    ]);

  let layout;
  try {
    layout = sankeyGen({ nodes, links: links.map((l) => ({ ...l })) });
  } catch {
    return; // bad data shape — render nothing rather than crash
  }
  const laidOutNodes = layout.nodes;
  const laidOutLinks = layout.links;

  // Build neighbor index so hover can dim non-connected nodes/links in one
  // pass without recomputing per-element.
  const neighborsByNode = new Map();
  for (const n of laidOutNodes) neighborsByNode.set(n.id, new Set([n.id]));
  for (const l of laidOutLinks) {
    neighborsByNode.get(l.source.id).add(l.target.id);
    neighborsByNode.get(l.target.id).add(l.source.id);
  }
  const linkTouchesNode = (link, nodeId) =>
    link.source.id === nodeId || link.target.id === nodeId;

  // Resolve highlightField (a field-level click in another panel) to a
  // (sourceId | null, targetId) pair so the matching ribbons can render in
  // the highlight color even without hover.
  const focusTargetId = highlightField
    ? `ent:${highlightField.entity_type}:${highlightField.entity_key}`
    : null;

  // Column labels.
  root
    .append("text")
    .attr("class", "sankey-col-label")
    .attr("x", 70)
    .attr("y", 14)
    .attr("text-anchor", "middle")
    .text("SOURCES");
  root
    .append("text")
    .attr("class", "sankey-col-label")
    .attr("x", width - 80)
    .attr("y", 14)
    .attr("text-anchor", "middle")
    .text("ENTITIES");

  // Links — d3.sankeyLinkHorizontal returns the path generator. Stroke width
  // scales with field-count so dense pairs read as thicker ribbons.
  const linkSel = root
    .append("g")
    .attr("class", "sankey-links")
    .selectAll("path")
    .data(laidOutLinks)
    .enter()
    .append("path")
    .attr("class", (d) =>
      focusTargetId && d.target.id === focusTargetId
        ? "sankey-link sankey-link-focus"
        : "sankey-link"
    )
    .attr("d", d3.sankeyLinkHorizontal())
    .attr("stroke-width", (d) => Math.max(1.2, d.width));
  linkSel.append("title").text((d) => `${d.source.label} → ${d.target.label}\n${d.value} field(s): ${d.fields.join(", ")}`);

  // Nodes — entities (right column) get the gradient fill; documents (left)
  // get a card-like rect with the doc-type icon + filename.
  const nodeG = root
    .append("g")
    .attr("class", "sankey-nodes")
    .selectAll("g")
    .data(laidOutNodes)
    .enter()
    .append("g")
    .attr("class", (d) => {
      const isFocus = focusTargetId && d.id === focusTargetId;
      return `sankey-node sankey-node-${d.kind}${isFocus ? " sankey-node-focus" : ""}`;
    })
    .attr("transform", (d) => `translate(${d.x0}, ${d.y0})`);

  nodeG
    .append("rect")
    .attr("width", (d) => d.x1 - d.x0)
    .attr("height", (d) => Math.max(2, d.y1 - d.y0))
    .attr("rx", 3);

  // Document labels (left side, anchored to the right of the column so the
  // text reads inward toward the ribbons).
  nodeG
    .filter((d) => d.kind === "document")
    .each(function (d) {
      const meta = K.docMeta({ source_class: d.sub, label: d.label });
      const g = d3.select(this);
      const labelX = -8;
      const labelY = (d.y1 - d.y0) / 2;
      const text = g
        .append("text")
        .attr("class", "sankey-label sankey-label-doc")
        .attr("x", labelX)
        .attr("y", labelY)
        .attr("dy", "0.32em")
        .attr("text-anchor", "end");
      text.append("tspan").attr("class", "sankey-label-icon").text(`${meta.icon} ${meta.typeLabel} `);
      const trimmed = (d.label || "").replace(/\.md$/, "");
      text
        .append("tspan")
        .attr("class", "sankey-label-sub")
        .text(trimmed.length > 24 ? trimmed.slice(0, 22) + "…" : trimmed);
    });

  // Entity labels (right side). Sankey allocates vertical space proportional
  // to ribbon weight, so a long-tail of low-traffic entities ends up with
  // <10px of room — labels there overlap and become unreadable. Mark those
  // nodes with `.has-tiny-label` so CSS can hide the label by default and
  // re-show it on hover (the neighbor-focus handler also adds `.hot`).
  nodeG
    .filter((d) => d.kind === "entity")
    .each(function (d) {
      const g = d3.select(this);
      const nodeH = d.y1 - d.y0;
      const labelX = (d.x1 - d.x0) + 8;
      const labelY = nodeH / 2;
      if (nodeH < 12) g.classed("has-tiny-label", true);
      const text = g
        .append("text")
        .attr("class", "sankey-label sankey-label-ent")
        .attr("x", labelX)
        .attr("y", labelY)
        .attr("dy", "0.32em")
        .attr("text-anchor", "start");
      text.append("tspan").attr("class", "sankey-label-type").text(`${d.sub} `);
      text.append("tspan").attr("class", "sankey-label-key").text(d.label);
      // Tooltip so labels hidden via .has-tiny-label are still discoverable
      // by hovering the rectangle itself.
      g.append("title").text(`${d.sub} · ${d.label} (${d.value} field-write${d.value === 1 ? "" : "s"})`);
    });

  // Neighbor highlight: on hover, mark connected nodes/links and dim the rest.
  // Uses class toggles so the visual state is driven by CSS, not inline attrs
  // (lets us tune colors / transitions in styles.css without touching JS).
  function focus(nodeId) {
    const neighbors = neighborsByNode.get(nodeId) || new Set([nodeId]);
    nodeG.classed("dim", (d) => !neighbors.has(d.id));
    nodeG.classed("hot", (d) => d.id === nodeId);
    linkSel.classed("dim", (l) => !linkTouchesNode(l, nodeId));
    linkSel.classed("hot", (l) => linkTouchesNode(l, nodeId));
  }
  function unfocus() {
    nodeG.classed("dim", false).classed("hot", false);
    linkSel.classed("dim", false).classed("hot", false);
  }
  nodeG.on("mouseenter", (_evt, d) => focus(d.id)).on("mouseleave", unfocus);
  linkSel
    .on("mouseenter", (_evt, l) => focus(l.target.id))
    .on("mouseleave", unfocus);
}

window.K.GraphPanel = function GraphPanel({ refresh, highlightField }) {
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(true);
  const svgRef = useRef(null);
  const wrapRef = useRef(null);

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

  const docs = graph.nodes.filter((n) => n.kind === "document");
  const ents = graph.nodes.filter((n) => n.kind === "entity");

  // Render whenever data, highlight, or container size change. Sankey layout
  // depends on width/height so we observe the wrapper and recompute on resize.
  useEffect(() => {
    if (!svgRef.current || !wrapRef.current) return;
    if (docs.length === 0 || ents.length === 0) return;
    if (!window.d3 || typeof window.d3.sankey !== "function") return;

    const d3 = window.d3;
    const draw = () => {
      const wrap = wrapRef.current;
      if (!wrap) return;
      const width = wrap.clientWidth || 700;
      const height = wrap.clientHeight || 360;
      renderSankey({
        svg: svgRef.current,
        width,
        height,
        graph,
        highlightField,
      });
    };

    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, [graph, highlightField]);

  // Aggregate edges per (source, target) for the badge in panel-head.
  const linkPairCount = new Set(graph.edges.map((e) => `${e.source}→${e.target}`)).size;

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Reasoning graph</span>
        <span className="panel-sub">all memory · hover to focus</span>
        <span className="spacer" />
        <span className="panel-sub">
          {docs.length} sources · {ents.length} entities · {linkPairCount} flows
        </span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        <div className="graph-wrap" ref={wrapRef}>
          {loading && (
            <div style={{ padding: 14, color: "var(--ink-3)", fontSize: 11 }}>loading graph…</div>
          )}
          {!loading && (docs.length === 0 || ents.length === 0) && (
            <div style={{ padding: 14, color: "var(--ink-3)", fontSize: 11 }}>
              No documents or entities yet.
            </div>
          )}
          <svg
            ref={svgRef}
            className="graph-svg sankey-svg"
            style={{
              display: !loading && docs.length > 0 && ents.length > 0 ? "block" : "none",
            }}
          />
        </div>
      </div>
    </div>
  );
};

// ── Work Panel ──────────────────────────────────────────────────────────────
// Tabbed wrapper that holds Ingestion pipeline + Reasoning graph in the same
// horizontal slot. The graph needs more room than a 1fr column allows; tabbing
// the two views lets whichever one is active take the full 2-column width.
window.K.WorkPanel = function WorkPanel({
  documents,
  activeDocId,
  onPickDoc,
  onIngestEmail,
  pendingDoc,
  refresh,
  highlightField,
}) {
  const [tab, setTab] = useState("graph");
  return (
    <div className="panel work-panel">
      <div className="panel-head work-tabs">
        <button
          className={K.cls("work-tab", tab === "graph" && "active")}
          onClick={() => setTab("graph")}
        >
          Reasoning graph
        </button>
        <button
          className={K.cls("work-tab", tab === "ingest" && "active")}
          onClick={() => setTab("ingest")}
        >
          Ingestion pipeline
        </button>
        <span className="spacer" />
      </div>
      <div className="panel-body work-body">
        <div className="work-pane" style={{ display: tab === "graph" ? "flex" : "none" }}>
          <K.GraphPanel refresh={refresh} highlightField={highlightField} />
        </div>
        <div className="work-pane" style={{ display: tab === "ingest" ? "flex" : "none" }}>
          <K.ExtractionPanel
            documents={documents}
            activeDocId={activeDocId}
            onPickDoc={onPickDoc}
            onIngestEmail={onIngestEmail}
            pendingDoc={pendingDoc}
          />
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
