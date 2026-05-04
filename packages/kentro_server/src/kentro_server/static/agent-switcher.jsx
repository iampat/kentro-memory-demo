/* global React, K */
// Header bar — agent switcher + connection state.
//
// Renders the brand mark + acting-as dropdown using the prototype's design
// tokens (.agent-switcher class in styles.css). On agent switch, dispatches
// `kentro:actingAsChanged`. On bootstrap success, dispatches
// `kentro:bootstrapped` so panels know it's safe to fetch.

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
    <div className="agent-switcher">
      <span className="brand">
        kentro · live
        <span
          className={`brand-dot ${bootState === "error" ? "error" : bootState === "idle" ? "idle" : ""}`}
          title={
            bootState === "ok"
              ? "agent keys cached from /demo/keys"
              : bootState === "error"
                ? `bootstrap failed: ${error}`
                : "loading…"
          }
        />
      </span>
      <span className="acting-label">acting as</span>
      <select value={acting} onChange={onChange} disabled={agents.length === 0}>
        {agents.length === 0 && <option>(bootstrap pending)</option>}
        {agents.map((a) => (
          <option key={a.agent_id} value={a.agent_id}>
            {a.display_name || a.agent_id}
            {a.is_admin ? " · admin" : ""}
          </option>
        ))}
      </select>
      {bootState === "error" && <span className="err-msg">error: {error}</span>}
    </div>
  );
};
