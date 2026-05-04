/* global React, K */
// EventRail — the demo's narrative driver.
//
// Two side-by-side lists:
//   - Catalog (left): every available event. Active events stay in place, dimmed,
//     with their `+ Add` button repurposed as `× Remove` so the viewer can
//     deactivate from either side. Spatial memory holds — viewer's mental
//     map of "the email is the third event" doesn't shift.
//   - Event list (right): the active stack, sorted by `activation_seq`. Newer
//     activations land at the bottom; re-toggling an event lands it back at the
//     bottom with a fresh seq, which the resolver uses for tie-breaking.
//
// The cross-list move is animated with CSS transitions on the catalog row
// (saturation + a small "in play" badge) and the event-list row (slide-in /
// slide-out from the side). We keep this in plain CSS instead of an animation
// library — both lists stay small enough that React's reconciliation + a
// `transition` on the row level is enough.

const { useEffect, useState, useCallback } = React;

window.K.EventRail = function EventRail({ refresh, onChange }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
    try {
      const xs = await K.api.listCatalog();
      setEvents(xs);
      setError(null);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    K.api
      .listCatalog()
      .then((xs) => {
        if (!cancelled) {
          setEvents(xs);
          setError(null);
        }
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
  }, [refresh]);

  const onToggle = useCallback(
    async (id) => {
      if (busyId) return;
      setBusyId(id);
      try {
        await K.api.toggleCatalogEvent(id);
        await reload();
        if (onChange) onChange();
      } catch (err) {
        setError(err.message || String(err));
      } finally {
        setBusyId(null);
      }
    },
    [busyId, reload, onChange]
  );

  // Stable display order:
  //   - Catalog: by `catalog_order` ascending (demo author's intended menu).
  //   - Event list: by `activation_seq` ascending; newest activations sit at
  //     the bottom of the visible list.
  const catalogSorted = [...events].sort((a, b) => a.catalog_order - b.catalog_order);
  const activeSorted = events
    .filter((e) => e.active)
    .sort((a, b) => (a.activation_seq || 0) - (b.activation_seq || 0));

  return (
    <div className="panel event-rail">
      <div className="panel-head">
        <span className="panel-title">Catalog</span>
        <span className="panel-sub">Toggle events into the world</span>
        <span className="spacer" />
        <span className="panel-sub">
          {activeSorted.length}/{events.length} active
        </span>
      </div>
      <div className="panel-body event-rail-body">
        <div className="event-rail-col">
          <div className="event-rail-col-head">Available</div>
          {loading && (
            <div className="event-rail-empty">loading…</div>
          )}
          {!loading && error && (
            <div className="event-rail-empty event-rail-error">{error}</div>
          )}
          {!loading && !error && catalogSorted.length === 0 && (
            <div className="event-rail-empty">
              No events registered. Run <code>task reset-and-seed</code> or use
              the seed button.
            </div>
          )}
          {!loading &&
            catalogSorted.map((ev) => (
              <CatalogRow
                key={ev.id}
                event={ev}
                busy={busyId === ev.id}
                onToggle={onToggle}
              />
            ))}
        </div>
        <div className="event-rail-col event-rail-col-active">
          <div className="event-rail-col-head">In play</div>
          {!loading && activeSorted.length === 0 && (
            <div className="event-rail-empty">
              No active events. Click ＋ on the left to drop one into the world.
            </div>
          )}
          {activeSorted.map((ev) => (
            <ActiveRow
              key={ev.id}
              event={ev}
              busy={busyId === ev.id}
              onToggle={onToggle}
            />
          ))}
        </div>
      </div>
    </div>
  );
};

// One row in the left ("Available") column. Dims in place when active so
// spatial memory holds; the same row is the deactivation handle.
function CatalogRow({ event, busy, onToggle }) {
  return (
    <div
      className={K.cls(
        "event-rail-row",
        "event-rail-row-catalog",
        event.active && "event-rail-row-dimmed",
        busy && "event-rail-row-busy"
      )}
    >
      <span className="event-rail-row-icon">{iconForKind(event.kind)}</span>
      <span className="event-rail-row-text">
        <div className="event-rail-row-title">{event.title}</div>
        {event.description && (
          <div className="event-rail-row-desc">{event.description}</div>
        )}
      </span>
      <button
        className="event-rail-row-action"
        onClick={() => onToggle(event.id)}
        disabled={busy}
        title={event.active ? "Remove from world" : "Add to world"}
      >
        {busy ? "…" : event.active ? "× Remove" : "＋ Add"}
      </button>
      {event.active && <span className="event-rail-row-badge">in play</span>}
    </div>
  );
}

// One row in the right ("In play") column. Sorted by activation_seq so
// re-toggled events land at the bottom — visual reinforcement that the
// resolver now sees them as "newest".
function ActiveRow({ event, busy, onToggle }) {
  return (
    <div className={K.cls("event-rail-row", "event-rail-row-active", busy && "event-rail-row-busy")}>
      <span className="event-rail-row-seq">#{event.activation_seq}</span>
      <span className="event-rail-row-icon">{iconForKind(event.kind)}</span>
      <span className="event-rail-row-text">
        <div className="event-rail-row-title">{event.title}</div>
        {event.description && (
          <div className="event-rail-row-desc">{event.description}</div>
        )}
      </span>
      <button
        className="event-rail-row-action event-rail-row-action-remove"
        onClick={() => onToggle(event.id)}
        disabled={busy}
        title="Remove from world"
      >
        {busy ? "…" : "×"}
      </button>
    </div>
  );
}

function iconForKind(kind) {
  switch (kind) {
    case "ingest_document":
      return "📥";
    default:
      return "•";
  }
}
