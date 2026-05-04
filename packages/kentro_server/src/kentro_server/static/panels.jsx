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
// ── Extraction Steps List ───────────────────────────────────────────────────
// Per-document trace of every LLM extraction call that produced its writes.
// Surfaces telemetry the rest of the UI doesn't show: model name, produced
// fact count, latency. Rendered inside the inline doc pane underneath the
// styled content so users can see "what was extracted" right next to "from
// what input".
window.K.ExtractionStepsList = function ExtractionStepsList({ documentId, docLabel }) {
  const [steps, setSteps] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!documentId) {
      setSteps([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    K.api
      .listExtractionSteps(documentId)
      .then((s) => {
        if (!cancelled) setSteps(s || []);
      })
      .catch(() => {
        if (!cancelled) setSteps([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  if (loading) {
    return <div className="extraction-stream-empty">loading extraction trace…</div>;
  }
  if (steps.length === 0) {
    return (
      <div className="extraction-stream-empty">No extraction steps recorded for this document.</div>
    );
  }
  return (
    <div className="extraction-stream">
      <div className="ext-step">
        <span className="ts">+000ms</span>
        <span className="msg">
          read <span className="val">{K.docLabel(docLabel) || (documentId || "").slice(0, 8)}</span>
        </span>
      </div>
      {steps.map((s, i) => (
        <div key={s.id} className="ext-step">
          <span className="ts">+{(80 + i * 80).toString().padStart(3, "0")}ms</span>
          <span className="msg">
            extracted <span className="ent">{s.produced_writes}</span>{" "}
            <span className="field">facts</span>{" "}
            <span style={{ color: "var(--ink-3)" }}>({s.latency_ms}ms)</span>
          </span>
        </div>
      ))}
      <div className="ext-step">
        <span className="ts">+{(80 + steps.length * 80).toString().padStart(3, "0")}ms</span>
        <span className="msg">saved facts with lineage back to the source</span>
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
function renderSankey({ svg, width, height, graph, highlightField, onOpenDoc, onOpenEntity }) {
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
  linkSel.append("title").text((d) => `${K.docLabel(d.source.label)} → ${d.target.label}\n${d.value} field(s): ${d.fields.join(", ")}`);

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
      const trimmed = K.docLabel(d.label);
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
      g.append("title").text(`${d.sub} · ${K.docLabel(d.label) || d.label} (${d.value} field-write${d.value === 1 ? "" : "s"})`);
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

  // Click a doc node to open its content drawer. Node ids are formatted as
  // `doc:<uuid>` (matches the /viz/graph response shape); we strip the prefix
  // before handing the UUID to the caller's onOpenDoc handler.
  if (typeof onOpenDoc === "function") {
    nodeG
      .filter((d) => d.kind === "document")
      .style("cursor", "pointer")
      .on("click", (_evt, d) => {
        const docId = d.id.startsWith("doc:") ? d.id.slice(4) : d.id;
        onOpenDoc(docId);
      });
  }

  // Click an entity node to open its global + per-agent comparison drawer.
  // Node ids are formatted as `ent:<type>:<key>` — split on the first two
  // `:` so keys containing colons (rare, but possible) survive intact.
  if (typeof onOpenEntity === "function") {
    nodeG
      .filter((d) => d.kind === "entity")
      .style("cursor", "pointer")
      .on("click", (_evt, d) => {
        const id = d.id || "";
        if (!id.startsWith("ent:")) return;
        const rest = id.slice(4);
        const idx = rest.indexOf(":");
        if (idx < 0) return;
        const type = rest.slice(0, idx);
        const key = rest.slice(idx + 1);
        onOpenEntity({ entity_type: type, entity_key: key });
      });
  }
}

window.K.GraphPanel = function GraphPanel({
  refresh,
  highlightField,
  onOpenDoc,
  onOpenEntity,
  documents,
  onIngestEmail,
  pendingDoc,
}) {
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
        onOpenDoc,
        onOpenEntity,
      });
    };

    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, [graph, highlightField, onOpenDoc, onOpenEntity]);

  // Aggregate edges per (source, target) for the badge in panel-head.
  const linkPairCount = new Set(graph.edges.map((e) => `${e.source}→${e.target}`)).size;

  // Show the demo "drop the Jane Doe email" trigger right in the graph head
  // so the demo presenter has a one-click way to introduce the conflict
  // scene from the same panel they're narrating.
  const emailLabel = "email_jane_2026-04-17.md";
  const docList = documents || [];
  const hasEmail = docList.some((d) => d.label === emailLabel);
  const showIngestButton = typeof onIngestEmail === "function";

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Reasoning graph</span>
        <span className="panel-sub">all memory · hover to focus</span>
        <span className="spacer" />
        {showIngestButton && (
          <button
            className="ingest-email-btn"
            onClick={onIngestEmail}
            disabled={pendingDoc || hasEmail}
            title={hasEmail ? "already ingested" : "ingest the demo email from Jane Doe"}
          >
            {pendingDoc ? "ingesting…" : hasEmail ? "✓ jane email" : "+ drop ✉️ jane email"}
          </button>
        )}
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
  onIngestEmail,
  pendingDoc,
  refresh,
  highlightField,
  onOpenDoc,
  onOpenEntity,
}) {
  return (
    <div className="panel work-panel">
      <div className="work-pane">
        <div className="work-graph-area">
          <K.GraphPanel
            refresh={refresh}
            highlightField={highlightField}
            onOpenDoc={onOpenDoc}
            onOpenEntity={onOpenEntity}
            documents={documents}
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

// ── Source-class-styled body renderers ─────────────────────────────────────
// Each renderer takes the parsed structure from `K.parseDocumentContent(doc)`
// and returns JSX shaped like the tool the source came from (Gong call /
// Jira ticket / Gmail email). Markup-only — colors live in styles.css.

function CallBody({ parsed }) {
  // Color speaker pills deterministically by name so multi-speaker
  // transcripts read like a real Gong call (each speaker keeps the same hue
  // throughout). Hash → index into a small palette.
  const speakerHues = {};
  let nextHue = 0;
  const palette = ["accent-blue", "accent-green", "accent-purple", "accent-orange"];
  const hueFor = (name) => {
    if (!speakerHues[name]) {
      speakerHues[name] = palette[nextHue % palette.length];
      nextHue++;
    }
    return speakerHues[name];
  };
  // The corpus calls have no real timestamps, so synthesize per-turn time
  // ranges from word-count at ~150 wpm with a 5s minimum. Reads like a Gong
  // transcript — each turn shows `MM:SS-MM:SS` next to the speaker name —
  // without making up data the user could mistake for ground truth (the
  // synthesis is deterministic from the visible text).
  const fmt = (sec) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };
  let cursor = 0;
  const stamped = parsed.turns.map((t) => {
    const words = (t.text || "").trim().split(/\s+/).filter(Boolean).length;
    const dur = Math.max(5, Math.round((words / 150) * 60));
    const start = cursor;
    const end = cursor + dur;
    cursor = end;
    return { ...t, start: fmt(start), end: fmt(end) };
  });
  return (
    <div className="doc-call">
      {parsed.title && <div className="doc-call-title">{parsed.title}</div>}
      <div className="doc-call-transcript">
        {stamped.map((t, i) => (
          <div key={i} className={K.cls("doc-call-turn", `hue-${hueFor(t.speaker)}`)}>
            <div className="doc-call-speaker">
              <span className="doc-call-name">{t.speaker}</span>
              <span className="doc-call-time">{t.start}–{t.end}</span>
            </div>
            <div className="doc-call-text">{t.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TicketBody({ parsed }) {
  // Pull a couple of well-known fields out so they render as the colored
  // header chips Jira shows (status + severity); everything else stays in
  // the field grid below the header.
  const wellKnown = new Set(["status", "severity", "priority"]);
  const chips = parsed.fields.filter((f) => wellKnown.has(f.key.toLowerCase()));
  const otherFields = parsed.fields.filter((f) => !wellKnown.has(f.key.toLowerCase()));
  return (
    <div className="doc-ticket">
      <div className="doc-ticket-header">
        <div className="doc-ticket-title">{parsed.title || "Ticket"}</div>
        <div className="doc-ticket-chips">
          {chips.map((c, i) => (
            <span
              key={i}
              className={K.cls("doc-ticket-chip", `chip-${c.key.toLowerCase()}-${c.value.toLowerCase().replace(/\s+/g, "-")}`)}
            >
              <span className="chip-key">{c.key}</span>
              <span className="chip-value">{c.value}</span>
            </span>
          ))}
        </div>
      </div>
      {otherFields.length > 0 && (
        <div className="doc-ticket-fields">
          {otherFields.map((f, i) => (
            <div key={i} className="doc-ticket-field">
              <span className="doc-ticket-field-key">{f.key}</span>
              <span className="doc-ticket-field-value">{f.value}</span>
            </div>
          ))}
        </div>
      )}
      <div className="doc-ticket-sections">
        {parsed.sections.map((s, i) => (
          <div key={i} className="doc-ticket-section">
            {s.heading && <h4 className="doc-ticket-section-heading">{s.heading}</h4>}
            <div className="doc-ticket-section-body">
              {s.body.split(/\n\n+/).map((para, pi) => (
                <p key={pi}>
                  <InlineMarkdown text={para} />
                </p>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function EmailBody({ parsed }) {
  return (
    <div className="doc-email">
      <div className="doc-email-header">
        {parsed.subject && <div className="doc-email-subject">{parsed.subject}</div>}
        <div className="doc-email-meta">
          {parsed.from && (
            <div className="doc-email-row">
              <span className="doc-email-label">from</span>
              <span className="doc-email-value">{parsed.from}</span>
            </div>
          )}
          {parsed.to && (
            <div className="doc-email-row">
              <span className="doc-email-label">to</span>
              <span className="doc-email-value">{parsed.to}</span>
            </div>
          )}
          {parsed.date && (
            <div className="doc-email-row">
              <span className="doc-email-label">date</span>
              <span className="doc-email-value">{parsed.date}</span>
            </div>
          )}
        </div>
      </div>
      <div className="doc-email-body">{parsed.body}</div>
    </div>
  );
}

function SlackBody({ parsed }) {
  // Avatar: deterministic background from the handle so the same person
  // keeps the same color tile across the thread.
  const colorFor = (handle) => {
    const palette = ["#4A9EFF", "#36C5AB", "#E0B038", "#D672D5", "#E66B6B"];
    let h = 0;
    for (let i = 0; i < handle.length; i++) h = (h * 31 + handle.charCodeAt(i)) >>> 0;
    return palette[h % palette.length];
  };
  return (
    <div className="doc-slack">
      <div className="doc-slack-header">
        <div className="doc-slack-channel">{parsed.title || "Slack"}</div>
        {parsed.subtitle && <div className="doc-slack-subtitle">{parsed.subtitle}</div>}
      </div>
      <div className="doc-slack-thread">
        {parsed.messages.map((m, i) => {
          const initial = (m.handle || "?").slice(0, 1).toUpperCase();
          return (
            <div key={i} className="doc-slack-msg">
              <div className="doc-slack-avatar" style={{ background: colorFor(m.handle) }}>
                {initial}
              </div>
              <div className="doc-slack-msg-body">
                <div className="doc-slack-msg-meta">
                  <span className="doc-slack-handle">{m.handle}</span>
                  <span className="doc-slack-time">{m.time}</span>
                </div>
                <div className="doc-slack-text">{m.text}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function InlineMarkdown({ text }) {
  // Render bold/code/text segments produced by tokenizeInlineMarkdown so a
  // line like `**Action:** call Sarah` keeps the bold key intact.
  const tokens = K.tokenizeInlineMarkdown(text || "");
  return (
    <React.Fragment>
      {tokens.map((tok, i) => {
        if (tok.type === "bold") return <strong key={i}>{tok.text}</strong>;
        if (tok.type === "code") return <code key={i}>{tok.text}</code>;
        return <React.Fragment key={i}>{tok.text}</React.Fragment>;
      })}
    </React.Fragment>
  );
}

function NoteBody({ parsed }) {
  return (
    <div className="doc-note">
      {parsed.blocks.map((b, i) => {
        if (b.type === "heading" && b.level === 2)
          return <h3 key={i} className="doc-note-h2"><InlineMarkdown text={b.text} /></h3>;
        if (b.type === "heading" && b.level === 3)
          return <h4 key={i} className="doc-note-h3"><InlineMarkdown text={b.text} /></h4>;
        if (b.type === "list")
          return (
            <ul key={i} className="doc-note-list">
              {b.items.map((item, j) => (
                <li key={j}><InlineMarkdown text={item} /></li>
              ))}
            </ul>
          );
        if (b.type === "field")
          return (
            <div key={i} className="doc-note-field">
              <span className="doc-note-field-key">{b.key}</span>
              <span className="doc-note-field-value"><InlineMarkdown text={b.value} /></span>
            </div>
          );
        return (
          <p key={i} className="doc-note-p">
            <InlineMarkdown text={b.text} />
          </p>
        );
      })}
    </div>
  );
}

function DocumentBody({ doc }) {
  if (!doc) return null;
  const parsed = K.parseDocumentContent(doc);
  switch (parsed.kind) {
    case "call":
      return <CallBody parsed={parsed} />;
    case "ticket":
      return <TicketBody parsed={parsed} />;
    case "email":
      return <EmailBody parsed={parsed} />;
    case "slack":
      return <SlackBody parsed={parsed} />;
    case "note":
      return <NoteBody parsed={parsed} />;
    default:
      return <pre className="document-content">{parsed.content || doc.content}</pre>;
  }
}

// ── Source Overlay ──────────────────────────────────────────────────────────
// Global right-edge slide-over that covers everything except the topbar (the
// graph, the agent panels, the policy editor, the access matrix). Opens when
// a doc node is clicked anywhere in the app and shows the source rendered in
// a tool-shaped frame (Gong/Jira/Gmail/Slack) plus the per-document
// extraction-step trace below it.
window.K.SourceOverlay = function SourceOverlay({ open, documentId, onClose }) {
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!open || !documentId) {
      setDoc(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    K.api
      .getDocumentContent(documentId)
      .then((d) => {
        if (!cancelled) setDoc(d);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, documentId]);

  // Close on Escape so keyboard users can dismiss without aiming for the X.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const meta = doc ? K.docMeta({ source_class: doc.source_class, label: doc.label }) : null;
  return (
    <React.Fragment>
      <div
        className={K.cls("source-overlay-backdrop", open && "open")}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside
        className={K.cls("source-overlay", open && "open")}
        aria-hidden={!open}
        role="dialog"
        aria-label="Source content"
      >
        <div className="source-overlay-head">
          <span className="title">
            {meta ? `${meta.icon} ${meta.typeLabel}` : "source"}
            {doc && doc.label && <span> · {K.docLabel(doc.label)}</span>}
          </span>
          <button onClick={onClose} aria-label="Close">esc</button>
        </div>
        <div className="source-overlay-body">
          {loading && <p style={{ color: "var(--ink-3)" }}>loading content…</p>}
          {error && <p style={{ color: "var(--bad)" }}>failed to load: {error}</p>}
          {!loading && !error && doc && (
            <React.Fragment>
              <DocumentBody doc={doc} />
              <div className="source-overlay-trace">
                <h4 className="source-overlay-trace-heading">Extraction trace</h4>
                <K.ExtractionStepsList documentId={documentId} docLabel={doc.label} />
              </div>
            </React.Fragment>
          )}
        </div>
      </aside>
    </React.Fragment>
  );
};


// ── Entity Overlay ──────────────────────────────────────────────────────────
// Right-edge slide-over (same chrome as SourceOverlay) that opens when an
// entity node is clicked in the sankey. Shows the canonical/global state of
// the entity (admin's view, no ACL) plus what each non-admin agent sees,
// stacked as cards so users can compare side-by-side at a glance.
function EntityViewCard({ agent, type, key_, refresh, onFieldClick }) {
  const [record, setRecord] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    K.api
      .readEntityAs(agent.agent_id, type, key_)
      .then((r) => {
        if (!cancelled) setRecord(r);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agent.agent_id, type, key_, refresh]);

  // Tag class — sales|cs match the existing prototype CSS for header colors;
  // the admin agent renders as a generic "Global" header (the canonical
  // unredacted view), since "ingestion_agent · admin" is implementation
  // detail that doesn't help the demo audience.
  const tagCls =
    agent.agent_id === "customer_service"
      ? "cs"
      : agent.agent_id === "sales"
        ? "sales"
        : "admin";
  const headerLabel = agent.is_admin
    ? "Global"
    : agent.display_name || agent.agent_id;
  const fieldNames = record ? Object.keys(record.fields || {}) : [];
  return (
    <div className="entity-card">
      <div className="entity-card-head">
        <span className={K.cls("agent-tag", tagCls)}>
          <span className="swatch"></span>
          {headerLabel}
        </span>
      </div>
      <div className="entity-card-body">
        {loading && <p style={{ color: "var(--ink-3)", fontSize: 11 }}>loading…</p>}
        {error && <p style={{ color: "var(--bad)", fontSize: 11 }}>{error}</p>}
        {!loading && !error && record && fieldNames.length === 0 && (
          <p style={{ color: "var(--ink-3)", fontSize: 11 }}>
            ⊘ {type}.{key_} — no fields visible to this agent
          </p>
        )}
        {!loading && !error && record && fieldNames.length > 0 && (
          <div className="entity-card-fields">
            {fieldNames.map((fname) => {
              const f = record.fields[fname];
              const status = f.status;
              // Hidden fields have no lineage to inspect (the agent literally
              // can't see them) — leave them non-clickable. Every other status
              // (known / unknown / unresolved) has lineage worth showing.
              const clickable = status !== "hidden" && typeof onFieldClick === "function";
              return (
                <div
                  key={fname}
                  className={K.cls(
                    "entity-card-field",
                    status === "hidden" && "hidden",
                    status === "unresolved" && "unresolved",
                    clickable && "clickable"
                  )}
                  onClick={
                    clickable
                      ? () =>
                          onFieldClick({
                            agent_id: agent.agent_id,
                            entity_type: type,
                            entity_key: key_,
                            field_name: fname,
                          })
                      : undefined
                  }
                >
                  <span className="entity-card-field-name">{fname}</span>
                  <span className="entity-card-field-value">{K.fmtFieldValue(f)}</span>
                  <span className={`field-status status-${status}`}>{status.toUpperCase()}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

window.K.EntityOverlay = function EntityOverlay({ open, payload, onClose, refresh, onFieldClick }) {
  // Close on Escape (mirrors SourceOverlay).
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!payload) {
    // Render an empty overlay so the close transition still plays — payload
    // gets cleared after the slide-out animation finishes (or never; we just
    // always render and let the open class drive visibility).
    return (
      <React.Fragment>
        <div className={K.cls("source-overlay-backdrop", open && "open")} aria-hidden />
        <aside className={K.cls("source-overlay", open && "open")} aria-hidden />
      </React.Fragment>
    );
  }

  const agents = K.api.getAgentList();
  // Cards show user-facing perspectives only: admin (Global / canonical
  // truth) + non-admin agents that aren't system workers. `ingestion_agent`
  // is a worker that writes extracted facts — its "view" is irrelevant to
  // the demo and would just duplicate Global with explicit ACL applied.
  const ordered = [
    ...agents.filter((a) => a.is_admin),
    ...agents.filter((a) => !a.is_admin && a.agent_id !== "ingestion_agent"),
  ];

  return (
    <React.Fragment>
      <div
        className={K.cls("source-overlay-backdrop", open && "open")}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside
        className={K.cls("source-overlay", open && "open")}
        aria-hidden={!open}
        role="dialog"
        aria-label="Entity views"
      >
        <div className="source-overlay-head entity-overlay-head">
          <span className="entity-overlay-title">
            <span className="entity-type-chip">{payload.entity_type}</span>
            <span className="entity-key-text">{payload.entity_key}</span>
          </span>
          <button onClick={onClose} aria-label="Close">esc</button>
        </div>
        <div className="source-overlay-body entity-overlay-body">
          {ordered.map((agent) => (
            <EntityViewCard
              key={agent.agent_id}
              agent={agent}
              type={payload.entity_type}
              key_={payload.entity_key}
              refresh={refresh}
              onFieldClick={onFieldClick}
            />
          ))}
        </div>
      </aside>
    </React.Fragment>
  );
};

// ── Lineage Drawer ──────────────────────────────────────────────────────────
// Reads `GET /entities/{type}/{key}` and renders the resolution as a vertical
// flow: SOURCES → RESOLVER → RESULT, with animated connectors so the data
// flow reads as motion. The right-panel companion to EntityOverlay; clicking
// a field row opens this drawer to its left.
//
// `documents` (optional) maps doc IDs to their labels + source_class so we
// can show "📞 acme_call_2026-04-15.md" rather than "doc:abc12345" — same
// chrome the rest of the demo uses.
window.K.LineageDrawer = function LineageDrawer({ open, payload, onClose, shifted, documents }) {
  const [record, setRecord] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !payload) {
      setRecord(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
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

  // ESC closes the drawer *first* — without this, EntityOverlay's window-
  // level Escape handler eats the keystroke and the user can't dismiss the
  // drawer with the keyboard while EntityOverlay is open. Capture phase +
  // stopImmediatePropagation makes our handler fire before any sibling
  // overlay's bubbling listener sees the event.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopImmediatePropagation();
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!payload) return null;
  const fname = payload.field_name;
  const fval = record?.fields?.[fname];
  const docById = {};
  for (const d of documents || []) docById[d.id] = d;

  return (
    <>
      <div className={K.cls("drawer-overlay", open && "open")} onClick={onClose} />
      <aside className={K.cls("drawer", open && "open", shifted && "shifted")}>
        <div className="drawer-head">
          <span className="title">
            lineage · {payload.entity_type}.{payload.entity_key}.{fname}
          </span>
          <button onClick={onClose}>esc</button>
        </div>
        <div className="drawer-body">
          {loading && <p style={{ color: "var(--ink-3)" }}>loading…</p>}
          {!loading && fval && <LineageFlow fval={fval} docById={docById} />}
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

// ── Lineage Flow ────────────────────────────────────────────────────────────
// Horizontal pipeline rendered left-to-right inside the drawer body:
//
//   CANDIDATES         RESOLVER          RESULT
//   ┌──────┐                              ┌──────┐
//   │$250K ├──┐         ┌─────────┐       │$285K │
//   └──────┘  │         │ RESOLVE │       └──────┘
//   ┌──────┐  ├─────────┤  raw    ├───────  (orange,
//   │$300K ├──┤         └─────────┘        the picked
//   └──────┘  │                            value)
//   ┌──────┐  │
//   │$285K ├──┘
//   └──────┘
//
// Branches by status: `known` (one distinct value, possibly multiple sources
// corroborating it), `unresolved` (multiple distinct candidates), `hidden`
// (ACL-redacted), `unknown` (no writes yet). Connectors carry animated dots
// so the read pipeline looks alive even when the data is settled.
function LineageFlow({ fval, docById }) {
  if (fval.status === "hidden") {
    return (
      <div className="flow flow-stub">
        <div className="flow-stub-msg">
          ⊘ Field is hidden by ACL — sources, candidates, and the resolved
          value are all redacted for the agent that opened this drawer.
        </div>
        {fval.reason && <div className="flow-stub-reason">{fval.reason}</div>}
      </div>
    );
  }
  if (fval.status === "unknown") {
    return (
      <div className="flow flow-stub">
        <div className="flow-stub-msg">
          No writes recorded yet. Once a document writes this field the
          lineage flow will appear here.
        </div>
      </div>
    );
  }

  const isKnown = fval.status === "known";
  const lineage = fval.lineage || [];
  const candidates = fval.candidates || [];

  // Build per-source rows. Each row carries the value the SOURCE wrote
  // (from lineage.value), not the resolved value — three sources writing
  // 250K / 300K / 285K must show three distinct chips, even when the
  // resolver collapsed them onto a single winner. Falls back to fval.value
  // for old responses where lineage.value is absent.
  const valueOf = (lin, fallback) =>
    lin && lin.value !== undefined && lin.value !== null ? lin.value : fallback;
  const cands = [];
  if (isKnown) {
    for (const l of lineage) cands.push({ lineage: l, value: valueOf(l, fval.value) });
  } else {
    for (const c of candidates) {
      const lins = c.lineage || [];
      if (lins.length === 0) {
        cands.push({ lineage: null, value: c.value });
      } else {
        for (const l of lins) cands.push({ lineage: l, value: valueOf(l, c.value) });
      }
    }
  }

  // Resolver label — KNOWN with N>1 sources where every source wrote the
  // SAME value is true corroboration. KNOWN with N>1 sources where source
  // values differ means the resolver had to pick (latest_write etc.) — the
  // demo's "Customer.deal_size" rule. UNRESOLVED is a true conflict the
  // resolver couldn't merge.
  const distinctSourceValues = new Set(cands.map((c) => JSON.stringify(c.value))).size;
  const isCorroboration = isKnown && distinctSourceValues === 1 && cands.length > 1;
  const isLatestPick = isKnown && distinctSourceValues > 1;
  const resolverName = !isKnown
    ? "conflict"
    : isCorroboration
      ? "corroboration"
      : isLatestPick
        ? "latest write"
        : "direct";
  const resolverDetail = isKnown
    ? `${cands.length} → ${distinctSourceValues} value${distinctSourceValues === 1 ? "" : "s"}`
    : `${cands.length} → ${distinctSourceValues} values`;

  // Decide which candidate card is the WINNER so we can highlight its
  // connector. KNOWN: the winner's value matches the resolved fval.value;
  // when corroborating (all same), every row is a winner. UNRESOLVED: no
  // winner; every connector is dim.
  const winnerJson = isKnown ? JSON.stringify(fval.value) : null;
  const candIsWinner = (c) => winnerJson !== null && JSON.stringify(c.value) === winnerJson;

  // Per-source color palette — each candidate card and its arc gets a
  // unique hue so a viewer can read which source contributed which value.
  // Index-based (not source_class-based) so the colors stay distinct even
  // when N writes come from the same source class. The same hex is used by
  // both the card border and the SVG arc for visual coupling.
  const palette = [
    { stroke: "#4A9EFF", fill: "#EAF3FF", text: "#1B4A8A" },
    { stroke: "#36C5AB", fill: "#E5F8F4", text: "#185548" },
    { stroke: "#D672D5", fill: "#F8EAF7", text: "#5A1F59" },
    { stroke: "#E0A038", fill: "#FAF0DC", text: "#5A3F0E" },
    { stroke: "#E66B6B", fill: "#FBEAEA", text: "#5A1B1B" },
  ];
  const colorFor = (i) => palette[i % palette.length];

  return (
    <LineageFlowLayout
      cands={cands}
      docById={docById}
      colorFor={colorFor}
      isWinner={candIsWinner}
      isKnown={isKnown}
      resolverName={resolverName}
      resolverDetail={resolverDetail}
      reason={fval.reason}
      resultLabel={isKnown ? K.fmtFieldValue(fval) : "no winner"}
      resultStatus={fval.status}
    />
  );
}

// Lays out CANDIDATES / RESOLVER / RESULT as three flex regions and draws
// the connecting arcs as SVG paths underneath. After the DOM is measured,
// every path runs from a card's right-middle to the resolver's left-middle
// (and from resolver-right to result-left for the outbound) — no more
// dangling stubs.
function LineageFlowLayout({
  cands,
  docById,
  colorFor,
  isWinner,
  isKnown,
  resolverName,
  resolverDetail,
  reason,
  resultLabel,
  resultStatus,
}) {
  const containerRef = useRef(null);
  const cardRefs = useRef([]);
  const resolverRef = useRef(null);
  const resultRef = useRef(null);
  const [geom, setGeom] = useState(null);

  // Recompute SVG path geometry whenever the layout could shift: cand count
  // change, container resize, font metric changes after fonts load. The
  // ResizeObserver covers width/height; the cand-count dep covers list
  // changes; we also re-run on the next animation frame in case fonts
  // arrive after first paint.
  useEffect(() => {
    const compute = () => {
      const ctn = containerRef.current;
      const resolver = resolverRef.current;
      const result = resultRef.current;
      if (!ctn || !resolver || !result) return;
      const cBox = ctn.getBoundingClientRect();
      const rBox = resolver.getBoundingClientRect();
      const rsBox = result.getBoundingClientRect();
      const inPaths = cardRefs.current.map((el) => {
        if (!el) return null;
        const b = el.getBoundingClientRect();
        return {
          x1: b.right - cBox.left,
          y1: b.top + b.height / 2 - cBox.top,
          x2: rBox.left - cBox.left,
          y2: rBox.top + rBox.height / 2 - cBox.top,
        };
      });
      const outPath = {
        x1: rBox.right - cBox.left,
        y1: rBox.top + rBox.height / 2 - cBox.top,
        x2: rsBox.left - cBox.left,
        y2: rsBox.top + rsBox.height / 2 - cBox.top,
      };
      setGeom({ width: cBox.width, height: cBox.height, inPaths, outPath });
    };
    compute();
    const ro = new ResizeObserver(compute);
    if (containerRef.current) ro.observe(containerRef.current);
    // One more pass after fonts/layout settle so chip widths are stable.
    const raf = requestAnimationFrame(compute);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [cands.length, isKnown]);

  // Bezier path from card-right to resolver-left. Control offset = 45% of
  // dx — gentle S-curve when arcs converge. No clamp minimum so short
  // distances don't blow the curve out into a U-shape.
  const pathD = (p) => {
    const dx = p.x2 - p.x1;
    const cp = Math.max(20, dx * 0.45);
    return `M${p.x1},${p.y1} C${p.x1 + cp},${p.y1} ${p.x2 - cp},${p.y2} ${p.x2},${p.y2}`;
  };

  return (
    <div className="flow-h2" ref={containerRef}>
      <div className="flow-h2-col flow-h2-col-cands">
        <div className="flow-h2-col-label">CANDIDATES</div>
        <div className="flow-h2-col-body">
          {cands.map((c, i) => {
            const color = colorFor(i);
            const winner = isWinner(c);
            const lin = c.lineage;
            const docId = lin?.source_document_id;
            const doc = docId ? docById[docId] : null;
            const meta = doc
              ? K.docMeta({ source_class: doc.source_class, label: doc.label })
              : null;
            const titleLabel = meta ? meta.typeLabel.toLowerCase() : "source";
            const subLabel = doc
              ? K.docLabel(doc.label)
              : docId
                ? `doc:${docId.slice(0, 8)}`
                : lin?.written_by_agent_id || "(no source)";
            return (
              <div
                key={i}
                ref={(el) => (cardRefs.current[i] = el)}
                className={K.cls("flow-h2-cand", winner && "is-winner")}
                style={{
                  borderColor: color.stroke,
                  background: color.fill,
                }}
              >
                <span className="flow-h2-cand-icon">{meta ? meta.icon : "📄"}</span>
                <span className="flow-h2-cand-text">
                  <span className="flow-h2-cand-title" style={{ color: color.text }}>
                    {titleLabel}
                  </span>
                  <span className="flow-h2-cand-sub" title={subLabel}>
                    {subLabel}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="flow-h2-col flow-h2-col-resolver">
        <div className="flow-h2-col-label">RESOLVER</div>
        <div className="flow-h2-col-body">
          <div
            ref={resolverRef}
            className={K.cls("flow-h2-resolver", isKnown ? "is-ok" : "is-warn")}
          >
            <div className="flow-h2-resolver-title">RESOLVE</div>
            <div className="flow-h2-resolver-sub">{resolverName}</div>
          </div>
          <div className="flow-h2-resolver-detail">{resolverDetail}</div>
          {reason && <div className="flow-h2-resolver-reason">{reason}</div>}
        </div>
      </div>

      <div className="flow-h2-col flow-h2-col-result">
        <div className="flow-h2-col-label">RESULT</div>
        <div className="flow-h2-col-body">
          <div
            ref={resultRef}
            className={K.cls("flow-h2-result", `status-${resultStatus}`)}
          >
            <span className="flow-h2-result-value">{resultLabel}</span>
          </div>
        </div>
      </div>

      {geom && (
        <svg
          className="flow-h2-svg"
          width={geom.width}
          height={geom.height}
          viewBox={`0 0 ${geom.width} ${geom.height}`}
          aria-hidden
        >
          {geom.inPaths.map((p, i) => {
            if (!p) return null;
            const color = colorFor(i);
            const winner = isWinner(cands[i]);
            const opacity = winner || !isKnown ? 1 : 0.35;
            const d = pathD(p);
            const valueText = K.fmtCandidateValue(cands[i].value);
            // Anchor the value chip just outside the card on the card's
            // vertical centerline — keeps every chip horizontally aligned
            // and visually tied to its source row rather than the resolver.
            const chipX = p.x1 + 44;
            const chipY = p.y1;
            return (
              <g key={i} opacity={opacity}>
                <path
                  d={d}
                  fill="none"
                  stroke={color.stroke}
                  strokeWidth={winner || !isKnown ? 2.4 : 1.6}
                  strokeLinecap="round"
                />
                <circle r={4} fill={color.stroke}>
                  <animateMotion dur="1.6s" repeatCount="indefinite" path={d} />
                </circle>
                <g transform={`translate(${chipX}, ${chipY})`}>
                  <rect
                    x={-32}
                    y={-11}
                    width={64}
                    height={20}
                    rx={5}
                    ry={5}
                    fill="#fff"
                    stroke={color.stroke}
                    strokeWidth={1.2}
                  />
                  <text
                    x={0}
                    y={3}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fontFamily="var(--mono)"
                    fontSize="11"
                    fontWeight="700"
                    fill={color.text}
                  >
                    {valueText}
                  </text>
                </g>
              </g>
            );
          })}
          {geom.outPath && (
            <g>
              <path
                d={pathD(geom.outPath)}
                fill="none"
                stroke="#E66B36"
                strokeWidth={2.4}
                strokeLinecap="round"
              />
              <circle r={4} fill="#E66B36">
                <animateMotion
                  dur="1.6s"
                  repeatCount="indefinite"
                  path={pathD(geom.outPath)}
                />
              </circle>
            </g>
          )}
        </svg>
      )}
    </div>
  );
}
