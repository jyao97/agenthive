"""Database models for CC Orchestrator."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Priority(str, enum.Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    PLAN_REVIEW = "PLAN_REVIEW"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


def _utcnow():
    return datetime.now(timezone.utc)


def _new_uuid():
    return uuid.uuid4().hex[:12]


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    project: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority), nullable=False, default=Priority.P1
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.PENDING, index=True
    )
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    container_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)


class Project(Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    git_remote: Mapped[str | None] = mapped_column(String(500), nullable=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=2)
    default_model: Mapped[str] = mapped_column(
        String(100), default="claude-sonnet-4-5-20250514"
    )


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
