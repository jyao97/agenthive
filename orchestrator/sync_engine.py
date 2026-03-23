"""Sync engine — extracted sync logic for JSONL-to-DB reconciliation.

All functions are standalone async functions that take (ad, ctx) as first args,
where `ad` is an AgentDispatcher instance and `ctx` is a SyncContext dataclass.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import (
    Agent,
    AgentStatus,
    Message,
    MessageRole,
    MessageStatus,
    Project,
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

    # Incremental read state
    last_offset: int = 0          # byte position of last successful read (EOF)
    last_turn_count: int = 0
    last_tail_hash: str = ""
    incremental_turns: list = field(default_factory=list)

    # Turn-boundary pointers for incremental JSONL reading.
    # stable_boundary: line index into cached_lines just before the last
    #   user/system JSONL entry — everything before this is finalized
    #   turns that never need re-parsing.
    # stable_turn_count: number of fully completed turns before the boundary.
    # cached_lines: raw JSONL lines accumulated so far (avoids re-reading
    #   the entire file on each wake).
    stable_boundary: int = 0
    stable_turn_count: int = 0
    cached_lines: list = field(default_factory=list)

    # Agent state
    compact_notified: bool = False
    compact_end_emitted: bool = False  # True if PostCompact already emitted "Compact end"
    compact_detected_at: float = 0.0   # monotonic time when sync detected compact (for fallback)
    idle_polls: int = 0
    getsize_error_count: int = 0


# ---------------------------------------------------------------------------
# Incremental JSONL reading
# ---------------------------------------------------------------------------

def _read_new_lines(path: str, offset: int) -> tuple[list[str], int]:
    """Read new complete lines from *path* starting at byte *offset*.

    Returns (new_lines, new_offset) where new_offset points to the byte
    just after the last complete line.  Incomplete trailing lines (mid-write
    by Claude Code) are excluded — the offset stays before them so they're
    re-read on the next wake.

    Uses binary mode for exact byte-offset tracking (text mode + manual
    byte counting drifts when errors="replace" substitutes characters).
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            raw_bytes = f.read()
    except OSError as e:
        logger.warning("_read_new_lines: failed to read %s at offset %d: %s", path, offset, e)
        return [], offset

    if not raw_bytes:
        return [], offset

    # Find the last complete newline — everything after it is a partial
    # line that we'll re-read next time.
    last_nl = raw_bytes.rfind(b"\n")
    if last_nl == -1:
        # No complete line yet — don't advance offset
        return [], offset

    complete_bytes = raw_bytes[:last_nl + 1]
    new_offset = offset + len(complete_bytes)

    # Decode and split into lines
    text = complete_bytes.decode("utf-8", errors="replace")
    complete_lines = [line.strip() for line in text.split("\n") if line.strip()]

    return complete_lines, new_offset


def _is_turn_boundary(entry: dict) -> bool:
    """Return True if a JSONL entry starts a new conversation turn.

    MUST exactly mirror what _parse_session_turns_from_lines() in
    agent_dispatcher.py treats as a turn-producing entry.  Any mismatch
    causes the incremental parser to split assistant groups at points
    the full parser ignores, creating phantom duplicate turns.

    Boundaries: real user messages (string content), system entries
    (except filtered subtypes), and queue-operation enqueue with
    non-empty content.

    NOT boundaries: tool_result user entries (list content),
    system-injected messages, queue-operation remove/dequeue, system
    entries with turn_duration/stop_hook_summary subtypes, and
    enqueue with empty content.
    """
    entry_type = entry.get("type")
    if entry_type == "queue-operation":
        # Only enqueue with non-empty content creates a turn in the
        # parser (agent_dispatcher.py:1418-1428).  remove/dequeue are
        # silently skipped — treating them as boundaries here would
        # split an assistant group that the parser keeps whole.
        if entry.get("operation") != "enqueue":
            return False
        content = entry.get("content", "")
        return isinstance(content, str) and bool(content.strip())
    if entry_type == "system":
        # Parser skips these subtypes (agent_dispatcher.py:1434)
        subtype = entry.get("subtype", "")
        if subtype in ("turn_duration", "stop_hook_summary"):
            return False
        return True
    if entry_type == "user":
        content = entry.get("message", {}).get("content", "")
        if isinstance(content, list):
            return False  # tool_result
        if isinstance(content, str) and content.strip():
            stripped = content.strip()
            if (
                stripped.startswith("<local-command-caveat>")
                or stripped.startswith("<command-name>")
                or stripped.startswith("<local-command-stdout>")
                or stripped.startswith("<system-reminder>")
                or stripped.startswith("<task-notification>")
            ):
                return False  # system-injected
        return True
    return False


