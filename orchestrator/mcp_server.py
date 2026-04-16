#!/usr/bin/env python3
"""Xylocopa MCP Server — gives Claude Code agents access to orchestrator data.

First tool: session history (list + read previous conversations).
Framework designed for easy addition of more tools (task queries, agent
coordination, insights, etc.).

Runs as a stdio MCP server, spawned per-agent by Claude Code via .mcp.json.
Read-only — never writes to the database or session files.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys

# Add orchestrator dir so we can import jsonl_parser (lightweight, no config side effects)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from jsonl_parser import (  # noqa: E402
    format_tool_summary,
    parse_session_turns,
    strip_agent_preamble,
)

# ---------------------------------------------------------------------------
# Configuration — all from env vars, no config.py import
# ---------------------------------------------------------------------------
XYLOCOPA_ROOT = os.environ.get(
    "XYLOCOPA_ROOT",
    os.environ.get("AGENTHIVE_ROOT", os.path.dirname(_SCRIPT_DIR)),  # legacy fallback
)
DB_PATH = os.path.join(XYLOCOPA_ROOT, "data", "orchestrator.db")
CLAUDE_HOME = os.path.expanduser(os.environ.get("CLAUDE_HOME", "~/.claude"))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("xylocopa.mcp")


# ---------------------------------------------------------------------------
# Path encoding — replicated from session_cache.py (avoids config.py import
# chain which pulls in dotenv + heavy deps)
# ---------------------------------------------------------------------------

def _encode_project_path(path: str) -> str:
    encoded = re.sub(r"[^a-zA-Z0-9]", "-", path)
    if len(encoded) <= 200:
        return encoded
    h = hashlib.md5(path.encode()).hexdigest()[:8]
    return f"{encoded[:200]}-{h}"


def _session_source_dir(project_path: str) -> str:
    """Locate Claude Code's session directory for a project path."""
    predicted = _encode_project_path(project_path)
    projects_root = os.path.join(CLAUDE_HOME, "projects")

    # Fast path
    if os.path.isdir(os.path.join(projects_root, predicted)):
        return os.path.join(projects_root, predicted)

    # Scan for matching directory (handles encoding differences)
    path_norm = re.sub(r"[^a-zA-Z0-9]", "", project_path).lower()
    try:
        for entry in os.listdir(projects_root):
            if re.sub(r"[^a-zA-Z0-9]", "", entry).lower() == path_norm:
                candidate = os.path.join(projects_root, entry)
                if os.path.isdir(candidate):
                    return candidate
    except OSError:
        pass

    return os.path.join(projects_root, predicted)


# ---------------------------------------------------------------------------
# Database helpers (raw sqlite3, read-only)
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection | None:
    """Open a read-only connection to the orchestrator database."""
    if not os.path.isfile(DB_PATH):
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = FastMCP(
    "xylocopa",
    instructions=(
        "Xylocopa orchestrator tools. Use list_sessions to discover "
        "previous conversations, read_session to read one.\n\n"
        "File handling: when generating or referencing media files "
        "(images, videos, plots), save them inside the project directory "
        "so the web UI can display them. Files in /tmp/ or other external "
        "paths cannot be previewed."
    ),
)


@server.tool()
def list_sessions(project: str = "") -> str:
    """List recent Xylocopa agent sessions.

    Shows agent name, project, status, session ID, and last message preview.
    Use this to discover session IDs that can be passed to read_session().

    Args:
        project: Filter by project name (optional — shows all if empty)
    """
    db = _get_db()
    if db is None:
        return "Xylocopa database not found. Is the orchestrator running?"

    try:
        query = (
            "SELECT id, name, project, session_id, status, "
            "       last_message_preview, last_message_at "
            "FROM agents WHERE session_id IS NOT NULL "
        )
        params: tuple = ()
        if project:
            query += "AND project = ? "
            params = (project,)
        query += "ORDER BY last_message_at DESC LIMIT 30"

        rows = db.execute(query, params).fetchall()
    finally:
        db.close()

    if not rows:
        return "No sessions found." + (f" (project filter: {project})" if project else "")

    lines = [f"Found {len(rows)} session(s):\n"]
    for r in rows:
        ts = (r["last_message_at"] or "")[:19]
        preview = (r["last_message_preview"] or "")[:100]
        status = r["status"] or "?"
        lines.append(
            f"- **{r['name']}** [{status}] — {r['project']}\n"
            f"  session: `{r['session_id']}`  agent: `{r['id']}`\n"
            f"  {ts}  {preview}\n"
        )
    return "\n".join(lines)


