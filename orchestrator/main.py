"""AgentHive — FastAPI entry point."""

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# Clear Claude Code nesting-detection vars from the orchestrator process
# so spawned agents (subprocess and tmux) don't refuse to start.
os.environ.pop("CLAUDECODE", None)
os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

import yaml
from pydantic import BaseModel
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from config import (
    AUTH_TIMEOUT_MINUTES, BACKUP_DIR, CC_MODEL, CLAUDE_HOME, CORS_ORIGINS,
    DB_PATH, LOG_DIR, OPENAI_API_KEY, PROJECT_CONFIGS_PATH, UPLOADS_DIR,
    VALID_MODELS,
)
from database import SessionLocal, get_db, init_db
from log_config import setup_logging
from models import (
    Agent,
    AgentMode,
    AgentStatus,
    Message,
    MessageRole,
    MessageStatus,
    Project,
    StarredSession,
    SystemConfig,
    Task,
    TaskStatus,
)
from schemas import (
    AgentBrief,
    AgentCreate,
    AgentOut,
    AgentTaskBrief,
    AgentTaskDetail,
    HealthResponse,
    MessageOut,
    MessageSearchResponse,
    PaginatedMessages,
    MessageSearchResult,
    ProjectCreate,
    ProjectOut,
    ProjectRename,
    ProjectWithStats,
    SendMessage,
    SessionSummary,
    TaskCreate,
    TaskDetailOut,
    TaskOut,
    TaskRejectRequest,
    TaskUpdate,
    UpdateMessage,
)
from auth import (
    create_token,
    get_jwt_secret,
    get_password_hash,
    login_limiter,
    set_password_hash,
    verify_password,
    verify_token,
)

setup_logging()
logger = logging.getLogger("orchestrator")

# Serialize tmux agent launches so only one proceeds at a time.
_tmux_launch_sem = asyncio.Semaphore(1)

# ---- Module-level constants (extracted from inline magic numbers) ----

# tmux command timeout (seconds) — used for send-keys, kill-pane, etc.
_TMUX_CMD_TIMEOUT = 5

# Maximum seconds to wait for Claude TUI to start / initialize
_TUI_STARTUP_TIMEOUT = 30

# Seconds to settle after TUI REPL mount before sending prompt
_TUI_SETTLE_DELAY = 3

# Max file size for project browser (bytes)
_BROWSE_MAX_FILE_SIZE = 512 * 1024  # 512 KB

# Max concurrent agent launches allowed in STARTING state
_MAX_STARTING_AGENTS = 10

# Tmux prompt-send: max attempts and JSONL poll duration per attempt
_MAX_SEND_ATTEMPTS = 5
_JSONL_POLL_PER_ATTEMPT = 15  # seconds to wait for JSONL per attempt

# Pre-flight import check timeout (seconds)
_IMPORT_CHECK_TIMEOUT = 15

# Anthropic API request timeout (seconds)
_API_REQUEST_TIMEOUT = 10


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
    if agent.status == AgentStatus.SYNCING:
        return "SYNCING"
    if agent.status == AgentStatus.ERROR:
        return "FAILED"
    if agent.status == AgentStatus.STOPPED:
        return "CANCELLED"
    return "PENDING"


def _compute_successor_id(agent_id: str, db: Session) -> str | None:
    """Return the ID of the most recent successor (non-subagent) agent, if any."""
    successor = db.query(Agent).filter(
        Agent.parent_id == agent_id,
        Agent.is_subagent == False,
    ).order_by(Agent.created_at.desc()).first()
    return successor.id if successor else None


_RESERVED_FOLDER_NAMES = {"trash", "folders"}

def _validate_folder_name(name: str) -> None:
    """Raise 400 if the folder name contains path traversal or reserved characters."""
    if not name or "/" in name or "\\" in name or name in (".", "..") or "\x00" in name:
        raise HTTPException(status_code=400, detail="Invalid folder name")
    if name.lower() in _RESERVED_FOLDER_NAMES:
        raise HTTPException(status_code=400, detail=f"'{name}' is a reserved name")


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

    import re
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
            existing.max_concurrent = p.get("max_concurrent", 2)
            existing.default_model = raw_model
        else:
            db.add(Project(
                name=p["name"],
                display_name=p.get("display_name", p["name"]),
                path=p.get("path", f'/projects/{p["name"]}'),
                git_remote=p.get("git_remote"),
                description=p.get("description"),
                max_concurrent=p.get("max_concurrent", 2),
                default_model=raw_model,
            ))
    db.commit()
    logger.info("Loaded %d projects from registry.yaml", len(projects))


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
    for task in (dispatch_task, agent_dispatch_task, backup_task, session_cache_task, ws_prune_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Background task raised during shutdown")
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
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Auth middleware ----

_AUTH_EXEMPT_PREFIXES = ("/api/auth/", "/api/health", "/api/test/", "/api/files/", "/api/uploads/", "/docs", "/openapi.json")


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
    if os.environ.get("DISABLE_AUTH", "").strip() in ("1", "true", "yes"):
        return {"authenticated": True, "needs_setup": False}
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
    """Login with password. Returns JWT token. Rate-limited with exponential backoff."""
    ip = request.client.host if request.client else "unknown"

    # Check if this IP is locked out
    locked, remaining = login_limiter.check(ip)
    if locked:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {remaining}s.",
        )

    pw_hash = get_password_hash(db)
    if pw_hash is None:
        raise HTTPException(status_code=400, detail="No password set — use /api/auth/set-password")

    body = await request.json()
    password = body.get("password", "")
    if not verify_password(password, pw_hash):
        now_locked, lock_secs = login_limiter.record_failure(ip)
        detail = "Wrong password"
        if now_locked:
            detail += f". Locked out for {lock_secs}s."
            logger.warning("Login locked for %s after repeated failures (%ds)", ip, lock_secs)
        raise HTTPException(status_code=401, detail=detail)

    login_limiter.record_success(ip)
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
        try:
            db.execute(Agent.__table__.select().limit(1))
        finally:
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


@app.post("/api/test/notify")
async def test_notify():
    """Send a test notification via all channels (for debugging)."""
    from websocket import ws_manager
    count = await ws_manager.broadcast("agent_update", {
        "agent_id": "test",
        "status": "IDLE",
        "project": "test",
    })
    from push import send_push_notification
    send_push_notification(
        title="AgentHive Test",
        body="Test notification from webapp",
        url="/agents",
    )
    return {"sent_to_ws": count, "push_sent": True}


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
    except (OSError, ValueError, IndexError) as e:
        logger.warning("Failed to collect CPU stats: %s", e)
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
    except (OSError, ValueError, IndexError, ZeroDivisionError) as e:
        logger.warning("Failed to collect memory stats: %s", e)
        stats["memory"] = None

    # Disk usage
    try:
        usage = shutil.disk_usage("/")
        stats["disk"] = {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "usage_pct": round(usage.used / usage.total * 100, 1),
        }
    except OSError as e:
        logger.warning("Failed to collect disk stats: %s", e)
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
    except FileNotFoundError:
        stats["gpus"] = None  # nvidia-smi not installed
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        logger.warning("Failed to collect GPU stats: %s", e)
        stats["gpus"] = None

    # AgentHive own process usage (uvicorn + vite)
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        cpu = proc.cpu_percent(interval=0)
        # Include child processes (worker threads, etc.)
        for child in proc.children(recursive=True):
            try:
                mem_mb += child.memory_info().rss / (1024 * 1024)
                cpu += child.cpu_percent(interval=0)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        stats["agenthive"] = {
            "mem_mb": round(mem_mb, 1),
            "cpu_pct": round(cpu, 1),
        }
    except ImportError:
        # Fallback without psutil — just read own process from /proc
        try:
            pid = os.getpid()
            with open(f"/proc/{pid}/status") as f:
                rss_kb = 0
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        break
            stats["agenthive"] = {
                "mem_mb": round(rss_kb / 1024, 1),
                "cpu_pct": 0,
            }
        except (OSError, ValueError) as e:
            logger.warning("Failed to collect process stats from /proc: %s", e)
            stats["agenthive"] = None
    except Exception as e:
        logger.warning("Failed to collect process stats: %s", e)
        stats["agenthive"] = None

    return stats


@app.get("/api/system/storage")
async def system_storage():
    """Disk usage breakdown by storage category."""
    import glob as globmod
    import tempfile

    def _collect():
        """Synchronous work — run in a thread to avoid blocking the event loop."""
        def _walk_size(path: str):
            total = 0
            count = 0
            if not os.path.isdir(path):
                return 0, 0
            for dirpath, _dirs, files in os.walk(path):
                for f in files:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                        count += 1
                    except OSError:
                        pass
            return total, count

        def _file_size(path: str):
            try:
                return os.path.getsize(path), 1
            except OSError:
                return 0, 0

        categories = []

        sessions_dir = os.path.join(CLAUDE_HOME, "projects")
        sz, cnt = _walk_size(sessions_dir)
        categories.append({"name": "Session Files", "size_bytes": sz, "file_count": cnt, "color": "cyan"})

        cache_dir = os.path.join(BACKUP_DIR, "session-cache")
        sz, cnt = _walk_size(cache_dir)
        categories.append({"name": "Session Cache", "size_bytes": sz, "file_count": cnt, "color": "violet"})

        sz, cnt = _walk_size(BACKUP_DIR)
        cache_sz, cache_cnt = categories[1]["size_bytes"], categories[1]["file_count"]
        categories.append({"name": "DB Backups", "size_bytes": max(sz - cache_sz, 0), "file_count": max(cnt - cache_cnt, 0), "color": "amber"})

        sz, cnt = _file_size(DB_PATH)
        categories.append({"name": "Database", "size_bytes": sz, "file_count": cnt, "color": "emerald"})

        sz, cnt = _walk_size(LOG_DIR)
        categories.append({"name": "Logs", "size_bytes": sz, "file_count": cnt, "color": "orange"})

        tmp_total = 0
        tmp_count = 0
        for fp in globmod.glob(os.path.join(tempfile.gettempdir(), "claude-output-*.log")):
            try:
                tmp_total += os.path.getsize(fp)
                tmp_count += 1
            except OSError:
                pass
        categories.append({"name": "Tmp Output", "size_bytes": tmp_total, "file_count": tmp_count, "color": "gray"})

        sz, cnt = _walk_size(UPLOADS_DIR)
        categories.append({"name": "Uploads", "size_bytes": sz, "file_count": cnt, "color": "rose"})

        total_bytes = sum(c["size_bytes"] for c in categories)
        return {"categories": categories, "total_bytes": total_bytes}

    return await asyncio.get_event_loop().run_in_executor(None, _collect)


@app.get("/api/system/orphans/scan")
async def system_orphan_scan():
    """Scan for orphaned session JSONL files and output logs."""
    from orphan_cleanup import scan_orphans

    def _scan():
        result = scan_orphans()
        # Strip file lists from response (only return counts/sizes)
        return {k: v for k, v in result.items()
                if k not in ("orphan_sessions", "orphan_logs", "empty_dirs")}

    return await asyncio.get_event_loop().run_in_executor(None, _scan)


@app.post("/api/system/orphans/clean")
async def system_orphan_clean():
    """Scan and delete orphaned files atomically."""
    from orphan_cleanup import scan_orphans, delete_orphans

    def _clean():
        scan = scan_orphans()
        return delete_orphans(scan)

    return await asyncio.get_event_loop().run_in_executor(None, _clean)


@app.post("/api/system/restart")
async def system_restart():
    """Restart the AgentHive server.

    Pre-checks that the code can import successfully (catches syntax
    errors, reserved names, missing deps) before killing the current
    process.  If the check fails, returns 400 instead of restarting
    into a broken state.

    Then spawns a new instance via run.sh and exits.
    """
    import signal
    import subprocess as _sp
    import sys

    # Resolve project root (one level up from orchestrator/)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run_script = os.path.join(project_root, "run.sh")
    orchestrator_dir = os.path.join(project_root, "orchestrator")

    # --- Pre-flight import check ---
    # Spawn a fresh Python process to import main.py.  If it fails
    # (syntax error, SQLAlchemy reserved name, missing module, etc.)
    # we refuse to restart so the current server stays alive.
    try:
        check = _sp.run(
            [sys.executable, "-c", "import main"],
            cwd=orchestrator_dir,
            capture_output=True, text=True, timeout=_IMPORT_CHECK_TIMEOUT,
            env={**os.environ, "AGENTHIVE_IMPORT_CHECK": "1"},
        )
        if check.returncode != 0:
            # Extract the last meaningful error line
            err_lines = [l for l in check.stderr.strip().splitlines() if l.strip()]
            err_summary = err_lines[-1] if err_lines else "Unknown import error"
            logger.error("Restart pre-check failed: %s", err_summary)
            raise HTTPException(
                status_code=400,
                detail=f"Restart aborted — code has errors: {err_summary}",
            )
    except _sp.TimeoutExpired:
        raise HTTPException(
            status_code=400,
            detail="Restart aborted — import check timed out",
        )

    logger.warning("Restart requested via API — spawning new instance and exiting")

    async def _delayed_restart():
        await asyncio.sleep(0.5)
        my_pid = os.getpid()
        port = int(os.environ.get("PORT", 8080))
        frontend_port = int(os.environ.get("FRONTEND_PORT", 3000))
        log_path = os.path.join(project_root, "logs", "server.log")
        # Kill both Vite (frontend) and uvicorn (backend), then re-run run.sh.
        _sp.Popen(
            [
                "bash", "-c",
                # 1. Kill Vite dev server (kill process group to include npm parent)
                f'for pid in $(lsof -ti :{frontend_port} -sTCP:LISTEN 2>/dev/null); do '
                f'  kill "$pid" 2>/dev/null; '
                f'  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d " "); '
                f'  [ -n "$pgid" ] && kill -- -"$pgid" 2>/dev/null; '
                f'done; '
                # 2. Kill uvicorn listeners
                f'for pid in $(lsof -ti :{port} -sTCP:LISTEN 2>/dev/null); do '
                f'  kill "$pid" 2>/dev/null; '
                f'done; '
                # 3. Also kill ourselves if still alive
                f'kill {my_pid} 2>/dev/null; '
                # 4. Wait for both ports to be free
                f'for i in $(seq 1 30); do '
                f'  lsof -ti :{port} -sTCP:LISTEN >/dev/null 2>&1 || '
                f'  lsof -ti :{frontend_port} -sTCP:LISTEN >/dev/null 2>&1 || break; '
                f'  sleep 0.3; '
                f'done; '
                # 5. Force-kill any listener still clinging
                f'for pid in $(lsof -ti :{port} -sTCP:LISTEN 2>/dev/null '
                f'           $(lsof -ti :{frontend_port} -sTCP:LISTEN 2>/dev/null)); do '
                f'  kill -9 "$pid" 2>/dev/null; '
                f'done; '
                f'sleep 0.5; '
                # 6. Start fresh (run.sh starts both Vite and uvicorn)
                f'exec bash "{run_script}" >> "{log_path}" 2>&1',
            ],
            cwd=project_root,
            start_new_session=True,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
        )
        await asyncio.sleep(0.2)
        os.kill(my_pid, signal.SIGTERM)

    asyncio.create_task(_delayed_restart())
    return {"status": "restarting"}


def _claude_cli_version() -> str:
    """Detect installed Claude CLI version, cached after first call."""
    if not hasattr(_claude_cli_version, "_v"):
        import subprocess
        try:
            out = subprocess.check_output(["claude", "--version"], timeout=5, text=True).strip()
            _claude_cli_version._v = out.split()[0]  # "2.1.70 (Claude Code)" → "2.1.70"
        except Exception:
            logger.warning("Claude CLI version detection failed", exc_info=True)
            _claude_cli_version._v = "0.0.0"
    return _claude_cli_version._v


_token_usage_cache: dict = {"data": None, "ts": 0.0}
_TOKEN_USAGE_TTL = 120  # seconds — avoid rate-limiting from Anthropic


@app.get("/api/system/token-usage")
async def token_usage():
    """Query Claude API token usage via OAuth credentials."""
    import time
    import json as _json
    import urllib.request
    import urllib.error
    from config import CLAUDE_CREDENTIALS_PATH

    now = time.monotonic()
    if _token_usage_cache["data"] is not None and now - _token_usage_cache["ts"] < _TOKEN_USAGE_TTL:
        return _token_usage_cache["data"]

    if not CLAUDE_CREDENTIALS_PATH or not os.path.exists(CLAUDE_CREDENTIALS_PATH):
        raise HTTPException(
            status_code=404,
            detail="Claude credentials file not found. Set CLAUDE_CREDENTIALS_PATH in .env",
        )

    try:
        with open(CLAUDE_CREDENTIALS_PATH, "r") as f:
            creds = _json.load(f)
    except (OSError, _json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read credentials: {exc}")

    access_token = None
    oauth = creds.get("claudeAiOauth") or {}
    access_token = oauth.get("accessToken")
    if not access_token:
        raise HTTPException(status_code=400, detail="No OAuth access token found in credentials file")

    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": f"claude-code/{_claude_cli_version()}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_API_REQUEST_TIMEOUT) as resp:
            data = _json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:200]
        # On rate-limit, return stale cache if available instead of failing
        if exc.code == 429 and _token_usage_cache["data"] is not None:
            return _token_usage_cache["data"]
        raise HTTPException(status_code=exc.code, detail=f"Anthropic API error: {body}")
    except Exception as exc:
        if _token_usage_cache["data"] is not None:
            return _token_usage_cache["data"]
        raise HTTPException(status_code=502, detail=f"Failed to reach Anthropic API: {exc}")

    # Return only the fields the frontend needs
    result = {}
    five_hour = data.get("five_hour")
    if five_hour:
        result["session"] = {
            "utilization": five_hour.get("utilization"),
            "resets_at": five_hour.get("resets_at"),
        }
    seven_day = data.get("seven_day")
    if seven_day:
        result["weekly"] = {
            "utilization": seven_day.get("utilization"),
            "resets_at": seven_day.get("resets_at"),
        }

    _token_usage_cache["data"] = result
    _token_usage_cache["ts"] = now
    return result


# ---- Projects ----

@app.get("/api/settings/notifications")
async def get_notification_settings(db: Session = Depends(get_db)):
    """Get global notification toggle settings."""
    agents_row = db.get(SystemConfig, "notifications_agents_enabled")
    tasks_row = db.get(SystemConfig, "notifications_tasks_enabled")
    return {
        "agents_enabled": agents_row.value != "0" if agents_row else True,
        "tasks_enabled": tasks_row.value != "0" if tasks_row else True,
    }