def sync_parse_incremental(ctx: SyncContext) -> list[tuple[str, str, dict | None, str | None]]:
    """Incrementally read JSONL and return parsed turns.

    Uses ctx.cached_lines + ctx.stable_boundary to avoid re-reading and
    re-parsing the full file.  Only new bytes are read from disk, and only
    lines from the last stable boundary forward are re-parsed.
    """
    from agent_dispatcher import _parse_session_turns_from_lines

    # 1. Read new bytes from disk
    new_lines, new_offset = _read_new_lines(ctx.jsonl_path, ctx.last_offset)
    ctx.last_offset = new_offset

    if new_lines:
        ctx.cached_lines.extend(new_lines)

    if not ctx.cached_lines:
        return []

    # 2. Find where the last turn boundary is in cached_lines.
    #    A "boundary" is a JSONL entry that starts a new conversation turn:
    #    - Real user entries (string content, not tool_result lists)
    #    - System entries
    #    - Queue-operation entries
    #    tool_result user entries (list content with type=tool_result) are NOT
    #    boundaries — the parser skips them, and they sit between assistant
    #    entries that belong to the SAME grouped turn.
    last_boundary_idx = 0
    for i in range(len(ctx.cached_lines) - 1, -1, -1):
        line = ctx.cached_lines[i].strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if _is_turn_boundary(entry):
            last_boundary_idx = i
            break

    # 3. If boundary advanced, parse the stable prefix to get stable_turn_count
    if last_boundary_idx > ctx.stable_boundary:
        stable_prefix = ctx.cached_lines[:last_boundary_idx]
        stable_turns = _parse_session_turns_from_lines(stable_prefix)
        ctx.stable_turn_count = len(stable_turns)
        ctx.stable_boundary = last_boundary_idx
        # Cache the stable turns so we don't re-parse them
        ctx.incremental_turns = list(stable_turns)
    elif ctx.stable_boundary == 0 and not ctx.incremental_turns:
        # No boundary found yet — everything is open assistant tail
        ctx.stable_turn_count = 0

    # 4. Parse only the tail (from boundary to end)
    tail_lines = ctx.cached_lines[ctx.stable_boundary:]
    tail_turns = _parse_session_turns_from_lines(tail_lines)

    # 5. Combine stable + tail
    all_turns = list(ctx.incremental_turns[:ctx.stable_turn_count]) + tail_turns
    return all_turns


def sync_reset_incremental(ctx: SyncContext):
    """Reset incremental state — used after compact or session rotation."""
    ctx.cached_lines.clear()
    ctx.stable_boundary = 0
    ctx.stable_turn_count = 0
    ctx.incremental_turns.clear()
    ctx.last_turn_count = 0
    ctx.last_tail_hash = ""
    ctx.last_offset = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_hash(content: str) -> str:
    """Fast hash of content for change detection."""
    import hashlib
    return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:16]


def _purge_stale_messages_after_compact(
    db, agent_id: str,
    new_jsonl_uuids: set[str],
):
    """Remove cli-sourced messages whose jsonl_uuid is no longer in the
    compacted JSONL.  Web/task/system messages are preserved.

    This prevents duplicate messages after compact rewrites the JSONL
    (old turns stay in DB alongside newly imported compacted turns).
    """
    if not new_jsonl_uuids:
        # No UUIDs in new JSONL — likely a parse failure or empty file.
        # Don't purge anything to avoid data loss.
        return 0
    stale = (
        db.query(Message)
        .filter(
            Message.agent_id == agent_id,
            Message.source == "cli",
            Message.jsonl_uuid.isnot(None),
            Message.jsonl_uuid.notin_(new_jsonl_uuids),
        )
        .all()
    )
    if stale:
        for m in stale:
            db.delete(m)
        logger.info(
            "Purged %d stale cli messages for agent %s after compact",
            len(stale), agent_id,
        )
    return len(stale)


def _purge_stale_system_messages(db, agent_id: str, new_turns):
    """Remove cli-sourced system messages not in the compacted JSONL.

    Uses a Counter (multiset) to preserve multiplicity — if the new JSONL
    has 2 "session started" messages, keep exactly 2 in DB.
    """
    from collections import Counter

    new_system_counts = Counter(
        c for r, c, _, _ in new_turns if r == "system"
    )
    existing_system = (
        db.query(Message)
        .filter(
            Message.agent_id == agent_id,
            Message.role == MessageRole.SYSTEM,
            Message.source == "cli",
            Message.jsonl_uuid.is_(None),
        )
        .order_by(Message.created_at.asc())
        .all()
    )
    keep_counts = Counter()
    purged = 0
    for m in existing_system:
        if keep_counts[m.content] < new_system_counts.get(m.content, 0):
            keep_counts[m.content] += 1
        else:
            db.delete(m)
            purged += 1
    if purged:
        logger.info(
            "Purged %d stale system messages for agent %s after compact",
            purged, agent_id,
        )
    return purged


def _end_compact_activity(db, agent_id: str, session_id: str):
    """Mark the most recent unfinished Compact tool activity as ended in DB.

    The sync engine detects compact completion and emits WS events, but
    the DB record was never updated — causing stale in-progress entries
    on page reload / loadData.
    """
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


