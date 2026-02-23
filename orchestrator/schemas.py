"""Pydantic schemas for API request/response."""

from datetime import datetime

from pydantic import BaseModel, Field

from models import Priority, TaskStatus


# --- Task schemas ---

class TaskCreate(BaseModel):
    project: str
    prompt: str
    priority: Priority = Priority.P1
    timeout_seconds: int = 600


class TaskOut(BaseModel):
    id: str
    project: str
    prompt: str
    priority: Priority
    status: TaskStatus
    plan: str | None = None
    plan_approved: bool = False
    container_id: str | None = None
    branch: str | None = None
    retries: int = 0
    result_summary: str | None = None
    stream_log: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    timeout_seconds: int = 600

    model_config = {"from_attributes": True}


class TaskBrief(BaseModel):
    """Compact task representation for list views."""
    id: str
    project: str
    prompt: str
    priority: Priority
    status: TaskStatus
    retries: int = 0
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


# --- Project schemas ---

class ProjectOut(BaseModel):
    name: str
    display_name: str
    path: str
    git_remote: str | None = None
    max_concurrent: int = 2
    default_model: str = "claude-sonnet-4-5-20250514"

    model_config = {"from_attributes": True}


# --- System schemas ---

class HealthResponse(BaseModel):
    status: str
    service: str = "cc-orchestrator"
    db: str = "ok"
    docker: str = "unknown"
