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
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class MessageRole(str, enum.Enum):
    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class MessageStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"       # sent via tmux send-keys, awaiting JSONL delivery confirmation
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
    agent_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(10), nullable=True)
    skip_permissions: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    use_worktree: Mapped[bool] = mapped_column(Boolean, default=True)
    use_tmux: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    notify_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # V1 legacy columns — kept because SQLite NOT NULL without DEFAULT.
    # Not used by any code; safe to drop once a migration rewrites the table.
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    project: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    mode: Mapped[AgentMode] = mapped_column(Enum(AgentMode), nullable=False, default=AgentMode.AUTO)
    retries: Mapped[int] = mapped_column(Integer, default=0)


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
    cli_sync: Mapped[bool] = mapped_column(Boolean, default=True)
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
    generating_msg_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    has_pending_suggestions: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def is_generating(self) -> bool:
        return self.generating_msg_id is not None

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
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispatch_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # dispatch order per agent
    tool_use_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "text" | "tool_use" | None (legacy)
    display_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # display file sequence number


class Project(Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    git_remote: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=8)
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


class AgentInsightSuggestion(Base):
    __tablename__ = "agent_insight_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    edited_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending/accepted/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
