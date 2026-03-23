"""AgentHive — FastAPI entry point."""

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

# Clear Claude Code nesting-detection vars from the orchestrator process
# so spawned agents (subprocess and tmux) don't refuse to start.
os.environ.pop("CLAUDECODE", None)
os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session

from config import CORS_ORIGINS, PROJECT_CONFIGS_PATH, CC_MODEL, VALID_MODELS
from database import SessionLocal, get_db, init_db
from log_config import setup_logging
from models import Agent, Project, Task, TaskStatus, AgentStatus
from auth import get_jwt_secret, get_password_hash, verify_token

setup_logging()
logger = logging.getLogger("orchestrator")


# ---- Registry loader ----

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

    _valid_name = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    for p in projects:
        pname = p.get("name", "")
        if not pname or not _valid_name.match(pname) or "/" in pname or "\\" in pname:
            logger.warning("Skipping project with invalid name: %r", pname)
            continue
        # Validate model name — fall back to global default if invalid
        raw_model = p.get("default_model", CC_MODEL)
        if raw_model not in VALID_MODELS:
            logger.warning(
                "Project %r has invalid default_model %r, using %s",
                pname, raw_model, CC_MODEL,
            )
            raw_model = CC_MODEL

        existing = db.get(Project, p["name"])
        if existing:
            existing.display_name = p.get("display_name", p["name"])
            existing.path = p.get("path", f'/projects/{p["name"]}')
            existing.git_remote = p.get("git_remote")
            existing.description = p.get("description")
            existing.max_concurrent = p.get("max_concurrent", 8)
            existing.default_model = raw_model
        else:
            db.add(Project(
                name=p["name"],
                display_name=p.get("display_name", p["name"]),
                path=p.get("path", f'/projects/{p["name"]}'),
                git_remote=p.get("git_remote"),
                description=p.get("description"),
                max_concurrent=p.get("max_concurrent", 8),
                default_model=raw_model,
            ))
    db.commit()
    logger.info("Loaded %d projects from registry.yaml", len(projects))


