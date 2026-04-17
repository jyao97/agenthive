"""Slash command allowlist and lifecycle management.

Each command declares its own lifecycle:
- delivered_by  — which hook marks it as delivered
- completed_by  — which hook marks it as completed
- changes_session — whether it creates a new session ID
- args          — "required", "optional", or None
- description   — brief human-readable description

When Claude Code adds new slash commands, add an entry to COMMANDS below.
Empirically verified against Claude Code v2.1.76 (2026-03-15).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-command configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandConfig:
    delivered_by: str          # Hook that marks delivery (USP, PreCompact, SessionStart, etc.)
    completed_by: str          # Hook that marks completion (Stop, PostCompact, SessionStart, SessionEnd, CronDelete, etc.)
    changes_session: bool      # Whether this command creates a new session ID
    args: str | None           # "required", "optional", or None
    description: str           # Brief human-readable description


COMMANDS: dict[str, CommandConfig] = {
    # --- Dedicated lifecycle commands ---
    "/compact": CommandConfig(
        delivered_by="PreCompact",
        completed_by="PostCompact",
        changes_session=True,
        args="optional",
        description="Compact conversation context",
    ),
    "/clear": CommandConfig(
        delivered_by="SessionStart",
        completed_by="SessionStart",  # atomic — delivered and completed in same hook
        changes_session=True,
        args=None,
        description="Clear conversation and start new session",
    ),

    # --- Model-invoking commands (USP + Stop) ---
    "/init": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args=None,
        description="Initialize project with CLAUDE.md",
    ),
    "/review": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args=None,
        description="Review code changes (deprecated)",
    ),
    "/pr-comments": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args="optional",
        description="Address PR review comments",
    ),
    "/simplify": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args="optional",
        description="Simplify code",
    ),
    "/debug": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args="optional",
        description="Debug an issue",
    ),
    "/batch": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args="required",
        description="Run batch operations",
    ),
    "/claude-api": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args=None,
        description="Interact with Claude API directly",
    ),
    "/commit": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args=None,
        description="Create a git commit",
    ),
    "/security-review": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args=None,
        description="Run a security review",
    ),
    "/insights": CommandConfig(
        delivered_by="USP",
        completed_by="Stop",
        changes_session=False,
        args=None,
        description="Generate codebase insights",
    ),

    # --- Long-running commands (NOT completed by Stop) ---
    "/loop": CommandConfig(
        delivered_by="USP",
        completed_by="SessionEnd|CronDelete",  # NOT Stop — Stop fires after each iteration
        changes_session=False,
        args="required",
        description="Run a repeating loop task",
    ),
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

def classify(content: str) -> CommandConfig | None:
    """Return the command config if allowed, None if blocked."""
    cmd, _ = parse(content)
    return COMMANDS.get(cmd)


def _is_user_skill(cmd: str) -> bool:
    """True if cmd matches an installed user-level skill (~/.claude/skills/<name>/SKILL.md)."""
    import os
    from config import CLAUDE_HOME
    if not cmd.startswith("/"):
        return False
    name = cmd[1:]
    if not name or "/" in name or ".." in name:
        return False
    return os.path.isfile(os.path.join(CLAUDE_HOME, "skills", name, "SKILL.md"))


def is_allowed(content: str) -> bool:
    """Check if a slash command is allowed from web UI.

    Regular (non-slash) messages are always allowed.
    Lifecycle commands in COMMANDS are allowed, plus any installed user skill.
    """
    if not is_slash_command(content):
        return True
    cmd, _ = parse(content)
    if cmd in COMMANDS:
        return True
    return _is_user_skill(cmd)


def rejection_message(content: str) -> str:
    """User-friendly error for blocked commands."""
    cmd, _ = parse(content)
    return f"{cmd} can only be used directly in the terminal"


# ---------------------------------------------------------------------------
# Lifecycle query helpers
# ---------------------------------------------------------------------------

def completes_on_stop(content: str) -> bool:
    """Return True if this command should be marked completed when Stop fires.

    Returns False for:
    - /loop (completed by SessionEnd or CronDelete, not Stop)
    - /compact (completed by PostCompact, not Stop)
    - /clear (completed atomically by SessionStart, not Stop)
    - Non-slash or unrecognized commands
    """
    cmd, _ = parse(content)
    cfg = COMMANDS.get(cmd)
    if not cfg:
        return False
    return cfg.completed_by == "Stop"


# ---------------------------------------------------------------------------
# Lifecycle helpers — delivery + completion
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mark_delivered(agent_id: str, content: str) -> str | None:
    """Mark the matching undelivered slash command message as delivered.

    Called by hooks that confirm a slash command was received:
    - PreCompact  -> /compact
    - SessionStart(source=clear) -> /clear
    - Stop hook   -> fallback for USP-delivered commands

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

        from display_writer import update_last
        update_last(agent_id, msg.id)

        from websocket import emit_message_delivered
        asyncio.ensure_future(emit_message_delivered(agent_id, msg.id, now.isoformat()))
        logger.info("slash_commands: %s delivered for %s (msg=%s)", cmd, agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()


def mark_completed(agent_id: str) -> str | None:
    """Mark the oldest EXECUTING slash command as completed + delivered.

    Called by the Stop hook as a catch-all for commands whose completed_by
    is "Stop".  Skips /loop commands (completed by SessionEnd/CronDelete)
    and any command whose completed_by is not "Stop".

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

        # Skip commands that are not completed by Stop (e.g. /loop, /compact, /clear)
        if not completes_on_stop(msg.content):
            cmd, _ = parse(msg.content)
            logger.info(
                "slash_commands: skipping mark_completed for %s — "
                "not completed by Stop (agent=%s, msg=%s)",
                cmd, agent_id[:8], msg.id,
            )
            return None

        now = _utcnow()
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = now
        # Also mark delivered if USP didn't (safety net).
        if not msg.delivered_at:
            msg.delivered_at = now
        db.commit()

        from display_writer import update_last
        update_last(agent_id, msg.id)

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


def mark_delivered_and_completed(agent_id: str, content: str) -> str | None:
    """Atomically mark a slash command as both delivered and completed.

    Used for commands where delivery and completion happen in the same hook
    (e.g. /clear via SessionStart).

    Returns the message ID if found, None otherwise.
    """
    from database import SessionLocal
    from models import Message, MessageRole, MessageStatus

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
                Message.status == MessageStatus.EXECUTING,
                Message.content.startswith(cmd),
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg:
            logger.debug("mark_delivered_and_completed: no EXECUTING %s for %s", cmd, agent_id[:8])
            return None

        now = _utcnow()
        msg.delivered_at = now
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = now
        # Session-changing slash commands never appear in post-change JSONL.
        # Set synthetic jsonl_uuid so promotion queries skip them.
        cmd_cfg = COMMANDS.get(cmd)
        if cmd_cfg and cmd_cfg.changes_session and not msg.jsonl_uuid:
            msg.jsonl_uuid = f"slash-{msg.id[:8]}"
        db.commit()

        from display_writer import update_last
        update_last(agent_id, msg.id)

        from websocket import emit_message_delivered, emit_message_update
        asyncio.ensure_future(emit_message_delivered(agent_id, msg.id, now.isoformat()))
        asyncio.ensure_future(emit_message_update(
            agent_id, msg.id, "COMPLETED",
            completed_at=now.isoformat(),
        ))

        logger.info("slash_commands: %s delivered+completed for %s (msg=%s)", cmd, agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()


def mark_loop_completed(agent_id: str) -> str | None:
    """Mark an EXECUTING /loop command as completed.

    Called from SessionEnd hook or when CronDelete is detected in JSONL.
    This is the only way /loop commands get completed — the Stop hook
    explicitly skips them because Stop fires after each loop iteration.

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
                Message.content.startswith("/loop"),
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg:
            logger.debug("mark_loop_completed: no EXECUTING /loop for %s", agent_id[:8])
            return None

        now = _utcnow()
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = now
        if not msg.delivered_at:
            msg.delivered_at = now
        db.commit()

        from display_writer import update_last
        update_last(agent_id, msg.id)

        from websocket import emit_message_update
        asyncio.ensure_future(emit_message_update(
            agent_id, msg.id, "COMPLETED",
            completed_at=now.isoformat(),
        ))
        if msg.delivered_at == now:
            from websocket import emit_message_delivered
            asyncio.ensure_future(emit_message_delivered(agent_id, msg.id, now.isoformat()))

        logger.info("slash_commands: /loop completed for %s (msg=%s)", agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()
