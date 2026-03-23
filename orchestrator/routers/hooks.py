"""Claude Code Hooks endpoints — extracted from main.py."""

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from models import Agent, AgentMode, AgentStatus, Message, MessageRole, MessageStatus, Project, Task, TaskStatus, ToolActivity
from utils import utcnow as _utcnow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hooks"])


# ---- Helpers ----

def _resolve_agent_id_from_body(body: dict) -> str:
    """Resolve agent_id from hook body when X-Agent-Id header is empty.

    Adopted/unlinked sessions don't have AHIVE_AGENT_ID in their process
    environment, so the header expands to empty.  Fall back to body's
    session_id → Agent.session_id lookup.
    """
    sid = body.get("session_id", "").strip()
    if not sid:
        return ""
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.session_id == sid).first()
        if agent:
            logger.info("_resolve_agent_id_from_body: resolved session %s → agent %s", sid[:12], agent.id[:8])
            return agent.id
    finally:
        db.close()
    return ""


# ---- Claude Code Hooks Endpoints ----

# Stop hook signal file directory.  The dispatcher reads (and deletes)
# these when harvesting task completions.
_HOOK_SIGNAL_DIR = "/tmp/ahive-hooks"




@router.post("/api/hooks/agent-session-end")
async def hook_agent_session_end(request: Request):
    """Receive SessionEnd hook — deterministic signal that a CLI session ended.

    Replaces JSONL tail scanning (_session_has_ended polling) as the primary
    mechanism for detecting session completion.  The sync loop's polling-based
    check remains as a fallback for abnormal exits that don't fire hooks.
    """
    agent_id = request.headers.get("X-Agent-Id", "").strip()
    if not agent_id:
        try:
            body = await request.json()
        except Exception:
            body = {}
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_session_end: no X-Agent-Id and no session match")
            return {}

    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        logger.warning("hook_agent_session_end: no agent_dispatcher on app.state for agent %s", agent_id[:8])
        return {}

    # Mark any EXECUTING /loop command as completed — Stop hook skips /loop
    # because Stop fires after each iteration, but SessionEnd is terminal.
    import slash_commands as _sc
    _sc.mark_loop_completed(agent_id)

    # Trigger final sync via the sync loop
    asyncio.create_task(ad.trigger_sync(agent_id))

    logger.info("hook_agent_session_end: agent=%s", agent_id[:8])
    return {}


    # Slash command delivery/completion moved to slash_commands module.


@router.post("/api/hooks/agent-user-prompt")
async def hook_agent_user_prompt(request: Request):
    """Receive UserPromptSubmit hook — mark message delivered and wake sync.

    This hook fires when Claude actually accepts a prompt.  That IS the
    delivery event, so we mark delivered_at directly here rather than
    waiting for the JSONL sync loop (which can't see the turn yet because
    Claude writes the JSONL entry *after* this hook fires).
    """
    agent_id = request.headers.get("X-Agent-Id", "").strip()
    if not agent_id:
        # Adopted sessions don't have AHIVE_AGENT_ID — resolve from body
        try:
            body = await request.json()
        except Exception:
            body = {}
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_user_prompt: no X-Agent-Id and no session match (headers: %s)", dict(request.headers))
            return {}

    logger.info("hook_agent_user_prompt: received for agent %s", agent_id[:8])

    # Mark the most recent undelivered web-sent message as delivered.
    from websocket import emit_message_delivered
    db = SessionLocal()
    try:
        msg = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
                Message.source.in_(("web", "task", "plan_continue")),
                Message.delivered_at.is_(None),
            )
            .order_by(Message.created_at.asc())
            .first()
        )
        if msg:
            now = _utcnow()
            msg.delivered_at = now
            db.commit()
            asyncio.ensure_future(emit_message_delivered(
                agent_id, msg.id, now.isoformat(),
            ))
            logger.info("hook_agent_user_prompt: message %s delivered for agent %s", msg.id, agent_id[:8])
        else:
            logger.info("hook_agent_user_prompt: no undelivered message for agent %s", agent_id[:8])
    finally:
        db.close()

    # Unconditionally mark executing — latest signal wins.
    # UserPromptSubmit = executing, Stop = idle. No accumulated state.
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        _gen_msg_id = msg.id if msg else "unknown"
        ad._start_generating(agent_id)
        logger.info("hook_agent_user_prompt: started generating for %s (msg=%s)", agent_id[:8], _gen_msg_id[:8])
        from websocket import emit_agent_update
        db2 = SessionLocal()
        try:
            ag = db2.get(Agent, agent_id)
            if ag:
                ag.generating_msg_id = _gen_msg_id
                db2.commit()
            project = ag.project if ag else ""
        finally:
            db2.close()
        asyncio.ensure_future(emit_agent_update(agent_id, "EXECUTING", project))
        ad.wake_sync(agent_id)

        # UserPromptSubmit fires BEFORE Claude writes the user turn to JSONL.
        # The immediate wake above picks up any prior unsynced content, but
        # the new turn isn't on disk yet.  Schedule a delayed wake so the
        # sync loop catches the JSONL write right after Claude flushes it.
        async def _post_prompt_sync(_aid):
            from config import JSONL_FLUSH_DELAY
            await asyncio.sleep(JSONL_FLUSH_DELAY)
            ad.wake_sync(_aid)
        asyncio.ensure_future(_post_prompt_sync(agent_id))

    return {}


