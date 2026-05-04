/* global React, K */
// <EscalationToast/> — subscribes to GET /events SSE and renders fade-in toasts
// for `notify` events fired by SkillResolver workflow actions.
//
// EventSource doesn't accept Authorization headers, so we open the SSE stream
// via fetch() with a streaming reader. That lets us pass the bearer for the
// current acting agent. Reconnect on agent switch.

const { useEffect, useState, useRef } = React;

window.K.EscalationToast = function EscalationToast() {
  const [toasts, setToasts] = useState([]); // [{id, channel, message}]
  const dismissRef = useRef(null);

  // Push helper — auto-dismisses each toast after 6s.
  const push = (channel, message) => {
    const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    setToasts((prev) => [...prev, { id, channel, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  };
  dismissRef.current = (id) => setToasts((prev) => prev.filter((t) => t.id !== id));

  useEffect(() => {
    let abort = new AbortController();
    let cancelled = false;

    async function connect() {
      // Wait for bootstrap so the bearer cache is hot.
      const acting = K.api.getActingAs();
      const key = K.api.getKeyFor ? K.api.getKeyFor(acting) : null;
      if (!key) {
        // Bootstrap not done yet. Listen for it.
        return;
      }
      try {
        const response = await fetch("/events", {
          headers: { Authorization: `Bearer ${key}`, Accept: "text/event-stream" },
          signal: abort.signal,
        });
        if (!response.ok || !response.body) {
          console.warn("EscalationToast: /events failed", response.status);
          return;
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        // SSE parsing: events are separated by blank lines ("\n\n").
        while (!cancelled) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let sep;
          while ((sep = buf.indexOf("\n\n")) !== -1) {
            const block = buf.slice(0, sep);
            buf = buf.slice(sep + 2);
            // Each block is a sequence of "field: value" lines.
            let eventName = "message";
            const dataLines = [];
            for (const line of block.split("\n")) {
              if (line.startsWith(":")) continue; // comment / heartbeat
              if (line.startsWith("event:")) eventName = line.slice(6).trim();
              else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
            }
            if (eventName !== "notify" || dataLines.length === 0) continue;
            try {
              const payload = JSON.parse(dataLines.join("\n"));
              push(payload.channel || "?", payload.message || "(no message)");
            } catch (err) {
              console.warn("EscalationToast: malformed SSE payload", err);
            }
          }
        }
      } catch (err) {
        if (!cancelled && err.name !== "AbortError") {
          console.warn("EscalationToast: stream error", err);
        }
      }
    }

    const onSwitchOrBoot = () => {
      abort.abort();
      abort = new AbortController();
      connect();
    };
    window.addEventListener("kentro:bootstrapped", onSwitchOrBoot);
    window.addEventListener("kentro:actingAsChanged", onSwitchOrBoot);
    connect();
    return () => {
      cancelled = true;
      abort.abort();
      window.removeEventListener("kentro:bootstrapped", onSwitchOrBoot);
      window.removeEventListener("kentro:actingAsChanged", onSwitchOrBoot);
    };
  }, []);

  if (toasts.length === 0) return null;
  return (
    <div className="toast-stack">
      {toasts.map((t) => (
        <div key={t.id} className="toast" onClick={() => dismissRef.current?.(t.id)}>
          <div className="toast-channel">↳ skill notify · {t.channel}</div>
          <div className="toast-msg">{t.message}</div>
        </div>
      ))}
    </div>
  );
};
