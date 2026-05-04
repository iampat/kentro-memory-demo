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

// ── Access Matrix Table ─────────────────────────────────────────────────────
// Bare table — no panel chrome. Used both as a standalone Access Matrix
// panel (in the legacy bottom-right grid slot) and inline inside the new
// PolicyOverlay's "rules + matrix" composite view. Reads
// `GET /viz/access-matrix?entity_type=...` for whatever type is passed in.
window.K.AccessMatrixTable = function AccessMatrixTable({ entityType, refresh, changedKeys }) {
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

  if (loading) return <p style={{ color: "var(--ink-3)", fontSize: 11 }}>loading…</p>;
  if (!matrix) {
    return (
      <p style={{ color: "var(--ink-3)", fontSize: 11 }}>
        No matrix data — register the {entityType} schema first.
      </p>
    );
  }
  // Admin agents bypass ACL on reads (server-side `bypass_acl=is_admin`),
  // so the rule-derived matrix is misleading for them — every cell would
  // render as "invisible" because no FieldReadRule mentions the admin
  // agent. Filter admins out so the matrix only shows enforced permissions.
  const adminIds = new Set(
    K.api.getAgentList().filter((a) => a.is_admin).map((a) => a.agent_id)
  );
  const visibleAgents = matrix.agents.filter((a) => !adminIds.has(a));
  return (
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
        {visibleAgents.map((agent) => (
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

// ── Empty Right Rail ────────────────────────────────────────────────────────
// Placeholder rendered into the always-reserved right rail when no source
// or entity is selected. Keeps the graph layout stable — clicking around
// updates the rail in place without reflowing the canvas.
window.K.EmptyRightRail = function EmptyRightRail() {
  return (
    <aside className="source-overlay" role="complementary" aria-label="Selection details">
      <div className="source-overlay-head">
        <span className="title">no selection</span>
      </div>
      <div className="source-overlay-empty">
        Click an entity or source in the reasoning graph
        <br />
        to view its details here.
      </div>
    </aside>
  );
};

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

window.K.EntityOverlay = function EntityOverlay({ open, payload, onClose, refresh, onFieldClick, onOpenPolicy }) {
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
          <span className="entity-overlay-actions">
            {typeof onOpenPolicy === "function" && (
              <button
                className="entity-overlay-acl-chip"
                onClick={() => onOpenPolicy(payload.entity_type)}
                title={`view & edit ACL rules for ${payload.entity_type}`}
              >
                ACL
              </button>
            )}
            <button onClick={onClose} aria-label="Close">esc</button>
          </span>
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

// ── Policy Overlay ──────────────────────────────────────────────────────────
// Type-scoped view of "everything ACL" for one entity_type:
//
//   1. Access matrix (the agents × fields R/W grid, scoped to this type)
//   2. Rules list (FieldReadRule / EntityVisibilityRule / WriteRule /
//      ConflictRule whose entity_type matches), grouped by Rego package
//   3. NL editor (chat → parse → apply, same as the legacy PolicyEditor),
//      pre-prompted with suggestions relevant to the active type
//
// Lives in the LEFT-of-overlay slot (the same slot the LineageDrawer uses);
// opening one closes the other. Stacks left of EntityOverlay so the entity's
// identity stays visible while the user inspects/edits its rules.
const POLICY_SUGGESTIONS_BY_TYPE = {
  Customer: [
    { label: "Hide deal_size from CS", text: "Hide deal_size from Customer Service." },
    { label: "Prefer written over verbal", text: "On Customer.deal_size, written sources outweigh verbal." },
    { label: "CS reads support_tickets only", text: "Customer Service can read support_tickets but not edit them." },
  ],
  AuditLog: [
    { label: "Hide AuditLog from Sales", text: "Sales cannot see AuditLog." },
  ],
  Deal: [
    { label: "Sales can read Deal.size", text: "Sales can read Deal.size and Deal.stage." },
  ],
};

window.K.PolicyOverlay = function PolicyOverlay({ open, entityType, onClose, shifted, refresh, onApplied }) {
  const [rendered, setRendered] = useState({ version: 0, rules: [] });
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [parsed, setParsed] = useState(null);
  const [parsing, setParsing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [expanded, setExpanded] = useState({});
  const [error, setError] = useState(null);
  const [schemaTypes, setSchemaTypes] = useState([]);

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
    if (!open) return;
    reload();
    K.api.listSchema().then((types) => setSchemaTypes(types || [])).catch(() => setSchemaTypes([]));
  }, [open, reload, refresh]);

  // ESC closes the overlay first (capture-phase + stopImmediatePropagation),
  // mirroring LineageDrawer's behavior so it doesn't fall through to
  // EntityOverlay's bubble listener.
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
      // PR 35: server-side `apply_ruleset` does UPSERT — send only the
      // newly-parsed rules and the server merges them into the active
      // ruleset by (kind, agent, type, field|key).
      const onlyNew = {
        version: 0,
        rules: parsed.parsed_ruleset.rules || [],
      };
      const result = await K.api.applyRules(onlyNew, draft);
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

  if (!entityType) return null;

  // Filter rendered rules to those touching this entity_type. Server-rendered
  // summaries follow a stable shape ("[allow] ingestion_agent reads
  // Customer.name", "[hidden] sales sees AuditLog", etc.) — match against
  // the type token in word boundaries to avoid Customer matching CustomerX.
  const typeRe = new RegExp(`\\b${entityType}\\b`);
  const filteredRules = rendered.rules.filter(
    (r) => typeRe.test(r.summary || "") || typeRe.test(r.rego_body || r.rego || "")
  );

  const suggestions = POLICY_SUGGESTIONS_BY_TYPE[entityType] || [];

  return (
    <React.Fragment>
      <div className={K.cls("drawer-overlay", open && "open")} onClick={onClose} aria-hidden={!open} />
      <aside
        className={K.cls("drawer policy-overlay", open && "open", shifted && "shifted")}
        aria-hidden={!open}
        role="dialog"
        aria-label={`Policies for ${entityType}`}
      >
        <div className="drawer-head">
          <span className="title">
            policies · {entityType}
          </span>
          <button onClick={onClose} aria-label="Close">esc</button>
        </div>
        <div className="drawer-body policy-overlay-body">
          {/* Section 1: Access matrix scoped to entityType */}
          <div className="policy-section">
            <h4 className="policy-section-heading">Access matrix</h4>
            <K.AccessMatrixTable entityType={entityType} refresh={refresh} />
          </div>

          {/* Section 2: Rules filtered to entityType */}
          <div className="policy-section">
            <h4 className="policy-section-heading">
              Rules <span className="policy-section-count">{filteredRules.length}</span>
            </h4>
            {loading && <p style={{ padding: 8, color: "var(--ink-3)", fontSize: 11 }}>loading…</p>}
            {!loading && filteredRules.length === 0 && (
              <p style={{ padding: 8, color: "var(--ink-3)", fontSize: 11 }}>
                No rules touch <code>{entityType}</code> yet — add one via the chat below.
              </p>
            )}
            {!loading && filteredRules.length > 0 && (
              <div className="policy-list">
                {filteredRules.map((r, i) => {
                  const kind = policyKindOfSummary(r.summary);
                  const isExpanded = expanded[i];
                  return (
                    <div
                      key={i}
                      className={K.cls("policy-row", `kind-${kind}`)}
                      onClick={() => setExpanded({ ...expanded, [i]: !isExpanded })}
                    >
                      <div className="policy-row-main">
                        <span className={K.cls("policy-kind", `kind-${kind}`)}>{kind}</span>
                        <span className="policy-summary">{r.summary}</span>
                        <span className="policy-toggle">{isExpanded ? "▾" : "▸"}</span>
                      </div>
                      {isExpanded && (
                        <pre className="policy-rego">{r.rego_body || r.rego}</pre>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Section 3: NL editor */}
          <div className="policy-section">
            <h4 className="policy-section-heading">Edit</h4>
            {suggestions.length > 0 && (
              <div className="suggestion-row">
                <span className="suggestion-label">Try:</span>
                {suggestions.map((s, i) => (
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
            )}
            <div className="chat-box">
              <textarea
                className="chat-input"
                placeholder={`describe a change to ${entityType} rules in plain English`}
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
                <button className="primary" onClick={onParse} disabled={parsing || !draft.trim()}>
                  parse
                </button>
              </div>
            </div>
            {/* When parse returned 0 rules, the LLM's notes explain *why*
             * each intent was skipped (e.g. "Ticket isn't in the registered
             * schema"). Surface that reasoning so the user isn't left
             * staring at a silent "0 change(s) ready" prompt. */}
            {parsed && (parsed.parsed_ruleset?.rules?.length || 0) === 0 && parsed.notes && (
              <div className="parse-warn">
                <div className="parse-warn-head">
                  ⚠ {parsed.intents?.length || 0} intent
                  {(parsed.intents?.length || 0) === 1 ? "" : "s"} parsed, 0 rules produced
                </div>
                <div className="parse-warn-body">{parsed.notes}</div>
                <div className="parse-warn-hint">
                  Tip: only registered entity types can be referenced. Currently
                  registered: {schemaTypes.length > 0 ? schemaTypes.map((s) => s.name).join(", ") : "(loading…)"}.
                </div>
              </div>
            )}
            {parsed && parsed.parsed_ruleset?.rules?.length > 0 && (
              <div className="edit-preview">
                <div className="edit-preview-head">Pending changes</div>
                {parsed.parsed_ruleset.rules.map((r, i) => (
                  <div key={i} className="edit-row op-add">
                    <span className="edit-op op-add">+ add</span>
                    <span className="edit-summary">
                      {describeParsedRule(r)}
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
              <div style={{ color: "var(--bad)", fontFamily: "var(--mono)", fontSize: 10, padding: "6px 0" }}>
                {error}
              </div>
            )}
          </div>
        </div>
      </aside>
    </React.Fragment>
  );
};

// Mirror of the legacy PolicyEditor's helpers so PolicyOverlay can ship as a
// self-contained component without app.jsx wiring.
function policyKindOfSummary(summary) {
  const lower = (summary || "").toLowerCase();
  if (lower.includes("resolves")) return "conflict";
  if (lower.startsWith("[hidden]") || lower.includes(" sees ")) return "access";
  if (lower.includes(" writes ")) return "condition";
  return "access";
}

function describeParsedRule(r) {
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

// ── Lineage Drawer ──────────────────────────────────────────────────────────
// Reads `GET /entities/{type}/{key}` and renders the resolution as a vertical
// flow: SOURCES → RESOLVER → RESULT, with animated connectors so the data
// flow reads as motion. The right-panel companion to EntityOverlay; clicking
// a field row opens this drawer to its left.
//
// `documents` (optional) maps doc IDs to their labels + source_class so we
// can show "📞 acme_call_2026-04-15.md" rather than "doc:abc12345" — same
// chrome the rest of the demo uses.
window.K.LineageDrawer = function LineageDrawer({
  open,
  payload,
  onClose,
  shifted,
  documents,
  onEditResolver,
  onOpenDoc,
  refreshKey,
}) {
  const [record, setRecord] = useState(null);
  const [loading, setLoading] = useState(false);
  const [policyResolver, setPolicyResolver] = useState(null);

  useEffect(() => {
    if (!open || !payload) {
      setRecord(null);
      setPolicyResolver(null);
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
    K.api
      .getResolvers()
      .then((set) => {
        if (cancelled) return;
        const policy = (set.policies || []).find(
          (p) => p.entity_type === payload.entity_type && p.field_name === payload.field_name
        );
        setPolicyResolver(policy?.resolver || null);
      })
      .catch(() => {
        if (!cancelled) setPolicyResolver(null);
      });
    return () => {
      cancelled = true;
    };
  }, [open, payload, refreshKey]);

  // ESC closes the drawer *first* — without this, EntityOverlay's window-
  // level Escape handler eats the keystroke and the user can't dismiss the
  // drawer with the keyboard while EntityOverlay is open. Capture phase +
  // stopImmediatePropagation makes our handler fire before any sibling
  // overlay's bubbling listener sees the event.
  //
  // Defer ESC to the deep drawer (Source or Resolver) when one is open —
  // that drawer stacks left of lineage and should close first so the user
  // dismisses panels in last-in/first-out order. Our listener was registered
  // before the deep drawer's (lineage opens first), so without this guard
  // our handler fires first, calls stopImmediatePropagation, and the deep
  // drawer's listener never runs.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (document.querySelector(".drawer.drawer-deep.open")) return;
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
          {!loading && fval && (
            <LineageFlow
              fval={fval}
              docById={docById}
              entityType={payload.entity_type}
              fieldName={fname}
              onEditResolver={onEditResolver}
              onOpenDoc={onOpenDoc}
              policyResolver={policyResolver}
            />
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
function LineageFlow({
  fval,
  docById,
  entityType,
  fieldName,
  onEditResolver,
  onOpenDoc,
  policyResolver,
}) {
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
  // Prefer the actual configured resolver type when one exists — the chip
  // should reflect what's wired up, not a value-derived guess. Falls back to
  // the heuristic labels for status (corroboration/direct/conflict) when no
  // explicit policy is set, or for non-resolution cases (single source).
  const policyType = policyResolver?.type;
  const resolverTypeLabel =
    policyType === "auto"
      ? "auto ✨ Kentro AI"
      : policyType
        ? `${policyType.replace(/_/g, " ")} resolver`
        : null;
  const resolverName = !isKnown
    ? "conflict"
    : isCorroboration
      ? resolverTypeLabel || "corroboration"
      : isLatestPick
        ? resolverTypeLabel || "latest write resolver"
        : resolverTypeLabel || "direct";
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
      resultValue={isKnown ? fval.value : null}
      resultStatus={fval.status}
      entityType={entityType}
      fieldName={fieldName}
      onEditResolver={onEditResolver}
      onOpenDoc={onOpenDoc}
    />
  );
}

// Form body for editing the resolver of one (entity_type, field_name) pair.
// Loads the active policy on mount, lets the user pick a resolver type +
// type-specific fields, then POSTs to /resolvers/apply. Hosted by
// ResolverDrawer (drawer chrome supplies the title + close button).
// Resolver types that the editor exposes as user-selectable options. The
// backend also supports `raw` (return all candidates without picking) but
// that's a diagnostic affordance, not a stored policy choice — we don't
// surface it here. If a saved policy ever uses `raw` we render a migration
// banner instead of silently rewriting it (which would be a destructive
// regression on top of any state created via the API or older UI versions).
const USER_FACING_RESOLVER_TYPES = ["latest_write", "skill", "auto"];

function ResolverEditorForm({ entityType, fieldName, onApplied, onCancel }) {
  // `loadedResolver` is the resolver as it currently exists on the server —
  // null while loading, null when no policy is saved, otherwise the
  // backend-shape resolver object. We track it separately from the editor's
  // working state so a legacy type can be detected and preserved.
  const [loadedResolver, setLoadedResolver] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [resolverType, setResolverType] = useState("latest_write");
  const [prompt, setPrompt] = useState("");
  const [migrating, setMigrating] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoadedResolver(null);
    setLoaded(false);
    setResolverType("latest_write");
    setPrompt("");
    setMigrating(false);
    setError(null);
    K.api
      .getResolvers()
      .then((set) => {
        if (cancelled) return;
        const policy = (set.policies || []).find(
          (p) => p.entity_type === entityType && p.field_name === fieldName
        );
        const r = policy ? policy.resolver || {} : null;
        setLoadedResolver(r);
        if (r && USER_FACING_RESOLVER_TYPES.includes(r.type)) {
          setResolverType(r.type);
          if (r.type === "skill") setPrompt(r.prompt || "");
        }
        // Legacy types: leave editor state at defaults but DO NOT submit
        // until the user explicitly chooses to migrate. The render branch
        // below shows a banner with a "replace" button.
        setLoaded(true);
      })
      .catch(() => {
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [entityType, fieldName]);

  const isLegacy =
    loadedResolver && !USER_FACING_RESOLVER_TYPES.includes(loadedResolver.type);

  const apply = async () => {
    setApplying(true);
    setError(null);
    try {
      const resolver = { type: resolverType };
      if (resolverType === "skill") {
        if (!prompt.trim()) {
          setError("prompt is required for skill resolver");
          setApplying(false);
          return;
        }
        resolver.prompt = prompt.trim();
      }
      await K.api.applyResolvers(
        [{ entity_type: entityType, field_name: fieldName, resolver }],
        `update resolver for ${entityType}.${fieldName}`
      );
      onApplied?.();
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setApplying(false);
    }
  };

  if (!loaded) {
    return <div className="resolver-form resolver-form-loading">loading…</div>;
  }

  // Legacy resolver, not yet migrating: surface the existing policy and
  // require an explicit "replace" before showing the editor. This prevents
  // an inadvertent Apply from rewriting a non-user-facing type (e.g. `raw`)
  // to whatever the dropdown happens to be defaulted to.
  if (isLegacy && !migrating) {
    return (
      <div className="resolver-form">
        <div className="resolver-editor-legacy">
          <div className="resolver-editor-legacy-title">legacy resolver</div>
          <div className="resolver-editor-legacy-body">
            This field uses a <code>{loadedResolver.type}</code> resolver, which
            isn't editable from this form. Replacing it will overwrite the
            existing policy.
          </div>
          <pre className="resolver-editor-legacy-pre">
            {JSON.stringify(loadedResolver, null, 2)}
          </pre>
        </div>
        <div className="resolver-editor-actions">
          <button onClick={onCancel} className="secondary">
            cancel
          </button>
          <button onClick={() => setMigrating(true)} className="primary">
            replace…
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="resolver-form">
      {isLegacy && migrating && (
        <div className="resolver-editor-migrate-note">
          replacing legacy <code>{loadedResolver.type}</code> resolver
        </div>
      )}
      <label className="resolver-editor-row">
        <span className="resolver-editor-label">type</span>
        <select value={resolverType} onChange={(e) => setResolverType(e.target.value)}>
          <option value="latest_write">latest_write — newest wins</option>
          <option value="skill">skill — LLM picks per a domain prompt</option>
          <option value="auto">auto ✨ — Kentro AI picks the best strategy</option>
        </select>
      </label>
      {resolverType === "skill" && (
        <label className="resolver-editor-row resolver-editor-row-prompt">
          <span className="resolver-editor-label">prompt</span>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="written sources outweigh verbal"
            rows={2}
          />
        </label>
      )}
      {error && <div className="resolver-editor-error">{error}</div>}
      <div className="resolver-editor-actions">
        <button onClick={onCancel} className="secondary" disabled={applying}>
          cancel
        </button>
        <button onClick={apply} className="primary" disabled={applying}>
          {applying ? "applying…" : "apply"}
        </button>
      </div>
    </div>
  );
}

// Source drawer — sibling of ResolverDrawer in the same deep slot
// (right: 1160px). Opens when the user clicks a candidate card in the
// lineage flow; mutually exclusive with ResolverDrawer (only one of the
// two deep drawers is ever rendered at a time, decided in app.jsx). Kept
// here next to ResolverDrawer so both deep-drawer components live together.
window.K.SourceDrawer = function SourceDrawer({ open, documentId, onClose }) {
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

  // ESC closes this drawer first — capture phase + stopImmediatePropagation
  // so it beats the LineageDrawer behind it. The lineage handler already
  // bails when any `.drawer.drawer-deep.open` is present, so this is the
  // belt-and-suspenders companion to that DOM check.
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

  if (!documentId) return null;
  const meta = doc ? K.docMeta({ source_class: doc.source_class, label: doc.label }) : null;
  return (
    <React.Fragment>
      <aside
        className={K.cls("drawer drawer-deep drawer-deep-wide", open && "open")}
        aria-hidden={!open}
        role="dialog"
        aria-label="Source content"
      >
        <div className="drawer-head">
          <span className="title">
            {meta ? `${meta.icon} ${meta.typeLabel}` : "source"}
            {doc && doc.label && <span> · {K.docLabel(doc.label)}</span>}
          </span>
          <button onClick={onClose} aria-label="Close">esc</button>
        </div>
        <div className="drawer-body">
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

// Resolver drawer — third stacked left-of-right-rail panel (slot at
// right: 1160px), opening one slot deeper than LineageDrawer (which sits at
// right: 440px, immediately left of the permanent right rail). Kept narrower
// than the lineage drawer because the form is small. Click the RESOLVER chip
// inside LineageDrawer to open it; the lineage drawer remains visible behind
// it so the candidate flow stays in view while editing. Mutually exclusive
// with SourceDrawer (the sibling in the same slot).
window.K.ResolverDrawer = function ResolverDrawer({ open, target, onClose, onApplied }) {
  // ESC closes the resolver drawer FIRST, ahead of LineageDrawer/EntityOverlay
  // listeners — capture phase + stopImmediatePropagation, same shape as the
  // other left-stacked drawers in this app.
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

  if (!target) return null;
  return (
    <React.Fragment>
      <aside
        className={K.cls("drawer drawer-deep", open && "open")}
        aria-hidden={!open}
        role="dialog"
        aria-label={`Edit resolver for ${target.entity_type}.${target.field_name}`}
      >
        <div className="drawer-head">
          <span className="title">
            resolver · {target.entity_type}.{target.field_name}
          </span>
          <button onClick={onClose} aria-label="Close">esc</button>
        </div>
        <div className="drawer-body">
          <ResolverEditorForm
            key={`${target.entity_type}.${target.field_name}`}
            entityType={target.entity_type}
            fieldName={target.field_name}
            onApplied={onApplied}
            onCancel={onClose}
          />
        </div>
      </aside>
    </React.Fragment>
  );
};

// Result chip — the orange pill carrying the resolved value. Pill text is
// hard-clamped to a single line with ellipsis so a long scalar (e.g. a
// note subject) or an array preview can never blow out the column. When
// the value is array-typed OR a string longer than ~28 chars, the pill
// becomes clickable: click to open a popover that shows the full value
// (arrays → one row per item; long scalars → wrapped+scrollable text).
// Anchored below + right-aligned to the pill so it extends leftward into
// the drawer body rather than overflowing the right rail.
function ResultChip({ resultLabel, resultStatus, resultValue, innerRef }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  const isArray = Array.isArray(resultValue);
  const arrayItems = isArray
    ? resultValue.map((v) => (typeof v === "string" ? v : JSON.stringify(v)))
    : [];
  const longScalar =
    !isArray && typeof resultLabel === "string" && resultLabel.length > 28;
  const isExpandable = isArray || longScalar;

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      // Close just the popover; don't let lineage / drawer ESC fire too.
      e.stopImmediatePropagation();
      e.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  return (
    <div className="flow-h2-result-wrap" ref={wrapRef}>
      <div
        ref={innerRef}
        className={K.cls(
          "flow-h2-result",
          `status-${resultStatus}`,
          isExpandable && "is-clickable",
          open && "is-open"
        )}
        onClick={isExpandable ? () => setOpen((o) => !o) : undefined}
        role={isExpandable ? "button" : undefined}
        tabIndex={isExpandable ? 0 : undefined}
        title={isExpandable ? "click to expand" : undefined}
        onKeyDown={
          isExpandable
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOpen((o) => !o);
                }
              }
            : undefined
        }
      >
        <span className="flow-h2-result-value">
          {isArray ? arrayItems[0] || "[empty]" : resultLabel}
        </span>
        {isArray && arrayItems.length > 1 && (
          <span className="flow-h2-result-count">+{arrayItems.length - 1}</span>
        )}
      </div>
      {open && (
        <div
          className="flow-h2-result-popover"
          role="dialog"
          aria-label="Full resolved value"
        >
          <div className="flow-h2-result-popover-head">
            {isArray ? `${arrayItems.length} VALUES` : "VALUE"}
          </div>
          {isArray ? (
            <ul className="flow-h2-result-popover-list">
              {arrayItems.map((s, i) => (
                <li key={i} className="flow-h2-result-popover-item">
                  {s}
                </li>
              ))}
            </ul>
          ) : (
            <div className="flow-h2-result-popover-scalar">{resultLabel}</div>
          )}
        </div>
      )}
    </div>
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
  resultValue,
  resultStatus,
  entityType,
  fieldName,
  onEditResolver,
  onOpenDoc,
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
            const clickable = !!(docId && onOpenDoc);
            return (
              <div
                key={i}
                ref={(el) => (cardRefs.current[i] = el)}
                className={K.cls(
                  "flow-h2-cand",
                  winner && "is-winner",
                  clickable && "is-clickable"
                )}
                style={{
                  borderColor: color.stroke,
                  background: color.fill,
                }}
                onClick={clickable ? () => onOpenDoc(docId) : undefined}
                role={clickable ? "button" : undefined}
                tabIndex={clickable ? 0 : undefined}
                title={clickable ? `open ${subLabel}` : undefined}
                onKeyDown={
                  clickable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onOpenDoc(docId);
                        }
                      }
                    : undefined
                }
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
            className={K.cls(
              "flow-h2-resolver",
              isKnown ? "is-ok" : "is-warn",
              entityType && fieldName && onEditResolver && "is-clickable"
            )}
            onClick={() => {
              if (entityType && fieldName && onEditResolver) {
                onEditResolver({ entity_type: entityType, field_name: fieldName });
              }
            }}
            title={
              entityType && fieldName && onEditResolver
                ? `click to edit resolver for ${entityType}.${fieldName} — ${resolverName}`
                : resolverName
            }
          >
            <div className="flow-h2-resolver-title">RESOLVE</div>
          </div>
          <div className="flow-h2-resolver-name">{resolverName}</div>
          <div className="flow-h2-resolver-detail">{resolverDetail}</div>
          {reason && <div className="flow-h2-resolver-reason">{reason}</div>}
        </div>
      </div>

      <div className="flow-h2-col flow-h2-col-result">
        <div className="flow-h2-col-label">RESULT</div>
        <div className="flow-h2-col-body">
          <ResultChip
            innerRef={resultRef}
            resultLabel={resultLabel}
            resultValue={resultValue}
            resultStatus={resultStatus}
          />
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
            const chip = K.formatCandidateChip(cands[i].value);
            // Anchor labels just outside the card and let them grow rightward
            // toward the resolver pill. Truncate each line to fit the actual
            // available width on this row (card-right + 12 → pill-left − 8)
            // so the chip never bleeds through the resolver. 11px mono ≈
            // 6.5px per glyph, so floor(width / 6.5) is the char budget.
            const chipX = p.x1 + 12;
            const chipY = p.y1;
            const availableWidth = Math.max(40, p.x2 - p.x1 - 20);
            const maxChars = Math.max(4, Math.floor(availableWidth / 6.5));
            const fittedLines = chip.lines.map((ln) =>
              ln.length > maxChars ? ln.slice(0, Math.max(1, maxChars - 1)) + "…" : ln
            );
            const lineHeight = 13;
            const startY = chipY - ((fittedLines.length - 1) * lineHeight) / 2;
            return (
              <g key={i} opacity={opacity}>
                <title>{chip.full}</title>
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
                {fittedLines.map((ln, j) => (
                  <text
                    key={j}
                    x={chipX}
                    y={startY + j * lineHeight}
                    textAnchor="start"
                    dominantBaseline="middle"
                    fontFamily="var(--mono)"
                    fontSize="11"
                    fontWeight={j === 0 ? "700" : "500"}
                    fill={color.text}
                    stroke="#fff"
                    strokeWidth="3.5"
                    strokeLinejoin="round"
                    style={{ paintOrder: "stroke" }}
                  >
                    {ln}
                  </text>
                ))}
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
