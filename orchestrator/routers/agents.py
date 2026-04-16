"""Agent routes — create, launch, list, update, messages, interactive answers."""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import time as _time
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import CC_MODEL, CLAUDE_HOME, DISPLAY_DIR, VALID_MODELS
from database import SessionLocal, get_db
from models import (
    Agent, AgentInsightSuggestion, AgentMode, AgentStatus,
    Message, MessageRole, MessageStatus, Project, Task, TaskStatus,
)
from agent_dispatcher import ACTIVE_STATUSES, ALIVE_STATUSES, TERMINAL_STATUSES
from plat import platform as _platform
from schemas import (
    AgentBrief, AgentCreate, AgentInsightSuggestionOut, AgentOut,
    DisplayEntry, DisplayResponse,
    MessageOut, MessageSearchResponse, MessageSearchResult,
    PaginatedMessages, SendMessage, UpdateMessage,
)
from route_helpers import (
    check_project_capacity, compute_successor_id,
    create_tmux_claude_session, enrich_agent_briefs,
    generate_worktree_name_local, graceful_kill_tmux,
    subprocess_clean_env, tmux_launch_sem,
    TMUX_CMD_TIMEOUT, TUI_STARTUP_TIMEOUT, TUI_SETTLE_DELAY,
    MAX_STARTING_AGENTS, MAX_SEND_ATTEMPTS, JSONL_POLL_PER_ATTEMPT,
    IMPORT_CHECK_TIMEOUT, SUBPROCESS_STRIP_VARS,
)
from utils import utcnow as _utcnow, is_interrupt_message
from task_state_machine import can_transition, InvalidTransitionError
from task_state import TaskStateMachine
from websocket import emit_task_update, emit_agent_update

logger = logging.getLogger("orchestrator")

router = APIRouter(tags=["agents"])

# Aliases for route_helpers functions (original code used underscore-prefixed names)
_check_project_capacity = check_project_capacity
_create_tmux_claude_session = create_tmux_claude_session
_graceful_kill_tmux = graceful_kill_tmux
_tmux_launch_sem = tmux_launch_sem
_TMUX_CMD_TIMEOUT = TMUX_CMD_TIMEOUT
_TUI_STARTUP_TIMEOUT = TUI_STARTUP_TIMEOUT
_TUI_SETTLE_DELAY = TUI_SETTLE_DELAY
_MAX_STARTING_AGENTS = MAX_STARTING_AGENTS
_MAX_SEND_ATTEMPTS = MAX_SEND_ATTEMPTS
_JSONL_POLL_PER_ATTEMPT = JSONL_POLL_PER_ATTEMPT
_IMPORT_CHECK_TIMEOUT = IMPORT_CHECK_TIMEOUT
_generate_worktree_name_local = generate_worktree_name_local
_enrich_agent_briefs = enrich_agent_briefs


def _discover_session_id_from_pane(tmux_pane: str, project_path: str) -> str:
    """Discover the active session_id for a tmux pane.

    Strategy (in order):
    1. Check /tmp/ahive-pending-sessions/ for an entry matching this pane
    2. Scan open files for JSONL belonging to the pane's process tree
    3. Read Claude Code's session_id from its tasks dir (latest lock file)
    """
    if not tmux_pane:
        return ""

    # Strategy 1: pending session files written by the SessionStart hook
    pending_dir = "/tmp/ahive-pending-sessions"
    if os.path.isdir(pending_dir):
        for fname in os.listdir(pending_dir):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(pending_dir, fname)) as f:
                    info = json.load(f)
                if info.get("tmux_pane") == tmux_pane and info.get("session_id"):
                    return info["session_id"]
            except (OSError, json.JSONDecodeError):
                logger.debug("Skipped pending session file: %s", fname)
                continue

    # Strategy 2: scan process tree's open files
    from session_cache import session_source_dir
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-t", tmux_pane, "-p", "#{pane_pid}"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return ""
        pane_pid = int(r.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return ""

    # Collect descendant PIDs via platform layer
    desc_pids = {pane_pid}
    for child_pid, _comm in _platform.get_child_pids(pane_pid):
        desc_pids.add(child_pid)

    sdir = session_source_dir(project_path)
    if os.path.isdir(sdir):
        for pid in desc_pids:
            for fpath in _platform.get_open_files(pid):
                if fpath.startswith(sdir) and fpath.endswith(".jsonl"):
                    basename = os.path.basename(fpath)
                    return basename.rsplit(".jsonl", 1)[0]

    # Strategy 3: check Claude Code's tasks directory for the active session
    for pid in desc_pids:
        for fpath in _platform.get_open_files(pid):
            if "/tasks/" in fpath:
                parts = fpath.split("/tasks/")
                if len(parts) > 1:
                    sid_part = parts[1].split("/")[0]
                    if len(sid_part) >= 32 and "-" in sid_part:
                        return sid_part

    return ""


# ---------------------------------------------------------------------------
# Background summary helpers (deferred import from routers.projects)
# ---------------------------------------------------------------------------

def _run_agent_summary_background(*args, **kwargs):
    """Proxy — deferred import from routers.projects to avoid circular imports."""
    from routers.projects import _run_agent_summary_background as _impl
    return _impl(*args, **kwargs)


def _generate_retry_summary_background(*args, **kwargs):
    """Proxy — deferred import from routers.projects to avoid circular imports."""
    from routers.projects import _generate_retry_summary_background as _impl
    return _impl(*args, **kwargs)


# ---------------------------------------------------------------------------
# Hooks config helpers (called from lifespan too)
# ---------------------------------------------------------------------------

def _write_agent_hooks_config(project_path: str):
    """Write project-level hooks (PreToolUse safety + activity, PostToolUse, Stop)
    to settings.local.json.

    SessionStart is handled globally via _write_global_session_hook().
    """
    port = os.getenv("PORT", "8080")
    base_url = f"http://localhost:{port}/api/hooks"

    hook_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "hooks", "pretooluse-safety.py",
    )

    _tool_activity_hook = {
        "type": "http",
        "url": f"{base_url}/agent-tool-activity",
        "headers": {"X-Agent-Id": "$AHIVE_AGENT_ID"},
        "allowedEnvVars": ["AHIVE_AGENT_ID"],
    }

    # Permission gate hook — separate URL so Claude Code doesn't dedup
    # with the activity hook.  Large timeout (24h) so it can block
    # indefinitely until the user responds from the web UI.
    _permission_hook = {
        "type": "http",
        "url": f"{base_url}/agent-permission",
        "headers": {"X-Agent-Id": "$AHIVE_AGENT_ID"},
        "allowedEnvVars": ["AHIVE_AGENT_ID"],
        "timeout": 86400,
    }

    # PermissionRequest hook — auto-allow native CC permission prompts
    # (supervised agents already went through our PreToolUse gate)
    _permission_request_hook = {
        "type": "http",
        "url": f"{base_url}/agent-permission-request",
        "headers": {"X-Agent-Id": "$AHIVE_AGENT_ID"},
        "allowedEnvVars": ["AHIVE_AGENT_ID"],
        "timeout": 86400,
    }

    desired_hooks = {
        "PreToolUse": [
            # Safety guardrails (Bash/Write/Edit only)
            {
                "matcher": "Bash|Write|Edit",
                "hooks": [{
                    "type": "command",
                    "command": hook_script,
                }],
            },
            # Tool activity broadcast (all tools)
            {
                "hooks": [_tool_activity_hook],
            },
            # Permission gate for supervised agents (all tools)
            {
                "hooks": [_permission_hook],
            },
        ],
        "PostToolUse": [{
            "hooks": [_tool_activity_hook],
        }],
        "PostToolUseFailure": [{
            "hooks": [_tool_activity_hook],
        }],
        "SubagentStart": [{
            "hooks": [_tool_activity_hook],
        }],
        "SubagentStop": [{
            "hooks": [_tool_activity_hook],
        }],
        "Notification": [{
            "matcher": "permission_prompt",
            "hooks": [_tool_activity_hook],
        }],
        "PreCompact": [{
            "hooks": [_tool_activity_hook],
        }],
        "PostCompact": [{
            "hooks": [{
                "type": "http",
                "url": f"{base_url}/agent-post-compact",
                "headers": {"X-Agent-Id": "$AHIVE_AGENT_ID"},
                "allowedEnvVars": ["AHIVE_AGENT_ID"],
            }],
        }],
        "PermissionRequest": [{
            "hooks": [_permission_request_hook],
        }],
        "Stop": [{
            "hooks": [{
                "type": "http",
                "url": f"{base_url}/agent-stop",
                "headers": {"X-Agent-Id": "$AHIVE_AGENT_ID"},
                "allowedEnvVars": ["AHIVE_AGENT_ID"],
            }],
        }],
        "SessionEnd": [{
            "hooks": [{
                "type": "http",
                "url": f"{base_url}/agent-session-end",
                "headers": {"X-Agent-Id": "$AHIVE_AGENT_ID"},
                "allowedEnvVars": ["AHIVE_AGENT_ID"],
            }],
        }],
        "UserPromptSubmit": [{
            "hooks": [{
                "type": "http",
                "url": f"{base_url}/agent-user-prompt",
                "headers": {"X-Agent-Id": "$AHIVE_AGENT_ID"},
                "allowedEnvVars": ["AHIVE_AGENT_ID"],
            }],
        }],
    }

    settings_local_dir = os.path.join(project_path, ".claude")
    settings_local_path = os.path.join(settings_local_dir, "settings.local.json")
    try:
        os.makedirs(settings_local_dir, exist_ok=True)

        existing = {}
        if os.path.isfile(settings_local_path):
            with open(settings_local_path, "r") as f:
                existing = json.load(f)

        current_hooks = existing.get("hooks", {})
        # Remove stale SessionStart from project-level (now global)
        current_hooks.pop("SessionStart", None)
        merged_hooks = {**current_hooks, **desired_hooks}

        if existing.get("hooks") != merged_hooks:
            existing["hooks"] = merged_hooks
            with open(settings_local_path, "w") as f:
                json.dump(existing, f, indent=2)
            logger.info("Preflight: wrote agent hooks to %s", settings_local_path)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Preflight: failed to write agent hooks config: %s", e)


def _write_mcp_config(project_path: str):
    """Write .mcp.json to give agents access to the AgentHive MCP server.

    For the agenthive project itself, we skip — the committed .mcp.json
    with a relative path is preferred.  For other projects, we write an
    absolute path so agents can call list_sessions / read_session.
    """
    agenthive_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    # Skip if this IS the agenthive project (committed .mcp.json handles it)
    if os.path.realpath(project_path) == os.path.realpath(agenthive_root):
        return

    mcp_server_path = os.path.join(agenthive_root, "orchestrator", "mcp_server.py")
    if not os.path.isfile(mcp_server_path):
        return

    mcp_json_path = os.path.join(project_path, ".mcp.json")
    desired = {
        "mcpServers": {
            "agenthive": {
                "command": "python3",
                "args": [mcp_server_path],
            }
        }
    }

    try:
        existing = {}
        if os.path.isfile(mcp_json_path):
            with open(mcp_json_path, "r") as f:
                existing = json.load(f)

        # Merge — don't clobber other MCP servers the project may have
        servers = existing.get("mcpServers", {})
        if servers.get("agenthive") != desired["mcpServers"]["agenthive"]:
            servers["agenthive"] = desired["mcpServers"]["agenthive"]
            existing["mcpServers"] = servers
            with open(mcp_json_path, "w") as f:
                json.dump(existing, f, indent=2)
                f.write("\n")
            logger.info("Preflight: wrote MCP config to %s", mcp_json_path)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Preflight: failed to write MCP config: %s", e)


def _write_global_session_hook():
    """Write SessionStart hook to ~/.claude/settings.json (global).

    This ensures ALL claude processes on this machine fire the hook,
    regardless of which project they're in or whether AgentHive started
    them.  The hook script tries HTTP POST to the orchestrator and falls
    back to writing a local file when the orchestrator is offline.
    """
    hook_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "hooks", "session-start.sh",
    )

    desired_hook = [{
        "hooks": [{
            "type": "command",
            "command": hook_script,
        }],
    }]

    claude_home = os.path.expanduser("~/.claude")
    settings_path = os.path.join(claude_home, "settings.json")
    try:
        existing = {}
        if os.path.isfile(settings_path):
            with open(settings_path, "r") as f:
                existing = json.load(f)

        current_hooks = existing.get("hooks", {})
        if current_hooks.get("SessionStart") == desired_hook:
            return  # Already configured

        current_hooks["SessionStart"] = desired_hook
        existing["hooks"] = current_hooks
        with open(settings_path, "w") as f:
            json.dump(existing, f, indent=2)
        logger.info("Wrote global SessionStart hook to %s", settings_path)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to write global session hook: %s", e)