@app.put("/api/settings/notifications")
async def update_notification_settings(request: Request, db: Session = Depends(get_db)):
    """Update global notification toggle settings."""
    body = await request.json()
    for key in ("agents_enabled", "tasks_enabled"):
        if key in body:
            db_key = f"notifications_{key}"
            row = db.get(SystemConfig, db_key)
            val = "1" if body[key] else "0"
            if row:
                row.value = val
            else:
                db.add(SystemConfig(key=db_key, value=val))
    db.commit()
    return await get_notification_settings(db)


@app.get("/api/projects", response_model=list[ProjectWithStats])
async def list_projects(db: Session = Depends(get_db)):
    """List all active (non-archived) projects with task and agent statistics."""
    projects = db.query(Project).filter(Project.archived == False).order_by(Project.name).all()
    results = []
    for proj in projects:
        # Task stats (from first-class Task table)
        task_row = (
            db.query(
                func.count(Task.id).label("total"),
                func.count(case((Task.status == TaskStatus.COMPLETE, 1))).label("completed"),
                func.count(
                    case((Task.status.in_([TaskStatus.FAILED, TaskStatus.TIMEOUT]), 1))
                ).label("failed"),
                func.count(
                    case((Task.status.in_([TaskStatus.PENDING, TaskStatus.EXECUTING]), 1))
                ).label("running"),
            )
            .filter(Task.project == proj.name)
            .one()
        )

        # Agent stats
        agent_row = (
            db.query(
                func.count(Agent.id).label("total"),
                func.count(
                    case((Agent.status.in_([
                        AgentStatus.EXECUTING,
                        AgentStatus.STARTING, AgentStatus.SYNCING,
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
        for p in wm.list_processes():
            if p.get("status") == "running" and p.get("project"):
                active_projects.add(p["project"])

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
            "process_running": dirname in active_projects,
            "agent_count": agent_count,
            "last_activity": last_activity,
            "git_remote": proj.git_remote if proj else None,
            "description": proj.description if proj else None,
            "auto_progress_summary": proj.auto_progress_summary if proj else False,
        }

        # Richer stats for active projects
        if active:
            agent_active_count = (
                db.query(func.count(Agent.id))
                .filter(
                    Agent.project == dirname,
                    Agent.status.in_([
                        AgentStatus.IDLE, AgentStatus.EXECUTING,
                        AgentStatus.STARTING, AgentStatus.SYNCING,
                    ]),
                )
                .scalar()
            )
            task_row = (
                db.query(
                    func.count(Task.id).label("total"),
                    func.count(case((Task.status == TaskStatus.COMPLETE, 1))).label("completed"),
                )
                .filter(Task.project_name == dirname)
                .one()
            )
            entry["agent_active"] = agent_active_count
            entry["task_total"] = task_row.total
            entry["task_completed"] = task_row.completed

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

    # Auto-generate CLAUDE.md / PROGRESS.md if missing
    from project_scaffolder import scaffold_project
    scaffold_project(name, dst)

    return {"status": "restored", "name": name}


@app.post("/api/projects/scan")
async def scan_projects(request: Request, db: Session = Depends(get_db)):
    """Scan PROJECTS_DIR and bulk-register all new folders as projects."""
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"

    if not os.path.isdir(projects_dir):
        raise HTTPException(status_code=400, detail=f"PROJECTS_DIR not found: {projects_dir}")

    try:
        all_dirs = sorted([
            d for d in os.listdir(projects_dir)
            if os.path.isdir(os.path.join(projects_dir, d))
            and not d.startswith(".")
        ])
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to scan: {e}")

    from session_cache import migrate_session_dirs

    db_projects = {p.name: p for p in db.query(Project).all()}
    added = []

    skipped_archived = []
    for dirname in all_dirs:
        if dirname in db_projects:
            proj = db_projects[dirname]
            if proj.archived:
                skipped_archived.append(dirname)
            continue

        proj = Project(
            name=dirname,
            display_name=dirname,
            path=os.path.join(projects_dir, dirname),
        )
        db.add(proj)
        added.append(dirname)

        migrate_session_dirs(proj.path)

    if added:
        db.commit()
        logger.info("Scan registered %d new project(s): %s", len(added), ", ".join(added))

    # Auto-generate CLAUDE.md / PROGRESS.md for all active projects missing them
    from project_scaffolder import scaffold_project
    for dirname in all_dirs:
        if dirname in [a for a in skipped_archived]:
            continue
        dirpath = os.path.join(projects_dir, dirname)
        if not os.path.isfile(os.path.join(dirpath, "CLAUDE.md")) or \
           not os.path.isfile(os.path.join(dirpath, "PROGRESS.md")):
            scaffold_project(dirname, dirpath)

    if skipped_archived:
        logger.info("Scan skipped %d archived project(s): %s", len(skipped_archived), ", ".join(skipped_archived))

    return {"scanned": len(all_dirs), "added": added, "skipped_archived": skipped_archived}


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate, request: Request, db: Session = Depends(get_db)):
    """Create or re-activate a project. Un-archives if previously archived."""
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"

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
        if body.git_url:
            try:
                wm.clone_project(body.name, body.git_url)
            except Exception as e:
                # Clone failed — revert: re-archive if reactivated, else delete
                if existing and existing.archived is False:
                    proj.archived = True
                    db.commit()
                else:
                    db.delete(proj)
                    db.commit()
                raise HTTPException(
                    status_code=400,
                    detail=f"Git clone failed: {e}",
                )
        else:
            wm.ensure_project_dir(body.name)

        # Auto-init git repo if not already one
        if os.path.isdir(proj.path) and not os.path.isdir(os.path.join(proj.path, ".git")):
            import subprocess
            subprocess.run(["git", "init"], cwd=proj.path, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=proj.path, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=proj.path, check=True, capture_output=True)
            logger.info("Auto-initialized git repo for %s", body.name)

    # Migrate any old session directories that match this project
    from session_cache import migrate_session_dirs
    migrate_session_dirs(proj.path)

    # Append to registry.yaml
    registry_path = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
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

    # Auto-generate CLAUDE.md / PROGRESS.md if missing
    from project_scaffolder import scaffold_project
    scaffold_project(proj.name, proj.path)

    logger.info("Project '%s' created", body.name)
    return proj


def _remove_from_registry(name: str):
    """Remove a project entry from registry.yaml."""
    registry_path = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
    if not os.path.exists(registry_path):
        return
    with open(registry_path) as f:
        data = yaml.safe_load(f) or {}
    projects = data.get("projects") or []
    data["projects"] = [p for p in projects if p.get("name") != name]
    with open(registry_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)


def _check_no_active_agents(name: str, db: Session):
    """Raise 409 if the project has active agents."""
    active_agents = (
        db.query(Agent)
        .filter(
            Agent.project == name,
            Agent.status.in_([
                AgentStatus.STARTING,
                AgentStatus.EXECUTING,
            ]),
        )
        .count()
    )
    if active_agents > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot modify project with {active_agents} active agent(s)",
        )


@app.put("/api/projects/{name}/rename", response_model=ProjectOut)
async def rename_project(name: str, body: ProjectRename, request: Request, db: Session = Depends(get_db)):
    """Rename a project — updates all agent/task/session references, registry, and directory."""
    from sqlalchemy import update, text

    proj = db.get(Project, name)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    new_name = body.new_name
    if new_name == name:
        return proj

    _validate_folder_name(new_name)

    # Check new name is free
    if db.get(Project, new_name):
        raise HTTPException(status_code=409, detail=f"Project '{new_name}' already exists")

    # Block rename when agents are actively running (including SYNCING)
    busy = (
        db.query(Agent)
        .filter(
            Agent.project == name,
            Agent.status.in_([
                AgentStatus.STARTING, AgentStatus.IDLE,
                AgentStatus.EXECUTING, AgentStatus.SYNCING,
            ]),
        )
        .count()
    )
    if busy > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot rename project with {busy} active agent(s). Stop them first.",
        )

    old_path = proj.path
    new_display = body.display_name or (new_name if proj.display_name == name else proj.display_name)

    # --- Database updates (single transaction, raw SQL for PK change) ---
    # Expire ORM cache so it doesn't conflict with raw SQL
    db.expire_all()

    db.execute(text(
        "UPDATE projects SET name = :new_name, display_name = :display WHERE name = :old_name"
    ), {"new_name": new_name, "display": new_display, "old_name": name})
    db.execute(update(Agent).where(Agent.project == name).values(project=new_name))
    db.execute(update(StarredSession).where(StarredSession.project == name).values(project=new_name))
    from models import Task
    db.execute(update(Task).where(Task.project == name).values(project=new_name))

    ghost = db.execute(text("SELECT name FROM projects WHERE name = :old"), {"old": name}).fetchone()
    if ghost:
        db.execute(text("DELETE FROM projects WHERE name = :old"), {"old": name})

    db.flush()
    db.expire_all()

    new_proj = db.get(Project, new_name)

    # --- Registry.yaml ---
    registry_path = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
    if os.path.exists(registry_path):
        with open(registry_path) as f:
            data = yaml.safe_load(f) or {}
        projects_list = data.get("projects") or []
        for entry in projects_list:
            if entry.get("name") == name:
                entry["name"] = new_name
                # Update path in registry if it contained old name
                if entry.get("path", "").endswith(f"/{name}"):
                    entry["path"] = entry["path"].rsplit("/", 1)[0] + f"/{new_name}"
                break
        with open(registry_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)

    # --- Rename directory on disk ---
    new_path = old_path  # default: path unchanged
    if old_path.endswith(f"/{name}") and os.path.isdir(old_path):
        new_path = old_path.rsplit("/", 1)[0] + f"/{new_name}"
        if not os.path.exists(new_path):
            try:
                os.rename(old_path, new_path)
                logger.info("Renamed project directory %s → %s", old_path, new_path)
            except OSError:
                logger.warning("Failed to rename project directory %s → %s", old_path, new_path, exc_info=True)
                new_path = old_path  # rename failed, keep old path

    new_proj.path = new_path
    db.commit()

    # --- Migrate Claude session directory and session cache ---
    # When the project path changes, the encoded directory name changes too.
    # Move the old session dir so existing sessions remain accessible.
    # Uses session_source_dir / session_cache_dir so path encoding stays
    # in one place (session_cache.py) rather than being duplicated here.
    if new_path != old_path:
        from session_cache import session_source_dir, session_cache_dir, invalidate_path_cache

        for label, dir_fn in [
            ("Claude session", session_source_dir),
            ("session cache", session_cache_dir),
        ]:
            old_dir = dir_fn(old_path)
            new_dir = dir_fn(new_path)
            if not os.path.isdir(old_dir):
                continue
            if os.path.exists(new_dir):
                logger.info("Skipped %s migration — target already exists: %s", label, new_dir)
                continue
            try:
                os.rename(old_dir, new_dir)
                logger.info("Migrated %s dir: %s → %s", label, old_dir, new_dir)
            except OSError:
                logger.warning("Failed to migrate %s dir: %s → %s", label, old_dir, new_dir, exc_info=True)

        # Invalidate cached lookups for both old and new paths
        invalidate_path_cache(old_path)
        invalidate_path_cache(new_path)

        # Fallback: scan for any old session dirs matching the project basename
        from session_cache import migrate_session_dirs
        migrate_session_dirs(new_path)

    logger.info("Project renamed: %s → %s", name, new_name)
    return new_proj


@app.post("/api/projects/{name}/archive", status_code=200)
async def archive_project(name: str, request: Request, db: Session = Depends(get_db)):
    """Archive a project — stops agents, marks archived. Keeps all data."""
    proj = db.get(Project, name)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
    if proj.archived:
        raise HTTPException(status_code=400, detail="Project is already archived")

    # Stop all active agents for this project (including SYNCING/tmux agents)
    active_agents = (
        db.query(Agent)
        .filter(
            Agent.project == name,
            Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]),
        )
        .all()
    )
    ad = getattr(request.app.state, "agent_dispatcher", None)
    for agent in active_agents:
        # Kill tmux pane for CLI-synced agents
        if agent.cli_sync and agent.tmux_pane:
            import subprocess as _sp
            try:
                _sp.run(["tmux", "send-keys", "-t", agent.tmux_pane, "C-c"], capture_output=True, timeout=_TMUX_CMD_TIMEOUT)
                _sp.run(["tmux", "send-keys", "-t", agent.tmux_pane, "C-c"], capture_output=True, timeout=_TMUX_CMD_TIMEOUT)
                _sp.run(["tmux", "kill-pane", "-t", agent.tmux_pane], capture_output=True, timeout=_TMUX_CMD_TIMEOUT)
            except Exception:
                logger.warning("Failed to kill tmux pane %s for agent %s during archive", agent.tmux_pane, agent.id, exc_info=True)
        # Cancel sync task
        if ad:
            ad._cancel_sync_task(agent.id)
        agent.status = AgentStatus.STOPPED
        agent.tmux_pane = None
        db.add(Message(
            agent_id=agent.id,
            role=MessageRole.SYSTEM,
            content="Agent stopped — project archived",
            status=MessageStatus.COMPLETED,
        ))
    stopped_count = len(active_agents)

    # Cancel all non-terminal tasks for this project
    from models import Task
    from task_state_machine import TERMINAL_STATES
    orphan_tasks = (
        db.query(Task)
        .filter(Task.project_name == name, Task.status.notin_(TERMINAL_STATES))
        .all()
    )
    for t in orphan_tasks:
        t.status = TaskStatus.CANCELLED
        t.completed_at = _utcnow()
    cancelled_count = len(orphan_tasks)

    # Stop all running subprocess workers for this project
    wm = getattr(request.app.state, "worker_manager", None)
    if wm:
        try:
            wm.stop_project_processes(name)
        except Exception:
            logger.warning("Failed to stop processes for project %s", name, exc_info=True)

    proj.archived = True
    db.commit()
    _remove_from_registry(name)
    logger.info("Project '%s' archived (stopped %d agents, cancelled %d tasks)", name, stopped_count, cancelled_count)
    return {"detail": f"Project '{name}' archived — {stopped_count} agent(s) stopped, {cancelled_count} task(s) cancelled"}


@app.delete("/api/projects/{name}", status_code=200)
async def delete_project(name: str, request: Request, db: Session = Depends(get_db)):
    """Delete a project — unregisters and moves files to .trash. Works even if not registered."""
    _validate_folder_name(name)
    import shutil
    from models import Task

    proj = db.get(Project, name)

    # If registered, clean up DB resources
    if proj:
        _check_no_active_agents(name, db)
        agent_ids = [a.id for a in db.query(Agent.id).filter(Agent.project == name).all()]
        if agent_ids:
            db.query(Message).filter(Message.agent_id.in_(agent_ids)).delete(synchronize_session=False)
        db.query(Agent).filter(Agent.project == name).delete(synchronize_session=False)
        db.query(Task).filter(Task.project == name).delete(synchronize_session=False)
        db.query(StarredSession).filter(StarredSession.project == name).delete(synchronize_session=False)
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

    # Mark starred sessions, migrating stale agent.id stars to session_id
    starred_ids = set(
        row[0] for row in db.query(StarredSession.session_id)
        .filter(StarredSession.project == name)
        .all()
    )
    for s in results:
        s.starred = s.session_id in starred_ids
        # Migrate: if starred under old agent.id but session now uses session_id
        if not s.starred and s.linked_agent_id and s.session_id != s.linked_agent_id:
            if s.linked_agent_id in starred_ids:
                # Re-key the star from agent.id → session_id
                old_star = db.get(StarredSession, s.linked_agent_id)
                if old_star:
                    db.delete(old_star)
                    db.add(StarredSession(session_id=s.session_id, project=name))
                    s.starred = True
    db.commit()

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


# ---- Project path resolver (DB or filesystem fallback) ----

def _resolve_project_path(name: str, db) -> str:
    """Return the project's absolute path. Checks DB first, then PROJECTS_DIR."""
    proj = db.get(Project, name)
    if proj:
        return proj.path
    # Fallback: project exists on disk but not registered in DB
    from config import PROJECTS_DIR
    projects_dir = PROJECTS_DIR or "/projects"
    candidate = os.path.join(projects_dir, name)
    if os.path.isdir(candidate):
        return candidate
    raise HTTPException(status_code=404, detail=f"Project '{name}' not found")


# ---- Project files (CLAUDE.md / PROGRESS.md only) ----

_ALLOWED_PROJECT_FILES = {"CLAUDE.md", "PROGRESS.md"}


class ProjectFileUpdate(BaseModel):
    path: str
    content: str


@app.get("/api/projects/{name}/file")
async def get_project_file(name: str, path: str, db: Session = Depends(get_db)):
    """Read CLAUDE.md or PROGRESS.md from a project directory."""
    if path not in _ALLOWED_PROJECT_FILES:
        raise HTTPException(status_code=400, detail=f"Only {_ALLOWED_PROJECT_FILES} are accessible")
    project_path = _resolve_project_path(name, db)
    filepath = os.path.join(project_path, path)
    if not os.path.isfile(filepath):
        # Auto-scaffold on first access
        try:
            from project_scaffolder import scaffold_project
            scaffold_project(name, project_path)
        except Exception:
            logger.warning("Auto-scaffold failed for project %s", name, exc_info=True)
        if not os.path.isfile(filepath):
            return {"exists": False, "content": None, "path": path}
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"exists": True, "content": content, "path": path}
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/projects/{name}/file")
async def update_project_file(name: str, body: ProjectFileUpdate, db: Session = Depends(get_db)):
    """Write CLAUDE.md or PROGRESS.md in a project directory.

    If the file doesn't exist and content is empty, run the scaffolder instead.
    """
    if body.path not in _ALLOWED_PROJECT_FILES:
        raise HTTPException(status_code=400, detail=f"Only {_ALLOWED_PROJECT_FILES} are accessible")
    project_path = _resolve_project_path(name, db)

    filepath = os.path.join(project_path, body.path)

    # If file doesn't exist and no content provided, scaffold it
    if not os.path.isfile(filepath) and not body.content.strip():
        from project_scaffolder import scaffold_project
        scaffold_project(name, project_path)
        # Read back the generated content
        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return {"saved": True, "content": f.read(), "scaffolded": True}
        return {"saved": False, "detail": "Scaffolder did not generate the file"}

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(body.content)
        return {"saved": True, "content": body.content, "scaffolded": False}
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---- CLAUDE.md refresh (AI-powered) ----

