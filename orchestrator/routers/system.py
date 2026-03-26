"""System routes — health, stats, storage, backups, restart, notifications."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import BACKUP_DIR, CLAUDE_HOME, DB_PATH, LOG_DIR, UPLOADS_DIR
from database import SessionLocal, get_db
from models import Agent, AgentStatus, Message, Project, SystemConfig, Task, TaskStatus
from schemas import HealthResponse
from route_helpers import IMPORT_CHECK_TIMEOUT as _IMPORT_CHECK_TIMEOUT, API_REQUEST_TIMEOUT as _API_REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


# ---- Health ----

@router.get("/api/health", response_model=HealthResponse)
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


@router.post("/api/test/notify")
async def test_notify(request: Request):
    """Send a test notification through the notify() gateway.

    Query params:
        channel: notify_at | task_complete | message (default: message)
        agent_id: agent ID to test with (default: "test")
        muted: true/false (default: false)
        in_use: true/false (default: false) — if "auto", uses real _is_agent_in_use()
    """
    params = request.query_params
    channel = params.get("channel", "message")
    agent_id = params.get("agent_id", "test")
    muted = params.get("muted", "false").lower() == "true"
    in_use_param = params.get("in_use", "false")

    # Auto-detect in-use from real signals
    ws_viewed = False
    pane_attached = False
    pane = None
    if in_use_param == "auto" and agent_id != "test":
        dispatcher = getattr(request.app.state, "agent_dispatcher", None)
        if dispatcher:
            from database import SessionLocal
            from models import Agent
            db = SessionLocal()
            try:
                agent = db.get(Agent, agent_id)
                pane = agent.tmux_pane if agent else None
            finally:
                db.close()
            from websocket import ws_manager
            ws_viewed = ws_manager.is_agent_viewed(agent_id)
            dispatcher._refresh_pane_attached()
            pane_attached = bool(pane and dispatcher._pane_attached.get(pane, False))
            in_use = ws_viewed or pane_attached
        else:
            in_use = False
    else:
        in_use = in_use_param.lower() == "true"

    from notify import notify
    decision = notify(channel, agent_id, "AgentHive Test",
           f"Test via {channel} (muted={muted}, in_use={in_use})",
           "/agents", muted=muted, in_use=in_use)

    # Emit debug bubble to frontend
    from websocket import ws_manager
    asyncio.get_event_loop().create_task(ws_manager.broadcast(
        "notification_debug",
        {"agent_id": agent_id, "decision": decision,
         "channel": channel, "body": f"test ({decision})"}
    ))

    return {
        "channel": channel,
        "agent_id": agent_id,
        "muted": muted,
        "in_use": in_use,
        "ws_viewed": ws_viewed,
        "pane_attached": pane_attached,
        "tmux_pane": pane,
        "decision": decision,
        "routed_through": "notify()",
    }


@router.post("/api/debug/frontend-state")
async def frontend_debug_state(request: Request):
    """Receive and log frontend rendered state for debugging."""
    _dbg = logging.getLogger("frontend.debug")
    body = await request.json()
    agent_id = body.get("agentId", "?")[:8]
    page = body.get("page", "?")
    msgs = body.get("messages", [])
    ws_events = body.get("wsEvents", [])

    dom_els = body.get("domElements", [])

    _dbg.info("=== Frontend State: agent=%s page=%s ===", agent_id, page)
    _dbg.info("Data messages: %d | DOM elements: %d", len(msgs), len(dom_els))
    _dbg.info("--- Data (from React state) ---")
    for m in msgs:
        _dbg.info("  %s role=%-6s kind=%-10s seq=%-4s src=%-5s id=%s content=%.80s",
                   m.get("created_at", "?")[:19] if m.get("created_at") else "?",
                   m.get("role", "?"), m.get("kind") or "null",
                   m.get("session_seq", "?"), m.get("source") or "?",
                   m.get("id", "?"), (m.get("content", "") or "")[:80].replace("\n", " "))
    if dom_els:
        _dbg.info("--- DOM (actual rendered elements) ---")
        for el in dom_els:
            _dbg.info("  y=%-5s h=%-4s vis=%-5s type=%-15s id=%s text=%.60s",
                       el.get("y", "?"), el.get("h", "?"),
                       el.get("visible", "?"), el.get("type", "?"),
                       el.get("msgId", "?"),
                       (el.get("text", "") or "")[:60].replace("\n", " "))
    if ws_events:
        _dbg.info("--- WS events (last 20) ---")
        for ev in ws_events[-20:]:
            _dbg.info("  %s", ev)
    _dbg.info("=== End Frontend State ===")
    return {"ok": True}


@router.post("/api/debug/kb-log")
async def kb_debug_log(request: Request):
    """Receive keyboard viewport debug samples — writes to logs/kb-debug.log."""
    body = await request.json()
    samples = body.get("samples", [])
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs")
    log_path = os.path.join(log_dir, "kb-debug.log")
    with open(log_path, "a") as f:
        for s in samples:
            f.write(f"t={s.get('t','?')} cH={s.get('cH','?')} iH={s.get('iH','?')} "
                    f"vvH={s.get('vvH','?')} vvOT={s.get('vvOT','?')} "
                    f"off={s.get('off','?')} open={s.get('open','?')}\n")
    return {"ok": True, "count": len(samples)}


@router.get("/api/system/stats")
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

    return stats


@router.get("/api/system/storage")
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


@router.get("/api/system/orphans/scan")
async def system_orphan_scan():
    """Scan for orphaned session JSONL files and output logs."""
    from orphan_cleanup import scan_orphans

    def _scan():
        result = scan_orphans()
        # Strip file lists from response (only return counts/sizes)
        return {k: v for k, v in result.items()
                if k not in ("orphan_sessions", "orphan_logs", "empty_dirs")}

    return await asyncio.get_event_loop().run_in_executor(None, _scan)


@router.post("/api/system/orphans/clean")
async def system_orphan_clean():
    """Scan and delete orphaned files atomically."""
    from orphan_cleanup import scan_orphans, delete_orphans

    def _clean():
        scan = scan_orphans()
        return delete_orphans(scan)

    return await asyncio.get_event_loop().run_in_executor(None, _clean)


@router.get("/api/system/stale-agents/scan")
async def system_stale_agents_scan(
    max_age_days: int = 30,
    db: Session = Depends(get_db),
):
    """Scan for stale stopped/error agents older than max_age_days."""
    from orphan_cleanup import scan_stale_agents

    result = scan_stale_agents(db, max_age_days=max_age_days)
    # Strip detailed agent list from response (only return counts)
    return {k: v for k, v in result.items() if k not in ("eligible_agents", "orphan_subagent_ids")}


@router.post("/api/system/stale-agents/clean")
async def system_stale_agents_clean(
    max_age_days: int = 30,
    db: Session = Depends(get_db),
):
    """Scan and delete stale agents (stopped/error, older than max_age_days)."""
    from orphan_cleanup import scan_stale_agents, delete_stale_agents

    scan = scan_stale_agents(db, max_age_days=max_age_days)
    return delete_stale_agents(db, scan)


@router.get("/api/system/backup")
async def get_backup_status():
    """Return current backup config, on-disk stats, and backup list."""
    from backup import get_runtime_config, list_backups

    cfg = get_runtime_config()
    backups = list_backups()
    total_bytes = sum(b["total_bytes"] for b in backups)

    return {
        **cfg,
        "backup_dir": BACKUP_DIR,
        "backup_count": len(backups),
        "total_bytes": total_bytes,
        "backups": backups,
    }


@router.post("/api/system/backup")
async def trigger_manual_backup():
    """Trigger a manual backup immediately."""
    from backup import do_backup
    name = do_backup()
    return {"detail": "ok", "name": name}


@router.delete("/api/system/backup")
async def purge_backups():
    """Delete ALL backup snapshots (not session-cache)."""
    import glob as globmod
    import shutil

    backup_dirs = sorted(globmod.glob(os.path.join(BACKUP_DIR, "backup_*")))
    if not backup_dirs:
        return {"detail": "No backups to delete", "deleted": 0, "freed_bytes": 0}

    freed = 0
    deleted = 0
    for d in backup_dirs:
        sz = 0
        for dp, _, files in os.walk(d):
            for f in files:
                try:
                    sz += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        try:
            shutil.rmtree(d)
            freed += sz
            deleted += 1
        except OSError as e:
            logger.warning("Failed to remove backup %s: %s", d, e)

    logger.info("Purged %d backups, freed %d bytes", deleted, freed)
    return {"detail": "ok", "deleted": deleted, "freed_bytes": freed}


@router.delete("/api/system/backup/{name}")
async def delete_single_backup(name: str):
    """Delete a single backup snapshot."""
    from backup import delete_backup
    freed = delete_backup(name)
    if freed == 0:
        raise HTTPException(status_code=404, detail="Backup not found")
    return {"detail": "ok", "freed_bytes": freed}


@router.post("/api/system/backup/{name}/restore")
async def restore_from_backup(name: str):
    """Restore database and registry from a backup snapshot."""
    from backup import restore_backup
    try:
        result = restore_backup(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    return {"detail": "ok", **result}


@router.put("/api/system/backup/config")
async def update_backup_config(request: Request):
    """Update backup schedule config (persists to .env)."""
    from backup import update_runtime_config, persist_env_config, get_runtime_config

    body = await request.json()
    enabled = body.get("enabled")
    interval_hours = body.get("interval_hours")
    max_backups = body.get("max_backups")

    # Validate
    if interval_hours is not None and (not isinstance(interval_hours, int) or interval_hours < 1):
        raise HTTPException(status_code=400, detail="interval_hours must be >= 1")
    if max_backups is not None and (not isinstance(max_backups, int) or max_backups < 1):
        raise HTTPException(status_code=400, detail="max_backups must be >= 1")

    update_runtime_config(enabled=enabled, interval_hours=interval_hours, max_backups=max_backups)

    # Persist to .env
    cfg = get_runtime_config()
    persist_env_config(cfg["enabled"], cfg["interval_hours"], cfg["max_backups"])

    return {"detail": "ok", **cfg}


@router.post("/api/system/backup/import")
async def import_backup_upload(request: Request):
    """Import a backup from an uploaded zip file."""
    import tempfile
    from backup import import_backup

    # Read multipart form data
    form = await request.form()
    file = form.get("file")
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()
        name = import_backup(tmp.name)
        return {"detail": "ok", "name": name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


@router.get("/api/system/backup/{name}/download")
async def download_backup(name: str):
    """Download a backup snapshot as a zip file."""
    from backup import export_backup
    try:
        zip_path = export_backup(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{name}.zip",
    )


@router.post("/api/system/restart")
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

    # Resolve project root (two levels up from orchestrator/routers/)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
        except (OSError, subprocess.SubprocessError):
            logger.warning("Claude CLI version detection failed", exc_info=True)
            _claude_cli_version._v = "0.0.0"
    return _claude_cli_version._v


_token_usage_cache: dict = {"data": None, "ts": 0.0}
_TOKEN_USAGE_TTL = 120  # seconds — avoid rate-limiting from Anthropic


@router.get("/api/system/token-usage")
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
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
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


# ---- Settings ----

@router.get("/api/settings/notifications")
async def get_notification_settings(db: Session = Depends(get_db)):
    """Get global notification toggle settings."""
    agents_row = db.get(SystemConfig, "notifications_agents_enabled")
    tasks_row = db.get(SystemConfig, "notifications_tasks_enabled")
    return {
        "agents_enabled": agents_row.value != "0" if agents_row else True,
        "tasks_enabled": tasks_row.value != "0" if tasks_row else True,
    }


@router.put("/api/settings/notifications")
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


# ---- Sync Audit / Full Scan ----

@router.get("/api/sync/audit/{agent_id}")
async def run_sync_audit(agent_id: str, request: Request):
    """Run sync full scan (read-only audit + pointer reset) for a specific agent."""
    ad = request.app.state.agent_dispatcher
    ctx = ad._sync_contexts.get(agent_id)
    if not ctx:
        raise HTTPException(404, f"No sync context for agent {agent_id}")

    from sync_engine import sync_full_scan
    result = await sync_full_scan(ad, ctx, reason="manual")

    return {
        "agent_id": agent_id,
        **result,
    }


@router.get("/api/sync/status")
async def sync_status(request: Request):
    """Overview of sync state for all agents."""
    ad = request.app.state.agent_dispatcher

    agents = []
    for agent_id, ctx in ad._sync_contexts.items():
        agents.append({
            "agent_id": agent_id,
            "session_id": ctx.session_id,
            "jsonl_path": ctx.jsonl_path,
            "last_turn_count": ctx.last_turn_count,
            "last_offset": ctx.last_offset,
        })

    return {
        "idle_agents": len(agents),
        "agents": agents,
    }