def _preflight_claude_project(project_path: str):
    """Ensure all Claude Code prerequisites are met before launching.

    Claude Code can show up to 8 blocking dialogs on startup.  This preflight
    pre-accepts all of them so the TUI starts straight into the REPL.

    Dialogs handled (in startup order):
    1. Onboarding wizard (theme, login, security notes)
    2. Custom API key approval
    3. Workspace trust ("do you trust this folder?")
    4. Hooks trust
    5. CLAUDE.md external includes warning
    6. Bypass-permissions mode warning
    7. MCP server approval
    8. Project onboarding

    Config files:
    - ~/.claude.json          — per-project trust + global onboarding state
    - ~/.claude/settings.json — global settings (permissions, cleanup, MCP)

    Trust cascades from parent directories: trusting PROJECTS_DIR root covers
    all projects under it.
    """
    from config import CLAUDE_HOME, PROJECTS_DIR

    # --- 1. ~/.claude.json (global state + per-project trust) ---
    claude_json_path = os.path.join(os.path.expanduser("~"), ".claude.json")
    for _ in range(3):
        try:
            data = {}
            if os.path.isfile(claude_json_path):
                with open(claude_json_path, "r") as f:
                    data = json.load(f)

            changed = False

            # Global onboarding (dialog 1)
            if data.get("hasCompletedOnboarding") is not True:
                data["hasCompletedOnboarding"] = True
                changed = True

            projects = data.setdefault("projects", {})

            # Trust the PROJECTS_DIR root — cascades to all child projects
            # so we don't need per-project entries for trust alone.
            projects_dir = PROJECTS_DIR or ""
            if projects_dir:
                root_cfg = projects.setdefault(projects_dir, {})
                if root_cfg.get("hasTrustDialogAccepted") is not True:
                    root_cfg["hasTrustDialogAccepted"] = True
                    root_cfg["hasTrustDialogHooksAccepted"] = True
                    changed = True

            # Per-project flags (dialogs 3-5, 8)
            proj_cfg = projects.setdefault(project_path, {})
            _trust_fields = {
                "hasTrustDialogAccepted": True,
                "hasTrustDialogHooksAccepted": True,
                "hasCompletedProjectOnboarding": True,
                "hasClaudeMdExternalIncludesApproved": True,
                "hasClaudeMdExternalIncludesWarningShown": True,
            }
            for field, value in _trust_fields.items():
                if proj_cfg.get(field) is not value:
                    proj_cfg[field] = value
                    changed = True
            if not proj_cfg.get("projectOnboardingSeenCount"):
                proj_cfg["projectOnboardingSeenCount"] = 1
                changed = True

            if changed:
                with open(claude_json_path, "w") as f:
                    json.dump(data, f, indent=2)
                logger.info("Preflight: updated ~/.claude.json for %s", project_path)
            break
        except (json.JSONDecodeError, OSError) as e:
            # Retry after brief delay — concurrent Claude agents may be writing
            # to the same ~/.claude.json file, causing transient read/write races
            logger.warning("Preflight: failed to update ~/.claude.json: %s", e)
            import time
            time.sleep(0.1)

    # --- 2. ~/.claude/settings.json (global settings) ---
    settings_path = os.path.join(CLAUDE_HOME, "settings.json")
    try:
        settings = {}
        if os.path.isfile(settings_path):
            with open(settings_path, "r") as f:
                settings = json.load(f)

        changed = False
        _global_flags = {
            "skipDangerousModePermissionPrompt": True,   # dialog 6
            "cleanupPeriodDays": 36500,                  # prevent session cleanup
            "enableAllProjectMcpServers": True,          # dialog 7
        }
        for flag, value in _global_flags.items():
            if settings.get(flag) != value:
                settings[flag] = value
                changed = True

        if changed:
            with open(settings_path, "w") as f:
                json.dump(settings, f, indent=2)
            logger.info("Preflight: updated ~/.claude/settings.json")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Preflight: failed to update settings.json: %s", e)

    # --- 3. .claude/settings.local.json (project-level agent hooks) ---
    _write_agent_hooks_config(project_path)

    # --- 4. .mcp.json (MCP server for cross-session reference) ---
    _write_mcp_config(project_path)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/agents", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, request: Request, db: Session = Depends(get_db)):
    """Create a new agent with an initial message."""
    project = db.get(Project, body.project)
    if not project:
        raise HTTPException(status_code=400, detail=f"Project '{body.project}' not found")
    if project.archived:
        raise HTTPException(status_code=400, detail="Cannot create agents for archived projects — activate first")

    # Enforce per-project capacity
    _check_project_capacity(db, body.project)

    # Generate agent name from first ~50 chars of prompt
    name = body.prompt[:50].strip()
    if len(body.prompt) > 50:
        name += "..."

    # Resolve model: explicit > project default > global default
    agent_model = body.model or project.default_model or CC_MODEL
    if agent_model not in VALID_MODELS:
        logger.warning("Invalid model %r for agent, falling back to %s", agent_model, CC_MODEL)
        agent_model = CC_MODEL

    # Determine initial status: IDLE if importing CLI session
    is_sync = body.sync_session and body.resume_session_id
    initial_status = AgentStatus.IDLE if is_sync else AgentStatus.STARTING

    # Pre-generate agent ID so we can use it for worktree naming
    import uuid
    agent_id = uuid.uuid4().hex[:12]

    # Resolve worktree name: "auto" → GPT-generated branch name
    wt = body.worktree
    if wt == "auto":
        wt = _generate_worktree_name_local(body.prompt)

    # Infer worktree from session JSONL location when resuming/syncing
    # without an explicit worktree (e.g. Sessions tab resume)
    if not wt and body.resume_session_id:
        from agent_dispatcher import _infer_worktree_from_session
        _inferred = _infer_worktree_from_session(body.resume_session_id, project.path)
        if _inferred:
            wt = _inferred
            logger.info("Inferred worktree=%s from session JSONL path", wt)

    agent = Agent(
        id=agent_id,
        project=body.project,
        name=name,
        mode=body.mode,
        status=initial_status,
        model=agent_model,
        effort=body.effort,
        worktree=wt,
        timeout_seconds=body.timeout_seconds,
        session_id=body.resume_session_id,
        cli_sync=True,  # All agents are tmux-managed
        skip_permissions=body.skip_permissions,
        last_message_preview=name,
        last_message_at=_utcnow(),
    )
    db.add(agent)
    db.flush()  # Get agent.id

    if is_sync:
        # Sync mode: import existing history, don't create initial user message
        db.commit()
        db.refresh(agent)

        # Import history and start live sync in background
        ad = getattr(request.app.state, "agent_dispatcher", None)
        if ad:
            imported = ad.import_session_history(
                agent.id, body.resume_session_id, project.path
            )
            logger.info(
                "Agent %s: imported %d messages from CLI session %s",
                agent.id, imported, body.resume_session_id,
            )
            # Start live sync to tail ongoing CLI activity
            ad.start_session_sync(
                agent.id, body.resume_session_id, project.path
            )
    else:
        # Non-sync mode: launch a tmux session and send the prompt.
        # All agents must be tmux-managed — no subprocess dispatch.
        import shlex
        import secrets
        import subprocess as _sp
        import uuid as _uuid

        from config import CLAUDE_BIN

        # Get existing tmux session names for collision check
        try:
            _tmux_ls = _sp.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True, text=True, timeout=5,
            )
            _existing_tmux = set(_tmux_ls.stdout.strip().splitlines()) if _tmux_ls.returncode == 0 else set()
        except (OSError, _sp.TimeoutExpired):
            _existing_tmux = set()

        tmux_session = f"ah-{agent_id[:8]}"
        if tmux_session in _existing_tmux:
            # Collision — regenerate (extremely unlikely since agent_id is unique)
            tmux_session = f"ah-{secrets.token_hex(4)}"

        # Pre-generate session UUID and write .owner sidecar
        pre_session_id = str(_uuid.uuid4())
        from agent_dispatcher import _write_session_owner
        from session_cache import session_source_dir
        _sdir = session_source_dir(project.path)
        os.makedirs(_sdir, exist_ok=True)
        _write_session_owner(_sdir, pre_session_id, agent_id)

        # Build claude command (interactive mode — no -p)
        cmd_parts = [CLAUDE_BIN, "--session-id", pre_session_id,
                     "--output-format", "stream-json", "--verbose"]
        if body.skip_permissions:
            cmd_parts.append("--dangerously-skip-permissions")
        if agent_model:
            cmd_parts += ["--model", agent_model]
        if body.effort:
            cmd_parts += ["--effort", body.effort]
        if wt:
            cmd_parts += ["--worktree", wt]
        claude_cmd = " ".join(shlex.quote(p) for p in cmd_parts)

        _preflight_claude_project(project.path)
        pane_id = _create_tmux_claude_session(tmux_session, project.path, claude_cmd, agent_id=agent_id)
        agent.tmux_pane = pane_id

        # Create the initial user message
        ad = getattr(request.app.state, "agent_dispatcher", None)
        launch_prompt = None
        if ad:
            msg, launch_prompt, _ = ad._prepare_dispatch(
                db, agent, project, body.prompt,
                source="web",
                wrap_prompt=True,
            )
        else:
            msg = Message(
                agent_id=agent.id,
                role=MessageRole.USER,
                content=body.prompt,
                source="web",
            )
            db.add(msg)
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = _utcnow()

        db.commit()
        db.refresh(agent)

        # Schedule background task: wait for Claude TUI to load, send prompt,
        # detect session JSONL, and start sync.
        if ad and launch_prompt:
            launch_task = asyncio.ensure_future(
                _launch_tmux_background(
                    ad, agent.id, pane_id, launch_prompt, project.path,
                    pre_session_id=pre_session_id,
                )
            )
            ad.track_launch_task(agent.id, launch_task)

    logger.info("Agent %s created for project %s (mode %s, sync=%s, tmux=%s)",
                agent.id, agent.project, agent.mode.value, is_sync, bool(agent.tmux_pane))
    return agent