@router.post("/api/hooks/agent-stop")
async def hook_agent_stop(request: Request):
    """Receive Stop hook from Claude Code agents.

    Caches the last_assistant_message for the dispatcher and clears
    generating state so the frontend receives agent_stream_end.

    Push notifications are triggered from the JSONL sync loop (in
    agent_dispatcher) at the same moment unread_count increments, so
    badge and push are always in sync.

    Stop fires per conversation turn, not just at task completion, so this
    endpoint deliberately does NOT transition task state.
    """
    agent_id = request.headers.get("X-Agent-Id", "").strip()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_stop: no X-Agent-Id and no session match")
            return {}

    # Unconditionally clear generating — latest signal wins.
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        logger.info("hook_agent_stop: clearing generating state for %s", agent_id[:8])
        ad._stop_generating(agent_id)

        # Increment unread + push notification (without creating a Message —
        # the sync loop imports the full content from JSONL after wake).
        _stop_db = SessionLocal()
        try:
            _agent = _stop_db.get(Agent, agent_id)
            if _agent:
                _is_sub = _agent.is_subagent or _agent.parent_id
                if not _is_sub and not ad._is_agent_in_use(_agent.id, _agent.tmux_pane):
                    _agent.unread_count += 1
                _stop_db.commit()
                if not _is_sub:
                    ad._maybe_notify_message(_agent)
        except Exception:
            logger.exception("hook_agent_stop: failed to update unread for %s", agent_id[:8])
        finally:
            _stop_db.close()

        ctx = ad._sync_contexts.get(agent_id)
        if ctx:
            if ctx.compact_notified:
                logger.info(
                    "hook_agent_stop: compact in progress for %s, "
                    "deferring sync to loop",
                    agent_id[:8],
                )
                ad.wake_sync(agent_id)
            else:
                # Schedule a delayed wake so the sync loop picks up new
                # JSONL content for full turn import (dedup, interactive
                # items, metadata).  Unread logic is handled inline above.
                async def _post_stop_sync(_aid):
                    from config import JSONL_FLUSH_DELAY
                    await asyncio.sleep(JSONL_FLUSH_DELAY)
                    ad.wake_sync(_aid)
                asyncio.ensure_future(_post_stop_sync(agent_id))

                # Mark any EXECUTING slash commands as completed + delivered.
                import slash_commands as _sc
                _sc.mark_completed(agent_id)
        else:
            # Worker agents (cli_sync=False) never have a sync context —
            # this is expected, not an error.
            logger.debug(
                "hook_agent_stop: no sync context for %s (expected for worker agents)",
                agent_id[:8],
            )
    else:
        logger.warning("hook_agent_stop: no agent_dispatcher on app.state")

    logger.info("hook_agent_stop: agent=%s summary_len=%d", agent_id[:8], len(summary))

    return {}


