import { useState, useEffect } from "react";
import { useWsEvent } from "./useWebSocket";

/**
 * Track which agents are actively working via WebSocket events.
 * agent_update is the authoritative signal — active iff status is EXECUTING or SYNCING.
 * tool_activity and agent_stream add the agent immediately when work starts.
 * @param {Array} agents - Array of agent objects (must have .id, .status)
 * @returns {Set} activeAgents - Set of agent IDs currently active
 */
export function useStreamingAgents(agents) {
  const [activeAgents, setActiveAgents] = useState(new Set());

  useWsEvent((event) => {
    // Streaming content → active
    if (event.type === "agent_stream" && event.data?.agent_id) {
      const aid = event.data.agent_id;
      setActiveAgents((prev) => {
        if (prev.has(aid)) return prev;
        const next = new Set(prev);
        next.add(aid);
        return next;
      });
    }
    // Stream ended (Stop hook) → inactive
    if (event.type === "agent_stream_end" && event.data?.agent_id) {
      const aid = event.data.agent_id;
      setActiveAgents((prev) => {
        if (!prev.has(aid)) return prev;
        const next = new Set(prev);
        next.delete(aid);
        return next;
      });
    }
    // Hook-driven tool activity → active on start only
    if (event.type === "tool_activity" && event.data?.agent_id && event.data?.phase === "start") {
      const aid = event.data.agent_id;
      setActiveAgents((prev) => {
        if (prev.has(aid)) return prev;
        const next = new Set(prev);
        next.add(aid);
        return next;
      });
    }
    // Seed from backend on connect/reconnect
    if (event.type === "generating_agents" && event.data?.agent_ids) {
      setActiveAgents((prev) => {
        const next = new Set(prev);
        for (const id of event.data.agent_ids) next.add(id);
        if (next.size === prev.size) return prev;
        return next;
      });
    }
    // agent_update is the authoritative clear signal
    if (event.type === "agent_update" && event.data?.agent_id) {
      const aid = event.data.agent_id;
      const s = event.data.status;
      if (s !== "EXECUTING" && s !== "SYNCING") {
        setActiveAgents((prev) => {
          if (!prev.has(aid)) return prev;
          const next = new Set(prev);
          next.delete(aid);
          return next;
        });
      }
    }
  });

  // Sync with API status on poll.
  // EXECUTING agents are always active. SYNCING agents are only active if
  // is_generating is set (backend tracks this via Stop hook clearing).
  // This prevents idle SYNCING agents from permanently showing "executing".
  useEffect(() => {
    if (!agents.length) return;
    setActiveAgents((prev) => {
      const next = new Set(prev);
      for (const ag of agents) {
        if (ag.status === "EXECUTING" || (ag.status === "SYNCING" && ag.is_generating)) {
          next.add(ag.id);
        } else {
          next.delete(ag.id);
        }
      }
      if (next.size === prev.size && [...next].every((a) => prev.has(a))) return prev;
      return next;
    });
  }, [agents]);

  return activeAgents;
}
