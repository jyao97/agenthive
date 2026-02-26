"""Session Cache — incremental backup and restore of Claude Code session files.

Prevents session loss on orchestrator restart by:
1. Disabling Claude Code's auto-cleanup (cleanupPeriodDays -> 36500)
2. Incrementally caching active session JSONL files (append-only, like git packfiles)
3. Evicting old cached sessions when Claude assigns a new session_id (the new
   file already contains the full conversation — old one is redundant)
4. Restoring from cache when --resume fails with stale session
5. Repairing truncated JSONL lines from process kills
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
from typing import Callable

from config import BACKUP_DIR, CLAUDE_HOME, SESSION_CACHE_INTERVAL

logger = logging.getLogger("orchestrator.session_cache")

CACHE_DIR = os.path.join(BACKUP_DIR, "session-cache")


def encode_project_path(path: str) -> str:
    """Convert a project path to Claude's encoded directory name.

    e.g. /home/YOUR_USERNAME/agenthive-projects/splitvla
      -> -home-jyao073-agenthive-projects-splitvla
    """
    return path.replace("/", "-")


def session_source_dir(project_path: str) -> str:
    """Return the Claude projects directory for a given project path."""
    encoded = encode_project_path(project_path)
    return os.path.join(CLAUDE_HOME, "projects", encoded)


def session_cache_dir(project_path: str) -> str:
    """Return the cache directory for a given project path."""
    encoded = encode_project_path(project_path)
    return os.path.join(CACHE_DIR, encoded)


def ensure_cleanup_disabled() -> None:
    """Set cleanupPeriodDays to 36500 in ~/.claude/settings.json.

    This prevents Claude Code from auto-deleting old session files.
    """
    settings_path = os.path.join(CLAUDE_HOME, "settings.json")
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read settings.json: %s", e)

    current = settings.get("cleanupPeriodDays")
    if current == 36500:
        logger.debug("cleanupPeriodDays already set to 36500")
        return

    settings["cleanupPeriodDays"] = 36500
    try:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(settings_path), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(settings, f, indent=2)
                f.write("\n")
            os.replace(tmp_path, settings_path)
            logger.info("Set cleanupPeriodDays=36500 in %s", settings_path)
        except Exception:
            os.unlink(tmp_path)
            raise
    except OSError:
        logger.exception("Failed to write settings.json")


def cache_session(session_id: str, project_path: str) -> bool:
    """Incrementally cache a session JSONL file.

    JSONL files are append-only, so we only write the new bytes since the last
    cache.  If the cached file doesn't exist yet, we do a full copy.  If the
    source hasn't grown, we skip entirely.

    Subdirectories (subagents, tool-results) are copied once if missing.

    Returns True if anything was written, False if already up-to-date.
    """
    source_dir = session_source_dir(project_path)
    cache_dir = session_cache_dir(project_path)

    jsonl_src = os.path.join(source_dir, f"{session_id}.jsonl")
    subdir_src = os.path.join(source_dir, session_id)

    if not os.path.exists(jsonl_src):
        return False

    src_size = os.path.getsize(jsonl_src)
    jsonl_dst = os.path.join(cache_dir, f"{session_id}.jsonl")
    dst_size = os.path.getsize(jsonl_dst) if os.path.exists(jsonl_dst) else 0

    # Check subdirectory
    subdir_dst = os.path.join(cache_dir, session_id)
    subdir_needed = os.path.isdir(subdir_src) and not os.path.isdir(subdir_dst)

    # Nothing to do — source hasn't grown and subdir already cached
    if src_size <= dst_size and not subdir_needed:
        return False

    os.makedirs(cache_dir, exist_ok=True)
    cached = False

    # Incremental JSONL cache
    if src_size > dst_size:
        try:
            if dst_size == 0:
                # First time — full copy (atomic)
                tmp_path = jsonl_dst + ".tmp"
                shutil.copy2(jsonl_src, tmp_path)
                os.replace(tmp_path, jsonl_dst)
            else:
                # Append only the new bytes
                with open(jsonl_src, "rb") as src_f:
                    src_f.seek(dst_size)
                    new_bytes = src_f.read()
                if new_bytes:
                    with open(jsonl_dst, "ab") as dst_f:
                        dst_f.write(new_bytes)
            cached = True
            logger.debug(
                "Cached session %s: %d -> %d bytes (+%d)",
                session_id, dst_size, src_size, src_size - dst_size,
            )
        except OSError as e:
            logger.warning("Failed to cache session JSONL %s: %s", session_id, e)
            # Clean up partial temp file if full copy failed
            try:
                tmp_path = jsonl_dst + ".tmp"
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass

    # Copy subdirectory once (subagents, tool-results)
    if subdir_needed:
        tmp_subdir = subdir_dst + ".tmp"
        try:
            if os.path.exists(tmp_subdir):
                shutil.rmtree(tmp_subdir)
            shutil.copytree(subdir_src, tmp_subdir)
            if os.path.exists(subdir_dst):
                shutil.rmtree(subdir_dst)
            os.rename(tmp_subdir, subdir_dst)
            cached = True
        except OSError as e:
            logger.warning(
                "Failed to cache session subdir %s: %s", session_id, e
            )
            try:
                shutil.rmtree(tmp_subdir)
            except OSError:
                pass

    return cached


def evict_session(session_id: str, project_path: str) -> None:
    """Remove a cached session that has been superseded.

    When Claude assigns a new session_id on --resume, the new file contains
    the full conversation.  The old cached file is a strict subset and can be
    safely deleted.
    """
    cache_dir = session_cache_dir(project_path)
    jsonl_path = os.path.join(cache_dir, f"{session_id}.jsonl")
    subdir_path = os.path.join(cache_dir, session_id)

    removed = False
    if os.path.exists(jsonl_path):
        os.unlink(jsonl_path)
        removed = True
    if os.path.isdir(subdir_path):
        shutil.rmtree(subdir_path)
        removed = True

    if removed:
        logger.info("Evicted superseded cache for session %s", session_id)


def restore_session(session_id: str, project_path: str) -> bool:
    """Restore a cached session back to ~/.claude/projects/.

    Returns True if restored successfully, False if no cache found.
    """
    cache_dir = session_cache_dir(project_path)
    source_dir = session_source_dir(project_path)

    jsonl_cached = os.path.join(cache_dir, f"{session_id}.jsonl")
    if not os.path.exists(jsonl_cached):
        logger.debug("No cached session %s for project %s", session_id, project_path)
        return False

    os.makedirs(source_dir, exist_ok=True)

    # Restore JSONL
    jsonl_dst = os.path.join(source_dir, f"{session_id}.jsonl")
    try:
        shutil.copy2(jsonl_cached, jsonl_dst)
    except OSError as e:
        logger.warning("Failed to restore session JSONL %s: %s", session_id, e)
        return False

    # Restore subdirectory if cached
    subdir_cached = os.path.join(cache_dir, session_id)
    if os.path.isdir(subdir_cached):
        subdir_dst = os.path.join(source_dir, session_id)
        try:
            if os.path.exists(subdir_dst):
                shutil.rmtree(subdir_dst)
            shutil.copytree(subdir_cached, subdir_dst)
        except OSError as e:
            logger.warning(
                "Failed to restore session subdir %s: %s", session_id, e
            )

    logger.info(
        "Restored session %s for project %s from cache", session_id, project_path
    )
    return True


def repair_session_jsonl(session_id: str, project_path: str) -> bool:
    """Remove truncated/invalid last lines from a session JSONL file.

    When a process is killed mid-write, the last line(s) of the JSONL may be
    incomplete JSON or a tool_use without a matching tool_result. This removes
    those broken trailing lines.

    Returns True if any lines were removed, False otherwise.
    """
    source_dir = session_source_dir(project_path)
    jsonl_path = os.path.join(source_dir, f"{session_id}.jsonl")

    if not os.path.exists(jsonl_path):
        return False

    try:
        with open(jsonl_path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Failed to read session JSONL for repair: %s", e)
        return False

    if not lines:
        return False

    # Remove trailing lines that aren't valid JSON
    original_count = len(lines)
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        try:
            json.loads(last)
            break  # Valid JSON — stop
        except json.JSONDecodeError:
            logger.debug("Removing truncated line from %s", jsonl_path)
            lines.pop()

    if len(lines) == original_count:
        return False

    removed = original_count - len(lines)
    logger.info(
        "Repaired session %s: removed %d truncated line(s)", session_id, removed
    )

    # Write back atomically
    try:
        tmp_path = jsonl_path + ".repair.tmp"
        with open(tmp_path, "w") as f:
            f.writelines(lines)
        os.replace(tmp_path, jsonl_path)
    except OSError as e:
        logger.warning("Failed to write repaired JSONL: %s", e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False

    return True


async def run_session_cache_loop(
    get_active_sessions: Callable[[], list[tuple[str, str]]],
) -> None:
    """Periodically cache all active agent sessions.

    Args:
        get_active_sessions: callable returning list of (session_id, project_path)
            for all agents with active sessions.
    """
    logger.info("Session cache loop started (interval=%ds)", SESSION_CACHE_INTERVAL)
    while True:
        try:
            await asyncio.sleep(SESSION_CACHE_INTERVAL)
            sessions = get_active_sessions()
            if not sessions:
                continue
            cached = 0
            for session_id, project_path in sessions:
                try:
                    if cache_session(session_id, project_path):
                        cached += 1
                except Exception:
                    logger.exception(
                        "Failed to cache session %s", session_id
                    )
            if cached:
                logger.info("Cached %d/%d active sessions", cached, len(sessions))
        except asyncio.CancelledError:
            logger.info("Session cache loop stopped")
            raise
        except Exception:
            logger.exception("Session cache loop error")