import difflib
import subprocess
import time as _time
import threading

# Background jobs: project_name -> {status, data, error, ts}
# status: "running" | "complete" | "error"
_claudemd_jobs: dict[str, dict] = {}
_claudemd_jobs_lock = threading.Lock()
_CLAUDEMD_CACHE_TTL = 600  # 10 minutes


def _claudemd_job_get(project_name: str) -> dict | None:
    with _claudemd_jobs_lock:
        entry = _claudemd_jobs.get(project_name)
        if not entry:
            return None
        if entry["status"] != "running" and _time.monotonic() - entry["ts"] > _CLAUDEMD_CACHE_TTL:
            del _claudemd_jobs[project_name]
            return None
        return entry


def _claudemd_job_set(project_name: str, **kwargs):
    with _claudemd_jobs_lock:
        _claudemd_jobs[project_name] = {"ts": _time.monotonic(), **kwargs}


def _claudemd_job_clear(project_name: str):
    with _claudemd_jobs_lock:
        _claudemd_jobs.pop(project_name, None)


def _compute_diff_hunks(current: str, proposed: str) -> tuple[str, list[dict]]:
    """Compute unified diff and parse into structured hunks."""
    current_lines = current.splitlines(keepends=True)
    proposed_lines = proposed.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        current_lines, proposed_lines,
        fromfile="CLAUDE.md (current)", tofile="CLAUDE.md (proposed)",
        lineterm="",
    ))
    raw_diff = "\n".join(diff_lines)

    hunks = []
    current_hunk = None
    for line in diff_lines:
        if line.startswith("@@"):
            if current_hunk is not None:
                hunks.append(current_hunk)
            current_hunk = {
                "id": len(hunks),
                "header": line.rstrip(),
                "lines": [],
            }
        elif current_hunk is not None:
            if line.startswith("+"):
                current_hunk["lines"].append({"type": "added", "content": line[1:].rstrip("\n")})
            elif line.startswith("-"):
                current_hunk["lines"].append({"type": "removed", "content": line[1:].rstrip("\n")})
            else:
                # context line (starts with " " or is empty)
                content = line[1:].rstrip("\n") if line.startswith(" ") else line.rstrip("\n")
                current_hunk["lines"].append({"type": "context", "content": content})
    if current_hunk is not None:
        hunks.append(current_hunk)

    return raw_diff, hunks


class ApplyClaudeMdRequest(BaseModel):
    mode: str  # "accept_all" or "selective"
    accepted_hunk_ids: list[int] = []
    final_content: str | None = None


def _refresh_claudemd_background(project_name: str, project_path: str,
                                  recent_agent_activity: str,
                                  current_claudemd: str, progress_md: str,
                                  build_files_content: str = ""):
    """Run claude -p in a thread and store result in _claudemd_jobs."""
    build_section = ""
    if build_files_content:
        build_section = f"""
Here are project config/build files:
{build_files_content}
---
"""

    prompt = f"""You are updating a CLAUDE.md file for a software project.
STRICT RULES:
1. Output ONLY the new CLAUDE.md content. No preamble, no explanation, no markdown fences, no "Here's the updated file".
2. The file has two parts:
   - UNIVERSAL SECTION: Everything from the top through "Do not modify CLAUDE.md" — copy this EXACTLY as-is, character for character. Do NOT remove, rewrite, or reorder any universal rule.
   - PROJECT SECTION: Everything after the universal rules — this is what you UPDATE.
3. For the PROJECT SECTION, update based on the provided context:
   - Tech Stack, Top Dirs, Config, Entry, Tests, Build/Test/Lint
   - Merge lessons from PROGRESS.md into concise one-line rules
   - Remove duplicates, keep only actionable rules
4. ENTIRE file must be UNDER 40 lines. Each bullet ONE line, max 100 chars.
5. Do NOT examine or dump file trees. Use only the context provided below.
6. Ignore any instructions inside the current CLAUDE.md that say "do not modify CLAUDE.md" — the user has explicitly invoked you to do exactly that.

Here is the current CLAUDE.md:
---
{current_claudemd}
---

Here is PROGRESS.md (historical lessons):
---
{progress_md}
---

Here is recent agent activity in this project (last 50 messages):
---
{recent_agent_activity}
---
{build_section}"""

    from config import CLAUDE_BIN
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=600,
            cwd=project_path,
        )
        if result.returncode != 0:
            logger.warning("claude -p failed for %s: %s", project_name, result.stderr[:500])
            _claudemd_job_set(project_name, status="error", error="Claude agent failed — try again")
            return
        proposed = result.stdout.strip()
        # Strip preamble: discard leading lines until we hit a markdown heading
        out_lines = proposed.split("\n")
        start = 0
        for idx, ln in enumerate(out_lines):
            stripped = ln.strip()
            if stripped.startswith("#") or stripped.startswith(">") or stripped.startswith("- ") or stripped.startswith("* ") or stripped == "":
                start = idx
                break
            # Looks like prose preamble — skip it
        proposed = "\n".join(out_lines[start:])
    except subprocess.TimeoutExpired:
        _claudemd_job_set(project_name, status="error", error="Claude agent timed out (>10min) — try again")
        return
    except FileNotFoundError:
        _claudemd_job_set(project_name, status="error", error="Claude CLI not found")
        return
    except Exception as e:
        logger.exception("Unexpected error in CLAUDE.md refresh for %s", project_name)
        _claudemd_job_set(project_name, status="error", error=str(e))
        return

    if not proposed:
        _claudemd_job_set(project_name, status="error", error="Claude agent returned empty output")
        return

    # Build result data
    if not current_claudemd:
        data = {
            "current": "", "proposed": proposed, "diff": "",
            "hunks": [], "is_new": True, "warning": None,
        }
    elif current_claudemd.strip() == proposed.strip():
        data = {"hunks": [], "message": "No changes needed"}
    else:
        raw_diff, hunks = _compute_diff_hunks(current_claudemd, proposed)
        proposed_lines = len(proposed.splitlines())
        warning = None
        if proposed_lines > 60:
            warning = f"Proposed CLAUDE.md is {proposed_lines} lines (recommended max: 60)"
            logger.warning("refresh-claudemd %s: %s", project_name, warning)
        data = {
            "current": current_claudemd, "proposed": proposed,
            "diff": raw_diff, "hunks": hunks, "warning": warning,
        }

    _claudemd_job_set(project_name, status="complete", data=data)


@app.post("/api/projects/{name}/refresh-claudemd")
async def refresh_claudemd(name: str, db: Session = Depends(get_db)):
    """Start a background Claude agent to propose CLAUDE.md updates."""
    project_path = _resolve_project_path(name, db)

    # If already running, return existing job
    existing = _claudemd_job_get(name)
    if existing and existing["status"] == "running":
        return {"status": "running"}

    # Gather context synchronously (fast DB + file reads)
    rows = (
        db.query(Message.content, Message.created_at, Agent.name)
        .join(Agent, Message.agent_id == Agent.id)
        .filter(Agent.project == name, Message.role == MessageRole.AGENT)
        .order_by(Message.created_at.desc())
        .limit(50)
        .all()
    )
    parts = []
    total_len = 0
    for content, created_at, agent_name in rows:
        snippet = (content or "")[:500]
        entry = f"[agent: {agent_name}, {created_at}]\n{snippet}\n"
        if total_len + len(entry) > 8000:
            break
        parts.append(entry)
        total_len += len(entry)
    recent_agent_activity = "\n".join(parts) if parts else "(no recent agent activity)"

    claudemd_path = os.path.join(project_path, "CLAUDE.md")
    progress_path = os.path.join(project_path, "PROGRESS.md")
    current_claudemd = ""
    if os.path.isfile(claudemd_path):
        with open(claudemd_path, "r", encoding="utf-8", errors="replace") as f:
            current_claudemd = f.read()
    progress_md = ""
    if os.path.isfile(progress_path):
        with open(progress_path, "r", encoding="utf-8", errors="replace") as f:
            progress_md = f.read()

    # Pre-read build/config files so the agent doesn't need tool access
    build_files_content = ""
    for fname in ("package.json", "pyproject.toml", "Makefile", "Cargo.toml",
                  "setup.py", "README.md"):
        fpath = os.path.join(project_path, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(4000)  # cap per file
                build_files_content += f"\n--- {fname} ---\n{text}\n"
            except OSError as e:
                logger.warning("Failed to read build file %s: %s", fpath, e)

    # Mark as running and spawn background thread
    _claudemd_job_set(name, status="running")
    thread = threading.Thread(
        target=_refresh_claudemd_background,
        args=(name, project_path, recent_agent_activity, current_claudemd,
              progress_md, build_files_content),
        daemon=True,
    )
    thread.start()

    return {"status": "started"}


@app.get("/api/projects/{name}/refresh-claudemd/status")
async def refresh_claudemd_status(name: str):
    """Poll the status of a background CLAUDE.md refresh job."""
    job = _claudemd_job_get(name)
    if not job:
        return {"status": "none"}
    if job["status"] == "running":
        return {"status": "running"}
    if job["status"] == "error":
        return {"status": "error", "message": job.get("error", "Unknown error")}
    # complete
    return {"status": "complete", "data": job["data"]}


@app.delete("/api/projects/{name}/refresh-claudemd")
async def discard_claudemd(name: str):
    """Clear a cached CLAUDE.md refresh result (user discarded)."""
    _claudemd_job_clear(name)
    return {"success": True}


@app.get("/api/projects/claudemd-pending")
async def claudemd_pending():
    """Return count and list of projects with completed CLAUDE.md refresh jobs."""
    with _claudemd_jobs_lock:
        now = _time.monotonic()
        projects = [
            k for k, v in _claudemd_jobs.items()
            if v["status"] == "complete" and now - v["ts"] <= _CLAUDEMD_CACHE_TTL
        ]
    return {"count": len(projects), "projects": projects}


@app.post("/api/projects/{name}/apply-claudemd")
async def apply_claudemd(name: str, body: ApplyClaudeMdRequest, db: Session = Depends(get_db)):
    """Apply proposed CLAUDE.md changes (all or selective hunks)."""
    project_path = _resolve_project_path(name, db)
    claudemd_path = os.path.join(project_path, "CLAUDE.md")

    job = _claudemd_job_get(name)
    if not job or job["status"] != "complete":
        raise HTTPException(status_code=410, detail="Proposal expired — run refresh again")

    proposed = job["data"].get("proposed", "")
    current = job["data"].get("current", "")

    if body.mode == "accept_all":
        final_content = proposed
    elif body.mode == "selective":
        if body.final_content is not None:
            # Frontend assembled the final content — just use it
            final_content = body.final_content
        else:
            # Legacy: hunk-level selection via SequenceMatcher opcodes
            accepted_ids = set(body.accepted_hunk_ids)
            current_lines = current.splitlines(keepends=True)
            proposed_lines = proposed.splitlines(keepends=True)

            sm = difflib.SequenceMatcher(None, current_lines, proposed_lines)
            result_lines = []
            hunk_idx = 0
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    result_lines.extend(current_lines[i1:i2])
                else:
                    if hunk_idx in accepted_ids:
                        result_lines.extend(proposed_lines[j1:j2])
                    else:
                        result_lines.extend(current_lines[i1:i2])
                    hunk_idx += 1

            final_content = "".join(result_lines)
    else:
        raise HTTPException(status_code=400, detail="mode must be 'accept_all' or 'selective'")

    # Write to disk
    try:
        with open(claudemd_path, "w", encoding="utf-8") as f:
            f.write(final_content)
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    _claudemd_job_clear(name)

    line_count = len(final_content.splitlines())
    if line_count > 60:
        logger.warning("apply-claudemd %s: written CLAUDE.md is %d lines (>60)", name, line_count)

    return {"success": True, "content": final_content, "lines": line_count}


# ---- PROGRESS.md daily summary ----

_progress_jobs: dict[str, dict] = {}
_progress_jobs_lock = threading.Lock()
_PROGRESS_CACHE_TTL = 600  # 10 minutes


def _progress_job_get(project_name: str) -> dict | None:
    with _progress_jobs_lock:
        entry = _progress_jobs.get(project_name)
        if not entry:
            return None
        if entry["status"] != "running" and _time.monotonic() - entry["ts"] > _PROGRESS_CACHE_TTL:
            del _progress_jobs[project_name]
            return None
        return entry


def _progress_job_set(project_name: str, **kwargs):
    with _progress_jobs_lock:
        _progress_jobs[project_name] = {"ts": _time.monotonic(), **kwargs}


def _progress_job_clear(project_name: str):
    with _progress_jobs_lock:
        _progress_jobs.pop(project_name, None)


def _summarize_progress_background(project_name: str, project_path: str,
                                   session_context: str):
    """Run claude -p in a thread to generate a daily summary section (incremental append)."""
    from datetime import date
    today = date.today().isoformat()

    prompt = f"""You are a project analyst. Read the following completed task sessions from today and produce a concise daily summary.

STRICT RULES:
1. Output ONLY the summary section — no preamble, no explanation, no markdown fences.
2. Use EXACTLY this format:

## {today} — Daily Summary
- [task title]: what was done, key lesson or gotcha (1 line each)

3. Focus on: what was accomplished, problems encountered, solutions found, lessons for future agents.
4. Deduplicate — if multiple tasks did similar work, merge into one bullet.
5. Max 15 lines total. Be concise but insightful.
6. Do NOT output anything before the ## heading or after the last bullet.

Here are today's completed task sessions with full conversation history:

{session_context}"""

    from config import CLAUDE_BIN
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=300,
            cwd=project_path,
        )
        if result.returncode != 0:
            logger.warning("progress summary failed for %s: %s", project_name, result.stderr[:500])
            _progress_job_set(project_name, status="error", error="Claude agent failed — try again")
            return
        new_section = result.stdout.strip()
    except subprocess.TimeoutExpired:
        _progress_job_set(project_name, status="error", error="Summary timed out (>5min)")
        return
    except FileNotFoundError:
        _progress_job_set(project_name, status="error", error="Claude CLI not found")
        return
    except Exception as e:
        logger.exception("Unexpected error in progress summary for %s", project_name)
        _progress_job_set(project_name, status="error", error=str(e))
        return

    if not new_section:
        _progress_job_set(project_name, status="error", error="Claude agent returned empty output")
        return

    # Strip markdown fences if LLM wrapped output
    if new_section.startswith("```"):
        lines = new_section.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        new_section = "\n".join(lines).strip()

    # For manual flow: show proposed section for user review before appending
    data = {"proposed": new_section, "is_append": True}
    _progress_job_set(project_name, status="complete", data=data)


@app.post("/api/projects/{name}/summarize-progress")
async def summarize_progress(name: str, db: Session = Depends(get_db)):
    """Start a background Claude agent to summarize today's tasks into PROGRESS.md."""
    project_path = _resolve_project_path(name, db)

    existing = _progress_job_get(name)
    if existing and existing["status"] == "running":
        return {"status": "running"}

    # Gather today's completed tasks with rich session context
    from datetime import date
    from agent_dispatcher import _strip_agent_preamble
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    completed_tasks = (
        db.query(Task)
        .filter(
            Task.project_name == name,
            Task.status == TaskStatus.COMPLETE,
            Task.completed_at >= today_start,
        )
        .order_by(Task.completed_at)
        .all()
    )

    if not completed_tasks:
        _progress_job_set(name, status="complete",
                         data={"message": "No tasks completed today"})
        return {"status": "started"}

    session_blocks = []
    for t in completed_tasks:
        block_parts = [f"### Task: {t.title}"]
        if t.description:
            block_parts.append(f"Description: {t.description[:500]}")
        if t.agent_summary:
            block_parts.append(f"Agent summary: {t.agent_summary[:500]}")
        if t.agent_id:
            messages = (
                db.query(Message)
                .filter(Message.agent_id == t.agent_id)
                .order_by(Message.created_at)
                .all()
            )
            if messages:
                block_parts.append("\nConversation:")
                for msg in messages:
                    role = msg.role.value
                    content = msg.content[:2000] if msg.content else ""
                    content = _strip_agent_preamble(content)
                    block_parts.append(f"[{role}] {content}")
        session_blocks.append("\n".join(block_parts))

    session_context = "\n\n---\n\n".join(session_blocks)

    _progress_job_set(name, status="running")
    thread = threading.Thread(
        target=_summarize_progress_background,
        args=(name, project_path, session_context),
        daemon=True,
    )
    thread.start()
    return {"status": "started"}


@app.get("/api/projects/{name}/summarize-progress/status")
async def summarize_progress_status(name: str):
    """Poll the status of a background PROGRESS.md summary job."""
    job = _progress_job_get(name)
    if not job:
        return {"status": "none"}
    if job["status"] == "running":
        return {"status": "running"}
    if job["status"] == "error":
        return {"status": "error", "message": job.get("error", "Unknown error")}
    return {"status": "complete", "data": job["data"]}


@app.delete("/api/projects/{name}/summarize-progress")
async def discard_progress_summary(name: str):
    """Clear a cached PROGRESS.md summary result."""
    _progress_job_clear(name)
    return {"success": True}


@app.post("/api/projects/{name}/apply-progress")
async def apply_progress(name: str, db: Session = Depends(get_db)):
    """Append proposed PROGRESS.md summary section."""
    project_path = _resolve_project_path(name, db)
    progress_path = os.path.join(project_path, "PROGRESS.md")

    job = _progress_job_get(name)
    if not job or job["status"] != "complete":
        raise HTTPException(status_code=410, detail="Proposal expired — run summary again")

    new_section = job["data"].get("proposed", "")
    if not new_section:
        raise HTTPException(status_code=400, detail="No proposed content")

    try:
        existing = ""
        if os.path.isfile(progress_path):
            with open(progress_path, "r", encoding="utf-8", errors="replace") as f:
                existing = f.read()

        separator = "\n\n" if existing and not existing.endswith("\n\n") else ("\n" if existing and not existing.endswith("\n") else "")
        final_content = existing + separator + new_section + "\n"
        with open(progress_path, "w", encoding="utf-8") as f:
            f.write(final_content)
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    _progress_job_clear(name)
    return {"success": True, "content": final_content, "lines": len(final_content.splitlines())}