# ---- Lifespan ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    import socket

    port = int(os.environ.get("PORT", 8080))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            logger.error(
                "Port %d already in use — another instance may be running. "
                "Exiting to avoid conflicts.",
                port,
            )
            import sys
            sys.exit(1)

    logger.info("AgentHive starting up...")
    _main_event_loop = asyncio.get_event_loop()

    # Make the event loop available to routers that need it for background threads
    from routers import projects as _projects_router
    _projects_router._main_event_loop = _main_event_loop

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
    agent_dispatch_task = None
    backup_task = None
    session_cache_task = None
    try:
        from agent_dispatcher import AgentDispatcher
        from git_manager import GitManager
        from worker_manager import WorkerManager
        wm = WorkerManager()
        agent_dispatcher = AgentDispatcher(wm)
        gm = GitManager()
        from permissions import PermissionManager
        app.state.permission_manager = PermissionManager()
        app.state.agent_dispatcher = agent_dispatcher
        app.state.worker_manager = wm
        app.state.git_manager = gm
        agent_dispatch_task = asyncio.create_task(agent_dispatcher.run())
        logger.info("Dispatcher started")

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

    # Install global SessionStart hook so ALL claude processes are detected
    try:
        from routers.agents import _write_global_session_hook
        _write_global_session_hook()
    except Exception:
        logger.warning("Failed to write global session hook", exc_info=True)

    # Refresh project-level hook configs (ensures new hook types are registered)
    try:
        from routers.agents import _write_agent_hooks_config
        _db_hooks = SessionLocal()
        _project_paths = [
            p.path for p in _db_hooks.query(Project.path).distinct().all()
            if p.path and os.path.isdir(p.path)
        ]
        _db_hooks.close()
        for _pp in _project_paths:
            try:
                _write_agent_hooks_config(_pp)
            except Exception:
                logger.debug("Failed to refresh hooks for %s", _pp, exc_info=True)
        if _project_paths:
            logger.info("Refreshed hook configs for %d projects", len(_project_paths))
    except Exception:
        logger.warning("Failed to refresh project hook configs", exc_info=True)

    # Process sessions that accumulated while orchestrator was offline
    try:
        from routers.agents import _ingest_pending_sessions
        _ingest_pending_sessions()
    except Exception:
        logger.debug("Failed to ingest pending sessions", exc_info=True)

    # Clean stale unlinked session entries from previous runs
    try:
        from routers.agents import _clean_stale_unlinked
        _clean_stale_unlinked()
    except Exception:
        logger.debug("Failed to clean stale unlinked sessions", exc_info=True)

    # Recover tasks stuck in EXECUTING whose agent already stopped/errored
    try:
        from task_state import TaskStateMachine as _TSM
        _rdb = SessionLocal()
        try:
            _stuck_tasks = (
                _rdb.query(Task)
                .join(Agent, Task.agent_id == Agent.id)
                .filter(
                    Task.status == TaskStatus.EXECUTING,
                    Agent.status.in_([AgentStatus.STOPPED, AgentStatus.ERROR]),
                )
                .all()
            )
            for _st in _stuck_tasks:
                _agent = _rdb.get(Agent, _st.agent_id)
                if _agent and _agent.status == AgentStatus.ERROR:
                    _TSM.transition(_st, TaskStatus.FAILED, strict=False)
                else:
                    _TSM.transition(_st, TaskStatus.COMPLETE, strict=False)
            if _stuck_tasks:
                _rdb.commit()
                logger.info("Recovered %d stuck EXECUTING tasks at startup", len(_stuck_tasks))
        finally:
            _rdb.close()
    except Exception:
        logger.debug("Failed to recover stuck tasks", exc_info=True)

    # Start backup loop
    try:
        from backup import run_backup_loop
        backup_task = asyncio.create_task(run_backup_loop())
        logger.info("Backup loop started")
    except Exception:
        logger.exception("Failed to start backup loop")

    # Start WebSocket stale-connection pruning loop
    ws_prune_task = None
    try:
        from websocket import ws_manager

        async def _ws_prune_loop():
            while True:
                await asyncio.sleep(30)
                await ws_manager.prune_stale()

        ws_prune_task = asyncio.create_task(_ws_prune_loop())
    except Exception:
        logger.exception("Failed to start WS prune loop")

    yield

    # Shutdown
    for task in (agent_dispatch_task, backup_task, session_cache_task, ws_prune_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Background task raised during shutdown")
    if agent_dispatch_task:
        agent_dispatcher.stop()
    logger.info("AgentHive shutting down...")


# ---- App creation ----

app = FastAPI(
    title="AgentHive",
    description="Multi-instance Claude Code orchestration system",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Middleware ----

@app.middleware("http")
async def hook_request_logger(request: Request, call_next):
    """Log EVERY request to /api/hooks/* for debugging."""
    if request.url.path.startswith("/api/hooks/"):
        agent_id = request.headers.get("X-Agent-Id", "<none>")
        hook_name = request.url.path.split("/api/hooks/")[-1]
        logger.info(
            "HOOK_HTTP_IN: %s agent=%s method=%s",
            hook_name, agent_id[:12] if agent_id != "<none>" else "<none>", request.method,
        )
    return await call_next(request)


_AUTH_EXEMPT_PREFIXES = ("/api/auth/", "/api/health", "/api/test/", "/api/files/", "/api/uploads/", "/api/thumbs/", "/docs", "/openapi.json")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Reject unauthenticated requests to protected endpoints."""
    # Allow DISABLE_AUTH=1 for development/testing
    if os.environ.get("DISABLE_AUTH", "").strip() in ("1", "true", "yes"):
        return await call_next(request)

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


# ---- Voice and WebSocket ----

from voice import router as voice_router
app.include_router(voice_router)

from websocket import websocket_endpoint
app.websocket("/ws/status")(websocket_endpoint)

from voice_stream import transcribe_stream_endpoint
app.websocket("/ws/transcribe")(transcribe_stream_endpoint)


# ---- Include all routers ----

from routers.auth import router as auth_router
from routers.system import router as system_router
from routers.projects import router as projects_router
from routers.tasks import router as tasks_router
from routers.hooks import router as hooks_router
from routers.agents import router as agents_router
from routers.git import router as git_router
from routers.files import router as files_router
from routers.push import router as push_router
from routers.workers import router as workers_router
from routers.logs import router as logs_router

app.include_router(auth_router)
app.include_router(system_router)
app.include_router(projects_router)
app.include_router(tasks_router)
app.include_router(hooks_router)
app.include_router(agents_router)
app.include_router(git_router)
app.include_router(files_router)
app.include_router(push_router)
app.include_router(workers_router)
app.include_router(logs_router)
