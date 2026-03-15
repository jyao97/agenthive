"""Slash command allowlist and lifecycle management.

Claude Code slash commands fall into two categories:
1. DETECTABLE — fire hooks (USP+Stop, PreCompact+PostCompact, SessionStart)
   → allowed from web UI
2. UNDETECTABLE — no hooks fire, purely CLI-local
   → blocked from web UI (no delivery tracking, no completion signal)

When Claude Code adds new slash commands, update ALLOWLIST below.
Empirically verified against Claude Code v2.1.76 (2026-03-15).
"""

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

class Category(str, Enum):
    PROMPT = "prompt"        # Expands to model prompt → fires USP + Stop
    COMPACT = "compact"      # /compact → fires PreCompact + PostCompact
    SESSION = "session"      # /clear → fires SessionStart(source=clear)
    SKILL = "skill"          # Built-in skills → fires USP + Stop


# ---------------------------------------------------------------------------
# Allowlist — only these slash commands may be sent from the web UI.
# Everything else is blocked with a user-friendly error.
# ---------------------------------------------------------------------------

ALLOWLIST: dict[str, Category] = {
    # Model-invoking (fire USP + Stop)
    "/review":      Category.PROMPT,
    "/pr-comments": Category.PROMPT,
    "/init":        Category.PROMPT,

    # Built-in skills (fire USP + Stop)
    "/simplify":    Category.SKILL,
    "/debug":       Category.SKILL,
    "/batch":       Category.SKILL,
    "/loop":        Category.SKILL,
    "/claude-api":  Category.SKILL,

    # Dedicated hooks
    "/compact":     Category.COMPACT,

    # Session management
    "/clear":       Category.SESSION,
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse(content: str) -> tuple[str, str]:
    """Extract (command, args) from message content.

    >>> parse("/compact focus on API layer")
    ('/compact', 'focus on API layer')
    >>> parse("/help")
    ('/help', '')
    >>> parse("hello world")
    ('', 'hello world')
    """
    text = (content or "").strip()
    if not text.startswith("/"):
        return "", text
    parts = text.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    return cmd, args


def is_slash_command(content: str) -> bool:
    return (content or "").strip().startswith("/")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def classify(content: str) -> Category | None:
    """Return the category if allowed, None if blocked."""
    cmd, _ = parse(content)
    return ALLOWLIST.get(cmd)


def is_allowed(content: str) -> bool:
    """Check if a slash command is allowed from web UI.

    Regular (non-slash) messages are always allowed.
    """
    if not is_slash_command(content):
        return True
    cmd, _ = parse(content)
    return cmd in ALLOWLIST


def rejection_message(content: str) -> str:
    """User-friendly error for blocked commands."""
    cmd, _ = parse(content)
    return f"{cmd} can only be used directly in the terminal"


# ---------------------------------------------------------------------------
# Lifecycle helpers — delivery + completion
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mark_delivered(agent_id: str, content: str) -> str | None:
    """Mark the matching undelivered slash command message as delivered.

    Called by hooks that confirm a slash command was received:
    - PreCompact  → /compact
    - SessionStart(source=clear) → /clear
    - Stop hook   → fallback for PROMPT/SKILL commands

    Returns the message ID if found, None otherwise.
    """
    from database import SessionLocal
    from models import Message, MessageRole

    cmd, _ = parse(content)
    if not cmd:
        return None

    db = SessionLocal()
    try:
        msg = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
                Message.source == "web",
                Message.delivered_at.is_(None),
                Message.content.startswith(cmd),
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg:
            logger.debug("mark_delivered: no undelivered %s for %s", cmd, agent_id[:8])
            return None

        now = _utcnow()
        msg.delivered_at = now
        db.commit()

        from websocket import emit_message_delivered
        asyncio.ensure_future(emit_message_delivered(agent_id, msg.id, now.isoformat()))
        logger.info("slash_commands: %s delivered for %s (msg=%s)", cmd, agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()


def mark_completed(agent_id: str) -> str | None:
    """Mark the oldest EXECUTING slash command as completed + delivered.

    Called by the Stop hook as a catch-all for PROMPT/SKILL commands.
    For /compact, PostCompact handles completion — this won't find it
    because PostCompact already set status=COMPLETED.

    Returns the message ID if found, None otherwise.
    """
    from database import SessionLocal
    from models import Message, MessageRole, MessageStatus

    db = SessionLocal()
    try:
        msg = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
                Message.status == MessageStatus.EXECUTING,
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg or not is_slash_command(msg.content or ""):
            return None

        now = _utcnow()
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = now
        # Also mark delivered if USP didn't (safety net).
        if not msg.delivered_at:
            msg.delivered_at = now
        db.commit()

        from websocket import emit_message_update
        asyncio.ensure_future(emit_message_update(
            agent_id, msg.id, "COMPLETED",
            completed_at=msg.completed_at.isoformat(),
        ))
        if msg.delivered_at == now:
            from websocket import emit_message_delivered
            asyncio.ensure_future(emit_message_delivered(agent_id, msg.id, now.isoformat()))

        cmd, _ = parse(msg.content)
        logger.info("slash_commands: %s completed for %s (msg=%s)", cmd, agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()