@router.post("/api/hooks/agent-post-compact")
async def hook_agent_post_compact(request: Request):
    """Receive PostCompact hook — deterministic signal that /compact finished.

    This is the authoritative compact-completion signal.  It fires after
    Claude has rewritten the JSONL, so the file is safe to read.  Sets
    completed_at on the /compact message (double tick) and emits the
    "Compact end" tool activity event.
    """
    agent_id = request.headers.get("X-Agent-Id", "").strip()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_post_compact: no agent_id")
            return {}

    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        logger.warning("hook_agent_post_compact: no agent_dispatcher")
        return {}

    # 1. Clear compact pause flag only — do NOT clear generating state.
    #    After compact, Claude continues processing the same turn.
    #    The Stop hook will clear generating state when Claude finishes.
    logger.info("hook_agent_post_compact: compact done for %s (generating state preserved)", agent_id[:8])

    ctx = ad._sync_contexts.get(agent_id)
    if ctx:
        ctx.compact_notified = False
        ctx.compact_end_emitted = True  # prevent duplicate from sync engine

    # 2. Mark /compact message completed (double tick).
    db = SessionLocal()
    try:
        compact_msg = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
                Message.source == "web",
                Message.completed_at.is_(None),
                Message.content.startswith("/compact"),
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if compact_msg:
            compact_msg.completed_at = _utcnow()
            compact_msg.status = MessageStatus.COMPLETED
            if not compact_msg.delivered_at:
                compact_msg.delivered_at = compact_msg.completed_at

        # 3. End the compact tool activity in DB.
        from sync_engine import _end_compact_activity
        if ctx:
            _end_compact_activity(db, agent_id, ctx.session_id)
        db.commit()

        # 4. Emit completed_at update so frontend shows double tick.
        if compact_msg:
            from websocket import emit_message_update
            asyncio.ensure_future(emit_message_update(
                agent_id, compact_msg.id, "COMPLETED",
                completed_at=compact_msg.completed_at.isoformat(),
            ))
    finally:
        db.close()

    # 5. Defer JSONL read + purge to sync loop — PostCompact is blocking,
    #    so the rewritten JSONL may not be flushed yet.  The sync loop's
    #    compact_turn_decrease path handles purge + reconcile.
    if ctx:
        ctx.compact_detected_at = 0.0

        async def _post_compact_sync(_aid):
            await asyncio.sleep(0.1)
            ad.wake_sync(_aid)
        asyncio.ensure_future(_post_compact_sync(agent_id))

    # 6. Emit "Compact end" tool activity to frontend.
    #    System messages ("Conversation compacted", "This session is being
    #    continued...") come from Claude Code's JSONL — the sync loop
    #    imports them, so we don't add our own.
    from websocket import emit_tool_activity
    await emit_tool_activity(agent_id, "Compact", "end",
                             tool_output="context compacted")

    logger.info("hook_agent_post_compact: agent=%s", agent_id[:8])
    return {}


