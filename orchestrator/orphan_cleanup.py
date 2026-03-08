"""Scan and delete orphaned session JSONL files and output logs."""

import glob
import logging
import os
import shutil

from sqlalchemy import text

logger = logging.getLogger("orchestrator.orphan_cleanup")

from config import CLAUDE_HOME
from database import engine
from session_cache import CACHE_DIR


def scan_orphans() -> dict:
    """Walk ~/.claude/projects/ and /tmp/claude-output-*.log, return orphan
    file list with sizes.  Does NOT delete anything."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT session_id FROM agents WHERE session_id IS NOT NULL")
        ).fetchall()
        live_sessions = {r[0] for r in rows}

        rows = conn.execute(text("SELECT id FROM messages")).fetchall()
        live_msg_ids = {r[0] for r in rows}

    # 1. Session JSONL files
    projects_dir = os.path.join(CLAUDE_HOME, "projects")
    orphan_sessions = []
    total_sessions = 0
    if os.path.isdir(projects_dir):
        for dirpath, _, filenames in os.walk(projects_dir):
            for fname in filenames:
                if not fname.endswith(".jsonl"):
                    continue
                total_sessions += 1
                session_id = fname[:-6]
                if session_id not in live_sessions:
                    fp = os.path.join(dirpath, fname)
                    try:
                        sz = os.path.getsize(fp)
                    except OSError:
                        sz = 0
                    orphan_sessions.append({"path": fp, "size": sz})

    # 2. Output log files in /tmp
    orphan_logs = []
    total_logs = 0
    for log_path in glob.glob("/tmp/claude-output-*.log"):
        total_logs += 1
        basename = os.path.basename(log_path)
        msg_id = basename.replace("claude-output-", "").replace(".log", "")
        if msg_id not in live_msg_ids:
            try:
                sz = os.path.getsize(log_path)
            except OSError:
                sz = 0
            orphan_logs.append({"path": log_path, "size": sz})

    # 3. Empty directories
    empty_dirs = []
    if os.path.isdir(projects_dir):
        for entry in os.listdir(projects_dir):
            d = os.path.join(projects_dir, entry)
            if os.path.isdir(d) and not os.listdir(d):
                empty_dirs.append(d)

    session_bytes = sum(f["size"] for f in orphan_sessions)
    log_bytes = sum(f["size"] for f in orphan_logs)

    return {
        "orphan_sessions": orphan_sessions,
        "orphan_logs": orphan_logs,
        "empty_dirs": empty_dirs,
        "orphan_session_count": len(orphan_sessions),
        "orphan_session_bytes": session_bytes,
        "orphan_log_count": len(orphan_logs),
        "orphan_log_bytes": log_bytes,
        "empty_dir_count": len(empty_dirs),
        "total_files": len(orphan_sessions) + len(orphan_logs),
        "total_bytes": session_bytes + log_bytes,
    }


def delete_orphans(scan_result: dict) -> dict:
    """Delete files from a scan result.  Returns counts + freed bytes."""
    deleted_sessions = 0
    deleted_logs = 0
    deleted_dirs = 0
    freed = 0

    for f in scan_result.get("orphan_sessions", []):
        try:
            os.remove(f["path"])
            freed += f["size"]
            deleted_sessions += 1
        except OSError as e:
            logger.warning("Failed to remove orphan session %s: %s", f["path"], e)

    for f in scan_result.get("orphan_logs", []):
        try:
            os.remove(f["path"])
            freed += f["size"]
            deleted_logs += 1
        except OSError as e:
            logger.warning("Failed to remove orphan log %s: %s", f["path"], e)

    # Evict orphan sessions from session cache (mirrors ~/.claude/projects/ layout)
    evicted_cache = 0
    orphan_sids = set()
    for f in scan_result.get("orphan_sessions", []):
        sid = os.path.basename(f["path"]).replace(".jsonl", "")
        orphan_sids.add(sid)
    if orphan_sids and os.path.isdir(CACHE_DIR):
        for dirpath, _, filenames in os.walk(CACHE_DIR):
            for fname in filenames:
                if not fname.endswith(".jsonl"):
                    continue
                sid = fname[:-6]
                if sid in orphan_sids:
                    cache_path = os.path.join(dirpath, fname)
                    try:
                        os.remove(cache_path)
                        evicted_cache += 1
                    except OSError as e:
                        logger.warning("Failed to evict cached session %s: %s", cache_path, e)
        # Also remove orphan session subdirectories (chunked cache)
        for dirpath, dirnames, _ in os.walk(CACHE_DIR):
            for dname in dirnames:
                if dname in orphan_sids:
                    subdir = os.path.join(dirpath, dname)
                    try:
                        shutil.rmtree(subdir)
                        evicted_cache += 1
                    except OSError as e:
                        logger.warning("Failed to evict cached session dir %s: %s", subdir, e)
    if evicted_cache:
        logger.info("Evicted %d orphan entries from session cache", evicted_cache)

    for d in scan_result.get("empty_dirs", []):
        try:
            os.rmdir(d)
            deleted_dirs += 1
        except OSError as e:
            logger.warning("Failed to remove empty dir %s: %s", d, e)

    return {
        "deleted_sessions": deleted_sessions,
        "deleted_logs": deleted_logs,
        "deleted_dirs": deleted_dirs,
        "evicted_cache": evicted_cache,
        "freed_bytes": freed,
    }
