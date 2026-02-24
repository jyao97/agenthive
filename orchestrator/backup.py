"""Automatic backup — periodic DB and config snapshots."""

import asyncio
import glob
import logging
import os
import shutil
from datetime import datetime, timezone

from config import BACKUP_INTERVAL_HOURS, DB_PATH, MAX_BACKUPS

logger = logging.getLogger("orchestrator.backup")

BACKUP_DIR = "/app/backups"


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

    # 1. SQLite database
    if os.path.exists(DB_PATH):
        dest = os.path.join(backup_subdir, "orchestrator.db")
        # Use WAL-safe copy: copy main db + any wal/shm files
        shutil.copy2(DB_PATH, dest)
        for ext in (".db-wal", ".db-shm"):
            wal = DB_PATH + ext.replace(".db", "")
            if os.path.exists(wal):
                shutil.copy2(wal, dest + ext.replace(".db", ""))
        files_backed += 1
        logger.debug("Backed up database")

    # 2. All PROGRESS.md files from projects
    projects_dir = "/projects"
    if os.path.isdir(projects_dir):
        progress_dir = os.path.join(backup_subdir, "progress")
        os.makedirs(progress_dir, exist_ok=True)
        for proj in os.listdir(projects_dir):
            pm = os.path.join(projects_dir, proj, "PROGRESS.md")
            if os.path.isfile(pm):
                shutil.copy2(pm, os.path.join(progress_dir, f"{proj}_PROGRESS.md"))
                files_backed += 1

    # 3. registry.yaml
    registry = "/app/project-configs/registry.yaml"
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
