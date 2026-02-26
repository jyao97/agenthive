import { useEffect, useRef, useCallback, useState } from "react";
import { getAuthToken } from "../lib/api";

const MUTED_KEY = "agenthive-muted-agents";

/** Get the set of muted agent IDs from localStorage. */
function getMutedAgents() {
  try {
    const v = localStorage.getItem(MUTED_KEY);
    return v ? new Set(JSON.parse(v)) : new Set();
  } catch {
    return new Set();
  }
}

/** Check if a specific agent is muted. */
export function isAgentMuted(agentId) {
  return getMutedAgents().has(agentId);
}

/** Set mute state for a specific agent. */
export function setAgentMuted(agentId, muted) {
  const set = getMutedAgents();
  if (muted) set.add(agentId);
  else set.delete(agentId);
  localStorage.setItem(MUTED_KEY, JSON.stringify([...set]));
}

/** Tracks which agents have already shown a notification.
 *  Cleared when the user navigates to that agent's chat page. */
const _notifiedAgents = new Set();

/** Call this when the user views an agent's chat to allow future notifications. */
export function clearAgentNotified(agentId) {
  _notifiedAgents.delete(agentId);
}

/** Show a browser notification for relevant WebSocket events.
 *  Suppressed if the agent is muted, the user is viewing that agent's
 *  chat, or has already been notified for that agent (until they view it). */
function showBrowserNotification(event) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;

  const d = event.data || {};
  let title, body;

  if (event.type === "new_message") {
    if (d.agent_id && window.location.pathname === `/agents/${d.agent_id}`) return;
    if (d.agent_id && isAgentMuted(d.agent_id)) return;
    if (d.agent_id && _notifiedAgents.has(d.agent_id)) return;
    title = d.agent_name || `Agent ${d.agent_id?.slice(0, 8)}`;
    body = d.project ? `New message (${d.project})` : "New message";
  } else if (event.type === "agent_update") {
    if (d.agent_id && window.location.pathname === `/agents/${d.agent_id}`) return;
    if (d.agent_id && isAgentMuted(d.agent_id)) return;
    const s = d.status;
    // Always notify for IDLE/ERROR even if previously notified
    if (s === "IDLE") { title = "Agent done"; body = d.agent_name || d.agent_id?.slice(0, 8); }
    else if (s === "ERROR") { title = "Agent error"; body = d.agent_name || d.agent_id?.slice(0, 8); }
    else return;
    _notifiedAgents.delete(d.agent_id); // allow re-notification after status change
  } else {
    return;
  }

  if (d.agent_id) _notifiedAgents.add(d.agent_id);

  try {
    const tag = `${event.type}-${d.agent_id}`;
    const n = new Notification(title, { body, tag, renotify: true });
    n.onclick = () => { window.focus(); n.close(); };
    setTimeout(() => n.close(), 8000);
  } catch { /* ignore */ }
}

/**
 * Auto-reconnecting WebSocket hook for real-time status updates.
 *
 * Usage:
 *   const { lastEvent, connected } = useWebSocket();
 *   useEffect(() => { if (lastEvent?.type === "task_update") refresh(); }, [lastEvent]);
 */
export default function useWebSocket() {
  const wsRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);
  const reconnectTimer = useRef(null);
  const reconnectDelay = useRef(1000);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    let url = `${proto}//${window.location.host}/ws/status`;
    const token = getAuthToken();
    if (token) {
      url += `?token=${encodeURIComponent(token)}`;
    }

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        reconnectDelay.current = 1000; // Reset backoff
      };

      ws.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          if (event.type !== "pong") {
            setLastEvent(event);
            showBrowserNotification(event);
          }
        } catch {
          // ignore non-JSON
        }
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        // Reconnect with exponential backoff (max 30s)
        reconnectTimer.current = setTimeout(() => {
          reconnectDelay.current = Math.min(reconnectDelay.current * 1.5, 30000);
          connect();
        }, reconnectDelay.current);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      // WebSocket constructor can throw if URL is invalid
      reconnectTimer.current = setTimeout(connect, reconnectDelay.current);
    }
  }, []);

  useEffect(() => {
    connect();

    // Send ping every 30s to keep connection alive
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send("ping");
      }
    }, 30000);

    return () => {
      clearInterval(pingInterval);
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect]);

  const sendWsMessage = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === "string" ? data : JSON.stringify(data));
    }
  }, []);

  return { lastEvent, connected, sendWsMessage };
}
