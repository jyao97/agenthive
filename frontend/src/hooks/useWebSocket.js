import { useEffect, useRef, useCallback, useState } from "react";
import { getAuthToken } from "../lib/api";

const MUTED_KEY = "agenthive-muted-agents";

/** Get the set of muted agent IDs from localStorage. */
function getMutedAgents() {
  try {
    const v = localStorage.getItem(MUTED_KEY);
    return v ? new Set(JSON.parse(v)) : new Set();
  } catch (err) {
    // Expected: localStorage may be unavailable in private browsing, or value may not be valid JSON
    console.warn("getMutedAgents: failed to read muted agents:", err);
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
/** Agents currently streaming a response. */
const _streamingAgents = new Set();
/** Deferred message notifications, flushed on stream end. */
const _pendingMessageNotifications = new Map();
/** Fallback timers in case stream_end is missed (e.g., reconnect). */
const _pendingMessageTimers = new Map();
const PENDING_FLUSH_DELAY_MS = 12000;
const PENDING_FLUSH_RETRY_MS = 5000;
const PENDING_FLUSH_MAX_WAIT_MS = 45000;

/** Call this when the user views an agent's chat to allow future notifications. */
export function clearAgentNotified(agentId) {
  _notifiedAgents.delete(agentId);
  clearPendingNotification(agentId);
}

function shouldSuppressNotification(agentId, allowRepeat = false) {
  if (!agentId) return false;
  if (window.location.pathname === `/agents/${agentId}`) return true;
  if (isAgentMuted(agentId)) return true;
  if (!allowRepeat && _notifiedAgents.has(agentId)) return true;
  return false;
}

function showNativeNotification(eventType, agentId, title, body, allowRepeat = false) {
  if (shouldSuppressNotification(agentId, allowRepeat)) return;
  if (agentId) _notifiedAgents.add(agentId);

  try {
    const tag = `${eventType}-${agentId || "unknown"}`;
    const n = new Notification(title, { body, tag, renotify: true });
    n.onclick = () => { window.focus(); n.close(); };
    setTimeout(() => n.close(), 8000);
  } catch (err) {
    // Expected: Notification constructor can throw if permissions change mid-session
    console.warn("showBrowserNotification: failed to create notification:", err);
  }
}

function clearPendingNotification(agentId) {
  _pendingMessageNotifications.delete(agentId);
  const timer = _pendingMessageTimers.get(agentId);
  if (timer) {
    clearTimeout(timer);
    _pendingMessageTimers.delete(agentId);
  }
}

function flushPendingNotification(agentId) {
  const pending = _pendingMessageNotifications.get(agentId);
  if (!pending) return;
  clearPendingNotification(agentId);
  showNativeNotification("new_message", agentId, pending.title, pending.body);
}

function schedulePendingFlush(agentId, delayMs) {
  const prevTimer = _pendingMessageTimers.get(agentId);
  if (prevTimer) clearTimeout(prevTimer);
  const timer = setTimeout(() => {
    _pendingMessageTimers.delete(agentId);
    const pending = _pendingMessageNotifications.get(agentId);
    if (!pending) return;
    const elapsed = Date.now() - (pending.deferredAt || Date.now());
    if (_streamingAgents.has(agentId) && elapsed < PENDING_FLUSH_MAX_WAIT_MS) {
      schedulePendingFlush(agentId, PENDING_FLUSH_RETRY_MS);
      return;
    }
    _streamingAgents.delete(agentId);
    flushPendingNotification(agentId);
  }, delayMs);
  _pendingMessageTimers.set(agentId, timer);
}

function deferPendingNotification(agentId, payload) {
  const existing = _pendingMessageNotifications.get(agentId);
  _pendingMessageNotifications.set(agentId, {
    ...payload,
    deferredAt: existing?.deferredAt || Date.now(),
  });
  schedulePendingFlush(agentId, PENDING_FLUSH_DELAY_MS);
}

/** Show a browser notification for relevant WebSocket events.
 *  Suppressed if the agent is muted, the user is viewing that agent's
 *  chat, or has already been notified for that agent (until they view it).
 *  New-message events are deferred while an agent is still streaming and
 *  shown only when stream end is observed. */
function showBrowserNotification(event) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;

  const d = event.data || {};
  const agentId = d.agent_id;

  // Seed streaming set on (re)connect so deferred notifications work
  // even if we missed earlier agent_stream events.
  if (event.type === "generating_agents") {
    const ids = d.agent_ids || [];
    for (const id of ids) _streamingAgents.add(id);
    return;
  }

  if (event.type === "agent_stream") {
    if (agentId) _streamingAgents.add(agentId);
    return;
  }

  if (event.type === "agent_stream_end") {
    if (!agentId) return;
    _streamingAgents.delete(agentId);
    flushPendingNotification(agentId);
    return;
  }

  if (event.type === "new_message") {
    if (shouldSuppressNotification(agentId)) return;
    const title = d.agent_name || `Agent ${agentId?.slice(0, 8)}`;
    const body = d.project ? `New message (${d.project})` : "New message";
    if (agentId && _streamingAgents.has(agentId)) {
      deferPendingNotification(agentId, { title, body });
      return;
    }
    showNativeNotification(event.type, agentId, title, body);
    return;
  }

  if (event.type === "agent_update" && d.status === "ERROR") {
    // Always surface errors even if we already notified for a prior message.
    if (agentId) {
      _streamingAgents.delete(agentId);
      clearPendingNotification(agentId);
      _notifiedAgents.delete(agentId);
    }
    const title = "Agent error";
    const body = d.agent_name || agentId?.slice(0, 8);
    showNativeNotification(event.type, agentId, title, body, true);
    return;
  }
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
          // Expected: untrusted input may not be valid JSON
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
    } catch (err) {
      // WebSocket constructor can throw if URL is invalid
      console.warn("useWebSocket: connection failed, will retry:", err);
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