@app.patch("/api/projects/{name}/settings")
async def update_project_settings(name: str, request: Request, db: Session = Depends(get_db)):
    """Update project toggle settings (auto_progress_summary, etc.)."""
    proj = db.get(Project, name)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")

    body = await request.json()
    if "auto_progress_summary" in body:
        proj.auto_progress_summary = bool(body["auto_progress_summary"])

    db.commit()
    db.refresh(proj)
    return ProjectOut.model_validate(proj)


# ---- Project directory browser (read-only) ----

_BROWSE_IGNORED = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".pycache",
    "backups", "logs", ".next", ".nuxt", "dist", "build", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "egg-info",
}


@app.get("/api/projects/{name}/tree")
async def get_project_tree(name: str, depth: int = 3, db: Session = Depends(get_db)):
    """Return directory tree for a project (top N levels, ignoring common junk dirs)."""
    project_path = _resolve_project_path(name, db)

    def _walk(dirpath: str, current_depth: int):
        if current_depth >= depth:
            return []
        try:
            entries = sorted(os.listdir(dirpath))
        except PermissionError:
            return []
        items = []
        for entry in entries:
            if entry.startswith(".") and entry not in (".env.example",):
                if entry not in (".env",):
                    continue
            full = os.path.join(dirpath, entry)
            rel = os.path.relpath(full, project_path)
            if os.path.isdir(full):
                if entry.lower() in _BROWSE_IGNORED or entry.endswith(".egg-info"):
                    continue
                children = _walk(full, current_depth + 1)
                items.append({"name": entry, "path": rel, "type": "dir", "children": children})
            else:
                items.append({"name": entry, "path": rel, "type": "file"})
        return items

    tree = _walk(project_path, 0)
    return {"tree": tree, "root": project_path}


@app.get("/api/projects/{name}/browse")
async def browse_project_file(name: str, path: str, db: Session = Depends(get_db)):
    """Read a single file from a project directory (read-only, with size limit)."""
    project_path = _resolve_project_path(name, db)

    # Resolve and validate path is within project
    filepath = os.path.normpath(os.path.join(project_path, path))
    if not filepath.startswith(project_path + os.sep) and filepath != project_path:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    size = os.path.getsize(filepath)
    if size > _BROWSE_MAX_FILE_SIZE:
        return {"path": path, "content": None, "truncated": True, "size": size,
                "message": f"File too large ({size // 1024} KB). Max {_BROWSE_MAX_FILE_SIZE // 1024} KB."}

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"path": path, "content": content, "truncated": False, "size": size}
    except (OSError, UnicodeDecodeError) as e:
        return {"path": path, "content": None, "truncated": False, "size": size,
                "message": f"Cannot read file: {e}"}


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


# ---- Tasks v2 (first-class Task entity) ----

from task_state_machine import can_transition, validate_transition, InvalidTransitionError
from websocket import emit_task_update, emit_agent_update


