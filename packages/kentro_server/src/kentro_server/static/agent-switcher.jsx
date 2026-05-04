/* global React, K */
// Header bar — agent switcher + connection state.
//
// Renders a compact "Acting as: [Sales ▾]" dropdown plus a tiny dot indicating
// whether the page successfully booted the API (agent keys cached). On agent
// switch, dispatches `kentro:actingAsChanged` (handled by api.js + listened
// to in app.jsx to refetch).

const { useEffect, useState } = React;

window.K.AgentSwitcher = function AgentSwitcher() {
  const [acting, setActing] = useState(K.api.getActingAs());
  const [agents, setAgents] = useState(K.api.getAgentList());
  const [bootState, setBootState] = useState("idle"); // idle | ok | error
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const result = await K.api.bootstrap();
      if (cancelled) return;
      if (result.ok) {
        setAgents(K.api.getAgentList());
        setActing(K.api.getActingAs());
        setBootState("ok");
        // Tell consumers (App) the API is ready to accept reads.
        window.dispatchEvent(new CustomEvent("kentro:bootstrapped", { detail: result.payload }));
      } else {
        setBootState("error");
        setError(
          result.status === 404
            ? "/demo/keys disabled (set KENTRO_ALLOW_DEMO_KEYS=true)"
            : result.status
              ? `${result.status} from /demo/keys`
              : result.error || "unknown bootstrap error"
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const onChange = (e) => {
    const next = e.target.value;
    setActing(next);
    K.api.setActingAs(next);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "8px 14px",
        borderBottom: "1px solid var(--line)",
        background: "var(--surface)",
        fontFamily: "var(--mono)",
        fontSize: 11,
        letterSpacing: "0.04em",
      }}
    >
      <span style={{ color: "var(--ink-2)" }}>kentro · live</span>
      <span
        title={
          bootState === "ok"
            ? "agent keys cached from /demo/keys"
            : bootState === "error"
              ? `bootstrap failed: ${error}`
              : "loading…"
        }
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background:
            bootState === "ok"
              ? "var(--accent, #4ade80)"
              : bootState === "error"
                ? "#ef4444"
                : "#9ca3af",
        }}
      />
      <span style={{ marginLeft: "auto", color: "var(--ink-2)" }}>acting as:</span>
      <select
        value={acting}
        onChange={onChange}
        disabled={agents.length === 0}
        style={{
          fontFamily: "var(--mono)",
          fontSize: 11,
          letterSpacing: "0.04em",
          background: "var(--bg)",
          color: "var(--ink-1)",
          border: "1px solid var(--line)",
          padding: "4px 8px",
          minWidth: 200,
        }}
      >
        {agents.length === 0 && <option>(no agents — bootstrap pending)</option>}
        {agents.map((a) => (
          <option key={a.agent_id} value={a.agent_id}>
            {a.display_name || a.agent_id}
            {a.is_admin ? " · admin" : ""}
          </option>
        ))}
      </select>
      {bootState === "error" && (
        <span style={{ color: "#ef4444", fontSize: 10 }}>error: {error}</span>
      )}
    </div>
  );
};
