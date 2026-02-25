"""Pydantic schemas for API request/response."""

from datetime import datetime

from pydantic import BaseModel, Field

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


class AgentOut(BaseModel):
    id: str
    project: str
    name: str
    mode: AgentMode
    status: AgentStatus
    branch: str | None = None
    worktree: str | None = None
    plan: str | None = None
    plan_approved: bool = False
    model: str | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    created_at: datetime
    timeout_seconds: int = 1800

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
    model: str | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: str
    agent_id: str
    role: MessageRole
    content: str
    status: MessageStatus
    stream_log: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class SendMessage(BaseModel):
    content: str = Field(..., min_length=1)


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


# --- Plan approval schemas ---

class PlanReject(BaseModel):
    """Body for rejecting a plan with revision notes."""
    revision_notes: str = Field(..., min_length=1, description="Feedback for re-planning")


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