@router.post("/api/agents/launch-tmux", status_code=201)
async def launch_tmux_agent(request: Request, db: Session = Depends(get_db)):
    """Launch an interactive claude CLI session in a new tmux pane.

    Starts Claude in interactive mode (full TUI), then sends the prompt
    as input after Claude finishes loading.  The user can attach to the
    tmux pane to interact with Claude directly.

    A background task detects the session JSONL and starts live-syncing
    the conversation into the webapp.
    """
    import shlex
    import subprocess
    from config import CLAUDE_BIN

    body = await request.json()
    project_name = body.get("project")
    prompt = body.get("prompt", "").strip()
    model = body.get("model")
    effort = body.get("effort")
    worktree = body.get("worktree")
    skip_permissions = body.get("skip_permissions", True)
    task_id = body.get("task_id")

    # Reject if too many agents are already queued for launch
    starting_count = db.query(func.count(Agent.id)).filter(
        Agent.status == AgentStatus.STARTING,
    ).scalar() or 0
    if starting_count >= _MAX_STARTING_AGENTS:
        raise HTTPException(
            status_code=429,
            detail="Too many agents launching — please wait for current launches to finish",
        )

    if not project_name:
        raise HTTPException(status_code=400, detail="Project is required")

    proj = db.get(Project, project_name)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
    if not os.path.isdir(proj.path):
        raise HTTPException(status_code=400, detail="Project directory not found on disk")

    # Enforce per-project capacity
    _check_project_capacity(db, project_name)

    # Each agent gets its own tmux session: "ah-{agent_id_prefix}"
    # Pre-generate agent ID, ensuring no DB or tmux session name collision
    import secrets
    import subprocess as _sp

    # Get existing tmux session names for collision check
    try:
        _tmux_ls = _sp.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        _existing_tmux = set(_tmux_ls.stdout.strip().splitlines()) if _tmux_ls.returncode == 0 else set()
    except (OSError, _sp.TimeoutExpired):
        _existing_tmux = set()

    for _ in range(20):
        agent_hex = secrets.token_hex(6)
        tmux_session = f"ah-{agent_hex[:8]}"
        if db.get(Agent, agent_hex) is None and tmux_session not in _existing_tmux:
            break
    else:
        raise HTTPException(status_code=500, detail="Failed to generate unique agent ID")

    # Resolve worktree name: "auto" → GPT-generated branch name
    if worktree == "auto" and prompt:
        worktree = _generate_worktree_name_local(prompt)

    # Pre-generate session UUID so we can pre-write the .owner sidecar
    # BEFORE launching Claude.  This ensures the session has identity
    # from the very first moment the JSONL file appears.
    import uuid as _uuid
    pre_session_id = str(_uuid.uuid4())

    # Build the claude command in INTERACTIVE mode (no -p, so the user
    # gets the full TUI and can attach via tmux).
    cmd_parts = [CLAUDE_BIN,
                  "--session-id", pre_session_id,
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

    # Pre-write .owner sidecar before launching Claude.
    # Slug is unknown at this point — will be backfilled by the sync loop.
    from agent_dispatcher import _write_session_owner
    from session_cache import session_source_dir
    _sdir = session_source_dir(proj.path)
    os.makedirs(_sdir, exist_ok=True)
    _write_session_owner(_sdir, pre_session_id, agent_hex)

    # Pre-accept the project trust dialog in ~/.claude.json so Claude
    # doesn't show the "Is this a project you trust?" prompt that blocks
    # the TUI from starting.  This dialog appears on first launch in any
    # directory that hasn't been explicitly trusted yet.
    _preflight_claude_project(proj.path)

    pane_id = _create_tmux_claude_session(tmux_session, proj.path, claude_cmd, agent_id=agent_hex)

    # Create Agent record immediately so the frontend can navigate to it.
    agent_name = (prompt or "CLI session")[:80]
    resolved_model = model or proj.default_model
    if resolved_model not in VALID_MODELS:
        logger.warning("Invalid model %r for tmux agent, falling back to %s", resolved_model, CC_MODEL)
        resolved_model = CC_MODEL
    agent = Agent(
        id=agent_hex,
        project=project_name,
        name=agent_name,
        mode=AgentMode.AUTO,
        status=AgentStatus.STARTING,
        model=resolved_model,
        cli_sync=True,
        tmux_pane=pane_id,
        effort=effort if effort else None,
        worktree=worktree if worktree else None,
        skip_permissions=skip_permissions,
        task_id=task_id if task_id else None,
        last_message_preview=agent_name,
        last_message_at=datetime.now(timezone.utc),
    )
    db.add(agent)
    db.flush()

    # Link task → agent if task_id provided
    _task_linked = False
    if task_id:
        _task = db.get(Task, task_id)
        if _task and can_transition(_task.status, TaskStatus.EXECUTING):
            _task.agent_id = agent.id
            TaskStateMachine.transition(_task, TaskStatus.EXECUTING)
            _task.worktree_name = worktree if worktree else None
            if worktree:
                _task.branch_name = _task.branch_name or f"worktree-{worktree}"
            _task_linked = True

    # Save the initial prompt and prepare wrapped version for Claude
    launch_prompt = None
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if prompt:
        if ad:
            msg, launch_prompt, _ = ad._prepare_dispatch(
                db, agent, proj, prompt,
                source="web",
                wrap_prompt=True,
            )
        else:
            msg = Message(
                agent_id=agent.id,
                role=MessageRole.USER,
                content=prompt,
                source="web",
            )
            db.add(msg)
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(agent)

    # Emit task update after commit if task was linked
    if _task_linked:
        asyncio.ensure_future(emit_task_update(
            _task.id, _task.status.value, _task.project_name or "",
            title=_task.title,
        ))

    # Schedule background task: wait for Claude TUI to load, send prompt,
    # detect session JSONL, and start sync.
    if ad and launch_prompt:
        launch_task = asyncio.ensure_future(
            _launch_tmux_background(
                ad, agent.id, pane_id, launch_prompt, proj.path,
                pre_session_id=pre_session_id,
            )
        )
        ad.track_launch_task(agent.id, launch_task)

    logger.info(
        "Launched tmux claude session in pane %s for project %s (agent %s)",
        pane_id, project_name, agent.id,
    )
    return AgentOut.model_validate(agent)


async def _launch_tmux_background(
    ad, agent_id: str, pane_id: str, prompt: str, project_path: str,
    pre_session_id: str | None = None,
):
    """Background task for tmux agent launch.

    1. Wait for Claude's TUI to start (polls for a claude process in the pane)
    2. Send the user prompt
    3. Detect the session JSONL and start the sync loop

    On any failure, transitions the agent to ERROR so it doesn't stay
    stuck in STARTING forever.  Handles cancellation gracefully so that
    stopping the agent while the launch is in progress doesn't leave
    zombie error transitions.
    """
    import subprocess

    from agent_dispatcher import (
        _build_tmux_claude_map,
        _detect_pid_session_jsonl,
        capture_tmux_pane,
        send_tmux_message,
    )
    from database import SessionLocal
    from session_cache import session_source_dir
    from websocket import emit_agent_update, emit_new_message

    def _mark_error(reason: str):
        """Transition agent to ERROR status on launch failure."""
        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            if agent:
                ad.error_agent_cleanup(
                    db, agent, reason,
                    add_message=False, fail_executing=False,
                    cancel_tasks=False,
                )
                db.commit()
        finally:
            db.close()
        logger.warning("tmux launch failed for agent %s: %s", agent_id, reason)

    await _tmux_launch_sem.acquire()
    # Register this pane so _detect_successor_session skips sessions
    # belonging to this launching agent (prevents cross-agent theft).
    ad._launching_panes[agent_id] = pane_id
    try:
        # Step 1: Wait for Claude's TUI to fully load (up to 30s).
        # Two phases:
        #   a) Detect the claude process in the pane
        #   b) Wait for the TUI input prompt (❯) to appear in the pane content
        process_detected = False
        for _ in range(_TUI_STARTUP_TIMEOUT):
            await asyncio.sleep(1)
            pane_map = _build_tmux_claude_map()
            if pane_id in pane_map and not pane_map[pane_id]["is_orchestrator"]:
                process_detected = True
                break
        if not process_detected:
            _mark_error(
                "Claude TUI did not start in pane %s within %ds "
                "(project_path: %s)" % (pane_id, _TUI_STARTUP_TIMEOUT, project_path)
            )
            return

        # Wait for the REPL to be fully mounted.
        # IMPORTANT: The ❯ prompt character appears in the welcome box BEFORE
        # the REPL input handler is mounted.  On first launch in a new project
        # directory, showSetupScreens() takes ~4 seconds (vs ~200ms for
        # established projects).  We use the status bar ("⏵⏵ bypass permissions"
        # or "shift+tab to cycle") as the definitive REPL-mounted signal,
        # since it only renders after the full TUI component tree is ready.
        #
        # Also handles the project trust dialog ("Is this a project you
        # trust?") which can appear despite pre-acceptance if ~/.claude.json
        # was regenerated.  If detected, we press Enter to accept it.
        tui_ready = False
        trust_dialog_handled = False
        for _ in range(_TUI_STARTUP_TIMEOUT):
            await asyncio.sleep(1)
            pane_text = capture_tmux_pane(pane_id)
            if pane_text is None:
                continue

            # Check for the REPL status bar (definitive ready signal).
            # With --dangerously-skip-permissions: "⏵⏵ bypass ... shift+tab"
            # Without (supervised mode): "? for shortcuts ... /effort"
            for ln in pane_text.split("\n"):
                if ("\u23f5" in ln and "shift+tab" in ln) or \
                   ("? for shortcuts" in ln):
                    tui_ready = True
                    break
            if tui_ready:
                break

            # Check for the project trust dialog and auto-accept it
            if not trust_dialog_handled and "trust this folder" in pane_text.lower():
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane_id, "Enter"],
                    capture_output=True, text=True, timeout=_TMUX_CMD_TIMEOUT,
                )
                trust_dialog_handled = True
                logger.info(
                    "Auto-accepted project trust dialog in pane %s for agent %s",
                    pane_id, agent_id,
                )
        if not tui_ready:
            _mark_error(
                "Claude TUI did not fully initialize in pane %s within %ds "
                "(project_path: %s)" % (pane_id, _TUI_STARTUP_TIMEOUT, project_path)
            )
            return

        # Extra settle time after REPL mount.  On first-launch projects
        # showSetupScreens() finishes ~200ms before REPL mount; add a buffer
        # to ensure the input handler is fully wired up.
        await asyncio.sleep(_TUI_SETTLE_DELAY)

        # Step 2: Send the prompt, then wait for session JSONL as the
        # definitive acceptance signal.  If the JSONL doesn't appear within
        # a reasonable time, clear the input and re-send.
        #
        # Using session JSONL creation as the acceptance signal is far more
        # reliable than pane-capture heuristics, which are fragile against
        # TUI layout variations and re-render timing.
        from session_cache import invalidate_path_cache
        from agent_dispatcher import _get_session_pid

        actual_cwd = project_path
        try:
            cwd_result = subprocess.run(
                ["tmux", "display-message", "-t", pane_id, "-p", "#{pane_current_path}"],
                capture_output=True, text=True, timeout=5,
            )
            if cwd_result.returncode == 0 and cwd_result.stdout.strip():
                actual_cwd = os.path.realpath(cwd_result.stdout.strip())
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.debug("tmux pane CWD lookup failed for %s: %s", pane_id, e)

        session_dir = session_source_dir(actual_cwd)
        base_session_dir = session_source_dir(project_path)

        def _check_status_bar_processing() -> bool:
            """Check if the status bar shows 'esc to interrupt' — definitive
            indicator that Claude is actively processing."""
            pane_text = capture_tmux_pane(pane_id)
            if pane_text:
                for ln in pane_text.split("\n"):
                    if "\u23f5" in ln and "esc to interrupt" in ln:
                        return True
            return False

        def _scan_for_session_jsonl(owned_sids: set, pane_pid: int | None) -> str | None:
            """Find the JSONL created by our launch.

            If pre_session_id was provided (pre-generated UUID passed to
            Claude via --session-id), ONLY accept that exact session.
            Falls back to FD/mtime scan only when no pre_session_id was set
            (legacy launches without --session-id).
            """
            # When we pre-generated a session ID, only accept that one.
            # Never fall back to mtime guessing — it causes session theft
            # when the expected JSONL hasn't been written yet.
            if pre_session_id:
                for sdir in dict.fromkeys([session_dir, base_session_dir]):
                    if not os.path.isdir(sdir):
                        continue
                    fpath = os.path.join(sdir, f"{pre_session_id}.jsonl")
                    if os.path.exists(fpath):
                        return pre_session_id
                return None  # Not ready yet — caller will retry

            # Legacy fallback (no pre_session_id): scan for newest unowned JSONL
            if pane_pid:
                sid = _detect_pid_session_jsonl(pane_pid)
                if sid and sid not in owned_sids:
                    return sid

            best_sid, best_mtime = None, launch_start
            for sdir in dict.fromkeys([session_dir, base_session_dir]):
                if not os.path.isdir(sdir):
                    continue
                for fname in os.listdir(sdir):
                    if not fname.endswith(".jsonl"):
                        continue
                    sid = fname.replace(".jsonl", "")
                    if sid in owned_sids:
                        continue
                    fpath = os.path.join(sdir, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except OSError:
                        continue
                    if mtime > best_mtime:
                        best_sid, best_mtime = sid, mtime

            return best_sid

        # Collect session IDs already owned by other agents (once, reused)
        db_check = SessionLocal()
        try:
            owned_sids = set()
            for a in db_check.query(Agent).filter(
                Agent.session_id.is_not(None),
                Agent.id != agent_id,
            ).all():
                owned_sids.add(a.session_id)
        finally:
            db_check.close()

        pane_pid = None
        pane_map = _build_tmux_claude_map()
        if pane_id in pane_map:
            pane_pid = pane_map[pane_id].get("pid")

        import time as _time
        launch_start = _time.time()
        session_id = None

        for attempt in range(_MAX_SEND_ATTEMPTS):
            # Clear any leftover text from a prior failed attempt
            if attempt > 0:
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane_id, "C-u"],
                    capture_output=True, text=True, timeout=5,
                )
                # Increasing back-off between retries: 3s, 5s, 7s, 9s
                await asyncio.sleep(1 + attempt * 2)

            if not send_tmux_message(pane_id, prompt):
                _mark_error(
                    "Failed to send prompt to tmux pane %s "
                    "(project_path: %s)" % (pane_id, project_path)
                )
                return

            logger.info(
                "tmux launch agent %s: prompt sent (attempt %d/%d)",
                agent_id, attempt + 1, _MAX_SEND_ATTEMPTS,
            )

            # Poll for evidence that Claude accepted the prompt:
            # 1. Status bar shows "esc to interrupt" (processing), or
            # 2. Session JSONL file appears (definitive)
            for i in range(_JSONL_POLL_PER_ATTEMPT):
                await asyncio.sleep(1)

                # Refresh PID if not yet known
                if not pane_pid:
                    pane_map = _build_tmux_claude_map()
                    if pane_id in pane_map:
                        pane_pid = pane_map[pane_id].get("pid")

                # Quick check: is Claude processing?
                if i < 5 and _check_status_bar_processing():
                    logger.info(
                        "tmux launch agent %s: status bar confirms processing",
                        agent_id,
                    )

                # Invalidate path cache periodically to pick up new dirs
                if i in (5, 10):
                    invalidate_path_cache(actual_cwd)
                    invalidate_path_cache(project_path)
                    session_dir = session_source_dir(actual_cwd)
                    base_session_dir = session_source_dir(project_path)

                try:
                    session_id = _scan_for_session_jsonl(owned_sids, pane_pid)
                except OSError:
                    continue
                if session_id:
                    break

            if session_id:
                break

            # No JSONL after polling — check if the pane still has Claude
            pane_map = _build_tmux_claude_map()
            if pane_id not in pane_map:
                _mark_error(
                    "Claude process disappeared from pane %s during launch "
                    "(project_path: %s)" % (pane_id, project_path)
                )
                return

            logger.info(
                "tmux launch agent %s: no session JSONL after attempt %d/%d, "
                "will retry",
                agent_id, attempt + 1, _MAX_SEND_ATTEMPTS,
            )

        if not session_id:
            _mark_error(
                "No session JSONL appeared for agent %s after %d send attempts "
                "(session_dir: %s, project_path: %s)"
                % (agent_id, _MAX_SEND_ATTEMPTS, session_dir, project_path)
            )
            return

        # Update agent with session_id and transition to IDLE
        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            if not agent or agent.status == AgentStatus.STOPPED:
                return
            # Final guard: verify no other agent grabbed this session
            # in the meantime (race protection)
            existing = db.query(Agent).filter(
                Agent.session_id == session_id,
                Agent.id != agent_id,
            ).first()
            if existing:
                logger.warning(
                    "Session %s already owned by agent %s — "
                    "cannot assign to agent %s",
                    session_id[:12], existing.id, agent_id,
                )
                _mark_error(
                    "Session %s already owned by another agent" % session_id[:12]
                )
                return
            agent.session_id = session_id
            # Only transition STARTING → IDLE; if UserPromptSubmit already
            # set EXECUTING via _start_generating, don't overwrite it.
            if agent.status != AgentStatus.EXECUTING:
                agent.status = AgentStatus.IDLE
            _init_msg = (
                db.query(Message)
                .filter(
                    Message.agent_id == agent_id,
                    Message.role == MessageRole.USER,
                    Message.status == MessageStatus.PENDING,
                    Message.delivered_at.is_(None),
                )
                .order_by(Message.created_at.asc())
                .first()
            )
            if _init_msg:
                _init_msg.delivered_at = _utcnow()
                _init_msg.status = MessageStatus.COMPLETED
                _init_msg.completed_at = _utcnow()
            try:
                db.commit()
            except IntegrityError:
                # UNIQUE constraint on session_id — another agent raced us
                db.rollback()
                _mark_error(
                    "Session %s UNIQUE constraint violation" % session_id[:12]
                )
                return

            # Update display file with delivery status
            if _init_msg:
                from display_writer import update_last
                update_last(agent_id, _init_msg.id)

            ad._emit(emit_agent_update(agent_id, "IDLE", agent.project))
        finally:
            db.close()

        # Start the session sync loop — use actual_cwd so worktree agents
        # watch the correct session directory
        ad.start_session_sync(agent_id, session_id, actual_cwd)
        logger.info(
            "Started sync for launched tmux agent %s (session %s)",
            agent_id, session_id[:12],
        )
    except asyncio.CancelledError:
        logger.info("Launch task cancelled for agent %s", agent_id)
    finally:
        _tmux_launch_sem.release()
        ad._launch_tasks.pop(agent_id, None)
        ad._launching_panes.pop(agent_id, None)


