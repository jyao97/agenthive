"""Sync engine — pointer-based JSONL-to-DB synchronization.

Design principles:
1. Pointer (last_turn_count) tracks sync position
2. Hooks wake syncing (never create messages)
3. sync_import_new_turns is the SOLE message creation path
4. sync_full_scan is read-only audit (never creates/updates messages from JSONL)
5. Compact/clear/new → sync_full_scan resets pointer

All functions take (ad, ctx) where ad is AgentDispatcher, ctx is SyncContext.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import (
    Agent,
    AgentStatus,
    Message,
    MessageRole,
    MessageStatus,
    ToolActivity,
)
from utils import utcnow as _utcnow

logger = logging.getLogger("orchestrator.sync_engine")


# ---------------------------------------------------------------------------
# SyncContext dataclass — holds all per-agent sync state
# ---------------------------------------------------------------------------

@dataclass
class SyncContext:
    agent_id: str
    session_id: str
    project_path: str
    worktree: str | None = None
    agent_name: str = ""
    agent_project: str = ""
    jsonl_path: str = ""

    # Sync pointer — the only 3 state fields that matter
    last_offset: int = 0           # file byte size — change detection only
    last_turn_count: int = 0       # THE pointer: number of turns processed
    last_content_hash: str = ""    # hash of last turn content — streaming detection

    # Agent operational state
    compact_notified: bool = False
    compact_end_emitted: bool = False
    compact_detected_at: float = 0.0
    idle_polls: int = 0
    getsize_error_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tool_use_id(meta: dict | None) -> str | None:
    """Extract primary tool_use_id from parsed interactive metadata."""
    if not meta or not isinstance(meta, dict):
        return None
    items = meta.get("interactive", [])
    if items and isinstance(items, list):
        return items[0].get("tool_use_id")
    return None


def _content_hash(content: str) -> str:
    """Fast hash of content for change detection."""
    import hashlib
    return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:16]


def _end_compact_activity(db, agent_id: str, session_id: str):
    """Mark the most recent unfinished Compact tool activity as ended."""
    existing = (
        db.query(ToolActivity)
        .filter(
            ToolActivity.agent_id == agent_id,
            ToolActivity.session_id == session_id,
            ToolActivity.tool_name == "Compact",
            ToolActivity.ended_at.is_(None),
        )
        .order_by(ToolActivity.started_at.desc())
        .first()
    )
    if existing:
        existing.ended_at = _utcnow()
        existing.output_summary = "context compacted"


def _notify_interactive(ad, agent, new_turns):
    """Send push notifications for unanswered interactive items."""
    _interactive_types = []
    for _r, _c, *_rest in new_turns:
        if _r == "assistant" and _rest:
            _meta = _rest[0] if _rest else None
            if isinstance(_meta, dict):
                for _item in _meta.get("interactive", []):
                    if _item.get("answer") is None:
                        _interactive_types.append(_item.get("type", ""))

    if not _interactive_types:
        return
    if ad._is_agent_in_use(agent.id, agent.tmux_pane):
        return

    from notify import notify as _notify
    if "exit_plan_mode" in _interactive_types:
        _notify(
            "message", agent.id,
            agent.name or f"Agent {agent.id[:8]}",
            "Plan approval needed",
            f"/agents/{agent.id}",
            muted=agent.muted, in_use=False,
        )
    elif "ask_user_question" in _interactive_types:
        _notify(
            "message", agent.id,
            agent.name or f"Agent {agent.id[:8]}",
            "Question — waiting for your answer",
            f"/agents/{agent.id}",
            muted=agent.muted, in_use=False,
        )


# ---------------------------------------------------------------------------
# sync_import_new_turns — SOLE message creation path
# ---------------------------------------------------------------------------

async def sync_import_new_turns(ad, ctx: SyncContext):
    """Full-parse JSONL, import new turns via pointer.

    This is the SOLE path that creates Message rows from JSONL.
    Returns: "new_turns", "turn_updated", "no_change", "compact", "exit",
             "commit_error"
    """
    from agent_dispatcher import (
        _parse_session_turns,
        _is_wrapped_prompt,
        _merge_interactive_meta,
    )
    from websocket import emit_agent_update, emit_new_message
    from thumbnails import generate_thumbnails_for_message

    # 1. Check file size for change detection
    try:
        current_size = os.path.getsize(ctx.jsonl_path)
    except OSError:
        return "no_change"

    if current_size < ctx.last_offset:
        return "compact"  # caller handles via sync_full_scan

    if current_size == ctx.last_offset:
        return "no_change"

    # 2. Full parse — simple, correct, the "stable point" approach
    turns = _parse_session_turns(ctx.jsonl_path)

    # 3. Detect turn count decrease (compact with longer summary)
    if len(turns) < ctx.last_turn_count:
        return "compact"

    # 4. Slice new turns using the pointer
    new_turns = turns[ctx.last_turn_count:]

    # 5. Streaming update — last turn content changed but no new turns
    #    Only applies to text turns (tool_use turns don't stream)
    if not new_turns and turns:
        last_turn = turns[-1]
        last_kind = last_turn[4] if len(last_turn) > 4 else None
        new_hash = _content_hash(last_turn[1])
        if new_hash != ctx.last_content_hash and last_turn[0] == "assistant" and last_kind in ("text", None):
            db = SessionLocal()
            try:
                agent = db.get(Agent, ctx.agent_id)
                if not agent or agent.status != AgentStatus.SYNCING:
                    return "exit"

                last_msg = db.query(Message).filter(
                    Message.agent_id == ctx.agent_id,
                    Message.role == MessageRole.AGENT,
                ).order_by(Message.created_at.desc()).first()

                if last_msg:
                    _role, _content, *_rest = last_turn
                    _meta = _rest[0] if _rest else None
                    _uuid = _rest[1] if len(_rest) > 1 else None
                    last_msg.content = _content
                    last_msg.completed_at = _utcnow()
                    last_msg.session_seq = last_msg.session_seq or (len(turns) - 1)
                    if _uuid and not last_msg.jsonl_uuid:
                        last_msg.jsonl_uuid = _uuid
                    if _meta is not None:
                        last_msg.meta_json = _merge_interactive_meta(
                            last_msg.meta_json, _meta,
                        )
                    agent.last_message_preview = (_content or "")[:200]
                    agent.last_message_at = _utcnow()
                    db.commit()
                    ad._emit(emit_new_message(
                        agent.id, "sync", ctx.agent_name, ctx.agent_project,
                    ))
                    ctx.last_content_hash = new_hash
                    ctx.last_offset = current_size
                    logger.info(
                        "Updated last turn content for agent %s (%d chars)",
                        ctx.agent_id, len(_content),
                    )
            finally:
                db.close()
            return "turn_updated"
        else:
            ctx.last_offset = current_size
            return "no_change"

    if not new_turns:
        ctx.last_offset = current_size
        return "no_change"

    # 6. Import new turns
    db = SessionLocal()
    try:
        agent = db.get(Agent, ctx.agent_id)
        if not agent or agent.status != AgentStatus.SYNCING:
            return "exit"

        # Before importing, check if previous turn grew (streaming finalized)
        if ctx.last_turn_count > 0:
            prev_role, prev_content, *prev_rest = turns[ctx.last_turn_count - 1]
            prev_uuid = prev_rest[1] if len(prev_rest) > 1 else None
            prev_meta = prev_rest[0] if prev_rest else None
            if prev_role == "assistant":
                last_agent_msg = db.query(Message).filter(
                    Message.agent_id == ctx.agent_id,
                    Message.role == MessageRole.AGENT,
                ).order_by(Message.created_at.desc()).first()
                if (last_agent_msg
                        and len(last_agent_msg.content or "") < len(prev_content)):
                    last_agent_msg.content = prev_content
                    last_agent_msg.completed_at = _utcnow()
                    if prev_uuid and not last_agent_msg.jsonl_uuid:
                        last_agent_msg.jsonl_uuid = prev_uuid
                    if prev_meta is not None:
                        last_agent_msg.meta_json = _merge_interactive_meta(
                            last_agent_msg.meta_json, prev_meta,
                        )

        _actually_inserted = 0
        for i, (role, content, *rest) in enumerate(new_turns):
            seq = ctx.last_turn_count + i
            meta = rest[0] if rest else None
            jsonl_uuid = rest[1] if len(rest) > 1 else None
            kind = rest[2] if len(rest) > 2 else None
            meta_json = json.dumps(meta) if meta else None

            if role == "user":
                # Detect user interrupt
                if "[Request interrupted by user" in (content or ""):
                    if ctx.agent_id in ad._generating_agents or (
                        agent and agent.generating_msg_id is not None
                    ):
                        ad._stop_generating(ctx.agent_id)

                # Wrapped prompt linking (FIFO order — asc, not desc)
                from sqlalchemy import or_ as _or
                if _is_wrapped_prompt(content):
                    _web_msg = db.query(Message).filter(
                        Message.agent_id == ctx.agent_id,
                        Message.role == MessageRole.USER,
                        _or(
                            Message.source == "web",
                            Message.source == "plan_continue",
                        ),
                        Message.jsonl_uuid.is_(None),
                    ).order_by(Message.created_at.asc()).first()
                    if _web_msg:
                        if jsonl_uuid:
                            _web_msg.jsonl_uuid = jsonl_uuid
                        # Assign session_seq so web message sorts correctly
                        # alongside cli-sourced messages
                        _web_msg.session_seq = seq
                        if not _web_msg.delivered_at:
                            _web_msg.delivered_at = _utcnow()
                            from websocket import emit_message_delivered
                            asyncio.ensure_future(emit_message_delivered(
                                ctx.agent_id, _web_msg.id,
                                _web_msg.delivered_at.isoformat(),
                            ))
                    continue

                # UUID-based dedup
                if jsonl_uuid:
                    existing = db.query(Message.id).filter(
                        Message.agent_id == ctx.agent_id,
                        Message.jsonl_uuid == jsonl_uuid,
                    ).first()
                    if existing:
                        continue

                _now = _utcnow()
                msg = Message(
                    agent_id=ctx.agent_id,
                    role=MessageRole.USER,
                    content=content,
                    status=MessageStatus.COMPLETED,
                    source="cli",
                    jsonl_uuid=jsonl_uuid,
                    completed_at=_now,
                    delivered_at=_now,
                    tool_use_id=_extract_tool_use_id(meta),
                    session_seq=seq,
                    kind=kind,
                )

            elif role == "assistant":
                # UUID-based dedup
                if jsonl_uuid:
                    existing = db.query(Message.id).filter(
                        Message.agent_id == ctx.agent_id,
                        Message.jsonl_uuid == jsonl_uuid,
                    ).first()
                    if existing:
                        continue

                _now = _utcnow()
                # For tool_use turns, extract tool_use_id from tool metadata
                _tid = (meta.get("tool_use_id") if kind == "tool_use" and meta
                        else _extract_tool_use_id(meta))
                msg = Message(
                    agent_id=ctx.agent_id,
                    role=MessageRole.AGENT,
                    content=content,
                    status=MessageStatus.COMPLETED,
                    source="cli",
                    meta_json=meta_json,
                    jsonl_uuid=jsonl_uuid,
                    completed_at=_now,
                    delivered_at=_now,
                    tool_use_id=_tid,
                    session_seq=seq,
                    kind=kind,
                )

            elif role == "system":
                # UUID-based dedup (synthetic UUIDs assigned by parser)
                if jsonl_uuid:
                    existing = db.query(Message.id).filter(
                        Message.agent_id == ctx.agent_id,
                        Message.jsonl_uuid == jsonl_uuid,
                    ).first()
                    if existing:
                        continue

                _now = _utcnow()
                msg = Message(
                    agent_id=ctx.agent_id,
                    role=MessageRole.SYSTEM,
                    content=content,
                    status=MessageStatus.COMPLETED,
                    source="cli",
                    jsonl_uuid=jsonl_uuid,
                    completed_at=_now,
                    delivered_at=_now,
                    session_seq=seq,
                    kind=kind,
                )
            else:
                continue

            try:
                with db.begin_nested():  # SAVEPOINT
                    db.add(msg)
                    db.flush()
                    _actually_inserted += 1
            except IntegrityError:
                logger.warning(
                    "Skipped duplicate jsonl_uuid %s for agent %s",
                    jsonl_uuid, ctx.agent_id[:8],
                )
                continue

        if _actually_inserted:
            agent.last_message_preview = (new_turns[-1][1] or "")[:200]
            agent.last_message_at = _utcnow()

        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Commit failed for agent %s, will retry next cycle: %s",
                ctx.agent_id[:8], exc,
            )
            # DO NOT advance pointer — next cycle retries, UUID dedup
            # skips already-committed turns
            return "commit_error"

        # Advance pointer ONLY on successful commit
        ctx.last_turn_count = len(turns)
        ctx.last_offset = current_size
        ctx.last_content_hash = _content_hash(turns[-1][1]) if turns else ""

        ad._emit(emit_agent_update(
            agent.id, agent.status.value, agent.project,
        ))
        logger.info(
            "Synced %d new turns for agent %s (roles=%s)",
            len(new_turns), ctx.agent_id,
            [r for r, *_ in new_turns],
        )
        if any(r != "user" for r, *_ in new_turns):
            ad._emit(emit_new_message(
                agent.id, "sync", ctx.agent_name, ctx.agent_project,
            ))

        # Notify for unanswered interactive items
        _notify_interactive(ad, agent, new_turns)

        # Generate video thumbnails for new assistant turns
        for _r, _c, *_ in new_turns:
            if _r == "assistant" and _c:
                asyncio.ensure_future(asyncio.to_thread(
                    generate_thumbnails_for_message, _c, ctx.project_path,
                ))

    finally:
        db.close()

    return "new_turns"


# ---------------------------------------------------------------------------
# sync_full_scan — read-only audit + pointer reset
# ---------------------------------------------------------------------------

async def sync_full_scan(ad, ctx: SyncContext, reason: str = "startup"):
    """Read-only audit + pointer reset.

    Called on: startup, compact, clear, new session, manual trigger.
    - On compact: deletes orphaned DB messages, reassigns session_seq.
    - On mismatch: creates a SYSTEM warning bubble (source="sync_audit").
    - Always: resets the sync pointer to current state.
    - NEVER creates or updates regular messages from JSONL.
    """
    from agent_dispatcher import _parse_session_turns
    from websocket import emit_new_message

    logger.info("Full scan for agent %s (reason=%s)", ctx.agent_id, reason)

    turns = _parse_session_turns(ctx.jsonl_path)
    try:
        current_size = os.path.getsize(ctx.jsonl_path)
    except OSError:
        current_size = 0

    # Collect all UUIDs from JSONL
    jsonl_uuids = {uuid for _, _, _, uuid in turns if uuid}

    db = SessionLocal()
    try:
        # Get all DB messages that have a jsonl_uuid (includes cli-sourced
        # AND web-sourced messages that were linked via wrapped-prompt matching)
        db_msgs = (
            db.query(Message)
            .filter(
                Message.agent_id == ctx.agent_id,
                Message.jsonl_uuid.isnot(None),
            )
            .all()
        )

        db_by_uuid = {m.jsonl_uuid: m for m in db_msgs}

        # Detect drift
        missing_in_db = [u for u in jsonl_uuids if u not in db_by_uuid]
        extra_in_db = [
            m for m in db_msgs
            if m.jsonl_uuid and m.jsonl_uuid not in jsonl_uuids
        ]
        content_mismatches = []
        for _, content, _, uuid in turns:
            if uuid and uuid in db_by_uuid:
                db_msg = db_by_uuid[uuid]
                if abs(len(db_msg.content or "") - len(content)) > 50:
                    content_mismatches.append(uuid)

        _changes_made = False

        # On compact: delete orphaned cli-sourced messages + reassign session_seq
        if reason == "compact":
            _cli_orphans = [m for m in extra_in_db if m.source == "cli"]
            if _cli_orphans:
                for m in _cli_orphans:
                    db.delete(m)
                logger.info(
                    "Purged %d orphaned messages for agent %s after compact",
                    len(_cli_orphans), ctx.agent_id,
                )
                _changes_made = True

            # Reassign session_seq from fresh turn order
            for idx, (_, _, _, uuid) in enumerate(turns):
                if uuid and uuid in db_by_uuid:
                    db_by_uuid[uuid].session_seq = idx
            _changes_made = True

            # End compact tool activity record
            _end_compact_activity(db, ctx.agent_id, ctx.session_id)

            # Handle compact UI signals
            if ctx.compact_end_emitted:
                ctx.compact_end_emitted = False
            else:
                import time as _time
                ctx.compact_detected_at = _time.monotonic()
            ctx.compact_notified = False

        # Create warning bubble if drift found (beyond compact cleanup)
        drift_parts = []
        if missing_in_db:
            drift_parts.append(f"{len(missing_in_db)} turns in JSONL not in DB")
        if reason != "compact" and extra_in_db:
            drift_parts.append(
                f"{len(extra_in_db)} DB messages not in JSONL"
            )
        if content_mismatches:
            drift_parts.append(
                f"{len(content_mismatches)} content mismatches"
            )

        if drift_parts:
            summary = f"Sync audit ({reason}): {', '.join(drift_parts)}"
            logger.warning("Agent %s: %s", ctx.agent_id, summary)

            warning_msg = Message(
                agent_id=ctx.agent_id,
                role=MessageRole.SYSTEM,
                content=summary,
                status=MessageStatus.COMPLETED,
                source="sync_audit",
                completed_at=_utcnow(),
            )
            db.add(warning_msg)
            _changes_made = True

        if _changes_made:
            db.commit()
            if drift_parts:
                ad._emit(emit_new_message(
                    ctx.agent_id, "sync", ctx.agent_name, ctx.agent_project,
                ))

        # Reset pointer to current state
        ctx.last_turn_count = len(turns)
        ctx.last_offset = current_size
        ctx.last_content_hash = _content_hash(turns[-1][1]) if turns else ""

        logger.info(
            "Full scan complete for agent %s: %d turns, pointer at %d bytes",
            ctx.agent_id, len(turns), current_size,
        )

        return {
            "turns": len(turns),
            "missing_in_db": len(missing_in_db),
            "extra_in_db": len(extra_in_db),
            "content_mismatches": len(content_mismatches),
        }
    except Exception as e:
        logger.error("Full scan failed for %s: %s", ctx.agent_id, e)
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# trigger_sync — public entry point for hooks
# ---------------------------------------------------------------------------

async def trigger_sync(ad, agent_id: str):
    """Public entry point for hooks to wake the sync loop."""
    ctx = ad._sync_contexts.get(agent_id)
    if not ctx:
        return
    ad.wake_sync(agent_id)
