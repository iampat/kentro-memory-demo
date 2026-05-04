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
