"""Pydantic schemas for API request/response."""

import json as _json
import logging
from datetime import datetime, timezone

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from models import AgentMode, AgentStatus, MessageRole, MessageStatus, TaskStatus

logger = logging.getLogger("orchestrator.schemas")


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


# --- Task v2 schemas (first-class Task entity) ---

class TaskCreate(BaseModel):
    title: str = Field("", max_length=300)
    description: str | None = Field(None, max_length=50000)
    project_name: str | None = None
    priority: int = Field(0, ge=0, le=1)  # 0=normal, 1=high
    model: str | None = None
    effort: Literal["low", "medium", "high"] | None = None
    skip_permissions: bool = True
    sync_mode: bool = False
    use_worktree: bool = True
    notify_at: datetime | None = None
    auto_dispatch: bool = False

    @field_validator("model", mode="before")
    @classmethod
    def validate_model(cls, v):
        if v is None:
            return v
        from config import VALID_MODELS
        if v not in VALID_MODELS:
            raise ValueError(f"Invalid model: {v}. Must be one of: {', '.join(sorted(VALID_MODELS))}")
        return v


class TaskUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = None
    project_name: str | None = None
    priority: int | None = Field(None, ge=0, le=1)
    model: str | None = None
    effort: str | None = None
    status: str | None = None
    notify_at: datetime | None = None


class TaskOut(BaseModel):
    id: str
    title: str
    description: str | None = None
    project_name: str | None = None
    priority: int = 0
    status: TaskStatus
    agent_id: str | None = None
    worktree_name: str | None = None
    branch_name: str | None = None
    attempt_number: int = 1
    agent_summary: str | None = None
    rejection_reason: str | None = None
    error_message: str | None = None
    merge_agent_id: str | None = None
    model: str | None = None
    effort: str | None = None
    skip_permissions: bool = True
    sync_mode: bool = False
    use_worktree: bool = True
    try_base_commit: str | None = None
    review_artifacts: str | None = None
    notify_at: datetime | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_agent_message: str | None = None
    elapsed_seconds: int | None = None

    model_config = {"from_attributes": True}

    @field_validator("created_at", "started_at", "completed_at", "notify_at", mode="before")
    @classmethod
    def ensure_utc_task(cls, v):
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class TaskDetailOut(TaskOut):
    retry_context: str | None = None
    conversation: list["MessageOut"] = []


class TaskRejectRequest(BaseModel):
    reason: str = Field(..., min_length=1)


# --- Agent schemas ---

class AgentCreate(BaseModel):
    project: str
    prompt: str
    mode: AgentMode = AgentMode.AUTO
    model: str | None = None  # None = use project default
    effort: str | None = None  # low, medium, high
    worktree: str | None = None  # None = shared main, string = worktree name
    timeout_seconds: int = 1800
    resume_session_id: str | None = None  # Resume an existing Claude session
    sync_session: bool = False  # Import history from CLI session and live-sync
    skip_permissions: bool = True  # --dangerously-skip-permissions


class _AgentBase(BaseModel):
    """Shared fields between AgentOut and AgentBrief."""
    id: str
    project: str
    name: str
    mode: AgentMode
    status: AgentStatus
    branch: str | None = None
    worktree: str | None = None
    session_id: str | None = None
    tmux_pane: str | None = None
    model: str | None = None
    effort: str | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    created_at: datetime
    skip_permissions: bool = True
    muted: bool = False
    parent_id: str | None = None
    task_id: str | None = None
    is_subagent: bool = False
    claude_agent_id: str | None = None
    is_generating: bool = False

    model_config = {"from_attributes": True}


class AgentOut(_AgentBase):
    cli_sync: bool = False
    timeout_seconds: int = 1800
    successor_id: str | None = None
    session_size_bytes: int | None = None
    subagents: list["AgentBrief"] | None = None


class AgentBrief(_AgentBase):
    """Compact agent representation for list views."""
    pass


class MessageOut(BaseModel):
    id: str
    agent_id: str
    role: MessageRole
    content: str
    status: MessageStatus
    stream_log: str | None = None
    error_message: str | None = None
    source: str | None = None  # "web" | "cli" | None
    metadata: dict | None = Field(None, validation_alias="meta_json")
    created_at: datetime
    completed_at: datetime | None = None
    scheduled_at: datetime | None = None

    model_config = {"from_attributes": True, "populate_by_name": True}

    @field_validator("metadata", mode="before")
    @classmethod
    def parse_metadata_json(cls, v):
        """Parse JSON string from DB into a dict."""
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except (_json.JSONDecodeError, ValueError):
                logger.warning("Failed to parse metadata JSON: %s", v[:200] if len(v) > 200 else v)
                return None
        return v

    @field_validator("scheduled_at", "created_at", "completed_at", mode="before")
    @classmethod
    def ensure_utc(cls, v):
        """Ensure datetime fields carry UTC tzinfo (SQLite drops it)."""
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class PaginatedMessages(BaseModel):
    messages: list[MessageOut]
    has_more: bool


class MessageSearchResult(BaseModel):
    message_id: str
    agent_id: str
    agent_name: str
    project: str
    role: MessageRole
    content_snippet: str
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def ensure_utc_search(cls, v):
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class MessageSearchResponse(BaseModel):
    results: list[MessageSearchResult] = []
    total: int = 0


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
    auto_progress_summary: bool = False
    ai_insights: bool = False

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