@router.post("/api/hooks/agent-tool-activity")
async def hook_agent_tool_activity(request: Request):
    """Receive PreToolUse/PostToolUse hooks — broadcast tool activity to frontend.

    Gives users real-time visibility into which tool the agent is running,
    replacing the unreliable JSONL-polling approach that loses tool info
    after the idle threshold (~6s).
    """
    agent_id = request.headers.get("X-Agent-Id", "").strip()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_tool_activity: no X-Agent-Id and no session match")
            return {}

    hook_event = body.get("hook_event_name", "")

    from websocket import emit_tool_activity, _tool_input_summary, _tool_output_summary

    ad = getattr(request.app.state, "agent_dispatcher", None)
    tool_name = phase = summary = output_summary = ""
    is_error = False
    kind = "tool"

    # --- Tool lifecycle ---
    if hook_event == "PreToolUse":
        tool_name = body.get("tool_name", "")
        phase = "start"
        tool_input = body.get("tool_input")
        summary = _tool_input_summary(tool_name, tool_input) if tool_input else ""
        await emit_tool_activity(agent_id, tool_name, phase, tool_input=tool_input)
        # Immediate interactive card for AskUserQuestion / ExitPlanMode
        if tool_name in ("AskUserQuestion", "ExitPlanMode") and tool_input and ad:
            try:
                from database import SessionLocal as _SL
                from websocket import emit_new_message as _enm
                if tool_name == "AskUserQuestion":
                    _item = {
                        "type": "ask_user_question",
                        "tool_use_id": body.get("tool_use_id", ""),
                        "questions": tool_input.get("questions", []),
                        "answer": None,
                    }
                else:
                    _item = {
                        "type": "exit_plan_mode",
                        "tool_use_id": body.get("tool_use_id", ""),
                        "allowedPrompts": tool_input.get("allowedPrompts", []),
                        "plan": tool_input.get("plan", ""),
                        "answer": None,
                    }
                _meta = json.dumps({"interactive": [_item]})
                _db = _SL()
                try:
                    _ag = _db.get(Agent, agent_id)
                    _ag_name = _ag.name if _ag else ""
                    _ag_proj = _ag.project if _ag else ""
                    # Create message with interactive metadata
                    _msg = Message(
                        agent_id=agent_id,
                        role=MessageRole.AGENT,
                        content="",
                        status=MessageStatus.COMPLETED,
                        source="cli",
                        meta_json=_meta,
                        jsonl_uuid=f"hook-{body.get('tool_use_id', '')}",
                        completed_at=_utcnow(),
                        delivered_at=_utcnow(),
                    )
                    _db.add(_msg)
                    if _ag and not ad._is_agent_in_use(_ag.id, _ag.tmux_pane):
                        _ag.unread_count = (_ag.unread_count or 0) + 1
                    _db.commit()
                    ad._emit(_enm(agent_id, _msg.id, _ag_name, _ag_proj))
                    # Notify for unanswered interactive items
                    if _ag and not ad._is_agent_in_use(_ag.id, _ag.tmux_pane):
                        from notify import notify as _notify_ic
                        _ntype = "ask_user_question" if tool_name == "AskUserQuestion" else "exit_plan_mode"
                        _ntitle = "Question — waiting for your answer" if tool_name == "AskUserQuestion" else "Plan approval needed"
                        _notify_ic(
                            "message", agent_id,
                            _ag_name or f"Agent {agent_id[:8]}",
                            _ntitle,
                            f"/agents/{agent_id}",
                            muted=_ag.muted, in_use=False,
                        )
                finally:
                    _db.close()
            except Exception:
                logger.warning(
                    "PreToolUse: failed to create interactive card for %s",
                    agent_id[:8], exc_info=True,
                )
    elif hook_event in ("PostToolUse", "PostToolUseFailure"):
        tool_name = body.get("tool_name", "")
        phase = "end"
        is_error = hook_event == "PostToolUseFailure"
        tool_input = body.get("tool_input")
        tool_output = body.get("tool_output") or body.get("tool_error") or None
        summary = _tool_input_summary(tool_name, tool_input) if tool_input else ""
        output_summary = _tool_output_summary(tool_name, tool_output, is_error) if tool_output else ""
        await emit_tool_activity(agent_id, tool_name, phase, tool_input=tool_input,
                                  tool_output=tool_output, is_error=is_error)
        # Backfill interactive card answers from PostToolUse
        if tool_name in ("AskUserQuestion", "ExitPlanMode") and tool_output:
            tool_use_id = body.get("tool_use_id", "")
            if tool_use_id:
                from database import SessionLocal as _SL
                _db = _SL()
                try:
                    # Check if agent has skip_permissions (auto-approval)
                    _ag = _db.get(Agent, agent_id)
                    is_auto = bool(_ag and _ag.skip_permissions) if _ag else False

                    # Find ALL card messages with this tool_use_id and patch any
                    # that still have answer=None (handles duplicate messages from
                    # hook-created + JSONL-sourced cards).
                    _answer_text = str(tool_output)[:500]
                    _patched_any = False
                    _msgs = _db.query(Message).filter(
                        Message.agent_id == agent_id,
                        Message.meta_json.is_not(None),
                    ).order_by(Message.created_at.desc()).limit(20).all()
                    for _msg in _msgs:
                        try:
                            _meta = json.loads(_msg.meta_json)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        _msg_changed = False
                        for _item in _meta.get("interactive", []):
                            if _item.get("tool_use_id") != tool_use_id:
                                continue
                            if _item.get("answer") is not None:
                                continue  # already answered, check next msg
                            _item["answer"] = _answer_text
                            if is_auto:
                                _item["auto_approved"] = True
                            from agent_dispatcher import _derive_selected_index
                            _derive_selected_index(_item)
                            _msg_changed = True
                        if _msg_changed:
                            _msg.meta_json = json.dumps(_meta)
                            _patched_any = True
                    if _patched_any:
                        _db.commit()
                        # Re-read and emit for each patched message
                        for _msg in _msgs:
                            try:
                                _meta = json.loads(_msg.meta_json)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            for _item in _meta.get("interactive", []):
                                if _item.get("tool_use_id") == tool_use_id and _item.get("answer") == _answer_text:
                                    from websocket import emit_metadata_update
                                    await emit_metadata_update(agent_id, _msg.id, _meta)
                                    break
                finally:
                    _db.close()
    # --- Subagent lifecycle ---
    elif hook_event == "SubagentStart":
        agent_type = body.get("agent_type", "subagent")
        tool_name = f"Agent:{agent_type}"
        phase = "start"
        kind = "subagent"
        desc = body.get("description", "") or body.get("prompt", "")[:80] or ""
        summary = desc
        await emit_tool_activity(agent_id, tool_name, phase,
                                  tool_input={"description": desc} if desc else None)
        # Create Agent record immediately so UI shows the subagent
        sub_agent_id = body.get("agent_id", "")
        if not sub_agent_id:
            logger.warning("SubagentStart hook: no agent_id in body for parent %s", agent_id[:8])
        elif not ad:
            logger.warning("SubagentStart hook: no agent_dispatcher for parent %s", agent_id[:8])
        if ad and sub_agent_id:
            try:
                from database import SessionLocal as _SL
                from models import Agent as _Agent, AgentMode as _AM, AgentStatus as _AS
                from websocket import emit_agent_update as _eau
                _db = _SL()
                try:
                    # Look up parent to get project name
                    _parent = _db.get(Agent, agent_id)
                    _project_name = _parent.project if _parent else ""
                    _name = desc[:60] or f"subagent-{sub_agent_id[:8]}"
                    _sub = _Agent(
                        project=_project_name,
                        name=_name,
                        mode=_AM.AUTO,
                        status=_AS.SYNCING,
                        cli_sync=True,
                        parent_id=agent_id,
                        is_subagent=True,
                        claude_agent_id=sub_agent_id,
                    )
                    _db.add(_sub)
                    _db.commit()
                    # Register in known_subagents
                    known = ad._known_subagents.setdefault(agent_id, {})
                    known[sub_agent_id] = {
                        "agent_id": _sub.id,
                        "last_size": 0,
                        "idle_polls": 0,
                    }
                    ad._emit(_eau(_sub.id, "SYNCING", _project_name))
                    logger.info(
                        "SubagentStart hook: created subagent %s (%s) for parent %s",
                        _sub.id, _name, agent_id[:8],
                    )
                finally:
                    _db.close()
            except Exception:
                logger.warning(
                    "SubagentStart hook: failed to create subagent for parent %s",
                    agent_id[:8], exc_info=True,
                )
    elif hook_event == "SubagentStop":
        agent_type = body.get("agent_type", "subagent")
        tool_name = f"Agent:{agent_type}"
        phase = "end"
        kind = "subagent"
        output_summary = "done"
        await emit_tool_activity(agent_id, tool_name, phase, tool_output="done")
        # Final import of subagent messages + mark STOPPED
        sub_agent_id = body.get("agent_id", "")
        last_msg = body.get("last_assistant_message", "")
        transcript_path = body.get("agent_transcript_path", "")
        if not sub_agent_id:
            logger.warning("SubagentStop hook: no agent_id in body for parent %s", agent_id[:8])
        elif not ad:
            logger.warning("SubagentStop hook: no agent_dispatcher for parent %s", agent_id[:8])
        if ad and sub_agent_id:
            try:
                from database import SessionLocal as _SL
                from agent_dispatcher import _parse_session_turns
                from websocket import emit_agent_update as _eau, emit_new_message as _enm
                known = ad._known_subagents.get(agent_id, {})
                info = known.get(sub_agent_id)
                if not info:
                    logger.warning(
                        "SubagentStop hook: unknown subagent %s for parent %s (known: %s)",
                        sub_agent_id[:12], agent_id[:8], list(known.keys()),
                    )
                if info:
                    sub_db_id = info["agent_id"]
                    _db = _SL()
                    try:
                        # Final parse of subagent JSONL if transcript path available
                        if transcript_path and os.path.isfile(transcript_path):
                            turns = _parse_session_turns(transcript_path)
                            existing_count = _db.query(Message).filter(
                                Message.agent_id == sub_db_id,
                            ).count()
                            if len(turns) > existing_count:
                                ad._import_turns_as_messages(
                                    _db, sub_db_id, turns[existing_count:],
                                )
                        sub_ag = _db.get(Agent, sub_db_id)
                        if sub_ag and sub_ag.status == AgentStatus.SYNCING:
                            if last_msg:
                                _preview = str(last_msg)[:200] if isinstance(last_msg, str) else str(last_msg.get("content", ""))[:200]
                                sub_ag.last_message_preview = _preview
                            ad.stop_agent_cleanup(
                                _db, sub_ag, "",
                                kill_tmux=False, add_message=False,
                                cancel_tasks=False,
                            )
                            _db.commit()
                            _project_name = sub_ag.project or ""
                            ad._emit(_eau(sub_db_id, "STOPPED", _project_name))
                            ad._emit(_enm(sub_db_id, "sync", sub_ag.name, _project_name))
                            logger.info(
                                "SubagentStop hook: marked subagent %s STOPPED",
                                sub_db_id,
                            )
                    finally:
                        _db.close()
            except Exception:
                logger.warning(
                    "SubagentStop hook: failed to finalize subagent for parent %s",
                    agent_id[:8], exc_info=True,
                )
    # --- Permission prompt ---
    elif hook_event == "Notification":
        ntype = body.get("notification_type", "")
        if ntype == "permission_prompt":
            tool_name = body.get("tool_name", "unknown")
            phase = "permission"
            kind = "permission"
            tool_input = body.get("tool_input")
            summary = _tool_input_summary(tool_name, tool_input) if tool_input else ""
            await emit_tool_activity(agent_id, tool_name, phase, tool_input=tool_input)
        else:
            return {}
    # --- Context compaction ---
    elif hook_event == "PreCompact":
        tool_name = "Compact"
        phase = "start"
        kind = "compact"
        summary = "context compaction"
        await emit_tool_activity(agent_id, tool_name, phase)
        # /compact skips UserPromptSubmit, so mark delivered + generating here.
        import slash_commands as _sc
        _compact_msg_id = _sc.mark_delivered(agent_id, "/compact")
        if ad:
            ad._start_generating(agent_id)
            logger.info("PreCompact: started generating for %s", agent_id[:8])
            from database import SessionLocal as _SLC
            _dbc = _SLC()
            try:
                _agc = _dbc.get(Agent, agent_id)
                if _agc:
                    _agc.generating_msg_id = _compact_msg_id or "compact"
                    _dbc.commit()
            finally:
                _dbc.close()
        # Pause sync — JSONL is being rewritten
        if ad and ad._sync_contexts.get(agent_id):
            ad._sync_contexts[agent_id].compact_notified = True
    else:
        return {}

    # --- Persist tool activity to DB ---
    if tool_name and phase:
        try:
            from database import SessionLocal as _SL2
            from models import ToolActivity as _TA
            _db2 = _SL2()
            try:
                # Get agent's current session_id for scoping
                _ag2 = _db2.get(Agent, agent_id)
                _sid = _ag2.session_id if _ag2 else agent_id

                if phase in ("start", "permission"):
                    _ta = _TA(
                        agent_id=agent_id,
                        session_id=_sid,
                        tool_name=tool_name,
                        kind=kind,
                        summary=summary or "",
                        started_at=_utcnow(),
                    )
                    _db2.add(_ta)
                    _db2.commit()
                elif phase == "end":
                    # Update the most recent unfinished entry for this tool
                    _existing = (
                        _db2.query(_TA)
                        .filter(
                            _TA.agent_id == agent_id,
                            _TA.session_id == _sid,
                            _TA.tool_name == tool_name,
                            _TA.ended_at.is_(None),
                        )
                        .order_by(_TA.started_at.desc())
                        .first()
                    )
                    if _existing:
                        _existing.ended_at = _utcnow()
                        _existing.output_summary = output_summary or None
                        _existing.is_error = is_error
                        _db2.commit()
                    else:
                        # No matching start — insert a completed record
                        _ta = _TA(
                            agent_id=agent_id,
                            session_id=_sid,
                            tool_name=tool_name,
                            kind=kind,
                            summary=summary or "",
                            output_summary=output_summary or None,
                            is_error=is_error,
                            started_at=_utcnow(),
                            ended_at=_utcnow(),
                        )
                        _db2.add(_ta)
                        _db2.commit()
            finally:
                _db2.close()
        except Exception:
            logger.warning("Failed to persist tool activity for %s", agent_id[:8], exc_info=True)

    # Wake the JSONL sync loop so new message content is picked up
    # immediately instead of waiting for the next poll cycle.
    if ad:
        ad.wake_sync(agent_id)

    return {}


