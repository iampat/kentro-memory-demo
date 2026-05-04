/* global */
// window.K.api — thin HTTP client around the kentro-server endpoints.
//
// State model:
//   - Three bearer tokens are loaded once via GET /demo/keys and cached in
//     localStorage under `kentroDemoKeys` (a {tenant_id, agents: [...]} blob).
//   - The current "acting as" agent_id is in localStorage under `kentroActingAs`
//     (defaults to "ingestion_agent" — admin — on first load).
//   - Every read uses the active agent's bearer token. Writes (PR 10-3) will
//     auto-elevate to the admin token when the route is admin-only, with a
//     visible `↑ admin` indicator on the originating button.
//
// Authentication shape: `Authorization: Bearer <api_key>`. No separate tenant
// header — the key resolves the (tenant, agent) pair on the server.

window.K = window.K || {};

(function () {
  const LS_KEYS = "kentroDemoKeys";
  const LS_ACTING = "kentroActingAs";
  const DEFAULT_ACTING = "ingestion_agent";

  // ── Token resolution ──────────────────────────────────────────────────────

  function readCachedKeys() {
    try {
      const raw = localStorage.getItem(LS_KEYS);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function writeCachedKeys(payload) {
    localStorage.setItem(LS_KEYS, JSON.stringify(payload));
  }

  function getActingAs() {
    return localStorage.getItem(LS_ACTING) || DEFAULT_ACTING;
  }

  function setActingAs(agent_id) {
    localStorage.setItem(LS_ACTING, agent_id);
    window.dispatchEvent(new CustomEvent("kentro:actingAsChanged", { detail: agent_id }));
  }

  function getAgentList() {
    const cached = readCachedKeys();
    return cached ? cached.agents : [];
  }

  function getKeyFor(agent_id) {
    const cached = readCachedKeys();
    if (!cached) return null;
    const found = cached.agents.find((a) => a.agent_id === agent_id);
    return found ? found.api_key : null;
  }

  function getAdminKey() {
    const cached = readCachedKeys();
    if (!cached) return null;
    const admin = cached.agents.find((a) => a.is_admin);
    return admin ? admin.api_key : null;
  }

  function getActiveKey() {
    return getKeyFor(getActingAs());
  }

  // ── Bootstrap ─────────────────────────────────────────────────────────────
  //
  // Called once on page load. Tries any cached admin key first; if /demo/keys
  // returns 200 we cache the full list. If 401 (unknown key) the cache was
  // stale; if 404 the server isn't in demo-keys mode — UI prompts manual entry
  // (out of scope for Stage A; we just leave cache empty).

  async function bootstrap(initialAdminKeyGuess) {
    // Try the cached admin key first.
    let adminKey = getAdminKey() || initialAdminKeyGuess;
    if (!adminKey) {
      // Fall back to the well-known local default — the boot guard accepts
      // it only when KENTRO_ALLOW_DEMO_KEYS=true is set, which `task dev` does.
      adminKey = "local-ingestion-do-not-share";
    }
    try {
      const r = await fetch("/demo/keys", {
        headers: { Authorization: `Bearer ${adminKey}` },
      });
      if (r.status === 200) {
        const payload = await r.json();
        writeCachedKeys(payload);
        // Make sure acting agent is one of the cached ones.
        const acting = getActingAs();
        const found = payload.agents.find((a) => a.agent_id === acting);
        if (!found) setActingAs(DEFAULT_ACTING);
        return { ok: true, payload };
      }
      return { ok: false, status: r.status, body: await r.text() };
    } catch (err) {
      return { ok: false, error: String(err) };
    }
  }

  // ── Generic fetch ─────────────────────────────────────────────────────────

  async function _fetch(path, opts = {}) {
    const useAdmin = opts.elevateToAdmin === true;
    const key = useAdmin ? getAdminKey() : getActiveKey();
    if (!key) {
      throw new Error(
        `No bearer token available (acting=${getActingAs()}, elevate=${useAdmin}). Run K.api.bootstrap() first.`
      );
    }
    const headers = Object.assign(
      { Accept: "application/json" },
      opts.body ? { "Content-Type": "application/json" } : {},
      opts.headers || {},
      { Authorization: `Bearer ${key}` }
    );
    const r = await fetch(path, { method: opts.method || "GET", headers, body: opts.body });
    if (r.status === 204) return null;
    const text = await r.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      // non-JSON response (e.g. static html on 404 catch-all)
      json = { raw: text };
    }
    if (!r.ok) {
      const err = new Error(`HTTP ${r.status} ${path}: ${text.slice(0, 200)}`);
      err.status = r.status;
      err.body = json;
      throw err;
    }
    return json;
  }

  // ── Read API ──────────────────────────────────────────────────────────────

  async function listDocuments() {
    const r = await _fetch("/documents");
    return r.documents || [];
  }

  async function listEntities(entity_type) {
    const r = await _fetch(`/entities/${encodeURIComponent(entity_type)}`);
    return r.entities || [];
  }

  async function readEntity(entity_type, entity_key) {
    return _fetch(
      `/entities/${encodeURIComponent(entity_type)}/${encodeURIComponent(entity_key)}`
    );
  }

  async function listSchema() {
    const r = await _fetch("/schema");
    return r.type_defs || [];
  }

  async function getRules() {
    return _fetch("/rules/active");
  }

  async function getStats() {
    return _fetch("/llm/stats");
  }

  // ── Write API (admin-elevation auto for control-plane routes) ─────────────

  async function applyRules(ruleset, summary) {
    return _fetch("/rules/apply", {
      method: "POST",
      body: JSON.stringify({ ruleset, summary }),
      elevateToAdmin: true,
    });
  }

  async function parseNL(text) {
    return _fetch("/rules/parse", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
  }

  async function writeField(entity_type, entity_key, field_name, value_json, confidence) {
    return _fetch(
      `/entities/${encodeURIComponent(entity_type)}/${encodeURIComponent(
        entity_key
      )}/${encodeURIComponent(field_name)}`,
      {
        method: "POST",
        body: JSON.stringify({ value_json, confidence }),
      }
    );
  }

  async function ingestDocument(content, label, source_class) {
    // Acting agent ingests — not admin-elevated. Most plausible field-write
    // permissions live on the active agent (e.g. ingestion_agent has them).
    return _fetch("/documents", {
      method: "POST",
      body: JSON.stringify({ content, label, source_class }),
    });
  }

  async function deleteDocument(document_id) {
    return _fetch(`/documents/${encodeURIComponent(document_id)}`, {
      method: "DELETE",
      elevateToAdmin: true,
    });
  }

  // Demo-only seed shortcut (admin + KENTRO_ALLOW_DEMO_KEYS gated).
  async function seedDemo() {
    return _fetch("/demo/seed", { method: "POST", elevateToAdmin: true });
  }

  // ── Public surface ────────────────────────────────────────────────────────

  K.api = {
    // bootstrap + agent management
    bootstrap,
    getActingAs,
    setActingAs,
    getAgentList,
    getKeyFor,
    getAdminKey,
    // reads
    listDocuments,
    listEntities,
    readEntity,
    listSchema,
    getRules,
    getStats,
    // writes
    applyRules,
    parseNL,
    writeField,
    ingestDocument,
    deleteDocument,
    seedDemo,
    // low-level escape hatch
    _fetch,
  };
})();
