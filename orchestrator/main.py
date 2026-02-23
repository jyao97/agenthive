"""CC Orchestrator — FastAPI entry point."""

import logging
import os
from contextlib import asynccontextmanager

import yaml
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from config import PROJECT_CONFIGS_PATH
from database import SessionLocal, get_db, init_db
from models import Priority, Project, Task, TaskStatus
from schemas import HealthResponse, ProjectOut, TaskBrief, TaskCreate, TaskOut

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("orchestrator")


def load_registry(db: Session):
    """Load projects from registry.yaml into database."""
    registry_path = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
    if not os.path.exists(registry_path):
        logger.warning("registry.yaml not found at %s", registry_path)
        return

    with open(registry_path) as f:
        data = yaml.safe_load(f)

    projects = data.get("projects") or []
    if not projects:
        logger.info("No projects in registry.yaml")
        return

    for p in projects:
        existing = db.get(Project, p["name"])
        if existing:
            existing.display_name = p.get("display_name", p["name"])
            existing.path = p.get("path", f'/projects/{p["name"]}')
            existing.git_remote = p.get("git_remote")
            existing.max_concurrent = p.get("max_concurrent", 2)
            existing.default_model = p.get("default_model", "claude-sonnet-4-5-20250514")
        else:
            db.add(Project(
                name=p["name"],
                display_name=p.get("display_name", p["name"]),
                path=p.get("path", f'/projects/{p["name"]}'),
                git_remote=p.get("git_remote"),
                max_concurrent=p.get("max_concurrent", 2),
                default_model=p.get("default_model", "claude-sonnet-4-5-20250514"),
            ))
    db.commit()
    logger.info("Loaded %d projects from registry.yaml", len(projects))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("CC Orchestrator starting up...")
    init_db()
    logger.info("Database initialized")

    db = SessionLocal()
    try:
        load_registry(db)
    finally:
        db.close()

    # TODO: start dispatcher (Task 1.4)
    yield
    logger.info("CC Orchestrator shutting down...")


app = FastAPI(
    title="CC Orchestrator",
    description="Multi-instance Claude Code orchestration system",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Health ----

@app.get("/api/health", response_model=HealthResponse)
async def health():
    """System health check — verifies DB is writable and Docker is reachable."""
    result = HealthResponse(status="ok")

    # Check DB
    try:
        db = SessionLocal()
        db.execute(Task.__table__.select().limit(1))
        db.close()
    except Exception:
        result.db = "error"
        result.status = "degraded"

    # Check Docker
    try:
        import docker
        client = docker.from_env()
        client.ping()
        result.docker = "ok"
    except Exception:
        result.docker = "unavailable"
        result.status = "degraded"

    return result


# ---- Projects ----

@app.get("/api/projects", response_model=list[ProjectOut])
async def list_projects(db: Session = Depends(get_db)):
    """List all registered projects."""
    return db.query(Project).order_by(Project.name).all()


# ---- Tasks ----

@app.post("/api/tasks", response_model=TaskOut, status_code=201)
async def create_task(body: TaskCreate, db: Session = Depends(get_db)):
    """Create a new task."""
    # Validate project exists
    project = db.get(Project, body.project)
    if not project:
        raise HTTPException(status_code=400, detail=f"Project '{body.project}' not found")

    task = Task(
        project=body.project,
        prompt=body.prompt,
        priority=body.priority,
        timeout_seconds=body.timeout_seconds,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("Task %s created for project %s (priority %s)", task.id, task.project, task.priority.value)
    return task


@app.get("/api/tasks", response_model=list[TaskBrief])
async def list_tasks(
    project: str | None = None,
    status: TaskStatus | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List tasks with optional filters."""
    q = db.query(Task)
    if project:
        q = q.filter(Task.project == project)
    if status:
        q = q.filter(Task.status == status)
    return q.order_by(Task.created_at.desc()).limit(limit).all()


@app.get("/api/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: str, db: Session = Depends(get_db)):
    """Get full task details."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.delete("/api/tasks/{task_id}", response_model=TaskOut)
async def cancel_task(task_id: str, db: Session = Depends(get_db)):
    """Cancel a pending or executing task."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Task is already {task.status.value}")

    # TODO: if EXECUTING, stop the worker container (Task 1.3/1.4)
    task.status = TaskStatus.CANCELLED
    db.commit()
    db.refresh(task)
    logger.info("Task %s cancelled", task.id)
    return task


@app.post("/api/tasks/{task_id}/retry", response_model=TaskOut)
async def retry_task(task_id: str, db: Session = Depends(get_db)):
    """Retry a failed/timed out task."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Cannot retry task in {task.status.value} state")

    task.status = TaskStatus.PENDING
    task.retries += 1
    task.container_id = None
    task.error_message = None
    task.started_at = None
    task.completed_at = None
    db.commit()
    db.refresh(task)
    logger.info("Task %s queued for retry (#%d)", task.id, task.retries)
    return task
