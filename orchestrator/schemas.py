"""Pydantic schemas for API request/response."""

import json as _json
import logging
from datetime import datetime, timezone

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from models import AgentMode, AgentStatus, MessageRole, MessageStatus, TaskStatus

logger = logging.getLogger("orchestrator.schemas")


# --- Task schemas (first-class Task entity) ---

class TaskCreate(BaseModel):
    title: str = Field("", max_length=300)
    description: str | None = Field(None, max_length=50000)
    project_name: str | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high", "max"] | None = None
    skip_permissions: bool = True
    sync_mode: bool = False
    use_worktree: bool = True
    use_tmux: bool = True
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
    model: str | None = None
    effort: str | None = None
    status: str | None = None
    notify_at: datetime | None = None
    skip_permissions: bool | None = None
    use_worktree: bool | None = None
    use_tmux: bool | None = None
    worktree_name: str | None = Field(None, max_length=200)
    sort_order: int | None = None


class TaskOut(BaseModel):
    id: str
    title: str
    description: str | None = None
    project_name: str | None = None
    status: TaskStatus
    agent_id: str | None = None
    worktree_name: str | None = None
    branch_name: str | None = None
    attempt_number: int = 1
    agent_summary: str | None = None
    error_message: str | None = None
    model: str | None = None
    effort: str | None = None
    skip_permissions: bool = True
    sync_mode: bool = False
    use_worktree: bool = True
    use_tmux: bool = True
    retry_context: str | None = None
    sort_order: int = 0
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


class AttemptAgentOut(BaseModel):
    agent_id: str
    created_at: datetime
    status: str | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def ensure_utc_attempt(cls, v):
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

class TaskDetailOut(TaskOut):
    conversation: list["MessageOut"] = []
    attempt_agents: list[AttemptAgentOut] = []


# --- Agent schemas ---

class AgentCreate(BaseModel):
    project: str
    prompt: str
    mode: AgentMode = AgentMode.AUTO
    model: str | None = None  # None = use project default
    effort: str | None = None  # low, medium, high, max
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
    has_pending_suggestions: bool = False

    model_config = {"from_attributes": True}


class AgentOut(_AgentBase):
    cli_sync: bool = True
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
    delivered_at: datetime | None = None
    tool_use_id: str | None = None
    session_seq: int | None = None
    kind: str | None = None  # "text" | "tool_use" | None (legacy)

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

    @field_validator("scheduled_at", "created_at", "completed_at", "delivered_at", mode="before")
    @classmethod
    def ensure_utc(cls, v):
        """Ensure datetime fields carry UTC tzinfo (SQLite drops it)."""
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class DisplayEntry(BaseModel):
    id: str
    seq: int
    role: MessageRole
    kind: str | None = None
    content: str
    source: str | None = None
    status: MessageStatus
    metadata: dict | None = None
    tool_use_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    delivered_at: datetime | None = None
    session_seq: int | None = None

    @field_validator("created_at", "completed_at", "delivered_at", mode="before")
    @classmethod
    def ensure_utc_display(cls, v):
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class DisplayResponse(BaseModel):
    messages: list[DisplayEntry]
    next_offset: int
    queued: list[MessageOut]
    has_earlier: bool


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
    max_concurrent: int = 8
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

class AgentInsightSuggestionOut(BaseModel):
    id: int
    agent_id: str
    content: str
    edited_content: str | None = None
    status: str = "pending"
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("created_at", mode="before")
    @classmethod
    def ensure_utc_suggestion(cls, v):
        if v is not None and isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class HealthResponse(BaseModel):
    status: str
    service: str = "agenthive"
    db: str = "ok"
    claude_cli: str = "unknown"
