"""Display file writer — writes per-agent JSONL files for the frontend.

Each agent gets a `data/display/{agent_id}.jsonl` file containing one JSON
line per message, ordered by display_seq.  The frontend reads these files
to render the chat history without querying the DB.

Design:
    - Display file is append-only (rebuild appends new seq block)
    - File write happens BEFORE DB commit (safe: if DB fails, display_seq
      stays NULL, next flush retries; frontend deduplicates by id)
    - File writes use fcntl.flock to prevent interleaved lines

Functions:
    flush_agent       — append undisplayed messages to the display file
    update_last       — append a replacement line for a streaming update
    rebuild_agent     — rebuild the display file (append-only: new seq block)
    delete_agent      — remove the display file
    startup_rebuild_all — rebuild all active agents on server startup
"""

import fcntl
import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import func, text

from config import _resolve
from database import SessionLocal
from models import Agent, AgentStatus, Message, MessageRole, MessageStatus

logger = logging.getLogger("orchestrator.display_writer")

DISPLAY_DIR = _resolve("data/display")


def _display_path(agent_id: str) -> str:
    """Return path: data/display/{agent_id}.jsonl"""
    return os.path.join(DISPLAY_DIR, f"{agent_id}.jsonl")


def _serialize_message(msg: Message, seq: int, replace: bool = False) -> str:
    """Serialize a Message to a JSON line for the display file.

    Fields: id, seq, role, kind, content, source, status, metadata,
    tool_use_id, created_at, completed_at, delivered_at.
    If replace=True, add "_replace": true.
    """
    # Parse metadata from meta_json
    metadata = None
    if msg.meta_json:
        try:
            metadata = json.loads(msg.meta_json)
        except (json.JSONDecodeError, ValueError):
            metadata = None

    # Extract tool_use_id from metadata if present, or use direct column
    tool_use_id = getattr(msg, "tool_use_id", None)
    if not tool_use_id and isinstance(metadata, dict):
        for item in metadata.get("interactive", []):
            if "tool_use_id" in item:
                tool_use_id = item["tool_use_id"]
                break

    obj = {
        "id": msg.id,
        "seq": seq,
        "role": msg.role.value if msg.role else None,
        "kind": msg.kind if hasattr(msg, "kind") else "message",
        "content": msg.content,
        "source": msg.source,
        "status": msg.status.value if msg.status else None,
        "metadata": metadata,
        "tool_use_id": tool_use_id,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "completed_at": msg.completed_at.isoformat() if msg.completed_at else None,
        "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
    }
    if replace:
        obj["_replace"] = True

    return json.dumps(obj, separators=(",", ":"))