# ---------------------------------------------------------------------------
# 1. sync_reconcile_initial — full-scan comparison on loop start
# ---------------------------------------------------------------------------

async def sync_reconcile_initial(ad, ctx: SyncContext):
    """Reconcile JSONL turns with DB messages on sync loop start.

    Reads all turns, compares with DB, inserts any missing turns.
    """
    from agent_dispatcher import (
        _is_wrapped_prompt,
        _dedup_sig,
        _merge_interactive_meta,
        _update_stale_interactive_metadata,
    )
    from websocket import emit_new_message, emit_metadata_update

    initial_turns = list(ctx.incremental_turns)

    conv_turns = [
        t for t in initial_turns
        if t[0] in ("user", "assistant")
        and not (t[0] == "user" and _is_wrapped_prompt(t[1]))
    ]

    db = SessionLocal()
    try:
        if not conv_turns:
            # Still check stale interactive metadata even with no turns
            _stale_updates = _update_stale_interactive_metadata(db, ctx.agent_id, initial_turns)
            if _stale_updates:
                ad._emit(emit_new_message(
                    ctx.agent_id, "sync", ctx.agent_name, ctx.agent_project,
                ))
                for _upd_msg_id, _upd_meta in _stale_updates:
                    ad._emit(emit_metadata_update(ctx.agent_id, _upd_msg_id, _upd_meta))
                logger.debug(
                    "Updated stale interactive metadata for agent %s "
                    "(initial reconciliation)", ctx.agent_id,
                )
            return

        agent = db.get(Agent, ctx.agent_id)

        # Get ALL user/agent DB messages for dedup
        all_db = db.query(Message).filter(
            Message.agent_id == ctx.agent_id,
            Message.role.in_([MessageRole.USER, MessageRole.AGENT]),
        ).all()

        # Primary: UUID-based dedup via jsonl_uuid
        db_uuids: set[str] = {
            m.jsonl_uuid for m in all_db if m.jsonl_uuid
        }

        # Secondary: content multiset for backward compat
        # Track actual messages (not just counts) so we can update
        # jsonl_uuid / delivered_at when a content match is found after
        # compact — otherwise the matched message keeps its old timestamp
        # and appears before the compact boundary in the UI.
        db_sig_msgs: dict[tuple[str, str], list] = {}
        for m in all_db:
            role_char = "u" if m.role == MessageRole.USER else "a"
            sig = (role_char, _dedup_sig(m.content))
            db_sig_msgs.setdefault(sig, []).append(m)

        # Walk through JSONL turns and collect missing ones
        missing: list[tuple[str, str, dict | None, str | None]] = []
        for r, c, mt, uuid in conv_turns:
            # Primary: UUID-based dedup
            if uuid and uuid in db_uuids:
                continue
            # Secondary: content-based fallback (backward compat)
            role_char = "u" if r == "user" else "a"
            content_sig = _dedup_sig(c)
            sig = (role_char, content_sig)
            if db_sig_msgs.get(sig):
                matched = db_sig_msgs[sig].pop(0)
                # After compact the same content reappears under a new
                # JSONL uuid.  Stamp the matched DB message so it (a) is
                # trackable by uuid in future compacts and (b) sorts
                # after the compact boundary in the UI.
                if uuid and not matched.jsonl_uuid:
                    matched.jsonl_uuid = uuid
                    matched.delivered_at = _utcnow()
                    logger.info(
                        "Reconcile: linked msg %s to jsonl_uuid %s "
                        "(content dedup after compact) for agent %s",
                        matched.id, uuid[:12], ctx.agent_id[:8],
                    )
                continue
            # Check opposite role (e.g. task-notification fixed
            # from USER->AGENT)
            alt = ("a" if role_char == "u" else "u", content_sig)
            if db_sig_msgs.get(alt):
                db_sig_msgs[alt].pop(0)
                continue
            missing.append((r, c, mt, uuid))

        if missing and agent:
            _existing_agent_msgs = [
                m for m in all_db
                if m.role == MessageRole.AGENT
            ]
            _existing_user_msgs = [
                m for m in all_db
                if m.role == MessageRole.USER
            ]
            for role, content, meta, uuid in missing:
                meta_json = json.dumps(meta) if meta else None
                if role == "user":
                    is_wrapped_dup = False
                    for em in _existing_user_msgs:
                        if em.source in ("task", "web") and em.content:
                            if em.content[:100] in (content or ""):
                                is_wrapped_dup = True
                                if uuid and not em.jsonl_uuid:
                                    em.jsonl_uuid = uuid
                                # Mark delivered when found in JSONL
                                if not em.delivered_at:
                                    em.delivered_at = _utcnow()
                                    from websocket import emit_message_delivered
                                    asyncio.ensure_future(emit_message_delivered(
                                        ctx.agent_id, em.id,
                                        em.delivered_at.isoformat(),
                                    ))
                                break
                    if is_wrapped_dup:
                        continue
                    _now = _utcnow()
                    try:
                        with db.begin_nested():  # SAVEPOINT
                            db.add(Message(
                                agent_id=ctx.agent_id,
                                role=MessageRole.USER,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                source="cli",
                                jsonl_uuid=uuid,
                                completed_at=_now,
                                delivered_at=_now,
                            ))
                            db.flush()
                    except IntegrityError:
                        logger.warning(
                            "Skipped duplicate jsonl_uuid %s for agent %s",
                            uuid, ctx.agent_id[:8],
                        )
                        continue
                elif role == "assistant":
                    updated = False
                    for existing in _existing_agent_msgs:
                        if uuid and existing.jsonl_uuid == uuid:
                            if len(existing.content) < len(content):
                                existing.content = content
                                existing.completed_at = _utcnow()
                                if meta is not None:
                                    existing.meta_json = _merge_interactive_meta(
                                        existing.meta_json, meta,
                                    )
                            updated = True
                            break
                        if (
                            len(existing.content) < len(content)
                            and content.startswith(
                                existing.content[:200]
                            )
                        ):
                            existing.content = content
                            existing.completed_at = _utcnow()
                            if uuid and not existing.jsonl_uuid:
                                existing.jsonl_uuid = uuid
                            if meta is not None:
                                existing.meta_json = _merge_interactive_meta(
                                    existing.meta_json, meta,
                                )
                            updated = True
                            break
                    if not updated:
                        # Check for hook-created row to upgrade
                        if meta:
                            _interactive_items = meta.get("interactive", []) if isinstance(meta, dict) else []
                            for _item in _interactive_items:
                                _tid = _item.get("tool_use_id")
                                if _tid:
                                    _hook = next(
                                        (m for m in _existing_agent_msgs if m.jsonl_uuid == f"hook-{_tid}"),
                                        None,
                                    )
                                    if _hook:
                                        _hook.content = content
                                        _hook.jsonl_uuid = uuid
                                        _hook.meta_json = _merge_interactive_meta(
                                            _hook.meta_json, meta,
                                        )
                                        _hook.completed_at = _utcnow()
                                        updated = True
                                        break
                    if not updated:
                        _now2 = _utcnow()
                        try:
                            with db.begin_nested():  # SAVEPOINT
                                db.add(Message(
                                    agent_id=ctx.agent_id,
                                    role=MessageRole.AGENT,
                                    content=content,
                                    status=MessageStatus.COMPLETED,
                                    source="cli",
                                    meta_json=meta_json,
                                    jsonl_uuid=uuid,
                                    completed_at=_now2,
                                    delivered_at=_now2,
                                ))
                                db.flush()
                        except IntegrityError:
                            logger.warning(
                                "Skipped duplicate jsonl_uuid %s for agent %s",
                                uuid, ctx.agent_id[:8],
                            )
                            continue
            agent.last_message_preview = (conv_turns[-1][1] or "")[:200]
            agent.last_message_at = _utcnow()
            db.commit()
            if any(r != "user" for r, _, *_ in missing):
                ad._emit(emit_new_message(
                    ctx.agent_id, "sync", ctx.agent_name, ctx.agent_project,
                ))
            logger.info(
                "Reconciled %d missing turns for agent %s",
                len(missing), ctx.agent_id,
            )
        elif agent:
            # No missing turns — but update last agent msg if it grew
            last_agent_msg = db.query(Message).filter(
                Message.agent_id == ctx.agent_id,
                Message.role == MessageRole.AGENT,
            ).order_by(Message.created_at.desc()).first()
            last_assistant = None
            last_assistant_meta = None
            last_assistant_uuid = None
            for role, content, meta, uuid in reversed(conv_turns):
                if role == "assistant":
                    last_assistant = content
                    last_assistant_meta = meta
                    last_assistant_uuid = uuid
                    break
            _should_update = False
            if last_agent_msg and last_assistant:
                if (last_assistant_uuid and last_agent_msg.jsonl_uuid
                        and last_assistant_uuid == last_agent_msg.jsonl_uuid):
                    _should_update = len(last_agent_msg.content) < len(last_assistant)
                elif (
                    len(last_agent_msg.content) < len(last_assistant)
                    and last_assistant.startswith(
                        last_agent_msg.content[:200]
                    )
                ):
                    _should_update = True
            if _should_update:
                last_agent_msg.content = last_assistant
                last_agent_msg.completed_at = _utcnow()
                if last_assistant_uuid and not last_agent_msg.jsonl_uuid:
                    last_agent_msg.jsonl_uuid = last_assistant_uuid
                if last_assistant_meta is not None:
                    last_agent_msg.meta_json = _merge_interactive_meta(
                        last_agent_msg.meta_json, last_assistant_meta,
                    )
                db.commit()
                ad._emit(emit_new_message(
                    ctx.agent_id, "sync", ctx.agent_name, ctx.agent_project,
                ))

        # Update stale interactive metadata
        _stale_updates = _update_stale_interactive_metadata(db, ctx.agent_id, initial_turns)
        if _stale_updates:
            ad._emit(emit_new_message(
                ctx.agent_id, "sync", ctx.agent_name, ctx.agent_project,
            ))
            for _upd_msg_id, _upd_meta in _stale_updates:
                ad._emit(emit_metadata_update(ctx.agent_id, _upd_msg_id, _upd_meta))
            logger.debug(
                "Updated stale interactive metadata for agent %s "
                "(initial reconciliation)", ctx.agent_id,
            )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. sync_import_new_turns — incremental sync (file grew)