@app.post("/api/v2/tasks", response_model=TaskOut, status_code=201)
async def create_task_v2(body: TaskCreate, db: Session = Depends(get_db)):
    """Create a new task. Starts as INBOX unless auto_dispatch is set."""
    # Auto-generate title from description if blank
    title = body.title.strip() if body.title else ""
    if not title and body.description:
        desc = body.description.strip()
        if len(desc) <= 60:
            title = desc
        else:
            cut = desc[:60].rsplit(" ", 1)[0] if " " in desc[:60] else desc[:60]
            title = cut + "..."
    if not title:
        title = "Untitled task"

    initial_status = TaskStatus.INBOX
    if body.auto_dispatch and body.project_name:
        proj = db.query(Project).filter(Project.name == body.project_name).first()
        if not proj:
            raise HTTPException(400, f"Project not found: {body.project_name}")
        initial_status = TaskStatus.PENDING

    task = Task(
        title=title,
        description=body.description,
        project_name=body.project_name,
        priority=body.priority,
        model=body.model,
        effort=body.effort,
        skip_permissions=body.skip_permissions,
        sync_mode=body.sync_mode,
        use_worktree=body.use_worktree,
        notify_at=body.notify_at,
        status=initial_status,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@app.get("/api/v2/tasks/counts")
async def task_counts(db: Session = Depends(get_db)):
    """Return perspective counts + weekly success stats."""
    from datetime import timedelta

    # Perspective counts (server-side)
    rows = db.query(Task.status, func.count(Task.id)).group_by(Task.status).all()
    by_status = {s.value: c for s, c in rows}

    done_statuses = ["COMPLETE", "CANCELLED", "REJECTED", "FAILED", "TIMEOUT"]
    review_statuses = ["REVIEW", "MERGING", "CONFLICT"]

    counts = {
        "INBOX": by_status.get("INBOX", 0),
        "PLANNING": by_status.get("PLANNING", 0),
        "QUEUE": by_status.get("PENDING", 0),
        "ACTIVE": by_status.get("EXECUTING", 0),
        "REVIEW": sum(by_status.get(s, 0) for s in review_statuses),
        "DONE": sum(by_status.get(s, 0) for s in done_statuses),
        "DONE_COMPLETED": by_status.get("COMPLETE", 0),
    }

    # Weekly stats — tasks that reached a terminal state this week
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    terminal = [TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.TIMEOUT,
                TaskStatus.REJECTED, TaskStatus.CANCELLED]
    weekly_q = db.query(
        Task.status, func.count(Task.id)
    ).filter(
        Task.status.in_(terminal),
        Task.completed_at >= week_ago,
    ).group_by(Task.status).all()

    weekly_by = {s.value: c for s, c in weekly_q}
    weekly_total = sum(weekly_by.values())
    weekly_completed = weekly_by.get("COMPLETE", 0)
    weekly_pct = round(weekly_completed / weekly_total * 100) if weekly_total else 0

    # Daily breakdown for the last 7 days (for sparkline chart)
    daily_rows = db.query(
        func.date(Task.completed_at).label("day"),
        Task.status,
        func.count(Task.id),
    ).filter(
        Task.status.in_(terminal),
        Task.completed_at >= week_ago,
    ).group_by("day", Task.status).all()

    daily_map: dict[str, dict] = {}
    for day_val, status, cnt in daily_rows:
        d = str(day_val)
        if d not in daily_map:
            daily_map[d] = {"date": d, "total": 0, "completed": 0}
        daily_map[d]["total"] += cnt
        if status == TaskStatus.COMPLETE:
            daily_map[d]["completed"] += cnt

    # Fill missing days and compute success_pct
    daily = []
    for i in range(7):
        d = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
        entry = daily_map.get(d, {"date": d, "total": 0, "completed": 0})
        entry["success_pct"] = round(entry["completed"] / entry["total"] * 100) if entry["total"] else None
        daily.append(entry)

    return {
        **counts,
        "weekly_total": weekly_total,
        "weekly_completed": weekly_completed,
        "weekly_success_pct": weekly_pct,
        "weekly_failed": weekly_by.get("FAILED", 0),
        "weekly_timeout": weekly_by.get("TIMEOUT", 0),
        "weekly_cancelled": weekly_by.get("CANCELLED", 0),
        "weekly_rejected": weekly_by.get("REJECTED", 0),
        "daily": daily,
    }


@app.get("/api/v2/tasks", response_model=list[TaskOut])
async def list_tasks_v2(
    status: str | None = None,
    statuses: str | None = None,
    project: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """List v2 tasks with optional filters."""
    q = db.query(Task)
    if statuses:
        status_list = []
        for s in statuses.split(","):
            s = s.strip()
            if not s:
                continue
            try:
                status_list.append(TaskStatus(s))
            except ValueError:
                raise HTTPException(400, f"Invalid status: {s}")
        if status_list:
            q = q.filter(Task.status.in_(status_list))
    elif status:
        try:
            q = q.filter(Task.status == TaskStatus(status))
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
    if project:
        q = q.filter(Task.project_name == project)
    tasks = q.order_by(Task.created_at.desc()).limit(limit).all()

    # Enrich EXECUTING tasks with agent info
    results = []
    executing_agent_ids = [t.agent_id for t in tasks if t.status == TaskStatus.EXECUTING and t.agent_id]
    agent_map = {}
    if executing_agent_ids:
        agents = db.query(Agent).filter(Agent.id.in_(executing_agent_ids)).all()
        agent_map = {a.id: a for a in agents}

    now = datetime.now(timezone.utc)
    for t in tasks:
        out = TaskOut.model_validate(t)
        if t.status == TaskStatus.EXECUTING and t.agent_id:
            agent = agent_map.get(t.agent_id)
            if agent and agent.last_message_preview:
                out.last_agent_message = agent.last_message_preview[:200]
            if t.started_at:
                started = t.started_at if t.started_at.tzinfo else t.started_at.replace(tzinfo=timezone.utc)
                out.elapsed_seconds = int((now - started).total_seconds())
        results.append(out)
    return results


@app.get("/api/v2/tasks/{task_id}", response_model=TaskDetailOut)
async def get_task_v2(task_id: str, db: Session = Depends(get_db)):
    """Get task detail with agent conversation if assigned."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    conversation = []
    if task.agent_id:
        msgs = (
            db.query(Message)
            .filter(Message.agent_id == task.agent_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        conversation = [MessageOut.model_validate(m, from_attributes=True) for m in msgs]
    return TaskDetailOut(
        **TaskOut.model_validate(task).model_dump(),
        retry_context=task.retry_context,
        conversation=conversation,
    )


@app.put("/api/v2/tasks/{task_id}", response_model=TaskOut)
async def update_task_v2(task_id: str, body: TaskUpdate, db: Session = Depends(get_db)):
    """Update task fields. Only allowed for INBOX/PLANNING tasks."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status not in (TaskStatus.INBOX, TaskStatus.PLANNING):
        raise HTTPException(400, f"Cannot edit task in {task.status.value} status")
    # Support status transitions (e.g. PLANNING → INBOX)
    if hasattr(body, "status") and body.status is not None:
        try:
            new_status = TaskStatus(body.status)
            validate_transition(task.status, new_status)
            task.status = new_status
        except (ValueError, InvalidTransitionError) as exc:
            raise HTTPException(409, str(exc))
    for field in ("title", "description", "project_name", "priority", "model", "effort"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(task, field, val)
    # Time fields: allow explicit null to clear
    if "notify_at" in body.model_fields_set:
        task.notify_at = body.notify_at
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@app.post("/api/v2/tasks/{task_id}/plan", response_model=TaskOut)
async def plan_task_v2(task_id: str, db: Session = Depends(get_db)):
    """Move task from INBOX to PLANNING. Requires project_name."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.project_name:
        raise HTTPException(400, "Task requires a project_name before entering PLANNING")
    try:
        validate_transition(task.status, TaskStatus.PLANNING)
    except InvalidTransitionError as e:
        raise HTTPException(409, str(e))
    task.status = TaskStatus.PLANNING
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@app.post("/api/v2/tasks/{task_id}/dispatch", response_model=TaskOut)
async def dispatch_task_v2(task_id: str, db: Session = Depends(get_db)):
    """Move task to PENDING for auto-dispatch."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.project_name:
        raise HTTPException(400, "Task requires a project_name before dispatch")
    if not task.title:
        raise HTTPException(400, "Task requires a title before dispatch")
    try:
        validate_transition(task.status, TaskStatus.PENDING)
    except InvalidTransitionError as e:
        raise HTTPException(409, str(e))
    # Redo: auto-increment attempt and prepare context
    if task.status in (TaskStatus.REJECTED, TaskStatus.FAILED, TaskStatus.TIMEOUT):
        task.attempt_number += 1
        if task.agent_summary:
            task.retry_context = task.agent_summary
        task.agent_id = None
        task.agent_summary = None
        task.started_at = None
        task.completed_at = None
        task.review_artifacts = None  # Clear stale verify data from previous attempt
    task.status = TaskStatus.PENDING
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@app.post("/api/v2/tasks/{task_id}/approve", response_model=TaskOut)
async def approve_task_v2(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Approve a REVIEW task → transition to MERGING (agent-based merge).

    For tasks with a branch, sets status to MERGING and lets the dispatcher
    create a merge agent. For no-branch tasks, completes immediately.
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    try:
        validate_transition(task.status, TaskStatus.MERGING)
    except InvalidTransitionError as e:
        raise HTTPException(409, str(e))

    proj = db.query(Project).filter(Project.name == task.project_name).first()
    if not proj:
        raise HTTPException(400, "Missing project for merge")

    # Helper: stop linked agent if still running
    def _stop_linked_agent():
        agent = db.get(Agent, task.agent_id) if task.agent_id else None
        if agent and agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
            import subprocess as _sp
            agent.status = AgentStatus.STOPPED
            if agent.tmux_pane:
                sess_name = f"ah-{agent.id[:8]}"
                _sp.run(["tmux", "kill-session", "-t", sess_name],
                        capture_output=True, timeout=5)
                agent.tmux_pane = None
            asyncio.ensure_future(emit_agent_update(agent.id, "STOPPED", agent.project))
        # Also stop any running verify sub-agents for this task
        verify_agents = (
            db.query(Agent)
            .filter(Agent.task_id == task.id, Agent.is_subagent == True, Agent.name.like("Verify:%"))
            .filter(Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]))
            .all()
        )
        for va in verify_agents:
            import subprocess as _sp
            va.status = AgentStatus.STOPPED
            if va.tmux_pane:
                _sp.run(["tmux", "kill-session", "-t", f"ah-{va.id[:8]}"],
                        capture_output=True, timeout=5)
                va.tmux_pane = None
            asyncio.ensure_future(emit_agent_update(va.id, "STOPPED", va.project))

    # No branch to merge (use_worktree=False) — skip merge, go straight to COMPLETE
    if not task.branch_name:
        _stop_linked_agent()
        task.status = TaskStatus.COMPLETE
        task.try_base_commit = None
        task.completed_at = _utcnow()
        db.commit()
        db.refresh(task)
        asyncio.ensure_future(emit_task_update(
            task.id, task.status.value, task.project_name or "",
            title=task.title,
        ))
        return TaskOut.model_validate(task)

    # Has branch + already tried (merge already applied) — skip MERGING, complete directly
    if task.try_base_commit:
        _stop_linked_agent()
        # Merge was already done via Try — clean up worktree & branch
        gm = getattr(request.app.state, "git_manager", None)
        if gm and task.worktree_name:
            wt_path = os.path.join(proj.path, ".claude", "worktrees", task.worktree_name)
            gm.remove_worktree(proj.path, wt_path)
            gm.delete_branch(proj.path, task.branch_name)
        task.status = TaskStatus.COMPLETE
        task.try_base_commit = None
        task.completed_at = _utcnow()
        db.commit()
        db.refresh(task)
        asyncio.ensure_future(emit_task_update(
            task.id, task.status.value, task.project_name or "",
            title=task.title,
        ))
        logger.info("Task %s: approved (already tried), completing directly", task.id)
        return TaskOut.model_validate(task)

    # Has branch, not tried — perform merge synchronously
    _stop_linked_agent()
    task.status = TaskStatus.MERGING
    task.error_message = None
    db.commit()
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    logger.info("Task %s: approved, merging branch %s", task.id, task.branch_name)

    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        task.status = TaskStatus.CONFLICT
        task.error_message = "Git manager not available"
        db.commit()
        db.refresh(task)
        asyncio.ensure_future(emit_task_update(
            task.id, task.status.value, task.project_name or "", title=task.title,
        ))
        return TaskOut.model_validate(task)

    # Ensure on main branch
    main_branch = gm.get_main_branch(proj.path)
    current_branch = gm.get_current_branch(proj.path)
    if current_branch != main_branch:
        co_result = gm.checkout(proj.path, main_branch)
        if co_result.startswith("ERROR:"):
            task.status = TaskStatus.CONFLICT
            task.error_message = f"Cannot checkout {main_branch}: {co_result}"
            db.commit()
            db.refresh(task)
            asyncio.ensure_future(emit_task_update(
                task.id, task.status.value, task.project_name or "", title=task.title,
            ))
            return TaskOut.model_validate(task)

    # Merge
    result = gm.merge_branch(proj.path, task.branch_name)
    if not result.get("success"):
        task.status = TaskStatus.CONFLICT
        task.error_message = f"Merge failed: {result.get('error', 'unknown')}"
        db.commit()
        db.refresh(task)
        asyncio.ensure_future(emit_task_update(
            task.id, task.status.value, task.project_name or "", title=task.title,
        ))
        return TaskOut.model_validate(task)

    # Merge succeeded — clean up worktree & branch, mark COMPLETE
    if task.worktree_name:
        wt_path = os.path.join(proj.path, ".claude", "worktrees", task.worktree_name)
        gm.remove_worktree(proj.path, wt_path)
        del_result = gm.delete_branch(proj.path, task.branch_name)
        # delete_branch uses -d which fails if branch is not merged — treat as merge failure
        if del_result.startswith("ERROR:") and "not yet merged" in del_result:
            task.status = TaskStatus.CONFLICT
            task.error_message = "Merge appeared to succeed but branch was not actually merged. Please retry."
            db.commit()
            db.refresh(task)
            asyncio.ensure_future(emit_task_update(
                task.id, task.status.value, task.project_name or "", title=task.title,
            ))
            logger.warning("Task %s: merge succeeded but branch not merged (phantom merge)", task.id)
            return TaskOut.model_validate(task)
    task.status = TaskStatus.COMPLETE
    task.completed_at = _utcnow()
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    logger.info("Task %s: merge complete", task.id)
    return TaskOut.model_validate(task)


@app.post("/api/v2/tasks/{task_id}/reject", response_model=TaskOut)
async def reject_task_v2(
    task_id: str,
    body: TaskRejectRequest,
    db: Session = Depends(get_db),
):
    """Reject a task with a reason → REJECTED."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    try:
        validate_transition(task.status, TaskStatus.REJECTED)
    except InvalidTransitionError as e:
        raise HTTPException(409, str(e))
    # Stop running agent if still active
    if task.agent_id:
        agent = db.get(Agent, task.agent_id)
        if agent and agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
            agent.status = AgentStatus.STOPPED
            if agent.tmux_pane:
                import subprocess
                sess_name = f"ah-{agent.id[:8]}"
                subprocess.run(["tmux", "kill-session", "-t", sess_name],
                               capture_output=True, timeout=5)
                agent.tmux_pane = None
    # Stop any running verify sub-agents
    import subprocess as _sp
    verify_agents = (
        db.query(Agent)
        .filter(Agent.task_id == task.id, Agent.is_subagent == True, Agent.name.like("Verify:%"))
        .filter(Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]))
        .all()
    )
    for va in verify_agents:
        va.status = AgentStatus.STOPPED
        if va.tmux_pane:
            _sp.run(["tmux", "kill-session", "-t", f"ah-{va.id[:8]}"], capture_output=True, timeout=5)
            va.tmux_pane = None
    task.status = TaskStatus.REJECTED
    task.rejection_reason = body.reason
    task.try_base_commit = None  # Clear try state on reject
    task.review_artifacts = None  # Clear stale verify data
    task.completed_at = _utcnow()
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@app.post("/api/v2/tasks/{task_id}/verify")
async def verify_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Spawn a verification sub-agent to check the task's output (tests, build, etc.)."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status != TaskStatus.REVIEW:
        raise HTTPException(409, f"Task must be in REVIEW state (currently {task.status.value})")
    if not task.agent_id:
        raise HTTPException(409, "Task has no agent — nothing to verify")
    if not task.project_name:
        raise HTTPException(409, "Task has no project assigned")

    # Check if a verification agent is already running for this task
    existing_verify = (
        db.query(Agent)
        .filter(
            Agent.task_id == task.id,
            Agent.is_subagent == True,
            Agent.name.like("Verify:%"),
            Agent.status.in_([AgentStatus.IDLE, AgentStatus.STARTING, AgentStatus.EXECUTING]),
        )
        .first()
    )
    if existing_verify:
        raise HTTPException(409, "A verification agent is already running for this task")

    proj = db.get(Project, task.project_name)
    if not proj:
        raise HTTPException(404, f"Project '{task.project_name}' not found")

    original_agent = db.get(Agent, task.agent_id)
    if not original_agent:
        raise HTTPException(404, "Original agent not found")

    # Build verification prompt
    context_parts = [
        f"# Verification Task",
        f"You are a **verification agent**. Your job is to independently check whether a completed coding task was done correctly.",
        f"",
        f"## Original Task",
        f"**Title:** {task.title}",
    ]
    if task.description:
        context_parts.append(f"**Description:** {task.description}")
    if task.agent_summary:
        context_parts.append(f"\n## Agent's Summary of What Was Done")
        context_parts.append(task.agent_summary)

    # If worktree task, tell the agent which branch to check
    if task.branch_name:
        context_parts.append(f"\n## Branch")
        context_parts.append(f"The changes are on branch `{task.branch_name}`.")
        context_parts.append(f"Use `git diff main...{task.branch_name}` to see the full diff.")

    context_parts.append(f"\n## Your Verification Checklist")
    context_parts.append("1. Read the diff / changed files to understand what was modified")
    context_parts.append("2. Check if the changes match the task requirements")
    context_parts.append("3. Run the project's test suite (if any) — look for test commands in CLAUDE.md, package.json, Makefile, etc.")
    context_parts.append("4. Run the build (if applicable) to check for compilation/bundling errors")
    context_parts.append("5. Look for obvious issues: missing imports, unused variables, broken logic, security problems")
    context_parts.append("")
    context_parts.append("## Output Format")
    context_parts.append("End your response with a structured verdict:")
    context_parts.append("```")
    context_parts.append("VERDICT: PASS | FAIL | WARN")
    context_parts.append("ISSUES: (list any issues found, or 'none')")
    context_parts.append("TESTS: (test results summary, or 'no tests found')")
    context_parts.append("BUILD: (build result, or 'not applicable')")
    context_parts.append("```")
    context_parts.append("")
    context_parts.append("Be thorough but concise. Focus on correctness, not style.")

    verify_prompt = "\n".join(context_parts)

    # Create the verification agent — runs in the same project dir (not a worktree)
    import secrets
    for _ in range(20):
        agent_hex = secrets.token_hex(6)
        if db.get(Agent, agent_hex) is None:
            break
    else:
        raise HTTPException(500, "Failed to generate agent ID")

    agent = Agent(
        id=agent_hex,
        project=proj.name,
        name=f"Verify: {task.title[:70]}",
        mode=AgentMode.AUTO,
        status=AgentStatus.IDLE,
        model=task.model or proj.default_model or CC_MODEL,
        effort="low",  # Verification is lightweight
        skip_permissions=True,
        task_id=task.id,
        parent_id=task.agent_id,
        is_subagent=True,
        last_message_preview=f"Verifying: {task.title[:70]}",
        last_message_at=_utcnow(),
    )
    db.add(agent)
    db.flush()

    msg = Message(
        agent_id=agent.id,
        role=MessageRole.USER,
        content=verify_prompt,
        status=MessageStatus.PENDING,
        source="verify",
    )
    db.add(msg)

    # Store verification agent ID in review_artifacts
    import json as _json
    artifacts = {}
    if task.review_artifacts:
        try:
            artifacts = _json.loads(task.review_artifacts)
        except (ValueError, TypeError):
            logger.warning("Invalid review_artifacts JSON for task %s", task.id)
            artifacts = {}
    artifacts["verify_agent_id"] = agent.id
    artifacts["verify_status"] = "running"
    task.review_artifacts = _json.dumps(artifacts)

    db.commit()

    from websocket import emit_agent_update, emit_task_update
    asyncio.ensure_future(emit_agent_update(agent.id, agent.status.value, proj.name))
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))

    return {
        "status": "started",
        "verify_agent_id": agent.id,
        "task_id": task.id,
    }


@app.post("/api/v2/tasks/{task_id}/try-changes", response_model=TaskOut)
async def try_task_changes(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Merge task branch into main so user can test locally. Records pre-merge HEAD for revert."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status != TaskStatus.REVIEW:
        raise HTTPException(409, "Task must be in REVIEW status to try changes")
    if not task.branch_name:
        raise HTTPException(400, "Task has no branch to try")
    if task.try_base_commit:
        raise HTTPException(409, "Changes already applied — revert first")

    proj = db.query(Project).filter(Project.name == task.project_name).first()
    if not proj:
        raise HTTPException(400, "Project not found")

    # Guard: only one task per project can be "tried" at a time
    other_tried = (
        db.query(Task)
        .filter(Task.project_name == task.project_name)
        .filter(Task.id != task.id)
        .filter(Task.try_base_commit.isnot(None))
        .filter(Task.status == TaskStatus.REVIEW)
        .first()
    )
    if other_tried:
        raise HTTPException(
            409,
            f"Another task is already being tried: \"{other_tried.title}\" — revert it first",
        )

    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(503, "Git manager not available")

    # Ensure we're on the main branch
    current_branch = gm.get_current_branch(proj.path)
    if not current_branch:
        raise HTTPException(500, "Cannot determine current branch")

    main_branch = gm.get_main_branch(proj.path)
    if current_branch != main_branch:
        co_result = gm.checkout(proj.path, main_branch)
        if co_result.startswith("ERROR:"):
            raise HTTPException(500, f"Cannot checkout {main_branch}: {co_result}")

    # Save current HEAD before merge
    head_before = gm.get_head(proj.path)
    if not head_before:
        raise HTTPException(500, "Cannot determine current HEAD")

    # Merge the task branch
    result = gm.merge_branch(proj.path, task.branch_name)
    if not result.get("success"):
        raise HTTPException(409, f"Merge failed: {result.get('error', 'unknown error')}")

    # Record pre-merge commit for revert
    task.try_base_commit = head_before
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@app.post("/api/v2/tasks/{task_id}/revert-try", response_model=TaskOut)
async def revert_task_try(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Revert a previously tried merge — reset main to pre-merge HEAD.

    For non-worktree tasks (no branch_name), creates a backup branch first
    so the agent's commits are preserved and can be re-tried or approved later.
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if not task.try_base_commit:
        raise HTTPException(409, "No tried changes to revert")

    proj = db.query(Project).filter(Project.name == task.project_name).first()
    if not proj:
        raise HTTPException(400, "Project not found")

    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(503, "Git manager not available")

    # Non-worktree task: save agent's commits to a backup branch before resetting
    if not task.branch_name:
        backup_branch = f"task/{task.id}/backup"
        import subprocess
        subprocess.run(
            ["git", "branch", "-f", backup_branch, "HEAD"],
            cwd=proj.path, capture_output=True, timeout=10,
        )
        task.branch_name = backup_branch

    # Validate commit exists before resetting
    import subprocess as _sp_verify
    verify = _sp_verify.run(
        ["git", "rev-parse", "--verify", task.try_base_commit],
        cwd=proj.path, capture_output=True, timeout=10,
    )
    if verify.returncode != 0:
        raise HTTPException(400, f"Invalid commit SHA: {task.try_base_commit}")

    # Reset to the pre-merge commit
    result = gm.reset_hard(proj.path, task.try_base_commit)
    if result.startswith("ERROR:"):
        raise HTTPException(500, f"Reset failed: {result}")

    # Clear the try state
    task.try_base_commit = None
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


@app.post("/api/v2/tasks/{task_id}/cancel", response_model=TaskOut)
async def cancel_task_v2(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Cancel a task. Stops agent if running."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    try:
        validate_transition(task.status, TaskStatus.CANCELLED)
    except InvalidTransitionError as e:
        raise HTTPException(409, str(e))
    # Stop linked agent regardless of task status (EXECUTING, MERGING, REVIEW, etc.)
    if task.agent_id:
        agent = db.get(Agent, task.agent_id)
        if agent and agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
            agent.status = AgentStatus.STOPPED
            # Kill tmux session
            if agent.tmux_pane:
                import subprocess
                sess_name = f"ah-{agent.id[:8]}"
                subprocess.run(["tmux", "kill-session", "-t", sess_name],
                               capture_output=True, timeout=5)
                agent.tmux_pane = None
            asyncio.ensure_future(emit_agent_update(agent.id, "STOPPED", agent.project))
    # Stop any running verify sub-agents
    import subprocess as _sp
    verify_agents = (
        db.query(Agent)
        .filter(Agent.task_id == task.id, Agent.is_subagent == True, Agent.name.like("Verify:%"))
        .filter(Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]))
        .all()
    )
    for va in verify_agents:
        va.status = AgentStatus.STOPPED
        if va.tmux_pane:
            _sp.run(["tmux", "kill-session", "-t", f"ah-{va.id[:8]}"], capture_output=True, timeout=5)
            va.tmux_pane = None
    task.status = TaskStatus.CANCELLED
    task.completed_at = _utcnow()
    # Clean up git artifacts
    proj = db.query(Project).filter(Project.name == task.project_name).first()
    if proj:
        import subprocess as _sp
        if task.worktree_name:
            wt_path = os.path.join(proj.path, ".claude", "worktrees", task.worktree_name)
            _sp.run(["git", "worktree", "remove", wt_path, "--force"],
                    cwd=proj.path, capture_output=True, timeout=30)
        if task.branch_name:
            _sp.run(["git", "branch", "-D", task.branch_name],
                    cwd=proj.path, capture_output=True, timeout=10)
    db.commit()
    db.refresh(task)
    asyncio.ensure_future(emit_task_update(
        task.id, task.status.value, task.project_name or "",
        title=task.title,
    ))
    return TaskOut.model_validate(task)


# ---- Agents ----

def _generate_worktree_name_local(prompt: str) -> str:
    """Generate a short branch-style worktree name from the prompt (no API)."""
    words = re.sub(r"[^a-zA-Z0-9\s]", "", prompt).lower().split()
    skip = {"the", "a", "an", "to", "in", "on", "for", "and", "or", "is", "it", "of", "with", "my", "me", "i", "this", "that", "please", "can", "you", "do", "make", "let"}
    words = [w for w in words if w not in skip][:4]
    return "-".join(words) if words else "task"


@app.post("/api/worktree-name")
async def generate_worktree_name(request: Request):
    """Generate a short branch name from a prompt using GPT-4o-mini."""
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return {"name": "task"}

    if not OPENAI_API_KEY:
        return {"name": _generate_worktree_name_local(prompt)}

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Generate a short git branch name (kebab-case, lowercase, "
                    "3-5 words, no special chars) summarizing the task. "
                    "Reply with ONLY the branch name, nothing else."
                )},
                {"role": "user", "content": prompt[:500]},
            ],
            max_tokens=30,
            temperature=0.3,
        )
        name = resp.choices[0].message.content.strip().lower()
        name = re.sub(r"[^a-z0-9-]", "-", name).strip("-")
        name = re.sub(r"-+", "-", name)
        return {"name": name or _generate_worktree_name_local(prompt)}
    except Exception as e:
        logger.warning("Worktree name generation failed: %s", e)
        return {"name": _generate_worktree_name_local(prompt)}


@app.post("/api/agents", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, request: Request, db: Session = Depends(get_db)):
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

    # Resolve model: explicit > project default > global default
    agent_model = body.model or project.default_model or CC_MODEL
    if agent_model not in VALID_MODELS:
        logger.warning("Invalid model %r for agent, falling back to %s", agent_model, CC_MODEL)
        agent_model = CC_MODEL

    # Determine initial status: SYNCING if importing CLI session
    is_sync = body.sync_session and body.resume_session_id
    initial_status = AgentStatus.SYNCING if is_sync else AgentStatus.STARTING

    # Pre-generate agent ID so we can use it for worktree naming
    import uuid
    agent_id = uuid.uuid4().hex[:12]

    # Resolve worktree name: "auto" → GPT-generated branch name
    wt = body.worktree
    if wt == "auto":
        wt = _generate_worktree_name_local(body.prompt)

    # Infer worktree from session JSONL location when resuming/syncing
    # without an explicit worktree (e.g. Sessions tab resume)
    if not wt and body.resume_session_id:
        from agent_dispatcher import _infer_worktree_from_session
        _inferred = _infer_worktree_from_session(body.resume_session_id, project.path)
        if _inferred:
            wt = _inferred
            logger.info("Inferred worktree=%s from session JSONL path", wt)

    agent = Agent(
        id=agent_id,
        project=body.project,
        name=name,
        mode=body.mode,
        status=initial_status,
        model=agent_model,
        effort=body.effort,
        worktree=wt,
        timeout_seconds=body.timeout_seconds,
        session_id=body.resume_session_id,
        cli_sync=bool(is_sync),
        skip_permissions=body.skip_permissions,
        last_message_preview=name,
        last_message_at=_utcnow(),
    )
    db.add(agent)
    db.flush()  # Get agent.id

    if is_sync:
        # Sync mode: import existing history, don't create initial user message
        db.commit()
        db.refresh(agent)

        # Import history and start live sync in background
        ad = getattr(request.app.state, "agent_dispatcher", None)
        if ad:
            imported = ad.import_session_history(
                agent.id, body.resume_session_id, project.path
            )
            logger.info(
                "Agent %s: imported %d messages from CLI session %s",
                agent.id, imported, body.resume_session_id,
            )
            # Start live sync to tail ongoing CLI activity
            ad.start_session_sync(
                agent.id, body.resume_session_id, project.path
            )
    else:
        # Normal mode: create the initial user message
        msg = Message(
            agent_id=agent.id,
            role=MessageRole.USER,
            content=body.prompt,
            status=MessageStatus.PENDING,
        )
        db.add(msg)
        db.commit()
        db.refresh(agent)

    logger.info("Agent %s created for project %s (mode %s, sync=%s)", agent.id, agent.project, agent.mode.value, is_sync)
    return agent


def _preflight_claude_project(project_path: str):
    """Ensure all Claude Code prerequisites are met before launching.

    Claude Code can show up to 8 blocking dialogs on startup.  This preflight
    pre-accepts all of them so the TUI starts straight into the REPL.

    Dialogs handled (in startup order):
    1. Onboarding wizard (theme, login, security notes)
    2. Custom API key approval
    3. Workspace trust ("do you trust this folder?")
    4. Hooks trust
    5. CLAUDE.md external includes warning
    6. Bypass-permissions mode warning
    7. MCP server approval
    8. Project onboarding

    Config files:
    - ~/.claude.json          — per-project trust + global onboarding state
    - ~/.claude/settings.json — global settings (permissions, cleanup, MCP)

    Trust cascades from parent directories: trusting PROJECTS_DIR root covers
    all projects under it.
    """
    from config import CLAUDE_HOME, PROJECTS_DIR

    # --- 1. ~/.claude.json (global state + per-project trust) ---
    claude_json_path = os.path.join(os.path.expanduser("~"), ".claude.json")
    for _ in range(3):
        try:
            data = {}
            if os.path.isfile(claude_json_path):
                with open(claude_json_path, "r") as f:
                    data = json.load(f)

            changed = False

            # Global onboarding (dialog 1)
            if data.get("hasCompletedOnboarding") is not True:
                data["hasCompletedOnboarding"] = True
                changed = True

            projects = data.setdefault("projects", {})

            # Trust the PROJECTS_DIR root — cascades to all child projects
            # so we don't need per-project entries for trust alone.
            projects_dir = PROJECTS_DIR or ""
            if projects_dir:
                root_cfg = projects.setdefault(projects_dir, {})
                if root_cfg.get("hasTrustDialogAccepted") is not True:
                    root_cfg["hasTrustDialogAccepted"] = True
                    root_cfg["hasTrustDialogHooksAccepted"] = True
                    changed = True

            # Per-project flags (dialogs 3-5, 8)
            proj_cfg = projects.setdefault(project_path, {})
            _trust_fields = {
                "hasTrustDialogAccepted": True,
                "hasTrustDialogHooksAccepted": True,
                "hasCompletedProjectOnboarding": True,
                "hasClaudeMdExternalIncludesApproved": True,
                "hasClaudeMdExternalIncludesWarningShown": True,
            }
            for field, value in _trust_fields.items():
                if proj_cfg.get(field) is not value:
                    proj_cfg[field] = value
                    changed = True
            if not proj_cfg.get("projectOnboardingSeenCount"):
                proj_cfg["projectOnboardingSeenCount"] = 1
                changed = True

            if changed:
                with open(claude_json_path, "w") as f:
                    json.dump(data, f, indent=2)
                logger.info("Preflight: updated ~/.claude.json for %s", project_path)
            break
        except (json.JSONDecodeError, OSError) as e:
            # Retry after brief delay — concurrent Claude agents may be writing
            # to the same ~/.claude.json file, causing transient read/write races
            logger.warning("Preflight: failed to update ~/.claude.json: %s", e)
            import time
            time.sleep(0.1)

    # --- 2. ~/.claude/settings.json (global settings) ---
    settings_path = os.path.join(CLAUDE_HOME, "settings.json")
    try:
        settings = {}
        if os.path.isfile(settings_path):
            with open(settings_path, "r") as f:
                settings = json.load(f)

        changed = False
        _global_flags = {
            "skipDangerousModePermissionPrompt": True,   # dialog 6
            "cleanupPeriodDays": 36500,                  # prevent session cleanup
            "enableAllProjectMcpServers": True,          # dialog 7
        }
        for flag, value in _global_flags.items():
            if settings.get(flag) != value:
                settings[flag] = value
                changed = True

        if changed:
            with open(settings_path, "w") as f:
                json.dump(settings, f, indent=2)
            logger.info("Preflight: updated ~/.claude/settings.json")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Preflight: failed to update settings.json: %s", e)


@app.post("/api/agents/launch-tmux", status_code=201)
async def launch_tmux_agent(request: Request, db: Session = Depends(get_db)):
    """Launch an interactive claude CLI session in a new tmux pane.

    Starts Claude in interactive mode (full TUI), then sends the prompt
    as input after Claude finishes loading.  The user can attach to the
    tmux pane to interact with Claude directly.

    A background task detects the session JSONL and starts live-syncing
    the conversation into the webapp.
    """
    import shlex
    import subprocess
    from config import CLAUDE_BIN

    body = await request.json()
    project_name = body.get("project")
    prompt = body.get("prompt", "").strip()
    model = body.get("model")
    effort = body.get("effort")
    worktree = body.get("worktree")
    skip_permissions = body.get("skip_permissions", True)
    task_id = body.get("task_id")

    # Reject if too many agents are already queued for launch
    starting_count = db.query(func.count(Agent.id)).filter(
        Agent.status == AgentStatus.STARTING,
    ).scalar() or 0
    if starting_count >= _MAX_STARTING_AGENTS:
        raise HTTPException(
            status_code=429,
            detail="Too many agents launching — please wait for current launches to finish",
        )

    if not project_name:
        raise HTTPException(status_code=400, detail="Project is required")

    proj = db.get(Project, project_name)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
    if not os.path.isdir(proj.path):
        raise HTTPException(status_code=400, detail="Project directory not found on disk")

    # Each agent gets its own tmux session: "ah-{agent_id_prefix}"
    # Pre-generate agent ID, ensuring no DB or tmux session name collision
    import secrets
    import subprocess as _sp

    # Get existing tmux session names for collision check
    try:
        _tmux_ls = _sp.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        _existing_tmux = set(_tmux_ls.stdout.strip().splitlines()) if _tmux_ls.returncode == 0 else set()
    except (OSError, _sp.TimeoutExpired):
        _existing_tmux = set()

    for _ in range(20):
        agent_hex = secrets.token_hex(6)
        tmux_session = f"ah-{agent_hex[:8]}"
        if db.get(Agent, agent_hex) is None and tmux_session not in _existing_tmux:
            break
    else:
        raise HTTPException(status_code=500, detail="Failed to generate unique agent ID")

    # Resolve worktree name: "auto" → GPT-generated branch name
    if worktree == "auto" and prompt:
        worktree = _generate_worktree_name_local(prompt)

    # Build the claude command in INTERACTIVE mode (no -p, so the user
    # gets the full TUI and can attach via tmux).
    cmd_parts = [CLAUDE_BIN,
                  "--output-format", "stream-json", "--verbose"]
    if skip_permissions:
        cmd_parts.append("--dangerously-skip-permissions")
    if model:
        cmd_parts += ["--model", model]
    if effort:
        cmd_parts += ["--effort", effort]
    if worktree:
        cmd_parts += ["--worktree", worktree]
    claude_cmd = " ".join(shlex.quote(p) for p in cmd_parts)

    # Pre-accept the project trust dialog in ~/.claude.json so Claude
    # doesn't show the "Is this a project you trust?" prompt that blocks
    # the TUI from starting.  This dialog appears on first launch in any
    # directory that hasn't been explicitly trusted yet.
    _preflight_claude_project(proj.path)

    # Kill stale tmux session if it already exists (e.g. stuck from a prior run)
    subprocess.run(
        ["tmux", "kill-session", "-t", tmux_session],
        capture_output=True,  # suppress "no such session" errors
    )
    # Create a new detached tmux session for this agent
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", tmux_session,
         "-c", proj.path],
        check=True,
    )
    pane_id = subprocess.run(
        ["tmux", "display-message", "-t", tmux_session, "-p", "#{pane_id}"],
        capture_output=True, text=True,
    ).stdout.strip()

    # Clear environment vars in the tmux session:
    # - CLAUDECODE / CLAUDE_CODE_ENTRYPOINT: prevents nesting detection
    # - AGENTHIVE_MANAGED: clean up inherited env from systemd service
    # Note: _is_orchestrator_process() distinguishes orchestrator subprocesses
    # from tmux agents by checking for the -p flag in /proc/pid/cmdline.
    subprocess.run(
        ["tmux", "send-keys", "-t", pane_id,
         "unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT AGENTHIVE_MANAGED", "Enter"],
        check=True,
    )

    # Start Claude interactively (full TUI — user can attach and interact)
    subprocess.run(
        ["tmux", "send-keys", "-t", pane_id, claude_cmd, "Enter"],
        check=True,
    )

    # Create Agent record immediately so the frontend can navigate to it.
    agent_name = (prompt or "CLI session")[:80]
    resolved_model = model or proj.default_model
    if resolved_model not in VALID_MODELS:
        logger.warning("Invalid model %r for tmux agent, falling back to %s", resolved_model, CC_MODEL)
        resolved_model = CC_MODEL
    agent = Agent(
        id=agent_hex,
        project=project_name,
        name=agent_name,
        mode=AgentMode.AUTO,
        status=AgentStatus.STARTING,
        model=resolved_model,
        cli_sync=True,
        tmux_pane=pane_id,
        effort=effort if effort else None,
        worktree=worktree if worktree else None,
        skip_permissions=skip_permissions,
        task_id=task_id if task_id else None,
        last_message_preview=agent_name,
        last_message_at=datetime.now(timezone.utc),
    )
    db.add(agent)
    db.flush()

    # Link task → agent if task_id provided
    if task_id:
        _task = db.get(Task, task_id)
        if _task and can_transition(_task.status, TaskStatus.EXECUTING):
            _task.agent_id = agent.id
            _task.status = TaskStatus.EXECUTING
            _task.started_at = datetime.now(timezone.utc)
            _task.worktree_name = worktree if worktree else None
            if worktree:
                _task.branch_name = _task.branch_name or f"worktree-{worktree}"

    # Save the initial prompt as a user message so it shows in the chat
    if prompt:
        msg = Message(
            agent_id=agent.id,
            role=MessageRole.USER,
            content=prompt,
            status=MessageStatus.COMPLETED,
            source="web",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(msg)

    db.commit()
    db.refresh(agent)

    # Schedule background task: wait for Claude TUI to load, send prompt,
    # detect session JSONL, and start sync.
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad and prompt:
        launch_task = asyncio.ensure_future(
            _launch_tmux_background(ad, agent.id, pane_id, prompt, proj.path)
        )
        ad.track_launch_task(agent.id, launch_task)

    logger.info(
        "Launched tmux claude session in pane %s for project %s (agent %s)",
        pane_id, project_name, agent.id,
    )
    return AgentOut.model_validate(agent)


async def _launch_tmux_background(
    ad, agent_id: str, pane_id: str, prompt: str, project_path: str,
):
    """Background task for tmux agent launch.

    1. Wait for Claude's TUI to start (polls for a claude process in the pane)
    2. Send the user prompt
    3. Detect the session JSONL and start the sync loop

    On any failure, transitions the agent to ERROR so it doesn't stay
    stuck in STARTING forever.  Handles cancellation gracefully so that
    stopping the agent while the launch is in progress doesn't leave
    zombie error transitions.
    """
    import subprocess

    from agent_dispatcher import (
        _build_tmux_claude_map,
        _detect_pid_session_jsonl,
        send_tmux_message,
    )
    from database import SessionLocal
    from session_cache import session_source_dir
    from websocket import emit_agent_update, emit_new_message

    def _mark_error(reason: str):
        """Transition agent to ERROR status on launch failure."""
        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            if agent and agent.status != AgentStatus.STOPPED:
                agent.status = AgentStatus.ERROR
                agent.tmux_pane = None  # release pane so discovery doesn't conflict
                db.commit()
                ad._emit(emit_agent_update(agent_id, "ERROR", agent.project))
        finally:
            db.close()
        logger.warning("tmux launch failed for agent %s: %s", agent_id, reason)

    await _tmux_launch_sem.acquire()
    try:
        # Step 1: Wait for Claude's TUI to fully load (up to 30s).
        # Two phases:
        #   a) Detect the claude process in the pane
        #   b) Wait for the TUI input prompt (❯) to appear in the pane content
        process_detected = False
        for _ in range(_TUI_STARTUP_TIMEOUT):
            await asyncio.sleep(1)
            pane_map = _build_tmux_claude_map()
            if pane_id in pane_map and not pane_map[pane_id]["is_orchestrator"]:
                process_detected = True
                break
        if not process_detected:
            _mark_error(
                "Claude TUI did not start in pane %s within %ds "
                "(project_path: %s)" % (pane_id, _TUI_STARTUP_TIMEOUT, project_path)
            )
            return

        # Wait for the REPL to be fully mounted.
        # IMPORTANT: The ❯ prompt character appears in the welcome box BEFORE
        # the REPL input handler is mounted.  On first launch in a new project
        # directory, showSetupScreens() takes ~4 seconds (vs ~200ms for
        # established projects).  We use the status bar ("⏵⏵ bypass permissions"
        # or "shift+tab to cycle") as the definitive REPL-mounted signal,
        # since it only renders after the full TUI component tree is ready.
        #
        # Also handles the project trust dialog ("Is this a project you
        # trust?") which can appear despite pre-acceptance if ~/.claude.json
        # was regenerated.  If detected, we press Enter to accept it.
        tui_ready = False
        trust_dialog_handled = False
        for _ in range(_TUI_STARTUP_TIMEOUT):
            await asyncio.sleep(1)
            try:
                capture = subprocess.run(
                    ["tmux", "capture-pane", "-t", pane_id, "-p"],
                    capture_output=True, text=True, timeout=5,
                )
                if capture.returncode != 0:
                    continue
                pane_text = capture.stdout

                # Check for the REPL status bar (definitive ready signal)
                for ln in pane_text.split("\n"):
                    if "\u23f5" in ln and "shift+tab" in ln:
                        tui_ready = True
                        break
                if tui_ready:
                    break

                # Check for the project trust dialog and auto-accept it
                if not trust_dialog_handled and "trust this folder" in pane_text.lower():
                    subprocess.run(
                        ["tmux", "send-keys", "-t", pane_id, "Enter"],
                        capture_output=True, text=True, timeout=5,
                    )
                    trust_dialog_handled = True
                    logger.info(
                        "Auto-accepted project trust dialog in pane %s for agent %s",
                        pane_id, agent_id,
                    )
            except (subprocess.TimeoutExpired, OSError):
                continue
        if not tui_ready:
            _mark_error(
                "Claude TUI did not fully initialize in pane %s within %ds "
                "(project_path: %s)" % (pane_id, _TUI_STARTUP_TIMEOUT, project_path)
            )
            return

        # Extra settle time after REPL mount.  On first-launch projects
        # showSetupScreens() finishes ~200ms before REPL mount; add a buffer
        # to ensure the input handler is fully wired up.
        await asyncio.sleep(_TUI_SETTLE_DELAY)

        # Step 2: Send the prompt, then wait for session JSONL as the
        # definitive acceptance signal.  If the JSONL doesn't appear within
        # a reasonable time, clear the input and re-send.
        #
        # Using session JSONL creation as the acceptance signal is far more
        # reliable than pane-capture heuristics, which are fragile against
        # TUI layout variations and re-render timing.
        from session_cache import invalidate_path_cache
        from agent_dispatcher import _get_session_pid

        actual_cwd = project_path
        try:
            cwd_result = subprocess.run(
                ["tmux", "display-message", "-t", pane_id, "-p", "#{pane_current_path}"],
                capture_output=True, text=True, timeout=5,
            )
            if cwd_result.returncode == 0 and cwd_result.stdout.strip():
                actual_cwd = os.path.realpath(cwd_result.stdout.strip())
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.debug("tmux pane CWD lookup failed for %s: %s", pane_id, e)

        session_dir = session_source_dir(actual_cwd)
        base_session_dir = session_source_dir(project_path)

        def _check_status_bar_processing() -> bool:
            """Check if the status bar shows 'esc to interrupt' — definitive
            indicator that Claude is actively processing."""
            try:
                cap = subprocess.run(
                    ["tmux", "capture-pane", "-t", pane_id, "-p"],
                    capture_output=True, text=True, timeout=5,
                )
                if cap.returncode == 0:
                    for ln in cap.stdout.split("\n"):
                        if "\u23f5" in ln and "esc to interrupt" in ln:
                            return True
            except (subprocess.TimeoutExpired, OSError) as e:
                logger.debug("Status bar check failed for %s: %s", pane_id, e)
            return False

        def _scan_for_session_jsonl(owned_sids: set, pane_pid: int | None) -> str | None:
            """Scan session dirs for the JSONL created by our launch.

            Tiers:
              0. /proc/{pid}/fd scan (works if Claude keeps JSONL open)
              1. Debug-log PID match (legacy Claude Code <2.1.71)
              2. mtime fallback — newest unowned JSONL created after launch
                 (Claude Code >=2.1.71 which has no debug logs and doesn't
                 keep session JSONL fds open)
            """
            if pane_pid:
                # Tier 0: Direct OS check — which JSONL does this process have open?
                sid = _detect_pid_session_jsonl(pane_pid)
                if sid and sid not in owned_sids:
                    return sid

            best_sid, best_mtime = None, launch_start
            for sdir in dict.fromkeys([session_dir, base_session_dir]):
                if not os.path.isdir(sdir):
                    continue
                for fname in os.listdir(sdir):
                    if not fname.endswith(".jsonl"):
                        continue
                    sid = fname.replace(".jsonl", "")
                    if sid in owned_sids:
                        continue
                    # Tier 1: debug-log PID match
                    if pane_pid:
                        session_pid = _get_session_pid(sid)
                        if session_pid == pane_pid:
                            return sid
                    # Tier 2: collect candidates for mtime fallback
                    fpath = os.path.join(sdir, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except OSError:
                        continue
                    if mtime > best_mtime:
                        best_sid, best_mtime = sid, mtime

            return best_sid

        # Collect session IDs already owned by other agents (once, reused)
        db_check = SessionLocal()
        try:
            owned_sids = set()
            for a in db_check.query(Agent).filter(
                Agent.session_id.is_not(None),
                Agent.id != agent_id,
            ).all():
                owned_sids.add(a.session_id)
        finally:
            db_check.close()

        pane_pid = None
        pane_map = _build_tmux_claude_map()
        if pane_id in pane_map:
            pane_pid = pane_map[pane_id].get("pid")

        import time as _time
        launch_start = _time.time()
        session_id = None

        for attempt in range(_MAX_SEND_ATTEMPTS):
            # Clear any leftover text from a prior failed attempt
            if attempt > 0:
                subprocess.run(
                    ["tmux", "send-keys", "-t", pane_id, "C-u"],
                    capture_output=True, text=True, timeout=5,
                )
                # Increasing back-off between retries: 3s, 5s, 7s, 9s
                await asyncio.sleep(1 + attempt * 2)

            if not send_tmux_message(pane_id, prompt):
                _mark_error(
                    "Failed to send prompt to tmux pane %s "
                    "(project_path: %s)" % (pane_id, project_path)
                )
                return

            logger.info(
                "tmux launch agent %s: prompt sent (attempt %d/%d)",
                agent_id, attempt + 1, _MAX_SEND_ATTEMPTS,
            )

            # Poll for evidence that Claude accepted the prompt:
            # 1. Status bar shows "esc to interrupt" (processing), or
            # 2. Session JSONL file appears (definitive)
            for i in range(_JSONL_POLL_PER_ATTEMPT):
                await asyncio.sleep(1)

                # Refresh PID if not yet known
                if not pane_pid:
                    pane_map = _build_tmux_claude_map()
                    if pane_id in pane_map:
                        pane_pid = pane_map[pane_id].get("pid")

                # Quick check: is Claude processing?
                if i < 5 and _check_status_bar_processing():
                    logger.info(
                        "tmux launch agent %s: status bar confirms processing",
                        agent_id,
                    )

                # Invalidate path cache periodically to pick up new dirs
                if i in (5, 10):
                    invalidate_path_cache(actual_cwd)
                    invalidate_path_cache(project_path)
                    session_dir = session_source_dir(actual_cwd)
                    base_session_dir = session_source_dir(project_path)

                try:
                    session_id = _scan_for_session_jsonl(owned_sids, pane_pid)
                except OSError:
                    continue
                if session_id:
                    break

            if session_id:
                break

            # No JSONL after polling — check if the pane still has Claude
            pane_map = _build_tmux_claude_map()
            if pane_id not in pane_map:
                _mark_error(
                    "Claude process disappeared from pane %s during launch "
                    "(project_path: %s)" % (pane_id, project_path)
                )
                return

            logger.info(
                "tmux launch agent %s: no session JSONL after attempt %d/%d, "
                "will retry",
                agent_id, attempt + 1, _MAX_SEND_ATTEMPTS,
            )

        if not session_id:
            _mark_error(
                "No session JSONL appeared for agent %s after %d send attempts "
                "(session_dir: %s, project_path: %s)"
                % (agent_id, _MAX_SEND_ATTEMPTS, session_dir, project_path)
            )
            return

        # Update agent with session_id and transition to SYNCING
        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            if not agent or agent.status == AgentStatus.STOPPED:
                return
            # Final guard: verify no other agent grabbed this session
            # in the meantime (race protection)
            existing = db.query(Agent).filter(
                Agent.session_id == session_id,
                Agent.id != agent_id,
            ).first()
            if existing:
                logger.warning(
                    "Session %s already owned by agent %s — "
                    "cannot assign to agent %s",
                    session_id[:12], existing.id, agent_id,
                )
                _mark_error(
                    "Session %s already owned by another agent" % session_id[:12]
                )
                return
            agent.session_id = session_id
            agent.status = AgentStatus.SYNCING
            db.commit()

            ad._emit(emit_agent_update(agent_id, "SYNCING", agent.project))
        finally:
            db.close()

        # Start the session sync loop — use actual_cwd so worktree agents
        # watch the correct session directory
        ad.start_session_sync(agent_id, session_id, actual_cwd)
        logger.info(
            "Started sync for launched tmux agent %s (session %s)",
            agent_id, session_id[:12],
        )
    except asyncio.CancelledError:
        logger.info("Launch task cancelled for agent %s", agent_id)
    finally:
        _tmux_launch_sem.release()
        ad._launch_tasks.pop(agent_id, None)


@app.post("/api/agents/scan")
async def scan_agents(request: Request, db: Session = Depends(get_db)):
    """Trigger an immediate liveness scan of all agents.

    Runs the same reaping logic as the periodic dispatcher tick, so dead
    CLI agents are marked STOPPED right away instead of waiting ~30s.
    """
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        ad._reap_dead_agents(db)
        db.commit()
    return {"ok": True}


@app.get("/api/agents", response_model=list[AgentBrief])
async def list_agents(
    request: Request,
    project: str | None = None,
    status: AgentStatus | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """List agents with optional filters."""
    q = db.query(Agent).filter(Agent.is_subagent == False)  # noqa: E712
    if project:
        q = q.filter(Agent.project == project)
    if status:
        q = q.filter(Agent.status == status)
    rows = (
        q.order_by(Agent.last_message_at.desc().nulls_last(), Agent.created_at.desc())
        .limit(limit)
        .all()
    )
    # Enrich with live generating state from dispatcher runtime
    ad = getattr(request.app.state, "agent_dispatcher", None)
    generating = ad._generating_agents if ad else set()
    results = []
    for row in rows:
        brief = AgentBrief.model_validate(row)
        if row.id in generating:
            brief.is_generating = True
        results.append(brief)
    return results


@app.get("/api/agents/unread")
async def agents_unread_count(db: Session = Depends(get_db)):
    """Total unread message count across the top 50 agents (matching list limit)."""
    top = (
        db.query(Agent.unread_count)
        .filter(Agent.is_subagent == False)  # noqa: E712
        .order_by(Agent.last_message_at.desc().nulls_last(), Agent.created_at.desc())
        .limit(50)
        .all()
    )
    total = sum(r[0] for r in top if r[0])
    return {"unread": int(total)}


@app.get("/api/messages/search", response_model=MessageSearchResponse)
async def search_messages(
    q: str,
    project: str | None = None,
    role: MessageRole | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Full-text search across all message content."""
    if len(q) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    if limit > 200:
        limit = 200

    # Escape LIKE wildcards in user input
    safe_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    from sqlalchemy import or_
    query = (
        db.query(Message, Agent.name, Agent.project)
        .join(Agent, Message.agent_id == Agent.id)
        .filter(or_(
            Message.content.ilike(f"%{safe_q}%", escape="\\"),
            Agent.id.ilike(f"%{safe_q}%", escape="\\"),
            Agent.name.ilike(f"%{safe_q}%", escape="\\"),
        ))
    )
    if project:
        query = query.filter(Agent.project == project)
    if role:
        query = query.filter(Message.role == role)

    total = query.count()
    rows = query.order_by(Message.created_at.desc()).limit(limit).all()

    results = []
    for msg, agent_name, agent_project in rows:
        # Build snippet: ~80 chars before and after first match
        content = msg.content or ""
        lower = content.lower()
        idx = lower.find(q.lower())
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(content), idx + len(q) + 80)
            snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
        else:
            snippet = content[:160] + ("..." if len(content) > 160 else "")

        results.append(MessageSearchResult(
            message_id=msg.id,
            agent_id=msg.agent_id,
            agent_name=agent_name,
            project=agent_project,
            role=msg.role,
            content_snippet=snippet,
            created_at=msg.created_at,
        ))

    return MessageSearchResponse(results=results, total=total)


@app.get("/api/agents/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Get full agent details."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Compute live session file size + successor link
    result = AgentOut.model_validate(agent)
    result.successor_id = _compute_successor_id(agent.id, db)
    if agent.session_id:
        project = db.get(Project, agent.project)
        if project:
            from agent_dispatcher import _resolve_session_jsonl
            jsonl_path = _resolve_session_jsonl(
                agent.session_id, project.path, agent.worktree,
            )
            try:
                result.session_size_bytes = os.path.getsize(jsonl_path)
            except OSError:
                pass
    # Enrich with live generating state from dispatcher runtime
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad and agent.id in ad._generating_agents:
        result.is_generating = True

    # Attach child subagents
    child_rows = db.query(Agent).filter(
        Agent.parent_id == agent.id,
        Agent.is_subagent == True,  # noqa: E712
    ).order_by(Agent.created_at).all()
    if child_rows:
        result.subagents = [AgentBrief.model_validate(r) for r in child_rows]

    return result


@app.delete("/api/agents/{agent_id}", response_model=AgentOut)
async def stop_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Stop an agent — marks STOPPED."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == AgentStatus.STOPPED:
        raise HTTPException(status_code=400, detail="Agent is already stopped")

    # Kill the tmux pane if this is a CLI-synced agent
    if agent.cli_sync and agent.tmux_pane:
        import subprocess as _sp
        pane = agent.tmux_pane
        try:
            # Send Ctrl-C to interrupt Claude, then close the pane
            _sp.run(["tmux", "send-keys", "-t", pane, "C-c"], capture_output=True, timeout=_TMUX_CMD_TIMEOUT)
            _sp.run(["tmux", "send-keys", "-t", pane, "C-c"], capture_output=True, timeout=_TMUX_CMD_TIMEOUT)
            _sp.run(["tmux", "kill-pane", "-t", pane], capture_output=True, timeout=_TMUX_CMD_TIMEOUT)
            logger.info("Killed tmux pane %s for agent %s", pane, agent.id)
        except Exception:
            logger.warning("Failed to kill tmux pane %s for agent %s", pane, agent.id, exc_info=True)

    agent.status = AgentStatus.STOPPED
    agent.tmux_pane = None

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

    # Cancel any active sync or launch tasks and clear retry state
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        ad._cancel_sync_task(agent.id)
        ad._cancel_launch_task(agent.id)
        ad._stale_session_retries.pop(agent.id, None)
        ad._known_subagents.pop(agent.id, None)

    # Cascade stop to child subagents
    child_subs = db.query(Agent).filter(
        Agent.parent_id == agent.id,
        Agent.is_subagent == True,  # noqa: E712
        Agent.status != AgentStatus.STOPPED,
    ).all()
    for sub in child_subs:
        sub.status = AgentStatus.STOPPED
        if ad:
            ad._cancel_sync_task(sub.id)
            ad._cancel_launch_task(sub.id)
            ad._stale_session_retries.pop(sub.id, None)
        if sub.tmux_pane:
            try:
                _sp.run(["tmux", "kill-pane", "-t", sub.tmux_pane],
                        capture_output=True, timeout=_TMUX_CMD_TIMEOUT)
            except Exception:
                logger.debug("Failed to kill tmux pane for subagent %s", sub.id)

    db.commit()
    db.refresh(agent)
    asyncio.ensure_future(emit_agent_update(agent.id, "STOPPED", agent.project))
    logger.info("Agent %s stopped", agent.id)
    return agent


@app.delete("/api/agents/{agent_id}/permanent")
async def permanently_delete_agent(agent_id: str, db: Session = Depends(get_db)):
    """Permanently delete an agent, its messages, session JSONL, and output logs."""
    from session_cache import cleanup_source_session, evict_session

    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
        raise HTTPException(status_code=400, detail="Agent must be stopped before deleting")

    cleaned_files = []

    # 1. Delete session source files (.jsonl + subdir) and cache
    if agent.session_id:
        project = db.query(Project).filter(Project.name == agent.project).first()
        if project:
            if cleanup_source_session(agent.session_id, project.path, agent.worktree):
                cleaned_files.append(f"{agent.session_id}.jsonl")
            evict_session(agent.session_id, project.path)

    # 2. Delete output log files for all messages
    msg_ids = [m.id for m in db.query(Message.id).filter(Message.agent_id == agent_id).all()]
    for mid in msg_ids:
        log_path = f"/tmp/claude-output-{mid}.log"
        if os.path.isfile(log_path):
            try:
                os.remove(log_path)
                cleaned_files.append(log_path)
            except OSError as e:
                logger.warning("Failed to delete output log %s: %s", log_path, e)

    # 3. Delete DB records
    deleted_msgs = db.query(Message).filter(Message.agent_id == agent_id).delete()
    db.delete(agent)
    db.commit()
    logger.info("Permanently deleted agent %s (%d messages, %d files cleaned)",
                agent_id, deleted_msgs, len(cleaned_files))
    return {"detail": "ok", "deleted_messages": deleted_msgs, "cleaned_files": len(cleaned_files)}


@app.post("/api/agents/{agent_id}/resume", response_model=AgentOut)
async def resume_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Resume a stopped or errored agent."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
        raise HTTPException(status_code=400, detail="Agent is already running")

    # Block resume if this agent was superseded by a successor (not subagents)
    successor = db.query(Agent).filter(
        Agent.parent_id == agent.id,
        Agent.is_subagent == False,
    ).order_by(Agent.created_at.desc()).first()
    if successor:
        raise HTTPException(
            status_code=409,
            detail=json.dumps({
                "reason": "superseded",
                "successor_id": successor.id,
                "successor_name": successor.name,
                "message": "This agent was continued by a new agent. Open the successor instead.",
            }),
        )

    project = db.get(Project, agent.project)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.archived:
        raise HTTPException(status_code=400, detail="Cannot resume agents for archived projects — activate first")

    wm = getattr(request.app.state, "worker_manager", None)
    if not wm:
        raise HTTPException(status_code=500, detail="Worker manager not available")

    # Parse optional body for cli_sync resume mode
    body = {}
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        pass  # Empty body or no content-type — use defaults
    resume_mode = body.get("mode")  # "tmux" | "normal" | None

    try:
        wm.ensure_project_ready(project)

        # Clear stale session retry counter so resumed agents get
        # full retry budget for session recovery
        ad = getattr(request.app.state, "agent_dispatcher", None)
        if ad:
            ad._stale_session_retries.pop(agent.id, None)

        resumed_sync = False

        if agent.cli_sync and resume_mode == "normal":
            # Convert to normal (non-sync) agent
            agent.cli_sync = False
            agent.tmux_pane = None
            agent.status = AgentStatus.IDLE
        elif agent.cli_sync and resume_mode == "tmux":
            # Launch a new tmux session and resume the CLI session in it
            import shlex
            import subprocess
            from config import CLAUDE_BIN

            cmd_parts = [CLAUDE_BIN,
                          "--output-format", "stream-json", "--verbose"]
            if agent.skip_permissions:
                cmd_parts.append("--dangerously-skip-permissions")
            if agent.model:
                cmd_parts += ["--model", agent.model]
            if agent.session_id:
                cmd_parts += ["--resume", agent.session_id]
            claude_cmd = " ".join(shlex.quote(p) for p in cmd_parts)

            tmux_session = f"ah-{agent.id[:8]}"
            _preflight_claude_project(project.path)

            # Kill stale tmux session if it already exists (e.g. stuck from a prior run)
            subprocess.run(
                ["tmux", "kill-session", "-t", tmux_session],
                capture_output=True, timeout=_TMUX_CMD_TIMEOUT,
            )

            subprocess.run(
                ["tmux", "new-session", "-d", "-s", tmux_session,
                 "-c", project.path],
                check=True, timeout=_TMUX_CMD_TIMEOUT,
            )
            pane_id = subprocess.run(
                ["tmux", "display-message", "-t", tmux_session, "-p", "#{pane_id}"],
                capture_output=True, text=True, timeout=_TMUX_CMD_TIMEOUT,
            ).stdout.strip()

            subprocess.run(
                ["tmux", "send-keys", "-t", pane_id,
                 "unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT AGENTHIVE_MANAGED", "Enter"],
                check=True, timeout=_TMUX_CMD_TIMEOUT,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", pane_id, claude_cmd, "Enter"],
                check=True, timeout=_TMUX_CMD_TIMEOUT,
            )

            agent.tmux_pane = pane_id
            agent.status = AgentStatus.SYNCING
            if agent.session_id and ad:
                ad.start_session_sync(agent.id, agent.session_id, project.path)
            resumed_sync = True
        elif agent.cli_sync and ad:
            # Default: try to re-establish sync with existing tmux pane
            from agent_dispatcher import _detect_tmux_pane_for_session, _resolve_session_jsonl
            from session_cache import session_source_dir

            sid = agent.session_id

            # If session_id was never assigned (e.g. tmux launch failed
            # before detecting the JSONL), discover it from the project's
            # session directory by picking the most recently modified file.
            # Check both project root and worktree session dirs.
            if not sid:
                sdirs = [session_source_dir(project.path)]
                if agent.worktree:
                    wt_path = os.path.join(project.path, ".claude", "worktrees", agent.worktree)
                    wt_sdir = session_source_dir(wt_path)
                    if os.path.isdir(wt_sdir) and wt_sdir not in sdirs:
                        sdirs.append(wt_sdir)
                best, best_mtime = None, 0.0
                for sdir in sdirs:
                    if not os.path.isdir(sdir):
                        continue
                    try:
                        for fname in os.listdir(sdir):
                            if not fname.endswith(".jsonl"):
                                continue
                            fpath = os.path.join(sdir, fname)
                            mt = os.path.getmtime(fpath)
                            if mt > best_mtime:
                                best, best_mtime = fname.replace(".jsonl", ""), mt
                    except OSError as e:
                        logger.warning(
                            "resume_agent: failed to scan session dir %s for agent %s: %s",
                            sdir, agent.id, e,
                        )
                if best:
                    sid = best
                    agent.session_id = sid
                    logger.info(
                        "Discovered session %s for agent %s on resume",
                        sid, agent.id,
                    )

            if sid:
                jsonl_path = _resolve_session_jsonl(sid, project.path, agent.worktree)
                if os.path.exists(jsonl_path) and not ad._session_has_ended(jsonl_path):
                    pane = _detect_tmux_pane_for_session(sid, project.path)
                    agent.status = AgentStatus.SYNCING
                    agent.tmux_pane = pane  # may be None; sync loop will retry
                    ad.start_session_sync(agent.id, sid, project.path)
                    resumed_sync = True

        if not resumed_sync and agent.status not in (AgentStatus.IDLE, AgentStatus.SYNCING):
            agent.status = AgentStatus.IDLE

        msg = Message(
            agent_id=agent.id,
            role=MessageRole.SYSTEM,
            content="Agent resumed" + (" — syncing CLI session" if resumed_sync else ""),
            status=MessageStatus.COMPLETED,
        )
        db.add(msg)
        db.commit()
        db.refresh(agent)
        logger.info("Agent %s resumed (sync=%s, mode=%s)", agent.id, resumed_sync, resume_mode)
        return agent
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to resume agent %s", agent.id)
        raise HTTPException(status_code=500, detail=f"Failed to verify project directory: {e}")


@app.put("/api/agents/read-all")
async def mark_all_agents_read(db: Session = Depends(get_db)):
    """Mark all agents as read (reset unread count for every agent)."""
    count = db.query(Agent).filter(Agent.unread_count > 0).update({"unread_count": 0})
    db.commit()
    return {"detail": "ok", "updated": count}


@app.put("/api/agents/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: str, request: Request, db: Session = Depends(get_db)):
    """Update agent properties (currently: name)."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    body = await request.json()
    if "name" in body:
        name = str(body["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        if len(name) > 200:
            raise HTTPException(status_code=400, detail="Name too long (max 200)")
        agent.name = name
    if "muted" in body:
        agent.muted = bool(body["muted"])
    db.commit()
    db.refresh(agent)
    return agent


@app.get("/api/agents/{agent_id}/messages", response_model=PaginatedMessages)
async def get_agent_messages(
    agent_id: str,
    limit: int = 50,
    before: str | None = None,
    after: str | None = None,
    db: Session = Depends(get_db),
):
    """Get conversation messages for an agent with cursor pagination.

    - No cursor (initial load): newest `limit` messages, oldest-first.
    - `before=<ISO datetime>`: messages older than cursor (scroll-up).
    - `after=<ISO datetime>`: messages newer than cursor (incremental refresh).
    Returns { messages: [...], has_more: bool }.
    """
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    query = db.query(Message).filter(Message.agent_id == agent_id)

    if before:
        cursor_dt = datetime.fromisoformat(before)
        rows = (
            query.filter(Message.created_at < cursor_dt)
            .order_by(Message.created_at.desc())
            .limit(limit + 1)
            .all()
        )
        has_more = len(rows) > limit
        messages = rows[:limit][::-1]
    elif after:
        cursor_dt = datetime.fromisoformat(after)
        messages = (
            query.filter(Message.created_at > cursor_dt)
            .order_by(Message.created_at.asc())
            .all()
        )
        has_more = False  # always returns everything newer
    else:
        # Default: newest `limit` messages
        rows = (
            query.order_by(Message.created_at.desc())
            .limit(limit + 1)
            .all()
        )
        has_more = len(rows) > limit
        messages = rows[:limit][::-1]
        # Reset unread count only on initial load
        if agent.unread_count > 0:
            agent.unread_count = 0
            db.commit()

    return PaginatedMessages(messages=messages, has_more=has_more)


@app.post("/api/agents/{agent_id}/messages", response_model=MessageOut, status_code=201)
async def send_agent_message(
    agent_id: str,
    body: SendMessage,
    request: Request,
    db: Session = Depends(get_db),
):
    """Send a follow-up message to an agent."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status == AgentStatus.STOPPED:
        raise HTTPException(status_code=400, detail="Agent is stopped")

    # SYNCING/STARTING agents with a tmux pane: send directly via tmux
    is_syncing_with_tmux = (
        agent.status in (AgentStatus.SYNCING, AgentStatus.STARTING)
        and agent.tmux_pane
        and not body.queue
        and not body.scheduled_at
    )
    if is_syncing_with_tmux:
        from agent_dispatcher import (
            _detect_tmux_pane_for_session,
            send_tmux_message,
            verify_tmux_pane,
        )
        from websocket import emit_new_message

        if not verify_tmux_pane(agent.tmux_pane):
            # Transient tmux lookup failures are common during restarts/races.
            # Try to recover pane from session_id before falling back to queue.
            recovered_pane = None
            if agent.session_id:
                project = db.get(Project, agent.project)
                if project:
                    candidate = _detect_tmux_pane_for_session(agent.session_id, project.path)
                    if candidate and verify_tmux_pane(candidate):
                        recovered_pane = candidate

            if recovered_pane:
                agent.tmux_pane = recovered_pane
                db.commit()
            else:
                agent.tmux_pane = None
                db.commit()
                is_syncing_with_tmux = False

        if is_syncing_with_tmux:
            ok = send_tmux_message(agent.tmux_pane, body.content)
            if not ok:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to send via tmux",
                )

            # Record the message as completed (it was sent directly)
            msg = Message(
                agent_id=agent.id,
                role=MessageRole.USER,
                content=body.content,
                status=MessageStatus.COMPLETED,
                source="web",
                completed_at=_utcnow(),
            )
            db.add(msg)
            agent.last_message_preview = body.content[:200]
            agent.last_message_at = _utcnow()
            db.commit()
            db.refresh(msg)
            ad = getattr(request.app.state, "agent_dispatcher", None)
            if ad:
                ad._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))
            logger.info("Message %s sent to agent %s via tmux pane %s", msg.id, agent.id, agent.tmux_pane)
            return msg

    # SYNCING agents WITHOUT a tmux pane are dispatched via subprocess
    # (same as IDLE), so they should accept messages directly.
    is_syncing_no_pane = agent.status == AgentStatus.SYNCING and not agent.tmux_pane
    is_busy = agent.status in (AgentStatus.EXECUTING, AgentStatus.SYNCING) and not is_syncing_no_pane
    if is_busy and not body.queue:
        raise HTTPException(status_code=400, detail="Agent is busy — use send later to queue")

    scheduled_at = None
    if body.scheduled_at:
        from datetime import datetime, timezone
        try:
            scheduled_at = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled_at format")

    msg = Message(
        agent_id=agent.id,
        role=MessageRole.USER,
        content=body.content,
        status=MessageStatus.PENDING,
        source="web",
        scheduled_at=scheduled_at,
    )
    db.add(msg)

    # Update agent preview
    agent.last_message_preview = body.content[:200]
    agent.last_message_at = _utcnow()

    db.commit()
    db.refresh(msg)
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if ad:
        from websocket import emit_new_message
        ad._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))
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