@server.tool()
def read_session(session_id: str, max_turns: int = 50) -> str:
    """Read a previous Xylocopa conversation by session ID or agent ID.

    Returns formatted conversation turns (user prompts, agent responses,
    system events). Orchestrator preamble is stripped for readability.

    Args:
        session_id: Session UUID, agent ID, or a prefix of either
        max_turns: Maximum number of turns to return (default 50, most recent)
    """
    db = _get_db()
    if db is None:
        return "Xylocopa database not found. Is the orchestrator running?"

    try:
        row = _lookup_agent(db, session_id)
    finally:
        db.close()

    if row is None:
        return f"No agent found matching: {session_id}"

    agent_name = row["name"]
    project_name = row["project"]
    actual_session_id = row["session_id"]
    project_path = row["path"]

    # Locate JSONL file
    src_dir = _session_source_dir(project_path)
    jsonl_path = os.path.join(src_dir, f"{actual_session_id}.jsonl")

    # Also check worktree locations
    if not os.path.isfile(jsonl_path):
        wt_base = os.path.join(project_path, ".claude", "worktrees")
        if os.path.isdir(wt_base):
            for wt_name in os.listdir(wt_base):
                wt_session_dir = _session_source_dir(
                    os.path.join(wt_base, wt_name)
                )
                candidate = os.path.join(wt_session_dir, f"{actual_session_id}.jsonl")
                if os.path.isfile(candidate):
                    jsonl_path = candidate
                    break

    if not os.path.isfile(jsonl_path):
        return (
            f"Session JSONL not found for {agent_name} ({project_name}).\n"
            f"Looked at: {jsonl_path}\n"
            f"The session file may have been cleaned up."
        )

    # Parse JSONL
    # Cap at 2MB to avoid OOM on huge sessions
    turns = parse_session_turns(jsonl_path, max_bytes=2 * 1024 * 1024)
    total_turns = len(turns)

    if not turns:
        return f"Session {actual_session_id} exists but has no parseable turns."

    # Slice to most recent N turns
    if total_turns > max_turns:
        turns = turns[-max_turns:]

    # Format output
    lines = [
        f"# Session: {agent_name} ({project_name})",
        f"Session ID: `{actual_session_id}`",
        f"Turns: {total_turns} total"
        + (f" (showing last {max_turns})" if total_turns > max_turns else ""),
        "",
    ]

    for role, content, metadata, _uuid, kind, timestamp in turns:
        ts = (timestamp or "")[:19]
        role_label = {"user": "User", "assistant": "Agent", "system": "System"}.get(
            role, role
        )

        # Strip orchestrator wrapper from user messages
        if role == "user":
            content = strip_agent_preamble(content)

        # Format tool_use as one-line summary
        if kind == "tool_use" and metadata:
            summary = format_tool_summary(
                metadata.get("tool_name", ""),
                metadata.get("tool_input", {}),
            )
            if summary:
                content = summary

        # Truncate very long content
        if len(content) > 3000:
            content = content[:3000] + "\n... (truncated)"

        lines.append(f"**[{role_label}]** {ts}")
        lines.append(content)
        lines.append("")

    return "\n".join(lines)


def _lookup_agent(db: sqlite3.Connection, identifier: str) -> dict | None:
    """Look up an agent by session_id, agent_id, or prefix match.

    Returns a dict with agent fields + project path, or None.
    """
    # Join with projects to get path in one query
    base_query = (
        "SELECT a.id, a.name, a.project, a.session_id, p.path "
        "FROM agents a JOIN projects p ON a.project = p.name "
    )

    # Exact session_id match
    row = db.execute(
        base_query + "WHERE a.session_id = ?", (identifier,)
    ).fetchone()
    if row:
        return row

    # Exact agent_id match
    row = db.execute(
        base_query + "WHERE a.id = ?", (identifier,)
    ).fetchone()
    if row:
        return row

    # Prefix match on session_id
    row = db.execute(
        base_query + "WHERE a.session_id LIKE ? ORDER BY a.last_message_at DESC LIMIT 1",
        (f"{identifier}%",),
    ).fetchone()
    if row:
        return row

    # Prefix match on agent_id
    row = db.execute(
        base_query + "WHERE a.id LIKE ? ORDER BY a.last_message_at DESC LIMIT 1",
        (f"{identifier}%",),
    ).fetchone()
    return row


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server.run()
