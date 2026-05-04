/* global React */
// Small helpers shared across components

window.K = window.K || {};

// docMeta(doc) — derive the prototype's per-doc presentation from
// `source_class` (set during seed) + filename heuristics. Returns
// {icon, typeLabel, sourceClass}.
//   verbal  → 📞 Call
//   email   → ✉️ Email
//   ticket  → 🎫 Ticket
//   note    → 📝 Note
//   other   → 📄 Doc
K.docMeta = function (doc) {
  const sc = (doc && doc.source_class) || null;
  const label = (doc && doc.label) || "";
  // Re-infer when source_class is missing (older state, manual ingests).
  let bucket = sc;
  if (!bucket) {
    const n = label.toLowerCase();
    if (n.includes("call") || n.includes("transcript")) bucket = "verbal";
    else if (n.includes("email")) bucket = "email";
    else if (n.includes("ticket")) bucket = "ticket";
    else if (n.includes("meeting_note") || n.includes("slack") || n.includes("note"))
      bucket = "note";
  }
  switch (bucket) {
    case "verbal":
      return { icon: "📞", typeLabel: "Call", sourceClass: "verbal" };
    case "email":
      return { icon: "✉️", typeLabel: "Email", sourceClass: "written" };
    case "ticket":
      return { icon: "🎫", typeLabel: "Ticket", sourceClass: "written" };
    case "note":
      return { icon: "📝", typeLabel: "Note", sourceClass: "written" };
    default:
      return { icon: "📄", typeLabel: "Doc", sourceClass: bucket || null };
  }
};

// Format a field value from `GET /entities/...` as a display string. Hidden
// fields show the redaction marker; unresolved fields show candidate values
// joined by `⇄`; known fields show the raw string or JSON-encoded value.
// Shared between AgentPanel (top-row live read) and EntityOverlay (the
// global+per-agent comparison view).
K.fmtFieldValue = function (fval) {
  if (!fval) return "—";
  switch (fval.status) {
    case "hidden":
      return "⊘ redacted by ACL";
    case "unknown":
      return "—";
    case "unresolved": {
      const cands = (fval.candidates || []).map((c) => JSON.stringify(c.value));
      return cands.join(" ⇄ ") || "(unresolved)";
    }
    case "known":
      // String values that look like markdown filenames (e.g. the Note
      // entity's `source_label` set to "acme_ticket_162.md") read more
      // naturally without the `.md` suffix. K.docLabel strips trailing
      // `.md` and is a no-op for everything else.
      if (typeof fval.value === "string") return K.docLabel(fval.value);
      return JSON.stringify(fval.value);
    default:
      return JSON.stringify(fval.value ?? "—");
  }
};

// Type-aware presentation of a candidate value for the lineage flow's
// candidate→resolver curve label. Returns:
//   { lines: string[], full: string, kind: "text"|"number"|"dict"|"array"|"empty" }
//
// Strategy per type (matches the design discussion):
//   number → render in full, with thousand separators when ≥ 1000
//   text   → single-line truncation at ~22 chars (full text in <title>)
//   array  → "[…N items]" inline (full content in <title>)
//   dict   → up to 3 "key: value" lines, "+N more" if there's a tail
//   empty  → em-dash
//
// The renderer (LineageFlowLayout) anchors text at textAnchor="start" so
// chip width grows rightward toward the resolver, never leftward into
// the candidate card.
K.formatCandidateChip = function (value) {
  if (value === null || value === undefined) {
    return { lines: ["—"], full: "—", kind: "empty" };
  }
  if (typeof value === "number") {
    const formatted =
      Math.abs(value) >= 1000 && Number.isFinite(value)
        ? value.toLocaleString("en-US")
        : String(value);
    return { lines: [formatted], full: formatted, kind: "number" };
  }
  if (typeof value === "boolean") {
    const s = String(value);
    return { lines: [s], full: s, kind: "number" };
  }
  if (typeof value === "string") {
    const display = value.length > 24 ? value.slice(0, 22) + "…" : value;
    return { lines: [display], full: value, kind: "text" };
  }
  if (Array.isArray(value)) {
    const n = value.length;
    const label = n === 0 ? "[empty]" : `[…${n} ${n === 1 ? "item" : "items"}]`;
    return {
      lines: [label],
      full: JSON.stringify(value, null, 2),
      kind: "array",
    };
  }
  if (typeof value === "object") {
    const keys = Object.keys(value);
    if (keys.length === 0) {
      return { lines: ["{}"], full: "{}", kind: "dict" };
    }
    const maxLines = 3;
    const lines = [];
    for (let i = 0; i < Math.min(keys.length, maxLines); i++) {
      const k = keys[i];
      const v = value[k];
      const vStr = typeof v === "string" ? v : JSON.stringify(v);
      const display = vStr.length > 16 ? vStr.slice(0, 14) + "…" : vStr;
      lines.push(`${k}: ${display}`);
    }
    if (keys.length > maxLines) {
      lines.push(`+${keys.length - maxLines} more`);
    }
    return {
      lines,
      full: JSON.stringify(value, null, 2),
      kind: "dict",
    };
  }
  const s = String(value);
  return { lines: [s], full: s, kind: "text" };
};


