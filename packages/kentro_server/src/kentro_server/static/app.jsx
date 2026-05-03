/* global React */
const { useState, useEffect, useRef, useMemo, useReducer, useCallback } = React;

// ── Agent panel ─────────────────────────────────────────────────────────────
function AgentPanel({ agent, agentRules, resolver, entities, query, onQuery, onFieldClick, lastWriteResult, onAttemptWrite }) {
  const targetType = query.type;
  const targetKey = query.key;
  const entityId = `${targetType}:${targetKey}`;
  const entity = entities[entityId];
  const view = entity ? K.viewEntity(entity, agentRules, resolver) : { hidden: true, reason: "entity not found" };

  return (
    <div className="panel">
      <div className="panel-head">
        <span className={K.cls("agent-tag", agent.id)}>
          <span className="swatch"></span>{agent.label}
        </span>
        <span className="panel-sub">{agent.description}</span>
      </div>
      <div className="panel-body">
        <div className="query-row">
          <select value={query.type} onChange={(e) => onQuery({ ...query, type: e.target.value, key: e.target.value === "Customer" ? "Acme" : e.target.value === "Deal" ? "acme-renewal-2026" : "acme" })}>
            <option>Customer</option>
            <option>Deal</option>
            <option>AuditLog</option>
          </select>
          <input value={query.key} onChange={(e) => onQuery({ ...query, key: e.target.value })} />
          <button onClick={() => onQuery({ ...query })}>read</button>
        </div>

        {lastWriteResult && lastWriteResult.agent === agent.id && (
          <div className={K.cls("banner", lastWriteResult.allowed ? "ok" : "deny")}>
            <span className="banner-icon">{lastWriteResult.allowed ? "✓" : "⊘"}</span>
            <span>{lastWriteResult.message}</span>
          </div>
        )}

        {view.hidden ? (
          <div className="invisible-entity">
            ⊘ {targetType}.{targetKey} — {view.reason}
          </div>
        ) : (
          <div className="record">
            <div className="record-head">
              <span className="key">{targetType}.{targetKey}</span>
              <span className="type">{targetType}</span>
            </div>
            {(window.KENTRO_DATA.fieldOrder[targetType] || Object.keys(view.fields)).map((fname) => {
              const f = view.fields[fname];
              if (!f) return null;
              const status = f.status || "UNKNOWN";
              const cls = `status-${status.toLowerCase()}`;
              const valDisplay = (() => {
                if (status === "HIDDEN") return "⊘ redacted by ACL";
                if (status === "UNRESOLVED") return f.candidates.map((c) => c.value).join(" ⇄ ");
                if (status === "UNKNOWN") return "—";
                return f.value?.value;
              })();
              return (
                <div
                  key={fname}
                  className={K.cls("field", status === "HIDDEN" && "hidden", status === "UNRESOLVED" && "unresolved")}
                  onClick={() => onFieldClick(targetType, targetKey, fname, f)}
                >
                  <span className="field-name">{fname}</span>
                  <span className="field-value">{valDisplay}</span>
                  <span className={K.cls("field-status", cls)}>{status}</span>
                </div>
              );
            })}
          </div>
        )}

        {/* Quick actions per agent */}
        <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {agent.id === "sales" && (
            <button className="ghost-btn" onClick={() => onAttemptWrite("sales", "Customer", "Acme", "sales_notes", "follow up Mon")}>
              + write sales_notes
            </button>
          )}
          {agent.id === "cs" && (
            <>
              <button className="ghost-btn" onClick={() => onAttemptWrite("cs", "Customer", "Acme", "deal_size", "$275K")}>
                attempt write deal_size
              </button>
              <button className="ghost-btn" onClick={() => onAttemptWrite("cs", "Customer", "Acme", "support_tickets", "#170 (refund)")}>
                + add support_ticket
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Access matrix ───────────────────────────────────────────────────────────
function AccessMatrix({ rules, changedCells }) {
  const cellFor = (agent, type) => {
    const r = rules[agent]?.[type];
    if (!r || r.visible === false) {
      return { invisible: true };
    }
    return {
      read: r.read || [],
      write: r.write || [],
      writeApproval: r.writeRequiresApproval,
      create: r.create,
    };
  };
  const renderCell = (cell, key) => {
    if (cell.invisible) return <td key={key} className="invisible">invisible</td>;
    const isChanged = changedCells.includes(key);
    return (
      <td key={key} className={K.cls("cell", isChanged && "changed")}>
        <div className="perm-line">
          <span className="perm-tag r">R</span>
          <span>{cell.read.includes("*") ? "all fields" : cell.read.length === 0 ? "—" : cell.read.join(", ")}</span>
        </div>
        <div className="perm-line">
          <span className="perm-tag w">W</span>
          <span>
            {cell.write.includes("*") ? "all fields" : cell.write.length === 0 ? "—" : cell.write.join(", ")}
            {cell.writeApproval && " (approval req)"}
            {cell.create && ", +create"}
          </span>
        </div>
      </td>
    );
  };

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Access matrix</span>
        <span className="panel-sub">4 governance dimensions</span>
      </div>
      <div className="panel-body">
        <table className="matrix">
          <thead>
            <tr><th></th><th>Customer</th><th>Deal</th><th>AuditLog</th></tr>
          </thead>
          <tbody>
            <tr>
              <td className="row-head">Sales</td>
              {renderCell(cellFor("sales", "Customer"), "sales:Customer")}
              {renderCell(cellFor("sales", "Deal"), "sales:Deal")}
              {renderCell(cellFor("sales", "AuditLog"), "sales:AuditLog")}
            </tr>
            <tr>
              <td className="row-head">Customer Service</td>
              {renderCell(cellFor("cs", "Customer"), "cs:Customer")}
              {renderCell(cellFor("cs", "Deal"), "cs:Deal")}
              {renderCell(cellFor("cs", "AuditLog"), "cs:AuditLog")}
            </tr>
          </tbody>
        </table>
        <div style={{ marginTop: 10, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-3)" }}>
          Conflict policy: <strong style={{ color: "var(--ink)" }}>{rules.conflictResolver?.kind === "SkillResolver" ? "written outweighs verbal" : "latest write wins"}</strong>
          {rules.conflictResolver?.policy && <span> — {rules.conflictResolver.policy}</span>}
        </div>
      </div>
    </div>
  );
}

// ── Policy editor ───────────────────────────────────────────────────────────
// All edit recipes used by suggestion chips and free-form parsing.
const EDIT_RECIPES = {
  hide_deal_size: {
    id: "e_hide_deal_size",
    op: "modify",
    targetId: "p_cs_customer_read",
    summary: "Customer Service: hide deal_size",
    diff: "remove `deal_size` from cs.Customer.read",
    rego: `package kentro.access\n\nallow {\n  input.role == "cs"\n  input.action == "read"\n  input.resource.type == "Customer"\n  input.resource.field == allowed[_]\n}\n\nallowed := ["name", "contact", "support_tickets"]\n# deal_size removed`,
    engine: { type: "set_read", role: "cs", resourceType: "Customer", fields: ["name", "contact", "support_tickets"] },
  },
  show_audit: {
    id: "e_show_audit",
    op: "modify",
    targetId: "p_sales_auditlog_hidden",
    summary: "Sales: gain read access to AuditLog",
    diff: "deny → allow on sales.AuditLog.read",
    rego: `package kentro.access\n\nallow {\n  input.role == "sales"\n  input.action == "read"\n  input.resource.type == "AuditLog"\n}`,
    engine: { type: "set_visibility", role: "sales", resourceType: "AuditLog", visible: true },
  },
  cs_approval: {
    id: "e_cs_approval",
    op: "add",
    summary: "Customer Service: support_ticket writes require manager approval",
    diff: "new condition policy",
    rego: `package kentro.condition\n\nrequire_approval {\n  input.role == "cs"\n  input.action == "write"\n  input.resource.type == "Customer"\n  input.resource.field == "support_tickets"\n}`,
    engine: { type: "set_write_approval", role: "cs", resourceType: "Customer", required: true },
  },
  conflict_skill: {
    id: "e_conflict_skill",
    op: "modify",
    targetId: "p_conflict_latest",
    summary: "Conflict policy: written outweighs verbal",
    diff: "LatestWriteResolver → SkillResolver(written > verbal)",
    rego: `package kentro.resolve\n\nresolved[field] = winner {\n  candidates := input.field.values\n  written := [c | c := candidates[_]; c.sourceClass == "written"]\n  count(written) > 0\n  winner := latest(written)\n}\n\nresolved[field] = winner {\n  candidates := input.field.values\n  written := [c | c := candidates[_]; c.sourceClass == "written"]\n  count(written) == 0\n  winner := latest(candidates)\n}`,
    engine: { type: "set_resolver", kind: "SkillResolver", policy: "written outweighs verbal, latest among written wins" },
  },
  conflict_latest: {
    id: "e_conflict_latest",
    op: "modify",
    targetId: "p_conflict_latest",
    summary: "Conflict policy: latest write wins",
    diff: "→ LatestWriteResolver",
    rego: `package kentro.resolve\n\nresolved[field] = winner {\n  candidates := input.field.values\n  winner := candidates[_]\n  not exists_newer(winner, candidates)\n}`,
    engine: { type: "set_resolver", kind: "LatestWriteResolver" },
  },
};

// Suggestion chips for Access & Conditions section
const ACCESS_SUGGESTIONS = [
  {
    label: "Rewrite all access rules",
    kind: "rewrite",
    text: "Hide deal_size from Customer Service. Make AuditLog visible to Sales. Require manager approval before Customer Service writes a support ticket.",
    edits: ["hide_deal_size", "show_audit", "cs_approval"],
  },
  {
    label: "Hide deal_size from CS",
    kind: "edit",
    text: "Hide deal_size from Customer Service.",
    edits: ["hide_deal_size"],
  },
  {
    label: "Make AuditLog visible to Sales",
    kind: "edit",
    text: "Make AuditLog visible to Sales.",
    edits: ["show_audit"],
  },
];

// Suggestion chips for Conflict policy section
const CONFLICT_SUGGESTIONS = [
  {
    label: "Prefer written over verbal",
    kind: "edit",
    text: "When deal_size has multiple values, prefer written sources (email) over verbal sources (calls).",
    edits: ["conflict_skill"],
  },
  {
    label: "Latest write wins",
    kind: "edit",
    text: "On conflicting values, the latest write always wins.",
    edits: ["conflict_latest"],
  },
];

// Sub-editor used for both Access&Conditions and Conflict sections
function PolicySubEditor({ policies, suggestions, onApply, hoverEditId, setHoverEditId, placeholder, sectionId }) {
  const [text, setText] = useState("");
  const [edits, setEdits] = useState(null);
  const [parsing, setParsing] = useState(false);
  const [expanded, setExpanded] = useState({});

  const onParse = async () => {
    if (!text.trim()) return;
    setParsing(true);
    setEdits(null);
    await new Promise((r) => setTimeout(r, 700));

    // Recipe matching for known suggestions
    const lower = text.toLowerCase();
    const matched = [];
    if (sectionId === "access") {
      if (/hide.*deal_size|deal_size.*hide|redact.*deal_size/.test(lower)) matched.push(EDIT_RECIPES.hide_deal_size);
      if (/auditlog.*sales|sales.*auditlog|make auditlog visible/.test(lower)) matched.push(EDIT_RECIPES.show_audit);
      if (/approval|manager.*approve|require.*approval/.test(lower)) matched.push(EDIT_RECIPES.cs_approval);
    } else if (sectionId === "conflict") {
      if (/written.*verbal|prefer written|email.*outweigh/.test(lower)) matched.push(EDIT_RECIPES.conflict_skill);
      else if (/latest write|latest wins|most recent/.test(lower)) matched.push(EDIT_RECIPES.conflict_latest);
    }

    if (matched.length > 0) {
      setEdits(matched);
      setParsing(false);
      return;
    }

    // Fallback to claude.complete for novel prompts
    try {
      const out = await window.claude.complete(
        `Parse this ${sectionId} policy change request into 1-3 short structured edits. Available policies: ${JSON.stringify(policies.map(p => ({ id: p.id, summary: p.summary })))}. Return JSON array, each: {op:"add"|"modify", targetId?, summary, diff}. Request: ${text}`
      );
      const m = out.match(/\[[\s\S]*\]/);
      const arr = m ? JSON.parse(m[0]) : [];
      const norm = arr.slice(0, 3).map((x, i) => ({
        id: `g${sectionId}${i}`,
        op: x.op || "modify",
        targetId: x.targetId,
        summary: x.summary || "policy change",
        diff: x.diff || "—",
        rego: `# parsed from prompt\n# ${x.summary}`,
        engine: null,
      }));
      setEdits(norm.length ? norm : []);
    } catch {
      setEdits([]);
    }
    setParsing(false);
  };

  const apply = () => {
    if (!edits || edits.length === 0) return;
    onApply(edits);
    setEdits(null);
    setText("");
  };

  const onPickSuggestion = (s) => {
    setText(s.text);
    setEdits(null);
  };

  return (
    <div className="policy-section">
      <div className="policy-list">
        {policies.map((p) => {
          const isExpanded = expanded[p.id];
          const incomingEdit = (edits || []).find(e => e.op === "modify" && e.targetId === p.id);
          const isTouched = !!incomingEdit;
          const isHovered = hoverEditId && incomingEdit && hoverEditId === incomingEdit.id;
          return (
            <div
              key={p.id}
              className={K.cls("policy-row", `kind-${p.kind}`, isTouched && "touched", isHovered && "highlighted")}
              onClick={() => setExpanded({ ...expanded, [p.id]: !isExpanded })}
            >
              <div className="policy-row-main">
                <span className={K.cls("policy-kind", `kind-${p.kind}`)}>{p.kind}</span>
                <span className="policy-summary">{p.summary}</span>
                {isTouched && <span className="policy-pending" title={incomingEdit.diff}>· change pending</span>}
                <span className="policy-toggle">{isExpanded ? "▾" : "▸"}</span>
              </div>
              {isExpanded && (
                <pre className="policy-rego">{p.rego}</pre>
              )}
            </div>
          );
        })}
      </div>

      <div className="suggestion-row">
        <span className="suggestion-label">Try:</span>
        {suggestions.map((s, i) => (
          <button
            key={i}
            className={K.cls("suggestion-chip", `kind-${s.kind}`)}
            onClick={() => onPickSuggestion(s)}
            title={s.text}
          >
            <span className="chip-kind">{s.kind === "rewrite" ? "rewrite" : "edit"}</span>
            {s.label}
          </button>
        ))}
      </div>

      <div className="chat-box">
        <textarea
          className="chat-input"
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
        />
        <div className="chat-actions">
          <span className={K.cls("parse-status", edits && "parsed")}>
            {parsing ? "reading your request…"
              : edits && edits.length > 0 ? `${edits.length} ${edits.length === 1 ? "change" : "changes"} ready — review, then apply`
              : edits ? "couldn't parse — try a suggestion above"
              : "describe a change in plain English, or pick a suggestion"}
          </span>
          <button className="primary" onClick={onParse} disabled={parsing || !text.trim()}>parse</button>
        </div>
      </div>

      {edits && edits.length > 0 && (
        <div className="edit-preview">
          <div className="edit-preview-head">Pending changes</div>
          {edits.map((e) => (
            <div
              key={e.id}
              className={K.cls("edit-row", `op-${e.op}`)}
              onMouseEnter={() => setHoverEditId(e.id)}
              onMouseLeave={() => setHoverEditId(null)}
            >
              <span className={K.cls("edit-op", `op-${e.op}`)}>{e.op}</span>
              <span className="edit-summary">
                {e.summary}
                <span className="edit-diff">{e.diff}</span>
              </span>
            </div>
          ))}
          <div className="apply-row inline">
            <button className="secondary" onClick={() => { setEdits(null); setText(""); }}>cancel</button>
            <button onClick={apply}>apply changes</button>
          </div>
        </div>
      )}
    </div>
  );
}

function PolicyEditor({ policies, onApply, propagationLog, ruleVersion }) {
  const [hoverEditId, setHoverEditId] = useState(null);

  const accessPolicies = policies.filter(p => p.kind !== "conflict");
  const conflictPolicies = policies.filter(p => p.kind === "conflict");

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">Policies</span>
        <span className="panel-sub">PBAC · Rego</span>
        <span className="spacer" />
        <span className="rule-chip" title="Active policy version — increments each time policies are applied"><span className="dot"></span>Policies · version {ruleVersion}</span>
      </div>
      <div className="panel-body">
        <div className="rule-editor">
          <div className="policy-section-head">Access & condition policies</div>
          <PolicySubEditor
            policies={accessPolicies}
            suggestions={ACCESS_SUGGESTIONS}
            onApply={onApply}
            hoverEditId={hoverEditId}
            setHoverEditId={setHoverEditId}
            placeholder="Modify access rules… e.g. Hide deal_size from Customer Service."
            sectionId="access"
          />

          <div className="policy-section-head">Conflict policy</div>
          <PolicySubEditor
            policies={conflictPolicies}
            suggestions={CONFLICT_SUGGESTIONS}
            onApply={onApply}
            hoverEditId={hoverEditId}
            setHoverEditId={setHoverEditId}
            placeholder="Modify conflict resolution… e.g. Prefer written over verbal."
            sectionId="conflict"
          />

          {propagationLog.length > 0 && (
            <div className="propagation-log">
              {propagationLog.map((l, i) => (
                <div key={i} className="row">
                  <span className="ts">{l.ts}</span>
                  <span>{l.msg}</span>
                  {l.ok && <span className="ok">✓</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── App root ────────────────────────────────────────────────────────────────
function App() {
  const D = window.KENTRO_DATA;
  const [scene, setScene] = useState(1);
  const [rules, setRules] = useState(K.clone(D.initialRules));
  const [policies, setPolicies] = useState(K.clone(D.initialPolicies));
  const [entities, setEntities] = useState(K.clone(D.entities));
  const entitiesRef = useRef(entities);
  useEffect(() => { entitiesRef.current = entities; }, [entities]);
  const [documents, setDocuments] = useState(D.documents.filter((d) => d.addedAt === "scene1"));
  const [activeDocId, setActiveDocId] = useState("acme_call_2026-04-15");
  const [extractionLog, setExtractionLog] = useState([]);
  const [salesQuery, setSalesQuery] = useState({ type: "Customer", key: "Acme" });
  const [csQuery, setCsQuery] = useState({ type: "Customer", key: "Acme" });
  const [drawerPayload, setDrawerPayload] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [lastWriteResult, setLastWriteResult] = useState(null);
  const [propagationLog, setPropagationLog] = useState([]);
  const [ruleVersion, setRuleVersion] = useState(1);
  const [changedCells, setChangedCells] = useState([]);
  const [pendingDoc, setPendingDoc] = useState(false);
  const [tweaks, setTweaks] = useState({ captions: false, graphAgentScope: "all", density: "comfortable" });
  const [showVO, setShowVO] = useState(false);

  // Show extraction log for the active doc
  useEffect(() => {
    const d = documents.find((x) => x.id === activeDocId);
    if (!d || !d.extracts) { setExtractionLog([]); return; }
    setExtractionLog([]);
    const steps = [
      { html: `<span class="msg">read <span class="val">${d.label}</span> · ${d.timestamp}</span>` },
      { html: `<span class="msg">reading the document…</span>` },
      ...d.extracts.map((e) => ({
        html: `<span class="msg">extracted <span class="ent">${e.entity}.${e.key}</span>.<span class="field">${e.field}</span> = <span class="val">${e.value}</span>${e.note ? ` <em style="color:var(--ink-3)">(${e.note})</em>` : ""}</span>`,
      })),
      { html: `<span class="msg">saved ${d.extracts.length} facts with lineage back to the source</span>` },
    ];
    let i = 0;
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      if (i >= steps.length) return;
      const step = steps[i];
      if (step && step.html) {
        const ts = `+${(i * 80).toString().padStart(3, "0")}ms`;
        setExtractionLog((prev) => [...prev, { ts, html: step.html }]);
      }
      i++;
      if (i < steps.length) setTimeout(tick, 90);
    };
    tick();
    return () => { cancelled = true; };
  }, [activeDocId, documents.length]);

  // Scene controller
  const goToScene = (n) => {
    setScene(n);
    setShowVO(true);
    setTimeout(() => setShowVO(false), 7000);

    if (n === 1) {
      setRules(K.clone(D.initialRules));
      setPolicies(K.clone(D.initialPolicies));
      setEntities(K.clone(D.entities));
      setDocuments(D.documents.filter((d) => d.addedAt === "scene1"));
      setActiveDocId("acme_call_2026-04-15");
      setLastWriteResult(null);
      setPropagationLog([]);
      setRuleVersion(1);
      setChangedCells([]);
    }
    if (n === 2) {
      addEmailDoc();
    }
    if (n === 3) {
      // ensure doc is present
      if (!documents.find((d) => d.id === "email_jane_2026-04-17")) addEmailDoc();
    }
    if (n === 4) {
      // open lineage on resolved deal_size
      if (!documents.find((d) => d.id === "email_jane_2026-04-17")) addEmailDoc();
      setTimeout(() => {
        openLineage("Customer", "Acme", "deal_size");
      }, 900);
    }
  };

  const addEmailDoc = () => {
    if (documents.find((d) => d.id === "email_jane_2026-04-17")) return;
    setPendingDoc(true);
    const email = D.documents.find((d) => d.id === "email_jane_2026-04-17");
    setDocuments((prev) => [...prev, email]);
    setActiveDocId("email_jane_2026-04-17");
    // Mutate entities: add $300K candidate to deal_size + Deal.size
    setEntities((prev) => {
      const next = K.clone(prev);
      next["Customer:Acme"].fields.deal_size.values.push({
        value: "$300K", source: "email_jane_2026-04-17", agent: "ingestion_agent", ts: "2026-04-17 14:08", sourceClass: "written",
      });
      next["Deal:acme-renewal-2026"].fields.size.values.push({
        value: "$300K", source: "email_jane_2026-04-17", agent: "ingestion_agent", ts: "2026-04-17 14:08", sourceClass: "written",
      });
      return next;
    });
    // also tag the existing transcript value with sourceClass
    setEntities((prev) => {
      const next = K.clone(prev);
      next["Customer:Acme"].fields.deal_size.values[0].sourceClass = "verbal";
      next["Deal:acme-renewal-2026"].fields.size.values[0].sourceClass = "verbal";
      return next;
    });
    setTimeout(() => setPendingDoc(false), 800);
  };

  const onApplyEdits = (edits) => {
    // 1. Mutate the underlying engine rules per edit
    setRules((prev) => {
      const next = K.clone(prev);
      edits.forEach((e) => {
        const en = e.engine;
        if (!en) return;
        switch (en.type) {
          case "set_read":
            if (next[en.role]?.[en.resourceType]) next[en.role][en.resourceType].read = en.fields;
            break;
          case "set_visibility":
            if (next[en.role]?.[en.resourceType]) {
              next[en.role][en.resourceType] = en.visible
                ? { read: ["*"], write: [], visible: true }
                : { read: [], write: [], visible: false };
            }
            break;
          case "set_write_approval":
            if (next[en.role]?.[en.resourceType]) next[en.role][en.resourceType].writeRequiresApproval = en.required;
            break;
          case "set_resolver":
            next.conflictResolver = { kind: en.kind, policy: en.policy };
            break;
        }
      });
      return next;
    });

    // 2. Update the visible policies list
    setPolicies((prev) => {
      let next = prev.slice();
      edits.forEach((e) => {
        if (e.op === "modify" && e.targetId) {
          const i = next.findIndex(p => p.id === e.targetId);
          if (i >= 0) {
            next[i] = { ...next[i], summary: e.summary, rego: e.rego, engine: e.engine || next[i].engine };
          }
        } else if (e.op === "add") {
          next.push({
            id: `p_${Date.now()}_${Math.random().toString(36).slice(2,6)}`,
            kind: e.summary.toLowerCase().includes("approval") ? "condition" : "access",
            summary: e.summary,
            rego: e.rego,
            engine: e.engine,
          });
        }
      });
      return next;
    });

    setRuleVersion((v) => v + 1);
    setChangedCells(["cs:Customer", "sales:AuditLog"]);
    setTimeout(() => setChangedCells([]), 2400);

    // 3. Animate propagation log
    const summaries = edits.map(e => e.summary);
    const logs = [
      { ts: "0ms", msg: `Parsed ${edits.length} policy ${edits.length === 1 ? "change" : "changes"}`, ok: true },
      ...summaries.map((s, i) => ({ ts: `${12 + i * 11}ms`, msg: s, ok: true })),
      { ts: `${20 + summaries.length * 11}ms`, msg: `0 records re-ingested · 0 documents re-processed · policies version ${ruleVersion + 1} active`, ok: true },
    ];
    setPropagationLog([]);
    logs.forEach((l, i) => setTimeout(() => setPropagationLog((prev) => [...prev, l]), 100 + i * 110));
  };

  const onApplyRules = onApplyEdits; // legacy alias if anything still references it

  const onAttemptWrite = (agentId, type, key, field, value) => {
    const ar = rules[agentId];
    const check = K.canWrite(ar, type, field);
    if (!check.allowed) {
      setLastWriteResult({ agent: agentId, allowed: false, message: check.reason });
      return;
    }
    setEntities((prev) => {
      const next = K.clone(prev);
      const id = `${type}:${key}`;
      if (!next[id]) return prev;
      next[id].fields[field] = next[id].fields[field] || { values: [] };
      next[id].fields[field].values.push({
        value, source: `${agentId}_write`, agent: agentId, ts: new Date().toISOString().slice(0, 16).replace("T", " "),
        sourceClass: "written",
      });
      return next;
    });
    setLastWriteResult({ agent: agentId, allowed: true, message: `WriteResult.APPLIED → ${type}.${key}.${field} = ${value}` });
  };

  const openLineage = (type, key, fieldName) => {
    const id = `${type}:${key}`;
    const ent = entitiesRef.current[id];
    if (!ent || !ent.fields[fieldName]) return;
    const resolved = K.resolveField(ent.fields[fieldName], rules.conflictResolver, rules);
    let resolution = null;
    if (resolved.candidates.length > 1 && resolved.value) {
      resolution = {
        label: rules.conflictResolver.kind === "SkillResolver"
          ? "Written outweighs verbal — the email outranks the transcript because written sources rank above verbal ones."
          : "Latest write wins — the most recent value is canonical.",
        winnerSource: resolved.value.source,
      };
    }
    setDrawerPayload({
      entityLabel: `${type}.${key}`,
      fieldName,
      raw: ent.fields[fieldName],
      candidates: resolved.candidates,
      status: resolved.status,
      resolution,
      activeRules: [
        `Sales can read: ${(rules.sales[type]?.read || []).join(", ") || "—"}`,
        `Customer Service can read: ${(rules.cs[type]?.read || []).join(", ") || "—"}`,
        `Conflict policy: ${rules.conflictResolver.kind === "SkillResolver" ? "written outweighs verbal" : "latest write wins"}`,
        `Rules version: ${ruleVersion}`,
      ],
    });
    setDrawerOpen(true);
  };

  const onFieldClick = (type, key, fname, f) => {
    if (f.status === "HIDDEN") return;
    openLineage(type, key, fname);
  };

  const SCENES = [
    { n: 1, label: "01 · steady state" },
    { n: 2, label: "02 · conflict drops" },
    { n: 3, label: "03 · rule change" },
    { n: 4, label: "04 · lineage" },
  ];

  const agentScope = tweaks.graphAgentScope === "sales" ? rules.sales
    : tweaks.graphAgentScope === "cs" ? rules.cs
    : null;

  return (
    <div className="app" data-screen-label="Kentro Demo">
      <div className="topbar">
        <span className="brand"><span className="brand-mark"></span>KENTRO</span>
        <span className="tenant-chip">ali@kentro.demo</span>
        <div className="scene-stepper">
          {SCENES.map((s) => (
            <button key={s.n} className={K.cls(scene === s.n && "active")} onClick={() => goToScene(s.n)}>
              {s.label}
            </button>
          ))}
        </div>
        <span className="spacer" />
        <span className="rule-chip" title="Active rule set version — increments each time rules are applied"><span className="dot"></span>Rules · version {ruleVersion}</span>
        <span className="rule-chip" title="How conflicting values are resolved at read time">Conflict policy: {rules.conflictResolver.kind === "SkillResolver" ? "written outweighs verbal" : "latest write wins"}</span>
      </div>

      <div className="main">
        <AgentPanel
          agent={{ id: "sales", label: "Sales agent", description: "reads & writes deal info" }}
          agentRules={rules.sales}
          resolver={rules.conflictResolver}
          entities={entities}
          query={salesQuery}
          onQuery={setSalesQuery}
          onFieldClick={onFieldClick}
          lastWriteResult={lastWriteResult}
          onAttemptWrite={onAttemptWrite}
        />
        <AgentPanel
          agent={{ id: "cs", label: "Customer Service agent", description: "handles support tickets" }}
          agentRules={rules.cs}
          resolver={rules.conflictResolver}
          entities={entities}
          query={csQuery}
          onQuery={setCsQuery}
          onFieldClick={onFieldClick}
          lastWriteResult={lastWriteResult}
          onAttemptWrite={onAttemptWrite}
        />
        <PolicyEditor
          policies={policies}
          onApply={onApplyEdits}
          propagationLog={propagationLog}
          ruleVersion={ruleVersion}
        />

        <ExtractionPanel
          documents={documents}
          activeDocId={activeDocId}
          extractionLog={extractionLog}
          onPickDoc={setActiveDocId}
          onAddDoc={addEmailDoc}
          pendingDoc={pendingDoc}
        />
        <GraphPanel
          documents={documents}
          entities={entities}
          agentScope={agentScope}
          highlightField={drawerPayload ? { entId: `${drawerPayload.entityLabel.split(".")[0]}:${drawerPayload.entityLabel.split(".").slice(1).join(".")}`, field: drawerPayload.fieldName } : null}
        />
        <AccessMatrix rules={rules} changedCells={changedCells} />
      </div>

      <LineageDrawer open={drawerOpen} payload={drawerPayload} onClose={() => setDrawerOpen(false)} />

      {tweaks.captions && showVO && (
        <div className="vo-bar">
          <div className="label">scene {scene} · voice-over</div>
          {K.VO[`scene${scene}`]}
        </div>
      )}

      <KentroTweaks tweaks={tweaks} setTweaks={setTweaks} goToScene={goToScene} />
    </div>
  );
}

// ── Tweaks panel ────────────────────────────────────────────────────────────
function KentroTweaks({ tweaks, setTweaks, goToScene }) {
  const TP = window.TweaksPanel, TS = window.TweakSection, TT = window.TweakToggle, TR = window.TweakRadio, TB = window.TweakButton;
  if (!TP) return null;
  return (
    <TP title="Tweaks" defaults={tweaks} onChange={setTweaks}>
      <TS title="Scene">
        <TR id="_scene" label="Jump to scene" options={[
          { value: 1, label: "1 · steady" },
          { value: 2, label: "2 · conflict" },
          { value: 3, label: "3 · rule" },
          { value: 4, label: "4 · lineage" },
        ]} value={1} onChange={(v) => goToScene(v)} />
      </TS>
      <TS title="Display">
        <TT id="captions" label="Voice-over captions" value={tweaks.captions} onChange={(v) => setTweaks({ ...tweaks, captions: v })} />
        <TR id="graphAgentScope" label="Graph view scope"
          value={tweaks.graphAgentScope}
          options={[
            { value: "all", label: "all" },
            { value: "sales", label: "sales view" },
            { value: "cs", label: "cs view" },
          ]}
          onChange={(v) => setTweaks({ ...tweaks, graphAgentScope: v })}
        />
      </TS>
    </TP>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
