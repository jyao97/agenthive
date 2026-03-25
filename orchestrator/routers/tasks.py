"""Task routes — CRUD, dispatch, cancel, complete, queue status, worktree names."""

import asyncio
import json
import logging
import os
import re
import threading

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import CC_MODEL, OPENAI_API_KEY, VALID_MODELS
from database import get_db
from models import Agent, AgentMode, AgentStatus, Message, MessageRole, MessageStatus, Project, Task, TaskStatus
from schemas import MessageOut, TaskCreate, TaskDetailOut, TaskOut, TaskUpdate
from task_state_machine import can_transition, InvalidTransitionError
from task_state import TaskStateMachine
from websocket import emit_task_update, emit_agent_update
from route_helpers import (
    check_project_capacity, create_tmux_claude_session,
    generate_worktree_name_local, resolve_project_path,
)
from utils import utcnow as _utcnow

logger = logging.getLogger("orchestrator")

router = APIRouter(tags=["tasks"])


# ---- Helpers ----

def _stop_task_agents(db: Session, task, ad, reason, *, emit=True, add_message=False):
    """Stop the agent linked to a task."""
    if task.agent_id:
        agent = db.get(Agent, task.agent_id)
        if agent:
            if ad:
                ad.stop_agent_cleanup(db, agent, reason, add_message=add_message, emit=emit)
            elif agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
                import subprocess as _sp
                agent.status = AgentStatus.STOPPED
                if agent.tmux_pane:
                    _kill = _sp.run(["tmux", "kill-session", "-t", f"ah-{agent.id[:8]}"],
                            capture_output=True, timeout=5)
                    if _kill.returncode != 0:
                        logger.debug("tmux kill-session failed for agent %s", agent.id[:8])
                    agent.tmux_pane = None
                if emit:
                    asyncio.ensure_future(emit_agent_update(agent.id, "STOPPED", agent.project))


_generate_worktree_name_local = generate_worktree_name_local


def _dispatch_task_tmux(db: Session, task: Task, proj: Project, ad) -> str | None:
    """Create a tmux agent for a task. Returns agent_id or None.

    Extracted from launch_tmux_agent endpoint so dispatch_task_v2 can
    create tmux agents through the unified pipeline.
    """
    import secrets
    import shlex
    import subprocess as _sp
    import uuid as _uuid

    from config import CLAUDE_BIN

    prompt = task.description or task.title
    model = task.model or proj.default_model or CC_MODEL
    if model not in VALID_MODELS:
        model = CC_MODEL
    effort = task.effort or "high"
    worktree = None
    if getattr(task, "use_worktree", True):
        worktree = task.worktree_name or _generate_worktree_name_local(prompt)
        task.worktree_name = worktree
        task.branch_name = task.branch_name or f"worktree-{worktree}"
    skip_permissions = getattr(task, "skip_permissions", True)

    # Get existing tmux session names for collision check
    try:
        _tmux_ls = _sp.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        _existing_tmux = set(_tmux_ls.stdout.strip().splitlines()) if _tmux_ls.returncode == 0 else set()
    except (OSError, _sp.TimeoutExpired):
        _existing_tmux = set()

    # Generate unique agent ID
    for _ in range(20):
        agent_hex = secrets.token_hex(6)
        tmux_session = f"ah-{agent_hex[:8]}"
        if db.get(Agent, agent_hex) is None and tmux_session not in _existing_tmux:
            break
    else:
        return None

    # Pre-generate session UUID and write .owner sidecar
    pre_session_id = str(_uuid.uuid4())
    from agent_dispatcher import _write_session_owner
    from session_cache import session_source_dir
    _sdir = session_source_dir(proj.path)
    os.makedirs(_sdir, exist_ok=True)
    _write_session_owner(_sdir, pre_session_id, agent_hex)

    # Build claude command (interactive mode)
    cmd_parts = [CLAUDE_BIN, "--session-id", pre_session_id,
                 "--output-format", "stream-json", "--verbose"]
    if skip_permissions:
        cmd_parts.append("--dangerously-skip-permissions")
    if model:
        cmd_parts += ["--model", model]
    if effort:
        cmd_parts += ["--effort", effort]
    if worktree:
        cmd_parts += ["--worktree", worktree]
    claude_cmd = " ".join(shlex.quote(p) for p in cmd_parts)

    from routers.agents import _preflight_claude_project, _launch_tmux_background
    _preflight_claude_project(proj.path)
    pane_id = create_tmux_claude_session(tmux_session, proj.path, claude_cmd, agent_id=agent_hex)

    # Create Agent record
    agent = Agent(
        id=agent_hex,
        project=proj.name,
        name=f"Task: {task.title[:80]}",
        mode=AgentMode.AUTO,
        status=AgentStatus.STARTING,
        model=model,
        cli_sync=True,
        tmux_pane=pane_id,
        effort=effort,
        worktree=worktree if worktree else None,
        skip_permissions=skip_permissions,
        task_id=task.id,
        muted=False,
        last_message_preview=f"Task: {task.title[:80]}",
        last_message_at=_utcnow(),
    )
    db.add(agent)
    db.flush()

    # Prepare prompt with insights via _prepare_dispatch
    launch_prompt = None
    if prompt and ad:
        msg, launch_prompt, _ = ad._prepare_dispatch(
            db, agent, proj, prompt,
            source="task",
            wrap_prompt=True,
        )
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = _utcnow()

    # Schedule background task to send prompt to tmux
    if ad and launch_prompt:
        launch_task = asyncio.ensure_future(
            _launch_tmux_background(
                ad, agent_hex, pane_id, launch_prompt, proj.path,
                pre_session_id=pre_session_id,
            )
        )
        ad.track_launch_task(agent_hex, launch_task)

    return agent_hex


