"""Pydantic schemas for API request/response."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from models import AgentMode, AgentStatus, MessageRole, MessageStatus


# --- Task schemas (agent-sourced: each task = a user prompt → agent response cycle) ---

class AgentTaskBrief(BaseModel):
    """A task is a user message sent to an agent, with derived status."""
    id: str
    agent_id: str
    agent_name: str
    project: str
    mode: AgentMode
    prompt: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None


class AgentTaskDetail(AgentTaskBrief):
    """Task detail with the conversation thread for this prompt."""
    conversation: list["MessageOut"] = []


# --- Agent schemas ---

class AgentCreate(BaseModel):
    project: str
    prompt: str
    mode: AgentMode = AgentMode.AUTO
    model: str | None = None  # None = use project default
    worktree: str | None = None  # None = shared main, string = worktree name
    timeout_seconds: int = 1800
    resume_session_id: str | None = None  # Resume an existing Claude session
    sync_session: bool = False  # Import history from CLI session and live-sync
    skip_permissions: bool = True  # --dangerously-skip-permissions


class AgentOut(BaseModel):
    id: str
    project: str
    name: str
    mode: AgentMode
    status: AgentStatus
    branch: str | None = None
    worktree: str | None = None
    cli_sync: bool = False
    tmux_pane: str | None = None
    model: str | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    created_at: datetime
    timeout_seconds: int = 1800
    skip_permissions: bool = True

    model_config = {"from_attributes": True}


class AgentBrief(BaseModel):
    """Compact agent representation for list views."""
    id: str
    project: str
    name: str
    mode: AgentMode
    status: AgentStatus
    branch: str | None = None
    worktree: str | None = None
    tmux_pane: str | None = None
    model: str | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    created_at: datetime
    skip_permissions: bool = True

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: str
    agent_id: str
    role: MessageRole
    content: str
    status: MessageStatus
    stream_log: str | None = None
    error_message: str | None = None
    source: str | None = None  # "web" | "cli" | None
    created_at: datetime
    completed_at: datetime | None = None
    scheduled_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_validator("scheduled_at", "created_at", "completed_at", mode="before")
    @classmethod
    def ensure_utc(cls, v):
        """Ensure datetime fields carry UTC tzinfo (SQLite drops it)."""
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class SendMessage(BaseModel):
    content: str = Field(..., min_length=1)
    queue: bool = False
    scheduled_at: str | None = None  # ISO datetime string for scheduled send


class UpdateMessage(BaseModel):
    content: str | None = None
    scheduled_at: str | None = None  # ISO datetime string, or empty string to clear


# --- Project schemas ---

class ProjectOut(BaseModel):
    name: str
    display_name: str
    path: str
    git_remote: str | None = None
    description: str | None = None
    max_concurrent: int = 2
    default_model: str = "claude-opus-4-6"
    archived: bool = False

    model_config = {"from_attributes": True}


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    git_url: str | None = None
    description: str | None = None


class ProjectWithStats(ProjectOut):
    task_total: int = 0
    task_completed: int = 0
    task_failed: int = 0
    task_running: int = 0
    agent_total: int = 0
    agent_active: int = 0
    last_activity: datetime | None = None


class ProjectRename(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    display_name: str | None = Field(None, max_length=200)


# --- Session schemas (from ~/.claude/history.jsonl) ---

class SessionSummary(BaseModel):
    session_id: str
    first_message: str
    message_count: int
    created_at: int           # Unix ms
    last_activity_at: int     # Unix ms
    project_path: str
    linked_agent_id: str | None = None
    starred: bool = False


# --- System schemas ---

class HealthResponse(BaseModel):
    status: str
    service: str = "agenthive"
    db: str = "ok"
    claude_cli: str = "unknown"
