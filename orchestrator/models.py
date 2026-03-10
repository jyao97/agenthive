"""Database models for AgentHive."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from utils import utcnow as _utcnow


class Base(DeclarativeBase):
    pass


class AgentMode(str, enum.Enum):
    INTERVIEW = "INTERVIEW"    # Chat only — no auto-execution
    AUTO = "AUTO"              # Execute immediately


class TaskStatus(str, enum.Enum):
    INBOX = "INBOX"
    PLANNING = "PLANNING"
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    REVIEW = "REVIEW"
    MERGING = "MERGING"
    CONFLICT = "CONFLICT"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class AgentStatus(str, enum.Enum):
    STARTING = "STARTING"
    IDLE = "IDLE"
    EXECUTING = "EXECUTING"
    SYNCING = "SYNCING"       # Importing conversation from CLI session
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class MessageRole(str, enum.Enum):
    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class MessageStatus(str, enum.Enum):
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


def _new_uuid():
    return uuid.uuid4().hex[:12]


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    # New first-class fields
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_name: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0=normal, 1=high
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.INBOX, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
    )
    worktree_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    retry_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: previous failure info
    review_artifacts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: file paths, screenshots
    agent_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(10), nullable=True)
    skip_permissions: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    use_worktree: Mapped[bool] = mapped_column(Boolean, default=True)
    try_base_commit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notify_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispatch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # deprecated — kept for DB compat
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    # DEPRECATED: Legacy columns from v1 task system.
    # These are superseded by v2 fields (title, description, project_name, branch_name, etc.).
    # Kept for backward compatibility with old dispatcher and existing DB rows.
    # Plan: migrate TaskDetail.jsx to v2 API, then drop these columns.
    # See WS-6 in code quality audit for full removal plan.
    #
    # Remaining reads (as of 2026-03-09):
    #   - task.prompt: dispatcher.py:140 (push notification body fallback),
    #       worker_manager.py:115 (_build_prompt for v1 worker)
    #   - task.project: dispatcher.py (entire v1 dispatch loop — harvest, timeouts, retry,
    #       assign, recovery; ~20 refs), agent_dispatcher.py:2946,3017,3023 (fallback:
    #       project_name or task.project), main.py:1076,1492,1677 (project rename/delete)
    #   - task.mode: no reads outside model definition (Agent.mode used instead)
    #   - task.container_id: dispatcher.py (v1 worker process tracking — harvest, timeout,
    #       assign, recovery; ~12 refs)
    #   - task.branch: no reads outside model definition (branch_name used instead)
    #   - task.retries: dispatcher.py:188,194,203 (v1 auto-retry logic)
    #   - task.result_summary: dispatcher.py:120 (v1 harvest)
    #   - task.stream_log: dispatcher.py:115,173 (v1 harvest/timeout)
    #   - task.error_message: dispatcher.py (v1 harvest/timeout/retry/assign/recovery),
    #       agent_dispatcher.py (v2 task harvest — 2989,3029,3066,3098),
    #       main.py (v2 merge flow — 3093,3100,3115,3127,3143),
    #       schemas.py TaskOut (v2 API response)
    #   NOTE: error_message is actively used by BOTH v1 and v2 systems.
    # NOTE: existing DB has NOT NULL on prompt/project/mode — provide defaults for v2 inserts
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    project: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    mode: Mapped[AgentMode] = mapped_column(Enum(AgentMode), nullable=False, default=AgentMode.AUTO)
    container_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    project: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[AgentMode] = mapped_column(
        Enum(AgentMode), nullable=False, default=AgentMode.AUTO
    )
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus), nullable=False, default=AgentStatus.STARTING, index=True
    )
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    worktree: Mapped[str | None] = mapped_column(String(200), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    cli_sync: Mapped[bool] = mapped_column(Boolean, default=False)
    tmux_pane: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_message_preview: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    skip_permissions: Mapped[bool] = mapped_column(Boolean, default=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("tasks.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    is_subagent: Mapped[bool] = mapped_column(Boolean, default=False)
    claude_agent_id: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Parent → child subagent relationship (self-referential)
    subagents: Mapped[list["Agent"]] = relationship(
        "Agent",
        back_populates="parent_agent",
        foreign_keys="[Agent.parent_id]",
        viewonly=True,
        lazy="select",
    )
    parent_agent: Mapped["Agent | None"] = relationship(
        "Agent",
        back_populates="subagents",
        foreign_keys="[Agent.parent_id]",
        remote_side="[Agent.id]",
        viewonly=True,
        lazy="select",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus), nullable=False, default=MessageStatus.COMPLETED
    )
    stream_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "web" | "cli" | None
    jsonl_uuid: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)  # JSONL entry uuid for dedup
    meta_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)  # JSON string for interactive data
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Project(Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    git_remote: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=2)
    default_model: Mapped[str] = mapped_column(
        String(100), default="claude-opus-4-6"
    )
    archived: Mapped[bool] = mapped_column(default=False)
    auto_progress_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_insights: Mapped[bool] = mapped_column(Boolean, default=False)


class StarredSession(Base):
    __tablename__ = "starred_sessions"

    session_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    p256dh_key: Mapped[str] = mapped_column(String(200), nullable=False)
    auth_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ProgressInsight(Base):
    __tablename__ = "progress_insights"
    __table_args__ = (
        Index("ix_progress_project_date", "project", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Link insight to the agent that produced it (NULL for cross-agent daily summaries)
    agent_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
    )
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
