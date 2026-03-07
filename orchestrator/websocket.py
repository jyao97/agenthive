"""WebSocket hub — broadcasts real-time events to connected clients."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger("orchestrator.ws")

# Timeout for individual WS send operations (seconds)
_SEND_TIMEOUT = 5


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    def __init__(self):
        self.active: list[WebSocket] = []
        # Track which agent each WS client is currently viewing
        self._viewing: dict[WebSocket, str | None] = {}

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        self._viewing[ws] = None
        logger.info("WebSocket client connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        self._viewing.pop(ws, None)
        logger.info("WebSocket client disconnected (%d total)", len(self.active))

    def set_viewing(self, ws: WebSocket, agent_id: str | None):
        """Record which agent a client is currently viewing."""
        self._viewing[ws] = agent_id

    def is_agent_viewed(self, agent_id: str) -> bool:
        """True if any connected client is currently viewing this agent."""
        return any(v == agent_id for v in self._viewing.values())

    async def broadcast(self, event_type: str, data: dict) -> int:
        """Send an event to all connected clients. Returns count of successful sends."""
        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Send to all clients in parallel with per-client timeout
        # so one stale connection doesn't block the rest.
        async def _send(ws):
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=_SEND_TIMEOUT)
                return ws, True
            except Exception:
                return ws, False

        results = await asyncio.gather(*[_send(ws) for ws in list(self.active)])
        disconnected = [ws for ws, ok in results if not ok]
        for ws in disconnected:
            self.disconnect(ws)
        return sum(1 for _, ok in results if ok)

    async def prune_stale(self):
        """Ping all clients and remove any that fail to respond."""
        ping_msg = json.dumps({"type": "ping"})
        disconnected = []
        for ws in list(self.active):
            try:
                await asyncio.wait_for(ws.send_text(ping_msg), timeout=_SEND_TIMEOUT)
            except Exception:
                disconnected.append(ws)
        if disconnected:
            for ws in disconnected:
                self.disconnect(ws)
            logger.info("Pruned %d stale WebSocket connections", len(disconnected))


# Singleton manager
ws_manager = ConnectionManager()


async def websocket_endpoint(ws: WebSocket):
    """WebSocket handler for /ws/status. Requires ?token=<jwt> if password is set."""
    # Auth check — verify token if password protection is enabled
    from database import SessionLocal
    from auth import get_password_hash, get_jwt_secret, verify_token

    db = SessionLocal()
    try:
        pw_hash = get_password_hash(db)
        if pw_hash is not None:
            token = ws.query_params.get("token", "")
            jwt_secret = get_jwt_secret(db)
            if not token or not verify_token(token, jwt_secret):
                await ws.close(code=4001, reason="Unauthorized")
                return
    finally:
        db.close()

    await ws_manager.connect(ws)

    # Send currently-generating agents so the client can seed its streaming
    # set and defer notifications correctly even after a reconnect.
    try:
        ad = getattr(ws.app.state, "agent_dispatcher", None)
        generating = list(ad._generating_agents) if ad else []
        if generating:
            await ws.send_text(json.dumps({
                "type": "generating_agents",
                "data": {"agent_ids": generating},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))
    except Exception:
        logger.debug("Failed to send generating_agents on connect", exc_info=True)

    try:
        while True:
            # Keep connection alive; client may send pings or viewing updates
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            else:
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "viewing":
                        ws_manager.set_viewing(ws, msg.get("agent_id"))
                except json.JSONDecodeError:
                    logger.debug("WS received invalid JSON: %s", data[:100])
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        logger.warning("WebSocket handler error", exc_info=True)
        ws_manager.disconnect(ws)


# ---- Event helpers (called from dispatcher/main) ----

async def emit_task_update(task_id: str, status: str, project: str,
                           title: str | None = None, agent_id: str | None = None):
    payload = {
        "task_id": task_id,
        "status": status,
        "project": project,
    }
    if title is not None:
        payload["title"] = title
    if agent_id is not None:
        payload["agent_id"] = agent_id
    await ws_manager.broadcast("task_update", payload)


async def emit_worker_update(action: str, process_name: str, project: str = ""):
    await ws_manager.broadcast("worker_update", {
        "action": action,  # "created" | "destroyed"
        "process": process_name,
        "project": project,
    })


async def emit_system_alert(message: str, level: str = "warning"):
    await ws_manager.broadcast("system_alert", {
        "message": message,
        "level": level,
    })


async def emit_agent_update(agent_id: str, status: str, project: str):
    await ws_manager.broadcast("agent_update", {
        "agent_id": agent_id,
        "status": status,
        "project": project,
    })


async def emit_new_message(agent_id: str, message_id: str,
                           agent_name: str = "", project: str = ""):
    await ws_manager.broadcast("new_message", {
        "agent_id": agent_id,
        "message_id": message_id,
        "agent_name": agent_name,
        "project": project,
    })


async def emit_message_update(agent_id: str, message_id: str, status: str,
                              error_message: str | None = None):
    """Notify clients that a message's status changed (e.g. PENDING→EXECUTING)."""
    payload: dict = {
        "agent_id": agent_id,
        "message_id": message_id,
        "status": status,
    }
    if error_message:
        payload["error_message"] = error_message
    await ws_manager.broadcast("message_update", payload)


async def emit_agent_stream(agent_id: str, content: str,
                            generation_id: int | None = None,
                            active_tool: dict | None = None):
    """Send incremental streaming content for an executing agent."""
    payload: dict = {
        "agent_id": agent_id,
        "content": content,
    }
    if generation_id is not None:
        payload["generation_id"] = generation_id
    if active_tool is not None:
        payload["active_tool"] = active_tool
    await ws_manager.broadcast("agent_stream", payload)


async def emit_agent_stream_end(agent_id: str,
                                generation_id: int | None = None):
    """Signal that an agent's current generation has finished."""
    payload: dict = {"agent_id": agent_id}
    if generation_id is not None:
        payload["generation_id"] = generation_id
    await ws_manager.broadcast("agent_stream_end", payload)
