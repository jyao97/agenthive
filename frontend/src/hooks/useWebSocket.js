import { useCallback } from "react";
import { useWebSocketContext } from "../contexts/WebSocketContext";
export * from "../lib/notifications";

/**
 * Shared WebSocket hook — delegates to the single WebSocketProvider connection.
 * API-compatible with the old per-component hook.
 */
export default function useWebSocket() {
  const { lastEvent, connected, sendWsMessage: ctxSend } = useWebSocketContext();

  // Wrap sendWsMessage to handle the "viewing" convention:
  // Components send { type: "viewing", agent_id } on mount
  // and { type: "viewing", agent_id: null } on unmount.
  // We translate the unmount case to include _unview so the provider
  // can remove the specific agent from the viewing set.
  const sendWsMessage = useCallback((data) => {
    if (typeof data === "object" && data.type === "viewing" && data.agent_id === null) {
      // Unmount: the previous agent_id was sent on mount — we can't know it here.
      // Components should use the new unview pattern, but for backwards compat
      // we just forward it (the provider handles null).
      ctxSend(data);
      return;
    }
    ctxSend(data);
  }, [ctxSend]);

  return { lastEvent, connected, sendWsMessage };
}