def _write_locked(path: str, lines: list[str]):
    """Append lines to file with exclusive flock to prevent interleaving."""
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            for line in lines:
                f.write(line + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def flush_agent(agent_id: str):
    """Append all undisplayed messages (display_seq IS NULL) to the display file.

    Order of operations (file-first for safety):
    1. Query messages WHERE display_seq IS NULL
    2. Assign display_seq in memory
    3. Write to file (with flock)
    4. Commit display_seq to DB

    If file write fails → display_seq stays NULL → next flush retries.
    If DB commit fails → display_seq reverts to NULL → next flush retries,
    file has duplicate lines but frontend deduplicates by id.
    """
    db = SessionLocal()
    try:
        _MAX_TS = datetime(9999, 1, 1, tzinfo=timezone.utc)
        undisplayed = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.display_seq.is_(None),
                # Skip PENDING user messages — they'll be flushed when
                # dispatched (QUEUED) by the stop hook, ensuring they get
                # display_seq AFTER the preceding agent response.
                ~(
                    (Message.role == MessageRole.USER)
                    & (Message.status == MessageStatus.PENDING)
                ),
            )
            .all()
        )

        def _sort_key(msg: Message) -> datetime:
            """Ordering: system/agent by created_at, user by delivered_at.

            Undelivered user messages sort last (queued at bottom).
            """
            if msg.role == MessageRole.USER:
                ts = msg.delivered_at or _MAX_TS
            else:
                ts = msg.created_at or _MAX_TS
            # Ensure tz-aware for consistent comparison (some DB rows
            # may have naive timestamps from older code paths).
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts

        undisplayed.sort(key=_sort_key)
        if not undisplayed:
            return

        # Get current max display_seq
        max_seq = db.query(func.max(Message.display_seq)).filter(
            Message.agent_id == agent_id,
        ).scalar()
        next_seq = (max_seq or 0) + 1

        # Ensure directory exists
        os.makedirs(DISPLAY_DIR, exist_ok=True)

        path = _display_path(agent_id)
        lines = []
        for msg in undisplayed:
            msg.display_seq = next_seq
            lines.append(_serialize_message(msg, next_seq))
            next_seq += 1

        # Write file FIRST — if this fails, display_seq stays uncommitted
        try:
            _write_locked(path, lines)
        except OSError:
            # File write failed — reset display_seq so next flush retries
            for msg in undisplayed:
                msg.display_seq = None
            db.expire_all()
            logger.exception("File write failed for agent %s display file", agent_id[:8])
            return

        # Commit display_seq to DB — file is already written
        db.commit()

        logger.debug(
            "Flushed %d messages to display file for agent %s (seq %d..%d)",
            len(undisplayed), agent_id[:8],
            undisplayed[0].display_seq, undisplayed[-1].display_seq,
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to flush display file for agent %s", agent_id[:8])
    finally:
        db.close()


def update_last(agent_id: str, message_id: str):
    """Append a replacement line for a message whose content/status changed.

    Used for:
    - Streaming content updates (agent response growing)
    - Delivery status updates (delivered_at set after promotion)

    If the message already has display_seq, appends with _replace=True so
    the frontend overwrites the stale entry.  If display_seq is None (message
    not yet in the display file), falls through to flush_agent which assigns
    a seq and writes the message for the first time.
    """
    _needs_flush = False
    db = SessionLocal()
    try:
        msg = db.get(Message, message_id)
        if not msg or msg.agent_id != agent_id:
            return
        if msg.display_seq is None:
            _needs_flush = True
        else:
            os.makedirs(DISPLAY_DIR, exist_ok=True)
            path = _display_path(agent_id)
            line = _serialize_message(msg, msg.display_seq, replace=True)
            _write_locked(path, [line])
    except Exception:
        logger.exception(
            "Failed to update display file for agent %s msg %s",
            agent_id[:8], message_id,
        )
    finally:
        db.close()

    if _needs_flush:
        flush_agent(agent_id)


def rebuild_agent(agent_id: str):
    """Rebuild display file from scratch.

    Truncates the existing file and re-flushes all messages with fresh
    display_seq values.  This prevents stale append-only blocks from
    accumulating and ensures the file reflects the current DB state.
    """
    db = SessionLocal()
    try:
        # Reset display_seq to NULL so flush_agent picks them all up
        db.execute(
            text("UPDATE messages SET display_seq = NULL WHERE agent_id = :aid"),
            {"aid": agent_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to reset display_seq for agent %s", agent_id[:8])
        return
    finally:
        db.close()

    # Truncate existing file — flush_agent will write a clean file
    path = _display_path(agent_id)
    try:
        with open(path, "w") as f:
            f.truncate(0)
    except OSError:
        pass  # file may not exist yet — flush_agent will create it

    flush_agent(agent_id)


def delete_agent(agent_id: str):
    """Remove display file for a deleted/stopped agent."""
    path = _display_path(agent_id)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Failed to delete display file %s: %s", path, e)


def startup_rebuild_all():
    """On server startup, rebuild display files for all active agents.

    Query agents WHERE status NOT IN ('STOPPED', 'ERROR'), rebuild each.
    """
    db = SessionLocal()
    try:
        agents = (
            db.query(Agent.id)
            .filter(Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]))
            .all()
        )
    finally:
        db.close()

    if not agents:
        logger.info("No active agents — skipping display file rebuild")
        return

    logger.info("Rebuilding display files for %d active agents", len(agents))
    for (aid,) in agents:
        try:
            rebuild_agent(aid)
        except Exception:
            logger.exception("Failed to rebuild display file for agent %s", aid[:8])

    logger.info("Display file rebuild complete")
