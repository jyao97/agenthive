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
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Callable

from config import BACKUP_DIR, CLAUDE_HOME, SESSION_CACHE_INTERVAL

logger = logging.getLogger("orchestrator.session_cache")

CACHE_DIR = os.path.join(BACKUP_DIR, "session-cache")

# Process-level cache: project_path -> resolved encoded dir name.
# Only populated when a matching directory is confirmed on disk.
_encoded_name_cache: dict[str, str] = {}


def encode_project_path(path: str) -> str:
    """Predict Claude CLI's encoded directory name for a project path.

    Best-effort: replaces all non-alphanumeric characters with hyphens,
    matching Claude Code's actual encoding (``/[^a-zA-Z0-9]/g`` → ``-``).
    If the result exceeds 200 chars it is truncated and a hash suffix is
    appended.  Use _resolve_session_dir_name() for actual lookups — it
    verifies against the filesystem and handles encoding differences.
    """
    encoded = re.sub(r'[^a-zA-Z0-9]', '-', path)
    if len(encoded) <= 200:
        return encoded
    h = hashlib.md5(path.encode()).hexdigest()[:8]
    return f"{encoded[:200]}-{h}"


def _resolve_session_dir_name(project_path: str) -> str:
    """Resolve the actual encoded directory name for a project path.

    1. Check process-level cache
    2. Try predicted encoding (fast path)
    3. Scan ~/.claude/projects/ for a directory whose alphanumeric content
       matches the project path (handles unknown/changed encodings)
    4. Fall back to predicted encoding for new projects without a dir yet

    Negative results (no dir found) are NOT cached so subsequent calls
    can discover newly created directories.
    """
    if project_path in _encoded_name_cache:
        return _encoded_name_cache[project_path]

    predicted = encode_project_path(project_path)
    projects_root = os.path.join(CLAUDE_HOME, "projects")

    # Fast path: predicted directory exists
    if os.path.isdir(os.path.join(projects_root, predicted)):
        _encoded_name_cache[project_path] = predicted
        return predicted

    # Scan for matching directory by stripping all non-alphanumeric chars
    path_norm = re.sub(r'[^a-zA-Z0-9]', '', project_path).lower()
    try:
        for entry in os.listdir(projects_root):
            if re.sub(r'[^a-zA-Z0-9]', '', entry).lower() == path_norm:
                if os.path.isdir(os.path.join(projects_root, entry)):
                    logger.info(
                        "Discovered session dir for %s: %s (predicted: %s)",
                        project_path, entry, predicted,
                    )
                    _encoded_name_cache[project_path] = entry
                    return entry
    except OSError:
        logger.warning(
            "Failed to scan projects root %s for project path %s",
            projects_root, project_path,
        )

    # No existing directory — return predicted, don't cache
    return predicted


def invalidate_path_cache(project_path: str) -> None:
    """Clear the cached directory name for a project path."""
    _encoded_name_cache.pop(project_path, None)


def session_source_dir(project_path: str) -> str:
    """Return the Claude projects directory for a given project path."""
    name = _resolve_session_dir_name(project_path)
    return os.path.join(CLAUDE_HOME, "projects", name)


def session_cache_dir(project_path: str) -> str:
    """Return the cache directory for a given project path."""
    name = _resolve_session_dir_name(project_path)
    return os.path.join(CACHE_DIR, name)


