/* global React */
// Extraction + Graph + Lineage components

const { useState, useEffect, useRef, useMemo } = React;

// ── Extraction Panel ────────────────────────────────────────────────────────
window.ExtractionPanel = function ExtractionPanel({ documents, activeDocId, extractionLog, onPickDoc, onAddDoc, pendingDoc }) {
  const streamRef = useRef(null);
  useEffect(() => {
    if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight;
  }, [extractionLog]);

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Ingestion pipeline</span>
        <span className="panel-sub">Events become memory</span>
        <span className="spacer" />
        <span className="panel-sub">{documents.length} {documents.length === 1 ? "event" : "events"}</span>
      </div>
      <div className="panel-body">
        <div className="add-doc">
          <button onClick={() => onAddDoc("email_jane_2026-04-17")} disabled={pendingDoc || documents.find((d) => d.id === "email_jane_2026-04-17")}>
            + drop ✉️ email from Jane Doe
          </button>
        </div>
        <div className="doc-list">
          {documents.map((d) => {
            const typeLabel = d.type === "transcript" ? "Call"
              : d.type === "email" ? "Email"
              : d.type === "ticket" ? "Ticket"
              : d.type;
            return (
              <div
                key={d.id}
                className={K.cls("doc-item", activeDocId === d.id && "active")}
                onClick={() => onPickDoc(d.id)}
              >
                <span className="doc-icon">{d.icon}</span>
                <span>
                  <div className="doc-name">{typeLabel} · {d.timestamp.split(" ")[0]}</div>
                  <div className="doc-meta">{d.label}</div>
                </span>
                <span className="doc-meta">{d.timestamp.includes(" ") ? d.timestamp.split(" ")[1] : ""}</span>
              </div>
            );
          })}
        </div>
        <div className="extraction-stream" ref={streamRef}>
          {extractionLog.length === 0 ? (
            <div style={{ color: "var(--ink-3)", fontSize: 11 }}>
              Pick a document to inspect its ingestion trace, or drop a new source to watch live extraction.
            </div>
          ) : (
            extractionLog.filter(Boolean).map((s, i) => (
              <div key={i} className={K.cls("ext-step", s && s.processing && "processing")}>
                <span className="ts">{s ? s.ts : ""}</span>
                <span className="msg" dangerouslySetInnerHTML={{ __html: (s && s.html) || "" }} />
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

// ── Reasoning-graph visualization ───────────────────────────────────────────
// Layout: documents on left, entities in middle, fields on right
window.GraphPanel = function GraphPanel({ documents, entities, agentScope, highlightField, flowPulse }) {
  const W = 700, H = 360;
  const docX = 70, entX = 340, fldX = 600;

  const visibleDocs = documents;
  const visibleEnts = Object.values(entities).filter((e) => {
    if (!agentScope) return true;
    const r = agentScope[e.type];
    return r && r.visible !== false;
  });

  // Position docs evenly
  const docPos = {};
  visibleDocs.forEach((d, i) => {
    docPos[d.id] = { x: docX, y: 50 + i * ((H - 80) / Math.max(1, visibleDocs.length - 1 || 1)) };
  });

  // Position entities
  const entPos = {};
  visibleEnts.forEach((e, i) => {
    entPos[`${e.type}:${e.key}`] = { x: entX, y: 50 + i * ((H - 80) / Math.max(1, visibleEnts.length - 1 || 1)) };
  });

  // Build field nodes (one per readable field)
  const fldNodes = [];
  visibleEnts.forEach((e) => {
    const r = agentScope?.[e.type];
    const readable = r?.read || ["*"];
    const all = readable.includes("*");
    Object.keys(e.fields).forEach((fname) => {
      if (!all && !readable.includes(fname)) return;
      if (e.fields[fname].values.length === 0) return;
      fldNodes.push({ id: `${e.type}:${e.key}:${fname}`, entId: `${e.type}:${e.key}`, name: fname, entity: e });
    });
  });
  const fldPos = {};
  fldNodes.forEach((f, i) => {
    fldPos[f.id] = { x: fldX, y: 30 + i * ((H - 60) / Math.max(1, fldNodes.length - 1 || 1)) };
  });

  // Edges
  const edges = [];
  // doc -> entity (via any extraction)
  visibleEnts.forEach((e) => {
    const ePos = entPos[`${e.type}:${e.key}`];
    const seen = new Set();
    Object.values(e.fields).forEach((f) => {
      f.values.forEach((v) => {
        const sources = (v.source || "").split(",");
        sources.forEach((sid) => {
          const s = sid.trim();
          if (!s || seen.has(s) || !docPos[s]) return;
          seen.add(s);
          edges.push({
            from: docPos[s], to: ePos, kind: "doc-ent",
            highlight: false,
          });
        });
      });
    });
  });
  // entity -> field
  fldNodes.forEach((f) => {
    edges.push({
      from: entPos[f.entId], to: fldPos[f.id], kind: "ent-fld",
      highlight: highlightField && highlightField.entId === f.entId && highlightField.field === f.name,
    });
  });

  // Build path string for an edge
  const edgePath = (from, to) => {
    const fx = from.x + 50, fy = from.y;
    const tx = to.x - 50, ty = to.y;
    const cx = (fx + tx) / 2;
    return `M ${fx} ${fy} C ${cx} ${fy}, ${cx} ${ty}, ${tx} ${ty}`;
  };

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Reasoning graph</span>
        <span className="panel-sub">{agentScope ? "what this agent can see" : "all memory"}</span>
        <span className="spacer" />
        <span className="panel-sub">{visibleDocs.length} sources · {visibleEnts.length} entities · {fldNodes.length} fields</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        <div className="graph-wrap">
          <svg className="graph-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
            <defs>
              <linearGradient id="entGrad" x1="0" x2="1">
                <stop offset="0%" stopColor="oklch(0.55 0.18 255)" />
                <stop offset="100%" stopColor="oklch(0.6 0.20 285)" />
              </linearGradient>
              <linearGradient id="warnGrad" x1="0" x2="1">
                <stop offset="0%" stopColor="oklch(0.7 0.18 55)" />
                <stop offset="100%" stopColor="oklch(0.72 0.22 25)" />
              </linearGradient>
              <linearGradient id="docGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="oklch(1 0 0)" />
                <stop offset="100%" stopColor="oklch(0.96 0.01 260)" />
              </linearGradient>
              <filter id="softShadow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur in="SourceAlpha" stdDeviation="2" />
                <feOffset dy="2" />
                <feComponentTransfer><feFuncA type="linear" slope="0.15" /></feComponentTransfer>
                <feMerge><feMergeNode /><feMergeNode in="SourceGraphic" /></feMerge>
              </filter>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="oklch(0.7 0.05 260)" />
              </marker>
              <marker id="arrow-hl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="oklch(0.68 0.18 55)" />
              </marker>
            </defs>

            {/* column headers */}
            <text x={docX} y={18} textAnchor="middle" fontFamily="Inter, sans-serif" fontWeight="700" fontSize="9" fill="var(--ink-3)" letterSpacing="1.5">SOURCES</text>
            <text x={entX} y={18} textAnchor="middle" fontFamily="Inter, sans-serif" fontWeight="700" fontSize="9" fill="var(--ink-3)" letterSpacing="1.5">ENTITIES</text>
            <text x={fldX} y={18} textAnchor="middle" fontFamily="Inter, sans-serif" fontWeight="700" fontSize="9" fill="var(--ink-3)" letterSpacing="1.5">FIELDS</text>

            {/* edges */}
            {edges.map((e, i) => {
              const isWarn = e.kind === "ent-fld" && (() => {
                const f = fldNodes.find((fn) => entPos[fn.entId] === e.from && fldPos[fn.id] === e.to);
                return f && f.entity.fields[f.name].values.length > 1;
              })();
              return (
                <g key={i}>
                  <path
                    id={`edge-${i}`}
                    d={edgePath(e.from, e.to)}
                    stroke={e.highlight ? "oklch(0.68 0.18 55)" : isWarn ? "oklch(0.7 0.18 55 / 0.5)" : "oklch(0.78 0.02 260)"}
                    strokeWidth={e.highlight ? 2 : 1.2}
                    fill="none"
                    strokeDasharray={isWarn && !e.highlight ? "3 3" : "none"}
                  />
                  {/* Animated flow particle */}
                  <circle r={e.highlight ? 3 : 2} className={isWarn || e.highlight ? "flow-particle-warn" : "flow-particle"}>
                    <animateMotion
                      dur={`${2 + (i % 3) * 0.5}s`}
                      repeatCount="indefinite"
                      begin={`${(i % 5) * 0.3}s`}
                      path={edgePath(e.from, e.to)}
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

            {/* doc nodes */}
            {visibleDocs.map((d, i) => {
              const p = docPos[d.id];
              return (
                <g key={d.id} transform={`translate(${p.x - 50}, ${p.y - 16})`} filter="url(#softShadow)">
                  <rect width="100" height="32" rx="6" fill="url(#docGrad)" stroke="var(--line)" />
                  <text x="10" y="14" fontFamily="Inter, sans-serif" fontWeight="600" fontSize="10" fill="var(--ink)">{d.icon} {d.type}</text>
                  <text x="10" y="25" fontFamily="JetBrains Mono, monospace" fontSize="8" fill="var(--ink-3)">{d.id.slice(0, 16)}…</text>
                </g>
              );
            })}

            {/* entity nodes */}
            {visibleEnts.map((e, i) => {
              const p = entPos[`${e.type}:${e.key}`];
              const isHLEnt = highlightField && highlightField.entId === `${e.type}:${e.key}`;
              return (
                <g key={`${e.type}:${e.key}`} transform={`translate(${p.x - 60}, ${p.y - 16})`}
                   className={K.cls("node-entity", isHLEnt && "node-highlight")}>
                  <rect width="120" height="32" rx="6" fill="url(#entGrad)" stroke="oklch(0.5 0.2 255)" />
                  <text x="12" y="14" fontFamily="Inter, sans-serif" fontWeight="700" fontSize="10" fill="white">{e.type}</text>
                  <text x="12" y="25" fontFamily="JetBrains Mono, monospace" fontSize="9" fill="oklch(1 0 0 / 0.85)">.{e.key}</text>
                </g>
              );
            })}

            {/* field nodes */}
            {fldNodes.map((f) => {
              const p = fldPos[f.id];
              const isHL = highlightField && highlightField.entId === f.entId && highlightField.field === f.name;
              const conflict = f.entity.fields[f.name].values.length > 1;
              return (
                <g key={f.id} transform={`translate(${p.x - 50}, ${p.y - 11})`}
                   className={K.cls(conflict && "node-conflict", isHL && "node-highlight")}
                   filter="url(#softShadow)">
                  <rect width="100" height="22" rx="5"
                    fill={isHL ? "var(--warn-soft)" : conflict ? "var(--warn-soft)" : "oklch(1 0 0 / 0.9)"}
                    stroke={isHL ? "var(--warn)" : conflict ? "var(--warn)" : "var(--line)"}
                    strokeWidth={isHL || conflict ? 1.5 : 1}
                  />
                  <text x="8" y="15" fontFamily="JetBrains Mono, monospace" fontSize="9" fontWeight={conflict ? "600" : "400"} fill="var(--ink)">
                    {f.name}{conflict ? " ⚠" : ""}
                  </text>
                </g>
              );
            })}
          </svg>
          <div className="graph-legend">
            <div className="row"><span className="swatch" style={{ background: "white", border: "1px solid var(--line)", borderRadius: 3 }}></span> source doc</div>
            <div className="row"><span className="swatch" style={{ background: "linear-gradient(135deg, var(--accent), var(--pop))", borderRadius: 3 }}></span> entity</div>
            <div className="row"><span className="swatch" style={{ background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 3 }}></span> conflict field</div>
            <div className="row" style={{ marginTop: 4, paddingTop: 4, borderTop: "1px dashed var(--line)" }}>
              <span className="swatch" style={{ background: "var(--accent)", borderRadius: "50%", width: 6, height: 6, boxShadow: "0 0 6px var(--accent-glow)" }}></span> data flow
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ── Lineage drawer ───────────────────────────────────────────────────────────
window.LineageDrawer = function LineageDrawer({ open, payload, onClose }) {
  if (!payload) return null;
  const { entityLabel, fieldName, raw, resolution, candidates, status } = payload;
  const sourceMeta = (sid) => {
    const d = window.KENTRO_DATA.documents.find((x) => x.id === sid);
    return d || { id: sid, icon: "📄", label: sid, timestamp: "" };
  };
  const winnerSourceId = resolution?.winnerSource;

  // Mini flow viz: candidates → resolver → resolved value (3-act narrative)
  // Stable per-source colors: source A = orange, source B = blue. Winner = full saturation, loser = dimmed.
  const MiniFlow = () => {
    const cands = candidates || [];
    if (cands.length === 0) return null;
    const W = 480, H = 130;
    const candX = 60, resX = 240, valX = 420;
    const candYs = cands.length === 1
      ? [H / 2]
      : cands.map((_, i) => 30 + i * (60 / (cands.length - 1)));
    const resY = H / 2;
    const path = (fx, fy, tx, ty) => {
      const cx = (fx + tx) / 2;
      return `M ${fx} ${fy} C ${cx} ${fy}, ${cx} ${ty}, ${tx} ${ty}`;
    };

    // Stable color per candidate index (orange / blue / purple, …)
    const HUES = [
      { full: "oklch(0.65 0.18 55)", dim: "oklch(0.85 0.06 55)", text: "oklch(0.4 0.15 55)" },   // orange — verbal/transcript
      { full: "oklch(0.6 0.18 250)", dim: "oklch(0.85 0.06 250)", text: "oklch(0.4 0.15 250)" }, // blue — written/email
      { full: "oklch(0.6 0.2 300)",  dim: "oklch(0.85 0.06 300)", text: "oklch(0.4 0.15 300)" }, // purple
    ];
    const colorFor = (i) => HUES[i % HUES.length];

    const winnerIdx = winnerSourceId ? cands.findIndex(c => c.source === winnerSourceId) : -1;
    const winner = winnerIdx >= 0 ? cands[winnerIdx] : cands[0];
    const winnerColor = colorFor(winnerIdx >= 0 ? winnerIdx : 0);

    const resolverLabel = resolution
      ? (resolution.label.includes("Written") || resolution.label.includes("written outweighs") ? "written > verbal" : "latest write wins")
      : "—";

    return (
      <div className="mini-flow">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
          {/* Act labels */}
          <text x={candX} y="14" textAnchor="middle" fontFamily="Inter, sans-serif" fontWeight="600" fontSize="8" letterSpacing="1.2" fill="oklch(0.55 0.02 260)">CANDIDATES</text>
          <text x={resX} y="14" textAnchor="middle" fontFamily="Inter, sans-serif" fontWeight="600" fontSize="8" letterSpacing="1.2" fill="oklch(0.55 0.02 260)">RESOLVER</text>
          <text x={valX} y="14" textAnchor="middle" fontFamily="Inter, sans-serif" fontWeight="600" fontSize="8" letterSpacing="1.2" fill="oklch(0.55 0.02 260)">RESULT</text>

          {/* edges: candidate → resolver (per-source color, dim if loser) */}
          {cands.map((c, i) => {
            const isWinner = i === winnerIdx;
            const col = colorFor(i);
            return (
              <g key={`e${i}`}>
                <path
                  d={path(candX + 32, candYs[i], resX - 26, resY)}
                  stroke={isWinner ? col.full : col.dim}
                  strokeWidth={isWinner ? 2.5 : 1.8}
                  fill="none"
                />
                <circle r="2.5" className="flow-particle" style={{ fill: isWinner ? col.full : col.dim }}>
                  <animateMotion dur={`${1.5 + i * 0.3}s`} repeatCount="indefinite" path={path(candX + 32, candYs[i], resX - 26, resY)} />
                  <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.15;0.85;1" dur={`${1.5 + i * 0.3}s`} repeatCount="indefinite" />
                </circle>
              </g>
            );
          })}

          {/* edge: resolver → result (winner's color, full saturation) */}
          <path d={path(resX + 26, resY, valX - 32, resY)} stroke={winnerColor.full} strokeWidth="2.5" fill="none" />
          <circle r="3" className="flow-particle" style={{ fill: winnerColor.full }}>
            <animateMotion dur="1.2s" repeatCount="indefinite" path={path(resX + 26, resY, valX - 32, resY)} />
          </circle>

          {/* candidate nodes */}
          {cands.map((c, i) => {
            const isWinner = i === winnerIdx;
            const col = colorFor(i);
            return (
              <g key={`n${i}`} transform={`translate(${candX - 32}, ${candYs[i] - 13})`}>
                <rect width="64" height="26" rx="5" fill={isWinner ? col.full : col.dim} stroke={col.full} strokeWidth={isWinner ? 1.5 : 1} />
                <text x="32" y="17" textAnchor="middle" fontFamily="JetBrains Mono, monospace" fontSize="11" fontWeight="700" fill={isWinner ? "white" : col.text}>
                  {c.value}
                </text>
              </g>
            );
          })}

          {/* resolver node */}
          <g transform={`translate(${resX - 26}, ${resY - 16})`}>
            <rect width="52" height="32" rx="7" fill="oklch(0.22 0.03 260)" stroke="oklch(0.4 0.05 260)" strokeWidth="1" />
            <text x="26" y="13" textAnchor="middle" fontFamily="Inter, sans-serif" fontWeight="700" fontSize="8" fill="white" letterSpacing="0.5">RESOLVE</text>
            <text x="26" y="24" textAnchor="middle" fontFamily="JetBrains Mono, monospace" fontSize="7" fill="oklch(0.85 0.02 260)">
              {resolution ? (resolution.label.includes("Written") || resolution.label.includes("written outweighs") ? "skill" : "latest") : "raw"}
            </text>
          </g>
          {/* resolver policy label below */}
          <text x={resX} y={resY + 30} textAnchor="middle" fontFamily="JetBrains Mono, monospace" fontSize="9" fill="oklch(0.45 0.02 260)" fontWeight="500">
            {resolverLabel}
          </text>

          {/* winner value (right) — colored by winning source */}
          <g transform={`translate(${valX - 32}, ${resY - 14})`} className="node-highlight">
            <rect width="64" height="28" rx="6" fill={winnerColor.full} stroke={winnerColor.full} strokeWidth="1.5" />
            <text x="32" y="18" textAnchor="middle" fontFamily="JetBrains Mono, monospace" fontSize="11" fontWeight="700" fill="white">
              {winner?.value || "—"}
            </text>
          </g>
        </svg>
      </div>
    );
  };

  return (
    <>
      <div className={K.cls("drawer-overlay", open && "open")} onClick={onClose} />
      <aside className={K.cls("drawer", open && "open")}>
        <div className="drawer-head">
          <span className="title">lineage · {entityLabel}.{fieldName}</span>
          <button onClick={onClose}>esc</button>
        </div>
        <div className="drawer-body">
          {candidates && candidates.length > 0 && <MiniFlow />}
          <div className="lineage-section">
            <h4>Field</h4>
            <div className="kv-list">
              <div className="kv"><span className="k">entity</span><span className="v">{entityLabel}</span></div>
              <div className="kv"><span className="k">field</span><span className="v">{fieldName}</span></div>
              <div className="kv"><span className="k">status</span><span className="v">{status}</span></div>
              <div className="kv"><span className="k">corroboration</span><span className="v">{candidates?.length || 0} source(s)</span></div>
            </div>
          </div>

          <div className="lineage-section">
            <h4>Sources</h4>
            {(candidates || []).map((c, i) => {
              const meta = sourceMeta((c.source || "").split(",")[0]);
              const isWinner = winnerSourceId && c.source === winnerSourceId;
              const isLoser = winnerSourceId && c.source !== winnerSourceId && candidates.length > 1;
              return (
                <div key={i} className={K.cls("source-row", isWinner && "winner", isLoser && "loser")}>
                  <span className="source-icon">{meta.icon}</span>
                  <span className="source-meta">
                    <span className="name">{meta.label || meta.id}</span>
                    <span className="ts">{c.ts} · {c.sourceClass || "—"} source · captured by ingestion</span>
                  </span>
                  <span className="source-value">{c.value}</span>
                </div>
              );
            })}
          </div>

          {resolution && (
            <div className="lineage-section">
              <h4>Resolution</h4>
              <div className="resolution">
                <div className="label">policy</div>
                {resolution.label}
              </div>
            </div>
          )}

          <div className="lineage-section">
            <h4>Active rules at write time</h4>
            <div className="rule-list-mini">
              {(payload.activeRules || []).map((r, i) => (
                <div key={i} className="rule">{r}</div>
              ))}
            </div>
          </div>
        </div>
      </aside>
    </>
  );
};

Object.assign(window, { ExtractionPanel: window.ExtractionPanel, GraphPanel: window.GraphPanel, LineageDrawer: window.LineageDrawer });
