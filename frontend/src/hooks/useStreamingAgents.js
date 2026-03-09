import { useState, useEffect } from "react";

/**
 * Track which agents are actively streaming via WebSocket events + API is_generating.
 * @param {Array} agents - Array of agent objects (must have .id, .status, .is_generating)
 * @param {Object|null} lastEvent - Latest WebSocket event from useWebSocket()
 * @returns {Set} streamingAgents - Set of agent IDs currently streaming
 */
export function useStreamingAgents(agents, lastEvent) {
  const [streamingAgents, setStreamingAgents] = useState(new Set());

  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.type === "agent_stream" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      setStreamingAgents((prev) => {
        if (prev.has(aid)) return prev;
        const next = new Set(prev);
        next.add(aid);
        return next;
      });
    }
    // Deterministic end signal from backend
    if (lastEvent.type === "agent_stream_end" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      setStreamingAgents((prev) => {
        if (!prev.has(aid)) return prev;
        const next = new Set(prev);
        next.delete(aid);
        return next;
      });
    }
    // Seed from backend on connect/reconnect
    if (lastEvent.type === "generating_agents" && lastEvent.data?.agent_ids) {
      const ids = lastEvent.data.agent_ids;
      setStreamingAgents((prev) => {
        const next = new Set(prev);
        for (const id of ids) next.add(id);
        if (next.size === prev.size) return prev;
        return next;
      });
    }
    // Clear streaming on status change away from active states
    if (lastEvent.type === "agent_update" && lastEvent.data?.agent_id) {
      const aid = lastEvent.data.agent_id;
      const s = lastEvent.data.status;
      if (s !== "EXECUTING" && s !== "SYNCING") {
        setStreamingAgents((prev) => {
          if (!prev.has(aid)) return prev;
          const next = new Set(prev);
          next.delete(aid);
          return next;
        });
      }
    }
  }, [lastEvent]);

  // Seed streaming state from API is_generating on poll
  useEffect(() => {
    if (!agents.length) return;
    setStreamingAgents((prev) => {
      const apiGenerating = new Set(agents.filter((a) => a.is_generating).map((a) => a.id));
      // Merge: keep WS-derived streaming that API hasn't caught yet, add API-derived
      const next = new Set([...prev, ...apiGenerating]);
      // Remove agents API says are not generating AND no recent WS stream
      for (const aid of prev) {
        if (!apiGenerating.has(aid)) {
          // Keep if agent is still EXECUTING/SYNCING (WS might be ahead of poll)
          const ag = agents.find((a) => a.id === aid);
          if (!ag || (ag.status !== "EXECUTING" && ag.status !== "SYNCING")) {
            next.delete(aid);
          }
        }
      }
      if (next.size === prev.size && [...next].every((a) => prev.has(a))) return prev;
      return next;
    });
  }, [agents]);

  return streamingAgents;
}