def migrate_session_dirs(project_path: str) -> bool:
    """Find and migrate existing Claude session dirs for a project.

    When a project is registered at a new path (e.g. moved from
    ~/Work/mast3r to ~/agenthive-projects/mast3r), the old session
    directory under ~/.claude/projects/ still uses the old path encoding.

    This scans for any existing session dir whose name ends with the same
    project folder name (e.g. '-mast3r') and migrates it to match the
    current project path encoding.  Matching is done with normalized
    (alphanumeric-only) comparison so it works regardless of the separator
    characters used by different CLI versions.

    Returns True if a migration was performed.
    """
    target_dir = session_source_dir(project_path)
    if os.path.isdir(target_dir):
        return False  # Already exists, nothing to do

    project_basename = os.path.basename(project_path.rstrip("/"))
    if not project_basename:
        return False

    # Also migrate session cache if source is found
    target_cache = session_cache_dir(project_path)
    # Normalize suffix for matching: e.g. "google_map_fusion" -> "-google-map-fusion"
    suffix_normalized = "-" + re.sub(r'[^a-zA-Z0-9]', '-', project_basename)
    projects_root = os.path.join(CLAUDE_HOME, "projects")
    target_name = encode_project_path(project_path)

    if not os.path.isdir(projects_root):
        return False

    for entry in os.listdir(projects_root):
        # Normalize the entry the same way before comparing suffix
        entry_normalized = re.sub(r'[^a-zA-Z0-9]', '-', entry)
        if not entry_normalized.endswith(suffix_normalized):
            continue
        candidate = os.path.join(projects_root, entry)
        if not os.path.isdir(candidate) or candidate == target_dir:
            continue

        # Found an old session dir for the same project basename
        try:
            os.rename(candidate, target_dir)
            logger.info(
                "Migrated session dir for %s: %s → %s",
                project_basename, entry, target_name,
            )
        except OSError:
            logger.warning("Failed to migrate session dir %s → %s", entry, target_dir)
            return False

        # Also migrate the corresponding session cache dir
        old_cache = os.path.join(CACHE_DIR, entry)
        if os.path.isdir(old_cache) and not os.path.exists(target_cache):
            try:
                os.rename(old_cache, target_cache)
                logger.info("Migrated session cache: %s → %s", entry, target_name)
            except OSError:
                logger.warning("Failed to migrate session cache %s", entry)

        # Invalidate cache so next lookup picks up the new name
        invalidate_path_cache(project_path)
        return True

    return False


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
            except OSError as _cleanup_err:
                logger.debug("Cleanup failed: %s", _cleanup_err)

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
            except OSError as _cleanup_err:
                logger.debug("Cleanup failed: %s", _cleanup_err)

    return cached


def cleanup_source_session(
    session_id: str,
    project_path: str,
    worktree: str | None = None,
) -> bool:
    """Delete a session JSONL and its subdirectory from the source directory.

    Called when a session is no longer referenced by any agent.
    Checks both the project root and worktree session dirs.
    Returns True if anything was removed.
    """
    removed = False
    dirs_to_check = [session_source_dir(project_path)]
    if worktree:
        wt_path = os.path.join(project_path, ".claude", "worktrees", worktree)
        dirs_to_check.append(session_source_dir(wt_path))

    for src_dir in dirs_to_check:
        jsonl_path = os.path.join(src_dir, f"{session_id}.jsonl")
        subdir_path = os.path.join(src_dir, session_id)

        if os.path.exists(jsonl_path):
            try:
                os.unlink(jsonl_path)
                removed = True
                logger.info("Removed orphan source session %s from %s", session_id, src_dir)
            except OSError as e:
                logger.warning("Failed to remove source JSONL %s: %s", jsonl_path, e)

        if os.path.isdir(subdir_path):
            try:
                shutil.rmtree(subdir_path)
                removed = True
            except OSError as e:
                logger.warning("Failed to remove source subdir %s: %s", subdir_path, e)

    return removed


def evict_session(session_id: str, project_path: str, worktree: str | None = None) -> None:
    """Remove a cached session that has been superseded.

    When Claude assigns a new session_id on --resume, the new file contains
    the full conversation.  The old cached file is a strict subset and can be
    safely deleted.  Also cleans the worktree cache directory if provided.
    """
    dirs_to_check = [session_cache_dir(project_path)]
    if worktree:
        wt_path = os.path.join(project_path, ".claude", "worktrees", worktree)
        dirs_to_check.append(session_cache_dir(wt_path))

    removed = False
    for cache_dir in dirs_to_check:
        jsonl_path = os.path.join(cache_dir, f"{session_id}.jsonl")
        subdir_path = os.path.join(cache_dir, session_id)

        if os.path.exists(jsonl_path):
            try:
                os.unlink(jsonl_path)
                removed = True
            except OSError as e:
                logger.warning("Failed to evict cached JSONL %s: %s", jsonl_path, e)
        if os.path.isdir(subdir_path):
            try:
                shutil.rmtree(subdir_path)
                removed = True
            except OSError as e:
                logger.warning("Failed to evict cached subdir %s: %s", subdir_path, e)

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
        except OSError as _cleanup_err:
            logger.debug("Cleanup of %s failed: %s", tmp_path, _cleanup_err)
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
                except OSError:
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
