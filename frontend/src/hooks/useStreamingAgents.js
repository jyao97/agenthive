import { useState, useEffect, useRef } from "react";

// Grace period (ms) after last tool_activity before clearing active state.
// Covers compact/thinking gaps where no events fire.
const HOOK_GRACE_MS = 30_000;

/**
 * Track which agents are actively working via WebSocket events + API is_generating.
 * Combines agent_stream, tool_activity, and is_generating signals.
 * @param {Array} agents - Array of agent objects (must have .id, .status, .is_generating)
 * @param {Object|null} lastEvent - Latest WebSocket event from useWebSocket()
 * @returns {Set} activeAgents - Set of agent IDs currently active (streaming or hook-active)
 */
export function useStreamingAgents(agents, lastEvent) {
  const [activeAgents, setActiveAgents] = useState(new Set());
  // Per-agent grace timers for hook activity
  const graceTimers = useRef(new Map());

  useEffect(() => {
    if (!lastEvent) return;

    // Streaming content → definitely active
    if (lastEvent.type === "agent_stream" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      setActiveAgents((prev) => {
        if (prev.has(aid)) return prev;
        const next = new Set(prev);
        next.add(aid);
        return next;
      });
    }
    // Deterministic end signal from backend
    if (lastEvent.type === "agent_stream_end" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      setActiveAgents((prev) => {
        if (!prev.has(aid)) return prev;
        const next = new Set(prev);
        next.delete(aid);
        return next;
      });
    }
    // Hook-driven tool activity → agent is working
    if (lastEvent.type === "tool_activity" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      setActiveAgents((prev) => {
        if (prev.has(aid)) return prev;
        const next = new Set(prev);
        next.add(aid);
        return next;
      });
      // Reset grace timer — keep active for HOOK_GRACE_MS after last event
      clearTimeout(graceTimers.current.get(aid));
      if (lastEvent.data.phase === "end") {
        graceTimers.current.set(aid, setTimeout(() => {
          graceTimers.current.delete(aid);
          setActiveAgents((prev) => {
            if (!prev.has(aid)) return prev;
            const next = new Set(prev);
            next.delete(aid);
            return next;
          });
        }, HOOK_GRACE_MS));
      }
    }
    // New message committed → agent finished this turn (unless mid-compact)
    if (lastEvent.type === "new_message" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      // If a grace timer is running, the agent was recently doing tool calls —
      // this new_message is likely a compact system message, not a real turn end.
      // Let the grace timer handle cleanup instead.
      if (!graceTimers.current.has(aid)) {
        setActiveAgents((prev) => {
          if (!prev.has(aid)) return prev;
          const next = new Set(prev);
          next.delete(aid);
          return next;
        });
      }
    }
    // Seed from backend on connect/reconnect
    if (lastEvent.type === "generating_agents" && lastEvent.data?.agent_ids) {
      const ids = lastEvent.data.agent_ids;
      setActiveAgents((prev) => {
        const next = new Set(prev);
        for (const id of ids) next.add(id);
        if (next.size === prev.size) return prev;
        return next;
      });
    }
    // Clear on status change away from active states
    if (lastEvent.type === "agent_update" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      const s = lastEvent.data.status;
      if (s !== "EXECUTING" && s !== "SYNCING") {
        clearTimeout(graceTimers.current.get(aid));
        graceTimers.current.delete(aid);
        setActiveAgents((prev) => {
          if (!prev.has(aid)) return prev;
          const next = new Set(prev);
          next.delete(aid);
          return next;
        });
      }
    }
  }, [lastEvent]);

  // Seed from API is_generating on poll
  useEffect(() => {
    if (!agents.length) return;
    setActiveAgents((prev) => {
      const apiGenerating = new Set(agents.filter((a) => a.is_generating).map((a) => a.id));
      const next = new Set([...prev, ...apiGenerating]);
      for (const aid of prev) {
        if (!apiGenerating.has(aid)) {
          const ag = agents.find((a) => a.id === aid);
          // Keep if grace timer is running or agent is still active status
          if (!ag || (ag.status !== "EXECUTING" && ag.status !== "SYNCING" && !graceTimers.current.has(aid))) {
            next.delete(aid);
          }
        }
      }
      if (next.size === prev.size && [...next].every((a) => prev.has(a))) return prev;
      return next;
    });
  }, [agents]);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      for (const t of graceTimers.current.values()) clearTimeout(t);
    };
  }, []);

  return activeAgents;
}
