"""In-memory per-tool permission manager for non-skip-permissions agents.

When an agent runs without --dangerously-skip-permissions, the PreToolUse
HTTP hook blocks until the user approves or denies each tool call from the
web UI.  This module manages the pending requests, "always allow" session
rules, and the asyncio event gates that unblock the hook handler.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("orchestrator.permissions")

# Tools that are always safe — auto-allow even for supervised agents.
# These are read-only and can't modify the filesystem.
SAFE_TOOLS = frozenset({
    "Read", "Glob", "Grep", "WebSearch", "WebFetch",
    "TodoRead", "Task", "TaskOutput",
})


@dataclass
class PermissionRequest:
    id: str
    agent_id: str
    tool_name: str
    tool_input: dict
    summary: str
    created_at: float = field(default_factory=time.time)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: str | None = None   # "allow" / "deny"
    reason: str | None = None
    updated_input: dict | None = None   # updatedInput payload for AskUserQuestion answers


class PermissionManager:
    """Manages pending tool-permission requests and per-agent session rules."""

    def __init__(self):
        self._pending: dict[str, PermissionRequest] = {}     # request_id → request
        self._always_allow: dict[str, set[str]] = {}         # agent_id → {"Bash", "Edit", ...}

    # ------------------------------------------------------------------
    # Always-allow rules
    # ------------------------------------------------------------------
    def add_always_allow(self, agent_id: str, tool_name: str):
        """Add a tool to the always-allow set for this agent's session."""
        self._always_allow.setdefault(agent_id, set()).add(tool_name)
        logger.info("permissions: always-allow %s for agent %s", tool_name, agent_id[:8])

    def check_always_allow(self, agent_id: str, tool_name: str) -> bool:
        """Check if this tool is always-allowed for the agent."""
        rules = self._always_allow.get(agent_id)
        if not rules:
            return False
        return tool_name in rules

    def clear_agent(self, agent_id: str):
        """Remove all rules and deny pending requests for a stopped agent."""
        self._always_allow.pop(agent_id, None)
        # Deny any pending requests (agent is stopping)
        to_remove = [rid for rid, req in self._pending.items() if req.agent_id == agent_id]
        for rid in to_remove:
            req = self._pending.pop(rid)
            req.decision = "deny"
            req.reason = "Agent stopped"
            req.event.set()
        if to_remove:
            logger.info("permissions: cleared %d pending for agent %s", len(to_remove), agent_id[:8])

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------
    def create_request(
        self, agent_id: str, tool_name: str, tool_input: dict, summary: str,
    ) -> PermissionRequest:
        """Create a pending permission request. Returns immediately."""
        import secrets
        request_id = secrets.token_hex(8)
        req = PermissionRequest(
            id=request_id,
            agent_id=agent_id,
            tool_name=tool_name,
            tool_input=tool_input,
            summary=summary,
        )
        self._pending[request_id] = req
        logger.info(
            "permissions: created request %s for agent %s tool=%s",
            request_id, agent_id[:8], tool_name,
        )
        return req

    async def wait_for_decision(self, request_id: str) -> tuple[str, str | None, dict | None]:
        """Block until the user responds. Returns (decision, reason, updated_input)."""
        req = self._pending.get(request_id)
        if not req:
            return ("deny", "Request not found", None)
        await req.event.wait()
        # Clean up
        self._pending.pop(request_id, None)
        return (req.decision or "deny", req.reason, req.updated_input)

    def respond(self, request_id: str, decision: str, reason: str | None = None, updated_input: dict | None = None) -> bool:
        """Resolve a pending request. Returns False if not found."""
        req = self._pending.get(request_id)
        if not req:
            return False
        req.decision = decision
        req.reason = reason
        req.updated_input = updated_input
        req.event.set()
        logger.info(
            "permissions: resolved %s → %s (agent %s, tool=%s)",
            request_id, decision, req.agent_id[:8], req.tool_name,
        )
        return True

    def get_pending(self, agent_id: str | None = None) -> list[dict]:
        """Return pending requests, optionally filtered by agent."""
        results = []
        for req in self._pending.values():
            if agent_id and req.agent_id != agent_id:
                continue
            results.append({
                "request_id": req.id,
                "agent_id": req.agent_id,
                "tool_name": req.tool_name,
                "tool_input": req.tool_input,
                "summary": req.summary,
                "created_at": req.created_at,
            })
        return results

    def pending_count(self, agent_id: str | None = None) -> int:
        """Count pending requests, optionally for a specific agent."""
        if agent_id:
            return sum(1 for r in self._pending.values() if r.agent_id == agent_id)
        return len(self._pending)

    def find_pending_by_tool(self, agent_id: str, tool_name: str) -> str | None:
        """Find a pending request by agent_id and tool_name. Returns request_id or None."""
        for req in self._pending.values():
            if req.agent_id == agent_id and req.tool_name == tool_name:
                return req.id
        return None