# ---- Routes ----

@router.post("/api/v2/tasks", response_model=TaskOut, status_code=201)
async def create_task_v2(body: TaskCreate, db: Session = Depends(get_db)):
    """Create a new task. Starts as INBOX unless auto_dispatch is set."""
    # Auto-generate title from description if blank
    title = body.title.strip() if body.title else ""
    if not title and body.description:
        desc = body.description.strip()
        if len(desc) <= 60:
            title = desc
        else:
            cut = desc[:60].rsplit(" ", 1)[0] if " " in desc[:60] else desc[:60]
            title = cut + "..."
    if not title:
        title = "Untitled task"

    initial_status = TaskStatus.INBOX
    if body.auto_dispatch and body.project_name:
        proj = db.query(Project).filter(Project.name == body.project_name).first()
        if not proj:
            raise HTTPException(400, f"Project not found: {body.project_name}")
        initial_status = TaskStatus.PENDING

    task = Task(
        title=title,
        description=body.description,
        project_name=body.project_name,
        priority=body.priority,
        model=body.model,
        effort=body.effort,
        skip_permissions=body.skip_permissions,
        sync_mode=body.sync_mode,
        use_worktree=body.use_worktree,
        use_tmux=body.use_tmux,
        notify_at=body.notify_at,
        status=initial_status,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@router.get("/api/v2/tasks/counts")
async def task_counts(project: str | None = None, db: Session = Depends(get_db)):
    """Return perspective counts + weekly success stats.

    Optional ``project`` query param filters to a single project.
    """
    from datetime import timedelta

    # Base filter — optionally scoped to a project
    def _pf(q):
        return q.filter(Task.project_name == project) if project else q

    # Perspective counts (server-side)
    rows = _pf(db.query(Task.status, func.count(Task.id))).group_by(Task.status).all()
    by_status = {s.value: c for s, c in rows}

    done_statuses = ["COMPLETE", "CANCELLED", "REJECTED", "FAILED", "TIMEOUT"]

    counts = {
        "INBOX": by_status.get("INBOX", 0),
        "PLANNING": 0,
        "QUEUE": by_status.get("PENDING", 0),
        "ACTIVE": by_status.get("EXECUTING", 0),
        "REVIEW": 0,
        "DONE": sum(by_status.get(s, 0) for s in done_statuses),
        "DONE_COMPLETED": by_status.get("COMPLETE", 0),
    }

    # Weekly stats — tasks that reached a terminal state this week
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    terminal = [TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.TIMEOUT,
                TaskStatus.REJECTED, TaskStatus.CANCELLED]
    weekly_q = _pf(db.query(
        Task.status, func.count(Task.id)
    )).filter(
        Task.status.in_(terminal),
        Task.completed_at >= week_ago,
    ).group_by(Task.status).all()

    weekly_by = {s.value: c for s, c in weekly_q}
    weekly_total = sum(weekly_by.values())
    weekly_completed = weekly_by.get("COMPLETE", 0)
    weekly_pct = round(weekly_completed / weekly_total * 100) if weekly_total else 0

    # Daily breakdown for the last 7 days (for sparkline chart)
    daily_rows = _pf(db.query(
        func.date(Task.completed_at).label("day"),
        Task.status,
        func.count(Task.id),
    )).filter(
        Task.status.in_(terminal),
        Task.completed_at >= week_ago,
    ).group_by("day", Task.status).all()

    daily_map: dict[str, dict] = {}
    for day_val, status, cnt in daily_rows:
        d = str(day_val)
        if d not in daily_map:
            daily_map[d] = {"date": d, "total": 0, "completed": 0}
        daily_map[d]["total"] += cnt
        if status == TaskStatus.COMPLETE:
            daily_map[d]["completed"] += cnt

    # Fill missing days and compute success_pct
    daily = []
    for i in range(7):
        d = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
        entry = daily_map.get(d, {"date": d, "total": 0, "completed": 0})
        entry["success_pct"] = round(entry["completed"] / entry["total"] * 100) if entry["total"] else None
        daily.append(entry)

    return {
        **counts,
        "weekly_total": weekly_total,
        "weekly_completed": weekly_completed,
        "weekly_success_pct": weekly_pct,
        "weekly_failed": weekly_by.get("FAILED", 0),
        "weekly_timeout": weekly_by.get("TIMEOUT", 0),
        "weekly_cancelled": weekly_by.get("CANCELLED", 0),
        "weekly_rejected": weekly_by.get("REJECTED", 0),
        "daily": daily,
    }


@router.get("/api/v2/tasks/queue")
async def task_queue_status(
    tz_offset: int = Query(default=0, description="Client timezone offset in minutes (e.g. -420 for PDT)"),
    db: Session = Depends(get_db),
):
    """Return queue status: pending/executing tasks + per-project capacity."""
    # Dispatcher uses these to gate capacity
    dispatcher_active = [AgentStatus.STARTING, AgentStatus.EXECUTING, AgentStatus.SYNCING]
    # All non-stopped agents (full picture for UI)
    alive_statuses = [AgentStatus.STARTING, AgentStatus.IDLE, AgentStatus.EXECUTING, AgentStatus.SYNCING]

    # ── Source of truth: agents table ──
    # All non-stopped, non-subagent agents
    active_agents = (
        db.query(Agent)
        .filter(Agent.status.in_(alive_statuses), Agent.is_subagent == False)
        .order_by(Agent.created_at.asc())
        .all()
    )

    # Build unified agent list with effective status + task info
    agents_list = []
    for a in active_agents:
        # Effective status: SYNCING + is_generating → EXECUTING (matches Agents page logic)
        effective = a.status.value
        if a.status == AgentStatus.SYNCING and a.is_generating:
            effective = "EXECUTING"
        entry = {
            "agent_id": a.id,
            "name": a.name,
            "project": a.project,
            "status": effective,
            "model": a.model,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        # Attach task info if linked
        if a.task_id:
            task = db.get(Task, a.task_id)
            if task:
                entry["task_id"] = task.id
                entry["task_title"] = task.title
        agents_list.append(entry)

    # Pending tasks (no agent assigned yet) — still need to show in queue
    pending_tasks = (
        db.query(Task)
        .filter(Task.status == TaskStatus.PENDING)
        .order_by(Task.priority.desc(), Task.created_at.asc())
        .all()
    )
    pending_list = [TaskOut.model_validate(t).model_dump() for t in pending_tasks]

    # Per-project capacity
    projects = db.query(Project).filter(Project.archived == False).all()
    capacity = {}
    for proj in projects:
        active = (
            db.query(func.count(Agent.id))
            .filter(Agent.project == proj.name, Agent.status.in_(dispatcher_active))
            .scalar()
        )
        alive = (
            db.query(func.count(Agent.id))
            .filter(Agent.project == proj.name, Agent.status.in_(alive_statuses))
            .scalar()
        )
        syncing = (
            db.query(func.count(Agent.id))
            .filter(Agent.project == proj.name, Agent.status == AgentStatus.SYNCING)
            .scalar()
        )
        capacity[proj.name] = {
            "max_concurrent": proj.max_concurrent,
            "active": active,
            "alive": alive,
            "syncing": syncing,
        }

    # Today's completed tasks (in client's local timezone)
    client_tz = timezone(timedelta(minutes=-tz_offset))
    today_start = datetime.now(client_tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    terminal = [TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.TIMEOUT,
                TaskStatus.REJECTED, TaskStatus.CANCELLED]
    today_done = (
        db.query(Task)
        .filter(Task.status.in_(terminal), Task.completed_at >= today_start)
        .order_by(Task.completed_at.desc())
        .all()
    )
    today_done_list = [TaskOut.model_validate(t).model_dump() for t in today_done]

    return {
        "agents": agents_list,
        "pending": pending_list,
        "capacity": capacity,
        "today_done": today_done_list,
    }


@router.get("/api/v2/tasks", response_model=list[TaskOut])
async def list_tasks_v2(
    status: str | None = None,
    statuses: str | None = None,
    project: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """List v2 tasks with optional filters."""
    q = db.query(Task)
    if statuses:
        status_list = []
        for s in statuses.split(","):
            s = s.strip()
            if not s:
                continue
            try:
                status_list.append(TaskStatus(s))
            except ValueError:
                raise HTTPException(400, f"Invalid status: {s}")
        if status_list:
            q = q.filter(Task.status.in_(status_list))
    elif status:
        try:
            q = q.filter(Task.status == TaskStatus(status))
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
    if project:
        q = q.filter(Task.project_name == project)
    tasks = q.order_by(Task.created_at.desc()).limit(limit).all()

    # Enrich EXECUTING tasks with agent info
    results = []
    enrich_agent_ids = [t.agent_id for t in tasks if t.status == TaskStatus.EXECUTING and t.agent_id]
    agent_map = {}
    if enrich_agent_ids:
        agents = db.query(Agent).filter(Agent.id.in_(enrich_agent_ids)).all()
        agent_map = {a.id: a for a in agents}

    now = datetime.now(timezone.utc)
    for t in tasks:
        out = TaskOut.model_validate(t)
        if t.status == TaskStatus.EXECUTING and t.agent_id:
            agent = agent_map.get(t.agent_id)
            if agent and agent.last_message_preview:
                out.last_agent_message = agent.last_message_preview[:200]
            if t.started_at:
                started = t.started_at if t.started_at.tzinfo else t.started_at.replace(tzinfo=timezone.utc)
                out.elapsed_seconds = int((now - started).total_seconds())
        results.append(out)
    return results


@router.get("/api/v2/tasks/{task_id}", response_model=TaskDetailOut)
async def get_task_v2(task_id: str, db: Session = Depends(get_db)):
    """Get task detail with agent conversation if assigned."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    conversation = []
    if task.agent_id:
        msgs = (
            db.query(Message)
            .filter(Message.agent_id == task.agent_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        conversation = [MessageOut.model_validate(m, from_attributes=True) for m in msgs]
    return TaskDetailOut(
        **TaskOut.model_validate(task).model_dump(),
        conversation=conversation,
    )



@router.post("/api/v2/tasks/batch-process")
async def batch_process_tasks(request: Request, db: Session = Depends(get_db)):
    """Spawn a tmux agent to triage inbox tasks: refine prompts, assign projects, dispatch."""
    try:
        body_raw = await request.json()
    except Exception:
        body_raw = {}
    task_ids = body_raw.get("task_ids")

    query = db.query(Task).filter(Task.status == TaskStatus.INBOX)
    if task_ids:
        query = query.filter(Task.id.in_(task_ids))
    inbox_tasks = query.order_by(Task.sort_order, Task.created_at.desc()).all()
    if not inbox_tasks:
        raise HTTPException(400, "No inbox tasks to process")

    # Gather available projects
    projects = db.query(Project).filter(Project.archived == False).all()
    project_list = [{"name": p.name, "display_name": getattr(p, "display_name", None) or p.name,
                     "description": getattr(p, "description", None) or ""} for p in projects]

    tasks_data = [{"id": t.id, "title": t.title or "", "description": t.description or "",
                   "project_name": t.project_name or None} for t in inbox_tasks]

    # Pick first available project as agent host (prefer cc-orchestrator)
    host_project = "cc-orchestrator"
    if not db.get(Project, host_project):
        host_project = projects[0].name if projects else None
    if not host_project:
        raise HTTPException(400, "No projects available to host the agent")

    api_base = "http://localhost:8080"
    prompt = f"""You are a task triage assistant for AgentHive. Analyze the inbox tasks below, then update each one via the local API.

FOR EACH TASK:
1. **Title**: Rewrite to be clear, specific, and actionable (<80 chars). Keep good titles unchanged.
2. **Description**: Focus on making the problem definition crystal clear — what is the current behavior, what is the desired outcome, and why it matters. Do NOT add implementation steps or technical solutions — the executing agent will explore the codebase and figure out the approach itself. Preserve existing good content. Keep the original language.
3. **Project**: If project_name is null, assign the best-matching project. If none fits, leave null.
4. **Dispatch**: If the task has enough detail AND a project, dispatch it for execution.

AVAILABLE PROJECTS:
{json.dumps(project_list, ensure_ascii=False, indent=2)}

INBOX TASKS:
{json.dumps(tasks_data, ensure_ascii=False, indent=2)}

HOW TO UPDATE:
- Update a task: curl -s -X PUT {api_base}/api/v2/tasks/TASK_ID -H "Content-Type: application/json" -d '{{"title":"...","description":"...","project_name":"..."}}'
- Dispatch for execution: curl -s -X POST {api_base}/api/v2/tasks/TASK_ID/dispatch

SAFETY RULES:
- You may ONLY call these API endpoints: PUT /api/v2/tasks/TASK_ID (update) and POST /api/v2/tasks/TASK_ID/dispatch (start execution)
- Do NOT call any other endpoints (no /api/agents/*, /api/git/*, /api/projects/*, DELETE endpoints, etc.)
- Do NOT write to memory files (.claude/memory/, MEMORY.md) or modify CLAUDE.md

INSTRUCTIONS:
1. First, analyze all tasks and present a summary table of your proposed changes (title, project assignment, ready status)
2. If anything is ambiguous — unclear intent, multiple possible projects, vague descriptions that could go different directions — ask the user to clarify before proceeding. Don't guess on important decisions.
3. Ask the user to confirm before applying changes
4. After confirmation, execute the curl commands to update each task
5. Report a final summary of what was changed and dispatched"""

    # Launch via the tmux agent endpoint
    from routers.agents import launch_tmux_agent

    # Build a mock request body for launch_tmux_agent
    class _MockRequest:
        """Thin wrapper to forward app state + override json body."""
        def __init__(self, real_request, body):
            self.app = real_request.app
            self._body = body
        async def json(self):
            return self._body

    mock_body = {
        "project": host_project,
        "prompt": prompt,
        "skip_permissions": True,
    }
    mock_req = _MockRequest(request, mock_body)
    agent_out = await launch_tmux_agent(mock_req, db)
    return {"ok": True, "agent_id": agent_out.id}


@router.put("/api/v2/tasks/reorder")
async def reorder_tasks_v2(body: dict, db: Session = Depends(get_db)):
    """Set sort_order for a list of task IDs. Body: { "task_ids": ["id1", "id2", ...] }"""
    task_ids = body.get("task_ids", [])
    if not task_ids:
        raise HTTPException(400, "task_ids required")
    for i, tid in enumerate(task_ids):
        task = db.get(Task, tid)
        if task:
            task.sort_order = i
    db.commit()
    return {"ok": True, "count": len(task_ids)}


@router.put("/api/v2/tasks/{task_id}", response_model=TaskOut)
async def update_task_v2(task_id: str, body: TaskUpdate, db: Session = Depends(get_db)):
    """Update task fields. Only allowed for INBOX/PLANNING tasks."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status not in (TaskStatus.INBOX, TaskStatus.PLANNING):
        raise HTTPException(400, f"Cannot edit task in {task.status.value} status")
    # Support status transitions (e.g. PLANNING → INBOX)
    if hasattr(body, "status") and body.status is not None:
        try:
            new_status = TaskStatus(body.status)
            TaskStateMachine.transition(task, new_status)
        except (ValueError, InvalidTransitionError) as exc:
            raise HTTPException(409, str(exc))
    for field in ("title", "description", "project_name", "priority", "model", "effort"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(task, field, val)
    # Boolean fields: explicit set check (None means "not sent")
    for field in ("skip_permissions", "use_worktree", "use_tmux"):
        if field in body.model_fields_set:
            setattr(task, field, getattr(body, field))
    if "worktree_name" in body.model_fields_set:
        task.worktree_name = body.worktree_name or None
    if "sort_order" in body.model_fields_set and body.sort_order is not None:
        task.sort_order = body.sort_order
    # Time fields: allow explicit null to clear
    if "notify_at" in body.model_fields_set:
        task.notify_at = body.notify_at
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)



@router.post("/api/v2/tasks/{task_id}/dispatch", response_model=TaskOut)
async def dispatch_task_v2(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Dispatch a task: create agent synchronously and move to EXECUTING.

    Creates a tmux agent and moves the task to EXECUTING.
    Returns the task with agent_id set so the frontend can navigate immediately.
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.project_name:
        raise HTTPException(400, "Task requires a project_name before dispatch")
    if not task.title:
        raise HTTPException(400, "Task requires a title before dispatch")
    # Validate transition
    if not can_transition(task.status, TaskStatus.EXECUTING):
        raise HTTPException(409, f"Invalid task transition: {task.status.value} -> executing (task {task.id})")

    proj = db.query(Project).filter(Project.name == task.project_name).first()
    if not proj:
        raise HTTPException(400, f"Project not found: {task.project_name}")

    # Enforce per-project capacity
    check_project_capacity(db, task.project_name)

    expected_status = task.status

    # Redo: prepare retry context before creating agent
    if expected_status in (TaskStatus.FAILED, TaskStatus.TIMEOUT):
        task.attempt_number += 1
        if task.agent_summary:
            task.retry_context = task.agent_summary
        task.agent_id = None
        task.agent_summary = None
        task.started_at = None
        task.completed_at = None

    ad = getattr(request.app.state, "agent_dispatcher", None)

    # All tasks dispatch via tmux
    agent_id = _dispatch_task_tmux(db, task, proj, ad)

    if not agent_id:
        raise HTTPException(500, "Failed to create agent for task")

    # Atomic CAS: only update if status hasn't changed since we read it
    from task_state import TaskStateMachine
    update_dict: dict = {
        "status": TaskStatus.EXECUTING,
        "agent_id": agent_id,
        "started_at": _utcnow(),
    }
    # Include retry fields in the CAS for FAILED/TIMEOUT
    if expected_status in (TaskStatus.FAILED, TaskStatus.TIMEOUT):
        update_dict["attempt_number"] = task.attempt_number
        update_dict["retry_context"] = task.retry_context
        update_dict["agent_summary"] = None
        update_dict["completed_at"] = None
    rows = (
        db.query(Task)
        .filter(Task.id == task_id, Task.status == expected_status)
        .update(update_dict, synchronize_session="fetch")
    )
    if rows == 0:
        raise HTTPException(409, "Task status changed concurrently")
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title, agent_id=agent_id,
    ))
    return TaskOut.model_validate(task)


@router.post("/api/v2/tasks/{task_id}/cancel", response_model=TaskOut)
async def cancel_task_v2(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Cancel a task. Stops agent if running."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not can_transition(task.status, TaskStatus.CANCELLED):
        raise HTTPException(409, f"Invalid task transition: {task.status.value} -> cancelled (task {task.id})")
    ad = getattr(request.app.state, "agent_dispatcher", None)
    _stop_task_agents(db, task, ad, "Agent stopped — task cancelled")
    TaskStateMachine.transition(task, TaskStatus.CANCELLED)
    # Clean up git artifacts
    proj = db.query(Project).filter(Project.name == task.project_name).first()
    if proj:
        from git_manager import GitManager
        gm = GitManager()
        if task.worktree_name:
            wt_path = os.path.join(proj.path, ".claude", "worktrees", task.worktree_name)
            gm.remove_worktree(proj.path, wt_path)
        if task.branch_name:
            gm.delete_branch(proj.path, task.branch_name, force=True)
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@router.post("/api/v2/tasks/{task_id}/complete", response_model=TaskOut)
async def complete_task_v2(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Manually complete a task. Stops agent and marks COMPLETE."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not can_transition(task.status, TaskStatus.COMPLETE):
        raise HTTPException(409, f"Cannot complete task in {task.status.value} state")
    ad = getattr(request.app.state, "agent_dispatcher", None)
    _stop_task_agents(db, task, ad, "Agent stopped — task completed")
    TaskStateMachine.transition(task, TaskStatus.COMPLETE)
    task.completed_at = _utcnow()
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@router.post("/api/v2/tasks/{task_id}/regenerate-summary", response_model=TaskOut)
async def regenerate_task_summary(task_id: str, db: Session = Depends(get_db)):
    """Manually re-trigger retry summary generation for a task."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.attempt_number < 2:
        raise HTTPException(409, "No previous attempt to summarize")

    # Find the most recent stopped agent for this task
    prev_agent = (
        db.query(Agent)
        .filter(Agent.task_id == task_id, Agent.status.in_([AgentStatus.STOPPED, AgentStatus.ERROR]))
        .order_by(Agent.created_at.desc())
        .first()
    )
    if not prev_agent:
        raise HTTPException(404, "No previous agent found for this task")

    project_path = resolve_project_path(task.project_name, db) if task.project_name else None
    if not project_path:
        raise HTTPException(404, "Project path not found")

    # Reset marker
    task.agent_summary = ":::generating:::"
    db.commit()
    db.refresh(task)

    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "", title=task.title,
    ))

    # Spawn background thread
    from routers.projects import _generate_retry_summary_background
    thread = threading.Thread(
        target=_generate_retry_summary_background,
        args=(prev_agent.id, task.id, task.title or "Unknown task",
              task.project_name or "", project_path,
              task.retry_context),
        daemon=True,
    )
    thread.start()
    logger.info("Manual retry summary spawned for task %s (agent %s)", task_id, prev_agent.id)

    return TaskOut.model_validate(task)


@router.post("/api/worktree-name")
async def generate_worktree_name(request: Request):
    """Generate a short branch name from a prompt using GPT-4o-mini."""
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return {"name": "task"}

    if not OPENAI_API_KEY:
        return {"name": _generate_worktree_name_local(prompt)}

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Generate a short git branch name (kebab-case, lowercase, "
                    "3-5 words, no special chars) summarizing the task. "
                    "Reply with ONLY the branch name, nothing else."
                )},
                {"role": "user", "content": prompt[:500]},
            ],
            max_tokens=30,
            temperature=0.3,
        )
        name = resp.choices[0].message.content.strip().lower()
        name = re.sub(r"[^a-z0-9-]", "-", name).strip("-")
        name = re.sub(r"-+", "-", name)
        return {"name": name or _generate_worktree_name_local(prompt)}
    except Exception as e:
        logger.warning("Worktree name generation failed: %s", e)
        return {"name": _generate_worktree_name_local(prompt)}
