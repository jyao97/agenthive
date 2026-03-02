"""Automatic backup — periodic DB and config snapshots."""

import asyncio
import glob
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone

from config import BACKUP_DIR, BACKUP_INTERVAL_HOURS, DB_PATH, MAX_BACKUPS

logger = logging.getLogger("orchestrator.backup")


async def run_backup_loop():
    """Run periodic backups on a schedule."""
    logger.info(
        "Backup loop started (interval=%dh, max_backups=%d)",
        BACKUP_INTERVAL_HOURS, MAX_BACKUPS,
    )
    while True:
        try:
            await asyncio.sleep(BACKUP_INTERVAL_HOURS * 3600)
            do_backup()
        except asyncio.CancelledError:
            logger.info("Backup loop cancelled")
            break
        except Exception:
            logger.exception("Backup failed")


def do_backup():
    """Perform a single backup."""
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


def _prune_old_backups():
    """Remove oldest backups beyond MAX_BACKUPS."""
    if not os.path.isdir(BACKUP_DIR):
        return

    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*")))
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)
        logger.info("Pruned old backup: %s", oldest)