@router.post("/api/hooks/agent-permission")
async def hook_agent_permission(request: Request):
    """PreToolUse hook for non-skip-permissions agents.

    Blocks until the user approves or denies the tool call from the web UI.
    Auto-allows safe read-only tools (Read, Glob, Grep, etc.) and any tool
    the user has previously marked "always allow" for this agent session.
    """
    agent_id = request.headers.get("X-Agent-Id", "").strip()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_permission: no X-Agent-Id and no session match")
            return {}

    if body.get("hook_event_name") != "PreToolUse":
        logger.warning("hook_agent_permission: unexpected event %s for agent %s", body.get("hook_event_name"), agent_id[:8])
        return {}

    tool_name = body.get("tool_name", "")
    tool_input = body.get("tool_input") or {}

    from permissions import PermissionManager, SAFE_TOOLS

    pm: PermissionManager | None = getattr(request.app.state, "permission_manager", None)
    if not pm:
        logger.warning("hook_agent_permission: no permission_manager on app.state for agent %s", agent_id[:8])
        return {}

    # Check if the agent actually needs permission gating
    from database import SessionLocal
    db = SessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        if not agent or agent.skip_permissions:
            return {}
        agent_name = agent.name or ""
        agent_project = agent.project or ""
    finally:
        db.close()

    # Auto-allow safe read-only tools
    if tool_name in SAFE_TOOLS:
        return {"hookSpecificOutput": {"permissionDecision": "allow"}}

    # Check session "always allow" rules
    if pm.check_always_allow(agent_id, tool_name):
        return {"hookSpecificOutput": {"permissionDecision": "allow"}}

    # Create pending request and broadcast to frontend
    from websocket import _tool_input_summary
    summary = _tool_input_summary(tool_name, tool_input) if tool_input else ""
    req = pm.create_request(agent_id, tool_name, tool_input, summary)

    from websocket import ws_manager
    await ws_manager.broadcast("permission_request", {
        "request_id": req.id,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "project": agent_project,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "summary": summary,
    })

    # Send push notification
    from notify import notify
    notify(
        "permission", agent_id,
        f"Permission: {tool_name}",
        f"{agent_name}: {summary[:100]}" if summary else f"{agent_name} wants to use {tool_name}",
        url=f"/agents/{agent_id}",
    )

    # Block until user responds, with configurable timeout (default 2h)
    _perm_timeout = int(os.getenv("AHIVE_PERMISSION_TIMEOUT", "7200"))
    try:
        decision, reason = await asyncio.wait_for(
            pm.wait_for_decision(req.id), timeout=_perm_timeout,
        )
    except asyncio.TimeoutError:
        pm.respond(req.id, "deny", "Permission timed out")
        from notify import notify
        notify("permission", agent_id, "Permission timed out",
               f"{agent_name}: {tool_name} auto-denied after timeout",
               url=f"/agents/{agent_id}")
        return {"hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": "Permission request timed out",
        }}

    # Wake sync after permission resolves — agent will proceed with tool use
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        ad.wake_sync(agent_id)

    if decision == "allow":
        return {"hookSpecificOutput": {"permissionDecision": "allow"}}
    else:
        return {"hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": reason or "Denied by user",
        }}


