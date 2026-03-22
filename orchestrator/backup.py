"""Automatic backup — periodic DB and config snapshots."""

import asyncio
import glob
import logging
import os
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone

from config import BACKUP_DIR, BACKUP_ENABLED, BACKUP_INTERVAL_HOURS, DB_PATH, MAX_BACKUPS

logger = logging.getLogger("orchestrator.backup")

# ── Runtime-mutable config (updated by PUT /api/system/backup/config) ──
_rt_enabled = BACKUP_ENABLED
_rt_interval_hours = BACKUP_INTERVAL_HOURS
_rt_max_backups = MAX_BACKUPS

# Event to wake the backup loop early (manual backup / config change)
_wake_event = asyncio.Event()


def get_runtime_config():
    """Return current runtime backup config."""
    return {
        "enabled": _rt_enabled,
        "interval_hours": _rt_interval_hours,
        "max_backups": _rt_max_backups,
    }


def update_runtime_config(*, enabled=None, interval_hours=None, max_backups=None):
    """Update runtime config and wake the loop so the new interval takes effect."""
    global _rt_enabled, _rt_interval_hours, _rt_max_backups
    if enabled is not None:
        _rt_enabled = enabled
    if interval_hours is not None:
        _rt_interval_hours = interval_hours
    if max_backups is not None:
        _rt_max_backups = max_backups
    _wake_event.set()
    logger.info("Backup config updated: enabled=%s interval=%dh max=%d",
                _rt_enabled, _rt_interval_hours, _rt_max_backups)


async def run_backup_loop():
    """Run periodic backups on a schedule.  Respects runtime config changes."""
    global _rt_enabled
    if not _rt_enabled:
        logger.info("Backup disabled (BACKUP_ENABLED=0)")
    logger.info(
        "Backup loop started (interval=%dh, max_backups=%d, dir=%s)",
        _rt_interval_hours, _rt_max_backups, BACKUP_DIR,
    )
    while True:
        try:
            _wake_event.clear()
            interval = _rt_interval_hours * 3600
            try:
                await asyncio.wait_for(_wake_event.wait(), timeout=interval)
                # Woken early — config changed or manual trigger; loop continues
            except asyncio.TimeoutError:
                pass  # Normal timer expiry

            if _rt_enabled:
                do_backup()
        except asyncio.CancelledError:
            logger.info("Backup loop cancelled")
            break
        except Exception:
            logger.exception("Backup failed")