// Display-friendly version of a document filename: strip the `.md` suffix the
// corpus uses but keep the stem intact. The on-disk label stays canonical;
// only the rendering changes. Returns "" for null/undefined so callers don't
// have to null-guard.
K.docLabel = function (label) {
  if (!label) return "";
  return String(label).replace(/\.md$/i, "");
};

// === Source-class-aware document rendering ==================================
//
// Parses each corpus document into a typed structure so the UI can render it
// in a tool-shaped frame (Gong call / Jira ticket / Gmail email) instead of
// raw markdown. The parsers are intentionally regex-based — the corpus is
// small, hand-authored, and predictable; pulling in a markdown parser would
// be over-engineering. If a doc doesn't match the expected shape, the parsers
// return `{ kind: "raw" }` and the renderer falls back to <pre>.

K.parseCallContent = function (content) {
  // Expected: `## <title>\n\n**Speaker:** text\n\n**Speaker:** text...`
  const lines = String(content || "").split("\n");
  let title = "";
  if (lines[0] && lines[0].startsWith("## ")) {
    title = lines[0].replace(/^##\s+/, "");
  }
  const turns = [];
  // Each turn starts with `**Name:**` and runs until the next `**Name:**` or EOF.
  // Walk the body line-by-line so multi-paragraph turns stay together.
  const turnRe = /^\*\*([^:*]+):\*\*\s*(.*)$/;
  let cur = null;
  for (let i = title ? 1 : 0; i < lines.length; i++) {
    const ln = lines[i];
    const m = ln.match(turnRe);
    if (m) {
      if (cur) turns.push(cur);
      cur = { speaker: m[1].trim(), text: m[2] };
    } else if (cur) {
      cur.text += "\n" + ln;
    }
  }
  if (cur) turns.push(cur);
  // Trim trailing whitespace on each turn.
  turns.forEach((t) => {
    t.text = t.text.trim();
  });
  if (turns.length === 0) return { kind: "raw", content };
  return { kind: "call", title, turns };
};

K.parseTicketContent = function (content) {
  // Expected: `## Ticket #N - X\n\n**Status:** ...\n**Severity:** ...\n\n---\n\n### Description\n\n...`
  // The corpus uses two heading styles inside the body — `### Description`
  // (markdown heading) AND `**Description:**` (bold-key, alone on its line).
  // The parser handles both so 142.md and 157.md both render with proper
  // section headings rather than raw bold text in the body.
  const text = String(content || "");
  const lines = text.split("\n");
  let title = "";
  let cursor = 0;
  if (lines[0] && lines[0].startsWith("## ")) {
    title = lines[0].replace(/^##\s+/, "");
    cursor = 1;
  }
  // Skip blank lines, then collect `**Key:** value` lines until we hit the `---`
  // separator. A `**Key:**` line with NO value (just whitespace after) is a
  // section heading, not a metadata field — bail to the body parser.
  while (cursor < lines.length && lines[cursor].trim() === "") cursor++;
  const fields = [];
  const fieldRe = /^\*\*([^:*]+):\*\*\s*(.*)$/;
  while (cursor < lines.length) {
    const ln = lines[cursor];
    if (ln.trim() === "" || ln.trim().startsWith("---") || ln.trim().startsWith("###")) break;
    const m = ln.match(fieldRe);
    if (m) {
      const value = m[2].trim();
      if (value === "") break; // bold-only line → section heading, not field
      fields.push({ key: m[1].trim(), value });
      cursor++;
    } else {
      break;
    }
  }
  // Skip blank lines + the `---` rule + blank lines.
  while (cursor < lines.length) {
    const t = lines[cursor].trim();
    if (t === "" || t === "---") {
      cursor++;
      continue;
    }
    break;
  }
  // Body: split into sections by `### Heading` OR `**Heading:**` (alone).
  const sections = [];
  let curHeading = null;
  let curBody = [];
  const flush = () => {
    if (curHeading || curBody.length > 0) {
      sections.push({ heading: curHeading, body: curBody.join("\n").trim() });
    }
  };
  const boldHeadRe = /^\*\*([^:*]+):\*\*\s*$/;
  for (; cursor < lines.length; cursor++) {
    const ln = lines[cursor];
    if (ln.startsWith("### ")) {
      flush();
      curHeading = ln.replace(/^###\s+/, "").trim();
      curBody = [];
      continue;
    }
    const bm = ln.match(boldHeadRe);
    if (bm) {
      flush();
      curHeading = bm[1].trim();
      curBody = [];
      continue;
    }
    curBody.push(ln);
  }
  flush();
  if (fields.length === 0 && sections.length === 0) return { kind: "raw", content };
  return { kind: "ticket", title, fields, sections };
};

// Slack thread parser. Matches `## Slack - #channel - date\n\n*description*\n\n---\n\n**handle** [HH:MM]\nbody...`
K.parseSlackContent = function (content) {
  const text = String(content || "");
  const lines = text.split("\n");
  let title = "";
  let subtitle = "";
  let cursor = 0;
  if (lines[0] && lines[0].startsWith("## ")) {
    title = lines[0].replace(/^##\s+/, "");
    cursor = 1;
  }
  while (cursor < lines.length && lines[cursor].trim() === "") cursor++;
  // Optional italicized blurb (`*Thread in #aes regarding ...*`).
  if (cursor < lines.length) {
    const ln = lines[cursor].trim();
    const im = ln.match(/^\*([^*][^*]*[^*])\*$/);
    if (im) {
      subtitle = im[1];
      cursor++;
    }
  }
  // Skip the `---` rule + blanks.
  while (cursor < lines.length) {
    const t = lines[cursor].trim();
    if (t === "" || t === "---") {
      cursor++;
      continue;
    }
    break;
  }
  const messages = [];
  // Each message: `**handle** [HH:MM]` line, then text until the next message or EOF.
  const headRe = /^\*\*([^*]+)\*\*\s*\[([^\]]+)\]\s*$/;
  let cur = null;
  for (; cursor < lines.length; cursor++) {
    const ln = lines[cursor];
    const m = ln.match(headRe);
    if (m) {
      if (cur) messages.push(cur);
      cur = { handle: m[1].trim(), time: m[2].trim(), text: "" };
    } else if (cur) {
      cur.text += (cur.text ? "\n" : "") + ln;
    }
  }
  if (cur) messages.push(cur);
  messages.forEach((m) => {
    m.text = m.text.trim();
  });
  if (messages.length === 0) return { kind: "raw", content };
  return { kind: "slack", title, subtitle, messages };
};

// Inline-markdown formatter for note bodies — handles **bold** and `code`.
// Returns an array of {type, text} segments the renderer can map to JSX.
K.tokenizeInlineMarkdown = function (text) {
  if (!text) return [];
  const tokens = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) tokens.push({ type: "text", text: text.slice(last, m.index) });
    const seg = m[0];
    if (seg.startsWith("**")) tokens.push({ type: "bold", text: seg.slice(2, -2) });
    else if (seg.startsWith("`")) tokens.push({ type: "code", text: seg.slice(1, -1) });
    last = m.index + seg.length;
  }
  if (last < text.length) tokens.push({ type: "text", text: text.slice(last) });
  return tokens;
};

// Note parser: produces a sequence of typed blocks (heading, paragraph,
// list, label-value) the renderer can map to proper markdown-ish JSX. Used
// for meeting notes and any note-class document that isn't a slack thread.
K.parseNoteContent = function (content) {
  const text = String(content || "");
  const lines = text.split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    const trimmed = ln.trim();
    if (trimmed === "") {
      i++;
      continue;
    }
    if (ln.startsWith("## ")) {
      blocks.push({ type: "heading", level: 2, text: ln.replace(/^##\s+/, "") });
      i++;
      continue;
    }
    if (ln.startsWith("### ")) {
      blocks.push({ type: "heading", level: 3, text: ln.replace(/^###\s+/, "") });
      i++;
      continue;
    }
    // Bullet list — collect contiguous `- ` lines.
    if (ln.startsWith("- ") || ln.startsWith("* ")) {
      const items = [];
      while (i < lines.length && (lines[i].startsWith("- ") || lines[i].startsWith("* "))) {
        items.push(lines[i].replace(/^[-*]\s+/, ""));
        i++;
      }
      blocks.push({ type: "list", items });
      continue;
    }
    // Label-value `**Key:** value` — short metadata pair, render as a row
    // rather than a paragraph so the note reads like a structured note.
    const fieldRe = /^\*\*([^:*]+):\*\*\s+(.+)$/;
    const fm = ln.match(fieldRe);
    if (fm) {
      blocks.push({ type: "field", key: fm[1].trim(), value: fm[2].trim() });
      i++;
      continue;
    }
    // Otherwise gather lines into a paragraph until blank.
    const para = [];
    while (i < lines.length && lines[i].trim() !== "") {
      para.push(lines[i]);
      i++;
    }
    if (para.length > 0) blocks.push({ type: "paragraph", text: para.join("\n") });
  }
  return { kind: "note", blocks };
};

K.parseEmailContent = function (content) {
  // Expected: `## Email - X -> Y, date\n\n**From:** ...\n**To:** ...\n**Subject:** ...\n**Date:** ...\n\n---\n\nbody`
  const text = String(content || "");
  const lines = text.split("\n");
  let title = "";
  let cursor = 0;
  if (lines[0] && lines[0].startsWith("## ")) {
    title = lines[0].replace(/^##\s+/, "");
    cursor = 1;
  }
  while (cursor < lines.length && lines[cursor].trim() === "") cursor++;
  const headers = {};
  const fieldRe = /^\*\*([^:*]+):\*\*\s*(.*)$/;
  while (cursor < lines.length) {
    const ln = lines[cursor];
    if (ln.trim() === "" || ln.trim().startsWith("---")) break;
    const m = ln.match(fieldRe);
    if (m) {
      headers[m[1].trim().toLowerCase()] = m[2].trim();
      cursor++;
    } else {
      break;
    }
  }
  while (cursor < lines.length) {
    const t = lines[cursor].trim();
    if (t === "" || t === "---") {
      cursor++;
      continue;
    }
    break;
  }
  const body = lines.slice(cursor).join("\n").trim();
  if (!headers.from && !headers.to && !headers.subject) return { kind: "raw", content };
  return {
    kind: "email",
    title,
    from: headers.from || null,
    to: headers.to || null,
    subject: headers.subject || null,
    date: headers.date || null,
    body,
  };
};

// Pick the right parser based on the canonical source_class produced during
// ingest (verbal/email/ticket/note). Falls back to filename heuristics for
// older state. For notes, the title or filename further distinguishes Slack
// threads (rendered as a chat) from meeting notes (rendered as markdown).
// Returns `{ kind: "raw", content }` when nothing matches so the renderer
// always has something to show.
K.parseDocumentContent = function (doc) {
  if (!doc) return { kind: "raw", content: "" };
  const meta = K.docMeta({ source_class: doc.source_class, label: doc.label });
  const content = doc.content || "";
  const label = (doc.label || "").toLowerCase();
  switch (meta.typeLabel) {
    case "Call":
      return K.parseCallContent(content);
    case "Ticket":
      return K.parseTicketContent(content);
    case "Email":
      return K.parseEmailContent(content);
    case "Note": {
      const titleLower = content.split("\n", 1)[0].toLowerCase();
      const isSlack = label.includes("slack") || titleLower.includes("slack");
      if (isSlack) return K.parseSlackContent(content);
      return K.parseNoteContent(content);
    }
    default:
      return { kind: "raw", content };
  }
};

K.cls = function (...parts) {
  return parts.filter(Boolean).join(" ");
};

K.uid = (() => { let i = 0; return () => `k${++i}`; })();

// Build a deep clone of an object (state util)
K.clone = (o) => JSON.parse(JSON.stringify(o));

// Voice-over snippets per scene — shown when captions tweak is on
K.VO = {
  scene1: "Two agents — Sales and Customer Service. Three kinds of memory: Customer, Deal, AuditLog. Different agents, different views. Different agents, different write rights. Some memory is invisible entirely.",
  scene2: "A second source disagrees. In Monday's call the prospect floated $250K. In Wednesday's email, after talking with finance, $300K. Kentro doesn't pick a winner blindly — conflict isn't an event, it's a memory record.",
  scene3: "Compliance writes the change in plain English. Kentro parses it to structured rule edits. Then apply. Field reads, entity visibility, write permissions, conflict resolution — all four changed in one edit. Nothing was re-indexed.",
  scene4: "Click any field — full lineage. Both source documents, the agent that wrote them, the rules in effect, and the policy that resolved the conflict. Audit isn't a bolt-on — it's how memory works.",
};

// Resolve a field's effective value given current rules + conflict resolver.
K.resolveField = function (field, resolver, rules) {
  if (!field || !field.values || field.values.length === 0) {
    return { status: "UNKNOWN", value: null, candidates: [] };
  }
  if (field.values.length === 1) {
    return { status: "KNOWN", value: field.values[0], candidates: field.values };
  }
  // multiple values — conflict
  switch (resolver?.kind) {
    case "SkillResolver": {
      // policy: written outweighs verbal, latest among written wins
      const written = field.values.filter((v) => v.sourceClass === "written");
      if (written.length > 0) {
        const winner = written.slice().sort((a, b) => (a.ts < b.ts ? 1 : -1))[0];
        return { status: "KNOWN", value: winner, candidates: field.values, resolution: "SkillResolver: written outweighs verbal" };
      }
      return { status: "UNRESOLVED", value: null, candidates: field.values, reason: "no written sources" };
    }
    case "LatestWriteResolver": {
      const winner = field.values.slice().sort((a, b) => (a.ts < b.ts ? 1 : -1))[0];
      return { status: "KNOWN", value: winner, candidates: field.values, resolution: "LatestWriteResolver: most recent write wins" };
    }
    default: {
      return { status: "UNRESOLVED", value: null, candidates: field.values };
    }
  }
};

// Filter an entity through a rule set for a specific agent
K.viewEntity = function (entity, agentRules, resolver) {
  const typeRules = agentRules?.[entity.type];
  if (!typeRules || typeRules.visible === false) {
    return { hidden: true, reason: "entity not visible to this agent" };
  }
  const readable = typeRules.read || [];
  const all = readable.includes("*");
  const fields = {};
  for (const [name, field] of Object.entries(entity.fields)) {
    if (!all && !readable.includes(name)) {
      fields[name] = { status: "HIDDEN", redacted: true };
    } else {
      fields[name] = { ...K.resolveField(field, resolver), raw: field };
    }
  }
  return { hidden: false, fields };
};

// Can agent write field?
K.canWrite = function (agentRules, type, field) {
  const typeRules = agentRules?.[type];
  if (!typeRules || typeRules.visible === false) return { allowed: false, reason: "entity not visible" };
  const writable = typeRules.write || [];
  const all = writable.includes("*");
  if (all || writable.includes(field)) {
    if (typeRules.writeRequiresApproval) {
      return { allowed: false, reason: "write blocked: manager approval required" };
    }
    return { allowed: true };
  }
  return { allowed: false, reason: `write blocked: ${type}.${field} is not writable by this agent` };
};
