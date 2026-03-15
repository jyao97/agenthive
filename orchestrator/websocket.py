"""WebSocket hub — broadcasts real-time events to connected clients."""

import asyncio
import json
import logging
import os
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
        # Track which agents each WS client is currently viewing
        self._viewing: dict[WebSocket, set[str]] = {}

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        self._viewing[ws] = set()
        logger.info("WebSocket client connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        self._viewing.pop(ws, None)
        logger.info("WebSocket client disconnected (%d total)", len(self.active))

    def set_viewing(self, ws: WebSocket, agent_ids: set[str]):
        """Record which agents a client is currently viewing."""
        self._viewing[ws] = agent_ids

    def is_agent_viewed(self, agent_id: str) -> bool:
        """True if any connected client is currently viewing this agent."""
        return any(agent_id in v for v in self._viewing.values())

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
                logger.debug("WS send failed for %s", ws.client, exc_info=True)
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
                logger.debug("WS ping failed for %s", ws.client, exc_info=True)
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

    if os.environ.get("DISABLE_AUTH", "").strip() not in ("1", "true", "yes"):
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
                        ids = msg.get("agent_ids")
                        if ids is not None:
                            ws_manager.set_viewing(ws, set(ids))
                        else:
                            aid = msg.get("agent_id")
                            ws_manager.set_viewing(ws, {aid} if aid else set())
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


async def emit_message_delivered(agent_id: str, message_id: str,
                                 delivered_at: str):
    """Notify clients that a web-sent message was delivered to Claude."""
    await ws_manager.broadcast("message_delivered", {
        "agent_id": agent_id,
        "message_id": message_id,
        "delivered_at": delivered_at,
    })


async def emit_message_update(agent_id: str, message_id: str, status: str,
                              error_message: str | None = None,
                              completed_at: str | None = None):
    """Notify clients that a message's status changed (e.g. PENDING→EXECUTING)."""
    payload: dict = {
        "agent_id": agent_id,
        "message_id": message_id,
        "status": status,
    }
    if error_message:
        payload["error_message"] = error_message
    if completed_at:
        payload["completed_at"] = completed_at
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


async def emit_tool_activity(agent_id: str, tool_name: str, phase: str,
                              tool_input: dict | None = None,
                              tool_output: str | None = None,
                              is_error: bool = False):
    """Broadcast tool/subagent/permission events driven by CC hooks."""
    payload: dict = {
        "agent_id": agent_id,
        "tool_name": tool_name,
        "phase": phase,  # "start", "end", or "permission"
    }
    if tool_input:
        summary = _tool_input_summary(tool_name, tool_input)
        if summary:
            payload["summary"] = summary
    if phase == "end" and tool_output is not None:
        payload["output_summary"] = _tool_output_summary(tool_name, tool_output, is_error)
    if is_error:
        payload["is_error"] = True
    await ws_manager.broadcast("tool_activity", payload)


def _tool_input_summary(tool_name: str, tool_input: dict) -> str:
    """Build a short human-readable summary from tool input."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:120] if cmd else ""
    if tool_name in ("Read", "Write", "Edit"):
        return tool_input.get("file_path", "")[:120]
    if tool_name in ("Glob", "Grep"):
        return tool_input.get("pattern", "")[:80]
    if tool_name == "Agent":
        return tool_input.get("description", "")[:80]
    if tool_name in ("WebFetch", "WebSearch"):
        return (tool_input.get("url") or tool_input.get("query") or "")[:120]
    return ""


def _tool_output_summary(tool_name: str, output: str, is_error: bool) -> str:
    """Build a short result summary from tool output."""
    if is_error:
        # First meaningful line of error
        for line in output.strip().splitlines():
            line = line.strip()
            if line:
                return f"error: {line[:100]}"
        return "error"

    if tool_name == "Bash":
        lines = output.strip().splitlines()
        if not lines:
            return "done (no output)"
        if len(lines) == 1:
            return lines[0][:120]
        return f"{lines[-1][:80]} ({len(lines)} lines)"
    if tool_name == "Read":
        lines = output.strip().splitlines()
        return f"{len(lines)} lines"
    if tool_name == "Grep":
        lines = output.strip().splitlines()
        if not lines:
            return "no matches"
        return f"{len(lines)} matches"
    if tool_name == "Glob":
        lines = [l for l in output.strip().splitlines() if l.strip()]
        if not lines:
            return "no files"
        return f"{len(lines)} files"
    if tool_name == "Agent":
        # Agent output can be huge — just indicate completion
        return "done"
    if tool_name in ("Edit", "Write"):
        return "done"
    # Generic fallback
    length = len(output)
    if length < 50:
        return output.strip()[:50] or "done"
    return f"done ({length} chars)"