@router.post("/api/agents/scan")
async def scan_agents(request: Request, db: Session = Depends(get_db)):
    """Trigger an immediate liveness scan of all agents.

    Runs the same reaping logic as the periodic dispatcher tick, so dead
    CLI agents are marked STOPPED right away instead of waiting ~30s.
    """
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        ad._reap_dead_agents(db)
        db.commit()
    return {"ok": True}


@router.post("/api/agents/wake-sync-all")
async def wake_all_agent_syncs(request: Request, db: Session = Depends(get_db)):
    """Wake sync loops for all active (non-STOPPED) agents.

    Mirrors the per-agent ``/api/agents/{id}/wake-sync`` endpoint:
    recovers ERROR→IDLE, restarts dead sync loops, redispatches stuck
    queued messages, and dispatches pending messages.
    """
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        raise HTTPException(status_code=503, detail="Dispatcher not ready")
    active = db.query(Agent).filter(Agent.status != AgentStatus.STOPPED).all()

    # Phase 1: recover ERROR agents (matches individual wake-sync)
    recovered_ids: list[str] = []
    for agent in active:
        if agent.status == AgentStatus.ERROR:
            agent.status = AgentStatus.IDLE
            agent.error_message = None
            recovered_ids.append(agent.id)
    if recovered_ids:
        db.commit()
        from websocket import emit_agent_update
        for aid in recovered_ids:
            ag = db.get(Agent, aid)
            if ag:
                ad._emit(emit_agent_update(ag.id, "IDLE", ag.project))
        logger.info("wake-sync-all: recovered %d ERROR agents", len(recovered_ids))

    # Phase 2: wake / restart sync loops + redispatch
    woken = 0
    for agent in active:
        asyncio.ensure_future(ad.redispatch_stuck_queued(agent.id))
        asyncio.ensure_future(ad.dispatch_pending_message(agent.id, delay=0))
        if ad.wake_sync(agent.id):
            woken += 1
    return {"ok": True, "woken": woken, "recovered": len(recovered_ids), "total": len(active)}


# ---- Unlinked (detected) sessions ----

_UNLINKED_DIR: str | None = None


def _get_unlinked_dir() -> str:
    """Return (and lazily create) the unlinked-sessions directory."""
    global _UNLINKED_DIR
    if _UNLINKED_DIR is None:
        from config import BACKUP_DIR
        _UNLINKED_DIR = os.path.join(BACKUP_DIR, "unlinked-sessions")
    os.makedirs(_UNLINKED_DIR, exist_ok=True)
    return _UNLINKED_DIR


