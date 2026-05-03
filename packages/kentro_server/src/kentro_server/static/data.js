// Kentro demo data — synthetic corpus + initial state
window.KENTRO_DATA = (function () {
  const documents = [
    {
      id: "acme_call_2026-04-15",
      type: "transcript",
      icon: "📞",
      sourceClass: "verbal",
      name: "acme_call_2026-04-15.md",
      label: "Sales call — Acme Corp",
      timestamp: "2026-04-15 10:32",
      addedAt: "scene1",
      content:
`# Sales call — Acme Corp · 2026-04-15

**Attendees:** Jane Doe (Acme, VP Ops), Marco Riley (Sales)

Marco: Great to finally connect, Jane. You mentioned the renewal was on your radar?

Jane: Yeah. We're aligned on staying. Internally I'm floating renewal at around $250K — that's the number I have in my head, but let me confirm with finance before we put pen to paper.

Marco: That works. We can hold pricing for two weeks while you confirm.

Jane: One more thing — we'll likely need extra seats for our new support team. I'll loop in our CFO this week.

Marco: Sounds good. I'll send a follow-up summary today.`,
      extracts: [
        { entity: "Customer", key: "Acme", field: "name", value: "Acme Corp" },
        { entity: "Customer", key: "Acme", field: "contact", value: "Jane Doe" },
        { entity: "Customer", key: "Acme", field: "deal_size", value: "$250K", note: "verbal, conditional" },
        { entity: "Person", key: "Jane", field: "name", value: "Jane Doe" },
        { entity: "Person", key: "Jane", field: "role", value: "VP Ops, Acme" },
        { entity: "Deal", key: "acme-renewal-2026", field: "size", value: "$250K" },
      ],
    },
    {
      id: "email_jane_2026-04-17",
      type: "email",
      icon: "✉️",
      sourceClass: "written",
      name: "email_jane_2026-04-17.md",
      label: "Follow-up email — Jane Doe",
      timestamp: "2026-04-17 14:08",
      addedAt: "scene2",
      content:
`From: jane.doe@acme.com
To: marco@kentro.demo
Subject: Re: Acme renewal — updated number

Hi Marco,

Thanks for the call on Monday. I spoke with finance yesterday and we revised the renewal to $300K — that includes the additional support seats we discussed and pulls forward the Q3 expansion budget.

Can you have the paperwork ready by end of next week?

Best,
Jane`,
      extracts: [
        { entity: "Customer", key: "Acme", field: "deal_size", value: "$300K", note: "written, finance-approved" },
        { entity: "Deal", key: "acme-renewal-2026", field: "size", value: "$300K" },
      ],
    },
    {
      id: "ticket_142",
      type: "ticket",
      icon: "🎫",
      sourceClass: "written",
      name: "acme_ticket_142.md",
      label: "Support ticket #142",
      timestamp: "2026-04-12",
      addedAt: "scene1",
      extracts: [
        { entity: "Customer", key: "Acme", field: "support_tickets", value: "#142 (login issues)" },
      ],
    },
    {
      id: "ticket_157",
      type: "ticket",
      icon: "🎫",
      sourceClass: "written",
      name: "acme_ticket_157.md",
      label: "Support ticket #157",
      timestamp: "2026-04-14",
      addedAt: "scene1",
      extracts: [
        { entity: "Customer", key: "Acme", field: "support_tickets", value: "#157 (export bug)" },
      ],
    },
  ];

  // Entity store — starts populated from scene1 documents
  const entities = {
    "Customer:Acme": {
      type: "Customer",
      key: "Acme",
      label: "Customer.Acme",
      fields: {
        name: { values: [{ value: "Acme Corp", source: "acme_call_2026-04-15", agent: "ingestion_agent", ts: "2026-04-15 10:32" }] },
        contact: { values: [{ value: "Jane Doe", source: "acme_call_2026-04-15", agent: "ingestion_agent", ts: "2026-04-15 10:32" }] },
        deal_size: { values: [{ value: "$250K", source: "acme_call_2026-04-15", agent: "ingestion_agent", ts: "2026-04-15 10:32", sourceClass: "verbal" }] },
        sales_notes: { values: [] },
        support_tickets: {
          values: [
            { value: "#142, #157", source: "ticket_142,ticket_157", agent: "ingestion_agent", ts: "2026-04-14" },
          ],
        },
      },
    },
    "Deal:acme-renewal-2026": {
      type: "Deal",
      key: "acme-renewal-2026",
      label: "Deal.acme-renewal-2026",
      fields: {
        customer: { values: [{ value: "Acme", source: "acme_call_2026-04-15", agent: "ingestion_agent", ts: "2026-04-15 10:32" }] },
        size: { values: [{ value: "$250K", source: "acme_call_2026-04-15", agent: "ingestion_agent", ts: "2026-04-15 10:32" }] },
        stage: { values: [{ value: "negotiation", source: "acme_call_2026-04-15", agent: "ingestion_agent", ts: "2026-04-15 10:32" }] },
      },
    },
    "AuditLog:acme": {
      type: "AuditLog",
      key: "acme",
      label: "AuditLog.acme",
      fields: {
        events: {
          values: [
            { value: "12 access events (90d)", source: "system", agent: "audit_agent", ts: "2026-04-30" },
          ],
        },
      },
    },
  };

  const initialRules = {
    sales: {
      Customer: { read: ["name", "contact", "deal_size", "sales_notes", "support_tickets"], write: ["deal_size", "sales_notes"] },
      Deal: { read: ["*"], write: ["*"], create: true },
      AuditLog: { read: [], write: [], visible: false },
    },
    cs: {
      Customer: { read: ["name", "contact", "support_tickets"], write: ["support_tickets"], writeRequiresApproval: false },
      Deal: { read: [], write: [], visible: false },
      AuditLog: { read: [], write: [], visible: false },
    },
    conflictResolver: { kind: "LatestWriteResolver" },
  };

  const fieldOrder = {
    Customer: ["name", "contact", "deal_size", "sales_notes", "support_tickets"],
    Deal: ["customer", "size", "stage"],
    AuditLog: ["events"],
  };

  // Initial PBAC policies — Rego-flavored, mapped to the engine via `engine` field
  const initialPolicies = [
    {
      id: "p_sales_customer_read",
      kind: "access",
      summary: "Sales can read all Customer fields",
      rego: `package kentro.access\n\nallow {\n  input.role == "sales"\n  input.action == "read"\n  input.resource.type == "Customer"\n}`,
      engine: { type: "set_read", role: "sales", resourceType: "Customer", fields: ["name", "contact", "deal_size", "sales_notes", "support_tickets"] },
    },
    {
      id: "p_cs_customer_read",
      kind: "access",
      summary: "Customer Service can read Customer.{name, contact, support_tickets}",
      rego: `package kentro.access\n\nallow {\n  input.role == "cs"\n  input.action == "read"\n  input.resource.type == "Customer"\n  input.resource.field == allowed[_]\n}\n\nallowed := ["name", "contact", "support_tickets"]`,
      engine: { type: "set_read", role: "cs", resourceType: "Customer", fields: ["name", "contact", "support_tickets"] },
    },
    {
      id: "p_sales_auditlog_hidden",
      kind: "access",
      summary: "AuditLog is hidden from Sales",
      rego: `package kentro.access\n\ndeny[msg] {\n  input.role == "sales"\n  input.resource.type == "AuditLog"\n  msg := "AuditLog not visible"\n}`,
      engine: { type: "set_visibility", role: "sales", resourceType: "AuditLog", visible: false },
    },
    {
      id: "p_conflict_latest",
      kind: "conflict",
      summary: "On conflicting writes, the latest write wins",
      rego: `package kentro.resolve\n\nresolved[field] = winner {\n  candidates := input.field.values\n  winner := candidates[_]\n  not exists_newer(winner, candidates)\n}`,
      engine: { type: "set_resolver", kind: "LatestWriteResolver" },
    },
  ];

  return { documents, entities, initialRules, initialPolicies, fieldOrder };
})();
