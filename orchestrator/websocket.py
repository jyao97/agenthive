"""WebSocket hub — broadcasts real-time events to connected clients."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("orchestrator.ws")


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info("WebSocket client connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info("WebSocket client disconnected (%d total)", len(self.active))

    async def broadcast(self, event_type: str, data: dict):
        """Send an event to all connected clients."""
        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        disconnected = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)


# Singleton manager
ws_manager = ConnectionManager()


async def websocket_endpoint(ws: WebSocket):
    """WebSocket handler for /ws/status."""
    await ws_manager.connect(ws)
    try:
        while True:
            # Keep connection alive; client may send pings
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


# ---- Event helpers (called from dispatcher/main) ----

async def emit_task_update(task_id: str, status: str, project: str):
    await ws_manager.broadcast("task_update", {
        "task_id": task_id,
        "status": status,
        "project": project,
    })


async def emit_worker_update(action: str, container_name: str, project: str = ""):
    await ws_manager.broadcast("worker_update", {
        "action": action,  # "created" | "destroyed"
        "container": container_name,
        "project": project,
    })


async def emit_plan_ready(task_id: str, project: str):
    await ws_manager.broadcast("plan_ready", {
        "task_id": task_id,
        "project": project,
    })


async def emit_system_alert(message: str, level: str = "warning"):
    await ws_manager.broadcast("system_alert", {
        "message": message,
        "level": level,
    })