def _clean_stale_unlinked(max_age: int = 3600):
    """Remove unlinked session entries whose JSONL hasn't been updated in max_age seconds.

    Preserves entries whose tmux pane still has a running process.
    """
    udir = _get_unlinked_dir()
    now = _time.time()
    removed = 0
    try:
        for fname in os.listdir(udir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(udir, fname)
            try:
                with open(fpath) as f:
                    info = json.load(f)
                transcript = info.get("transcript_path", "")
                if transcript and os.path.isfile(transcript):
                    mtime = os.path.getmtime(transcript)
                    if now - mtime < max_age:
                        continue  # still active
                # Transcript stale or gone — check if tmux pane is alive
                tmux_pane = info.get("tmux_pane", "")
                if tmux_pane:
                    try:
                        r = subprocess.run(
                            ["tmux", "display-message", "-t", tmux_pane, "-p", "#{pane_pid}"],
                            capture_output=True, text=True, timeout=3,
                        )
                        current_pid = r.stdout.strip() if r.returncode == 0 else ""
                        if current_pid:
                            continue  # pane still alive — keep entry
                    except (subprocess.TimeoutExpired, OSError):
                        pass
                # Transcript gone/stale and no live pane → remove
                os.unlink(fpath)
                removed += 1
            except (OSError, json.JSONDecodeError):
                try:
                    os.unlink(fpath)
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    if removed:
        logger.info("Cleaned %d stale unlinked session entries", removed)
    return removed


@router.get("/api/unlinked-sessions")
async def list_unlinked_sessions(db: Session = Depends(get_db)):
    """List manually-launched Claude Code sessions not bound to any agent."""
    _clean_stale_unlinked()
    udir = _get_unlinked_dir()

    # Session IDs owned by ACTIVE agents — filter them out
    bound_sids: set[str] = {
        r[0] for r in db.query(Agent.session_id).filter(
            Agent.session_id.is_not(None),
            Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]),
        ).all()
    }

    sessions = []
    try:
        for fname in sorted(os.listdir(udir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(udir, fname)
            try:
                with open(fpath) as f:
                    info = json.load(f)
                sid = info.get("session_id", "")
                if sid in bound_sids:
                    # Already adopted — clean up stale signal file
                    try:
                        os.unlink(fpath)
                    except OSError:
                        pass
                    continue
                if not info.get("project_name"):
                    cwd = info.get("cwd", "")
                    info["project_name"] = os.path.basename(cwd.rstrip("/")) if cwd else ""
                info["file"] = fname
                sessions.append(info)
            except (OSError, json.JSONDecodeError):
                logger.debug("Skipped unlinked session file: %s", fname)
                continue
    except OSError:
        pass
    return sessions


@router.post("/api/unlinked-sessions/{file_key}/adopt")
async def adopt_unlinked_session(
    file_key: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Bind an unlinked session to a new agent and start syncing.

    file_key is the stem of the JSON entry (session_id or pane-X).
    Body: {"project": "project-name"}
    Optional: {"agent_id": "existing-agent-id"} to bind to existing agent.
    """
    import secrets
    from session_cache import session_source_dir

    udir = _get_unlinked_dir()
    info_path = os.path.join(udir, f"{file_key}.json")
    if not os.path.isfile(info_path):
        raise HTTPException(status_code=404, detail="Unlinked session not found")

    try:
        with open(info_path) as f:
            info = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to read session info: {e}")

    body = await request.json()
    project_name = body.get("project") or os.path.basename(info.get("cwd", "").rstrip("/"))
    existing_agent_id = body.get("agent_id")

    proj = db.get(Project, project_name)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")

    actual_cwd = info.get("cwd", "")

    # Resolve session_id — may be empty for poll-detected sessions.
    # Discover from the most recent JSONL in the project's session dir.
    session_id = info.get("session_id", "")
    if not session_id:
        tmux_pane = info.get("tmux_pane", "")
        session_id = _discover_session_id_from_pane(tmux_pane, proj.path)
        if not session_id:
            raise HTTPException(
                status_code=400,
                detail="Could not determine session ID for this pane. Is Claude Code running?",
            )

    # Check if session is already bound to an agent
    existing = db.query(Agent).filter(Agent.session_id == session_id).first()
    if existing:
        if existing.status in (AgentStatus.STOPPED, AgentStatus.ERROR):
            existing.session_id = None
            db.flush()
        else:
            try:
                os.unlink(info_path)
            except OSError:
                pass
            raise HTTPException(
                status_code=409,
                detail=f"Session already bound to active agent {existing.id} ({existing.name})",
            )

    if not existing_agent_id:
        _check_project_capacity(db, project_name)

    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        raise HTTPException(status_code=503, detail="Agent dispatcher not ready")

    if existing_agent_id:
        # Bind to existing agent
        agent = db.get(Agent, existing_agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        agent.session_id = session_id
        agent.tmux_pane = info.get("tmux_pane")
        if agent.status in (AgentStatus.STOPPED, AgentStatus.ERROR):
            agent.status = AgentStatus.IDLE
        agent.cli_sync = True
    else:
        # Create new agent
        for _ in range(20):
            agent_hex = secrets.token_hex(6)
            if db.get(Agent, agent_hex) is None:
                break
        else:
            raise HTTPException(status_code=500, detail="Failed to generate agent ID")

        agent = Agent(
            id=agent_hex,
            project=project_name,
            name=(
                f"Detected: {info['tmux_session']}"
                if info.get("tmux_session")
                else f"Manual: {os.path.basename(info.get('cwd', 'session'))}"
            )[:80],
            mode=AgentMode.AUTO,
            status=AgentStatus.IDLE,
            model=proj.default_model or CC_MODEL,
            cli_sync=True,
            session_id=session_id,
            tmux_pane=info.get("tmux_pane"),
            last_message_preview="Confirmed session",
            last_message_at=datetime.now(timezone.utc),
        )
        db.add(agent)

    db.commit()
    db.refresh(agent)

    # Write .owner, start sync, and immediately wake for first import
    ad.start_session_sync(agent.id, session_id, proj.path, cwd=actual_cwd)
    ad.wake_sync(agent.id)

    # Remove the unlinked entry
    try:
        os.unlink(info_path)
    except OSError:
        pass

    logger.info(
        "Adopted unlinked session %s → agent %s (project %s)",
        session_id[:12], agent.id, project_name,
    )

    asyncio.ensure_future(emit_agent_update(agent.id, agent.status.value, agent.project))

    return AgentOut.model_validate(agent)


@router.get("/api/agents", response_model=list[AgentBrief])
async def list_agents(
    request: Request,
    project: str | None = None,
    status: AgentStatus | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """List agents with optional filters."""
    q = db.query(Agent).filter(Agent.is_subagent == False)  # noqa: E712
    if project:
        q = q.filter(Agent.project == project)
    if status:
        q = q.filter(Agent.status == status)
    rows = (
        q.order_by(Agent.last_message_at.desc().nulls_last(), Agent.created_at.desc())
        .limit(limit)
        .all()
    )
    return _enrich_agent_briefs(rows, request)


@router.get("/api/agents/unread")
async def agents_unread_count(db: Session = Depends(get_db)):
    """Total unread message count across the top 50 agents (matching list limit)."""
    top = (
        db.query(Agent.unread_count)
        .filter(Agent.is_subagent == False)  # noqa: E712
        .order_by(Agent.last_message_at.desc().nulls_last(), Agent.created_at.desc())
        .limit(50)
        .all()
    )
    total = sum(r[0] for r in top if r[0])
    return {"unread": int(total)}


@router.get("/api/messages/search", response_model=MessageSearchResponse)
async def search_messages(
    q: str,
    project: str | None = None,
    role: MessageRole | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Full-text search across all message content."""
    if len(q) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    if limit > 200:
        limit = 200

    # Escape LIKE wildcards in user input
    safe_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    query = (
        db.query(Message, Agent.name, Agent.project)
        .join(Agent, Message.agent_id == Agent.id)
        .filter(or_(
            Message.content.ilike(f"%{safe_q}%", escape="\\"),
            Agent.id.ilike(f"%{safe_q}%", escape="\\"),
            Agent.name.ilike(f"%{safe_q}%", escape="\\"),
        ))
    )
    if project:
        query = query.filter(Agent.project == project)
    if role:
        query = query.filter(Message.role == role)

    total = query.count()
    rows = query.order_by(Message.created_at.desc()).limit(limit).all()

    results = []
    for msg, agent_name, agent_project in rows:
        # Build snippet: ~80 chars before and after first match
        content = msg.content or ""
        lower = content.lower()
        idx = lower.find(q.lower())
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(content), idx + len(q) + 80)
            snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
        else:
            snippet = content[:160] + ("..." if len(content) > 160 else "")

        results.append(MessageSearchResult(
            message_id=msg.id,
            agent_id=msg.agent_id,
            agent_name=agent_name,
            project=agent_project,
            role=msg.role,
            content_snippet=snippet,
            created_at=msg.created_at,
        ))

    return MessageSearchResponse(results=results, total=total)


@router.get("/api/agents/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Get full agent details."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Compute live session file size + successor link
    result = AgentOut.model_validate(agent)
    result.successor_id = compute_successor_id(agent.id, db)
    if agent.session_id:
        project = db.get(Project, agent.project)
        if project:
            from agent_dispatcher import _resolve_session_jsonl
            jsonl_path = _resolve_session_jsonl(
                agent.session_id, project.path, agent.worktree,
            )
            try:
                result.session_size_bytes = os.path.getsize(jsonl_path)
            except OSError:
                pass
    # Attach child subagents
    child_rows = db.query(Agent).filter(
        Agent.parent_id == agent.id,
        Agent.is_subagent == True,  # noqa: E712
    ).order_by(Agent.created_at).all()
    if child_rows:
        result.subagents = [AgentBrief.model_validate(r) for r in child_rows]

    return result


@router.delete("/api/agents/{agent_id}", response_model=AgentOut)
async def stop_agent(agent_id: str, request: Request,
                     generate_summary: bool = False,
                     task_complete: bool = True,
                     task_drop: bool = False,
                     incomplete_reason: str | None = None,
                     db: Session = Depends(get_db)):
    """Stop an agent — marks STOPPED."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == AgentStatus.STOPPED:
        raise HTTPException(status_code=400, detail="Agent is already stopped")

    # Capture task info before stopping (needed for background summary)
    _task_title = None
    _project_path = None
    _retry_task_id = None
    _retry_project_name = ""
    _retry_task_title = "Unknown task"
    _should_summarize = generate_summary and agent.task_id
    if agent.task_id:
        _t = db.get(Task, agent.task_id)
        if _t:
            _task_title = _t.title or "Unknown task"
        _p = db.get(Project, agent.project)
        _project_path = _p.path if _p else None
        if _should_summarize and not _project_path:
            _should_summarize = False

    # Kill the tmux pane/session if active
    if agent.tmux_pane:
        _graceful_kill_tmux(agent.tmux_pane, f"ah-{agent.id[:8]}")
        logger.info("Killed tmux pane %s for agent %s", agent.tmux_pane, agent.id)

    ad = getattr(request.app.state, "agent_dispatcher", None)

    if ad:
        ad.stop_agent_cleanup(db, agent, "Agent stopped",
                              kill_tmux=False, fail_executing=True,
                              fail_reason="Agent stopped by user",
                              cascade_subagents=True,
                              skip_task_transition=True)
    else:
        agent.status = AgentStatus.STOPPED
        agent.tmux_pane = None

        # Mark any EXECUTING messages as FAILED so they don't stay stuck
        executing_msgs = db.query(Message).filter(
            Message.agent_id == agent.id,
            Message.status == MessageStatus.EXECUTING,
        ).all()
        for m in executing_msgs:
            m.status = MessageStatus.FAILED
            m.error_message = "Agent stopped by user"
            m.completed_at = datetime.now(timezone.utc)

        # Add system message
        db.add(Message(
            agent_id=agent.id,
            role=MessageRole.SYSTEM,
            content="Agent stopped",
            status=MessageStatus.COMPLETED,
            delivered_at=datetime.now(timezone.utc),
        ))

        # Cascade stop to child subagents
        child_subs = db.query(Agent).filter(
            Agent.parent_id == agent.id,
            Agent.is_subagent == True,  # noqa: E712
            Agent.status != AgentStatus.STOPPED,
        ).all()
        for sub in child_subs:
            sub.status = AgentStatus.STOPPED
            if sub.tmux_pane:
                try:
                    import subprocess as _sp2
                    _sp2.run(["tmux", "kill-pane", "-t", sub.tmux_pane],
                             capture_output=True, timeout=_TMUX_CMD_TIMEOUT)
                except (OSError, subprocess.TimeoutExpired):
                    logger.debug("Failed to kill tmux pane for subagent %s", sub.id)
            asyncio.ensure_future(emit_agent_update(sub.id, "STOPPED", sub.project))

        asyncio.ensure_future(emit_agent_update(agent.id, "STOPPED", agent.project))

    db.commit()
    db.refresh(agent)
    logger.info("Agent %s stopped", agent.id)

    # Flush "Agent stopped" to display file + notify WS clients
    from display_writer import flush_agent as _stop_flush
    from websocket import emit_new_message as _stop_enm
    _stop_flush(agent.id)
    asyncio.ensure_future(_stop_enm(agent.id, "sync", agent.name, agent.project))

    # Transition linked task based on user choice
    if agent.task_id:
        _linked_task = db.get(Task, agent.task_id)
        if _linked_task and _linked_task.status in (TaskStatus.EXECUTING, TaskStatus.COMPLETE):
            if task_drop:
                TaskStateMachine.transition(_linked_task, TaskStatus.CANCELLED, strict=False)
                if incomplete_reason:
                    db.add(Message(
                        agent_id=agent.id, role=MessageRole.SYSTEM,
                        content=f"Task dropped — {incomplete_reason}",
                        status=MessageStatus.COMPLETED,
                        delivered_at=datetime.now(timezone.utc),
                    ))
                logger.info("Task %s CANCELLED/dropped (agent %s stopped by user)", _linked_task.id, agent.id)
            elif task_complete:
                TaskStateMachine.transition(_linked_task, TaskStatus.COMPLETE, strict=False)
                logger.info("Task %s marked COMPLETE (agent %s stopped by user)", _linked_task.id, agent.id)
            else:
                # Build human-readable retry_context for _build_task_prompt
                _ctx_parts = []
                if incomplete_reason:
                    _ctx_parts.append(f"User feedback: {incomplete_reason}")
                    db.add(Message(
                        agent_id=agent.id, role=MessageRole.SYSTEM,
                        content=f"Redo — {incomplete_reason}",
                        status=MessageStatus.COMPLETED,
                        delivered_at=datetime.now(timezone.utc),
                    ))
                if _ctx_parts:
                    _linked_task.retry_context = "\n".join(_ctx_parts)
                # Mark summary as generating — background thread will replace
                _linked_task.agent_summary = ":::generating:::"
                _linked_task.attempt_number = (_linked_task.attempt_number or 0) + 1
                TaskStateMachine.transition(_linked_task, TaskStatus.INBOX, strict=False)
                logger.info("Task %s returned to INBOX attempt=%d (agent %s stopped by user)",
                            _linked_task.id, _linked_task.attempt_number, agent.id)
                # Always generate retry summary for incomplete tasks
                _retry_task_id = _linked_task.id
                _retry_project_name = _linked_task.project_name or ""
                _retry_task_title = _linked_task.title or "Unknown task"
            db.commit()
            # Flush drop/redo message to display file + notify WS clients
            _stop_flush(agent.id)
            asyncio.ensure_future(_stop_enm(agent.id, "sync", agent.name, agent.project))
            asyncio.ensure_future(emit_task_update(
                _linked_task.id, _linked_task.status.value, _linked_task.project_name or "",
                title=_linked_task.title, agent_id=agent.id,
            ))

    # Spawn background summary thread if requested
    if _should_summarize:
        agent.insight_status = "generating"
        db.commit()
        thread = threading.Thread(
            target=_run_agent_summary_background,
            args=(agent.id, agent.name, _task_title, agent.project, _project_path),
            daemon=True,
        )
        thread.start()
        logger.info("Spawned background summary for agent %s", agent.id)

    # Spawn retry summary for incomplete tasks (always, no toggle needed)
    if not task_complete and _retry_task_id and _project_path:
        thread = threading.Thread(
            target=_generate_retry_summary_background,
            args=(agent.id, _retry_task_id, _retry_task_title,
                  _retry_project_name, _project_path, incomplete_reason),
            daemon=True,
        )
        thread.start()
        logger.info("Spawned retry summary for task %s", _retry_task_id)

    return agent


# --- Agent Insight Suggestions ---

@router.get("/api/agents/{agent_id}/suggestions", response_model=list[AgentInsightSuggestionOut])
async def get_agent_suggestions(agent_id: str, status: str = "pending",
                                db: Session = Depends(get_db)):
    """Return insight suggestions for an agent, filtered by status.

    status: "pending" (default), "accepted", "rejected", or "processed" (accepted+rejected).
    """
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    q = db.query(AgentInsightSuggestion).filter(
        AgentInsightSuggestion.agent_id == agent_id,
    )
    if status == "processed":
        q = q.filter(AgentInsightSuggestion.status.in_(["accepted", "rejected"]))
    else:
        q = q.filter(AgentInsightSuggestion.status == status)
    rows = q.order_by(AgentInsightSuggestion.id).all()
    return rows


class _ApplySuggestionsBody(BaseModel):
    accepted: list[dict] = []  # [{id, edited_content?}]
    rejected_ids: list[int] = []


@router.post("/api/agents/{agent_id}/apply-suggestions")
async def apply_agent_suggestions(agent_id: str, body: _ApplySuggestionsBody,
                                  db: Session = Depends(get_db)):
    """Accept/reject insight suggestions — write accepted ones to PROGRESS.md + FTS5."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    project = db.get(Project, agent.project)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    accepted_ids = {item["id"] for item in body.accepted}
    edits = {item["id"]: item.get("edited_content") for item in body.accepted}

    # Build PROGRESS.md section from accepted suggestions
    accepted_contents = []
    for item in body.accepted:
        row = db.get(AgentInsightSuggestion, item["id"])
        if not row or row.agent_id != agent_id:
            continue
        content = item.get("edited_content") or row.content
        row.edited_content = item.get("edited_content")
        row.status = "accepted"
        accepted_contents.append(content)

    # Mark rejected
    for rid in body.rejected_ids:
        row = db.get(AgentInsightSuggestion, rid)
        if row and row.agent_id == agent_id:
            row.status = "rejected"

    # Mark remaining pending as rejected too
    remaining = (
        db.query(AgentInsightSuggestion)
        .filter(
            AgentInsightSuggestion.agent_id == agent_id,
            AgentInsightSuggestion.status == "pending",
            AgentInsightSuggestion.id.notin_(accepted_ids),
        )
        .all()
    )
    for r in remaining:
        r.status = "rejected"

    # Clear flag
    agent.has_pending_suggestions = False
    db.commit()

    # Write accepted insights to PROGRESS.md
    if accepted_contents:
        from agent_dispatcher import store_insights
        today = datetime.now(timezone.utc).date().isoformat()
        progress_path = os.path.join(project.path, "PROGRESS.md")

        # Build section text
        task = db.get(Task, agent.task_id) if agent.task_id else None
        task_label = task.title if task else agent.name
        section_lines = [f"## {today} — {task_label}"]
        for i, c in enumerate(accepted_contents, 1):
            section_lines.append(f"{i}. {c}")
        new_section = "\n".join(section_lines)

        try:
            existing = ""
            if os.path.isfile(progress_path):
                with open(progress_path, "r", encoding="utf-8", errors="replace") as f:
                    existing = f.read()
            separator = "\n\n" if existing and not existing.endswith("\n\n") else (
                "\n" if existing and not existing.endswith("\n") else "")
            with open(progress_path, "w", encoding="utf-8") as f:
                f.write(existing + separator + new_section + "\n")
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Store in FTS5
        n = store_insights(db, agent.project, today, new_section, agent_id=agent_id)
        if n:
            logger.info("Stored %d agent insights in FTS5 for %s", n, agent.project)

    return {"success": True, "accepted": len(accepted_contents)}


@router.delete("/api/agents/{agent_id}/suggestions")
async def discard_agent_suggestions(agent_id: str, db: Session = Depends(get_db)):
    """Reject all pending suggestions for an agent."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    db.query(AgentInsightSuggestion).filter(
        AgentInsightSuggestion.agent_id == agent_id,
        AgentInsightSuggestion.status == "pending",
    ).update({"status": "rejected"})
    agent.has_pending_suggestions = False
    db.commit()
    return {"success": True}


@router.post("/api/agents/{agent_id}/regenerate-insights")
async def regenerate_agent_insights(agent_id: str, db: Session = Depends(get_db)):
    """Re-trigger insight generation for a stopped agent (e.g. after interrupted generation)."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status != AgentStatus.STOPPED:
        raise HTTPException(status_code=400, detail="Agent must be stopped")
    if agent.insight_status == "generating":
        raise HTTPException(status_code=400, detail="Already generating")
    if agent.has_pending_suggestions:
        raise HTTPException(status_code=400, detail="Suggestions already pending")

    # Need task + project path for context
    _task_title = "Unknown task"
    _project_path = None
    if agent.task_id:
        _t = db.get(Task, agent.task_id)
        if _t:
            _task_title = _t.title or "Unknown task"
    _p = db.get(Project, agent.project)
    _project_path = _p.path if _p else None
    if not _project_path:
        raise HTTPException(status_code=400, detail="Project path not found")

    agent.insight_status = "generating"
    db.commit()

    thread = threading.Thread(
        target=_run_agent_summary_background,
        args=(agent.id, agent.name, _task_title, agent.project, _project_path),
        daemon=True,
    )
    thread.start()
    logger.info("Regenerating insights for agent %s", agent.id)
    return {"success": True}


@router.delete("/api/agents/{agent_id}/permanent")
async def permanently_delete_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Permanently delete an agent, its messages, session JSONL, and output logs."""
    from session_cache import cleanup_source_session, evict_session

    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
        raise HTTPException(status_code=400, detail="Agent must be stopped before deleting")

    # 0. Kill tmux session if still alive (always try by name —
    #    tmux_pane is cleared to None during stop, but session may linger)
    import subprocess as _sp
    sess_name = f"ah-{agent.id[:8]}"
    try:
        _sp.run(["tmux", "kill-session", "-t", sess_name],
                capture_output=True, timeout=5)
        logger.info("Killed tmux session %s for permanent delete of agent %s", sess_name, agent.id)
    except (OSError, _sp.TimeoutExpired):
        logger.debug("tmux kill-session %s failed (may already be dead) for agent %s", sess_name, agent.id)

    # Cancel dispatcher tasks
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        ad._cancel_sync_task(agent.id)
        ad._cancel_launch_task(agent.id)
        ad._stale_session_retries.pop(agent.id, None)
        ad._known_subagents.pop(agent.id, None)

    # 1. Collect all agents to delete (parent + subagents cascade)
    child_agents = db.query(Agent).filter(
        Agent.parent_id == agent_id,
        Agent.is_subagent == True,  # noqa: E712
    ).all()
    agents_to_delete = [agent] + child_agents

    # Collect file info before deleting DB records
    all_agent_ids = [a.id for a in agents_to_delete]
    session_infos = [(a.session_id, a.project, a.worktree) for a in agents_to_delete if a.session_id]
    msg_ids = [m.id for m in db.query(Message.id).filter(Message.agent_id.in_(all_agent_ids)).all()]

    # 2. Delete DB records FIRST (so if this fails, no files are orphaned)
    deleted_msgs = db.query(Message).filter(Message.agent_id.in_(all_agent_ids)).delete(synchronize_session=False)
    # Unlink Tasks that reference these agents (SET NULL, don't delete the tasks)
    db.query(Task).filter(Task.agent_id.in_(all_agent_ids)).update(
        {Task.agent_id: None}, synchronize_session=False
    )
    # Delete children first (FK ordering), then parent
    for child in child_agents:
        db.delete(child)
    db.delete(agent)
    db.commit()

    # 3. Delete session source files (.jsonl + subdir) and cache (safe: DB already committed)
    cleaned_files = []
    for sid, proj_name, worktree in session_infos:
        project = db.query(Project).filter(Project.name == proj_name).first()
        if project:
            if cleanup_source_session(sid, project.path, worktree):
                cleaned_files.append(f"{sid}.jsonl")
            evict_session(sid, project.path, worktree)

    # 4. Delete display files for all agents being removed
    from display_writer import delete_agent as _delete_display
    for aid in all_agent_ids:
        _delete_display(aid)
        cleaned_files.append(f"display/{aid}.jsonl")

    # 5. Delete output log files for all messages
    for mid in msg_ids:
        log_path = os.path.join(tempfile.gettempdir(), f"claude-output-{mid}.log")
        if os.path.isfile(log_path):
            try:
                os.remove(log_path)
                cleaned_files.append(log_path)
            except OSError as e:
                logger.warning("Failed to delete output log %s: %s", log_path, e)

    logger.info("Permanently deleted agent %s (+%d subagents, %d messages, %d files cleaned)",
                agent_id, len(child_agents), deleted_msgs, len(cleaned_files))
    return {
        "detail": "ok",
        "deleted_messages": deleted_msgs,
        "deleted_subagents": len(child_agents),
        "cleaned_files": len(cleaned_files),
    }


@router.post("/api/agents/{agent_id}/resume", response_model=AgentOut)
async def resume_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Resume a stopped or errored agent."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
        raise HTTPException(status_code=400, detail="Agent is already running")

    # Block resume if this agent was superseded by a successor (not subagents)
    successor = db.query(Agent).filter(
        Agent.parent_id == agent.id,
        Agent.is_subagent == False,
    ).order_by(Agent.created_at.desc()).first()
    if successor:
        raise HTTPException(
            status_code=409,
            detail=json.dumps({
                "reason": "superseded",
                "successor_id": successor.id,
                "successor_name": successor.name,
                "message": "This agent was continued by a new agent. Open the successor instead.",
            }),
        )

    project = db.get(Project, agent.project)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.archived:
        raise HTTPException(status_code=400, detail="Cannot resume agents for archived projects — activate first")

    wm = getattr(request.app.state, "worker_manager", None)
    if not wm:
        raise HTTPException(status_code=500, detail="Worker manager not available")

    # Parse optional body for resume mode
    body = {}
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        pass  # Empty body or no content-type — use defaults
    resume_mode = body.get("mode")  # "tmux" | None

    wm.ensure_project_ready(project)

    # Clear stale session retry counter so resumed agents get
    # full retry budget for session recovery
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        ad._stale_session_retries.pop(agent.id, None)

    resumed_sync = False

    if resume_mode == "tmux":
        # Launch a new tmux session and resume the CLI session in it
        import shlex
        import subprocess
        from config import CLAUDE_BIN

        cmd_parts = [CLAUDE_BIN,
                      "--output-format", "stream-json", "--verbose"]
        if agent.skip_permissions:
            cmd_parts.append("--dangerously-skip-permissions")
        if agent.model:
            cmd_parts += ["--model", agent.model]
        if agent.session_id:
            cmd_parts += ["--resume", agent.session_id]
        claude_cmd = " ".join(shlex.quote(p) for p in cmd_parts)

        tmux_session = f"ah-{agent.id[:8]}"
        _preflight_claude_project(project.path)

        pane_id = _create_tmux_claude_session(tmux_session, project.path, claude_cmd, agent_id=agent.id)

        agent.tmux_pane = pane_id
        agent.status = AgentStatus.IDLE
        if agent.session_id and ad:
            ad.start_session_sync(agent.id, agent.session_id, project.path)
        resumed_sync = True
    elif ad:
        # Default: try to re-establish sync with existing tmux pane
        from agent_dispatcher import _detect_tmux_pane_for_session, _resolve_session_jsonl
        from session_cache import session_source_dir

        sid = agent.session_id

        # If session_id was never assigned (e.g. tmux launch failed
        # before detecting the JSONL), discover it from the project's
        # session directory by picking the most recently modified file.
        # Check both project root and worktree session dirs.
        if not sid:
            sdirs = [session_source_dir(project.path)]
            if agent.worktree:
                wt_path = os.path.join(project.path, ".claude", "worktrees", agent.worktree)
                wt_sdir = session_source_dir(wt_path)
                if os.path.isdir(wt_sdir) and wt_sdir not in sdirs:
                    sdirs.append(wt_sdir)
            best, best_mtime = None, 0.0
            for sdir in sdirs:
                if not os.path.isdir(sdir):
                    continue
                try:
                    for fname in os.listdir(sdir):
                        if not fname.endswith(".jsonl"):
                            continue
                        fpath = os.path.join(sdir, fname)
                        mt = os.path.getmtime(fpath)
                        if mt > best_mtime:
                            best, best_mtime = fname.replace(".jsonl", ""), mt
                except OSError as e:
                    logger.warning(
                        "resume_agent: failed to scan session dir %s for agent %s: %s",
                        sdir, agent.id, e,
                    )
            if best:
                sid = best
                agent.session_id = sid
                logger.info(
                    "Discovered session %s for agent %s on resume",
                    sid, agent.id,
                )

        if sid:
            jsonl_path = _resolve_session_jsonl(sid, project.path, agent.worktree)
            if os.path.exists(jsonl_path) and not ad._session_has_ended(jsonl_path):
                pane = _detect_tmux_pane_for_session(sid, project.path)
                agent.status = AgentStatus.IDLE
                agent.tmux_pane = pane  # may be None; sync loop will retry
                ad.start_session_sync(agent.id, sid, project.path)
                resumed_sync = True

    if not resumed_sync and agent.status not in (AgentStatus.IDLE, AgentStatus.IDLE):
        agent.status = AgentStatus.IDLE

    msg = Message(
        agent_id=agent.id,
        role=MessageRole.SYSTEM,
        content="Agent resumed" + (" — syncing CLI session" if resumed_sync else ""),
        status=MessageStatus.COMPLETED,
        delivered_at=_utcnow(),
    )
    db.add(msg)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Session already owned by another agent",
        )
    db.refresh(agent)
    asyncio.ensure_future(emit_agent_update(agent.id, agent.status.value, agent.project))
    logger.info("Agent %s resumed (sync=%s, mode=%s)", agent.id, resumed_sync, resume_mode)
    return agent


@router.put("/api/agents/reorder")
async def reorder_agents(body: dict, db: Session = Depends(get_db)):
    """Set sort_order for a list of agent IDs. Body: { "agent_ids": ["id1", "id2", ...] }"""
    agent_ids = body.get("agent_ids", [])
    if not agent_ids:
        raise HTTPException(400, "agent_ids required")
    for i, aid in enumerate(agent_ids):
        agent = db.get(Agent, aid)
        if agent:
            agent.sort_order = i
    db.commit()
    return {"ok": True, "count": len(agent_ids)}


@router.put("/api/agents/read-all")
async def mark_all_agents_read(db: Session = Depends(get_db)):
    """Mark all agents as read (reset unread count for every agent)."""
    count = db.query(Agent).filter(Agent.unread_count > 0).update({"unread_count": 0})
    db.commit()
    return {"detail": "ok", "updated": count}


@router.put("/api/agents/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Update agent properties (currently: name)."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    body = await request.json()
    if "name" in body:
        name = str(body["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        if len(name) > 200:
            raise HTTPException(status_code=400, detail="Name too long (max 200)")
        agent.name = name
    if "muted" in body:
        agent.muted = bool(body["muted"])
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/api/agents/{agent_id}/messages", response_model=PaginatedMessages)
async def get_agent_messages(
    agent_id: str,
    limit: int = 50,
    before: str | None = None,
    after: str | None = None,
    db: Session = Depends(get_db),
):
    """Get conversation messages for an agent with cursor pagination.

    Sort order: session_seq ASC (messages without session_seq sink to bottom).
    - No cursor (initial load): newest `limit` messages, oldest-first.
    - `before=<int>`: messages with session_seq < cursor (scroll-up).
    - `after=<int>`: messages with session_seq > cursor (incremental refresh).
    Returns { messages: [...], has_more: bool }.
    """
    from sqlalchemy import func

    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Sort key: session_seq if set, otherwise 999999 (unsequenced → bottom)
    sort_key = func.coalesce(Message.session_seq, 999999)

    query = db.query(Message).filter(Message.agent_id == agent_id)

    if before:
        before_seq = int(before)
        rows = (
            query.filter(sort_key < before_seq)
            .order_by(sort_key.desc(), Message.created_at.desc())
            .limit(limit + 1)
            .all()
        )
        has_more = len(rows) > limit
        messages = rows[:limit][::-1]
    elif after:
        after_seq = int(after)
        messages = (
            query.filter(sort_key > after_seq)
            .order_by(sort_key.asc(), Message.created_at.asc())
            .all()
        )
        has_more = False  # always returns everything newer
    else:
        # Default: newest `limit` messages
        rows = (
            query.order_by(sort_key.desc(), Message.created_at.desc())
            .limit(limit + 1)
            .all()
        )
        has_more = len(rows) > limit
        messages = rows[:limit][::-1]
        # Reset unread count only on initial load
        if agent.unread_count > 0:
            agent.unread_count = 0
            db.commit()

    return PaginatedMessages(messages=messages, has_more=has_more)


def _apply_display_content(msg: Message) -> MessageOut:
    """Serialize a queued Message to MessageOut, applying the same display
    transformations used when writing the JSONL file (attachment stripping,
    stop-note parsing, task-notification XML, display_content override)."""
    from display_writer import transform_for_display

    out = MessageOut.model_validate(msg)
    meta = None
    if msg.meta_json:
        try:
            meta = json.loads(msg.meta_json)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Skipped unparseable meta_json for msg_id=%s", msg.id)
            pass
    role_val = msg.role.value if msg.role else None
    content, meta = transform_for_display(role_val, out.content, meta)
    out.content = content
    if meta is not None:
        out.metadata = meta
    return out


@router.get("/api/agents/{agent_id}/display", response_model=DisplayResponse)
async def get_agent_display(
    agent_id: str,
    offset: int = Query(0, ge=0),
    tail_bytes: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Read display entries from the per-agent JSONL file.

    Returns parsed messages, the byte offset to resume from, any queued
    web/plan messages not yet written, and whether earlier content exists
    before the returned window.

    Note: last-occurrence-wins by id handles _replace entries appended by
    update_last() for streaming and delivery-status updates.
    """
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    display_path = os.path.join(DISPLAY_DIR, f"{agent_id}.jsonl")
    empty = DisplayResponse(messages=[], next_offset=0, queued=[], has_earlier=False)

    if not os.path.isfile(display_path):
        # Still return queued messages even if no display file yet
        queued = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.source.in_(("web", "plan_continue", "task")),
                Message.display_seq.is_(None),
            )
            .order_by(Message.created_at.asc())
            .all()
        )
        empty.queued = [_apply_display_content(m) for m in queued]
        return empty

    has_earlier = False
    try:
        with open(display_path, "r", encoding="utf-8") as f:
            file_size = f.seek(0, 2)  # seek to end to get size

            if tail_bytes > 0 and offset == 0:
                start = max(0, file_size - tail_bytes)
                f.seek(start)
                if start > 0:
                    # Align to next complete line boundary
                    f.readline()  # discard partial line
                has_earlier = f.tell() > 0 and start > 0
            elif offset > 0:
                f.seek(min(offset, file_size))
                has_earlier = True
            else:
                f.seek(0)

            raw = f.read()
            next_offset = f.tell()
    except OSError:
        return empty

    # Parse lines, dedup by id (last occurrence wins for _replace entries)
    seen: dict[str, DisplayEntry] = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Skipped malformed JSON in display file for agent %s", agent_id)
            continue
        try:
            entry = DisplayEntry.model_validate(obj)
        except Exception as e:
            logger.warning("Failed to parse display entry: %s", e)
            continue
        seen[entry.id] = entry

    messages = list(seen.values())

    # Queued messages: sent from web/plan but not yet in the display file
    queued = (
        db.query(Message)
        .filter(
            Message.agent_id == agent_id,
            Message.source.in_(("web", "plan_continue", "task")),
            Message.display_seq.is_(None),
        )
        .order_by(Message.created_at.asc())
        .all()
    )

    return DisplayResponse(
        messages=messages,
        next_offset=next_offset,
        queued=[_apply_display_content(m) for m in queued],
        has_earlier=has_earlier,
    )


@router.post("/api/agents/{agent_id}/wake-sync")
async def wake_agent_sync(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Wake the sync loop for an agent to trigger immediate JSONL import.

    For ERROR agents, recovers to IDLE first so the sync loop doesn't
    immediately exit.  This is the only path that does ERROR→IDLE —
    hooks calling wake_sync() won't auto-recover.
    """
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        raise HTTPException(status_code=503, detail="Dispatcher not ready")
    # Explicit user action: recover ERROR agents before waking sync
    if agent.status == AgentStatus.ERROR:
        agent.status = AgentStatus.IDLE
        agent.error_message = None
        db.commit()
        from websocket import emit_agent_update
        ad._emit(emit_agent_update(agent.id, "IDLE", agent.project))
        logger.info("wake-sync: recovered agent %s from ERROR → IDLE", agent_id[:8])
    # Re-dispatch stuck QUEUED messages (sent to tmux but never confirmed in JSONL)
    asyncio.ensure_future(ad.redispatch_stuck_queued(agent_id))
    # Also dispatch any PENDING messages if agent is idle
    asyncio.ensure_future(ad.dispatch_pending_message(agent_id, delay=0))
    if ad.wake_sync(agent_id):
        return {"status": "ok", "detail": "Sync woken"}
    raise HTTPException(status_code=409, detail="No active sync loop for this agent")


@router.post("/api/agents/{agent_id}/messages", response_model=MessageOut, status_code=201)
async def send_agent_message(
    agent_id: str,
    body: SendMessage,
    request: Request,
    db: Session = Depends(get_db),
):
    """Send a follow-up message to an agent."""
    import slash_commands
    if slash_commands.is_slash_command(body.content) and not slash_commands.is_allowed(body.content):
        raise HTTPException(status_code=400, detail=slash_commands.rejection_message(body.content))

    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == AgentStatus.STOPPED:
        raise HTTPException(status_code=400, detail="Agent is stopped")

    # --- Scheduled messages: store as PENDING for _dispatch_tmux_scheduled ---
    scheduled_at = None
    if body.scheduled_at:
        from datetime import datetime, timezone
        try:
            scheduled_at = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled_at format")

    # --- Tmux agents: send via tmux immediately (even while generating) ---
    has_tmux = (
        agent.status in (AgentStatus.IDLE, AgentStatus.STARTING, AgentStatus.EXECUTING)
        and agent.tmux_pane
        and not scheduled_at
    )
    if has_tmux:
        from agent_dispatcher import (
            _detect_tmux_pane_for_session,
            send_tmux_message,
            verify_tmux_pane,
        )
        from websocket import emit_new_message

        if not verify_tmux_pane(agent.tmux_pane):
            recovered_pane = None
            if agent.session_id:
                project = db.get(Project, agent.project)
                if project:
                    candidate = _detect_tmux_pane_for_session(agent.session_id, project.path)
                    if candidate and verify_tmux_pane(candidate):
                        recovered_pane = candidate

            if recovered_pane:
                agent.tmux_pane = recovered_pane
                db.commit()
            else:
                ad_msg = getattr(request.app.state, "agent_dispatcher", None)
                if ad_msg:
                    ad_msg._clear_agent_pane(db, agent, kill_tmux=False)
                else:
                    agent.tmux_pane = None
                db.commit()
                has_tmux = False

        if has_tmux:
            agent_is_busy = bool(agent.generating_msg_id) or agent.status == AgentStatus.EXECUTING

            if not agent_is_busy:
                # --- IDLE: send via tmux immediately ---
                # After C-c interrupt, CC needs time to finish processing
                # (write file-history-snapshot, return to input prompt).
                # If we send too soon, CC treats it as enqueued input and
                # skips all hooks (UserPromptSubmit + Stop).
                import time as _time
                POST_ESC_GRACE = 2.0  # seconds
                last_esc = _last_escape.get(agent_id, 0)
                since_esc = _time.time() - last_esc
                if 0 < since_esc < POST_ESC_GRACE:
                    await asyncio.sleep(POST_ESC_GRACE - since_esc)

                ok = send_tmux_message(agent.tmux_pane, body.content)
                if not ok:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to send via tmux",
                    )

                # Create message in DB
                project = db.get(Project, agent.project)
                if not project:
                    raise HTTPException(status_code=400, detail="Project not found")
                ad = getattr(request.app.state, "agent_dispatcher", None)
                if ad:
                    msg, _, _ = ad._prepare_dispatch(
                        db, agent, project, body.content,
                        source="web",
                        wrap_prompt=False,
                    )
                else:
                    msg = Message(
                        agent_id=agent.id,
                        role=MessageRole.USER,
                        content=body.content,
                        source="web",
                    )
                    db.add(msg)
                # QUEUED = sent via tmux, awaiting JSONL delivery confirmation.
                # Slash commands also get QUEUED — delivery is confirmed the same
                # way (sync engine matches the turn in JSONL).
                msg.status = MessageStatus.QUEUED
                # delivered_at stays NULL — sync engine sets it from JSONL timestamp
                if ad:
                    msg.dispatch_seq = ad.next_dispatch_seq(db, agent.id)
                db.commit()
                db.refresh(msg)
                if ad:
                    ad._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))
                    if msg.meta_json:
                        import json as _json
                        from websocket import emit_metadata_update
                        ad._emit(emit_metadata_update(agent.id, msg.id, _json.loads(msg.meta_json)))
                # Flush to display file so queued message appears immediately
                from display_writer import flush_agent as _msg_flush
                _msg_flush(agent.id)
                logger.info("Message %s queued to agent %s via tmux pane %s", msg.id, agent.id, agent.tmux_pane)
                return msg

            # --- BUSY: agent is generating — store as PENDING for stop-hook dispatch ---
            project = db.get(Project, agent.project)
            if not project:
                raise HTTPException(status_code=400, detail="Project not found")
            ad = getattr(request.app.state, "agent_dispatcher", None)
            if ad:
                msg, _, _ = ad._prepare_dispatch(
                    db, agent, project, body.content,
                    source="web",
                    wrap_prompt=False,
                )
            else:
                msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.USER,
                    content=body.content,
                    source="web",
                )
                db.add(msg)
            msg.status = MessageStatus.PENDING
            db.commit()
            db.refresh(msg)
            if ad:
                ad._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))
                if msg.meta_json:
                    import json as _json
                    from websocket import emit_metadata_update
                    ad._emit(emit_metadata_update(agent.id, msg.id, _json.loads(msg.meta_json)))
            # No flush_agent here — PENDING messages stay out of the display
            # file until dispatched (QUEUED) by the stop hook, so they get
            # display_seq after the preceding agent response.
            logger.info("Message %s stored PENDING for busy agent %s (generating %s) — stop hook will dispatch",
                        msg.id, agent.id, agent.generating_msg_id)
            return msg

    # --- Fallback: store as PENDING (e.g., scheduled messages) ---
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        project = db.get(Project, agent.project)
        if project:
            msg, _, _ = ad._prepare_dispatch(
                db, agent, project, body.content,
                source="web",
                wrap_prompt=False,
            )
        else:
            msg = Message(
                agent_id=agent.id,
                role=MessageRole.USER,
                content=body.content,
                source="web",
            )
            db.add(msg)
    else:
        msg = Message(
            agent_id=agent.id,
            role=MessageRole.USER,
            content=body.content,
            source="web",
        )
        db.add(msg)
    msg.status = MessageStatus.PENDING
    msg.scheduled_at = scheduled_at

    db.commit()
    db.refresh(msg)
    if ad:
        from websocket import emit_new_message
        ad._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))
        if msg.meta_json:
            import json as _json
            from websocket import emit_metadata_update
            ad._emit(emit_metadata_update(agent.id, msg.id, _json.loads(msg.meta_json)))
    logger.info("Message %s pending for agent %s", msg.id, agent.id)
    return msg



@router.put("/api/agents/{agent_id}/read")
async def mark_agent_read(agent_id: str, db: Session = Depends(get_db)):
    """Mark agent as read (reset unread count)."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.unread_count = 0
    db.commit()
    return {"detail": "ok"}


@router.delete("/api/agents/{agent_id}/messages/{message_id}")
async def cancel_message(agent_id: str, message_id: str, db: Session = Depends(get_db)):
    """Cancel a pending/queued/scheduled message."""
    msg = db.get(Message, message_id)
    if not msg or msg.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.status not in (MessageStatus.PENDING, MessageStatus.QUEUED):
        raise HTTPException(status_code=400, detail="Only PENDING or QUEUED messages can be cancelled")
    db.delete(msg)
    db.commit()
    logger.info("Message %s cancelled for agent %s", message_id, agent_id)
    from websocket import emit_message_update
    await emit_message_update(agent_id, message_id, "CANCELLED")
    return {"detail": "Message cancelled"}


@router.put("/api/agents/{agent_id}/messages/{message_id}", response_model=MessageOut)
async def update_message(
    agent_id: str,
    message_id: str,
    body: UpdateMessage,
    db: Session = Depends(get_db),
):
    """Update content and/or scheduled_at of a PENDING message."""
    msg = db.get(Message, message_id)
    if not msg or msg.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.status != MessageStatus.PENDING:
        raise HTTPException(status_code=400, detail="Only PENDING messages can be updated")

    if body.content is not None:
        if not body.content.strip():
            raise HTTPException(status_code=400, detail="Content cannot be empty")
        msg.content = body.content.strip()

    if body.scheduled_at is not None:
        if body.scheduled_at == "":
            # Clear scheduled_at (convert to immediate pending)
            msg.scheduled_at = None
        else:
            try:
                msg.scheduled_at = datetime.fromisoformat(
                    body.scheduled_at.replace("Z", "+00:00")
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid scheduled_at format")

    db.commit()
    db.refresh(msg)
    logger.info("Message %s updated for agent %s", message_id, agent_id)
    return msg


# ---- Interactive Answer (AskUserQuestion / ExitPlanMode via tmux) ----

class AnswerPayload(BaseModel):
    tool_use_id: str
    type: str  # "ask_user_question", "exit_plan_mode", or "permission_prompt"
    selected_index: int | None = None  # 0-based option index (AskUserQuestion)
    question_index: int = 0  # which question in multi-Q AskUserQuestion
    approved: bool | None = None  # (ExitPlanMode only)


_PLAN_LABELS = [
    "Yes, bypass permissions",
    "Yes, manual approval",
    "Give feedback",
]


def _patch_interactive_answer(
    db: Session, agent_id: str, tool_use_id: str,
    selected_index: int, answer_type: str,
    question_index: int = 0,
) -> bool:
    """Immediately mark an interactive item as answered in the DB.

    Builds an answer string from the selected option so the frontend can
    render the selection without waiting for the sync loop to pick up the
    tool_result from the session JSONL.

    For multi-question AskUserQuestion, each call patches one question at a
    time via question_index, accumulating into selected_indices and answer.
    """
    msgs = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.tool_use_id == tool_use_id,
    ).order_by(Message.created_at.desc()).all()

    for msg in msgs:
        try:
            meta = json.loads(msg.meta_json)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Skipped unparseable meta_json in _patch_interactive_answer, msg_id=%s", msg.id)
            continue
        items = meta.get("interactive")
        if not items:
            continue
        for item in items:
            if item.get("tool_use_id") != tool_use_id:
                continue
            # Don't overwrite a dismissed/rejected answer
            existing_answer = item.get("answer") or ""
            if isinstance(existing_answer, str) and (
                existing_answer.startswith("The user doesn't want to proceed")
                or existing_answer.startswith("User declined")
                or existing_answer.startswith("Tool use rejected")
            ):
                return
            if answer_type == "ask_user_question":
                # Per-question check: skip if this specific question already answered
                sel_indices = item.get("selected_indices", {})
                if sel_indices.get(str(question_index)) is not None:
                    return
                # Store per-question index
                sel_indices[str(question_index)] = selected_index
                item["selected_indices"] = sel_indices
                # Backward compat: also set selected_index for Q0
                if question_index == 0:
                    item["selected_index"] = selected_index
                # Build answer string for this question
                questions = item.get("questions", [])
                if questions and question_index < len(questions):
                    q = questions[question_index]
                    options = q.get("options", [])
                    label = options[selected_index]["label"] if selected_index < len(options) else str(selected_index)
                    part = f'"{q.get("question", "")}"="{label}"'
                else:
                    part = str(selected_index)
                # Append to existing answer (multi-question accumulation)
                existing = item.get("answer")
                if existing and isinstance(existing, str):
                    item["answer"] = existing + "\n" + part
                else:
                    item["answer"] = part
            elif answer_type == "permission_prompt":
                if item.get("answer") is not None:
                    return  # Already answered
                questions = item.get("questions", [])
                if questions:
                    options = questions[0].get("options", [])
                    label = options[selected_index]["label"] if selected_index < len(options) else str(selected_index)
                else:
                    label = str(selected_index)
                item["selected_index"] = selected_index
                item["answer"] = label
            elif answer_type == "exit_plan_mode":
                if item.get("answer") is not None:
                    return  # Already answered
                item["selected_index"] = selected_index
                item["answer"] = _PLAN_LABELS[selected_index] if selected_index < len(_PLAN_LABELS) else str(selected_index)
            msg.meta_json = json.dumps(meta)
            db.commit()
            # Update display file so the answer persists across page refreshes
            from display_writer import update_last as _update_ia
            _update_ia(agent_id, msg.id)
            return {"message_id": msg.id, "metadata": meta}

    logger.debug(
        "No interactive item found for tool_use_id=%s agent=%s (type=%s)",
        tool_use_id, agent_id, answer_type,
    )
    return None


def _count_interactive_questions(db: Session, agent_id: str, tool_use_id: str) -> int:
    """Return the total number of questions for an interactive item."""
    msgs = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.meta_json.is_not(None),
    ).order_by(Message.created_at.desc()).limit(50).all()
    for msg in msgs:
        try:
            meta = json.loads(msg.meta_json)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Skipped unparseable meta_json in _count_interactive_questions, msg_id=%s", msg.id)
            continue
        for item in meta.get("interactive", []):
            if item.get("tool_use_id") == tool_use_id:
                return len(item.get("questions", []))
    return 1


def _build_answers_from_metadata(db: Session, agent_id: str, tool_use_id: str) -> dict:
    """Build {question_text: selected_label} from accumulated per-question answers."""
    msgs = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.tool_use_id == tool_use_id,
    ).order_by(Message.created_at.desc()).all()
    for msg in msgs:
        try:
            meta = json.loads(msg.meta_json or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.debug("Skipped unparseable meta_json in _build_answers_from_metadata, msg_id=%s", msg.id)
            continue
        for item in meta.get("interactive", []):
            if item.get("tool_use_id") != tool_use_id:
                continue
            answers = {}
            questions = item.get("questions", [])
            sel_indices = item.get("selected_indices", {})
            for qi, q in enumerate(questions):
                idx = sel_indices.get(str(qi))
                if idx is not None:
                    options = q.get("options", [])
                    if idx < len(options):
                        answers[q["question"]] = options[idx]["label"]
            return answers
    return {}


def _get_questions_from_metadata(db: Session, agent_id: str, tool_use_id: str) -> list:
    """Get the original questions array from interactive metadata."""
    msgs = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.tool_use_id == tool_use_id,
    ).order_by(Message.created_at.desc()).all()
    for msg in msgs:
        try:
            meta = json.loads(msg.meta_json or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.debug("Skipped unparseable meta_json in _get_questions_from_metadata, msg_id=%s", msg.id)
            continue
        for item in meta.get("interactive", []):
            if item.get("tool_use_id") == tool_use_id:
                return item.get("questions", [])
    return []


@router.post("/api/agents/{agent_id}/answer")
async def answer_agent_interactive(
    agent_id: str,
    body: AnswerPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """Answer an AskUserQuestion or approve/reject ExitPlanMode via tmux keys."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status not in (AgentStatus.IDLE, AgentStatus.EXECUTING, AgentStatus.IDLE):
        raise HTTPException(status_code=400, detail=f"Agent is {agent.status}, not in interactive state")

    # Agents without an active tmux pane (e.g. pane lost): patch DB only.
    # Claude auto-approves with --dangerously-skip-permissions, so the card is informational.
    has_tmux = bool(agent.tmux_pane)
    if has_tmux:
        from agent_dispatcher import send_tmux_keys, verify_tmux_pane
        if not verify_tmux_pane(agent.tmux_pane):
            raise HTTPException(status_code=400, detail="Tmux pane no longer exists")

    pane_id = agent.tmux_pane
    MAX_INDEX = 20  # safety cap to prevent excessive keystrokes

    if body.type == "ask_user_question":
        if body.selected_index is None or body.selected_index < 0:
            raise HTTPException(status_code=400, detail="selected_index required for ask_user_question")
        if body.selected_index > MAX_INDEX:
            raise HTTPException(status_code=400, detail=f"selected_index too large (max {MAX_INDEX})")

        # Patch DB immediately for instant UI feedback
        patched = _patch_interactive_answer(db, agent_id, body.tool_use_id, body.selected_index, body.type, body.question_index)
        if not patched:
            logger.warning("Interactive patch missed: tool_use_id=%s agent=%s", body.tool_use_id, agent_id)
        else:
            from websocket import emit_metadata_update
            await emit_metadata_update(agent_id, patched["message_id"], patched["metadata"])

        # Multi-Q: accumulate answers until all questions are answered
        total_questions = _count_interactive_questions(db, agent_id, body.tool_use_id)
        if total_questions > 1 and body.question_index < total_questions - 1:
            return {"detail": "ok", "partial": True, "question_index": body.question_index}

        # All questions answered — try updatedInput path (hook-based, no tmux keys)
        from permissions import PermissionManager
        pm: PermissionManager | None = getattr(request.app.state, "permission_manager", None)
        pending_id = pm.find_pending_by_tool(agent_id, "AskUserQuestion") if pm else None

        if pending_id:
            # Build updatedInput payload: {questions: [...], answers: {q_text: label}}
            answers = _build_answers_from_metadata(db, agent_id, body.tool_use_id)
            questions = _get_questions_from_metadata(db, agent_id, body.tool_use_id)
            updated_input = {"questions": questions, "answers": answers}
            pm.respond(pending_id, "allow", reason="Answered from AgentHive", updated_input=updated_input)
            logger.info("AskUserQuestion resolved via updatedInput for agent %s: %s", agent_id[:8], list(answers.keys()))
            return {"detail": "ok", "method": "updatedInput"}
        else:
            # Fallback: tmux keys (no pending hook request — race condition or old CC)
            logger.info("AskUserQuestion fallback to tmux keys for agent %s (no pending hook request)", agent_id[:8])
            if has_tmux:
                keys = ["Down"] * body.selected_index + ["Enter"]
                if not send_tmux_keys(pane_id, keys):
                    raise HTTPException(status_code=500, detail="Failed to send keys to tmux")
                # Multi-Q submit confirmation
                if total_questions > 1 and body.question_index == total_questions - 1:
                    await asyncio.sleep(0.5)
                    send_tmux_keys(pane_id, ["Enter"])
                    return {"detail": "ok", "method": "tmux", "keys_sent": body.selected_index + 2, "submitted": True}
                return {"detail": "ok", "method": "tmux", "keys_sent": body.selected_index + 1}
            else:
                return {"detail": "ok", "method": "tmux", "keys_sent": 0, "auto_approved": True}

    elif body.type == "exit_plan_mode":
        # Claude Code TUI plan approval options (arrow-navigated):
        # (v2.1.81+: "clear context" hidden by default)
        # 0: "Yes, and bypass permissions"
        # 1: "Yes, manually approve edits"
        # 2: "Type here to tell Claude what to change"

        if body.selected_index is not None and body.selected_index >= 0:
            if body.selected_index > MAX_INDEX:
                raise HTTPException(status_code=400, detail=f"selected_index too large (max {MAX_INDEX})")
            keys = ["Down"] * body.selected_index + ["Enter"]
        elif body.approved is True:
            keys = ["Enter"]  # legacy: approve = first option (bypass permissions)
        elif body.approved is False:
            keys = ["Down", "Enter"]  # legacy: reject → manual approval (safest)
        else:
            raise HTTPException(status_code=400, detail="selected_index or approved required for exit_plan_mode")
        effective_index = body.selected_index
        if effective_index is None:
            effective_index = 0 if body.approved else 1

        # --- Check if this is a planning agent ---
        _task = db.get(Task, agent.task_id) if agent.task_id else None
        is_planning_agent = _task and _task.status == TaskStatus.PLANNING

        if is_planning_agent and effective_index in (0, 1):
            # Planning agent: extract plan, kill tmux, dispatch new -p execution agent.
            # DON'T send tmux keys — the planning agent's job is done.

            # Extract plan text from the interactive metadata
            plan_text = ""
            if last_msg and last_msg.meta_json:
                try:
                    meta = json.loads(last_msg.meta_json)
                    for item in meta.get("interactive", []):
                        if item.get("type") == "exit_plan_mode":
                            plan_text = item.get("plan", "")
                            break
                except (json.JSONDecodeError, AttributeError):
                    logger.debug("Failed to parse plan metadata for agent %s", agent_id)
                    pass

            # Patch metadata to record the approval
            patched = _patch_interactive_answer(db, agent_id, body.tool_use_id, effective_index, body.type)
            if not patched:
                logger.warning("Interactive patch missed: tool_use_id=%s agent=%s", body.tool_use_id, agent_id)
            else:
                from websocket import emit_metadata_update
                await emit_metadata_update(agent_id, patched["message_id"], patched["metadata"])

            try:
                # Store approved plan on task for the execution agent
                _task.retry_context = plan_text or None

                # Transition PLANNING → PENDING (dispatch picks it up)
                TaskStateMachine.transition(_task, TaskStatus.PENDING)
                _task.agent_id = None  # unlink so dispatch creates new agent
                _task.started_at = None

                # Option 1 (manual approval): disable skip_permissions
                if effective_index == 1:
                    _task.skip_permissions = False

                # Stop planning agent
                agent.task_id = None
                agent.status = AgentStatus.STOPPED
                db.commit()

                # Kill tmux session
                if has_tmux:
                    _graceful_kill_tmux(pane_id, f"ah-{agent.id[:8]}")

                asyncio.ensure_future(emit_task_update(
                    _task.id, _task.status.value, _task.project_name or "",
                    title=_task.title,
                ))
                logger.info(
                    "Planning task %s → PENDING for -p execution (option %d, killed planning agent %s)",
                    _task.id, effective_index, agent.id,
                )
            except InvalidTransitionError as e:
                logger.warning("Failed to transition planning task %s: %s", _task.id, e)

            return {"detail": "ok", "keys_sent": 0, "prompt_type": "planning_handoff", "auto_approved": False}

        # --- Non-planning agent: normal tmux key flow ---
        if has_tmux:
            # Capture pane content BEFORE sending keys for diagnostics
            from agent_dispatcher import capture_tmux_pane, _detect_plan_prompt
            pre_content = capture_tmux_pane(pane_id)
            prompt_type = _detect_plan_prompt(pre_content) if pre_content else "unknown"
            logger.info(
                "ExitPlanMode answer for agent %s: prompt_type=%s, selected_index=%s, pre_pane:\n%s",
                agent_id, prompt_type, body.selected_index,
                (pre_content or "")[-2000:],  # last 2000 chars to avoid huge logs
            )

            # Send tmux keys FIRST — only patch DB on success (Bug 6 race fix)
            if not send_tmux_keys(pane_id, keys):
                raise HTTPException(status_code=500, detail="Failed to send keys to tmux")

            # Patch DB immediately after keys succeed — BEFORE any await.
            patched = _patch_interactive_answer(db, agent_id, body.tool_use_id, effective_index, body.type)
            if not patched:
                logger.warning("Interactive patch missed: tool_use_id=%s agent=%s", body.tool_use_id, agent_id)
            else:
                from websocket import emit_metadata_update
                await emit_metadata_update(agent_id, patched["message_id"], patched["metadata"])

            # Capture pane content AFTER sending keys for diagnostics
            await asyncio.sleep(0.5)
            post_content = capture_tmux_pane(pane_id)
            logger.info(
                "ExitPlanMode post-keys for agent %s: post_pane:\n%s",
                agent_id,
                (post_content or "")[-2000:],
            )
        else:
            prompt_type = "no-pane"

        # No tmux pane: patch DB immediately (no keys to send)
        if not has_tmux:
            patched = _patch_interactive_answer(db, agent_id, body.tool_use_id, effective_index, body.type)
            if not patched:
                logger.warning("Interactive patch missed: tool_use_id=%s agent=%s", body.tool_use_id, agent_id)
            else:
                from websocket import emit_metadata_update
                await emit_metadata_update(agent_id, patched["message_id"], patched["metadata"])

        return {"detail": "ok", "keys_sent": len(keys) if has_tmux else 0, "prompt_type": prompt_type, "auto_approved": not has_tmux}

    elif body.type == "permission_prompt":
        # Native Claude Code permission prompt — same TUI nav as AskUserQuestion
        if body.selected_index is None or body.selected_index < 0:
            raise HTTPException(status_code=400, detail="selected_index required for permission_prompt")
        if body.selected_index > MAX_INDEX:
            raise HTTPException(status_code=400, detail=f"selected_index too large (max {MAX_INDEX})")

        if has_tmux:
            keys = ["Down"] * body.selected_index + ["Enter"]
            if not send_tmux_keys(pane_id, keys):
                raise HTTPException(status_code=500, detail="Failed to send keys to tmux")
        else:
            keys = []

        patched = _patch_interactive_answer(db, agent_id, body.tool_use_id, body.selected_index, body.type)
        if not patched:
            logger.warning("Interactive patch missed: tool_use_id=%s agent=%s", body.tool_use_id, agent_id)
        else:
            from websocket import emit_metadata_update
            await emit_metadata_update(agent_id, patched["message_id"], patched["metadata"])

        return {"detail": "ok", "keys_sent": len(keys), "auto_approved": not has_tmux}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown type: {body.type}")


# ---- Escape (send Escape key to tmux) ----

_last_escape: dict[str, float] = {}  # agent_id → timestamp

@router.post("/api/agents/{agent_id}/escape")
async def send_escape_to_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Send Escape key to the agent's tmux pane to dismiss interactive prompts."""
    import time

    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.tmux_pane:
        raise HTTPException(status_code=400, detail="Agent has no tmux pane")

    # Rate limit: max 1 Escape per 2 seconds per agent
    now = time.time()
    last = _last_escape.get(agent_id, 0)
    if now - last < 2.0:
        raise HTTPException(status_code=429, detail="Escape rate limited (max 1 per 2s)")
    _last_escape[agent_id] = now

    # Clear any pending AskUserQuestion hook request (dismiss via PermissionManager)
    from permissions import PermissionManager
    pm: PermissionManager | None = getattr(request.app.state, "permission_manager", None)
    if pm:
        pending_id = pm.find_pending_by_tool(agent_id, "AskUserQuestion")
        if pending_id:
            pm.respond(pending_id, "deny", reason="Dismissed by user")
            logger.info("Dismissed pending AskUserQuestion for agent %s via escape", agent_id[:8])

    from agent_dispatcher import send_tmux_keys, verify_tmux_pane
    if not verify_tmux_pane(agent.tmux_pane):
        raise HTTPException(status_code=400, detail="Tmux pane no longer exists")

    # Send Ctrl+C (hardcoded app:interrupt in Claude Code) instead of Escape.
    # Escape is context-dependent: with queued prompts it pulls text back to
    # the input bar; in vim mode it switches to NORMAL; in tmux it has a 50ms
    # disambiguation delay.  Ctrl+C always interrupts generation reliably.
    if not send_tmux_keys(agent.tmux_pane, ["C-c"]):
        raise HTTPException(status_code=500, detail="Failed to send interrupt to tmux")
    # CC v2.1.83 restores the interrupted prompt text to its input bar.
    # Clear it so stale text doesn't linger or get accidentally re-submitted.
    send_tmux_keys(agent.tmux_pane, ["C-u"])

    # Interrupt confirmation and dispatch are handled by the sync engine:
    # CC writes "[Request interrupted by user]" to JSONL → sync imports it
    # → _stop_generating clears state → dispatch_pending_message fires.
    # We just need to clear generating state (immediate UI update) and wake sync.
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        ad._stop_generating(agent_id)
        ad.wake_sync(agent_id)

    logger.info("escape: sent C-c to agent %s pane %s, woke sync", agent_id[:8], agent.tmux_pane)
    return {"detail": "ok"}
