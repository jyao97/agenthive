import { createContext, useContext, useEffect, useRef, useCallback, useMemo, useSyncExternalStore } from "react";
import { useWebSocketContext } from "./WebSocketContext";

/**
 * Global executing-agents tracker.
 *
 * Start events (→ executing): agent_update(EXECUTING), agent_stream, tool_activity, generating_agents seed
 * Stop events  (→ idle):      agent_stream_end, agent_update(terminal)
 *
 * Single global Set shared across all pages — survives navigation.
 */

const ExecutingAgentsContext = createContext(null);

export function ExecutingAgentsProvider({ children }) {
  const { subscribe } = useWebSocketContext();

  const activeRef = useRef(new Set());
  const listenersRef = useRef(new Set());

  const notify = useCallback(() => {
    for (const fn of listenersRef.current) {
      try { fn(); } catch { /* ignore */ }
    }
  }, []);

  const add = useCallback((id) => {
    if (activeRef.current.has(id)) return;
    const next = new Set(activeRef.current);
    next.add(id);
    activeRef.current = next;
    notify();
  }, [notify]);

  const remove = useCallback((id) => {
    if (!activeRef.current.has(id)) return;
    const next = new Set(activeRef.current);
    next.delete(id);
    activeRef.current = next;
    notify();
  }, [notify]);

  useEffect(() => {
    return subscribe((event) => {
      const aid = event.data?.agent_id;

      // Start signals → executing
      if (event.type === "agent_stream" && aid) { add(aid); return; }
      if (event.type === "tool_activity" && aid) { add(aid); return; }
      if (event.type === "generating_agents" && event.data?.agent_ids) {
        for (const id of event.data.agent_ids) add(id);
        return;
      }
      if (event.type === "agent_update" && aid) {
        const s = event.data.status;
        if (s === "EXECUTING") add(aid);
        else if (s !== "IDLE") remove(aid); // terminal
        return;
      }

      // Stop signal → idle
      if (event.type === "agent_stream_end" && aid) { remove(aid); return; }
    });
  }, [subscribe, add, remove]);

  const subscribeStore = useCallback((fn) => {
    listenersRef.current.add(fn);
    return () => listenersRef.current.delete(fn);
  }, []);
  const getSnapshot = useCallback(() => activeRef.current, []);

  const value = useMemo(() => ({ subscribeStore, getSnapshot }), [subscribeStore, getSnapshot]);

  return (
    <ExecutingAgentsContext.Provider value={value}>
      {children}
    </ExecutingAgentsContext.Provider>
  );
}

export function useExecutingAgents() {
  const ctx = useContext(ExecutingAgentsContext);
  return useSyncExternalStore(ctx.subscribeStore, ctx.getSnapshot);
}
