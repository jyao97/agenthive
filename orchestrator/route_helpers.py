"""Shared helpers and constants used across multiple routers."""

import asyncio
import logging
import os
import subprocess as _sp

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import re

from agent_dispatcher import ACTIVE_STATUSES
from models import Agent, Project
from schemas import AgentBrief

logger = logging.getLogger("orchestrator")

# Serialize tmux agent launches so only one proceeds at a time.
tmux_launch_sem = asyncio.Semaphore(1)

# ---- Module-level constants (extracted from inline magic numbers) ----

# tmux command timeout (seconds) — used for send-keys, kill-pane, etc.
TMUX_CMD_TIMEOUT = 5

# Maximum seconds to wait for Claude TUI to start / initialize
TUI_STARTUP_TIMEOUT = 30

# Seconds to settle after TUI REPL mount before sending prompt
TUI_SETTLE_DELAY = 3

# Max file size for project browser (bytes)
BROWSE_MAX_FILE_SIZE = 512 * 1024  # 512 KB

# Max concurrent agent launches allowed in STARTING state
MAX_STARTING_AGENTS = 10

# Tmux prompt-send: max attempts and JSONL poll duration per attempt
MAX_SEND_ATTEMPTS = 5
JSONL_POLL_PER_ATTEMPT = 15  # seconds to wait for JSONL per attempt

# Pre-flight import check timeout (seconds)
IMPORT_CHECK_TIMEOUT = 15

# Anthropic API request timeout (seconds)
API_REQUEST_TIMEOUT = 10

# Env vars stripped from claude -p subprocesses to prevent false session
# rotation signals.
SUBPROCESS_STRIP_VARS = {"AHIVE_AGENT_ID", "TMUX", "TMUX_PANE"}


def subprocess_clean_env() -> dict[str, str]:
    """Return os.environ without vars that can trigger false session rotation."""
    return {k: v for k, v in os.environ.items() if k not in SUBPROCESS_STRIP_VARS}


def check_project_capacity(db, project_name: str) -> tuple[int, int]:
    """Return (active_count, max_concurrent) for a project.

    Raises HTTPException 429 if at capacity.
    """
    proj = db.get(Project, project_name)
    if not proj:
        return (0, 8)
    active = (
        db.query(func.count(Agent.id))
        .filter(Agent.project == project_name, Agent.status.in_(ACTIVE_STATUSES))
        .scalar() or 0
    )
    if active >= proj.max_concurrent:
        raise HTTPException(
            status_code=429,
            detail=f"Project '{project_name}' at capacity ({active}/{proj.max_concurrent}) — wait for an agent to finish or increase the limit",
        )
    return (active, proj.max_concurrent)


def resolve_project_path(name: str, db) -> str:
    """Return the project's absolute path. Checks DB first, then PROJECTS_DIR."""
    proj = db.get(Project, name)
    if proj:
        return proj.path
    # Fallback: project exists on disk but not registered in DB
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"
    candidate = os.path.join(projects_dir, name)
    if os.path.isdir(candidate):
        return candidate
    raise HTTPException(status_code=404, detail=f"Project '{name}' not found")


def compute_successor_id(agent_id: str, db: Session) -> str | None:
    """Return the ID of the most recent successor (non-subagent) agent, if any."""
    successor = db.query(Agent).filter(
        Agent.parent_id == agent_id,
        Agent.is_subagent == False,
    ).order_by(Agent.created_at.desc()).first()
    return successor.id if successor else None


def create_tmux_claude_session(
    session_name: str, project_path: str, claude_cmd: str,
    agent_id: str | None = None,
) -> str:
    """Create a tmux session running Claude. Returns pane_id."""
    # Kill any stale session with same name
    _sp.run(["tmux", "kill-session", "-t", session_name],
            capture_output=True, timeout=TMUX_CMD_TIMEOUT)
    # Create new detached session
    _sp.run(["tmux", "new-session", "-d", "-s", session_name, "-c", project_path],
            check=True, capture_output=True, timeout=TMUX_CMD_TIMEOUT)
    # Get pane ID
    pane_result = _sp.run(["tmux", "display-message", "-p", "-t", session_name, "#{pane_id}"],
                          capture_output=True, text=True, timeout=TMUX_CMD_TIMEOUT)
    pane_id = pane_result.stdout.strip()
    # Unset problematic env vars, export AHIVE_AGENT_ID for hooks,
    # and disable prompt suggestions so tmux send-keys Enter always
    # reaches onSubmit (avoids autocomplete intercepting Enter).
    env_setup = "unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT AGENTHIVE_MANAGED CLAUDE_CODE_OAUTH_TOKEN"
    env_setup += " && export CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false"
    if agent_id:
        env_setup += f" && export AHIVE_AGENT_ID={agent_id}"
    _sp.run(["tmux", "send-keys", "-t", pane_id, env_setup, "Enter"],
            check=True, capture_output=True, timeout=TMUX_CMD_TIMEOUT)
    # Launch Claude
    _sp.run(["tmux", "send-keys", "-t", pane_id, claude_cmd, "Enter"],
            check=True, capture_output=True, timeout=TMUX_CMD_TIMEOUT)
    return pane_id


def graceful_kill_tmux(pane_id: str, session_name: str):
    """Send Ctrl-C to interrupt Claude, then kill the pane and session."""
    try:
        _sp.run(["tmux", "send-keys", "-t", pane_id, "C-c"], capture_output=True, timeout=TMUX_CMD_TIMEOUT)
        _sp.run(["tmux", "send-keys", "-t", pane_id, "C-c"], capture_output=True, timeout=TMUX_CMD_TIMEOUT)
        _sp.run(["tmux", "kill-pane", "-t", pane_id], capture_output=True, timeout=TMUX_CMD_TIMEOUT)
    except Exception:
        logger.warning("Failed graceful tmux kill for pane %s", pane_id, exc_info=True)
    try:
        _sp.run(["tmux", "kill-session", "-t", session_name], capture_output=True, timeout=TMUX_CMD_TIMEOUT)
    except Exception:
        logger.debug("tmux kill-session %s failed (may already be dead)", session_name)


def generate_worktree_name_local(prompt: str) -> str:
    """Generate a short branch-style worktree name from the prompt (no API)."""
    words = re.sub(r"[^a-zA-Z0-9\s]", "", prompt).lower().split()
    skip = {"the", "a", "an", "to", "in", "on", "for", "and", "or", "is", "it", "of", "with", "my", "me", "i", "this", "that", "please", "can", "you", "do", "make", "let"}
    words = [w for w in words if w not in skip][:4]
    return "-".join(words) if words else "task"


def enrich_agent_briefs(rows, request) -> list[AgentBrief]:
    """Convert Agent ORM rows to AgentBrief — is_generating is derived
    from generating_msg_id via property, no runtime enrichment needed."""
    return [AgentBrief.model_validate(row) for row in rows]