# ---------------------------------------------------------------------------

async def sync_import_new_turns(ad, ctx: SyncContext):
    """Read JSONL incrementally, parse new turns, import to DB.

    Returns one of: "new_turns", "turn_updated", "streaming", "no_change"
    """
    from agent_dispatcher import (
        _is_wrapped_prompt,
        _dedup_sig,
        _merge_interactive_meta,
        _update_stale_interactive_metadata,
    )
    from websocket import (
        emit_agent_update,
        emit_metadata_update,
        emit_new_message,
        emit_tool_activity,
    )
    from thumbnails import generate_thumbnails_for_message

    # Incremental parse — reads only new bytes, re-parses only the tail
    # (from last turn boundary forward).  sync_parse_incremental updates
    # ctx.last_offset internally.
    _prev_offset = ctx.last_offset
    turns = sync_parse_incremental(ctx)
    _current_offset = ctx.last_offset

    # Detect turn count decrease (compact may produce a larger
    # file but with fewer turns if the summary is long).
    # This requires a full re-read since the incremental cache is stale.
    if len(turns) < ctx.last_turn_count:
        from agent_dispatcher import _parse_session_turns
        logger.info(
            "Turn count decreased for agent %s (%d -> %d, "
            "likely /compact), doing full re-read",
            ctx.agent_id, ctx.last_turn_count, len(turns),
        )
        sync_reset_incremental(ctx)
        turns = _parse_session_turns(ctx.jsonl_path)
        try:
            _current_offset = os.path.getsize(ctx.jsonl_path)
        except OSError:
            _current_offset = _prev_offset
        ctx.last_offset = _current_offset
        # Re-populate cached_lines from full read for future incremental use
        try:
            with open(ctx.jsonl_path, "r", errors="replace") as _f:
                for _raw in _f:
                    _stripped = _raw.strip()
                    if _stripped:
                        ctx.cached_lines.append(_stripped)
        except OSError:
            pass
        ctx.incremental_turns = list(turns)
        ctx.last_turn_count = len(turns)
        _t = turns[-1] if turns else ("", "", None)
        _meta_sig = str(_t[2]) if len(_t) > 2 and _t[2] else ""
        ctx.last_tail_hash = f"{_content_hash(_t[1])}:{_meta_sig}" if turns else ""

        # Purge old cli-sourced messages whose UUIDs are no longer in the
        # compacted JSONL — prevents duplicate messages in the chat.
        new_uuids = {uuid for _, _, _, uuid in turns if uuid}
        db_purge = SessionLocal()
        try:
            _purge_stale_messages_after_compact(db_purge, ctx.agent_id, new_uuids)
            _purge_stale_system_messages(db_purge, ctx.agent_id, turns)
            db_purge.commit()
        finally:
            db_purge.close()

        # Do NOT emit compact-end UI signals here — PostCompact hook is
        # the authoritative source.  Emitting here races the hook and
        # causes premature double-tick / system-message indicators.
        if ctx.compact_end_emitted:
            logger.info(
                "Compact end already emitted by PostCompact hook for agent %s, "
                "resetting flag",
                ctx.agent_id,
            )
            ctx.compact_end_emitted = False
        else:
            import time as _time
            ctx.compact_detected_at = _time.monotonic()
            logger.info(
                "Turn-count-decrease compact for agent %s but PostCompact not "
                "yet received, deferring UI signals",
                ctx.agent_id,
            )
        ctx.compact_notified = False
        # After compact, reconcile to import any genuinely new turns that
        # arrived post-compact (the counter reset above marks ALL turns as
        # "processed" including ones not yet in DB).
        await sync_reconcile_initial(ad, ctx)
        return "compact_turn_decrease"

    # Update incremental state
    ctx.incremental_turns = list(turns)

    new_turns = turns[ctx.last_turn_count:]

    # Check if the last existing turn's content grew
    _tail_turn = turns[-1] if turns else ("", "", None)
    _tail_meta_sig = str(_tail_turn[2]) if len(_tail_turn) > 2 and _tail_turn[2] else ""
    tail_hash = f"{_content_hash(_tail_turn[1])}:{_tail_meta_sig}" if turns else ""
    last_turn_updated = (
        not new_turns
        and len(turns) == ctx.last_turn_count
        and tail_hash != ctx.last_tail_hash
        and turns
        and turns[-1][0] == "assistant"
    )

    if not new_turns and not last_turn_updated:
        # Commit offset even on no_change — the full parse succeeded,
        # so we know the file state is consistent up to this point.
        ctx.last_offset = _current_offset
        return "no_change"

    db = SessionLocal()
    try:
        agent = db.get(Agent, ctx.agent_id)
        if not agent or agent.status != AgentStatus.SYNCING:
            logger.info(
                "Sync loop exiting for agent %s (status changed to %s during turn import)",
                ctx.agent_id, agent.status if agent else "DELETED",
            )
            return "exit"

        if last_turn_updated:
            # Update the last agent message in-place
            last_msg = db.query(Message).filter(
                Message.agent_id == ctx.agent_id,
                Message.role == MessageRole.AGENT,
            ).order_by(Message.created_at.desc()).first()
            if last_msg:
                _last_role, _last_content, *_last_rest = turns[-1]
                _last_meta = _last_rest[0] if _last_rest else None
                last_msg.content = _last_content
                last_msg.completed_at = _utcnow()
                if _last_meta is not None:
                    last_msg.meta_json = _merge_interactive_meta(
                        last_msg.meta_json, _last_meta,
                    )
                agent.last_message_preview = (_last_content or "")[:200]
                agent.last_message_at = _utcnow()
                db.commit()
                ad._emit(emit_new_message(agent.id, "sync", ctx.agent_name, ctx.agent_project))
                # Interactive card notifications are handled by PreToolUse hook
                ctx.last_tail_hash = tail_hash
                ctx.last_offset = _current_offset
                logger.info(
                    "Updated last turn content for agent %s (%s chars)",
                    ctx.agent_id, len(_last_content),
                )
            return "turn_updated"
        else:
            # Before importing new turns, check if the turn just
            # before the new ones grew
            if ctx.last_turn_count > 0 and new_turns:
                prev_role, prev_content, *prev_rest = turns[ctx.last_turn_count - 1]
                prev_meta = prev_rest[0] if prev_rest else None
                prev_uuid = prev_rest[1] if len(prev_rest) > 1 else None
                if prev_role == "assistant":
                    last_agent_msg = db.query(Message).filter(
                        Message.agent_id == ctx.agent_id,
                        Message.role == MessageRole.AGENT,
                    ).order_by(Message.created_at.desc()).first()
                    _is_match = False
                    if last_agent_msg:
                        if (prev_uuid and last_agent_msg.jsonl_uuid
                                and prev_uuid == last_agent_msg.jsonl_uuid):
                            _is_match = True
                        elif len(last_agent_msg.content) < len(prev_content):
                            _is_match = True
                    if _is_match and len(last_agent_msg.content) < len(prev_content):
                        old_len = len(last_agent_msg.content)
                        last_agent_msg.content = prev_content
                        last_agent_msg.completed_at = _utcnow()
                        if prev_uuid and not last_agent_msg.jsonl_uuid:
                            last_agent_msg.jsonl_uuid = prev_uuid
                        if prev_meta is not None:
                            last_agent_msg.meta_json = _merge_interactive_meta(
                                last_agent_msg.meta_json, prev_meta,
                            )
                        logger.info(
                            "Updated previous turn content for agent %s "
                            "(%d -> %d chars)",
                            ctx.agent_id, old_len, len(prev_content),
                        )

            # Import new turns
            for role, content, *rest in new_turns:
                meta = rest[0] if rest else None
                jsonl_uuid = rest[1] if len(rest) > 1 else None
                meta_json = json.dumps(meta) if meta else None
                if role == "user":
                    # Detect user interrupt — Claude Code writes this when
                    # Escape/Ctrl+C stops generation.  Clear generating state
                    # so the UI reflects the actual idle status.
                    if "[Request interrupted by user" in (content or ""):
                        if ctx.agent_id in ad._generating_agents or (
                            agent and agent.generating_msg_id is not None
                        ):
                            ad._stop_generating(ctx.agent_id)
                            logger.info(
                                "Interrupt detected for agent %s, cleared generating state",
                                ctx.agent_id[:8],
                            )

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
                        ).order_by(Message.created_at.desc()).first()
                        if _web_msg:
                            if jsonl_uuid:
                                _web_msg.jsonl_uuid = jsonl_uuid
                            if not _web_msg.delivered_at:
                                _web_msg.delivered_at = _utcnow()
                                from websocket import emit_message_delivered
                                asyncio.ensure_future(emit_message_delivered(
                                    ctx.agent_id, _web_msg.id,
                                    _web_msg.delivered_at.isoformat(),
                                ))
                                logger.info(
                                    "Message %s delivered for agent %s (JSONL wrapped prompt)",
                                    _web_msg.id, ctx.agent_id[:8],
                                )
                        continue
                    # Primary: UUID-based dedup
                    if jsonl_uuid:
                        existing_uuid = db.query(Message.id).filter(
                            Message.agent_id == ctx.agent_id,
                            Message.jsonl_uuid == jsonl_uuid,
                        ).first()
                        if existing_uuid:
                            continue
                    # Secondary: content dedup against unlinked web/plan_continue
                    _norm = _dedup_sig(content)
                    _unlinked = db.query(Message).filter(
                        Message.agent_id == ctx.agent_id,
                        Message.role == MessageRole.USER,
                        _or(
                            Message.source == "web",
                            Message.source == "plan_continue",
                        ),
                        Message.jsonl_uuid.is_(None),
                    ).all()
                    _match = next(
                        (m for m in _unlinked
                         if _dedup_sig(m.content) == _norm),
                        None,
                    )
                    if _match:
                        if jsonl_uuid:
                            _match.jsonl_uuid = jsonl_uuid
                        # JSONL contains this message — mark delivered
                        if not _match.delivered_at:
                            _match.delivered_at = _utcnow()
                            from websocket import emit_message_delivered
                            asyncio.ensure_future(emit_message_delivered(
                                ctx.agent_id, _match.id,
                                _match.delivered_at.isoformat(),
                            ))
                            logger.info(
                                "Message %s delivered for agent %s (JSONL sync)",
                                _match.id, ctx.agent_id[:8],
                            )
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
                    )
                elif role == "assistant":
                    # UUID-based dedup for assistant turns (prevents
                    # duplicates after compact resets turn counters)
                    if jsonl_uuid:
                        _existing_asst = db.query(Message.id).filter(
                            Message.agent_id == ctx.agent_id,
                            Message.jsonl_uuid == jsonl_uuid,
                        ).first()
                        if _existing_asst:
                            continue

                    # Upgrade hook-created row if it exists
                    _hook_upgraded = False
                    if meta:
                        _interactive = meta.get("interactive", [])
                        _tids = [item.get("tool_use_id") for item in _interactive if item.get("tool_use_id")]
                        if _tids:
                            for _tid in _tids:
                                _hook_msg = db.query(Message).filter(
                                    Message.agent_id == ctx.agent_id,
                                    Message.jsonl_uuid == f"hook-{_tid}",
                                ).first()
                                if _hook_msg:
                                    _hook_msg.content = content
                                    _hook_msg.jsonl_uuid = jsonl_uuid
                                    _hook_msg.meta_json = _merge_interactive_meta(
                                        _hook_msg.meta_json, meta,
                                    )
                                    _hook_msg.completed_at = _utcnow()
                                    _hook_upgraded = True
                                    logger.info(
                                        "Upgraded hook message %s -> jsonl_uuid %s for agent %s",
                                        _hook_msg.id, jsonl_uuid[:12] if jsonl_uuid else "none", ctx.agent_id[:8],
                                    )
                                    break
                    if _hook_upgraded:
                        continue
                    _now = _utcnow()
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
                    )
                elif role == "system":
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
                    )
                else:
                    continue
                try:
                    with db.begin_nested():  # SAVEPOINT
                        db.add(msg)
                        db.flush()
                except IntegrityError:
                    logger.warning(
                        "Skipped duplicate jsonl_uuid %s for agent %s",
                        jsonl_uuid, ctx.agent_id[:8],
                    )
                    continue

            agent.last_message_preview = (new_turns[-1][1] or "")[:200]
            agent.last_message_at = _utcnow()
            db.commit()

            ctx.last_turn_count = len(turns)
            ctx.last_tail_hash = tail_hash
            ctx.last_offset = _current_offset
            ad._emit(emit_agent_update(
                agent.id, agent.status.value, agent.project
            ))
            _new_roles = [r for r, *_ in new_turns]
            logger.info(
                "Synced %d new turns for agent %s (roles=%s)",
                len(new_turns), ctx.agent_id, _new_roles,
            )
            if any(r != "user" for r, *_ in new_turns):
                ad._emit(emit_new_message(agent.id, "sync", ctx.agent_name, ctx.agent_project))

            # Notify for unanswered interactive items (AskUserQuestion, ExitPlanMode)
            _has_unanswered_interactive = False
            for _r, _c, *_rest in new_turns:
                if _r == "assistant" and _rest:
                    _meta = _rest[0] if _rest else None
                    if isinstance(_meta, dict):
                        for _item in _meta.get("interactive", []):
                            if _item.get("answer") is None:
                                _has_unanswered_interactive = True
                                break
                if _has_unanswered_interactive:
                    break
            if _has_unanswered_interactive and not ad._is_agent_in_use(
                agent.id, agent.tmux_pane
            ):
                _interactive_types = []
                for _r, _c, *_rest in new_turns:
                    if _r == "assistant" and _rest:
                        _meta = _rest[0] if _rest else None
                        if isinstance(_meta, dict):
                            for _item in _meta.get("interactive", []):
                                if _item.get("answer") is None:
                                    _interactive_types.append(_item.get("type", ""))
                from notify import notify as _notify
                if "exit_plan_mode" in _interactive_types:
                    _notify(
                        "message", agent.id,
                        agent.name or f"Agent {agent.id[:8]}",
                        "Plan approval needed",
                        f"/agents/{agent.id}",
                        muted=agent.muted,
                        in_use=False,
                    )
                elif "ask_user_question" in _interactive_types:
                    _notify(
                        "message", agent.id,
                        agent.name or f"Agent {agent.id[:8]}",
                        "Question — waiting for your answer",
                        f"/agents/{agent.id}",
                        muted=agent.muted,
                        in_use=False,
                    )

            # Generate video thumbnails for new assistant turns
            for _r, _c, *_ in new_turns:
                if _r == "assistant" and _c:
                    asyncio.ensure_future(asyncio.to_thread(
                        generate_thumbnails_for_message, _c, ctx.project_path,
                    ))

        # Update stale interactive metadata on EARLIER messages
        _stale_updates = _update_stale_interactive_metadata(db, ctx.agent_id, turns)
        if _stale_updates:
            ad._emit(emit_new_message(
                ctx.agent_id, "sync", ctx.agent_name, ctx.agent_project,
            ))
            for _upd_msg_id, _upd_meta in _stale_updates:
                ad._emit(emit_metadata_update(ctx.agent_id, _upd_msg_id, _upd_meta))
            logger.debug(
                "Updated stale interactive metadata for agent %s",
                ctx.agent_id,
            )
    finally:
        db.close()

    return "new_turns"


