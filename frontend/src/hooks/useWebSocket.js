import { useCallback, useEffect, useRef, useState } from "react";
import { useWebSocketContext } from "../contexts/WebSocketContext";
export * from "../lib/notifications";

/**
 * Subscribe to WebSocket events with a callback.
 * The callback is invoked synchronously for every event — no events
 * are lost to React 18 batching.
 *
 * @param {Function} handler - Called with each WebSocket event object
 * @param {Array} deps - Extra dependencies (handler is always latest via ref)
 */
export function useWsEvent(handler, deps = []) {
  const { subscribe } = useWebSocketContext();
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  useEffect(() => {
    return subscribe((event) => handlerRef.current(event));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscribe, ...deps]);
}

/**
 * Shared WebSocket hook — delegates to the single WebSocketProvider connection.
 * Provides `lastEvent` for simple consumers that only need the latest event.
 */
export default function useWebSocket() {
  const { subscribe, connected, sendWsMessage: ctxSend } = useWebSocketContext();

  // Derive lastEvent from the subscribe stream for backward compatibility.
  const [lastEvent, setLastEvent] = useState(null);
  useEffect(() => {
    return subscribe((event) => setLastEvent(event));
  }, [subscribe]);

  // Wrap sendWsMessage to handle the "viewing" convention
  const sendWsMessage = useCallback((data) => {
    ctxSend(data);
  }, [ctxSend]);

  return { lastEvent, connected, sendWsMessage };
}
