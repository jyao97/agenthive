"""CC Orchestrator — FastAPI entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config import PROJECT_CONFIGS_PATH
from database import SessionLocal, get_db, init_db
from log_config import setup_logging
from models import Priority, Project, Task, TaskStatus
from schemas import HealthResponse, PlanReject, ProjectOut, TaskBrief, TaskCreate, TaskOut

setup_logging()
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

    # Start dispatcher and git manager
    dispatch_task = None
    backup_task = None
    try:
        from dispatcher import TaskDispatcher
        from git_manager import GitManager
        from worker_manager import WorkerManager
        wm = WorkerManager()
        dispatcher = TaskDispatcher(wm)
        gm = GitManager()
        app.state.dispatcher = dispatcher
        app.state.worker_manager = wm
        app.state.git_manager = gm
        dispatch_task = asyncio.create_task(dispatcher.run())
        logger.info("Dispatcher started")
    except Exception:
        logger.exception("Failed to start dispatcher — running without scheduling")

    # Start backup loop
    try:
        from backup import run_backup_loop
        backup_task = asyncio.create_task(run_backup_loop())
        logger.info("Backup loop started")
    except Exception:
        logger.exception("Failed to start backup loop")

    yield

    # Shutdown
    for task in (dispatch_task, backup_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    if dispatch_task:
        dispatcher.stop()
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

# Voice router
from voice import router as voice_router
app.include_router(voice_router)

# WebSocket
from websocket import websocket_endpoint
app.websocket("/ws/status")(websocket_endpoint)


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
async def cancel_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Cancel a pending or executing task."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise HTTPException(status_code=400, detail=f"Task is already {task.status.value}")

    # Stop the worker container if task is currently executing or planning
    if task.status in (TaskStatus.EXECUTING, TaskStatus.PLANNING) and task.container_id:
        try:
            wm = getattr(request.app.state, "worker_manager", None)
            if wm:
                wm.stop_worker(task.container_id)
        except Exception:
            logger.warning("Failed to stop container for task %s", task.id)
    task.status = TaskStatus.CANCELLED
    db.commit()
    db.refresh(task)
    logger.info("Task %s cancelled", task.id)
    return task


@app.put("/api/tasks/{task_id}/approve", response_model=TaskOut)
async def approve_plan(task_id: str, db: Session = Depends(get_db)):
    """Approve a task's plan — moves task back to PENDING for execution."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != TaskStatus.PLAN_REVIEW:
        raise HTTPException(status_code=400, detail=f"Task is not in PLAN_REVIEW state (current: {task.status.value})")
    task.plan_approved = True
    task.status = TaskStatus.PENDING
    task.container_id = None
    task.started_at = None
    db.commit()
    db.refresh(task)
    logger.info("Task %s plan approved — queued for execution", task.id)
    return task


@app.put("/api/tasks/{task_id}/reject", response_model=TaskOut)
async def reject_plan(task_id: str, body: PlanReject, db: Session = Depends(get_db)):
    """Reject a task's plan with revision notes — re-queues for re-planning."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != TaskStatus.PLAN_REVIEW:
        raise HTTPException(status_code=400, detail=f"Task is not in PLAN_REVIEW state (current: {task.status.value})")
    # Append revision notes to prompt so re-planning considers the feedback
    task.prompt = f"{task.prompt}\n\n[Revision feedback]: {body.revision_notes}"
    task.plan = None
    task.plan_approved = False
    task.status = TaskStatus.PENDING
    task.container_id = None
    task.started_at = None
    db.commit()
    db.refresh(task)
    logger.info("Task %s plan rejected — re-queued for re-planning", task.id)
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


# ---- Workers ----

@app.get("/api/workers")
async def list_workers(request: Request):
    """List all active worker containers."""
    wm = getattr(request.app.state, "worker_manager", None)
    if not wm:
        return []
    return wm.list_workers()


# ---- Git ----

@app.get("/api/git/{project}/log")
async def git_log(project: str, limit: int = 30, request: Request = None, db: Session = Depends(get_db)):
    """Get recent git commits for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_log(project, limit=limit)


@app.get("/api/git/{project}/branches")
async def git_branches(project: str, request: Request, db: Session = Depends(get_db)):
    """Get branches for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_branches(project)


@app.post("/api/git/{project}/merge/{branch}")
async def git_merge(project: str, branch: str, request: Request, db: Session = Depends(get_db)):
    """Merge a branch into the current branch for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    result = gm.merge_branch(project, branch)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)
    return result


# ---- Files ----

@app.get("/api/files/{project}/{path:path}")
async def serve_project_file(project: str, path: str, db: Session = Depends(get_db)):
    """Serve a file from a project's directory (images, videos, etc.)."""
    import mimetypes

    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    # Resolve and validate path to prevent directory traversal
    full_path = os.path.normpath(os.path.join("/projects", project, path))
    if not full_path.startswith(f"/projects/{project}/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    return FileResponse(full_path, media_type=media_type)


# ---- Logs ----

@app.get("/api/logs")
async def get_logs(level: str = "", limit: int = 100):
    """Get recent orchestrator log lines, optionally filtered by level."""
    from log_config import get_recent_logs
    return {"lines": get_recent_logs(level=level, limit=limit)}