# ---------------------------------------------------------------------------
# 3. sync_handle_compact — file shrink detection
# ---------------------------------------------------------------------------

async def sync_handle_compact(ad, ctx: SyncContext):
    """Handle JSONL rewrite (e.g. /compact shrinks the file).

    Full re-parse, drain tool_log, emit compact notification.
    """
    from agent_dispatcher import _parse_session_turns
    from websocket import emit_new_message, emit_tool_activity

    logger.info(
        "Session file shrank for agent %s (%d -> ? bytes, "
        "likely /compact), resetting incremental state + full re-parse",
        ctx.agent_id, ctx.last_offset,
    )
    # Reset incremental cache — file was rewritten
    sync_reset_incremental(ctx)

    turns = _parse_session_turns(ctx.jsonl_path)
    ctx.incremental_turns = list(turns)
    ctx.last_turn_count = len(turns)
    _t = turns[-1] if turns else ("", "", None)
    _meta_sig = str(_t[2]) if len(_t) > 2 and _t[2] else ""
    ctx.last_tail_hash = f"{_content_hash(_t[1])}:{_meta_sig}" if turns else ""
    try:
        ctx.last_offset = os.path.getsize(ctx.jsonl_path)
    except OSError:
        ctx.last_offset = 0
    ctx.idle_polls = 0

    # Re-populate cached_lines from fresh full read
    try:
        with open(ctx.jsonl_path, "r", errors="replace") as _f:
            for _raw in _f:
                _stripped = _raw.strip()
                if _stripped:
                    ctx.cached_lines.append(_stripped)
    except OSError:
        pass

    # Purge old cli-sourced messages whose UUIDs are no longer in the
    # compacted JSONL — prevents duplicate messages in the chat.
    new_uuids = {uuid for _, _, _, uuid in turns if uuid}
    db_purge = SessionLocal()
    try:
        _purge_stale_messages_after_compact(db_purge, ctx.agent_id, new_uuids)
        _purge_stale_system_messages(db_purge, ctx.agent_id, turns)
        db_purge.commit()
    finally:
        db_purge.close()

    # Do NOT emit compact-end UI signals here.  PostCompact hook is the
    # authoritative source — emitting here races the hook and causes
    # premature double-tick / system-message indicators.  If PostCompact
    # already fired, compact_end_emitted is True and we just reset it.
    # If PostCompact hasn't fired yet, we note the time so the sync loop
    # can emit a fallback after a grace period (see _COMPACT_GRACE_SECS).
    if ctx.compact_end_emitted:
        logger.info(
            "Compact end already emitted by PostCompact hook for agent %s, "
            "resetting flag",
            ctx.agent_id,
        )
        ctx.compact_end_emitted = False
    else:
        import time as _time
        ctx.compact_detected_at = _time.monotonic()
        logger.info(
            "Compact detected for agent %s but PostCompact not yet received, "
            "deferring UI signals",
            ctx.agent_id,
        )
    ctx.compact_notified = False


# ---------------------------------------------------------------------------
# 4. trigger_sync — public entry point for hooks
# ---------------------------------------------------------------------------

async def trigger_sync(ad, agent_id: str):
    """Public entry point for hooks to wake the sync loop."""
    ctx = ad._sync_contexts.get(agent_id)
    if not ctx:
        return
    ad.wake_sync(agent_id)


