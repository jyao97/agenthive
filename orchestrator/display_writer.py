"""Display file writer — writes per-agent JSONL files for the frontend.

Each agent gets a `data/display/{agent_id}.jsonl` file containing one JSON
line per message, ordered by display_seq.  The frontend reads these files
to render the chat history without querying the DB.

Functions:
    flush_agent       — append undisplayed messages to the display file
    update_last       — append a replacement line for a streaming update
    rebuild_agent     — rebuild the display file (append-only: new seq block)
    delete_agent      — remove the display file
    startup_rebuild_all — rebuild all active agents on server startup
"""

import json
import logging
import os

from sqlalchemy import func, text

from config import _resolve
from database import SessionLocal
from models import Agent, AgentStatus, Message

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


def flush_agent(agent_id: str):
    """Append all undisplayed messages (display_seq IS NULL) to the display file.

    - Query messages WHERE agent_id=X AND display_seq IS NULL
    - Order by created_at ASC
    - Get current max display_seq for this agent
    - Assign sequential display_seq starting from max+1
    - Append each as a JSON line to data/display/{agent_id}.jsonl
    - Commit the display_seq updates
    """
    db = SessionLocal()
    try:
        undisplayed = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.display_seq.is_(None),
            )
            .order_by(Message.created_at.asc())
            .all()
        )
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

        db.commit()

        # Append to file
        with open(path, "a") as f:
            for line in lines:
                f.write(line + "\n")

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
    """Append a replacement line for a message whose content changed (streaming).

    Only if the message already has display_seq (already in file).
    Append with _replace=True.
    """
    db = SessionLocal()
    try:
        msg = db.get(Message, message_id)
        if not msg or msg.agent_id != agent_id:
            return
        if msg.display_seq is None:
            return  # not yet in display file

        os.makedirs(DISPLAY_DIR, exist_ok=True)
        path = _display_path(agent_id)
        line = _serialize_message(msg, msg.display_seq, replace=True)
        with open(path, "a") as f:
            f.write(line + "\n")
    except Exception:
        logger.exception(
            "Failed to update display file for agent %s msg %s",
            agent_id[:8], message_id,
        )
    finally:
        db.close()


def rebuild_agent(agent_id: str):
    """Rebuild display file by re-flushing all messages as a new seq block.

    This is append-only: existing display file content is preserved.
    All messages get new display_seq values (NULL → re-assigned), which
    produces _replace-free lines that the frontend deduplicates by id.
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

    # flush_agent will append new lines — file is never deleted
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
