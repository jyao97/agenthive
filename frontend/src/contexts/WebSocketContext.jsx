import { createContext, useContext, useEffect, useRef, useCallback, useState } from "react";
import { getAuthToken } from "../lib/api";
import { calibrate } from "../lib/serverTime";

const WebSocketContext = createContext(null);

/**
 * Single shared WebSocket connection for the entire browser tab.
 * Place this provider near the root of the component tree (above all routes).
 *
 * Events are delivered via a subscriber/callback pattern so that rapid
 * successive WebSocket messages are never lost to React 18 batching
 * (which can collapse multiple setState calls into one render).
 */
export function WebSocketProvider({ children }) {
  const wsRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef(null);
  const reconnectDelay = useRef(1000);
  // Merged viewing state: union of all agent IDs currently being viewed
  // by any component (AgentChatPage panes). Backend receives the full set.
  const viewingAgentsRef = useRef(new Set());
  // Subscriber callbacks — called synchronously for every WS event
  const subscribersRef = useRef(new Set());

  // Register a callback that receives every WS event.
  // Returns an unsubscribe function.
  const subscribe = useCallback((handler) => {
    subscribersRef.current.add(handler);
    return () => subscribersRef.current.delete(handler);
  }, []);

  // Send a raw message to the server (if connected)
  const _send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === "string" ? data : JSON.stringify(data));
    }
  }, []);

  // Sync the backend about which agents are currently being viewed
  const _syncViewing = useCallback(() => {
    const agents = viewingAgentsRef.current;
    const ids = document.visibilityState === "visible" ? [...agents] : [];
    _send({ type: "viewing", agent_ids: ids });
  }, [_send]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const token = getAuthToken();
    if (!token) {
      // No auth token available — skip connection attempt and retry later.
      // Without a token the server rejects with 403, creating a spam loop.
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(reconnectDelay.current * 1.5, 30000);
        connect();
      }, reconnectDelay.current);
      return;
    }

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws/status?token=${encodeURIComponent(token)}`;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        reconnectDelay.current = 1000;
        _syncViewing();
      };

      ws.onmessage = (e) => {
        let event;
        try {
          event = JSON.parse(e.data);
        } catch {
          return; // untrusted input may not be valid JSON
        }
        if (event.type === "pong" || event.type === "ping") return;
        // Calibrate client-server clock offset from every event timestamp
        if (event.timestamp) calibrate(event.timestamp);
        // Deliver to all subscribers synchronously — each callback runs
        // for every event, bypassing React batching entirely.
        for (const fn of subscribersRef.current) {
          try {
            fn(event);
          } catch (err) {
            console.error("WebSocket subscriber error:", err);
          }
        }
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        reconnectTimer.current = setTimeout(() => {
          reconnectDelay.current = Math.min(reconnectDelay.current * 1.5, 30000);
          connect();
        }, reconnectDelay.current);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch (err) {
      console.warn("WebSocketProvider: connection failed, will retry:", err);
      reconnectTimer.current = setTimeout(connect, reconnectDelay.current);
    }
  }, [_syncViewing]);

  useEffect(() => {
    connect();

    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send("ping");
      }
    }, 30000);

    const onVisibilityChange = () => {
      _syncViewing();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      clearInterval(pingInterval);
      clearTimeout(reconnectTimer.current);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect, _syncViewing]);

  // Public API for components to send messages
  const sendWsMessage = useCallback((data) => {
    if (typeof data === "object" && data.type === "viewing") {
      // Track viewing state centrally
      const agentId = data.agent_id;
      if (agentId) {
        viewingAgentsRef.current.add(agentId);
      } else {
        // null means "stop viewing" — components pass their own agent_id
        // on unmount, so we need a way to remove specific IDs.
        // Convention: { type: "viewing", agent_id: null, _unview: "xxx" }
        const toRemove = data._unview;
        if (toRemove) viewingAgentsRef.current.delete(toRemove);
      }
      _syncViewing();
      return;
    }
    _send(data);
  }, [_send, _syncViewing]);

  return (
    <WebSocketContext.Provider value={{ subscribe, connected, sendWsMessage }}>
      {children}
    </WebSocketContext.Provider>
  );
}

// Safe fallback so components don't crash if rendered outside the provider
// (e.g. during HMR transitions or stale service-worker cache).
const _fallback = {
  subscribe: () => () => {},
  connected: false,
  sendWsMessage: () => {},
};

export function useWebSocketContext() {
  const ctx = useContext(WebSocketContext);
  if (!ctx) {
    console.warn("useWebSocketContext: no provider found, using fallback (stale cache?)");
    return _fallback;
  }
  return ctx;
}