@router.post("/api/agents/{agent_id}/permission/{request_id}/respond")
async def respond_permission(
    agent_id: str, request_id: str,
    request: Request, db: Session = Depends(get_db),
):
    """User responds to a pending tool permission request."""
    body = await request.json()
    decision = body.get("decision")  # "allow" | "deny" | "allow_always"
    reason = body.get("reason", "")

    from permissions import PermissionManager
    pm: PermissionManager | None = getattr(request.app.state, "permission_manager", None)
    if not pm:
        raise HTTPException(status_code=500, detail="Permission manager not available")

    actual_decision = "allow" if decision in ("allow", "allow_always") else "deny"

    if decision == "allow_always":
        tool_name = body.get("tool_name", "")
        if tool_name:
            pm.add_always_allow(agent_id, tool_name)

    if not pm.respond(request_id, actual_decision, reason):
        raise HTTPException(status_code=404, detail="Permission request not found or already resolved")

    # Broadcast resolution so all frontend clients update
    from websocket import ws_manager
    await ws_manager.broadcast("permission_resolved", {
        "request_id": request_id,
        "agent_id": agent_id,
        "decision": actual_decision,
    })

    return {"detail": "ok"}


@router.get("/api/agents/{agent_id}/permissions/pending")
async def get_pending_permissions(agent_id: str, request: Request):
    """Get all pending permission requests for an agent."""
    from permissions import PermissionManager
    pm: PermissionManager | None = getattr(request.app.state, "permission_manager", None)
    if not pm:
        return []
    return pm.get_pending(agent_id)


