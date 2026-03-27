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
from models import Agent, AgentMode, AgentStatus, Message, MessageRole, MessageStatus, Project, Task, TaskStatus
from utils import utcnow as _utcnow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hooks"])


# ---- Helpers ----

def _resolve_agent_id_from_body(body: dict) -> str:
    """Resolve agent_id from hook body when X-Agent-Id header is empty.

    All agents are now tmux-managed with AHIVE_AGENT_ID set in their
    environment. Sessions without this header are from non-managed
    ``claude -p`` processes and must be ignored.
    """
    sid = body.get("session_id", "").strip()
    if sid:
        logger.debug("_resolve_agent_id_from_body: ignoring non-managed session %s (no AHIVE_AGENT_ID)", sid[:12])
    return ""


def _is_subprocess_session(agent_id: str, hook_session_id: str, request: Request) -> bool:
    """Return True if a hook is from a Claude Code subprocess, not the main agent.

    When Claude Code's Agent tool spawns ``claude -p`` subprocesses, they
    inherit AHIVE_AGENT_ID and fire hooks with the parent agent's ID.
    These must be ignored to prevent session theft and false state changes.

    Checks if the hook's session_id differs from the agent's tracked
    session in the sync context.
    """
    if not agent_id or not hook_session_id:
        return False
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        return False
    ctx = ad._sync_contexts.get(agent_id)
    if not ctx or not ctx.session_id:
        return False
    return ctx.session_id != hook_session_id


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
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_session_end: no X-Agent-Id and no session match")
            return {}

    # Guard: ignore hooks from subprocess sessions (Agent tool inherits AHIVE_AGENT_ID)
    hook_sid = body.get("session_id", "") if isinstance(body, dict) else ""
    if _is_subprocess_session(agent_id, hook_sid, request):
        logger.info("hook_agent_session_end: ignoring subprocess session %s for agent %s",
                    hook_sid[:12], agent_id[:8])
        return {}

    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        logger.warning("hook_agent_session_end: no agent_dispatcher on app.state for agent %s", agent_id[:8])
        return {}

    # Signal that a rotation is expected — SessionStart should accept the next session.
    ctx = ad._sync_contexts.get(agent_id)
    if ctx:
        ctx.awaiting_rotation = True

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
    delivery event, so we mark delivered_at directly here.  The sync engine
    guards with `if not web_msg.delivered_at` so it won't overwrite.
    """
    agent_id = request.headers.get("X-Agent-Id", "").strip()
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_user_prompt: no X-Agent-Id and no session match (headers: %s)", dict(request.headers))
            return {}

    # Guard: ignore hooks from subprocess sessions (Agent tool inherits AHIVE_AGENT_ID)
    hook_sid = body.get("session_id", "") if isinstance(body, dict) else ""
    if _is_subprocess_session(agent_id, hook_sid, request):
        logger.info("hook_agent_user_prompt: ignoring subprocess session %s for agent %s",
                    hook_sid[:12], agent_id[:8])
        return {}

    logger.info("hook_agent_user_prompt: received for agent %s", agent_id[:8])

    # Mark the most recent undelivered web-sent message as delivered.
    from websocket import emit_message_delivered
    db = SessionLocal()
    msg = None
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
            msg.status = MessageStatus.COMPLETED
            msg.completed_at = now
            db.commit()

            from display_writer import update_last
            update_last(agent_id, msg.id)

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
        # Wait for JSONL flush then wake sync
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
    except (ValueError, UnicodeDecodeError):
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_stop: no X-Agent-Id and no session match")
            return {}

    # Guard: ignore hooks from subprocess sessions (Agent tool inherits AHIVE_AGENT_ID)
    hook_sid = body.get("session_id", "") if isinstance(body, dict) else ""
    if _is_subprocess_session(agent_id, hook_sid, request):
        logger.info("hook_agent_stop: ignoring subprocess session %s for agent %s",
                    hook_sid[:12], agent_id[:8])
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
                # Wait for JSONL flush then wake sync
                async def _post_stop_sync(_aid):
                    from config import JSONL_FLUSH_DELAY
                    await asyncio.sleep(JSONL_FLUSH_DELAY)
                    ad.wake_sync(_aid)
                asyncio.ensure_future(_post_stop_sync(agent_id))

                # Mark any EXECUTING slash commands as completed + delivered.
                import slash_commands as _sc
                _sc.mark_completed(agent_id)
        else:
            # Edge case: no sync context for agent (e.g., dispatcher not yet
            # initialized or agent registered before sync started).
            logger.debug(
                "hook_agent_stop: no sync context for %s (dispatcher may not be initialized)",
                agent_id[:8],
            )
    else:
        logger.warning("hook_agent_stop: no agent_dispatcher on app.state")

    logger.info("hook_agent_stop: agent=%s done", agent_id[:8])

    # --- Dispatch next queued message (stop-hook-driven queue drain) ---
    # Messages sent while agent was busy are stored as PENDING.
    # Now that the agent has stopped generating, send the first one.
    if ad:
        async def _dispatch_pending(_aid):
            # Wait for the sync cycle (triggered by _post_stop_sync above)
            # to finish importing the agent's last response from JSONL into
            # the display file.  Without this, the queued user message would
            # be flushed (via UserPromptSubmit → update_last) before the
            # preceding agent response, giving it a lower display_seq and
            # making it appear above the response in the chat.
            from config import JSONL_FLUSH_DELAY
            await asyncio.sleep(JSONL_FLUSH_DELAY + 0.35)
            _dispatch_db = SessionLocal()
            try:
                pending_msg = (
                    _dispatch_db.query(Message)
                    .filter(
                        Message.agent_id == _aid,
                        Message.role == MessageRole.USER,
                        Message.status == MessageStatus.PENDING,
                        Message.scheduled_at.is_(None),
                    )
                    .order_by(Message.created_at.asc())
                    .first()
                )
                if not pending_msg:
                    return

                _agent = _dispatch_db.get(Agent, _aid)
                if not _agent or not _agent.tmux_pane:
                    return

                from agent_dispatcher import send_tmux_message, verify_tmux_pane
                if not verify_tmux_pane(_agent.tmux_pane):
                    logger.warning(
                        "hook_agent_stop: tmux pane gone for agent %s, skipping dispatch",
                        _aid[:8],
                    )
                    return

                ok = send_tmux_message(_agent.tmux_pane, pending_msg.content)
                if ok:
                    pending_msg.status = MessageStatus.QUEUED
                    pending_msg.dispatch_seq = ad.next_dispatch_seq(_dispatch_db, _aid)
                    _dispatch_db.commit()

                    from websocket import emit_message_update
                    asyncio.ensure_future(emit_message_update(_aid, pending_msg.id, "QUEUED"))

                    # No flush_agent here — the message has no delivered_at
                    # yet, so display_writer skips it.  It enters the display
                    # file when UserPromptSubmit sets delivered_at.
                    logger.info(
                        "hook_agent_stop: dispatched pending message %s to agent %s",
                        pending_msg.id[:8], _aid[:8],
                    )
                else:
                    logger.warning(
                        "hook_agent_stop: send_tmux_message failed for agent %s, "
                        "will retry on next stop hook",
                        _aid[:8],
                    )
            except Exception:
                logger.exception("hook_agent_stop: error dispatching pending message for %s", _aid[:8])
            finally:
                _dispatch_db.close()

        asyncio.ensure_future(_dispatch_pending(agent_id))

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
    except (ValueError, UnicodeDecodeError):
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_post_compact: no agent_id")
            return {}

    # Guard: ignore hooks from subprocess sessions
    hook_sid = body.get("session_id", "") if isinstance(body, dict) else ""
    if _is_subprocess_session(agent_id, hook_sid, request):
        logger.info("hook_agent_post_compact: ignoring subprocess session %s for agent %s",
                    hook_sid[:12], agent_id[:8])
        return {}

    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        logger.warning("hook_agent_post_compact: no agent_dispatcher")
        return {}

    # 1. Clear compact pause flag and generating state.
    #    After /compact, Claude returns to the prompt — it's no longer executing.
    logger.info("hook_agent_post_compact: compact done for %s", agent_id[:8])

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
            # Mark non-promotable: /compact never appears in post-compact
            # JSONL, so it must not consume a real user turn's UUID
            if not compact_msg.jsonl_uuid:
                compact_msg.jsonl_uuid = f"slash-{compact_msg.id[:8]}"

        # 3. End the compact tool activity in DB.
        from sync_engine import _end_compact_activity
        _compact_activity_id = None
        if ctx:
            _compact_activity_id = _end_compact_activity(db, agent_id, ctx.session_id)
        db.commit()

        # Update display file with delivery/completion status
        from display_writer import update_last
        if compact_msg:
            update_last(agent_id, compact_msg.id)
        if _compact_activity_id:
            update_last(agent_id, _compact_activity_id)

        # 4. Write "Conversation compacted" system bubble to display file.
        #    Don't wait for the sync loop — write it now so it appears immediately.
        #    _create_system_msg in sync_engine will content-dedup if sync re-imports.
        #    Use /compact message's completed_at as the anchor — this is the moment
        #    compact finished, guaranteed before the new session's JSONL timestamps.
        _compact_ts = compact_msg.completed_at if compact_msg else _utcnow()
        compact_sys = Message(
            agent_id=agent_id,
            role=MessageRole.SYSTEM,
            content="Conversation compacted",
            status=MessageStatus.COMPLETED,
            source="hook",
            created_at=_compact_ts,
            completed_at=_compact_ts,
            delivered_at=_compact_ts,
            jsonl_uuid=f"compact-sys-{agent_id[:8]}",
        )
        db.add(compact_sys)

        # 5. Transition agent status.
        #    After /compact, Claude returns to the prompt — no longer executing.
        #    All agents are tmux-managed → IDLE (sync loop keeps tailing the new session)
        agent = db.get(Agent, agent_id)
        if agent and agent.status == AgentStatus.EXECUTING:
            agent.status = AgentStatus.IDLE
            agent.generating_msg_id = None

        db.commit()

        # Don't flush here — let the deferred sync loop pick up both the
        # "Conversation compacted" and "This session is being continued..."
        # messages in a single flush_agent() call so they appear together.

        # 6. Emit completed_at update so frontend shows double tick.
        if compact_msg:
            from websocket import emit_message_update
            asyncio.ensure_future(emit_message_update(
                agent_id, compact_msg.id, "COMPLETED",
                completed_at=compact_msg.completed_at.isoformat(),
            ))

        # 8. Emit agent status update to frontend.
        _post_status = agent.status.value if agent else "IDLE"
        if agent:
            from websocket import ws_manager
            asyncio.ensure_future(ws_manager.broadcast("agent_update", {
                "agent_id": agent_id,
                "status": _post_status,
            }))
    finally:
        db.close()

    # 9. Defer JSONL read + purge to sync loop — PostCompact is blocking,
    #    so the rewritten JSONL may not be flushed yet.  The sync loop's
    #    compact_turn_decrease path handles purge + reconcile.
    if ctx:
        ctx.compact_detected_at = 0.0

        async def _post_compact_sync(_aid):
            from config import JSONL_FLUSH_DELAY
            await asyncio.sleep(JSONL_FLUSH_DELAY)
            ad.wake_sync(_aid)
        asyncio.ensure_future(_post_compact_sync(agent_id))

    # 10. Emit "Compact end" tool activity to frontend (WS acceleration).
    from websocket import emit_tool_activity
    await emit_tool_activity(agent_id, "Compact", "end",
                             tool_output="context compacted")

    # 11. Stop generating LAST — must follow tool_activity "end" because
    #     useStreamingAgents treats ANY tool_activity event as "active".
    #     agent_stream_end must be the final signal to clear the typing indicator.
    ad._stop_generating(agent_id)

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
    except (ValueError, UnicodeDecodeError):
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_tool_activity: no X-Agent-Id and no session match")
            return {}

    # Guard: ignore hooks from subprocess sessions.
    # Exception: permission_prompt notifications pass through so native
    # Claude Code permission prompts surface in the web UI as interactive cards.
    hook_sid = body.get("session_id", "") if isinstance(body, dict) else ""
    hook_event = body.get("hook_event_name", "")
    if _is_subprocess_session(agent_id, hook_sid, request):
        if not (hook_event == "Notification"
                and body.get("notification_type") == "permission_prompt"):
            return {}

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
        # Interactive cards (AskUserQuestion/ExitPlanMode): wake sync loop
        # immediately so it imports the assistant turn from JSONL. By the
        # time PreToolUse fires, the tool_use block is already in JSONL.
        if tool_name in ("AskUserQuestion", "ExitPlanMode") and ad:
            # Wait for JSONL flush then wake sync
            async def _delayed_interactive_wake(_aid):
                from config import JSONL_FLUSH_DELAY
                await asyncio.sleep(JSONL_FLUSH_DELAY)
                ad.wake_sync(_aid)
            asyncio.ensure_future(_delayed_interactive_wake(agent_id))
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
                    # that still have answer=None.
                    _answer_text = str(tool_output)[:500]
                    _patched_any = False
                    _msgs = _db.query(Message).filter(
                        Message.agent_id == agent_id,
                        Message.tool_use_id == tool_use_id,
                    ).order_by(Message.created_at.desc()).all()
                    for _msg in _msgs:
                        try:
                            _meta = json.loads(_msg.meta_json)
                        except (json.JSONDecodeError, TypeError):
                            logger.debug("Malformed meta_json for message %s", _msg.id)
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
                                logger.debug("Malformed meta_json for message %s", _msg.id)
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
                    status=_AS.IDLE,
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
                ad._emit(_eau(_sub.id, "IDLE", _project_name))
                logger.info(
                    "SubagentStart hook: created subagent %s (%s) for parent %s",
                    _sub.id, _name, agent_id[:8],
                )
            finally:
                _db.close()
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
                            ad._import_turns_as_messages_deduped(
                                _db, sub_db_id, turns[existing_count:],
                            )
                    sub_ag = _db.get(Agent, sub_db_id)
                    if sub_ag and sub_ag.status == AgentStatus.IDLE:
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

    # --- Persist tool activity as Message → display file pipeline ---
    # Skip subagent bubbles during compact — they're internal implementation
    # detail of the compaction process, not user-visible tool activity.
    _in_compact = False
    if ad:
        _ctx = ad._sync_contexts.get(agent_id)
        if _ctx and _ctx.compact_notified:
            _in_compact = True
    if tool_name and phase and not (kind == "subagent" and _in_compact):
        from database import SessionLocal as _SL2
        from uuid import uuid4
        _db2 = _SL2()
        try:
            _tool_use_id = body.get("tool_use_id", "") or ""
            _tool_uuid = f"tool-{_tool_use_id}" if _tool_use_id else f"tool-{uuid4().hex[:12]}"

            if phase in ("start", "permission"):
                _tool_msg = Message(
                    agent_id=agent_id,
                    role=MessageRole.SYSTEM,
                    kind="tool_activity",
                    content=summary or "",
                    source="hook",
                    status=MessageStatus.EXECUTING,
                    meta_json=json.dumps({
                        "tool_name": tool_name,
                        "tool_kind": kind,
                        "tool_use_id": _tool_use_id,
                        "phase": "start",
                    }),
                    jsonl_uuid=_tool_uuid,
                )
                _db2.add(_tool_msg)
                _db2.commit()
                # Flush immediately so tool_activity persists in the
                # display file.  WS events provide real-time feedback,
                # but the display file is the durable source of truth.
                from display_writer import flush_agent as _flush_ta
                _flush_ta(agent_id)

                # --- Native permission prompt: interactive card ---
                # When Claude Code shows a permission prompt in the terminal
                # (e.g. sensitive-file access), create an interactive card so
                # the user can respond from the web UI via tmux keys.
                if kind == "permission":
                    _perm_id = f"perm-{uuid4().hex[:12]}"
                    _perm_question = summary or f"{tool_name} requires permission"
                    _perm_meta = {
                        "interactive": [{
                            "type": "permission_prompt",
                            "tool_use_id": _perm_id,
                            "tool_name": tool_name,
                            "questions": [{
                                "header": "Permission",
                                "question": _perm_question,
                                "options": [
                                    {"label": "Yes", "description": "Allow this operation"},
                                    {"label": "Yes, and always allow", "description": "Don't ask again for this scope"},
                                    {"label": "No", "description": "Block this operation"},
                                ],
                            }],
                            "answer": None,
                        }],
                    }
                    _perm_msg = Message(
                        agent_id=agent_id,
                        role=MessageRole.AGENT,
                        kind=None,
                        content="",
                        source="hook",
                        status=MessageStatus.COMPLETED,
                        meta_json=json.dumps(_perm_meta),
                        tool_use_id=_perm_id,
                    )
                    _db2.add(_perm_msg)
                    _db2.commit()
                    _flush_ta(agent_id)

            elif phase == "end":
                # Find the start message by tool UUID
                _existing = (
                    _db2.query(Message)
                    .filter(
                        Message.agent_id == agent_id,
                        Message.jsonl_uuid == _tool_uuid,
                    )
                    .first()
                )
                if _existing:
                    _meta = json.loads(_existing.meta_json or "{}")
                    _meta["phase"] = "end"
                    _meta["output_summary"] = output_summary or ""
                    _meta["is_error"] = is_error
                    _existing.meta_json = json.dumps(_meta)
                    _existing.completed_at = _utcnow()
                    _existing.status = MessageStatus.COMPLETED
                    _db2.commit()
                    from display_writer import flush_agent, update_last
                    flush_agent(agent_id)
                    update_last(agent_id, _existing.id)
                else:
                    # No matching start — insert a completed record
                    _tool_msg = Message(
                        agent_id=agent_id,
                        role=MessageRole.SYSTEM,
                        kind="tool_activity",
                        content=summary or "",
                        source="hook",
                        status=MessageStatus.COMPLETED,
                        completed_at=_utcnow(),
                        meta_json=json.dumps({
                            "tool_name": tool_name,
                            "tool_kind": kind,
                            "tool_use_id": _tool_use_id,
                            "phase": "end",
                            "output_summary": output_summary or "",
                            "is_error": is_error,
                        }),
                        jsonl_uuid=_tool_uuid,
                    )
                    _db2.add(_tool_msg)
                    _db2.commit()
                    from display_writer import flush_agent
                    flush_agent(agent_id)
        finally:
            _db2.close()

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
    except (ValueError, UnicodeDecodeError):
        body = {}
    if not agent_id:
        agent_id = _resolve_agent_id_from_body(body)
        if not agent_id:
            logger.warning("hook_agent_permission: no X-Agent-Id and no session match")
            return {}

    # Guard: ignore hooks from subprocess sessions
    hook_sid = body.get("session_id", "") if isinstance(body, dict) else ""
    if _is_subprocess_session(agent_id, hook_sid, request):
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
    except (ValueError, UnicodeDecodeError):
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

    logger.info("SessionStart hook: agent=%s session=%s source=%r",
                agent_id[:8] if agent_id else "(none)", session_id[:12], source)

    if agent_id:
        # Emitting "end" prematurely here caused a false "compact done" in
        # the UI while the sync engine hadn't processed the new state yet.
        if source == "compact":
            # Compact creates a new session — rotate immediately so the
            # sync loop starts tailing the new JSONL (imports the
            # "continued from" system message) without waiting for idle
            # poll detection (~60s).
            ad = getattr(request.app.state, "agent_dispatcher", None)
            if ad:
                ctx = ad._sync_contexts.get(agent_id)
                if ctx:
                    ctx.compact_notified = False

                # Look up project_path + worktree for rotation
                _proj_path = None
                _worktree = None
                _db_ss = SessionLocal()
                try:
                    _ag_ss = _db_ss.get(Agent, agent_id)
                    if _ag_ss:
                        _worktree = _ag_ss.worktree
                        _proj_ss = _db_ss.get(Project, _ag_ss.project) if _ag_ss.project else None
                        _proj_path = _proj_ss.path if _proj_ss else None
                finally:
                    _db_ss.close()

                if _proj_path:
                    # Tmux agent: rotate session in-place and start fresh
                    # sync loop with the new JSONL.
                    rotated = ad._rotate_agent_session(
                        agent_id, session_id, _proj_path,
                        worktree=_worktree,
                    )
                    if rotated:
                        # Wake the new sync loop so it imports immediately
                        ad.wake_sync(agent_id)
                        logger.info(
                            "SessionStart hook: agent=%s compact rotation to %s",
                            agent_id[:8], session_id[:12],
                        )
                    else:
                        logger.warning(
                            "SessionStart hook: compact rotation failed for %s",
                            agent_id[:8],
                        )
                else:
                    # Fallback: write signal file for poll-based detection
                    signal_path = f"/tmp/ahive-{agent_id}.newsession"
                    try:
                        with open(signal_path, "w") as f:
                            f.write(session_id)
                    except OSError as e:
                        logger.warning("SessionStart hook: failed to write rotation signal %s: %s", signal_path, e)
                    logger.info(
                        "SessionStart hook: agent=%s compact fallback signal for %s",
                        agent_id[:8], session_id[:12],
                    )

            return {}

        # Confirm /clear command execution — no Stop hook follows,
        # so mark both delivered and completed here.
        if source == "clear":
            import slash_commands as _sc
            _sc.mark_delivered_and_completed(agent_id, "/clear")

        # Guard: ignore SessionStart from subprocesses (Agent tool inherits
        # AHIVE_AGENT_ID).  Accept if awaiting_rotation (set by SessionEnd)
        # or if this is a /clear rotation.
        if source != "clear":
            ad_check = getattr(request.app.state, "agent_dispatcher", None)
            if ad_check:
                ctx = ad_check._sync_contexts.get(agent_id)
                if ctx and ctx.session_id and ctx.session_id != session_id:
                    if not ctx.awaiting_rotation:
                        logger.info(
                            "SessionStart hook: agent=%s has active session %s, "
                            "ignoring subprocess session %s",
                            agent_id[:8], ctx.session_id[:12], session_id[:12],
                        )
                        return {}
                    else:
                        ctx.awaiting_rotation = False

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

    # --- Unmanaged session: no longer tracked ---
    # Only tmux agents managed by the orchestrator are synced.
    # User-started `claude` or `claude -p` sessions are ignored.
    logger.debug(
        "SessionStart hook: ignoring unmanaged session %s (no X-Agent-Id)",
        session_id[:12],
    )
    return {}
