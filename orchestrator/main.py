"""AgentHive — FastAPI entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from config import AUTH_TIMEOUT_MINUTES, PROJECT_CONFIGS_PATH
from database import SessionLocal, get_db, init_db
from log_config import setup_logging
from models import (
    Agent,
    AgentStatus,
    Message,
    MessageRole,
    MessageStatus,
    AgentMode,
    Project,
    StarredSession,
)
from schemas import (
    AgentBrief,
    AgentCreate,
    AgentOut,
    AgentTaskBrief,
    AgentTaskDetail,
    HealthResponse,
    MessageOut,
    PlanReject,
    ProjectCreate,
    ProjectOut,
    ProjectWithStats,
    SendMessage,
    SessionSummary,
)
from auth import (
    create_token,
    get_jwt_secret,
    get_password_hash,
    set_password_hash,
    verify_password,
    verify_token,
)

setup_logging()
logger = logging.getLogger("orchestrator")


def _utcnow():
    return datetime.now(timezone.utc)


def _effective_task_status(msg: Message, agent: Agent) -> str:
    """Derive a user-facing task status from message + agent state."""
    if msg.status == MessageStatus.COMPLETED:
        return "COMPLETED"
    if msg.status == MessageStatus.FAILED:
        return "FAILED"
    if msg.status == MessageStatus.TIMEOUT:
        return "TIMEOUT"
    if msg.status == MessageStatus.EXECUTING:
        return "EXECUTING"
    # PENDING — derive from agent state
    if agent.status == AgentStatus.PLANNING:
        return "PLANNING"
    if agent.status == AgentStatus.PLAN_REVIEW:
        return "PLAN_REVIEW"
    if agent.status == AgentStatus.ERROR:
        return "FAILED"
    if agent.status == AgentStatus.STOPPED:
        return "CANCELLED"
    return "PENDING"


def _validate_folder_name(name: str) -> None:
    """Raise 400 if the folder name contains path traversal characters."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid folder name")


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
            existing.description = p.get("description")
            existing.max_concurrent = p.get("max_concurrent", 2)
            existing.default_model = p.get("default_model", "claude-sonnet-4-5-20250514")
        else:
            db.add(Project(
                name=p["name"],
                display_name=p.get("display_name", p["name"]),
                path=p.get("path", f'/projects/{p["name"]}'),
                git_remote=p.get("git_remote"),
                description=p.get("description"),
                max_concurrent=p.get("max_concurrent", 2),
                default_model=p.get("default_model", "claude-sonnet-4-5-20250514"),
            ))
    db.commit()
    logger.info("Loaded %d projects from registry.yaml", len(projects))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("AgentHive starting up...")
    init_db()
    logger.info("Database initialized")

    db = SessionLocal()
    try:
        load_registry(db)
    finally:
        db.close()

    # Disable Claude Code session auto-cleanup
    try:
        from session_cache import ensure_cleanup_disabled
        ensure_cleanup_disabled()
    except Exception:
        logger.exception("Failed to disable session cleanup")

    # Start dispatchers and git manager
    dispatch_task = None
    agent_dispatch_task = None
    backup_task = None
    session_cache_task = None
    try:
        from agent_dispatcher import AgentDispatcher
        from dispatcher import TaskDispatcher
        from git_manager import GitManager
        from worker_manager import WorkerManager
        wm = WorkerManager()
        dispatcher = TaskDispatcher(wm)
        agent_dispatcher = AgentDispatcher(wm)
        gm = GitManager()
        app.state.dispatcher = dispatcher
        app.state.agent_dispatcher = agent_dispatcher
        app.state.worker_manager = wm
        app.state.git_manager = gm
        dispatch_task = asyncio.create_task(dispatcher.run())
        agent_dispatch_task = asyncio.create_task(agent_dispatcher.run())
        logger.info("Dispatchers started")

        # Start session cache loop
        try:
            from session_cache import run_session_cache_loop
            session_cache_task = asyncio.create_task(
                run_session_cache_loop(agent_dispatcher.get_active_sessions)
            )
        except Exception:
            logger.exception("Failed to start session cache loop")
    except Exception:
        logger.exception("Failed to start dispatchers — running without scheduling")

    # Start backup loop
    try:
        from backup import run_backup_loop
        backup_task = asyncio.create_task(run_backup_loop())
        logger.info("Backup loop started")
    except Exception:
        logger.exception("Failed to start backup loop")

    yield

    # Shutdown
    for task in (dispatch_task, agent_dispatch_task, backup_task, session_cache_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    if dispatch_task:
        dispatcher.stop()
    if agent_dispatch_task:
        agent_dispatcher.stop()
    logger.info("AgentHive shutting down...")


app = FastAPI(
    title="AgentHive",
    description="Multi-instance Claude Code orchestration system",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Auth middleware ----

_AUTH_EXEMPT_PREFIXES = ("/api/auth/", "/api/health", "/docs", "/openapi.json")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Reject unauthenticated requests to protected endpoints."""
    path = request.url.path

    # Skip auth for exempt paths and non-API static assets
    if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)

    # Check for password — if none set, allow all requests (first-time setup)
    db = SessionLocal()
    try:
        pw_hash = get_password_hash(db)
        if pw_hash is None:
            return await call_next(request)

        # Verify bearer token
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        token = auth_header[7:]
        jwt_secret = get_jwt_secret(db)
        if not verify_token(token, jwt_secret):
            return JSONResponse({"detail": "Token expired or invalid"}, status_code=401)
    finally:
        db.close()

    return await call_next(request)


# Voice router
from voice import router as voice_router
app.include_router(voice_router)

# WebSocket
from websocket import websocket_endpoint
app.websocket("/ws/status")(websocket_endpoint)


# ---- Auth ----

@app.post("/api/auth/check")
async def auth_check(request: Request, db: Session = Depends(get_db)):
    """Check auth state — returns whether password is set and if token is valid."""
    pw_hash = get_password_hash(db)
    if pw_hash is None:
        return {"authenticated": False, "needs_setup": True}

    # Password is set — verify the bearer token if provided
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        jwt_secret = get_jwt_secret(db)
        if verify_token(token, jwt_secret):
            return {"authenticated": True, "needs_setup": False}

    return {"authenticated": False, "needs_setup": False}


@app.post("/api/auth/set-password")
async def auth_set_password(request: Request, db: Session = Depends(get_db)):
    """First-time password setup. Only works if no password has been set yet."""
    pw_hash = get_password_hash(db)
    if pw_hash is not None:
        raise HTTPException(status_code=400, detail="Password already set")

    body = await request.json()
    password = body.get("password", "")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    set_password_hash(db, password)
    jwt_secret = get_jwt_secret(db)
    token = create_token(jwt_secret)
    logger.info("Initial password set")
    return {"token": token, "expires_minutes": AUTH_TIMEOUT_MINUTES}


@app.post("/api/auth/login")
async def auth_login(request: Request, db: Session = Depends(get_db)):
    """Login with password. Returns JWT token."""
    pw_hash = get_password_hash(db)
    if pw_hash is None:
        raise HTTPException(status_code=400, detail="No password set — use /api/auth/set-password")

    body = await request.json()
    password = body.get("password", "")
    if not verify_password(password, pw_hash):
        raise HTTPException(status_code=401, detail="Wrong password")

    jwt_secret = get_jwt_secret(db)
    token = create_token(jwt_secret)
    return {"token": token, "expires_minutes": AUTH_TIMEOUT_MINUTES}


@app.post("/api/auth/change-password")
async def auth_change_password(request: Request, db: Session = Depends(get_db)):
    """Change password. Requires current password for verification."""
    pw_hash = get_password_hash(db)
    if pw_hash is None:
        raise HTTPException(status_code=400, detail="No password set")

    body = await request.json()
    current = body.get("current_password", "")
    new_pw = body.get("new_password", "")

    if not verify_password(current, pw_hash):
        raise HTTPException(status_code=401, detail="Current password is wrong")
    if len(new_pw) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters")

    set_password_hash(db, new_pw)
    jwt_secret = get_jwt_secret(db)
    token = create_token(jwt_secret)
    logger.info("Password changed")
    return {"token": token, "expires_minutes": AUTH_TIMEOUT_MINUTES}


# ---- Health ----

@app.get("/api/health", response_model=HealthResponse)
async def health(request: Request):
    """System health check — verifies DB is writable and Claude CLI is reachable."""
    result = HealthResponse(status="ok")

    # Check DB
    try:
        db = SessionLocal()
        db.execute(Agent.__table__.select().limit(1))
        db.close()
    except Exception:
        result.db = "error"
        result.status = "degraded"

    # Check Claude CLI
    wm = getattr(request.app.state, "worker_manager", None)
    if wm and wm.ping():
        result.claude_cli = "ok"
    else:
        result.claude_cli = "unavailable"
        result.status = "degraded"

    return result


@app.get("/api/system/stats")
async def system_stats():
    """System resource usage — CPU, memory, disk, and optional GPU."""
    import shutil
    import subprocess

    stats = {}

    # CPU usage (per-core load average / count → percentage)
    try:
        with open("/proc/loadavg") as f:
            load1 = float(f.read().split()[0])
        cpu_count = os.cpu_count() or 1
        stats["cpu"] = {
            "load_1m": round(load1, 2),
            "cores": cpu_count,
            "usage_pct": round(min(load1 / cpu_count * 100, 100), 1),
        }
    except Exception:
        stats["cpu"] = None

    # Memory from /proc/meminfo
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                meminfo[parts[0].rstrip(":")] = int(parts[1])  # kB
        total = meminfo.get("MemTotal", 0)
        avail = meminfo.get("MemAvailable", 0)
        used = total - avail
        stats["memory"] = {
            "total_gb": round(total / 1048576, 1),
            "used_gb": round(used / 1048576, 1),
            "usage_pct": round(used / total * 100, 1) if total else 0,
        }
    except Exception:
        stats["memory"] = None

    # Disk usage
    try:
        usage = shutil.disk_usage("/")
        stats["disk"] = {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "usage_pct": round(usage.used / usage.total * 100, 1),
        }
    except Exception:
        stats["disk"] = None

    # GPU (nvidia-smi)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpus = []
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    gpus.append({
                        "index": int(parts[0]),
                        "name": parts[1],
                        "gpu_pct": int(parts[2]),
                        "mem_used_mb": int(parts[3]),
                        "mem_total_mb": int(parts[4]),
                        "mem_pct": round(int(parts[3]) / int(parts[4]) * 100, 1) if int(parts[4]) else 0,
                        "temp_c": int(parts[5]),
                    })
            stats["gpus"] = gpus
        else:
            stats["gpus"] = None
    except Exception:
        stats["gpus"] = None

    return stats


# ---- Projects ----

@app.get("/api/projects", response_model=list[ProjectWithStats])
async def list_projects(db: Session = Depends(get_db)):
    """List all active (non-archived) projects with task and agent statistics."""
    projects = db.query(Project).filter(Project.archived == False).order_by(Project.name).all()
    results = []
    for proj in projects:
        # Task stats (derived from agent USER messages)
        task_row = (
            db.query(
                func.count(Message.id).label("total"),
                func.count(case((Message.status == MessageStatus.COMPLETED, 1))).label("completed"),
                func.count(
                    case((Message.status.in_([MessageStatus.FAILED, MessageStatus.TIMEOUT]), 1))
                ).label("failed"),
                func.count(
                    case((Message.status.in_([MessageStatus.PENDING, MessageStatus.EXECUTING]), 1))
                ).label("running"),
            )
            .join(Agent, Message.agent_id == Agent.id)
            .filter(Agent.project == proj.name, Message.role == MessageRole.USER)
            .one()
        )

        # Agent stats
        agent_row = (
            db.query(
                func.count(Agent.id).label("total"),
                func.count(
                    case((Agent.status.in_([
                        AgentStatus.IDLE, AgentStatus.EXECUTING,
                        AgentStatus.PLANNING, AgentStatus.PLAN_REVIEW,
                        AgentStatus.STARTING,
                    ]), 1))
                ).label("active"),
            )
            .filter(Agent.project == proj.name)
            .one()
        )

        last_activity = db.query(func.max(Agent.last_message_at)).filter(
            Agent.project == proj.name
        ).scalar()

        results.append(
            ProjectWithStats(
                name=proj.name,
                display_name=proj.display_name,
                path=proj.path,
                git_remote=proj.git_remote,
                description=proj.description,
                max_concurrent=proj.max_concurrent,
                default_model=proj.default_model,
                task_total=task_row.total,
                task_completed=task_row.completed,
                task_failed=task_row.failed,
                task_running=task_row.running,
                agent_total=agent_row.total,
                agent_active=agent_row.active,
                last_activity=last_activity,
            )
        )
    return results


@app.get("/api/projects/folders")
async def list_all_folders(request: Request, db: Session = Depends(get_db)):
    """List ALL folders in projects dir with activation status and stats."""
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"
    try:
        all_dirs = sorted([
            d for d in os.listdir(projects_dir)
            if os.path.isdir(os.path.join(projects_dir, d)) and not d.startswith(".")
        ])
    except FileNotFoundError:
        all_dirs = []

    db_projects = {p.name: p for p in db.query(Project).all()}

    # Check which projects have active processes
    active_projects = set()
    wm = getattr(request.app.state, "worker_manager", None)
    if wm:
        try:
            for c in wm.list_containers():
                if c.get("status") == "running" and c.get("project"):
                    active_projects.add(c["project"])
        except Exception:
            logger.warning("Failed to list containers for active project detection")

    results = []
    for dirname in all_dirs:
        proj = db_projects.get(dirname)
        active = proj is not None and not proj.archived

        agent_count = db.query(func.count(Agent.id)).filter(
            Agent.project == dirname
        ).scalar()
        last_activity = db.query(func.max(Agent.last_message_at)).filter(
            Agent.project == dirname
        ).scalar()

        entry = {
            "name": dirname,
            "display_name": proj.display_name if proj else dirname,
            "active": active,
            "container_running": dirname in active_projects,
            "agent_count": agent_count,
            "last_activity": last_activity,
            "git_remote": proj.git_remote if proj else None,
            "description": proj.description if proj else None,
        }

        # Richer stats for active projects
        if active:
            agent_active_count = (
                db.query(func.count(Agent.id))
                .filter(
                    Agent.project == dirname,
                    Agent.status.in_([
                        AgentStatus.IDLE, AgentStatus.EXECUTING,
                        AgentStatus.PLANNING, AgentStatus.PLAN_REVIEW,
                        AgentStatus.STARTING,
                    ]),
                )
                .scalar()
            )
            task_total = (
                db.query(func.count(Message.id))
                .join(Agent, Message.agent_id == Agent.id)
                .filter(Agent.project == dirname, Message.role == MessageRole.USER)
                .scalar()
            )
            entry["agent_active"] = agent_active_count
            entry["task_total"] = task_total

        results.append(entry)

    return results


@app.get("/api/projects/trash")
async def list_trash_folders():
    """List deleted project folders in .trash."""
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"
    trash_dir = os.path.join(projects_dir, ".trash")
    try:
        dirs = sorted([
            d for d in os.listdir(trash_dir)
            if os.path.isdir(os.path.join(trash_dir, d))
        ])
    except FileNotFoundError:
        dirs = []
    return [{"name": d} for d in dirs]


@app.delete("/api/projects/trash/{name}", status_code=200)
async def delete_trash_folder(name: str):
    """Permanently delete a project folder from .trash."""
    _validate_folder_name(name)
    import shutil
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"
    target = os.path.join(projects_dir, ".trash", name)
    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail=f"Trash folder '{name}' not found")
    shutil.rmtree(target)
    logger.info("Permanently deleted trash folder: %s", target)
    return {"status": "deleted", "name": name}


@app.post("/api/projects/trash/{name}/restore", status_code=200)
async def restore_trash_folder(name: str):
    """Restore a project folder from .trash back to projects dir."""
    _validate_folder_name(name)
    import shutil
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"
    src = os.path.join(projects_dir, ".trash", name)
    if not os.path.isdir(src):
        raise HTTPException(status_code=404, detail=f"Trash folder '{name}' not found")
    dst = os.path.join(projects_dir, name)
    if os.path.exists(dst):
        raise HTTPException(status_code=409, detail=f"Folder '{name}' already exists")
    shutil.move(src, dst)
    logger.info("Restored trash folder %s to %s", src, dst)
    return {"status": "restored", "name": name}


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate, request: Request, db: Session = Depends(get_db)):
    """Create or re-activate a project. Un-archives if previously archived."""
    existing = db.get(Project, body.name)
    if existing:
        if existing.archived:
            # Re-activate archived project — preserves all history
            existing.archived = False
            if body.git_url:
                existing.git_remote = body.git_url
            if body.description:
                existing.description = body.description
            db.commit()
            db.refresh(existing)
            logger.info("Project '%s' re-activated from archive", body.name)
            proj = existing
        else:
            raise HTTPException(status_code=409, detail=f"Project '{body.name}' already exists")
    else:
        from config import PROJECTS_DIR
        projects_dir = PROJECTS_DIR or "/projects"
        proj = Project(
            name=body.name,
            display_name=body.name,
            path=os.path.join(projects_dir, body.name),
            git_remote=body.git_url,
            description=body.description,
        )
        db.add(proj)
        db.commit()
        db.refresh(proj)

    # Ensure project directory exists
    wm = getattr(request.app.state, "worker_manager", None)
    if wm:
        try:
            if body.git_url:
                wm.clone_project(body.name, body.git_url)
            else:
                wm.ensure_project_dir(body.name)
        except Exception:
            logger.warning("Failed to set up project directory for %s", body.name)

        # Auto-init git repo if not already one
        project_path = wm._get_project_path(body.name)
        if os.path.isdir(project_path) and not os.path.isdir(os.path.join(project_path, ".git")):
            try:
                import subprocess
                subprocess.run(["git", "init"], cwd=project_path, check=True, capture_output=True)
                subprocess.run(["git", "add", "-A"], cwd=project_path, check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=project_path, check=True, capture_output=True)
                logger.info("Auto-initialized git repo for %s", body.name)
            except Exception:
                logger.warning("Failed to auto-init git repo for %s", body.name)

    # Append to registry.yaml
    registry_path = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
    try:
        if os.path.exists(registry_path):
            with open(registry_path) as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
        if "projects" not in data or data["projects"] is None:
            data["projects"] = []
        entry = {"name": body.name, "path": os.path.join(projects_dir, body.name)}
        if body.git_url:
            entry["git_remote"] = body.git_url
        if body.description:
            entry["description"] = body.description
        data["projects"].append(entry)
        with open(registry_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)
    except Exception:
        logger.warning("Failed to update registry.yaml for project %s", body.name)

    logger.info("Project '%s' created", body.name)
    return proj


def _remove_from_registry(name: str):
    """Remove a project entry from registry.yaml."""
    registry_path = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
    try:
        if not os.path.exists(registry_path):
            return
        with open(registry_path) as f:
            data = yaml.safe_load(f) or {}
        projects = data.get("projects") or []
        data["projects"] = [p for p in projects if p.get("name") != name]
        with open(registry_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)
    except Exception:
        logger.warning("Failed to remove %s from registry.yaml", name)


def _check_no_active_agents(name: str, db: Session):
    """Raise 409 if the project has active agents."""
    active_agents = (
        db.query(Agent)
        .filter(
            Agent.project == name,
            Agent.status.in_([
                AgentStatus.STARTING, AgentStatus.IDLE,
                AgentStatus.EXECUTING, AgentStatus.PLANNING,
                AgentStatus.PLAN_REVIEW,
            ]),
        )
        .count()
    )
    if active_agents > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot modify project with {active_agents} active agent(s)",
        )


@app.post("/api/projects/{name}/archive", status_code=200)
async def archive_project(name: str, request: Request, db: Session = Depends(get_db)):
    """Archive a project — stops agents, kills container, marks archived. Keeps all data."""
    proj = db.get(Project, name)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
    if proj.archived:
        raise HTTPException(status_code=400, detail="Project is already archived")

    # Stop all active agents for this project
    active_agents = (
        db.query(Agent)
        .filter(
            Agent.project == name,
            Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]),
        )
        .all()
    )
    for agent in active_agents:
        agent.status = AgentStatus.STOPPED
        db.add(Message(
            agent_id=agent.id,
            role=MessageRole.SYSTEM,
            content="Agent stopped — project archived",
            status=MessageStatus.COMPLETED,
        ))
    stopped_count = len(active_agents)

    # Stop all running processes for this project
    wm = getattr(request.app.state, "worker_manager", None)
    if wm:
        try:
            wm.stop_project_processes(name)
        except Exception:
            logger.warning("Failed to stop processes for project %s", name)
        proj.container_id = None

    proj.archived = True
    db.commit()
    _remove_from_registry(name)
    logger.info("Project '%s' archived (stopped %d agents)", name, stopped_count)
    return {"detail": f"Project '{name}' archived — {stopped_count} agent(s) stopped"}


@app.delete("/api/projects/{name}", status_code=200)
async def delete_project(name: str, request: Request, db: Session = Depends(get_db)):
    """Delete a project — unregisters and moves files to .trash. Works even if not registered."""
    _validate_folder_name(name)
    import shutil

    proj = db.get(Project, name)

    # If registered, clean up DB resources
    if proj:
        _check_no_active_agents(name, db)
        db.delete(proj)
        db.commit()
        _remove_from_registry(name)

    # Move files to .trash regardless of DB registration
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"
    src = os.path.join(projects_dir, name)
    if os.path.isdir(src):
        trash_dir = os.path.join(projects_dir, ".trash")
        os.makedirs(trash_dir, exist_ok=True)
        dst = os.path.join(trash_dir, name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.move(src, dst)
        logger.info("Moved %s to %s", src, dst)
    elif not proj:
        raise HTTPException(status_code=404, detail=f"Folder '{name}' not found")

    logger.info("Project '%s' deleted (moved to .trash)", name)
    return {"detail": f"Project '{name}' deleted — files moved to .trash"}


@app.get("/api/projects/{name}/agents", response_model=list[AgentBrief])
async def list_project_agents(
    name: str,
    status: AgentStatus | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List agents for a project (works for active, archived, and unregistered projects)."""
    q = db.query(Agent).filter(Agent.project == name)
    if status:
        q = q.filter(Agent.status == status)
    return q.order_by(Agent.last_message_at.desc().nulls_last(), Agent.created_at.desc()).limit(limit).all()


# ---- Sessions (from ~/.claude/history.jsonl) ----

@app.get("/api/projects/{name}/sessions", response_model=list[SessionSummary])
async def list_project_sessions(name: str, db: Session = Depends(get_db)):
    """List all past Claude conversations for a project from history.jsonl."""
    import json
    from config import CLAUDE_HISTORY_PATH, PROJECTS_DIR

    projects_dir = PROJECTS_DIR or "/projects"
    history_path = CLAUDE_HISTORY_PATH

    if not os.path.isfile(history_path):
        return []

    # Group entries by sessionId
    sessions: dict[str, list[dict]] = {}
    try:
        with open(history_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = entry.get("sessionId")
                if not sid:
                    continue
                sessions.setdefault(sid, []).append(entry)
    except Exception:
        logger.exception("Failed to read history.jsonl")
        return []

    # Filter sessions matching this project by path basename or full path
    matched: dict[str, list[dict]] = {}
    for sid, entries in sessions.items():
        project_path = entries[0].get("project", "")
        if not project_path:
            continue
        basename = os.path.basename(project_path.rstrip("/"))
        canonical = os.path.join(projects_dir, name)
        if basename == name or project_path.rstrip("/") == canonical.rstrip("/"):
            matched[sid] = entries

    # Build agent session_id lookup for linking
    linked_agents: dict[str, str] = {}
    agent_rows = (
        db.query(Agent.id, Agent.session_id)
        .filter(Agent.project == name, Agent.session_id.is_not(None))
        .all()
    )
    for aid, asid in agent_rows:
        linked_agents[asid] = aid

    # Build summaries from history.jsonl
    seen_session_ids: set[str] = set()
    results = []
    for sid, entries in matched.items():
        entries.sort(key=lambda e: e.get("timestamp", 0))
        first_msg = entries[0].get("display", "")

        # Skip sessions that were interrupted before producing useful output
        if "[Request interrupted by user]" in (first_msg or ""):
            continue

        created = entries[0].get("timestamp", 0)
        last = entries[-1].get("timestamp", 0)
        project_path = entries[0].get("project", "")

        results.append(SessionSummary(
            session_id=sid,
            first_message=first_msg,
            message_count=len(entries),
            created_at=created,
            last_activity_at=last,
            project_path=project_path,
            linked_agent_id=linked_agents.get(sid),
        ))
        seen_session_ids.add(sid)

    # Also include orchestrator agents not found in history.jsonl
    all_agents = db.query(Agent).filter(Agent.project == name).all()
    for agent in all_agents:
        # Skip agents whose session_id is already covered by history.jsonl
        if agent.session_id and agent.session_id in seen_session_ids:
            continue

        # Use agent.id as the session identifier for agents without a session_id
        sid = agent.session_id or agent.id

        # Count user messages for this agent
        msg_count = (
            db.query(func.count(Message.id))
            .filter(Message.agent_id == agent.id, Message.role == MessageRole.USER)
            .scalar()
        )
        if msg_count == 0:
            continue

        created_ms = int(agent.created_at.timestamp() * 1000) if agent.created_at else 0
        last_ms = int(agent.last_message_at.timestamp() * 1000) if agent.last_message_at else created_ms

        results.append(SessionSummary(
            session_id=sid,
            first_message=agent.name,
            message_count=msg_count,
            created_at=created_ms,
            last_activity_at=last_ms,
            project_path=os.path.join(projects_dir, name),
            linked_agent_id=agent.id,
        ))

    # Sort by most recent first
    results.sort(key=lambda s: s.last_activity_at, reverse=True)

    # Mark starred sessions
    starred_ids = set(
        row[0] for row in db.query(StarredSession.session_id)
        .filter(StarredSession.project == name)
        .all()
    )
    for s in results:
        s.starred = s.session_id in starred_ids

    return results


@app.put("/api/projects/{name}/sessions/{session_id}/star")
async def star_session(name: str, session_id: str, db: Session = Depends(get_db)):
    """Star a session."""
    existing = db.get(StarredSession, session_id)
    if not existing:
        db.add(StarredSession(session_id=session_id, project=name))
        db.commit()
    return {"starred": True}


@app.delete("/api/projects/{name}/sessions/{session_id}/star")
async def unstar_session(name: str, session_id: str, db: Session = Depends(get_db)):
    """Unstar a session."""
    existing = db.get(StarredSession, session_id)
    if existing:
        db.delete(existing)
        db.commit()
    return {"starred": False}


# ---- Tasks (agent-sourced: each USER message = one task) ----

@app.get("/api/tasks", response_model=list[AgentTaskBrief])
async def list_tasks(
    project: str | None = None,
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List tasks (each USER message is a task) with optional filters."""
    q = (
        db.query(Message, Agent)
        .join(Agent, Message.agent_id == Agent.id)
        .filter(Message.role == MessageRole.USER)
    )
    if project:
        q = q.filter(Agent.project == project)
    rows = q.order_by(Message.created_at.desc()).limit(limit * 2).all()

    tasks = []
    for msg, agent in rows:
        eff = _effective_task_status(msg, agent)
        if status and eff != status:
            continue
        tasks.append(AgentTaskBrief(
            id=msg.id,
            agent_id=agent.id,
            agent_name=agent.name,
            project=agent.project,
            mode=agent.mode,
            prompt=msg.content,
            status=eff,
            created_at=msg.created_at,
            completed_at=msg.completed_at,
        ))
        if len(tasks) >= limit:
            break
    return tasks


@app.get("/api/tasks/{task_id}", response_model=AgentTaskDetail)
async def get_task(task_id: str, db: Session = Depends(get_db)):
    """Get task detail with the conversation thread for this prompt."""
    msg = db.get(Message, task_id)
    if not msg or msg.role != MessageRole.USER:
        raise HTTPException(status_code=404, detail="Task not found")

    agent = db.get(Agent, msg.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Find the next USER message from this agent (boundary of this task's conversation)
    next_user_msg = (
        db.query(Message)
        .filter(
            Message.agent_id == msg.agent_id,
            Message.role == MessageRole.USER,
            Message.created_at > msg.created_at,
        )
        .order_by(Message.created_at.asc())
        .first()
    )

    # Get all messages in this task's conversation range
    conv_q = db.query(Message).filter(
        Message.agent_id == msg.agent_id,
        Message.created_at >= msg.created_at,
    )
    if next_user_msg:
        conv_q = conv_q.filter(Message.created_at < next_user_msg.created_at)
    conversation = conv_q.order_by(Message.created_at.asc()).all()

    eff = _effective_task_status(msg, agent)
    return AgentTaskDetail(
        id=msg.id,
        agent_id=agent.id,
        agent_name=agent.name,
        project=agent.project,
        mode=agent.mode,
        prompt=msg.content,
        status=eff,
        created_at=msg.created_at,
        completed_at=msg.completed_at,
        conversation=[MessageOut.model_validate(m, from_attributes=True) for m in conversation],
    )


# ---- Agents ----

@app.post("/api/agents", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, db: Session = Depends(get_db)):
    """Create a new agent with an initial message."""
    project = db.get(Project, body.project)
    if not project:
        raise HTTPException(status_code=400, detail=f"Project '{body.project}' not found")
    if project.archived:
        raise HTTPException(status_code=400, detail="Cannot create agents for archived projects — activate first")

    # Generate agent name from first ~50 chars of prompt
    name = body.prompt[:50].strip()
    if len(body.prompt) > 50:
        name += "..."

    agent = Agent(
        project=body.project,
        name=name,
        mode=body.mode,
        worktree=body.worktree,
        timeout_seconds=body.timeout_seconds,
        session_id=body.resume_session_id,
        last_message_preview=name,
        last_message_at=_utcnow(),
    )
    db.add(agent)
    db.flush()  # Get agent.id

    # Create the initial user message
    msg = Message(
        agent_id=agent.id,
        role=MessageRole.USER,
        content=body.prompt,
        status=MessageStatus.PENDING,
    )
    db.add(msg)

    # AUTO mode agents skip plan review
    if body.mode == AgentMode.AUTO:
        agent.plan_approved = True

    db.commit()
    db.refresh(agent)
    logger.info("Agent %s created for project %s (mode %s)", agent.id, agent.project, agent.mode.value)
    return agent


@app.get("/api/agents", response_model=list[AgentBrief])
async def list_agents(
    project: str | None = None,
    status: AgentStatus | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List agents with optional filters."""
    q = db.query(Agent)
    if project:
        q = q.filter(Agent.project == project)
    if status:
        q = q.filter(Agent.status == status)
    return (
        q.order_by(Agent.last_message_at.desc().nulls_last(), Agent.created_at.desc())
        .limit(limit)
        .all()
    )


@app.get("/api/agents/unread")
async def agents_unread_count(db: Session = Depends(get_db)):
    """Total unread message count across all agents."""
    total = db.query(func.sum(Agent.unread_count)).scalar() or 0
    return {"unread": int(total)}


@app.get("/api/agents/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, db: Session = Depends(get_db)):
    """Get full agent details."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.delete("/api/agents/{agent_id}", response_model=AgentOut)
async def stop_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Stop an agent — marks STOPPED but leaves the project container running."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == AgentStatus.STOPPED:
        raise HTTPException(status_code=400, detail="Agent is already stopped")

    agent.status = AgentStatus.STOPPED

    # Mark any EXECUTING messages as FAILED so they don't stay stuck
    executing_msgs = db.query(Message).filter(
        Message.agent_id == agent.id,
        Message.status == MessageStatus.EXECUTING,
    ).all()
    for m in executing_msgs:
        m.status = MessageStatus.FAILED
        m.error_message = "Agent stopped by user"
        m.completed_at = datetime.now(timezone.utc)

    # Add system message
    msg = Message(
        agent_id=agent.id,
        role=MessageRole.SYSTEM,
        content="Agent stopped",
        status=MessageStatus.COMPLETED,
    )
    db.add(msg)

    db.commit()
    db.refresh(agent)
    logger.info("Agent %s stopped", agent.id)
    return agent


@app.post("/api/agents/{agent_id}/resume", response_model=AgentOut)
async def resume_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Resume a stopped or errored agent — reuses existing project container."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
        raise HTTPException(status_code=400, detail="Agent is already running")

    project = db.get(Project, agent.project)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.archived:
        raise HTTPException(status_code=400, detail="Cannot resume agents for archived projects — activate first")

    wm = getattr(request.app.state, "worker_manager", None)
    if not wm:
        raise HTTPException(status_code=500, detail="Worker manager not available")

    try:
        wm.ensure_project_ready(project)
        agent.status = AgentStatus.IDLE

        msg = Message(
            agent_id=agent.id,
            role=MessageRole.SYSTEM,
            content="Agent resumed",
            status=MessageStatus.COMPLETED,
        )
        db.add(msg)
        db.commit()
        db.refresh(agent)
        logger.info("Agent %s resumed", agent.id)
        return agent
    except Exception as e:
        logger.exception("Failed to resume agent %s", agent.id)
        raise HTTPException(status_code=500, detail=f"Failed to verify project directory: {e}")


@app.get("/api/agents/{agent_id}/messages", response_model=list[MessageOut])
async def get_agent_messages(
    agent_id: str,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """Get conversation messages for an agent (oldest first). Resets unread count."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    messages = (
        db.query(Message)
        .filter(Message.agent_id == agent_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
        .all()
    )

    # Reset unread count
    if agent.unread_count > 0:
        agent.unread_count = 0
        db.commit()

    return messages


@app.post("/api/agents/{agent_id}/messages", response_model=MessageOut, status_code=201)
async def send_agent_message(
    agent_id: str,
    body: SendMessage,
    db: Session = Depends(get_db),
):
    """Send a follow-up message to an agent."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == AgentStatus.STOPPED:
        raise HTTPException(status_code=400, detail="Agent is stopped")
    if agent.status == AgentStatus.EXECUTING:
        raise HTTPException(status_code=400, detail="Agent is currently executing — wait for completion")

    msg = Message(
        agent_id=agent.id,
        role=MessageRole.USER,
        content=body.content,
        status=MessageStatus.PENDING,
    )
    db.add(msg)

    # Update agent preview
    agent.last_message_preview = body.content[:200]
    agent.last_message_at = _utcnow()

    db.commit()
    db.refresh(msg)
    logger.info("Message %s sent to agent %s", msg.id, agent.id)
    return msg


@app.put("/api/agents/{agent_id}/read")
async def mark_agent_read(agent_id: str, db: Session = Depends(get_db)):
    """Mark agent as read (reset unread count)."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.unread_count = 0
    db.commit()
    return {"detail": "ok"}


@app.put("/api/agents/{agent_id}/approve", response_model=AgentOut)
async def approve_agent_plan(agent_id: str, db: Session = Depends(get_db)):
    """Approve an agent's plan — starts execution."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status != AgentStatus.PLAN_REVIEW:
        raise HTTPException(
            status_code=400,
            detail=f"Agent is not in PLAN_REVIEW state (current: {agent.status.value})",
        )
    agent.plan_approved = True
    agent.status = AgentStatus.IDLE  # Dispatcher will pick up the pending message

    # System message
    msg = Message(
        agent_id=agent.id,
        role=MessageRole.SYSTEM,
        content="Plan approved",
        status=MessageStatus.COMPLETED,
    )
    db.add(msg)

    db.commit()
    db.refresh(agent)
    logger.info("Agent %s plan approved", agent.id)
    return agent


@app.put("/api/agents/{agent_id}/reject", response_model=AgentOut)
async def reject_agent_plan(
    agent_id: str, body: PlanReject, db: Session = Depends(get_db),
):
    """Reject an agent's plan — adds revision as a new user message."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status != AgentStatus.PLAN_REVIEW:
        raise HTTPException(
            status_code=400,
            detail=f"Agent is not in PLAN_REVIEW state (current: {agent.status.value})",
        )

    # Add rejection as system message
    sys_msg = Message(
        agent_id=agent.id,
        role=MessageRole.SYSTEM,
        content=f"Plan rejected: {body.revision_notes}",
        status=MessageStatus.COMPLETED,
    )
    db.add(sys_msg)

    # Reset plan for re-planning
    agent.plan = None
    agent.plan_approved = False
    agent.status = AgentStatus.IDLE

    # Update the original pending user message with revision notes
    pending_msg = (
        db.query(Message)
        .filter(
            Message.agent_id == agent.id,
            Message.role == MessageRole.USER,
            Message.status == MessageStatus.PENDING,
        )
        .first()
    )
    if pending_msg:
        pending_msg.content = f"{pending_msg.content}\n\n[Revision feedback]: {body.revision_notes}"

    db.commit()
    db.refresh(agent)
    logger.info("Agent %s plan rejected — re-queued", agent.id)
    return agent


# ---- Containers ----

@app.get("/api/containers")
async def list_containers(request: Request):
    """List all tracked Claude processes (backward-compatible endpoint)."""
    wm = getattr(request.app.state, "worker_manager", None)
    if not wm:
        return []
    return wm.list_containers()

@app.get("/api/processes")
async def list_processes(request: Request):
    """List running Claude processes (active agent execs)."""
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        return []
    return ad.get_active_processes()

# Legacy alias
@app.get("/api/workers")
async def list_workers(request: Request):
    return await list_containers(request)


# ---- Project worktrees ----

@app.get("/api/projects/{project_name}/worktrees")
async def list_project_worktrees(project_name: str, db: Session = Depends(get_db)):
    """Get distinct worktree names used by agents in a project."""
    rows = (
        db.query(Agent.worktree)
        .filter(Agent.project == project_name, Agent.worktree.is_not(None))
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


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


@app.get("/api/git/{project}/status")
async def git_status(project: str, request: Request, db: Session = Depends(get_db)):
    """Get git status (staged, unstaged, untracked) for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_status(project)


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
    from config import PROJECTS_DIR

    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    projects_dir = PROJECTS_DIR or "/projects"
    base_dir = os.path.join(projects_dir, project)
    full_path = os.path.normpath(os.path.join(base_dir, path))
    if not full_path.startswith(os.path.normpath(base_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    return FileResponse(full_path, media_type=media_type)


# ---- Push Notifications ----

@app.get("/api/push/vapid-public-key")
async def push_vapid_public_key():
    """Return the VAPID public key for Web Push subscription."""
    from config import VAPID_PUBLIC_KEY
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="VAPID keys not configured")
    return {"publicKey": VAPID_PUBLIC_KEY}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, db: Session = Depends(get_db)):
    """Register a push subscription (upsert by endpoint)."""
    from models import PushSubscription

    body = await request.json()
    endpoint = body.get("endpoint", "")
    keys = body.get("keys", {})
    p256dh = keys.get("p256dh", "")
    auth = keys.get("auth", "")

    if not endpoint or not p256dh or not auth:
        raise HTTPException(status_code=400, detail="Missing endpoint or keys")

    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == endpoint
    ).first()
    if existing:
        existing.p256dh_key = p256dh
        existing.auth_key = auth
    else:
        db.add(PushSubscription(
            endpoint=endpoint,
            p256dh_key=p256dh,
            auth_key=auth,
        ))
    db.commit()
    return {"status": "subscribed"}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request, db: Session = Depends(get_db)):
    """Remove a push subscription by endpoint."""
    from models import PushSubscription

    body = await request.json()
    endpoint = body.get("endpoint", "")
    if not endpoint:
        raise HTTPException(status_code=400, detail="Missing endpoint")

    db.query(PushSubscription).filter(
        PushSubscription.endpoint == endpoint
    ).delete(synchronize_session=False)
    db.commit()
    return {"status": "unsubscribed"}


# ---- Logs ----

@app.get("/api/logs")
async def get_logs(level: str = "", limit: int = 100):
    """Get recent orchestrator log lines, optionally filtered by level."""
    from log_config import get_recent_logs
    return {"lines": get_recent_logs(level=level, limit=limit)}