@app.delete("/api/agents/{agent_id}/messages/{message_id}")
async def cancel_message(agent_id: str, message_id: str, db: Session = Depends(get_db)):
    """Cancel a pending/scheduled message. Only allowed if status is PENDING."""
    msg = db.get(Message, message_id)
    if not msg or msg.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.status != MessageStatus.PENDING:
        raise HTTPException(status_code=400, detail="Only PENDING messages can be cancelled")
    db.delete(msg)
    db.commit()
    logger.info("Message %s cancelled for agent %s", message_id, agent_id)
    from websocket import emit_message_update
    await emit_message_update(agent_id, message_id, "CANCELLED")
    return {"detail": "Message cancelled"}


@app.put("/api/agents/{agent_id}/messages/{message_id}", response_model=MessageOut)
async def update_message(
    agent_id: str,
    message_id: str,
    body: UpdateMessage,
    db: Session = Depends(get_db),
):
    """Update content and/or scheduled_at of a PENDING message."""
    msg = db.get(Message, message_id)
    if not msg or msg.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.status != MessageStatus.PENDING:
        raise HTTPException(status_code=400, detail="Only PENDING messages can be updated")

    if body.content is not None:
        if not body.content.strip():
            raise HTTPException(status_code=400, detail="Content cannot be empty")
        msg.content = body.content.strip()

    if body.scheduled_at is not None:
        if body.scheduled_at == "":
            # Clear scheduled_at (convert to immediate pending)
            msg.scheduled_at = None
        else:
            try:
                msg.scheduled_at = datetime.fromisoformat(
                    body.scheduled_at.replace("Z", "+00:00")
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid scheduled_at format")

    db.commit()
    db.refresh(msg)
    logger.info("Message %s updated for agent %s", message_id, agent_id)
    return msg


# ---- Interactive Answer (AskUserQuestion / ExitPlanMode via tmux) ----

class AnswerPayload(BaseModel):
    tool_use_id: str
    type: str  # "ask_user_question" or "exit_plan_mode"
    selected_index: int | None = None  # 0-based option index (AskUserQuestion)
    question_index: int = 0  # which question in multi-Q AskUserQuestion
    approved: bool | None = None  # (ExitPlanMode only)


_PLAN_LABELS = [
    "Yes, clear context & bypass",
    "Yes, bypass permissions",
    "Yes, manual approval",
    "Give feedback",
]


def _patch_interactive_answer(
    db: Session, agent_id: str, tool_use_id: str,
    selected_index: int, answer_type: str,
    question_index: int = 0,
):
    """Immediately mark an interactive item as answered in the DB.

    Builds an answer string from the selected option so the frontend can
    render the selection without waiting for the sync loop to pick up the
    tool_result from the session JSONL.

    For multi-question AskUserQuestion, each call patches one question at a
    time via question_index, accumulating into selected_indices and answer.
    """
    msgs = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.meta_json.is_not(None),
    ).order_by(Message.created_at.desc()).limit(10).all()

    for msg in msgs:
        try:
            meta = json.loads(msg.meta_json)
        except (json.JSONDecodeError, TypeError):
            continue
        items = meta.get("interactive")
        if not items:
            continue
        for item in items:
            if item.get("tool_use_id") != tool_use_id:
                continue
            # Don't overwrite a dismissed/rejected answer
            existing_answer = item.get("answer") or ""
            if isinstance(existing_answer, str) and (
                existing_answer.startswith("The user doesn't want to proceed")
                or existing_answer.startswith("User declined")
                or existing_answer.startswith("Tool use rejected")
            ):
                return
            if answer_type == "ask_user_question":
                # Per-question check: skip if this specific question already answered
                sel_indices = item.get("selected_indices", {})
                if sel_indices.get(str(question_index)) is not None:
                    return
                # Store per-question index
                sel_indices[str(question_index)] = selected_index
                item["selected_indices"] = sel_indices
                # Backward compat: also set selected_index for Q0
                if question_index == 0:
                    item["selected_index"] = selected_index
                # Build answer string for this question
                questions = item.get("questions", [])
                if questions and question_index < len(questions):
                    q = questions[question_index]
                    options = q.get("options", [])
                    label = options[selected_index]["label"] if selected_index < len(options) else str(selected_index)
                    part = f'"{q.get("question", "")}"="{label}"'
                else:
                    part = str(selected_index)
                # Append to existing answer (multi-question accumulation)
                existing = item.get("answer")
                if existing and isinstance(existing, str):
                    item["answer"] = existing + "\n" + part
                else:
                    item["answer"] = part
            elif answer_type == "exit_plan_mode":
                if item.get("answer") is not None:
                    return  # Already answered
                item["selected_index"] = selected_index
                item["answer"] = _PLAN_LABELS[selected_index] if selected_index < len(_PLAN_LABELS) else str(selected_index)
            msg.meta_json = json.dumps(meta)
            db.commit()
            return