def do_backup():
    """Perform a single backup. Returns the backup directory name."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_subdir = os.path.join(BACKUP_DIR, f"backup_{ts}")
    os.makedirs(backup_subdir, exist_ok=True)

    files_backed = 0

    # 1. SQLite database — use sqlite3 backup API for WAL-safe copy
    if os.path.exists(DB_PATH):
        dest = os.path.join(backup_subdir, "orchestrator.db")
        src_conn = sqlite3.connect(DB_PATH)
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        files_backed += 1
        logger.debug("Backed up database")

    # 2. All PROGRESS.md files from projects (keyed by DB project name, not folder)
    from database import SessionLocal
    from models import Project
    db = SessionLocal()
    try:
        projects = db.query(Project).filter(Project.archived == False).all()  # noqa: E712
    finally:
        db.close()
    if projects:
        progress_dir = os.path.join(backup_subdir, "progress")
        os.makedirs(progress_dir, exist_ok=True)
        for proj in projects:
            pm = os.path.join(proj.path, "PROGRESS.md")
            if os.path.isfile(pm):
                shutil.copy2(pm, os.path.join(progress_dir, f"{proj.name}_PROGRESS.md"))
                files_backed += 1

    # 3. registry.yaml
    from config import PROJECT_CONFIGS_PATH
    registry = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
    if os.path.isfile(registry):
        shutil.copy2(registry, os.path.join(backup_subdir, "registry.yaml"))
        files_backed += 1

    logger.info("Backup complete: %s (%d files)", backup_subdir, files_backed)

    # 4. Prune old backups
    _prune_old_backups()

    return os.path.basename(backup_subdir)


def _prune_old_backups():
    """Remove oldest backups beyond max_backups."""
    if not os.path.isdir(BACKUP_DIR):
        return

    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*")))
    while len(backups) > _rt_max_backups:
        oldest = backups.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)
        logger.info("Pruned old backup: %s", oldest)


# ── List / inspect ──

def list_backups():
    """Return list of backup snapshots with metadata."""
    if not os.path.isdir(BACKUP_DIR):
        return []

    result = []
    for d in sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*")), reverse=True):
        name = os.path.basename(d)
        # Parse timestamp from name: backup_YYYYMMDD_HHMMSS or backup_YYYYMMDD_HHMMSS_imported
        m = re.match(r"backup_(\d{8})_(\d{6})(_imported)?$", name)
        if not m:
            continue
        ts_str = f"{m.group(1)}_{m.group(2)}"
        try:
            ts = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        # Calculate size
        total_bytes = 0
        file_count = 0
        has_db = False
        has_registry = False
        progress_count = 0
        for dp, _, files in os.walk(d):
            for f in files:
                try:
                    total_bytes += os.path.getsize(os.path.join(dp, f))
                    file_count += 1
                except OSError:
                    pass
                if f == "orchestrator.db":
                    has_db = True
                elif f == "registry.yaml":
                    has_registry = True
                elif f.endswith("_PROGRESS.md"):
                    progress_count += 1

        result.append({
            "name": name,
            "timestamp": ts.isoformat(),
            "total_bytes": total_bytes,
            "file_count": file_count,
            "has_db": has_db,
            "has_registry": has_registry,
            "progress_count": progress_count,
        })

    return result


def delete_backup(name: str):
    """Delete a single backup by name. Returns bytes freed."""
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isdir(path) or not name.startswith("backup_"):
        return 0
    sz = 0
    for dp, _, files in os.walk(path):
        for f in files:
            try:
                sz += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)
    logger.info("Deleted backup: %s (%d bytes)", name, sz)
    return sz


# ── Restore ──

def restore_backup(name: str):
    """Restore database and registry from a backup snapshot.

    Returns dict with what was restored. Caller should restart the server
    after this completes.
    """
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isdir(path) or not name.startswith("backup_"):
        raise FileNotFoundError(f"Backup not found: {name}")

    restored = []

    # 1. Restore database
    backup_db = os.path.join(path, "orchestrator.db")
    if os.path.isfile(backup_db):
        # Use sqlite3 backup API in reverse: backup → current DB
        src_conn = sqlite3.connect(backup_db)
        dst_conn = sqlite3.connect(DB_PATH)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        restored.append("database")
        logger.info("Restored database from %s", name)

    # 2. Restore registry.yaml
    backup_reg = os.path.join(path, "registry.yaml")
    if os.path.isfile(backup_reg):
        from config import PROJECT_CONFIGS_PATH
        dest = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
        shutil.copy2(backup_reg, dest)
        restored.append("registry")
        logger.info("Restored registry from %s", name)

    return {"name": name, "restored": restored}


# ── Import (upload zip) ──

def import_backup(zip_path: str):
    """Import a backup from a zip file. Extracts to backup dir.

    The zip should contain orchestrator.db and optionally registry.yaml
    and a progress/ directory.

    Returns the name of the imported backup.
    """
    import tempfile

    # Validate zip
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Not a valid zip file")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{ts}_imported"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    os.makedirs(backup_path, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Security: reject paths with .. or absolute paths
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    raise ValueError(f"Unsafe path in zip: {info.filename}")

            names = zf.namelist()

            # Detect if files are in a subdirectory (e.g. backup_xxx/orchestrator.db)
            prefix = ""
            if not any(n == "orchestrator.db" for n in names):
                # Check for files inside a single top-level directory
                dirs = set()
                for n in names:
                    parts = n.split("/")
                    if len(parts) > 1:
                        dirs.add(parts[0])
                if len(dirs) == 1:
                    prefix = dirs.pop() + "/"

            # Extract relevant files
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel = info.filename
                if prefix and rel.startswith(prefix):
                    rel = rel[len(prefix):]
                if not rel:
                    continue

                dest = os.path.join(backup_path, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)

        # Verify at least a database exists
        if not os.path.isfile(os.path.join(backup_path, "orchestrator.db")):
            shutil.rmtree(backup_path, ignore_errors=True)
            raise ValueError("Zip must contain orchestrator.db")

        logger.info("Imported backup: %s from %s", backup_name, zip_path)
        return backup_name

    except Exception:
        # Clean up on failure
        if os.path.isdir(backup_path):
            shutil.rmtree(backup_path, ignore_errors=True)
        raise


# ── Export (download as zip) ──

def export_backup(name: str) -> str:
    """Create a zip of a backup snapshot. Returns path to the zip file."""
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isdir(path) or not name.startswith("backup_"):
        raise FileNotFoundError(f"Backup not found: {name}")

    zip_path = os.path.join(BACKUP_DIR, f"{name}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dp, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(dp, f)
                arcname = os.path.join(name, os.path.relpath(fp, path))
                zf.write(fp, arcname)

    logger.info("Exported backup: %s -> %s", name, zip_path)
    return zip_path


# ── .env persistence ──

def persist_env_config(enabled: bool, interval_hours: int, max_backups: int):
    """Update backup-related values in .env file."""
    from config import _PROJECT_ROOT
    env_path = os.path.join(_PROJECT_ROOT, ".env")

    if not os.path.isfile(env_path):
        logger.warning("Cannot persist backup config: .env not found")
        return

    content = open(env_path, "r").read()

    def _replace_or_add(text, key, value):
        pattern = rf"^{re.escape(key)}=.*$"
        replacement = f"{key}={value}"
        if re.search(pattern, text, re.MULTILINE):
            return re.sub(pattern, replacement, text, flags=re.MULTILINE)
        # Add before the first blank line after === Backup === section
        return text  # Key should already exist

    content = _replace_or_add(content, "BACKUP_ENABLED", "1" if enabled else "0")
    content = _replace_or_add(content, "BACKUP_INTERVAL_HOURS", str(interval_hours))
    content = _replace_or_add(content, "MAX_BACKUPS", str(max_backups))

    with open(env_path, "w") as f:
        f.write(content)

    logger.info("Persisted backup config to .env")
