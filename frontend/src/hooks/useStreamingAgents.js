import { useState } from "react";
import { useWsEvent } from "./useWebSocket";

/**
 * Track which agents are actively working via WebSocket hook events.
 * Any hook event (tool_activity, agent_stream) → active.
 * Stop signal (agent_stream_end) or terminal status (agent_update) → inactive.
 * @returns {Set} activeAgents - Set of agent IDs currently active
 */
export function useStreamingAgents() {
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
    // Stop hook → inactive
    if (event.type === "agent_stream_end" && event.data?.agent_id) {
      const aid = event.data.agent_id;
      setActiveAgents((prev) => {
        if (!prev.has(aid)) return prev;
        const next = new Set(prev);
        next.delete(aid);
        return next;
      });
    }
    // Hook-driven tool activity → active
    if (event.type === "tool_activity" && event.data?.agent_id) {
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
    // Status update → add if EXECUTING, remove if terminal
    if (event.type === "agent_update" && event.data?.agent_id) {
      const aid = event.data.agent_id;
      const s = event.data.status;
      if (s === "EXECUTING") {
        setActiveAgents((prev) => {
          if (prev.has(aid)) return prev;
          const next = new Set(prev);
          next.add(aid);
          return next;
        });
      } else if (s !== "IDLE") {
        setActiveAgents((prev) => {
          if (!prev.has(aid)) return prev;
          const next = new Set(prev);
          next.delete(aid);
          return next;
        });
      }
    }
  });

  return activeAgents;
}