@router.post("/api/hooks/agent-session-start")
async def hook_agent_session_start(request: Request):
    """Receive SessionStart hook from Claude Code agents.

    Managed agents (X-Agent-Id present): writes a signal file for
    _detect_successor() to track session rotation.

    Unmanaged sessions (no X-Agent-Id): creates an unlinked session entry
    so the user can confirm (adopt) it in the UI.  This is push-based
    detection that complements the polling-based tmux scan fallback.
    """
    agent_id = request.headers.get("X-Agent-Id", "").strip()

    try:
        body = await request.json()
    except Exception:
        logger.debug("SessionStart hook: failed to parse body (agent_id=%s)", agent_id[:8] if agent_id else "(none)")
        return {}

    # Claude Code sends session info — extract session_id
    session_id = ""
    if isinstance(body, dict):
        session_id = body.get("session_id", "") or ""
        if not session_id:
            session = body.get("session") or {}
            if isinstance(session, dict):
                session_id = session.get("session_id", "") or session.get("id", "") or ""

    if not session_id:
        logger.warning("SessionStart hook: no session_id in body (agent=%s)", agent_id[:8] if agent_id else "(none)")
        return {}

    source = ""
    if isinstance(body, dict):
        source = body.get("source", "") or ""

    if agent_id:
        # Compact completion — resume sync, but do NOT emit "Compact end"
        # here.  The sync engine emits the authoritative "end" signal only
        # after it has actually detected and imported the rewritten JSONL.
        # Emitting "end" prematurely here caused a false "compact done" in
        # the UI while the sync engine hadn't processed the new state yet.
        if source == "compact":
            # Fallback for compact completion — PostCompact is the primary
            # handler, but if it didn't fire (older Claude Code version,
            # hook failure), SessionStart still resumes sync.
            ad = getattr(request.app.state, "agent_dispatcher", None)
            if ad:
                ctx = ad._sync_contexts.get(agent_id)
                if ctx:
                    ctx.compact_notified = False
                    asyncio.create_task(ad.trigger_sync(agent_id))
            logger.info("SessionStart hook: agent=%s compact complete (fallback), session=%s",
                        agent_id[:8], session_id[:12])
            # Still write the rotation signal — session_id changed
            signal_path = f"/tmp/ahive-{agent_id}.newsession"
            try:
                with open(signal_path, "w") as f:
                    f.write(session_id)
            except OSError as e:
                logger.warning("SessionStart hook: failed to write rotation signal %s: %s", signal_path, e)
            return {}

        # Confirm /clear command execution — no Stop hook follows,
        # so mark both delivered and completed here.
        if source == "clear":
            import slash_commands as _sc
            _sc.mark_delivered_and_completed(agent_id, "/clear")

        # Managed agent — session rotation signal
        signal_path = f"/tmp/ahive-{agent_id}.newsession"
        try:
            with open(signal_path, "w") as f:
                f.write(session_id)
            logger.info("SessionStart hook: agent=%s session=%s (source=%s)",
                        agent_id[:8], session_id[:12], source or "unknown")
        except OSError as e:
            logger.warning("SessionStart hook: failed to write signal %s: %s", signal_path, e)

        # Wake sync loop — new session means new JSONL content
        ad = getattr(request.app.state, "agent_dispatcher", None)
        if ad:
            ad.wake_sync(agent_id)

        return {}

    # --- Unmanaged session: push-based detection ---
    # Extract CWD and tmux pane from headers (set via allowedEnvVars).
    cwd = request.headers.get("X-Session-Cwd", "").strip()
    tmux_pane = request.headers.get("X-Tmux-Pane", "").strip()

    if not cwd:
        logger.debug("SessionStart hook: unmanaged session %s has no CWD header", session_id[:12])
        return {}

    # If this tmux pane is already owned by an active agent, treat this as
    # a session rotation (e.g. /clear) — write a signal file instead of
    # creating a new unlinked entry.  This is critical for detected agents
    # that don't have AHIVE_AGENT_ID in their environment.
    if tmux_pane:
        from database import SessionLocal as _SL
        _db = _SL()
        try:
            pane_owner = _db.query(Agent).filter(
                Agent.tmux_pane == tmux_pane,
                Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]),
            ).first()
            if pane_owner:
                signal_path = f"/tmp/ahive-{pane_owner.id}.newsession"
                try:
                    with open(signal_path, "w") as f:
                        f.write(session_id)
                    logger.info(
                        "SessionStart hook: pane %s owned by agent %s — "
                        "wrote rotation signal for session %s",
                        tmux_pane, pane_owner.id[:8], session_id[:12],
                    )
                except OSError as e:
                    logger.warning("SessionStart hook: failed to write pane-owner signal %s: %s", signal_path, e)
                return {}
        finally:
            _db.close()

    # Match CWD to a registered project
    from database import SessionLocal
    db = SessionLocal()
    try:
        cwd_real = os.path.realpath(cwd)
        projects = db.query(Project).filter(Project.archived == False).all()
        matched_proj = None
        for p in projects:
            proj_real = os.path.realpath(p.path)
            if cwd_real == proj_real or cwd_real.startswith(proj_real + "/"):
                matched_proj = p
                break
        if not matched_proj:
            logger.debug(
                "SessionStart hook: unmanaged session %s CWD %s doesn't match any project",
                session_id[:12], cwd,
            )
            return {}

        # Guard: don't create entry if session already owned by an agent
        existing = db.query(Agent).filter(Agent.session_id == session_id).first()
        if existing:
            logger.debug(
                "SessionStart hook: session %s already owned by agent %s",
                session_id[:12], existing.id[:8],
            )
            return {}
    finally:
        db.close()

    # Resolve transcript JSONL path
    from session_cache import session_source_dir
    sdir = session_source_dir(matched_proj.path)
    transcript_path = os.path.join(sdir, f"{session_id}.jsonl")
    if not os.path.isfile(transcript_path):
        # JSONL may not exist yet at session start — that's OK,
        # the unlinked entry will be cleaned up later if it never appears.
        transcript_path = ""

    # Resolve tmux session name from pane ID
    tmux_session_name = None
    if tmux_pane:
        try:
            tmux_session_name = subprocess.check_output(
                ["tmux", "display-message", "-t", tmux_pane, "-p", "#{session_name}"],
                timeout=2, text=True,
            ).strip() or None
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass

    from agent_dispatcher import _write_unlinked_entry
    _write_unlinked_entry(
        session_id=session_id,
        cwd=cwd_real,
        transcript_path=transcript_path,
        tmux_pane=tmux_pane or None,
        tmux_session=tmux_session_name,
        project_name=matched_proj.name,
    )
    logger.info(
        "SessionStart hook: unmanaged session %s → unlinked entry (project=%s, pane=%s, tmux_session=%s)",
        session_id[:12], matched_proj.name, tmux_pane or "?", tmux_session_name or "?",
    )

    return {}