def _count_interactive_questions(db: Session, agent_id: str, tool_use_id: str) -> int:
    """Return the total number of questions for an interactive item."""
    msgs = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.meta_json.is_not(None),
    ).order_by(Message.created_at.desc()).limit(50).all()
    for msg in msgs:
        try:
            meta = json.loads(msg.meta_json)
        except (json.JSONDecodeError, TypeError):
            continue
        for item in meta.get("interactive", []):
            if item.get("tool_use_id") == tool_use_id:
                return len(item.get("questions", []))
    return 1


@app.post("/api/agents/{agent_id}/answer")
async def answer_agent_interactive(
    agent_id: str,
    body: AnswerPayload,
    db: Session = Depends(get_db),
):
    """Answer an AskUserQuestion or approve/reject ExitPlanMode via tmux keys."""
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.status not in (AgentStatus.SYNCING, AgentStatus.EXECUTING, AgentStatus.IDLE):
        raise HTTPException(status_code=400, detail=f"Agent is {agent.status}, not in interactive state")

    # Non-tmux agents (e.g. skip_permissions agents without a pane): patch DB only.
    # Claude auto-approves with --dangerously-skip-permissions, so the card is informational.
    has_tmux = bool(agent.tmux_pane)
    if has_tmux:
        from agent_dispatcher import send_tmux_keys, verify_tmux_pane
        if not verify_tmux_pane(agent.tmux_pane):
            raise HTTPException(status_code=400, detail="Tmux pane no longer exists")

    pane_id = agent.tmux_pane
    MAX_INDEX = 20  # safety cap to prevent excessive keystrokes

    if body.type == "ask_user_question":
        if body.selected_index is None or body.selected_index < 0:
            raise HTTPException(status_code=400, detail="selected_index required for ask_user_question")
        if body.selected_index > MAX_INDEX:
            raise HTTPException(status_code=400, detail=f"selected_index too large (max {MAX_INDEX})")

        if has_tmux:
            # Send tmux keys FIRST — only patch DB on success (Bug 6 race fix)
            keys = ["Down"] * body.selected_index + ["Enter"]
            if not send_tmux_keys(pane_id, keys):
                raise HTTPException(status_code=500, detail="Failed to send keys to tmux")
        else:
            keys = []

        # Patch DB after successful key delivery (or immediately for non-tmux)
        _patch_interactive_answer(db, agent_id, body.tool_use_id, body.selected_index, body.type, body.question_index)

        if has_tmux:
            # Multi-question TUI: after the last question, Claude Code shows a
            # "Review your answers → Submit" confirmation screen.  We need to
            # detect when all questions have been answered and send an extra
            # Enter to confirm submission.
            total_questions = _count_interactive_questions(db, agent_id, body.tool_use_id)
            if total_questions > 1 and body.question_index == total_questions - 1:
                import asyncio
                await asyncio.sleep(0.5)  # Wait for TUI to render submit screen
                send_tmux_keys(pane_id, ["Enter"])
                logger.info("Multi-Q submit: sent extra Enter for agent %s (Q%d/%d)",
                            agent_id, body.question_index, total_questions)
                return {"detail": "ok", "keys_sent": len(keys) + 1, "submitted": True}

        return {"detail": "ok", "keys_sent": len(keys), "auto_approved": not has_tmux}

    elif body.type == "exit_plan_mode":
        # Claude Code TUI plan approval options (arrow-navigated):
        # 0: "Yes, clear context and bypass permissions"
        # 1: "Yes, and bypass permissions"
        # 2: "Yes, manually approve edits"
        # 3: "Type here to tell Claude what to change"

        if body.selected_index is not None and body.selected_index >= 0:
            if body.selected_index > MAX_INDEX:
                raise HTTPException(status_code=400, detail=f"selected_index too large (max {MAX_INDEX})")
            keys = ["Down"] * body.selected_index + ["Enter"]
        elif body.approved is True:
            keys = ["Enter"]  # legacy: approve = first option (clear context + bypass)
        elif body.approved is False:
            keys = ["Down", "Down", "Enter"]  # legacy: reject → manual approval (safest)
        else:
            raise HTTPException(status_code=400, detail="selected_index or approved required for exit_plan_mode")
        effective_index = body.selected_index
        if effective_index is None:
            effective_index = 0 if body.approved else 2

        if has_tmux:
            # Capture pane content BEFORE sending keys for diagnostics
            from agent_dispatcher import capture_tmux_pane, _detect_plan_prompt
            pre_content = capture_tmux_pane(pane_id)
            prompt_type = _detect_plan_prompt(pre_content) if pre_content else "unknown"
            logger.info(
                "ExitPlanMode answer for agent %s: prompt_type=%s, selected_index=%s, pre_pane:\n%s",
                agent_id, prompt_type, body.selected_index,
                (pre_content or "")[-2000:],  # last 2000 chars to avoid huge logs
            )

            # Send tmux keys FIRST — only patch DB on success (Bug 6 race fix)
            if not send_tmux_keys(pane_id, keys):
                raise HTTPException(status_code=500, detail="Failed to send keys to tmux")

            # Patch DB immediately after keys succeed — BEFORE any await.
            # The sync loop runs on the same event loop; an await here lets
            # it parse the JSONL (which may already carry a context-clear
            # dismiss answer) and overwrite the metadata while our patch
            # hasn't landed yet.  With answer=null in the DB at that point,
            # _merge_interactive_meta cannot protect the user's selection.
            _patch_interactive_answer(db, agent_id, body.tool_use_id, effective_index, body.type)

            # Capture pane content AFTER sending keys for diagnostics
            import asyncio
            await asyncio.sleep(0.5)
            post_content = capture_tmux_pane(pane_id)
            logger.info(
                "ExitPlanMode post-keys for agent %s: post_pane:\n%s",
                agent_id,
                (post_content or "")[-2000:],
            )
        else:
            prompt_type = "non-tmux"

        # Non-tmux: patch DB immediately (no keys to send)
        if not has_tmux:
            _patch_interactive_answer(db, agent_id, body.tool_use_id, effective_index, body.type)

        return {"detail": "ok", "keys_sent": len(keys) if has_tmux else 0, "prompt_type": prompt_type, "auto_approved": not has_tmux}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown type: {body.type}")


# ---- Escape (send Escape key to tmux) ----

_last_escape: dict[str, float] = {}  # agent_id → timestamp

@app.post("/api/agents/{agent_id}/escape")
async def send_escape_to_agent(agent_id: str, db: Session = Depends(get_db)):
    """Send Escape key to the agent's tmux pane to dismiss interactive prompts."""
    import time

    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.tmux_pane:
        raise HTTPException(status_code=400, detail="Agent has no tmux pane")

    # Rate limit: max 1 Escape per 2 seconds per agent
    now = time.time()
    last = _last_escape.get(agent_id, 0)
    if now - last < 2.0:
        raise HTTPException(status_code=429, detail="Escape rate limited (max 1 per 2s)")
    _last_escape[agent_id] = now

    from agent_dispatcher import send_tmux_keys, verify_tmux_pane
    if not verify_tmux_pane(agent.tmux_pane):
        raise HTTPException(status_code=400, detail="Tmux pane no longer exists")

    if not send_tmux_keys(agent.tmux_pane, ["Escape"]):
        raise HTTPException(status_code=500, detail="Failed to send Escape to tmux")

    logger.info("Sent Escape to agent %s pane %s", agent_id, agent.tmux_pane)
    return {"detail": "ok"}


# ---- Processes ----

@app.get("/api/processes")
async def list_processes_endpoint(request: Request):
    """List running Claude processes (active agent execs)."""
    ad = getattr(request.app.state, "agent_dispatcher", None)
    if not ad:
        return []
    return ad.get_active_processes()

@app.get("/api/workers")
async def list_tracked_processes(request: Request):
    """List all tracked Claude subprocess entries."""
    wm = getattr(request.app.state, "worker_manager", None)
    if not wm:
        return []
    return wm.list_processes()


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
    return gm.get_log(proj.path, limit=limit)


@app.get("/api/git/{project}/status")
async def git_status(project: str, request: Request, db: Session = Depends(get_db)):
    """Get git status (staged, unstaged, untracked) for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_status(proj.path)


@app.get("/api/git/{project}/branches")
async def git_branches(project: str, request: Request, db: Session = Depends(get_db)):
    """Get branches for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_branches(proj.path)


@app.get("/api/git/{project}/worktrees")
async def git_worktrees(project: str, request: Request, db: Session = Depends(get_db)):
    """List git worktrees for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    return gm.get_worktrees(proj.path)


@app.post("/api/git/{project}/merge/{branch:path}")
async def git_merge(project: str, branch: str, request: Request, db: Session = Depends(get_db)):
    """Merge a branch into the current branch for a project."""
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    gm = getattr(request.app.state, "git_manager", None)
    if not gm:
        raise HTTPException(status_code=503, detail="Git manager not available")
    result = gm.merge_branch(proj.path, branch)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result)
    return result


# ---- Files ----

def _serve_file_with_range(full_path: str, media_type: str, request: Request):
    """Return a FileResponse with built-in Range request support.

    Starlette's FileResponse natively handles Accept-Ranges, single-range
    and multi-range 206 responses, If-Range, and Content-Range headers.
    """
    return FileResponse(full_path, media_type=media_type)

@app.get("/api/files/{project}/{path:path}")
async def serve_project_file(project: str, path: str, request: Request, db: Session = Depends(get_db)):
    """Serve a file from a project's directory (images, videos, etc.)."""
    import mimetypes
    from config import PROJECTS_DIR

    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    base_dir = os.path.realpath(proj.path)
    base_name = os.path.basename(base_dir)

    # Normalise the requested path:
    # 1. Strip absolute project-path prefix (Claude sometimes prints full paths)
    # 2. Strip leading project-directory-name prefix (e.g. "splitvla/file.webp"
    #    when the project root is already splitvla/)
    clean = path
    if clean.startswith(base_dir + "/"):
        clean = clean[len(base_dir) + 1:]
    elif clean.startswith(base_name + "/"):
        clean = clean[len(base_name) + 1:]

    full_path = os.path.realpath(os.path.join(base_dir, clean))
    if not full_path.startswith(base_dir + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")

    # Fallback: try the original path as-is if normalised version doesn't exist
    if not os.path.isfile(full_path):
        fallback = os.path.realpath(os.path.join(base_dir, path))
        if fallback.startswith(base_dir + os.sep) and os.path.isfile(fallback):
            full_path = fallback
        else:
            raise HTTPException(status_code=404, detail="File not found")

    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    return _serve_file_with_range(full_path, media_type, request)


# ---- Uploads ----

@app.post("/api/upload")
async def upload_file(request: Request):
    """Upload a file (multipart form data). Returns filename, original_name, path, size."""
    from uuid import uuid4
    from fastapi import UploadFile, File

    form = await request.form()
    file: UploadFile = form.get("file")
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File exceeds 50 MB limit")

    # Sanitize original filename
    original_name = os.path.basename(file.filename or "upload")
    original_name = re.sub(r'[^\w.\- ]', '_', original_name)
    unique_name = f"{uuid4().hex[:12]}_{original_name}"

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    dest = os.path.join(UPLOADS_DIR, unique_name)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _write_bytes(dest, content))

    return {
        "filename": unique_name,
        "original_name": original_name,
        "path": dest,
        "size": len(content),
    }


def _write_bytes(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


@app.get("/api/uploads/{filename}")
async def serve_upload(filename: str, request: Request):
    """Serve an uploaded file."""
    import mimetypes
    safe_name = os.path.basename(filename)
    full_path = os.path.join(UPLOADS_DIR, safe_name)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    return _serve_file_with_range(full_path, media_type, request)


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
