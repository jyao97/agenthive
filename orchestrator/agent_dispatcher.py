"""Agent Dispatcher — scheduling loop for persistent agent processes."""

import asyncio
import json
import logging
import os
import re
import time as _time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config import CC_MODEL, CLAUDE_HOME, MAX_CONCURRENT_WORKERS
from database import SessionLocal
from log_config import save_worker_log
from models import (
    Agent,
    AgentMode,
    AgentStatus,
    Message,
    MessageRole,
    MessageStatus,
    Project,
    SystemConfig,
    Task,
    TaskStatus,
)
from session_cache import (
    session_source_dir,
    cache_session,
    cleanup_source_session,
    evict_session,
    repair_session_jsonl,
    restore_session,
)
from thumbnails import generate_thumbnails_for_message
from worker_manager import WorkerManager

logger = logging.getLogger("orchestrator.agent_dispatcher")

# Session file stale threshold (seconds) — 30 minutes without writes
# means the CLI session has ended.  Used in _reap_dead_agents and
# startup recovery to decide whether a session is still active.
_STALE_SESSION_THRESHOLD = 1800


def _utcnow():
    return datetime.now(timezone.utc)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n... [truncated]"


def _short_path(path: str) -> str:
    """Shorten a file path for display (last 2 components)."""
    parts = path.rstrip("/").split("/")
    if len(parts) <= 2:
        return path
    return "/".join(parts[-2:])


def _derive_selected_index(item: dict) -> None:
    """Derive selected_index and selected_indices from an interactive item's answer text.

    For ask_user_question, matches the ="label" pattern against each question's options.
    Populates both selected_index (Q0, backward compat) and selected_indices (all Qs).
    For exit_plan_mode, uses keyword matching on the answer.
    """
    answer = item.get("answer")
    if not answer or not isinstance(answer, str):
        return
    # Skip dismissed/rejected answers — no valid selection to derive
    if (answer.startswith("The user doesn't want to proceed")
            or answer.startswith("User declined")
            or answer.startswith("Tool use rejected")):
        return
    if item.get("type") == "ask_user_question":
        questions = item.get("questions", [])
        if not questions:
            return
        # Find all ="label" patterns in order
        matches = re.findall(r'="([^"]+)"', answer)
        if not matches:
            return
        sel_indices = item.get("selected_indices", {})
        # Positional matching: consume match indices so duplicate labels
        # across questions don't cross-match.
        used_match_indices: set[int] = set()
        for qi, q in enumerate(questions):
            if sel_indices.get(str(qi)) is not None:
                continue  # Already set for this question
            options = q.get("options", [])
            for mi, label in enumerate(matches):
                if mi in used_match_indices:
                    continue
                for oi, opt in enumerate(options):
                    if opt.get("label") == label:
                        sel_indices[str(qi)] = oi
                        used_match_indices.add(mi)
                        break
                if sel_indices.get(str(qi)) is not None:
                    break
        if sel_indices:
            item["selected_indices"] = sel_indices
        # Backward compat: set selected_index from Q0
        if item.get("selected_index") is None and sel_indices.get("0") is not None:
            item["selected_index"] = sel_indices["0"]
    elif item.get("type") == "exit_plan_mode":
        if item.get("selected_index") is not None:
            return  # Already set
        a = answer.lower().strip()
        # Dismissal / rejection — don't assign any index
        if (a.startswith("the user doesn't want to proceed")
                or a.startswith("user declined")
                or a.startswith("tool use rejected")):
            return
        # Exact label matching first (avoids keyword collision like "bypass manual")
        _PLAN_LABELS_LOWER = [
            "yes, clear context & bypass",
            "yes, bypass permissions",
            "yes, manual approval",
            "give feedback",
        ]
        for i, lbl in enumerate(_PLAN_LABELS_LOWER):
            if a == lbl:
                item["selected_index"] = i
                return
        # Keyword fallback for answers from Claude's tool_result (may differ in wording)
        if "clear context" in a:
            item["selected_index"] = 0
        elif "bypass" in a and "clear" not in a and "manual" not in a:
            item["selected_index"] = 1
        elif "manual" in a:
            item["selected_index"] = 2
        elif "feedback" in a or "type here" in a:
            item["selected_index"] = 3
        # else: leave selected_index unset — don't default to 0


def _merge_interactive_meta(db_meta_json: str | None, new_meta: dict | None) -> str | None:
    """Merge interactive metadata, preserving web-set answers during sync.

    When the web UI answers an interactive prompt via /api/agents/{id}/answer,
    _patch_interactive_answer() immediately stores selected_index + answer in
    the DB.  The sync loop later re-parses the JSONL and may overwrite the
    metadata with a version where answer is still null (Claude hasn't written
    the tool_result yet).  This function prevents that regression by keeping
    the DB's selected_index/answer when the JSONL version has answer=null.

    If the JSONL version has a non-null answer, it takes precedence (it's the
    authoritative response from Claude's actual tool_result).

    Returns a JSON string.  Does NOT mutate *new_meta*.
    """
    import copy

    if new_meta is None:
        return db_meta_json  # Nothing to merge — keep existing
    if not db_meta_json:
        return json.dumps(new_meta)  # No existing — use new

    try:
        db_meta = json.loads(db_meta_json)
    except (json.JSONDecodeError, TypeError):
        return json.dumps(new_meta)

    db_items = {
        item.get("tool_use_id"): item
        for item in db_meta.get("interactive", [])
        if item.get("tool_use_id")
    }
    if not db_items:
        return json.dumps(new_meta)

    # Work on a copy so the caller's parsed turns are not mutated
    merged = copy.deepcopy(new_meta)

    for item in merged.get("interactive", []):
        tid = item.get("tool_use_id", "")
        db_item = db_items.get(tid)
        if not db_item:
            continue
        # JSONL answer is null but DB has a web-set answer → preserve it
        if item.get("answer") is None and db_item.get("answer") is not None:
            item["answer"] = db_item["answer"]
            if db_item.get("selected_index") is not None:
                item["selected_index"] = db_item["selected_index"]
            if db_item.get("selected_indices"):
                item["selected_indices"] = db_item["selected_indices"]
        # JSONL has a real answer → usually authoritative, but carry over
        # selected_index/selected_indices if the JSONL version doesn't have them.
        # Exception: if the JSONL answer is a dismiss/rejection artifact (e.g.
        # from context-clear terminating the session) but the DB already has a
        # valid non-dismissed answer, keep the DB answer — it reflects the
        # user's actual selection via the web UI.
        elif item.get("answer") is not None:
            jsonl_answer = item["answer"]
            jsonl_is_dismiss = isinstance(jsonl_answer, str) and (
                jsonl_answer.startswith("The user doesn't want to proceed")
                or jsonl_answer.startswith("User declined")
                or jsonl_answer.startswith("Tool use rejected")
            )
            db_answer = db_item.get("answer")
            db_has_valid = (
                db_answer is not None
                and isinstance(db_answer, str)
                and not db_answer.startswith("The user doesn't want to proceed")
                and not db_answer.startswith("User declined")
                and not db_answer.startswith("Tool use rejected")
            )
            if jsonl_is_dismiss and db_has_valid:
                # DB answer is the user's real choice; JSONL dismiss is an
                # artifact (e.g. context-clear killed the old session).
                item["answer"] = db_item["answer"]
                if db_item.get("selected_index") is not None:
                    item["selected_index"] = db_item["selected_index"]
                if db_item.get("selected_indices"):
                    item["selected_indices"] = db_item["selected_indices"]
            else:
                # DB's selected_index/selected_indices were set by the
                # user's explicit web UI click (_patch_interactive_answer)
                # and are more reliable than heuristic derivation from
                # tool_result text.  Always prefer them when available.
                if db_item.get("selected_index") is not None:
                    item["selected_index"] = db_item["selected_index"]
                if db_item.get("selected_indices"):
                    item["selected_indices"] = db_item["selected_indices"]

    return json.dumps(merged)


# Image metadata injected by Claude Code's Read tool — internal only, hide from UI
_IMAGE_META_RE = re.compile(
    r"^\[Image: original \d+x\d+, displayed at \d+x\d+\."
)


def _is_image_metadata(text: str) -> bool:
    """Return True if text is CLI-generated image metadata (not user-facing)."""
    return bool(_IMAGE_META_RE.match(text.strip()))


def _format_tool_summary(name: str, input_data: dict) -> str | None:
    """Format a tool call as a brief one-line markdown summary."""
    if name == "Bash":
        desc = input_data.get("description", "")
        if not desc:
            cmd = input_data.get("command", "")
            desc = cmd.split("\n")[0]
            if len(desc) > 60:
                desc = desc[:57] + "..."
        return f"> `Bash` {desc}"
    if name in ("Read", "Edit", "Write"):
        path = input_data.get("file_path", "")
        # Keep full path for media files so the frontend can preview them
        if path.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
             ".mp4", ".webm", ".mov", ".csv")
        ):
            return f"> `{name}` {path}"
        return f"> `{name}` {_short_path(path)}"
    if name == "Grep":
        pat = input_data.get("pattern", "")
        if len(pat) > 40:
            pat = pat[:37] + "..."
        return f'> `Grep` "{pat}"'
    if name == "Glob":
        return f"> `Glob` {input_data.get('pattern', '')}"
    if name == "Task":
        return f"> `Task` {input_data.get('description', '')}"
    # Skip noisy internal tools
    if name in ("ToolSearch",):
        return None
    return f"> `{name}`"


def _parse_stream_parts(
    logs: str,
) -> tuple[list[tuple[str, str]], dict | None, list[dict], dict | None]:
    """Parse stream-json logs into an ordered list of (kind, content) parts.

    Returns ``(parts, result_event, interactive_items, active_tool)`` where
    *parts* is a list of ``("text", text_string)`` or
    ``("tool", summary_string)`` tuples, *interactive_items* captures any
    ``AskUserQuestion`` / ``ExitPlanMode`` tool calls together with their
    answers (if present), and *active_tool* is a dict with ``name`` and
    ``summary`` keys for the most recent tool_use that has no matching
    tool_result yet (or ``None``).
    """
    parts: list[tuple[str, str]] = []
    result_event = None
    interactive_items: list[dict] = []
    interactive_by_id: dict[str, dict] = {}
    # Track tool_use order and matched tool_results for active-tool detection
    tool_use_order: list[tuple[str, str, str | None]] = []  # (id, name, summary)
    tool_result_ids: set[str] = set()

    for line in logs.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get("type") == "result":
                result_event = event

            if event.get("type") == "assistant" and "message" in event:
                # Skip subagent messages (Task agents)
                if event.get("parent_tool_use_id"):
                    continue
                msg = event["message"]
                if isinstance(msg, dict):
                    for block in msg.get("content", []):
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            if not _is_image_metadata(block.get("text", "")):
                                parts.append(("text", block["text"]))
                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            tool_input = block.get("input", {})
                            tool_use_id = block.get("id", "")

                            # Capture interactive tool calls
                            if tool_name == "AskUserQuestion":
                                entry = {
                                    "type": "ask_user_question",
                                    "tool_use_id": tool_use_id,
                                    "questions": tool_input.get("questions", []),
                                    "answer": None,
                                }
                                interactive_items.append(entry)
                                interactive_by_id[tool_use_id] = entry
                            elif tool_name == "ExitPlanMode":
                                entry = {
                                    "type": "exit_plan_mode",
                                    "tool_use_id": tool_use_id,
                                    "allowedPrompts": tool_input.get("allowedPrompts", []),
                                    "plan": tool_input.get("plan", ""),
                                    "answer": None,
                                }
                                interactive_items.append(entry)
                                interactive_by_id[tool_use_id] = entry

                            summary = _format_tool_summary(
                                tool_name, tool_input,
                            )
                            if summary:
                                parts.append(("tool", summary))
                            tool_use_order.append((tool_use_id, tool_name, summary))

            # Check user entries for tool_result answers to interactive calls
            if event.get("type") == "user":
                user_content = event.get("message", {}).get("content", "")
                if isinstance(user_content, list):
                    for block in user_content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tid = block.get("tool_use_id", "")
                            tool_result_ids.add(tid)
                            if tid in interactive_by_id:
                                rc = block.get("content", "")
                                if isinstance(rc, list):
                                    rc = " ".join(
                                        b.get("text", "") if isinstance(b, dict) else str(b)
                                        for b in rc
                                    ).strip()
                                interactive_by_id[tid]["answer"] = rc
                                _derive_selected_index(interactive_by_id[tid])

        except json.JSONDecodeError:
            continue  # expected: partial/truncated lines during streaming
        except (KeyError, TypeError):
            logger.warning("_parse_stream_parts: unexpected error parsing line: %s", line[:200], exc_info=True)
            continue

    # Determine the currently active tool: walk tool_use_order in reverse,
    # first entry whose id is NOT in tool_result_ids is the active one.
    active_tool: dict | None = None
    for tu_id, tu_name, tu_summary in reversed(tool_use_order):
        if tu_id not in tool_result_ids:
            active_tool = {"name": tu_name, "summary": tu_summary or f"`{tu_name}`"}
            break

    return parts, result_event, interactive_items, active_tool


def _format_parts(parts: list[tuple[str, str]]) -> str:
    """Format parsed parts into a single markdown-ish string."""
    if not parts:
        return ""
    groups = []
    current_tools = []
    for kind, content in parts:
        if kind == "tool":
            current_tools.append(content)
        else:
            if current_tools:
                groups.append("\n".join(current_tools))
                current_tools = []
            groups.append(content)
    if current_tools:
        groups.append("\n".join(current_tools))

    text = "\n\n".join(groups)
    # Strip legacy EXIT_SUCCESS / EXIT_FAILURE markers
    text = re.sub(r"\n?EXIT_SUCCESS\s*$", "", text).strip()
    text = re.sub(r"\n?EXIT_FAILURE:?.*$", "", text).strip()
    return text


_TOOL_SUMMARY_RE = re.compile(r'^> `(\w+)`\s*(.*)')


def _extract_last_tool_from_content(content: str) -> dict | None:
    """Extract last tool summary from formatted content if it's the final block.

    Used by the sync streaming path (which uses ``_parse_session_turns``
    rather than ``_parse_stream_parts``) to detect the currently active tool
    from the rendered markdown content.
    """
    lines = content.rstrip().split("\n")
    for line in reversed(lines):
        stripped = line.strip()
        m = _TOOL_SUMMARY_RE.match(stripped)
        if m:
            return {"name": m.group(1), "summary": f"`{m.group(1)}` {m.group(2)}".strip()}
        if stripped:
            return None  # text after tools = no active tool
    return None


def _extract_result(logs: str) -> tuple[str, str | None]:
    """Extract agent response text and tool call summaries from stream-json.

    Returns ``(text, meta_json)`` where *meta_json* is a JSON string
    containing interactive tool call data (``AskUserQuestion``,
    ``ExitPlanMode``) if any were found, or ``None``.
    """
    parts, result_event, interactive_items, _ = _parse_stream_parts(logs)

    meta_json = None
    if interactive_items:
        meta_json = json.dumps({"interactive": interactive_items})

    # Friendly error messages for known error patterns
    if result_event and result_event.get("is_error"):
        errors = result_event.get("errors", [])
        for err in errors:
            if isinstance(err, str) and "No conversation found with session ID" in err:
                return (
                    "This session's conversation data is no longer available. "
                    "It may have been cleaned up or created on a different machine. "
                    "Please start a new conversation instead.",
                    meta_json,
                )

    text = _format_parts(parts)
    if text:
        return text, meta_json

    # Fallback: return last chunk of raw output — but skip if the log only
    # contains system/init events (no actual assistant response).
    lines = logs.strip().splitlines()
    has_only_system = True
    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            ev = json.loads(raw_line)
            if ev.get("type") not in ("system", None):
                has_only_system = False
                break
        except (json.JSONDecodeError, TypeError):
            has_only_system = False
            break
    if has_only_system:
        return "(no output)", meta_json
    fallback = "\n".join(lines[-20:]) if lines else "(no output)"
    return fallback, meta_json


def _is_result_error(logs: str) -> bool:
    """Check if the stream-json result event indicates an error.
    Also returns True when the CLI crashed before producing any result event
    (e.g. nested-session error, missing binary, permission denied)."""
    found_result = False
    for line in logs.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get("type") == "result":
                found_result = True
                return event.get("is_error", False)
        except json.JSONDecodeError:
            continue
        except (KeyError, TypeError):
            logger.warning("_is_result_error: unexpected error parsing line: %s", line[:200], exc_info=True)
            continue
    # No result event at all — CLI likely crashed before producing output
    return not found_result and len(logs.strip()) > 0


def _extract_session_id(logs: str) -> str | None:
    """Extract session_id from the result event in stream-json output."""
    for line in logs.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get("type") == "result" and event.get("session_id"):
                return event["session_id"]
        except json.JSONDecodeError:
            continue
    return None


def _extract_session_id_from_output(output_file: str) -> str | None:
    """Read the session_id from a stream-json output file (init or result event).

    Only reads the first few lines to avoid scanning large files.
    """
    try:
        with open(output_file, "r", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    sid = event.get("session_id")
                    if sid:
                        return sid
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.debug("_extract_session_id_from_output: failed to read %s: %s", output_file, e)
    return None


def _parse_session_model(jsonl_path: str) -> str | None:
    """Extract the model from the first assistant message in a session JSONL."""
    try:
        with open(jsonl_path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "assistant":
                        model = entry.get("message", {}).get("model")
                        if model:
                            return model
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.debug("_parse_session_model: failed to read %s: %s", jsonl_path, e)
    return None


# Backward-compat alias — _detect_session_model was identical to _parse_session_model.
_detect_session_model = _parse_session_model


import re as _re

_PREAMBLE_RE = _re.compile(
    r"^You are working in project: .+?\n"
    r"Project path: .+?\n\n"
    r"First read the project's CLAUDE\.md to understand project conventions\.\n\n",
    _re.DOTALL,
)
_POSTAMBLE_RE = _re.compile(
    r"\n\nIf you make code changes, commit with message format: \[agent-[0-9a-f]+\] short description$"
)


def _strip_agent_preamble(content: str) -> str:
    """Strip orchestrator-injected preamble/postamble from user messages."""
    text = _PREAMBLE_RE.sub("", content)
    text = _POSTAMBLE_RE.sub("", text)
    return text.strip() if text != content else content


def _resolve_session_jsonl(
    session_id: str,
    project_path: str,
    worktree: str | None = None,
) -> str:
    """Resolve the path to a session JSONL, checking worktree dirs too.

    Worktree agents store session files in a separate Claude projects
    directory based on the worktree CWD, not the project root.
    When *worktree* is None, scans all worktree directories as a fallback.
    """
    jsonl_path = os.path.join(
        session_source_dir(project_path), f"{session_id}.jsonl"
    )
    if os.path.isfile(jsonl_path):
        return jsonl_path
    if worktree:
        wt_path = os.path.join(project_path, ".claude", "worktrees", worktree)
        wt_jsonl = os.path.join(
            session_source_dir(wt_path), f"{session_id}.jsonl"
        )
        if os.path.isfile(wt_jsonl):
            return wt_jsonl
    else:
        # No worktree specified — scan all worktree directories as fallback
        wt_base = os.path.join(project_path, ".claude", "worktrees")
        if os.path.isdir(wt_base):
            try:
                for name in os.listdir(wt_base):
                    wt_path = os.path.join(wt_base, name)
                    if os.path.isdir(wt_path):
                        wt_jsonl = os.path.join(
                            session_source_dir(wt_path), f"{session_id}.jsonl"
                        )
                        if os.path.isfile(wt_jsonl):
                            return wt_jsonl
            except OSError:
                pass
    return jsonl_path  # return original path even if not found


def _infer_worktree_from_session(
    session_id: str,
    project_path: str,
) -> str | None:
    """Try to determine which worktree a session belongs to by scanning
    worktree session directories.  Returns the worktree name or None."""
    wt_base = os.path.join(project_path, ".claude", "worktrees")
    if not os.path.isdir(wt_base):
        return None
    try:
        for name in os.listdir(wt_base):
            wt_path = os.path.join(wt_base, name)
            if os.path.isdir(wt_path):
                wt_jsonl = os.path.join(
                    session_source_dir(wt_path), f"{session_id}.jsonl"
                )
                if os.path.isfile(wt_jsonl):
                    return name
    except OSError:
        pass
    return None


def _scan_subagents(
    session_id: str,
    project_path: str,
    worktree: str | None = None,
) -> list[dict]:
    """Scan for Claude Code subagent JSONL files spawned by a parent session.

    Returns a list of dicts with keys:
      claude_agent_id, slug, prompt, model, jsonl_path, size, mtime
    """
    # Resolve the session dir from the parent's JSONL path
    parent_jsonl = _resolve_session_jsonl(session_id, project_path, worktree)
    session_dir = os.path.dirname(parent_jsonl)
    subagents_dir = os.path.join(session_dir, session_id, "subagents")
    if not os.path.isdir(subagents_dir):
        return []

    results = []
    try:
        for fname in os.listdir(subagents_dir):
            if not fname.startswith("agent-") or not fname.endswith(".jsonl"):
                continue
            if "compact" in fname:
                continue
            fpath = os.path.join(subagents_dir, fname)
            try:
                stat = os.stat(fpath)
            except OSError:
                continue

            # Extract claude_agent_id from filename: agent-{id}.jsonl
            claude_agent_id = fname[len("agent-"):-len(".jsonl")]

            # Parse first two entries for prompt, slug, model
            prompt = ""
            slug = ""
            model = ""
            try:
                with open(fpath, "r", errors="replace") as f:
                    for i, line in enumerate(f):
                        if i >= 2:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if i == 0:
                            slug = entry.get("slug", "")
                            msg = entry.get("message", {})
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                prompt = content[:300]
                            elif isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        prompt = block["text"][:300]
                                        break
                        elif i == 1:
                            msg = entry.get("message", {})
                            model = msg.get("model", "")
            except OSError:
                pass

            results.append({
                "claude_agent_id": claude_agent_id,
                "slug": slug,
                "prompt": prompt,
                "model": model,
                "jsonl_path": fpath,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
    except OSError:
        pass

    return results


def _parse_session_turns(jsonl_path: str) -> list[tuple[str, str, dict | None]]:
    """Parse a Claude Code session JSONL into conversation turns.

    Returns a list of (role, content, metadata) tuples where role is
    "user", "assistant", or "system".  metadata is a dict with an
    "interactive" key when the turn contains AskUserQuestion or
    ExitPlanMode tool calls, or None otherwise.

    Skips tool_result entries (intermediate tool calls) and queue-operations.
    Groups consecutive assistant entries into a single turn using _format_parts style.
    """
    turns: list[tuple[str, str, dict | None]] = []

    try:
        with open(jsonl_path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("_parse_session_turns: failed to read %s: %s", jsonl_path, e)
        return turns

    # Accumulate assistant blocks between user messages
    assistant_parts: list[tuple[str, str]] = []
    # Track interactive tool calls (AskUserQuestion / ExitPlanMode) in current turn
    pending_interactive: list[dict] = []
    # Map tool_use_id → interactive entry for matching tool_result answers
    interactive_by_id: dict[str, dict] = {}

    def flush_assistant():
        if not assistant_parts and not pending_interactive:
            return
        text = _format_parts(assistant_parts) if assistant_parts else ""
        meta = None
        if pending_interactive:
            meta = {"interactive": list(pending_interactive)}
        if text.strip() or meta:
            turns.append(("assistant", text.strip() if text else "", meta))
        assistant_parts.clear()
        pending_interactive.clear()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")

        if entry_type == "user":
            msg = entry.get("message", {})
            content = msg.get("content", "")
            # Check for tool_result in list-type content
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id", "")
                        if tool_use_id in interactive_by_id:
                            result_content = block.get("content", "")
                            # Ensure answer is always a string (content
                            # can be a list of content blocks in the API)
                            if isinstance(result_content, list):
                                result_content = " ".join(
                                    b.get("text", "") if isinstance(b, dict) else str(b)
                                    for b in result_content
                                ).strip() or ""
                            interactive_by_id[tool_use_id]["answer"] = result_content
                            # Derive selected_index from the answer text
                            _derive_selected_index(interactive_by_id[tool_use_id])
                            # Flush so each interactive Q&A becomes its
                            # own message bubble instead of one giant block.
                            flush_assistant()
                continue
            # Real user message = string content (not tool_result list)
            if isinstance(content, str) and content.strip():
                stripped = content.strip()
                # Skip system-injected messages that aren't real user input
                if (
                    stripped.startswith("<local-command-caveat>")
                    or stripped.startswith("<command-name>")
                    or stripped.startswith("<local-command-stdout>")
                    or stripped.startswith("<system-reminder>")
                    or stripped.startswith("<task-notification>")
                ):
                    continue
                # Compact summary → system message instead of user
                if stripped.startswith(
                    "This session is being continued from a previous conversation"
                ):
                    flush_assistant()
                    turns.append(("system", content, None))
                    continue
                flush_assistant()
                clean = _strip_agent_preamble(stripped)
                turns.append(("user", clean, None))

        elif entry_type == "assistant":
            msg = entry.get("message", {})
            # Skip subagent messages
            if entry.get("parent_tool_use_id"):
                continue
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    if not _is_image_metadata(block["text"]):
                        assistant_parts.append(("text", block["text"]))
                elif block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    tool_use_id = block.get("id", "")

                    # Capture interactive tool calls — flush accumulated
                    # text/tool parts first so the card gets its own bubble.
                    if tool_name in ("AskUserQuestion", "ExitPlanMode"):
                        # Flush preceding content into its own message
                        flush_assistant()
                        if tool_name == "AskUserQuestion":
                            entry_data = {
                                "type": "ask_user_question",
                                "tool_use_id": tool_use_id,
                                "questions": tool_input.get("questions", []),
                                "answer": None,
                            }
                        else:
                            entry_data = {
                                "type": "exit_plan_mode",
                                "tool_use_id": tool_use_id,
                                "allowedPrompts": tool_input.get("allowedPrompts", []),
                                "plan": tool_input.get("plan", ""),
                                "answer": None,
                            }
                        pending_interactive.append(entry_data)
                        interactive_by_id[tool_use_id] = entry_data

                    summary = _format_tool_summary(tool_name, tool_input)
                    if summary:
                        assistant_parts.append(("tool", summary))

        elif entry_type == "queue-operation":
            # Queued prompts sent while assistant is working
            if entry.get("operation") == "enqueue":
                queued_content = entry.get("content", "")
                if isinstance(queued_content, str) and queued_content.strip():
                    clean_q = _strip_agent_preamble(queued_content.strip())
                    # Sub-agent task results are system-generated, not user input
                    if clean_q.lstrip().startswith("<task-notification>"):
                        flush_assistant()
                        turns.append(("assistant", clean_q, None))
                    else:
                        flush_assistant()
                        turns.append(("user", clean_q, None))

        elif entry_type == "system":
            # Use structured fields from JSONL (subtype, content)
            subtype = entry.get("subtype", "")
            # Skip internal CLI metrics
            if subtype in ("turn_duration",):
                continue
            flush_assistant()
            content = entry.get("content", "")
            if subtype or content:
                label = content or subtype.replace("_", " ")
                turns.append(("system", label, None))

    # Flush any remaining assistant content
    flush_assistant()

    # Deduplicate identical user turns.  Claude Code context compaction
    # re-injects the same user prompt for every continuation session,
    # producing many copies of "You are working in project: ..." etc.
    # Keep only the first occurrence of each unique user message.
    if turns:
        seen_user: set[str] = set()
        deduped: list[tuple[str, str, dict | None]] = []
        for role, content, meta in turns:
            if role == "user":
                if content in seen_user:
                    continue
                seen_user.add(content)
            deduped.append((role, content, meta))
        turns = deduped

    return turns


def _update_stale_interactive_metadata(
    db: "Session", agent_id: str, turns: list[tuple[str, str, dict | None]]
) -> bool:
    """Update DB messages whose interactive metadata has stale (null) answers.

    When a user answers an AskUserQuestion in the terminal, the tool_result
    appears in a subsequent user entry.  _parse_session_turns() links the
    answer back to the original assistant turn's metadata via interactive_by_id.
    But the sync loop may have already stored the assistant message with
    answer=null.  This function re-checks and patches those stale entries.

    Returns True if any DB messages were updated.
    """
    # 1. Collect all interactive items with non-null answers, keyed by tool_use_id
    answered: dict[str, str] = {}
    for _role, _content, meta in turns:
        if not meta or "interactive" not in meta:
            continue
        for item in meta["interactive"]:
            if item.get("answer") is not None:
                answered[item["tool_use_id"]] = item["answer"]

    if not answered:
        return False

    # 2. Query DB messages with metadata for this agent
    db_msgs = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.meta_json.is_not(None),
    ).all()

    updated = False
    for msg in db_msgs:
        try:
            meta = json.loads(msg.meta_json)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("_update_stale_interactive_metadata: bad meta_json for msg %s: %s", msg.id, e)
            continue
        items = meta.get("interactive")
        if not items:
            continue
        msg_changed = False
        for item in items:
            tid = item.get("tool_use_id", "")
            if item.get("answer") is None and tid in answered:
                item["answer"] = answered[tid]
                _derive_selected_index(item)
                msg_changed = True
        if msg_changed:
            msg.meta_json = json.dumps(meta)
            updated = True

    if updated:
        db.commit()

    # 3. Also backfill interactive metadata onto AGENT messages that were
    #    created before the parser produced metadata.  Match by content prefix.
    #    GUARD: skip if another message already carries the same tool_use_id
    #    to prevent duplicate interactive bubbles.
    turns_with_meta = [
        (content, meta)
        for _role, content, meta in turns
        if _role == "assistant" and meta and meta.get("interactive")
    ]
    if turns_with_meta:
        # Collect tool_use_ids already present in existing DB messages
        existing_tids: set[str] = set()
        for msg in db_msgs:
            try:
                m = json.loads(msg.meta_json)
            except (json.JSONDecodeError, TypeError):
                continue
            for it in m.get("interactive", []):
                if it.get("tool_use_id"):
                    existing_tids.add(it["tool_use_id"])

        agent_msgs = db.query(Message).filter(
            Message.agent_id == agent_id,
            Message.role == MessageRole.AGENT,
            Message.meta_json.is_(None),
        ).all()
        for msg in agent_msgs:
            for turn_content, turn_meta in turns_with_meta:
                # Skip if this interactive item already exists on another message
                turn_tids = {
                    it.get("tool_use_id")
                    for it in turn_meta.get("interactive", [])
                    if it.get("tool_use_id")
                }
                if turn_tids & existing_tids:
                    continue
                # Match by content prefix (DB msg may be truncated/shorter)
                if (
                    msg.content
                    and turn_content
                    and (
                        msg.content[:100] == turn_content[:100]
                        or turn_content.startswith(msg.content[:100])
                        or msg.content.startswith(turn_content[:100])
                    )
                ):
                    msg.meta_json = json.dumps(turn_meta)
                    existing_tids.update(turn_tids)
                    updated = True
                    break
        if updated:
            db.commit()

    return updated


# ---- tmux helpers ----

import subprocess as _sp

_CLAUDE_DEBUG_DIR = os.path.join(CLAUDE_HOME, "debug")


def _get_session_pid(session_id: str) -> int | None:
    """Extract the PID that owns a session from its debug log.

    Claude Code writes ``~/.claude/debug/{session_id}.txt``.  We check
    (in priority order):

    1. ``Acquired PID lock for ... (PID \\d+)`` — only present when
       the process successfully acquires the version lock (first launch).
    2. ``Writing to temp file: .../.claude.json.tmp.{PID}.{timestamp}``
       — always present regardless of lock acquisition.
    3. ``Writing to temp file: .../{file}.tmp.{PID}.{timestamp}``
       — broader fallback for any file write by Claude (needed when
       /clear creates a new session that doesn't write .claude.json
       early enough).

    Fallback (2) is needed because concurrent claude processes report
    "Cannot acquire lock" instead of "Acquired PID lock".
    """
    debug_file = os.path.join(_CLAUDE_DEBUG_DIR, f"{session_id}.txt")
    try:
        fallback_pid = None
        broad_fallback_pid = None
        with open(debug_file, "r") as f:
            for line in f:
                if "Acquired PID lock" in line:
                    m = re.search(r"\(PID (\d+)\)", line)
                    if m:
                        pid = int(m.group(1))
                        if os.path.exists(f"/proc/{pid}"):
                            return pid
                        # PID is dead — fall through to fallback patterns
                        # instead of returning None (the same PID may appear
                        # in .tmp file writes which are equally valid)
                        continue
                elif ".tmp." in line and "Writing to temp file:" in line:
                    # Prefer .claude.json.tmp.{PID} (most specific)
                    if fallback_pid is None and ".claude.json.tmp." in line:
                        m = re.search(r"\.claude\.json\.tmp\.(\d+)\.", line)
                        if m:
                            fallback_pid = int(m.group(1))
                    # Broad fallback: any {file}.tmp.{PID}.{timestamp}
                    if broad_fallback_pid is None:
                        m = re.search(r"\.tmp\.(\d+)\.\d+$", line)
                        if m:
                            broad_fallback_pid = int(m.group(1))
        # Use most specific fallback first
        if fallback_pid is not None and os.path.exists(f"/proc/{fallback_pid}"):
            return fallback_pid
        if broad_fallback_pid is not None and os.path.exists(f"/proc/{broad_fallback_pid}"):
            return broad_fallback_pid
    except OSError as e:
        logger.debug("_get_session_pid: failed to read debug file for session %s: %s", session_id, e)
    return None


def _get_session_slug(jsonl_path: str) -> str | None:
    """Extract the session slug from the first few lines of a JSONL file.

    Claude Code writes the slug in SessionStart events at the top of every
    JSONL.  A /clear transition reuses the same slug in the new session,
    providing PID-independent proof of continuity.
    """
    try:
        with open(jsonl_path, "r") as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                if '"slug"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                slug = obj.get("slug")
                if slug:
                    return slug
    except OSError:
        pass
    return None


def _get_session_cwd(jsonl_path: str) -> str | None:
    """Extract the working directory from the first user/assistant entry in a JSONL.

    Each JSONL entry written by Claude Code includes a ``cwd`` field.
    """
    try:
        with open(jsonl_path, "r") as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                cwd = obj.get("cwd")
                if cwd:
                    return cwd
    except OSError:
        pass
    return None


def _detect_pid_session_jsonl(claude_pid: int) -> str | None:
    """Find the session JSONL that a Claude process currently has open.

    Scans ``/proc/{pid}/fd`` for file handles pointing to ``.jsonl``
    files under the Claude projects directory.  Returns the session ID
    (filename without extension) if found.
    """
    try:
        fd_dir = f"/proc/{claude_pid}/fd"
        for entry in os.listdir(fd_dir):
            try:
                target = os.readlink(os.path.join(fd_dir, entry))
                if target.endswith(".jsonl") and "/.claude/projects/" in target:
                    sid = os.path.basename(target).replace(".jsonl", "")
                    if len(sid) >= 32 and "-" in sid:
                        return sid
            except OSError:
                continue
    except OSError:
        pass
    return None


def _dedup_sig(text: str) -> str:
    """Normalize content for dedup comparison.

    tmux converts tabs to spaces, so a message sent via web (tabs)
    won't exactly match the same message in the JSONL (spaces).
    Collapse all whitespace runs to single space for comparison.
    """
    import re
    return re.sub(r"\s+", " ", text[:200]).strip()


def _get_first_user_content(jsonl_path: str) -> str | None:
    """Extract the content of the first user message from a JSONL file."""
    try:
        with open(jsonl_path, "r") as f:
            for i, line in enumerate(f):
                if i >= 50:
                    break
                if '"user"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                msg = obj.get("message", {})
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text.strip():
                                return text
    except OSError:
        pass
    return None


def _pid_owns_session(pid: int, session_id: str) -> bool:
    """Check if *pid* has open file handles referencing *session_id*.

    Claude Code keeps ``~/.claude/tasks/{session_id}/`` open for the
    life of the session.  When ``/clear`` reuses the same process, the
    new session's debug log lacks the "Acquired PID lock" line, so
    ``_get_session_pid`` returns None.  This fallback scans
    ``/proc/{pid}/fd`` for symlinks pointing into the task directory.
    """
    tasks_fragment = f"/tasks/{session_id}"
    try:
        fd_dir = f"/proc/{pid}/fd"
        for entry in os.listdir(fd_dir):
            try:
                target = os.readlink(os.path.join(fd_dir, entry))
                if tasks_fragment in target:
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def _get_process_start_time(pid: int) -> float | None:
    """Return the start time (epoch seconds) of a process, or None."""
    try:
        stat_start = os.stat(f"/proc/{pid}").st_mtime
        return stat_start
    except OSError:
        return None


def _tier2_match_for_pane(
    pane_pid: int,
    recent_candidates: list[tuple[str, str, float]],
) -> tuple[str, str] | None:
    """Find the best session JSONL for a pane using process start time.

    Matches the session whose JSONL creation time (ctime) is closest to
    the process start time.  Rejects matches where the delta exceeds
    _MAX_START_DELTA to avoid mismatching stale sessions.

    Removes the matched entry from recent_candidates (mutates in place).
    """
    if not recent_candidates:
        return None

    _MAX_START_DELTA = 1800  # 30 min max between process start and session creation

    proc_start = _get_process_start_time(pane_pid)
    if proc_start is None:
        return None

    # Find the session whose ctime is closest to the process start
    best_idx = None
    best_delta = float("inf")
    for i, (sid, fpath, mtime) in enumerate(recent_candidates):
        try:
            ctime = os.path.getctime(fpath)
        except OSError:
            ctime = mtime
        delta = abs(ctime - proc_start)
        if delta < best_delta:
            best_delta = delta
            best_idx = i

    if best_idx is not None and best_delta <= _MAX_START_DELTA:
        sid, fpath, _mt = recent_candidates.pop(best_idx)
        return sid, fpath
    return None


def _is_orchestrator_process(pid: int) -> bool:
    """Check if a PID is an orchestrator-spawned claude process.

    Orchestrator-managed subprocesses (via WorkerManager) always run with
    ``-p`` (non-interactive pipe mode).  Interactive TUI sessions
    (tmux-launched from the web UI or started by the user on the CLI)
    never use ``-p``, even though they may use ``--output-format``.

    We check /proc/{pid}/cmdline rather than the environment because the
    systemd service sets AGENTHIVE_MANAGED=1 on the uvicorn server, and
    that env var propagates to the tmux server and all panes, making
    environment-based detection unreliable.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
        # cmdline is NUL-separated; split into actual argv list
        args = raw.decode("utf-8", errors="replace").split("\0")
        return "-p" in args
    except OSError:
        return False


def _build_tmux_claude_map() -> dict[str, dict]:
    """Build a map of all tmux panes running claude.

    Walks each pane's process tree downward from its shell PID to find
    claude child processes. This is authoritative because a pane's
    process tree is unambiguous.

    Returns: {pane_id: {"pid": int, "cwd": str, "is_orchestrator": bool, "session_name": str}}
    """
    try:
        result = _sp.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{pane_id} #{pane_pid} #{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            logger.warning(
                "_build_tmux_claude_map: tmux list-panes returned %d: %s",
                result.returncode, result.stderr.strip(),
            )
            return {}
    except (_sp.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("_build_tmux_claude_map: tmux list-panes failed: %s", e)
        return {}

    pane_map = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        pane_id, shell_pid = parts[0], parts[1]
        session_name = parts[2] if len(parts) > 2 else ""

        # Find claude child process of this pane's shell
        try:
            children = _sp.run(
                ["ps", "--ppid", shell_pid, "-o", "pid=,comm="],
                capture_output=True, text=True, timeout=5,
            )
            for cline in children.stdout.strip().splitlines():
                cparts = cline.strip().split(None, 1)
                if len(cparts) == 2 and cparts[1] == "claude":
                    cpid = int(cparts[0])
                    try:
                        cwd = os.path.realpath(os.readlink(f"/proc/{cpid}/cwd"))
                    except OSError as e:
                        logger.debug("_build_tmux_claude_map: readlink cwd for PID %d failed: %s", cpid, e)
                        cwd = ""
                    pane_map[pane_id] = {
                        "pid": cpid,
                        "cwd": cwd,
                        "is_orchestrator": _is_orchestrator_process(cpid),
                        "session_name": session_name,
                    }
                    break
        except (_sp.TimeoutExpired, OSError, ValueError) as e:
            logger.debug("_build_tmux_claude_map: inspecting pane %s failed: %s", pane_id, e)
            continue

    return pane_map


def _get_pane_owner(pane_id: str, exclude_agent_id: str | None = None) -> "Agent | None":
    """Return any non-STOPPED agent that owns *pane_id*, or None."""
    db = SessionLocal()
    try:
        q = db.query(Agent).filter(
            Agent.tmux_pane == pane_id,
            Agent.status != AgentStatus.STOPPED,
        )
        if exclude_agent_id:
            q = q.filter(Agent.id != exclude_agent_id)
        return q.first()
    finally:
        db.close()


def _detect_tmux_pane_for_session(session_id: str, project_path: str) -> str | None:
    """Detect the tmux pane running a specific CLI session.

    Uses a two-tier strategy:

    Tier 1 - Session ID in cmdline:
        If the user ran `claude --resume <uuid>`, the session_id appears in
        /proc/PID/cmdline. Resolve its TTY to a tmux pane. Rarely works
        (users typically just run `claude --resume` without explicit UUID).

    Tier 2 - Pane-first process tree walk:
        Build a complete map of tmux pane -> claude process by walking each
        pane's process tree downward. Filter to non-orchestrator processes
        whose CWD matches the project. If exactly one matches, return it.
    """
    real_project = os.path.realpath(project_path)

    # ---- Tier 1: session_id in cmdline (rare but highest confidence) ----
    try:
        result = _sp.run(
            ["pgrep", "-f", "claude"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for pid_str in result.stdout.strip().splitlines():
                pid = int(pid_str)
                if _is_orchestrator_process(pid):
                    continue
                try:
                    with open(f"/proc/{pid}/cmdline", "r") as f:
                        cmdline = f.read()
                    if session_id in cmdline:
                        # Found the exact process — resolve TTY to pane
                        tty_r = _sp.run(
                            ["ps", "-ho", "tty", "-p", str(pid)],
                            capture_output=True, text=True, timeout=5,
                        )
                        tty = tty_r.stdout.strip()
                        if tty and tty != "?":
                            if not tty.startswith("/"):
                                tty = "/dev/" + tty
                            panes_r = _sp.run(
                                ["tmux", "list-panes", "-a", "-F", "#{pane_tty} #{pane_id}"],
                                capture_output=True, text=True, timeout=5,
                            )
                            for pline in panes_r.stdout.strip().splitlines():
                                pp = pline.split(None, 1)
                                if len(pp) == 2 and pp[0] == tty:
                                    return pp[1]
                except (OSError, ValueError) as e:
                    logger.debug("Tier 1 pane detect: PID %d inspect failed: %s", pid, e)
                    continue
    except _sp.TimeoutExpired as e:
        logger.debug("Tier 1 pane detection timed out for session %s: %s", session_id, e)
    except (FileNotFoundError, OSError) as e:
        logger.warning("Tier 1 pane detection failed for session %s: %s", session_id, e)

    # ---- Tier 2: pane-first process tree walk ----
    pane_map = _build_tmux_claude_map()
    def _cwd_matches(cwd: str, proj: str) -> bool:
        return cwd == proj or cwd.startswith(proj + "/")

    user_candidates = [
        (pane_id, info)
        for pane_id, info in pane_map.items()
        if not info["is_orchestrator"] and _cwd_matches(info["cwd"], real_project)
        and not _get_pane_owner(pane_id)  # skip panes already owned
    ]

    if len(user_candidates) == 1:
        return user_candidates[0][0]

    if len(user_candidates) > 1:
        # ---- Tier 3: match via direct OS file-handle check ----
        for pane_id, info in user_candidates:
            if _detect_pid_session_jsonl(info["pid"]) == session_id:
                return pane_id

        # Tier 4: fallback to debug-log PID (legacy Claude Code)
        session_pid = _get_session_pid(session_id)
        if session_pid:
            for pane_id, info in user_candidates:
                if info["pid"] == session_pid:
                    return pane_id

        logger.warning(
            "Ambiguous: %d user claude processes in tmux for project %s "
            "(panes: %s). Cannot determine which owns session %s.",
            len(user_candidates), project_path,
            [c[0] for c in user_candidates], session_id[:12],
        )
        return None

    return None


def _is_cli_session_alive(project_path: str, tmux_pane: str | None = None) -> bool:
    """Check if a specific agent's CLI process is still alive.

    If tmux_pane is set, checks only that specific pane (high confidence).
    If no pane, checks if ANY user claude process matches the project path
    (used only during initial detection / startup recovery).
    """
    real_project = os.path.realpath(project_path)
    pane_map = _build_tmux_claude_map()

    def _cwd_matches_project(cwd: str, proj: str) -> bool:
        return cwd == proj or cwd.startswith(proj + "/")

    # If we have a specific pane, ONLY check that one — don't match others
    if tmux_pane:
        info = pane_map.get(tmux_pane)
        return bool(info and not info["is_orchestrator"] and _cwd_matches_project(info["cwd"], real_project))

    # No specific pane — broad scan (used for initial detection only)
    for pane_id, info in pane_map.items():
        if not info["is_orchestrator"] and _cwd_matches_project(info["cwd"], real_project):
            return True

    # Check non-tmux claude processes
    try:
        result = _sp.run(
            ["pgrep", "-f", "claude"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for pid_str in result.stdout.strip().splitlines():
                try:
                    pid = int(pid_str)
                    if _is_orchestrator_process(pid):
                        continue
                    cwd = os.path.realpath(os.readlink(f"/proc/{pid}/cwd"))
                    if _cwd_matches_project(cwd, real_project):
                        return True
                except (OSError, ValueError):
                    continue
    except (_sp.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug("Non-tmux alive check failed for project %s: %s", project_path, e)

    return False


def send_tmux_message(pane_id: str, text: str) -> bool:
    """Send a message to a tmux pane.

    For short single-line messages: uses `send-keys -l` (literal text)
    which avoids the paste-buffer timing race entirely.

    For long/multiline: uses load-buffer + paste-buffer -p (bracketed paste)
    with a small delay before Enter.
    """
    import time

    try:
        # Clear any existing text in the input line first
        _sp.run(["tmux", "send-keys", "-t", pane_id, "C-u"],
                capture_output=True, text=True, timeout=5)
        time.sleep(0.05)

        is_short = len(text) < 200 and "\n" not in text

        if is_short:
            # send-keys -l sends literal characters — no paste-buffer needed
            r = _sp.run(
                ["tmux", "send-keys", "-t", pane_id, "-l", text],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                logger.warning("tmux send-keys -l failed: %s", r.stderr)
                return False
            # Small delay for Ink TUI to render the characters
            time.sleep(0.05)
        else:
            # Long/multiline: paste-buffer with bracketed paste mode
            r = _sp.run(
                ["tmux", "load-buffer", "-"],
                input=text, capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                logger.warning("tmux load-buffer failed: %s", r.stderr)
                return False
            r = _sp.run(
                ["tmux", "paste-buffer", "-t", pane_id, "-p"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                logger.warning("tmux paste-buffer failed: %s", r.stderr)
                return False
            time.sleep(0.15)

        # Send Enter to submit
        r = _sp.run(
            ["tmux", "send-keys", "-t", pane_id, "Enter"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            logger.warning("tmux send-keys Enter failed: %s", r.stderr)
            return False

        return True
    except (_sp.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("tmux send failed: %s", e)
        return False


def send_tmux_keys(pane_id: str, keys: list[str]) -> bool:
    """Send raw key names to a tmux pane (e.g., 'Down', 'Enter').

    Each key is sent individually with a 200ms delay between them
    to allow the Ink-based TUI to process each keystroke reliably.
    """
    import time

    try:
        for key in keys:
            r = _sp.run(
                ["tmux", "send-keys", "-t", pane_id, key],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                logger.warning("tmux send-keys %s failed: %s", key, r.stderr)
                return False
            time.sleep(0.2)
        return True
    except (_sp.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("tmux send_tmux_keys failed: %s", e)
        return False


def verify_tmux_pane(pane_id: str) -> bool:
    """Check if a tmux pane still exists."""
    try:
        result = _sp.run(
            ["tmux", "display-message", "-t", pane_id, "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == pane_id
    except (_sp.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("verify_tmux_pane(%s) failed: %s", pane_id, e)
        return False


def capture_tmux_pane(pane_id: str) -> str | None:
    """Capture the visible content of a tmux pane for diagnostic logging."""
    try:
        result = _sp.run(
            ["tmux", "capture-pane", "-t", pane_id, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except (_sp.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _detect_plan_prompt(pane_text: str) -> str:
    """Detect what kind of prompt is showing for ExitPlanMode.

    Returns:
        "option_select" — vertical option list (Yes, and / clear context / etc.)
        "permission"    — Allow/Deny permission prompt
        "unknown"       — cannot determine
    """
    if not pane_text:
        return "unknown"
    lower = pane_text.lower()
    # Check for option-select style (plan approval)
    if "clear context" in lower or "bypass" in lower or "manual" in lower:
        return "option_select"
    # Check for permission prompt style
    if "allow" in lower and "deny" in lower:
        return "permission"
    return "unknown"


class AgentDispatcher:
    """Dispatch loop for persistent agent processes."""

    def __init__(self, worker_manager: WorkerManager):
        self.worker_mgr = worker_manager
        self.running = False

        # In-memory tracking of active execs
        # agent_id -> {pid_str, output_file, message_id, started_at, last_activity}
        self._active_execs: dict[str, dict] = {}

        # Track stale session recovery retries per agent to avoid infinite loops.
        # agent_id -> consecutive retry count
        self._stale_session_retries: dict[str, int] = {}
        self._max_stale_retries = 3

        # Track timeout retries per message to avoid infinite retry loops.
        # message_id -> retry count
        self._timeout_retries: dict[str, int] = {}
        self._max_timeout_retries = 2

        # Grace retries for SYNCING cli_sync agents that temporarily lose
        # tmux pane association (e.g. tmux hiccup or race during re-detect).
        # agent_id -> consecutive no-pane ticks
        self._syncing_no_pane_retries: dict[str, int] = {}
        self._max_syncing_no_pane_retries = 15  # ~30s at 2s tick

        # Streaming output loops: agent_id -> asyncio.Task
        self._stream_tasks: dict[str, asyncio.Task] = {}

        # CLI session sync tasks: agent_id -> asyncio.Task
        self._sync_tasks: dict[str, asyncio.Task] = {}

        # Generation tracking: monotonic ID per agent + set of currently generating agents
        self._generation_ids: dict[str, int] = {}
        self._generating_agents: set[str] = set()

        # Tmux launch background tasks: agent_id -> asyncio.Task
        self._launch_tasks: dict[str, asyncio.Task] = {}

        # CLI auto-detect tick counter (run every ~30s, not every 2s tick)
        self._cli_detect_counter = 0
        self._cli_detect_interval = 15  # ticks (15 * 2s = 30s)

        # Track consecutive project-ready failures per project to avoid
        # silently retrying forever.
        self._project_ready_failures: dict[str, int] = {}
        self._max_project_ready_failures = 10  # ~20s of failures → ERROR

        # Cache: tmux pane_id -> True if a human client is attached
        self._pane_attached: dict[str, bool] = {}

        # Per-tick cache of _build_tmux_claude_map() to avoid spawning
        # N+1 subprocesses multiple times per tick.
        self._tmux_map_cache: dict[str, dict] | None = None

        # Track known subagent claude_agent_ids per parent agent
        # parent_agent_id -> {claude_agent_id: {agent_id, last_size, idle_polls}}
        self._known_subagents: dict[str, dict[str, dict]] = {}

    def _get_tmux_map(self) -> dict[str, dict]:
        """Get the per-tick cached tmux pane→claude map.

        Builds the map on first call per tick, then returns the cached copy.
        Call self._tmux_map_cache = None at tick start to invalidate.
        """
        if self._tmux_map_cache is None:
            self._tmux_map_cache = _build_tmux_claude_map()
        return self._tmux_map_cache

    def _release_session(
        self,
        session_id: str | None,
        exclude_agent_id: str,
        project_path: str | None,
        worktree: str | None,
        db,
    ) -> None:
        """Release a session — clean up source + cache if no other agent uses it."""
        if not session_id or not project_path:
            return
        # Check if another agent still references this session
        other = (
            db.query(Agent.id)
            .filter(Agent.session_id == session_id, Agent.id != exclude_agent_id)
            .first()
        )
        if other:
            logger.debug(
                "Session %s still referenced by agent %s — skipping cleanup",
                session_id, other[0],
            )
            return
        evict_session(session_id, project_path)
        cleanup_source_session(session_id, project_path, worktree)

    def _refresh_pane_attached(self):
        """Check which tmux panes have a human client attached."""
        import subprocess as sp
        try:
            result = sp.run(
                ["tmux", "list-panes", "-a",
                 "-F", "#{pane_id} #{session_attached}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                self._pane_attached = {}
                return
            attached = {}
            for line in result.stdout.strip().splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    attached[parts[0]] = parts[1] != "0"
            self._pane_attached = attached
        except (sp.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning("_refresh_pane_attached: tmux list-panes failed: %s", e)
            self._pane_attached = {}

    def _is_agent_in_use(self, agent_id: str, tmux_pane: str | None = None) -> bool:
        """Check if a user is actively viewing this agent (tmux or web)."""
        from websocket import ws_manager
        if ws_manager.is_agent_viewed(agent_id):
            return True
        if tmux_pane and self._pane_attached.get(tmux_pane, False):
            return True
        return False

    def get_active_sessions(self) -> list[tuple[str, str]]:
        """Return (session_id, project_path) for all agents with sessions.

        Used by the session cache loop to know which sessions to back up.
        """
        db = SessionLocal()
        try:
            agents = db.query(Agent).filter(
                Agent.session_id.is_not(None),
                Agent.status.in_([
                    AgentStatus.IDLE, AgentStatus.EXECUTING,
                    AgentStatus.SYNCING,
                ]),
            ).all()
            results = []
            for agent in agents:
                project = db.get(Project, agent.project)
                if not project:
                    continue
                results.append((agent.session_id, project.path))
            return results
        finally:
            db.close()

    def get_active_processes(self) -> list[dict]:
        """Return info about currently running Claude processes."""
        results = []
        for agent_id, info in self._active_execs.items():
            elapsed = (_utcnow() - info["started_at"]).total_seconds()
            results.append({
                "agent_id": agent_id,
                "message_id": info["message_id"],
                "started_at": info["started_at"].isoformat(),
                "elapsed_seconds": int(elapsed),
            })
        return results

    async def run(self):
        """Start the agent dispatcher loop."""
        self.running = True
        _consecutive_failures = 0
        logger.info("Agent dispatcher started")

        self._recover_agents()

        # Generate thumbnails for existing videos in background
        from thumbnails import backfill_thumbnails
        asyncio.ensure_future(asyncio.to_thread(backfill_thumbnails))

        while self.running:
            try:
                if not self.worker_mgr.ping():
                    await asyncio.sleep(5)
                    continue

                db = SessionLocal()
                try:
                    self._tick(db)

                    # Daily PROGRESS.md summary auto-trigger (once per day, persisted in DB)
                    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    last_row = db.get(SystemConfig, "auto_summary_last_date")
                    last_date = last_row.value if last_row else None
                    if last_date != today_str:
                        if last_row:
                            last_row.value = today_str
                        else:
                            db.add(SystemConfig(key="auto_summary_last_date", value=today_str))
                        db.commit()
                        self._trigger_daily_progress_summaries(db)
                finally:
                    db.close()
                _consecutive_failures = 0
            except Exception:
                _consecutive_failures += 1
                logger.exception(
                    "Agent dispatcher tick failed (%d consecutive failures)",
                    _consecutive_failures,
                )
                if _consecutive_failures >= 10:
                    logger.critical(
                        "Agent dispatcher: %d consecutive failures — stopping to avoid silent corruption",
                        _consecutive_failures,
                    )
                    break
            await asyncio.sleep(2)

        logger.info("Agent dispatcher stopped")

    def stop(self):
        self.running = False

    def _trigger_daily_progress_summaries(self, db: Session):
        """Auto-trigger PROGRESS.md summary for projects with the toggle enabled."""
        projects = (
            db.query(Project)
            .filter(Project.auto_progress_summary == True, Project.archived == False)
            .all()
        )
        if not projects:
            return

        import threading
        from main import _progress_job_get, _progress_job_set

        for proj in projects:
            # Skip if already running or completed today
            existing = _progress_job_get(proj.name)
            if existing:
                continue

            # Gather today's completed tasks (use UTC date to match UTC completed_at)
            today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time()).replace(tzinfo=timezone.utc)
            completed_tasks = (
                db.query(Task)
                .filter(
                    Task.project_name == proj.name,
                    Task.status == TaskStatus.COMPLETE,
                    Task.completed_at >= today_start,
                )
                .order_by(Task.completed_at)
                .all()
            )

            if not completed_tasks:
                logger.info("Auto-summary skipped for %s: no tasks completed today", proj.name)
                continue

            # Build rich session context: task info + full conversation turns
            session_blocks = []
            for t in completed_tasks:
                block_parts = [f"### Task: {t.title}"]
                if t.description:
                    block_parts.append(f"Description: {t.description[:500]}")
                if t.agent_summary:
                    block_parts.append(f"Agent summary: {t.agent_summary[:500]}")

                # Fetch conversation turns for this task's agent
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
                            # Truncate very long messages but keep enough for understanding
                            content = msg.content[:2000] if msg.content else ""
                            content = _strip_agent_preamble(content)
                            block_parts.append(f"[{role}] {content}")

                session_blocks.append("\n".join(block_parts))

            session_context = "\n\n---\n\n".join(session_blocks)

            # Auto-apply: run summary and append result (no review step)
            _progress_job_set(proj.name, status="running")
            thread = threading.Thread(
                target=self._auto_apply_progress_summary,
                args=(proj.name, proj.path, session_context),
                daemon=True,
            )
            thread.start()
            logger.info("Auto-triggered daily PROGRESS.md summary for project %s", proj.name)

    @staticmethod
    def _auto_apply_progress_summary(project_name: str, project_path: str,
                                     session_context: str):
        """Generate a daily summary section and append it to PROGRESS.md."""
        import subprocess
        from datetime import date
        from config import CLAUDE_BIN
        from main import _progress_job_set, _progress_job_clear

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

        try:
            result = subprocess.run(
                [CLAUDE_BIN, "-p", prompt, "--output-format", "text"],
                capture_output=True, text=True, timeout=300,
                cwd=project_path,
            )
            if result.returncode != 0:
                logger.warning("Auto progress summary failed for %s: %s", project_name, result.stderr[:500])
                _progress_job_set(project_name, status="error", error="Auto-summary failed")
                return
            new_section = result.stdout.strip()
        except Exception as e:
            logger.warning("Auto progress summary error for %s: %s", project_name, e)
            _progress_job_set(project_name, status="error", error=str(e))
            return

        if not new_section:
            _progress_job_clear(project_name)
            return

        # Guard: reject conversational / non-markdown output from LLM
        first_line = new_section.lstrip().split("\n", 1)[0].lower()
        _REFUSAL_MARKERS = ("since ", "it seems", "i ", "the file", "could you", "no tasks",
                            "unfortunately", "i'm ", "i cannot", "here is", "sure,", "certainly")
        if not first_line.startswith("#") and any(first_line.startswith(m) for m in _REFUSAL_MARKERS):
            logger.warning("Auto-summary for %s rejected: LLM returned conversational text: %.100s",
                           project_name, first_line)
            _progress_job_clear(project_name)
            return

        # Strip markdown fences if LLM wrapped output
        if new_section.startswith("```"):
            lines = new_section.split("\n")
            # Remove first ``` line and last ``` line
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            new_section = "\n".join(lines).strip()

        # Append to PROGRESS.md (never overwrite)
        progress_path = os.path.join(project_path, "PROGRESS.md")
        try:
            existing = ""
            if os.path.isfile(progress_path):
                with open(progress_path, "r", encoding="utf-8", errors="replace") as f:
                    existing = f.read()

            separator = "\n\n" if existing and not existing.endswith("\n\n") else ("\n" if existing and not existing.endswith("\n") else "")
            with open(progress_path, "w", encoding="utf-8") as f:
                f.write(existing + separator + new_section + "\n")
            logger.info("Auto-appended daily PROGRESS.md summary for %s", project_name)
        except OSError as e:
            logger.warning("Failed to write PROGRESS.md for %s: %s", project_name, e)
        _progress_job_clear(project_name)

    def _emit(self, coro):
        try:
            asyncio.ensure_future(coro)
        except Exception:
            logger.warning("Failed to schedule WebSocket emit", exc_info=True)

    def _next_generation_id(self, agent_id: str) -> int:
        """Return the next monotonic generation ID for an agent."""
        gid = self._generation_ids.get(agent_id, 0) + 1
        self._generation_ids[agent_id] = gid
        return gid

    def _start_generating(self, agent_id: str) -> int:
        """Mark agent as generating and return a new generation_id."""
        gid = self._next_generation_id(agent_id)
        self._generating_agents.add(agent_id)
        return gid

    def _stop_generating(self, agent_id: str):
        """Mark agent as no longer generating and emit stream_end."""
        gid = self._generation_ids.get(agent_id)
        self._generating_agents.discard(agent_id)
        from websocket import emit_agent_stream_end
        self._emit(emit_agent_stream_end(agent_id, generation_id=gid))

    # ---- v2 Task dispatch/harvest ----

    def _check_scheduled_tasks(self, db: Session):
        """Handle notify_at — send push reminder with status-aware action text."""
        from task_state_machine import TERMINAL_STATES
        now = _utcnow()

        notify_tasks = (
            db.query(Task)
            .filter(
                Task.notify_at != None,  # noqa: E711
                Task.notify_at <= now,
                Task.status.notin_(TERMINAL_STATES),
            )
            .all()
        )
        _ACTION_TEXT = {
            TaskStatus.INBOX: "Ready to plan",
            TaskStatus.PLANNING: "Ready to dispatch",
            TaskStatus.PENDING: "Queued for execution",
            TaskStatus.EXECUTING: "Still running",
            TaskStatus.REVIEW: "Ready to review",
            TaskStatus.CONFLICT: "Needs attention",
        }
        for task in notify_tasks:
            task.notify_at = None
            action = _ACTION_TEXT.get(task.status, "Reminder")
            try:
                from push import send_push_notification, is_notification_enabled
                if is_notification_enabled("tasks"):
                    send_push_notification(
                        action,
                        task.title or "Untitled task",
                        url=f"/tasks/{task.id}",
                    )
            except Exception:
                logger.warning("Failed to send notify_at notification for %s", task.id, exc_info=True)

    def _dispatch_pending_tasks(self, db: Session):
        """Pick up PENDING v2 tasks and create tmux agents for them."""
        import secrets
        import subprocess

        tasks = (
            db.query(Task)
            .filter(Task.status == TaskStatus.PENDING)
            .order_by(Task.priority.desc(), Task.created_at.asc())
            .limit(5)
            .all()
        )
        if not tasks:
            return

        for task in tasks:
            proj = db.query(Project).filter(Project.name == task.project_name).first()
            if not proj:
                logger.warning("Task %s: project %s not found, skipping", task.id, task.project_name)
                continue

            # Check project capacity (only count agents actively running)
            active = (
                db.query(Agent)
                .filter(Agent.project == proj.name)
                .filter(Agent.status.in_([AgentStatus.EXECUTING, AgentStatus.STARTING]))
                .count()
            )
            if active >= proj.max_concurrent:
                continue

            try:
                agent_id = self._create_task_agent(db, task, proj)
                if agent_id:
                    task.status = TaskStatus.EXECUTING
                    task.agent_id = agent_id
                    task.started_at = _utcnow()
                    db.commit()
                    from websocket import emit_task_update
                    self._emit(emit_task_update(
                        task.id, task.status.value, task.project_name or "",
                        title=task.title, agent_id=agent_id,
                    ))
                    logger.info("Task %s dispatched to agent %s", task.id, agent_id)
            except Exception:
                logger.exception("Failed to dispatch task %s", task.id)

    def _create_task_agent(self, db: Session, task: Task, proj: Project) -> str | None:
        """Create an agent for a v2 task. Reuses the standard IDLE→dispatch flow.

        Creates Agent(IDLE) + Message(PENDING), then the existing
        _dispatch_pending_messages loop picks it up on the next tick
        and runs it through worker_mgr.exec_claude_in_agent().
        """
        import secrets

        # Generate unique agent ID
        for _ in range(20):
            agent_hex = secrets.token_hex(6)
            if db.get(Agent, agent_hex) is None:
                break
        else:
            return None

        # Worktree name from task title (only if use_worktree is enabled)
        wt_name = None
        branch = None
        if getattr(task, 'use_worktree', True):
            wt_name = task.worktree_name or f"task-{task.id}"
            task.worktree_name = wt_name
            branch = f"worktree-{wt_name}"
            task.branch_name = branch
        else:
            # Non-worktree: record HEAD so we can revert agent's changes later
            import subprocess
            try:
                head = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=proj.path,
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip()
                if head and len(head) >= 7:
                    task.try_base_commit = head
            except Exception:
                pass

        model = task.model or proj.default_model or CC_MODEL
        prompt = self._build_task_prompt(task)

        # Create agent record — IDLE so _dispatch_pending_messages picks it up
        agent = Agent(
            id=agent_hex,
            project=proj.name,
            name=f"Task: {task.title[:80]}",
            mode=AgentMode.AUTO,
            status=AgentStatus.IDLE,
            model=model,
            effort=task.effort or "high",
            worktree=wt_name if wt_name else None,
            skip_permissions=getattr(task, 'skip_permissions', True),
            task_id=task.id,
            last_message_preview=f"Task: {task.title[:80]}",
            last_message_at=_utcnow(),
        )
        db.add(agent)
        db.flush()

        # Save initial message as PENDING — dispatch loop will execute it
        msg = Message(
            agent_id=agent.id,
            role=MessageRole.USER,
            content=prompt,
            status=MessageStatus.PENDING,
            source="task",
        )
        db.add(msg)
        db.commit()

        from websocket import emit_agent_update
        self._emit(emit_agent_update(agent.id, agent.status.value, proj.name))

        return agent.id

    def _build_task_prompt(self, task: Task) -> str:
        """Build the full prompt for a task agent."""
        parts = [f"# Task: {task.title}"]
        if task.description:
            parts.append(f"\n{task.description}")
        if task.attempt_number > 1 and task.retry_context:
            parts.append(f"\n## Previous Attempt Context (attempt #{task.attempt_number})")
            parts.append(task.retry_context)
        if task.rejection_reason:
            parts.append(f"\n## Rejection Reason from Previous Attempt")
            parts.append(task.rejection_reason)
        if task.attempt_number > 1:
            parts.append(f"\n## Redo Context")
            parts.append(
                f"This is attempt #{task.attempt_number}. Before starting, briefly summarize why the previous "
                "attempt didn't fully satisfy the requirements and append it to PROGRESS.md "
                "in the project root. Then proceed with the task."
            )
        parts.append("\n## Before You Start")
        parts.append("- Read PROGRESS.md in the project root (if it exists), focusing on entries relevant to this task — avoid repeating past mistakes")
        parts.append("\n## Guidelines")
        parts.append("- Work autonomously — do not ask for confirmation, interviews, or permissions")
        parts.append(
            "- Complete the entire task in a single turn. Within this turn you may call as many "
            "tools as needed — read files, write code, run tests, fix errors, iterate — until the "
            "task is fully done and self-verified. Do not stop halfway and wait for feedback"
        )
        parts.append("- Avoid dangerous/destructive operations (force push, drop tables, rm -rf, etc.)")
        parts.append("- Commit all changes with descriptive messages")
        parts.append("- Before your final message, append a short entry to PROGRESS.md with today's date, task title, and any lessons learned (gotchas, workarounds, or 'straightforward — no issues' if none)")
        parts.append("- Leave a summary of what was done as your final message")
        return "\n".join(parts)

    def _harvest_task_completions(self, db: Session):
        """Check EXECUTING v2 tasks — if agent is done, move to REVIEW."""
        tasks = (
            db.query(Task)
            .filter(Task.status == TaskStatus.EXECUTING)
            .filter(Task.agent_id.isnot(None))
            .all()
        )
        for task in tasks:
            agent = db.get(Agent, task.agent_id)
            if not agent:
                # Agent deleted while task was EXECUTING — fail the task
                try:
                    from task_state_machine import validate_transition
                    validate_transition(task.status, TaskStatus.FAILED)
                except Exception:
                    logger.warning("Task %s: invalid transition %s -> FAILED (agent deleted)", task.id, task.status.value)
                task.status = TaskStatus.FAILED
                task.completed_at = _utcnow()
                task.error_message = "Agent was deleted while task was executing"
                db.commit()
                from websocket import emit_task_update
                self._emit(emit_task_update(
                    task.id, task.status.value, task.project_name or "",
                    title=task.title,
                ))
                logger.info("Task %s FAILED (agent deleted)", task.id)
                continue
            if agent.status in (AgentStatus.IDLE, AgentStatus.STOPPED):
                # Skip agents that haven't executed yet (still waiting for dispatch)
                has_pending = (
                    db.query(Message)
                    .filter(Message.agent_id == agent.id, Message.status == MessageStatus.PENDING)
                    .count()
                )
                if has_pending and agent.status == AgentStatus.IDLE:
                    continue
                # Agent finished — extract summary from last message
                last_msg = (
                    db.query(Message)
                    .filter(Message.agent_id == agent.id, Message.role == MessageRole.AGENT)
                    .order_by(Message.created_at.desc())
                    .first()
                )
                if last_msg:
                    task.agent_summary = last_msg.content[:2000] if last_msg.content else None
                # If agent died without producing any output → FAILED, not REVIEW
                if not last_msg:
                    try:
                        from task_state_machine import validate_transition
                        validate_transition(task.status, TaskStatus.FAILED)
                    except Exception:
                        logger.warning("Task %s: invalid transition %s -> FAILED (no output)", task.id, task.status.value)
                    task.status = TaskStatus.FAILED
                    task.completed_at = _utcnow()
                    task.error_message = "Agent stopped without producing output"
                    db.commit()
                    from websocket import emit_task_update
                    self._emit(emit_task_update(
                        task.id, task.status.value, task.project_name or "",
                        title=task.title,
                    ))
                    logger.info("Task %s FAILED (agent %s died without output)", task.id, agent.id)
                    continue
                task.status = TaskStatus.REVIEW
                # Stop agent — it has finished its task
                if agent.status != AgentStatus.STOPPED:
                    agent.status = AgentStatus.STOPPED
                    if agent.tmux_pane:
                        import subprocess as _sp
                        _sp.run(["tmux", "kill-session", "-t", f"ah-{agent.id[:8]}"],
                                capture_output=True, timeout=5)
                        agent.tmux_pane = None
                db.commit()
                from websocket import emit_task_update, emit_agent_update
                self._emit(emit_task_update(
                    task.id, task.status.value, task.project_name or "",
                    title=task.title, agent_id=task.agent_id,
                ))
                self._emit(emit_agent_update(agent.id, "STOPPED", agent.project))
                # Send push notification (suppress if user is viewing the agent or tasks notifications off)
                try:
                    from push import send_push_notification, is_notification_enabled
                    if is_notification_enabled("tasks") and not self._is_agent_in_use(agent.id, agent.tmux_pane):
                        send_push_notification(
                            "Task Ready for Review",
                            task.title[:60],
                            f"/tasks/{task.id}",
                        )
                except Exception:
                    logger.debug("Push notification failed for task %s", task.id)
                logger.info("Task %s moved to REVIEW (agent %s stopped)", task.id, agent.id)
            elif agent.status == AgentStatus.ERROR:
                try:
                    from task_state_machine import validate_transition
                    validate_transition(task.status, TaskStatus.FAILED)
                except Exception:
                    logger.warning("Task %s: invalid transition %s -> FAILED (agent error)", task.id, task.status.value)
                task.status = TaskStatus.FAILED
                task.completed_at = _utcnow()
                task.error_message = "Agent encountered an error"
                db.commit()
                from websocket import emit_task_update
                self._emit(emit_task_update(
                    task.id, task.status.value, task.project_name or "",
                    title=task.title,
                ))

        # --- MERGING tasks: legacy cleanup ---
        # Merges are now performed synchronously in approve_task_v2.
        # Fail any stale MERGING tasks that may have been left over.
        stale_merging = (
            db.query(Task)
            .filter(Task.status == TaskStatus.MERGING)
            .all()
        )
        for task in stale_merging:
            try:
                from task_state_machine import validate_transition
                validate_transition(task.status, TaskStatus.FAILED)
            except Exception:
                logger.warning("Task %s: invalid transition %s -> FAILED (stale merge)", task.id, task.status.value)
            # Stop linked agent if still running/idle
            if task.agent_id:
                agent = db.get(Agent, task.agent_id)
                if agent and agent.status not in (AgentStatus.STOPPED, AgentStatus.ERROR):
                    agent.status = AgentStatus.STOPPED
                    if agent.tmux_pane:
                        import subprocess as _sp
                        _sp.run(["tmux", "kill-session", "-t", f"ah-{agent.id[:8]}"],
                                capture_output=True, timeout=5)
                        agent.tmux_pane = None
            # Stop verify sub-agents
            import subprocess as _sp
            for va in (
                db.query(Agent)
                .filter(Agent.task_id == task.id, Agent.is_subagent == True, Agent.name.like("Verify:%"))
                .filter(Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]))
                .all()
            ):
                va.status = AgentStatus.STOPPED
                if va.tmux_pane:
                    _sp.run(["tmux", "kill-session", "-t", f"ah-{va.id[:8]}"], capture_output=True, timeout=5)
                    va.tmux_pane = None
            task.status = TaskStatus.FAILED
            task.completed_at = _utcnow()
            task.error_message = "Stale merge task — please re-approve"
            db.commit()
            from websocket import emit_task_update
            self._emit(emit_task_update(
                task.id, task.status.value, task.project_name or "",
                title=task.title,
            ))
            logger.info("Task %s: failed stale MERGING task", task.id)

    def _harvest_verify_completions(self, db: Session):
        """Check verification sub-agents — when done, update task review_artifacts."""
        verify_agents = (
            db.query(Agent)
            .filter(
                Agent.is_subagent == True,
                Agent.name.like("Verify:%"),
                Agent.task_id.isnot(None),
                Agent.status.in_([AgentStatus.STOPPED, AgentStatus.ERROR, AgentStatus.IDLE]),
            )
            .all()
        )
        for agent in verify_agents:
            task = db.get(Task, agent.task_id)
            if not task:
                continue

            # Parse current review_artifacts
            import json as _json
            artifacts = {}
            if task.review_artifacts:
                try:
                    artifacts = _json.loads(task.review_artifacts)
                except Exception:
                    artifacts = {}

            # Skip if already harvested
            if artifacts.get("verify_status") != "running":
                continue
            if artifacts.get("verify_agent_id") != agent.id:
                continue

            # Get the agent's last message (verification result)
            last_msg = (
                db.query(Message)
                .filter(Message.agent_id == agent.id, Message.role == MessageRole.AGENT)
                .order_by(Message.created_at.desc())
                .first()
            )

            # Skip IDLE agents that haven't been dispatched yet (no messages)
            if agent.status == AgentStatus.IDLE and not last_msg:
                continue

            if agent.status == AgentStatus.ERROR or not last_msg:
                artifacts["verify_status"] = "error"
                artifacts["verify_result"] = "Verification agent failed without output"
            else:
                content = last_msg.content or ""
                # Parse verdict from output
                verdict = "UNKNOWN"
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("VERDICT:"):
                        verdict = stripped.split(":", 1)[1].strip().upper()
                        break

                if verdict.startswith("PASS"):
                    artifacts["verify_status"] = "pass"
                elif verdict.startswith("FAIL"):
                    artifacts["verify_status"] = "fail"
                elif verdict.startswith("WARN"):
                    artifacts["verify_status"] = "warn"
                else:
                    artifacts["verify_status"] = "done"
                artifacts["verify_result"] = content[:3000]

            task.review_artifacts = _json.dumps(artifacts)
            db.commit()

            from websocket import emit_task_update
            self._emit(emit_task_update(
                task.id, task.status.value, task.project_name or "",
                title=task.title,
            ))
            logger.info("Task %s: verification %s (agent %s)", task.id, artifacts["verify_status"], agent.id)

    def _tick(self, db: Session):
        # Invalidate per-tick tmux map cache
        self._tmux_map_cache = None

        # Refresh tmux pane-attached cache for notification suppression
        self._refresh_pane_attached()

        # 0pre. Check scheduled tasks (notify_at reminders + dispatch_at auto-dispatch)
        self._check_scheduled_tasks(db)

        # 0a. Dispatch PENDING v2 tasks → create agents
        self._dispatch_pending_tasks(db)

        # 0b. Harvest v2 task completions (agent done → REVIEW)
        self._harvest_task_completions(db)

        # 0c. Harvest verification agent completions
        self._harvest_verify_completions(db)

        # 0. Early session_id assignment — grab session_id from output init
        #    event as soon as Claude starts, so auto-detect can see it.
        self._assign_early_session_ids(db)

        # 1. Harvest completed execs
        self._harvest_completed_execs(db)

        # 2. Check exec timeouts
        self._check_exec_timeouts(db)

        # 3. Start new agents
        self._start_new_agents(db)

        # 4. Dispatch pending messages to idle agents
        self._dispatch_pending_messages(db)

        # 4b. Dispatch due scheduled messages to SYNCING agents via tmux
        self._dispatch_tmux_pending(db)

        # 5. Auto-detect CLI sessions + pane dedup + reap dead agents (every ~30s)
        self._cli_detect_counter += 1
        if self._cli_detect_counter >= self._cli_detect_interval:
            self._cli_detect_counter = 0
            self._auto_detect_cli_sessions(db)
            self._dedup_pane_agents(db)
            self._reap_dead_agents(db)

        db.commit()

    def _assign_early_session_ids(self, db: Session):
        """Assign session_id to executing agents as soon as the init event appears.

        This runs every tick so that the auto-detect scanner can see session_ids
        from agents that are still mid-execution, using the same logic for all
        agents regardless of how they were started.
        """
        for agent_id, info in self._active_execs.items():
            agent = db.get(Agent, agent_id)
            if not agent or agent.session_id:
                continue  # Already has a session_id
            output_file = info.get("output_file", "")
            if not output_file or not os.path.isfile(output_file):
                continue
            sid = _extract_session_id_from_output(output_file)
            if sid:
                agent.session_id = sid
                logger.debug("Early session_id %s assigned to agent %s", sid[:12], agent_id)

    # ---- Step 1: Harvest completed execs ----

    def _harvest_completed_execs(self, db: Session):
        """Check active execs that have finished."""
        done_agents = []
        for agent_id, info in list(self._active_execs.items()):
            agent = db.get(Agent, agent_id)

            # If agent was stopped by user, kill the process and clean up
            if not agent or agent.status == AgentStatus.STOPPED:
                self.worker_mgr.stop_worker(info["pid_str"])
                message = db.get(Message, info["message_id"])
                if message and message.status == MessageStatus.EXECUTING:
                    message.status = MessageStatus.FAILED
                    message.error_message = "Agent stopped by user"
                    message.completed_at = _utcnow()
                    from websocket import emit_message_update
                    self._emit(emit_message_update(agent_id, message.id, "FAILED",
                                                   error_message=message.error_message))
                done_agents.append(agent_id)
                continue

            if self.worker_mgr.is_exec_running(info["pid_str"]):
                continue

            # Exec finished — read output

            logs = self.worker_mgr.read_exec_output(
                info["pid_str"], info["output_file"]
            )
            result_text, result_meta_json = _extract_result(logs)

            # Check process exit code
            proc_info = self.worker_mgr._processes.get(info["pid_str"])
            exit_code = proc_info["process"].returncode if proc_info else None

            # Save the session_id that was used for --resume (before it gets
            # overwritten by the new one from the result event)
            previous_session_id = agent.session_id

            # Determine success/failure from exit code + stream-json result event
            is_error = (exit_code is not None and exit_code != 0) or _is_result_error(logs)

            # Extract and store session_id for --resume on follow-ups
            sid = _extract_session_id(logs)
            if sid and not is_error:
                agent.session_id = sid
                # Cache the new session and evict the old one.
                # When Claude assigns a new session_id on --resume, the new
                # file contains the full conversation — the old is redundant.
                project = db.get(Project, agent.project)
                if project:
                    try:
                        cache_session(sid, project.path)
                        if previous_session_id and previous_session_id != sid:
                            self._release_session(
                                previous_session_id, agent.id,
                                project.path, agent.worktree, db,
                            )
                    except OSError:
                        logger.warning("Failed to cache session %s", sid, exc_info=True)

            # Update the message that triggered this exec
            message = db.get(Message, info["message_id"])
            if message:
                message.status = MessageStatus.COMPLETED
                message.completed_at = _utcnow()
                from websocket import emit_message_update
                self._emit(emit_message_update(agent_id, message.id, "COMPLETED"))

            # Auto-recover from stale session: try cache restore + repair first.
            # Use previous_session_id (the one used for --resume) for cache lookup,
            # since the result event may contain a different (new) session_id.
            # Track retries to avoid infinite loops when restore keeps failing.
            is_stale_session = (
                is_error
                and result_text
                and "session's conversation data is no longer available" in result_text
            )
            restore_sid = previous_session_id or agent.session_id
            if is_stale_session and restore_sid:
                retry_count = self._stale_session_retries.get(agent_id, 0) + 1
                self._stale_session_retries[agent_id] = retry_count

                if retry_count > self._max_stale_retries:
                    logger.warning(
                        "Agent %s: stale session %s, exhausted %d retries — clearing session_id",
                        agent.id, restore_sid, self._max_stale_retries,
                    )
                    project = db.get(Project, agent.project)
                    self._release_session(
                        restore_sid, agent.id,
                        project.path if project else None,
                        agent.worktree, db,
                    )
                    agent.session_id = None
                    self._stale_session_retries.pop(agent_id, None)
                    # Fall through to normal error handling below
                else:
                    project = db.get(Project, agent.project)
                    project_path = project.path if project else None

                    restored = False
                    if project_path:
                        restored = restore_session(restore_sid, project_path)
                        if restored:
                            repair_session_jsonl(restore_sid, project_path)
                            agent.session_id = restore_sid
                            logger.info(
                                "Agent %s: restored session %s from cache (attempt %d) — re-queuing",
                                agent.id, restore_sid, retry_count,
                            )

                    if not restored:
                        logger.warning(
                            "Agent %s: stale session %s, no cache — clearing session_id (attempt %d)",
                            agent.id, restore_sid, retry_count,
                        )
                        self._release_session(
                            restore_sid, agent.id,
                            project_path, agent.worktree, db,
                        )
                        agent.session_id = None

                    if message:
                        message.status = MessageStatus.PENDING
                        message.completed_at = None
                        from websocket import emit_message_update
                        self._emit(emit_message_update(agent_id, message.id, "PENDING"))
                    # cli_sync agents should return to SYNCING (not IDLE)
                    # so the sync loop can resume watching the session.
                    agent.status = AgentStatus.SYNCING if agent.cli_sync else AgentStatus.IDLE
                    done_agents.append(agent_id)
                    continue

            # Guard: re-read agent status to check if it was stopped by
            # the API while we were processing.  If so, don't overwrite.
            # Save dirty attributes BEFORE refresh — db.refresh() with
            # autoflush=False overwrites in-memory changes (like session_id
            # set at line 1316) with stale DB values.
            saved_session_id = agent.session_id
            db.refresh(agent)
            if agent.status == AgentStatus.STOPPED:
                done_agents.append(agent_id)
                continue
            # Restore session_id that was extracted from the exec output.
            # The refresh may have reverted it to the old DB value.
            agent.session_id = saved_session_id

            # cli_sync agents return to SYNCING so the sync loop can
            # resume watching the session JSONL; others go to IDLE.
            post_exec_status = AgentStatus.SYNCING if agent.cli_sync else AgentStatus.IDLE

            if is_error:
                resp = Message(
                    agent_id=agent.id,
                    role=MessageRole.AGENT,
                    content=result_text or "Agent encountered an error",
                    status=MessageStatus.FAILED,
                    stream_log=_truncate(logs, 50000),
                    error_message=result_text[:200] if result_text else "Unknown error",
                    meta_json=result_meta_json,
                )
                db.add(resp)
                agent.status = post_exec_status
            else:
                resp = Message(
                    agent_id=agent.id,
                    role=MessageRole.AGENT,
                    content=result_text,
                    status=MessageStatus.COMPLETED,
                    stream_log=_truncate(logs, 50000),
                    meta_json=result_meta_json,
                )
                db.add(resp)
                agent.status = post_exec_status
                # Successful completion — reset retry counters
                self._stale_session_retries.pop(agent_id, None)
                self._timeout_retries.pop(info["message_id"], None)

            # Auto-continue after ExitPlanMode in exec mode.
            # When a non-cli_sync agent calls ExitPlanMode, the CLI
            # auto-approves (no tmux) and exits.  Create a follow-up
            # PENDING message so the next dispatch cycle resumes the
            # agent and executes the plan.
            if result_meta_json and not agent.cli_sync:
                try:
                    _meta = json.loads(result_meta_json)
                    _has_exit_plan = any(
                        item.get("type") == "exit_plan_mode"
                        for item in _meta.get("interactive", [])
                    )
                    if _has_exit_plan:
                        # Guard: don't auto-continue if this exec was
                        # itself a plan follow-up (prevents infinite loop).
                        trigger_msg = db.get(Message, info["message_id"])
                        is_already_followup = (
                            trigger_msg
                            and trigger_msg.source == "plan_continue"
                        )
                        if not is_already_followup:
                            follow_up = Message(
                                agent_id=agent.id,
                                role=MessageRole.USER,
                                content=(
                                    "Plan approved. Proceed with implementation now. "
                                    "Do not re-plan — execute the plan directly."
                                ),
                                status=MessageStatus.PENDING,
                                source="plan_continue",
                            )
                            db.add(follow_up)
                            logger.info(
                                "Auto-created plan execution follow-up for agent %s",
                                agent.id,
                            )
                except (json.JSONDecodeError, TypeError):
                    pass

            # Update agent denormalized fields
            preview = (result_text or "")[:200]
            agent.last_message_preview = preview
            agent.last_message_at = _utcnow()
            is_viewed = self._is_agent_in_use(agent.id, agent.tmux_pane)
            if not is_viewed:
                agent.unread_count += 1

            save_worker_log(f"agent-{agent.id}", logs)

            from websocket import emit_agent_update, emit_new_message
            self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
            self._emit(emit_new_message(agent.id, resp.id, agent.name, agent.project))

            if not agent.muted and not is_viewed:
                from push import send_push_notification, is_notification_enabled
                if is_notification_enabled("agents"):
                    status_emoji = "\u274c" if is_error else "\u2705"
                    logger.info(
                        "PUSH_DEBUG: harvest_completed sending for %s: %s",
                        agent.id, preview[:50],
                    )
                    send_push_notification(
                        title=f"{status_emoji} {agent.name}",
                        body=preview[:100],
                        url=f"/agents/{agent.id}",
                    )

            # Generate video thumbnails in background thread
            if result_text:
                _proj = db.get(Project, agent.project)
                if _proj:
                    asyncio.ensure_future(asyncio.to_thread(
                        generate_thumbnails_for_message, result_text, _proj.path,
                    ))

            done_agents.append(agent_id)

        for agent_id in done_agents:
            info = self._active_execs.pop(agent_id, None)
            self._cancel_stream_task(agent_id)
            # Clean up output file to prevent /tmp accumulation
            if info:
                output_file = info.get("output_file", "")
                if output_file:
                    try:
                        os.remove(output_file)
                    except OSError:
                        pass

        # Restart sync tasks for cli_sync agents that returned to SYNCING.
        # The sync loop's reconciliation logic deduplicates turns already
        # imported by the harvest, so no duplicate messages will be created.
        for agent_id in done_agents:
            agent = db.get(Agent, agent_id)
            if (
                agent
                and agent.cli_sync
                and agent.session_id
                and agent.status == AgentStatus.SYNCING
            ):
                project = db.get(Project, agent.project)
                if project:
                    self.start_session_sync(agent_id, agent.session_id, project.path)
                    logger.info(
                        "Restarted sync task for cli_sync agent %s after exec",
                        agent_id,
                    )

    # ---- Step 2: Timeouts ----

    def _check_exec_timeouts(self, db: Session):
        """Kill execs that have been idle (no new output) for too long."""
        now = _utcnow()
        timed_out = []
        for agent_id, info in list(self._active_execs.items()):
            agent = db.get(Agent, agent_id)
            if not agent:
                timed_out.append(agent_id)
                continue

            last_activity = info.get("last_activity", info["started_at"])
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
            idle_seconds = (now - last_activity).total_seconds()
            elapsed = (now - info["started_at"].replace(tzinfo=timezone.utc)
                        if info["started_at"].tzinfo is None
                        else now - info["started_at"]).total_seconds()

            if idle_seconds > agent.timeout_seconds:
                logger.warning(
                    "Agent %s exec timed out: idle %ds, total %ds (limit %ds)",
                    agent.id, int(idle_seconds), int(elapsed), agent.timeout_seconds,
                )

                # Kill the process
                self.worker_mgr.stop_worker(info["pid_str"])

                # Read whatever output was produced
                logs = self.worker_mgr.read_exec_output(
                    info["pid_str"], info["output_file"]
                )

                # Update message — auto-retry if under the limit
                message = db.get(Message, info["message_id"])
                retry_count = self._timeout_retries.get(info["message_id"], 0)
                will_retry = message and retry_count < self._max_timeout_retries

                if message:
                    if will_retry:
                        retry_count += 1
                        self._timeout_retries[info["message_id"]] = retry_count
                        message.status = MessageStatus.PENDING
                        message.error_message = (
                            f"Timed out after {int(idle_seconds)}s of inactivity "
                            f"(auto-retry {retry_count}/{self._max_timeout_retries})"
                        )
                        message.completed_at = None
                        logger.info(
                            "Agent %s message %s: auto-retry %d/%d after timeout",
                            agent_id, message.id, retry_count, self._max_timeout_retries,
                        )
                    else:
                        message.status = MessageStatus.TIMEOUT
                        message.error_message = f"Timed out after {int(idle_seconds)}s of inactivity"
                        message.completed_at = now
                        self._timeout_retries.pop(info["message_id"], None)
                    from websocket import emit_message_update
                    self._emit(emit_message_update(agent_id, message.id, message.status.value))

                # Create system message
                timeout_note = f"Timed out after {int(idle_seconds)}s of inactivity (ran {int(elapsed)}s total)"
                if will_retry:
                    timeout_note += f" — auto-retrying ({retry_count}/{self._max_timeout_retries})"
                sys_msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.SYSTEM,
                    content=timeout_note,
                    status=MessageStatus.COMPLETED,
                )
                db.add(sys_msg)

                # Guard: check agent wasn't stopped by API during timeout handling
                db.refresh(agent)
                if agent.status != AgentStatus.STOPPED:
                    agent.status = AgentStatus.SYNCING if agent.cli_sync else AgentStatus.IDLE
                agent.last_message_preview = timeout_note
                agent.last_message_at = now
                is_viewed = self._is_agent_in_use(agent.id, agent.tmux_pane)
                if not is_viewed:
                    agent.unread_count += 1

                from websocket import emit_agent_update, emit_new_message
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                self._emit(emit_new_message(agent.id, sys_msg.id, agent.name, agent.project))

                if not agent.muted and not is_viewed:
                    from push import send_push_notification, is_notification_enabled
                    if is_notification_enabled("agents"):
                        send_push_notification(
                            title=f"\u23f0 {agent.name}",
                            body=timeout_note,
                            url=f"/agents/{agent.id}",
                        )

                timed_out.append(agent_id)

        for agent_id in timed_out:
            info = self._active_execs.pop(agent_id, None)
            self._cancel_stream_task(agent_id)
            if info:
                output_file = info.get("output_file", "")
                if output_file:
                    try:
                        os.remove(output_file)
                    except OSError:
                        pass

        # Restart sync tasks for timed-out cli_sync agents
        for agent_id in timed_out:
            agent = db.get(Agent, agent_id)
            if (
                agent
                and agent.cli_sync
                and agent.session_id
                and agent.status == AgentStatus.SYNCING
            ):
                project = db.get(Project, agent.project)
                if project:
                    self.start_session_sync(agent_id, agent.session_id, project.path)

    # ---- Step 4: Start new agents ----

    def _start_new_agents(self, db: Session):
        """Validate project dirs for STARTING agents and set them to IDLE.

        Skips cli_sync agents — they follow a different lifecycle
        (STARTING → SYNCING via the background launch task).
        """
        starting = db.query(Agent).filter(
            Agent.status == AgentStatus.STARTING,
            Agent.cli_sync == False,
        ).all()

        for agent in starting:
            project = db.get(Project, agent.project)
            if not project:
                agent.status = AgentStatus.ERROR
                msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.SYSTEM,
                    content=f"Project '{agent.project}' not found",
                    status=MessageStatus.FAILED,
                )
                db.add(msg)

                from websocket import emit_agent_update, emit_new_message
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                self._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))
                if not agent.muted and not self._is_agent_in_use(agent.id, agent.tmux_pane):
                    from push import send_push_notification, is_notification_enabled
                    if is_notification_enabled("agents"):
                        send_push_notification(
                            title=f"\u274c {agent.name}",
                            body=f"Project '{agent.project}' not found",
                            url=f"/agents/{agent.id}",
                        )
                continue

            try:
                project_path = self.worker_mgr.ensure_project_ready(project)
                agent.status = AgentStatus.IDLE

                sys_msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.SYSTEM,
                    content="Agent started",
                    status=MessageStatus.COMPLETED,
                )
                db.add(sys_msg)

                logger.info("Agent %s started (project: %s)", agent.id, project.name)
                from websocket import emit_agent_update
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
            except Exception:
                logger.exception("Failed to start agent %s", agent.id)
                agent.status = AgentStatus.ERROR
                msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.SYSTEM,
                    content="Failed to start — project directory not found",
                    status=MessageStatus.FAILED,
                )
                db.add(msg)

                from websocket import emit_agent_update, emit_new_message
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                self._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))
                if not agent.muted and not self._is_agent_in_use(agent.id, agent.tmux_pane):
                    from push import send_push_notification, is_notification_enabled
                    if is_notification_enabled("agents"):
                        send_push_notification(
                            title=f"\u274c {agent.name}",
                            body="Failed to start — project directory not found",
                            url=f"/agents/{agent.id}",
                        )

    # ---- Step 4: Dispatch pending messages ----

    def _dispatch_pending_messages(self, db: Session):
        """For IDLE agents with PENDING user messages, exec claude."""
        from websocket import emit_agent_update

        # SYNCING cli_sync agents without a pane: retry pane re-detection for a
        # short grace window before declaring them dead. This avoids false
        # STOPPED transitions from transient tmux lookup failures.
        syncing_no_pane = db.query(Agent).filter(
            Agent.status == AgentStatus.SYNCING,
            Agent.cli_sync == True,
            Agent.tmux_pane.is_(None),
        ).all()
        syncing_no_pane_ids = {a.id for a in syncing_no_pane}
        for aid in list(self._syncing_no_pane_retries.keys()):
            if aid not in syncing_no_pane_ids:
                self._syncing_no_pane_retries.pop(aid, None)

        for agent in syncing_no_pane:
            # Attempt pane re-detection first
            project = db.get(Project, agent.project)
            if project and agent.session_id:
                pane = _detect_tmux_pane_for_session(agent.session_id, project.path)
                if pane and verify_tmux_pane(pane):
                    agent.tmux_pane = pane
                    self._syncing_no_pane_retries.pop(agent.id, None)
                    continue  # Pane found — let _dispatch_tmux_pending handle it

            retries = self._syncing_no_pane_retries.get(agent.id, 0) + 1
            self._syncing_no_pane_retries[agent.id] = retries
            if retries < self._max_syncing_no_pane_retries:
                logger.warning(
                    "Agent %s SYNCING with no tmux pane (%d/%d) — waiting for re-detect",
                    agent.id, retries, self._max_syncing_no_pane_retries,
                )
                continue

            # Grace window exhausted — stop the agent
            self._syncing_no_pane_retries.pop(agent.id, None)
            agent.status = AgentStatus.STOPPED
            agent.tmux_pane = None
            db.add(Message(
                agent_id=agent.id, role=MessageRole.SYSTEM,
                content="CLI session ended — tmux pane not found",
                status=MessageStatus.COMPLETED,
            ))
            # Fail any pending messages
            pending = db.query(Message).filter(
                Message.agent_id == agent.id,
                Message.status == MessageStatus.PENDING,
            ).all()
            from websocket import emit_message_update
            for m in pending:
                m.status = MessageStatus.FAILED
                m.error_message = "Agent tmux session no longer exists"
                self._emit(emit_message_update(agent.id, m.id, "FAILED",
                                               error_message=m.error_message))
            # NOTE: removed db.commit() here — it was flushing ALL dirty
            # objects in the session (including harvest changes from step 1),
            # making re-queued messages visible to the dispatch query below
            # and causing same-tick double-dispatch (Bug: duplicate AGENT
            # responses).  The final db.commit() in _tick() handles
            # everything atomically.
            self._emit(emit_agent_update(agent.id, "STOPPED", agent.project))
            logger.info(
                "Stopped dead SYNCING agent %s — tmux pane gone", agent.id,
            )

        idle_agents = db.query(Agent).filter(
            Agent.status == AgentStatus.IDLE,
        ).all()

        executing_count = db.query(Agent).filter(
            Agent.status == AgentStatus.EXECUTING
        ).count()

        for agent in idle_agents:
            if agent.id in self._active_execs:
                continue
            if executing_count >= MAX_CONCURRENT_WORKERS:
                break

            # Check per-project concurrency
            project = db.get(Project, agent.project)
            if not project:
                continue
            proj_executing = db.query(Agent).filter(
                Agent.project == agent.project,
                Agent.status == AgentStatus.EXECUTING,
            ).count()
            if proj_executing >= project.max_concurrent:
                continue

            # Find the oldest pending user message (skip scheduled ones not yet due)
            pending_msg = (
                db.query(Message)
                .filter(
                    Message.agent_id == agent.id,
                    Message.role == MessageRole.USER,
                    Message.status == MessageStatus.PENDING,
                    (Message.scheduled_at == None) | (Message.scheduled_at <= _utcnow()),
                )
                .order_by(Message.created_at.asc())
                .first()
            )
            if not pending_msg:
                continue

            # Defense-in-depth: skip if this message is already being
            # executed by another active exec (prevents double-dispatch
            # if harvest re-queued a message in the same tick).
            already_dispatched = any(
                info["message_id"] == pending_msg.id
                for info in self._active_execs.values()
            )
            if already_dispatched:
                logger.warning(
                    "Skipping message %s — already in active_execs (double-dispatch guard)",
                    pending_msg.id,
                )
                continue

            # Ensure project directory exists
            try:
                project_path = self.worker_mgr.ensure_project_ready(project)
                self._project_ready_failures.pop(project.name, None)
            except Exception:
                count = self._project_ready_failures.get(project.name, 0) + 1
                self._project_ready_failures[project.name] = count
                logger.exception(
                    "Project dir not ready for %s (attempt %d/%d)",
                    project.name, count, self._max_project_ready_failures,
                )
                if count >= self._max_project_ready_failures:
                    agent.status = AgentStatus.ERROR
                    pending_msg.status = MessageStatus.FAILED
                    pending_msg.error_message = (
                        f"Project directory not ready after {count} attempts"
                    )
                    msg = Message(
                        agent_id=agent.id,
                        role=MessageRole.SYSTEM,
                        content=f"Project directory for '{project.name}' is not accessible — agent stopped",
                        status=MessageStatus.COMPLETED,
                    )
                    db.add(msg)
                    # NOTE: removed db.commit() — same-tick flush of ALL
                    # dirty objects caused double-dispatch.  Let _tick()
                    # commit atomically at the end.
                    self._emit(emit_agent_update(agent.id, "ERROR", agent.project))
                    from websocket import emit_message_update
                    self._emit(emit_message_update(agent.id, pending_msg.id, "FAILED",
                                                   error_message=pending_msg.error_message))
                continue

            # Use --resume with session_id if available.
            # Pre-check: if the session file is missing, restore from cache
            # now instead of waiting for Claude to error out (~5s wasted).
            resume_session_id = agent.session_id or None
            if resume_session_id:
                jsonl_path = _resolve_session_jsonl(
                    resume_session_id, project_path, agent.worktree,
                )
                if not os.path.exists(jsonl_path):
                    restored = restore_session(resume_session_id, project_path)
                    if restored:
                        repair_session_jsonl(resume_session_id, project_path)
                        logger.info(
                            "Pre-restored session %s for agent %s",
                            resume_session_id, agent.id,
                        )
                    else:
                        logger.info(
                            "Session %s missing, no cache — starting fresh for agent %s",
                            resume_session_id, agent.id,
                        )
                        self._release_session(
                            resume_session_id, agent.id,
                            project_path, agent.worktree, db,
                        )
                        agent.session_id = None
                        resume_session_id = None

            # Refresh agent from DB to catch concurrent status changes
            # (e.g. user stopped the agent via API while we were preparing)
            db.refresh(agent)
            if agent.status not in (AgentStatus.IDLE, AgentStatus.SYNCING):
                continue
            if agent.id in self._active_execs:
                continue

            # Build the prompt — session cache handles continuity, no fake history
            prompt = self._build_agent_prompt(
                agent, project, pending_msg.content,
                include_history=False, db=db,
            )

            try:
                pid_str, output_file = self.worker_mgr.exec_claude_in_agent(
                    project_path, prompt, project, agent,
                    resume_session_id=resume_session_id,
                    message_id=pending_msg.id,
                )
                self._active_execs[agent.id] = {
                    "pid_str": pid_str,
                    "output_file": output_file,
                    "message_id": pending_msg.id,
                    "started_at": _utcnow(),
                    "last_activity": _utcnow(),
                    "tmux_pane": agent.tmux_pane,
                }
                # Cancel sync task before changing status — the sync loop
                # would exit on its own when it sees non-SYNCING, but
                # explicit cancel is cleaner and avoids a race window.
                if agent.cli_sync:
                    self._cancel_sync_task(agent.id)
                agent.status = AgentStatus.EXECUTING
                if agent.worktree:
                    agent.branch = f"worktree-{agent.worktree}"
                pending_msg.status = MessageStatus.EXECUTING
                executing_count += 1

                # Start streaming output to frontend
                self._start_stream_task(agent.id, output_file)

                logger.info(
                    "Dispatched message %s to agent %s (resume=%s)",
                    pending_msg.id, agent.id, bool(resume_session_id),
                )
                from websocket import emit_agent_update, emit_message_update
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                self._emit(emit_message_update(agent.id, pending_msg.id, "EXECUTING"))
            except Exception:
                logger.exception(
                    "Failed to exec claude for agent %s", agent.id
                )
                pending_msg.status = MessageStatus.FAILED
                pending_msg.error_message = "Failed to start claude process"
                from websocket import emit_message_update
                self._emit(emit_message_update(agent.id, pending_msg.id, "FAILED",
                                               error_message=pending_msg.error_message))

    def _dispatch_tmux_pending(self, db: Session):
        """Send pending messages to SYNCING/STARTING agents via tmux.

        Handles both scheduled messages whose time has arrived AND
        non-scheduled queued messages (e.g. from "Send now" or messages
        queued while the agent was busy).
        """
        active_sync_agents = db.query(Agent).filter(
            Agent.status.in_([AgentStatus.SYNCING, AgentStatus.STARTING]),
            Agent.cli_sync == True,
            Agent.tmux_pane.is_not(None),
        ).all()

        for agent in active_sync_agents:
            # Refresh to catch concurrent status changes (e.g. user stopped agent)
            db.refresh(agent)
            if agent.status not in (AgentStatus.SYNCING, AgentStatus.STARTING) or not agent.tmux_pane:
                continue

            due_msg = (
                db.query(Message)
                .filter(
                    Message.agent_id == agent.id,
                    Message.role == MessageRole.USER,
                    Message.status == MessageStatus.PENDING,
                    (Message.scheduled_at == None) | (Message.scheduled_at <= _utcnow()),
                )
                .order_by(Message.created_at.asc())
                .first()
            )
            if not due_msg:
                continue

            if not verify_tmux_pane(agent.tmux_pane):
                logger.warning(
                    "Tmux pane %s gone for SYNCING agent %s — clearing pane",
                    agent.tmux_pane, agent.id,
                )
                agent.tmux_pane = None
                # Don't transition to STOPPED here — the sync loop's
                # liveness check handles that with a grace period.
                # But we must skip this agent so messages don't pile up.
                continue

            ok = send_tmux_message(agent.tmux_pane, due_msg.content)
            if ok:
                due_msg.status = MessageStatus.COMPLETED
                due_msg.completed_at = _utcnow()
                due_msg.scheduled_at = None
                logger.info(
                    "Dispatched pending message %s to SYNCING agent %s via tmux",
                    due_msg.id, agent.id,
                )
                from websocket import emit_message_update
                self._emit(emit_message_update(agent.id, due_msg.id, "COMPLETED"))
            else:
                due_msg.status = MessageStatus.FAILED
                due_msg.error_message = "Failed to send via tmux"
                logger.warning(
                    "Failed to dispatch pending message %s via tmux for agent %s",
                    due_msg.id, agent.id,
                )
                from websocket import emit_message_update
                self._emit(emit_message_update(agent.id, due_msg.id, "FAILED",
                                               error_message=due_msg.error_message))

    def _build_agent_prompt(
        self, agent: Agent, project: Project, user_message: str,
        include_history: bool = False, db: Session | None = None,
    ) -> str:
        """Build the prompt sent to claude for an agent message.
        When include_history=True, injects recent conversation history so context
        is preserved even when the Claude Code session can't be resumed.
        """
        history_block = ""
        if include_history and db:
            history_block = self._format_conversation_history(agent, db)

        return (
            f"You are working in project: {project.display_name}\n"
            f"Project path: {project.path}\n"
            f"\n"
            f"First read the project's CLAUDE.md to understand project conventions.\n"
            f"{history_block}\n"
            f"{user_message}\n\n"
            f"If you make code changes, commit with message format: "
            f"[agent-{agent.id}] short description"
        )

    def _format_conversation_history(self, agent: Agent, db: Session) -> str:
        """Format recent conversation messages as context for a fresh session."""
        recent = (
            db.query(Message)
            .filter(
                Message.agent_id == agent.id,
                Message.role.in_([MessageRole.USER, MessageRole.AGENT]),
                Message.status.in_([MessageStatus.COMPLETED, MessageStatus.FAILED, MessageStatus.TIMEOUT]),
            )
            .order_by(Message.created_at.desc())
            .limit(20)
            .all()
        )
        if not recent:
            return ""

        recent.reverse()  # chronological order
        lines = ["\n--- Previous conversation history (for context) ---"]
        for msg in recent:
            role = "User" if msg.role == MessageRole.USER else "Agent"
            # Truncate long agent responses to keep prompt manageable
            content = msg.content
            if role == "Agent" and len(content) > 500:
                content = content[:500] + "… [truncated]"
            lines.append(f"[{role}]: {content}")
        lines.append("--- End of history ---\n")
        return "\n".join(lines)


    def _auto_detect_cli_sessions(self, db: Session):
        """Detect interactive claude processes in tmux and sync them.

        Scans tmux panes for non-orchestrator claude processes whose CWD
        matches a registered project.  For each match, finds the most
        recently modified session JSONL and creates (or revives) a
        SYNCING agent.
        """
        from websocket import emit_agent_update

        # Get all registered (non-archived) projects, keyed by realpath
        projects = db.query(Project).filter(Project.archived == False).all()
        if not projects:
            return
        proj_by_path: dict[str, Project] = {
            os.path.realpath(p.path): p for p in projects
        }

        # Expire cached ORM state so we see commits from other DB sessions
        # (e.g. successor spawns that run in their own SessionLocal).
        db.expire_all()

        # Collect session IDs / tmux panes already owned by active agents
        active_session_ids: set[str] = set()
        active_tmux_panes: set[str] = set()
        stopped_session_agents: dict[str, Agent] = {}
        # Track ALL session_ids (including stopped) so we can prevent
        # reassigning a session that another agent already owns.
        all_agent_session_ids: set[str] = set()
        for a in db.query(Agent).filter(Agent.session_id.is_not(None)).all():
            all_agent_session_ids.add(a.session_id)
            if a.status == AgentStatus.STOPPED:
                stopped_session_agents[a.session_id] = a
            else:
                active_session_ids.add(a.session_id)
                if a.tmux_pane:
                    active_tmux_panes.add(a.tmux_pane)
        # Also protect panes owned by STARTING agents (no session_id yet,
        # still being set up by _launch_tmux_background).
        for a in db.query(Agent).filter(
            Agent.status == AgentStatus.STARTING,
            Agent.session_id.is_(None),
            Agent.tmux_pane.is_not(None),
        ).all():
            active_tmux_panes.add(a.tmux_pane)

        # Build map of tmux panes → interactive claude processes (cached per tick)
        pane_map = self._get_tmux_map()
        agents_to_sync: list[tuple[str, str, str]] = []

        # Group untracked panes by project path, keyed by PID for matching
        panes_per_project: dict[str, list[tuple[str, int, str]]] = {}  # realpath -> [(pane_id, pid, session_name)]
        for pane_id, info in pane_map.items():
            if info["is_orchestrator"] or pane_id in active_tmux_panes:
                continue
            cwd = info["cwd"]
            # Exact match first, then check if CWD is a subdirectory of a
            # project (handles worktree agents whose CWD is inside
            # .claude/worktrees/<name> under the project root).
            matched_proj_path = None
            if cwd in proj_by_path:
                matched_proj_path = cwd
            else:
                for pp in proj_by_path:
                    if cwd.startswith(pp + "/"):
                        matched_proj_path = pp
                        break
            if matched_proj_path:
                panes_per_project.setdefault(matched_proj_path, []).append(
                    (pane_id, info["pid"], info.get("session_name", ""))
                )

        for proj_path, pane_entries in panes_per_project.items():
            proj = proj_by_path[proj_path]
            session_dir = session_source_dir(proj.path)

            # Collect session dirs to scan: project root + any worktree dirs
            # (worktree agents store sessions in separate Claude project dirs)
            session_dirs_to_scan = []
            if os.path.isdir(session_dir):
                session_dirs_to_scan.append(session_dir)
            # Also scan worktree session dirs for panes with worktree CWDs
            for _pane_id, _pane_pid, _tmux_sname in pane_entries:
                pinfo = pane_map.get(_pane_id)
                if pinfo:
                    pcwd = pinfo["cwd"]
                    if pcwd != proj_path and pcwd.startswith(proj_path + "/"):
                        wt_sdir = session_source_dir(pcwd)
                        if os.path.isdir(wt_sdir) and wt_sdir not in session_dirs_to_scan:
                            session_dirs_to_scan.append(wt_sdir)
            if not session_dirs_to_scan:
                continue

            # Collect available session JSONLs sorted by mtime descending
            candidates: list[tuple[str, str, float]] = []  # (sid, fpath, mtime)
            seen_sids: set[str] = set()
            for sdir in session_dirs_to_scan:
                try:
                    for fname in os.listdir(sdir):
                        if not fname.endswith(".jsonl"):
                            continue
                        fpath = os.path.join(sdir, fname)
                        if not os.path.isfile(fpath):
                            continue
                        sid = fname.replace(".jsonl", "")
                        if sid in all_agent_session_ids or sid in seen_sids:
                            continue
                        seen_sids.add(sid)
                        candidates.append((sid, fpath, os.path.getmtime(fpath)))
                except OSError as e:
                    logger.warning(
                        "_auto_detect_cli_sessions: failed to scan session dir %s: %s",
                        sdir, e,
                    )
                    continue
            candidates.sort(key=lambda x: x[2], reverse=True)

            # Build PID→session map from debug logs for deterministic matching.
            pid_to_session: dict[int, tuple[str, str]] = {}  # pid -> (sid, fpath)
            for sid, fpath, mtime in candidates:
                session_pid = _get_session_pid(sid)
                if session_pid and session_pid not in pid_to_session:
                    pid_to_session[session_pid] = (sid, fpath)

            # Build mtime-sorted list for Tier 2 fallback (recently active sessions).
            # Used when debug logs don't exist (Claude Code >=2.1.71 no longer
            # writes debug logs by default).
            _now_ts = _time.time()
            _RECENT_THRESHOLD = _STALE_SESSION_THRESHOLD  # 30 min — same as liveness check
            pid_matched_sids = {sid for sid, _ in pid_to_session.values()}
            recent_candidates = [
                (sid, fpath, mtime) for sid, fpath, mtime in candidates
                if (_now_ts - mtime) < _RECENT_THRESHOLD
                and sid not in active_session_ids
                and sid not in pid_matched_sids
            ]

            # Assign sessions to panes: Tier 0 (session name) + Tier 1 (PID) + Tier 2 (mtime)
            for pane_id, pane_pid, tmux_session_name in pane_entries:
                # --- Tier 0: tmux session name → agent ID match ---
                if tmux_session_name.startswith("ah-"):
                    agent_prefix = tmux_session_name[3:]
                    named_agent = db.query(Agent).filter(
                        Agent.id.like(f"{agent_prefix}%"),
                        Agent.status == AgentStatus.STOPPED,
                        Agent.cli_sync == True,
                    ).first()
                    if named_agent:
                        agent_sid = named_agent.session_id
                        if agent_sid:
                            jsonl_path = os.path.join(
                                session_dir, f"{agent_sid}.jsonl"
                            )
                            if not os.path.isfile(jsonl_path) and named_agent.worktree:
                                wt_path = os.path.join(
                                    proj.path, ".claude", "worktrees", named_agent.worktree
                                )
                                wt_jsonl = os.path.join(
                                    session_source_dir(wt_path), f"{agent_sid}.jsonl"
                                )
                                if os.path.isfile(wt_jsonl):
                                    jsonl_path = wt_jsonl
                            if os.path.isfile(jsonl_path):
                                named_agent.status = AgentStatus.SYNCING
                                named_agent.tmux_pane = pane_id
                                named_agent.last_message_at = _utcnow()
                                db.flush()
                                active_session_ids.add(agent_sid)
                                all_agent_session_ids.add(agent_sid)
                                active_tmux_panes.add(pane_id)
                                logger.info(
                                    "Revived agent %s by tmux session name %s (tmux=%s)",
                                    named_agent.id, tmux_session_name, pane_id,
                                )
                                agents_to_sync.append((named_agent.id, agent_sid, proj.path))
                                self._emit(emit_agent_update(named_agent.id, "SYNCING", proj.name))
                                continue
                        # Named agent has no session_id — try Tier 2 to find
                        # one and assign it to this agent (common when agent
                        # previously errored and user restarted claude manually).
                        t2_match = _tier2_match_for_pane(pane_pid, recent_candidates)
                        if t2_match:
                            t2_sid, t2_fpath = t2_match
                            named_agent.session_id = t2_sid
                            named_agent.status = AgentStatus.SYNCING
                            named_agent.tmux_pane = pane_id
                            named_agent.last_message_at = _utcnow()
                            db.flush()
                            active_session_ids.add(t2_sid)
                            all_agent_session_ids.add(t2_sid)
                            active_tmux_panes.add(pane_id)
                            logger.info(
                                "Revived agent %s (Tier 0+2: name=%s, session=%s, pane=%s)",
                                named_agent.id, tmux_session_name, t2_sid[:12], pane_id,
                            )
                            agents_to_sync.append((named_agent.id, t2_sid, proj.path))
                            self._emit(emit_agent_update(named_agent.id, "SYNCING", proj.name))
                            continue

                best_sid, best_fpath = None, None

                # Tier 1: exact PID match (direct OS or debug log)
                os_sid = _detect_pid_session_jsonl(pane_pid)
                if os_sid:
                    for sid, fpath, mtime in candidates:
                        if sid == os_sid:
                            best_sid, best_fpath = sid, fpath
                            # Remove from pid_to_session if it was there (deterministic matching)
                            for p, (s, f) in list(pid_to_session.items()):
                                if s == sid:
                                    pid_to_session.pop(p)
                            break

                if not best_sid and pane_pid in pid_to_session:
                    best_sid, best_fpath = pid_to_session.pop(pane_pid)

                # Tier 2: mtime fallback — match the most recently written
                # unowned session JSONL.  Only used when Tier 1 fails (no
                # debug log, common since Claude Code >=2.1.71).
                # Uses process start time correlation to avoid mismatches
                # when multiple panes exist for the same project.
                if not best_sid and recent_candidates:
                    t2_match = _tier2_match_for_pane(pane_pid, recent_candidates)
                    if t2_match:
                        best_sid, best_fpath = t2_match
                        logger.info(
                            "Tier 2 (mtime) match: pane %s → session %s",
                            pane_id, best_sid[:12],
                        )

                if not best_sid:
                    continue

                active_session_ids.add(best_sid)
                all_agent_session_ids.add(best_sid)
                active_tmux_panes.add(pane_id)

                # --- Try to revive a stopped agent that owns this session ---
                stopped_agent = stopped_session_agents.get(best_sid)
                if stopped_agent:
                    existing_owner = _get_pane_owner(pane_id, exclude_agent_id=stopped_agent.id)
                    if existing_owner:
                        logger.warning(
                            "Skipping revive of %s — pane %s already owned by %s",
                            stopped_agent.id, pane_id, existing_owner.id,
                        )
                        continue
                    stopped_agent.status = AgentStatus.SYNCING
                    stopped_agent.tmux_pane = pane_id
                    stopped_agent.last_message_at = _utcnow()
                    db.flush()
                    logger.info(
                        "Revived stopped agent %s for session %s (tmux=%s)",
                        stopped_agent.id, best_sid[:12], pane_id,
                    )
                    agents_to_sync.append((stopped_agent.id, best_sid, proj.path))
                    self._emit(emit_agent_update(stopped_agent.id, "SYNCING", proj.name))
                    continue

                # --- Session change on existing SYNCING pane → rotate in-place ---
                existing_pane_agent = db.query(Agent).filter(
                    Agent.status == AgentStatus.SYNCING,
                    Agent.tmux_pane == pane_id,
                    Agent.cli_sync == True,
                ).first()
                if existing_pane_agent:
                    expected_session = f"ah-{existing_pane_agent.id[:8]}"
                    if tmux_session_name == expected_session:
                        db.commit()
                        self._rotate_agent_session(
                            existing_pane_agent.id, best_sid, proj.path,
                            worktree=existing_pane_agent.worktree,
                        )
                        continue
                    else:
                        logger.info(
                            "Pane %s session name %r != expected %r — "
                            "stopping old agent %s, creating fresh agent",
                            pane_id, tmux_session_name, expected_session,
                            existing_pane_agent.id,
                        )
                        self._cancel_sync_task(existing_pane_agent.id)
                        existing_pane_agent.status = AgentStatus.STOPPED
                        existing_pane_agent.tmux_pane = None
                        db.flush()
                        self._emit(emit_agent_update(
                            existing_pane_agent.id, "STOPPED", proj.name,
                        ))

                # --- Create new SYNCING agent ---
                agent_name = "CLI session"
                detected_model = None
                turns = []
                if best_fpath:
                    turns = _parse_session_turns(best_fpath)
                    for role, content, *_rest in turns:
                        if role == "user" and content:
                            agent_name = (content or "")[:80]
                            break
                    detected_model = _detect_session_model(best_fpath)

                # Detect worktree name from pane CWD
                detected_worktree = None
                pane_info = pane_map.get(pane_id)
                if pane_info:
                    pcwd = pane_info["cwd"]
                    wt_prefix = os.path.join(proj_path, ".claude", "worktrees") + "/"
                    if pcwd.startswith(wt_prefix):
                        remainder = pcwd[len(wt_prefix):]
                        detected_worktree = remainder.split("/")[0] or None

                logger.info(
                    "Auto-detected tmux CLI session %s in project %s (pane %s, worktree=%s)",
                    best_sid[:12], proj.name, pane_id, detected_worktree,
                )

                agent = Agent(
                    project=proj.name,
                    name=agent_name,
                    mode=AgentMode.AUTO,
                    status=AgentStatus.SYNCING,
                    model=detected_model,
                    session_id=best_sid,
                    cli_sync=True,
                    tmux_pane=pane_id,
                    worktree=detected_worktree,
                    last_message_preview=agent_name,
                    last_message_at=_utcnow(),
                )
                db.add(agent)
                db.flush()

                # Rename tmux session to ah-{id} format
                try:
                    res = _sp.run(
                        ["tmux", "display-message", "-t", pane_id,
                         "-p", "#{session_name}"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        _sp.run(
                            ["tmux", "rename-session", "-t",
                             res.stdout.strip(), f"ah-{agent.id[:8]}"],
                            capture_output=True, text=True, timeout=5,
                        )
                except (_sp.TimeoutExpired, OSError) as e:
                    logger.warning(
                        "tmux rename failed for agent %s: %s", agent.id, e,
                    )

                # Import existing turns as messages
                for role, content, *rest in turns:
                    meta = rest[0] if rest else None
                    meta_json = json.dumps(meta) if meta else None
                    if role == "user":
                        msg = Message(
                            agent_id=agent.id,
                            role=MessageRole.USER,
                            content=content,
                            status=MessageStatus.COMPLETED,
                            source="cli",
                            completed_at=_utcnow(),
                        )
                    elif role == "assistant":
                        msg = Message(
                            agent_id=agent.id,
                            role=MessageRole.AGENT,
                            content=content,
                            status=MessageStatus.COMPLETED,
                            source="cli",
                            meta_json=meta_json,
                            completed_at=_utcnow(),
                        )
                    else:
                        continue
                    db.add(msg)

                db.commit()
                agents_to_sync.append((agent.id, best_sid, proj.path))
                self._emit(emit_agent_update(agent.id, agent.status.value, proj.name))

        # Start sync tasks (after commit)
        for aid, sid, ppath in agents_to_sync:
            self.start_session_sync(aid, sid, ppath)

    def _reap_dead_agents(self, db: Session):
        """Stop agents whose underlying process is dead.

        Checks all non-STOPPED agents:
        - CLI-synced agents (STARTING/SYNCING/IDLE/ERROR): verifies the
          tmux pane still has a running claude process, or falls back to
          session file freshness.
        - Orchestrator agents: checks EXECUTING agents are still tracked.
        """
        import time
        from websocket import emit_agent_update

        stale_threshold = _STALE_SESSION_THRESHOLD

        # Include STARTING so launched tmux agents that never got a
        # session_id are still reaped when their process dies.
        candidates = db.query(Agent).filter(
            Agent.status.in_([
                AgentStatus.STARTING, AgentStatus.SYNCING,
                AgentStatus.IDLE, AgentStatus.ERROR,
                AgentStatus.EXECUTING,
            ]),
        ).all()

        # Build the tmux pane map once (expensive), reuse for all agents (cached per tick)
        pane_map = self._get_tmux_map()

        for agent in candidates:
            # --- Orchestrator-spawned agents (cli_sync=False) ---
            if not agent.cli_sync:
                if agent.status == AgentStatus.EXECUTING:
                    if agent.id in self._active_execs:
                        continue
                    # Not tracked — subprocess vanished; mark STOPPED
                    logger.info(
                        "Orchestrator agent %s EXECUTING but not tracked — stopping",
                        agent.id,
                    )
                    agent.status = AgentStatus.STOPPED
                    self._emit(emit_agent_update(agent.id, "STOPPED", agent.project))
                # IDLE/ERROR/STARTING orchestrator agents are fine
                continue

            # --- CLI-synced agents (cli_sync=True) ---

            # IDLE and EXECUTING cli_sync agents that are being driven by
            # the orchestrator (no tmux pane) should follow orchestrator
            # rules: IDLE is fine (waiting for messages), EXECUTING checks
            # _active_execs.  This prevents killing agents that were
            # originally tmux-launched but are now operating via subprocess
            # (e.g. after resume), where session file staleness is normal.
            if not agent.tmux_pane and agent.status in (
                AgentStatus.IDLE, AgentStatus.EXECUTING,
            ):
                if agent.status == AgentStatus.EXECUTING:
                    if agent.id in self._active_execs:
                        continue
                    logger.info(
                        "CLI agent %s EXECUTING but not tracked — stopping",
                        agent.id,
                    )
                    agent.status = AgentStatus.STOPPED
                    self._emit(emit_agent_update(agent.id, "STOPPED", agent.project))
                if agent.status == AgentStatus.IDLE:
                    # cli_sync agents should not normally be IDLE (they
                    # should be SYNCING or STOPPED).  If we see one here
                    # with no sync task running, it's orphaned — stop it.
                    if agent.id not in self._sync_tasks and agent.id not in self._active_execs:
                        logger.warning(
                            "CLI agent %s is IDLE with no sync task and no pane — stopping",
                            agent.id,
                        )
                        agent.status = AgentStatus.STOPPED
                        sys_msg = Message(
                            agent_id=agent.id,
                            role=MessageRole.SYSTEM,
                            content="Sync lost — agent stopped (no active CLI session)",
                            status=MessageStatus.COMPLETED,
                        )
                        db.add(sys_msg)
                        self._emit(emit_agent_update(agent.id, "STOPPED", agent.project))
                continue

            # Determine if this agent's underlying process is alive.
            # Priority: tmux pane check > session file freshness.
            alive = False

            if agent.tmux_pane:
                info = pane_map.get(agent.tmux_pane)
                if info and not info["is_orchestrator"]:
                    alive = True
                elif not verify_tmux_pane(agent.tmux_pane):
                    # Pane is gone entirely
                    agent.tmux_pane = None
                # else: pane exists but claude isn't running in it → not alive
            elif agent.session_id:
                # No pane — check if session file was recently written
                proj = db.get(Project, agent.project)
                if proj:
                    jsonl_path = _resolve_session_jsonl(
                        agent.session_id, proj.path, agent.worktree,
                    )
                    try:
                        mtime = os.path.getmtime(jsonl_path)
                        age = time.time() - mtime
                        alive = age < stale_threshold
                    except OSError:
                        alive = False

            # For STARTING agents without a session_id, give them a grace
            # period (60s) for the background task to set things up.
            # This covers both orchestrator-spawned and tmux-launched agents
            # where Claude TUI may still be loading.
            if (
                not alive
                and agent.status == AgentStatus.STARTING
                and not agent.session_id
            ):
                created = agent.created_at
                if created and hasattr(created, 'replace'):
                    created = created.replace(tzinfo=timezone.utc)
                age = (_utcnow() - created).total_seconds() if created else 9999
                if age < 60:
                    continue  # Still within grace period

            if alive:
                continue

            # Try to detect tmux pane before giving up (SYNCING agents only)
            if (
                not alive
                and agent.session_id
                and not agent.tmux_pane
                and agent.status == AgentStatus.SYNCING
            ):
                proj = db.get(Project, agent.project)
                if proj:
                    pane = _detect_tmux_pane_for_session(
                        agent.session_id, proj.path
                    )
                    if pane:
                        agent.tmux_pane = pane
                        # Re-check with the newly found pane
                        info = pane_map.get(pane)
                        if info and not info["is_orchestrator"]:
                            logger.info(
                                "Detected tmux pane %s for agent %s",
                                pane, agent.id,
                            )
                            continue

            # Process is dead or session is stale — stop the agent
            logger.info(
                "CLI agent %s (%s) is dead (status=%s, pane=%s, sid=%s) — stopping",
                agent.id, agent.name[:40], agent.status.value,
                agent.tmux_pane, (agent.session_id or "")[:12],
            )
            agent.status = AgentStatus.STOPPED
            agent.tmux_pane = None
            self._cancel_sync_task(agent.id)
            self._cancel_launch_task(agent.id)
            self._stale_session_retries.pop(agent.id, None)
            self._emit(emit_agent_update(agent.id, "STOPPED", agent.project))

    # ---- Streaming output ----

    async def _stream_output_loop(self, agent_id: str, output_file: str):
        """Tail an agent's output file and emit incremental content via WS.

        Runs as an asyncio task for the duration of an exec.  Reads new
        lines from the output file every 0.5s, parses stream-json, and
        broadcasts the accumulated text snapshot so the frontend can
        display it progressively.
        """
        from websocket import emit_agent_stream

        gid = self._start_generating(agent_id)
        file_pos = 0
        last_content = ""
        idle_checks = 0  # counts consecutive ticks with no new output
        PERMISSION_CHECK_TICKS = 60  # check tmux pane after ~30s of no output
        try:
            while True:
                await asyncio.sleep(0.5)

                # Check if the exec is still tracked (may have been harvested)
                if agent_id not in self._active_execs:
                    break

                try:
                    with open(output_file, "r", errors="replace") as f:
                        f.seek(file_pos)
                        new_data = f.read()
                        file_pos = f.tell()
                except FileNotFoundError:
                    continue  # file not created yet — normal during startup

                if not new_data:
                    idle_checks += 1
                    # After sustained idle, check tmux pane for stuck permission prompt
                    if idle_checks == PERMISSION_CHECK_TICKS:
                        info = self._active_execs.get(agent_id)
                        if info:
                            pane_id = info.get("tmux_pane")
                            if pane_id:
                                pane_text = capture_tmux_pane(pane_id)
                                if pane_text and _detect_plan_prompt(pane_text) == "permission":
                                    logger.warning(
                                        "Agent %s: detected stuck permission prompt after %ds idle, killing exec",
                                        agent_id, idle_checks // 2,
                                    )
                                    self.worker_mgr.stop_worker(info["pid_str"])
                    continue

                idle_checks = 0  # reset on new output

                # New output arrived — refresh inactivity timeout
                info = self._active_execs.get(agent_id)
                if info:
                    info["last_activity"] = _utcnow()

                # Re-read entire file to parse from scratch (stream-json
                # events can span multiple reads and we need the full picture)
                with open(output_file, "r", errors="replace") as f:
                    full_logs = f.read()

                parts, _, _, active_tool = _parse_stream_parts(full_logs)
                content = _format_parts(parts)

                if content and content != last_content:
                    last_content = content
                    self._emit(emit_agent_stream(agent_id, content, generation_id=gid, active_tool=active_tool))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "Stream output loop crashed for agent %s (file: %s)",
                agent_id, output_file,
            )
        finally:
            self._stop_generating(agent_id)

    def _start_stream_task(self, agent_id: str, output_file: str):
        """Start a streaming output task for an agent exec."""
        # Cancel any existing stream task
        self._cancel_stream_task(agent_id)
        task = asyncio.ensure_future(
            self._stream_output_loop(agent_id, output_file)
        )
        self._stream_tasks[agent_id] = task
        logger.info("Started stream task for agent %s -> %s", agent_id, output_file)

    def _cancel_stream_task(self, agent_id: str):
        """Cancel and clean up a streaming task."""
        task = self._stream_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()

    # ---- CLI Session Sync ----

    def import_session_history(
        self, agent_id: str, session_id: str, project_path: str
    ) -> int:
        """Import existing session JSONL conversation into Messages table.

        Returns the number of messages imported.
        Also sets the agent's model from the session if detected.
        """
        # Look up agent worktree for correct session path
        db_tmp = SessionLocal()
        try:
            _ag = db_tmp.get(Agent, agent_id)
            worktree = _ag.worktree if _ag else None
        finally:
            db_tmp.close()
        jsonl_path = _resolve_session_jsonl(session_id, project_path, worktree)
        turns = _parse_session_turns(jsonl_path)
        if not turns:
            return 0

        # Detect the actual model used in the CLI session
        session_model = _parse_session_model(jsonl_path)

        db = SessionLocal()
        try:
            imported = 0
            for role, content, *rest in turns:
                meta = rest[0] if rest else None
                meta_json = json.dumps(meta) if meta else None
                if role == "user":
                    msg = Message(
                        agent_id=agent_id,
                        role=MessageRole.USER,
                        content=content,
                        status=MessageStatus.COMPLETED,
                        completed_at=_utcnow(),
                    )
                elif role == "assistant":
                    msg = Message(
                        agent_id=agent_id,
                        role=MessageRole.AGENT,
                        content=content,
                        status=MessageStatus.COMPLETED,
                        meta_json=meta_json,
                        completed_at=_utcnow(),
                    )
                elif role == "system":
                    msg = Message(
                        agent_id=agent_id,
                        role=MessageRole.SYSTEM,
                        content=content,
                        status=MessageStatus.COMPLETED,
                        completed_at=_utcnow(),
                    )
                else:
                    continue
                db.add(msg)
                imported += 1

            if imported:
                agent = db.get(Agent, agent_id)
                if agent:
                    agent.last_message_preview = (turns[-1][1] or "")[:200]
                    agent.last_message_at = _utcnow()
                    if session_model:
                        agent.model = session_model

                db.commit()
            return imported
        finally:
            db.close()

    def start_session_sync(self, agent_id: str, session_id: str, project_path: str):
        """Start a background task to live-sync a CLI session JSONL."""
        self._cancel_sync_task(agent_id)
        task = asyncio.ensure_future(
            self._sync_session_loop(agent_id, session_id, project_path)
        )
        self._sync_tasks[agent_id] = task
        logger.info("Started sync task for agent %s (session %s)", agent_id, session_id)

    def _process_subagents(
        self, agent_id: str, session_id: str, project_path: str,
        worktree: str | None, agent_name: str, project_name: str,
    ):
        """Scan for subagent JSONLs and create/update Agent records."""
        from websocket import emit_agent_update, emit_new_message

        subs = _scan_subagents(session_id, project_path, worktree)
        if not subs:
            return

        known = self._known_subagents.setdefault(agent_id, {})

        db = SessionLocal()
        try:
            for sub in subs:
                cid = sub["claude_agent_id"]
                if cid in known:
                    # Already tracked — check if JSONL grew
                    info = known[cid]
                    if sub["size"] > info["last_size"]:
                        info["last_size"] = sub["size"]
                        info["idle_polls"] = 0
                        # Update messages from JSONL
                        sub_agent_id = info["agent_id"]
                        turns = _parse_session_turns(sub["jsonl_path"])
                        existing_count = db.query(Message).filter(
                            Message.agent_id == sub_agent_id,
                        ).count()
                        if len(turns) > existing_count:
                            # Import new turns
                            for role, content, *rest in turns[existing_count:]:
                                meta = rest[0] if rest else None
                                meta_json = json.dumps(meta) if meta else None
                                if role == "user":
                                    db.add(Message(
                                        agent_id=sub_agent_id,
                                        role=MessageRole.USER,
                                        content=content,
                                        status=MessageStatus.COMPLETED,
                                        source="cli",
                                        completed_at=_utcnow(),
                                    ))
                                elif role == "assistant":
                                    db.add(Message(
                                        agent_id=sub_agent_id,
                                        role=MessageRole.AGENT,
                                        content=content,
                                        status=MessageStatus.COMPLETED,
                                        source="cli",
                                        meta_json=meta_json,
                                        completed_at=_utcnow(),
                                    ))
                            sub_ag = db.get(Agent, sub_agent_id)
                            if sub_ag:
                                last_turn = turns[-1] if turns else None
                                if last_turn:
                                    sub_ag.last_message_preview = (last_turn[1] or "")[:200]
                                    sub_ag.last_message_at = _utcnow()
                                db.commit()
                                self._emit(emit_new_message(
                                    sub_agent_id, "sync",
                                    sub_ag.name, project_name,
                                ))
                    else:
                        info["idle_polls"] = info.get("idle_polls", 0) + 1
                        # If idle for 3+ polls, mark STOPPED
                        if info["idle_polls"] >= 3:
                            sub_ag = db.get(Agent, info["agent_id"])
                            if sub_ag and sub_ag.status == AgentStatus.SYNCING:
                                sub_ag.status = AgentStatus.STOPPED
                                db.commit()
                                self._emit(emit_agent_update(
                                    sub_ag.id, "STOPPED", project_name,
                                ))
                else:
                    # New subagent — create Agent record and import turns
                    name = sub["slug"] or f"subagent-{cid[:8]}"
                    sub_agent = Agent(
                        project=project_name,
                        name=name,
                        mode=AgentMode.AUTO,
                        status=AgentStatus.SYNCING,
                        cli_sync=True,
                        parent_id=agent_id,
                        is_subagent=True,
                        claude_agent_id=cid,
                        model=sub["model"] or None,
                    )
                    db.add(sub_agent)
                    db.flush()  # get the generated id

                    # Import turns from JSONL
                    turns = _parse_session_turns(sub["jsonl_path"])
                    for role, content, *rest in turns:
                        meta = rest[0] if rest else None
                        meta_json = json.dumps(meta) if meta else None
                        if role == "user":
                            db.add(Message(
                                agent_id=sub_agent.id,
                                role=MessageRole.USER,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                source="cli",
                                completed_at=_utcnow(),
                            ))
                        elif role == "assistant":
                            db.add(Message(
                                agent_id=sub_agent.id,
                                role=MessageRole.AGENT,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                source="cli",
                                meta_json=meta_json,
                                completed_at=_utcnow(),
                            ))

                    if turns:
                        last_turn = turns[-1]
                        sub_agent.last_message_preview = (last_turn[1] or "")[:200]
                        sub_agent.last_message_at = _utcnow()

                    db.commit()
                    known[cid] = {
                        "agent_id": sub_agent.id,
                        "last_size": sub["size"],
                        "idle_polls": 0,
                    }
                    logger.info(
                        "Created subagent %s (%s) for parent %s — %d turns",
                        sub_agent.id, name, agent_id, len(turns),
                    )
                    self._emit(emit_agent_update(
                        sub_agent.id, "SYNCING", project_name,
                    ))
        except Exception:
            logger.warning(
                "Error processing subagents for agent %s",
                agent_id, exc_info=True,
            )
        finally:
            db.close()

    def _cancel_sync_task(self, agent_id: str):
        """Cancel and clean up a sync task."""
        task = self._sync_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()

    def track_launch_task(self, agent_id: str, task: asyncio.Task):
        """Track a tmux launch background task so it can be cancelled."""
        self._cancel_launch_task(agent_id)
        self._launch_tasks[agent_id] = task

    def _cancel_launch_task(self, agent_id: str):
        """Cancel and clean up a launch background task."""
        task = self._launch_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()

    def _detect_successor_session(
        self, current_sid: str, project_path: str, agent_id: str,
        worktree: str | None = None,
    ) -> str | None:
        """Check if a newer session JSONL supersedes the current one.

        Returns the new session_id if found, otherwise None.
        Used to detect when Claude auto-continues into a new session
        (e.g. context too long).

        Strategies (in priority order):
          0. Direct fd scan — check if the claude process has an open
             handle to a .jsonl file (most reliable when available).
          1. Slug match — /clear transitions reuse the same slug.
          2. CWD + project match — if the claude process CWD is under
             the agent's project path and the candidate JSONL's CWD
             also matches, accept it.  Replaces the old PID/fd-ownership
             checks which broke in Claude Code v2.1+ (no debug files,
             no per-session fd handles).
          3. Legacy PID match — kept as final fallback.
        """
        current_jsonl = _resolve_session_jsonl(current_sid, project_path, worktree)
        try:
            current_mtime = os.path.getmtime(current_jsonl)
        except OSError:
            return None

        # Get claude process info from the tmux pane
        pane_pid: int | None = None
        claude_cwd: str = ""
        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            if agent and agent.tmux_pane:
                pane_info = _build_tmux_claude_map().get(agent.tmux_pane)
                if pane_info:
                    pane_pid = pane_info["pid"]
                    claude_cwd = pane_info.get("cwd", "")

            # Collect ALL session IDs assigned to any agent to avoid
            # re-adopting a dead or unrelated session.
            active_sids: set[str] = set()
            for a in db.query(Agent).filter(
                Agent.session_id.is_not(None),
            ).all():
                active_sids.add(a.session_id)

            # For CWD-based matching: collect user message signatures
            # so we can verify the candidate's first prompt matches this
            # agent's conversation (prevents cross-agent session adoption).
            agent_user_sigs: set[str] = set()
            for m in db.query(Message).filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
            ).all():
                agent_user_sigs.add(_dedup_sig(m.content))
        finally:
            db.close()

        # Strategy 0: direct fd scan — the most reliable method when
        # Claude Code keeps the JSONL open (not always the case).
        if pane_pid:
            fd_sid = _detect_pid_session_jsonl(pane_pid)
            if fd_sid and fd_sid != current_sid and fd_sid not in active_sids:
                logger.info(
                    "_detect_successor_session: fd-based match "
                    "agent=%s pid=%d candidate_sid=%s",
                    agent_id, pane_pid, fd_sid[:12],
                )
                return fd_sid

        current_slug = _get_session_slug(current_jsonl)

        # Collect session dirs to scan
        session_dirs = [session_source_dir(project_path)]
        if worktree:
            wt_path = os.path.join(project_path, ".claude", "worktrees", worktree)
            wt_sdir = session_source_dir(wt_path)
            if wt_sdir not in session_dirs and os.path.isdir(wt_sdir):
                session_dirs.append(wt_sdir)

        # Separate trackers: slug/PID matches pick newest (strong evidence),
        # CWD matches pick earliest (weakest evidence, avoid false positives
        # from unrelated sessions on the same project).
        best_sid, best_mtime = None, current_mtime       # slug/PID: newest wins
        cwd_sid, cwd_mtime = None, float("inf")           # CWD: earliest wins
        for session_dir in session_dirs:
            try:
                for fname in os.listdir(session_dir):
                    if not fname.endswith(".jsonl"):
                        continue
                    sid = fname.replace(".jsonl", "")
                    if sid == current_sid or sid in active_sids:
                        continue
                    fpath = os.path.join(session_dir, fname)
                    mtime = os.path.getmtime(fpath)
                    if mtime <= current_mtime:
                        continue

                    # Strategy 1: slug match (/clear transition) — newest wins
                    if current_slug:
                        candidate_slug = _get_session_slug(fpath)
                        if candidate_slug == current_slug:
                            if mtime > best_mtime:
                                logger.info(
                                    "_detect_successor_session: slug match "
                                    "agent=%s slug=%s candidate_sid=%s",
                                    agent_id, current_slug, sid[:12],
                                )
                                best_sid, best_mtime = sid, mtime
                            continue

                    # Strategy 2: CWD + content match — earliest wins
                    # Requires the candidate's first user message matches a
                    # message in this agent's history (prevents cross-agent
                    # session adoption when multiple agents share a project).
                    if claude_cwd and claude_cwd.startswith(project_path):
                        candidate_cwd = _get_session_cwd(fpath)
                        if candidate_cwd and candidate_cwd.startswith(project_path):
                            first_prompt = _get_first_user_content(fpath)
                            if (
                                first_prompt
                                and agent_user_sigs
                                and _dedup_sig(first_prompt) in agent_user_sigs
                                and mtime < cwd_mtime
                            ):
                                cwd_sid, cwd_mtime = sid, mtime
                            continue

                    # Strategy 3: legacy PID-based match (fallback) — newest wins
                    session_pid = _get_session_pid(sid)
                    if pane_pid is not None:
                        if session_pid == pane_pid:
                            if mtime > best_mtime:
                                best_sid, best_mtime = sid, mtime
                            continue
                        if session_pid is None and _pid_owns_session(pane_pid, sid):
                            if mtime > best_mtime:
                                logger.info(
                                    "_detect_successor_session: fd-ownership fallback "
                                    "agent=%s pane_pid=%d candidate_sid=%s",
                                    agent_id, pane_pid, sid[:12],
                                )
                                best_sid, best_mtime = sid, mtime
                    elif session_pid is not None:
                        if mtime > best_mtime:
                            best_sid, best_mtime = sid, mtime
            except OSError as e:
                logger.warning(
                    "_detect_successor_session: failed to scan session dir %s for agent %s: %s",
                    session_dir, agent_id, e,
                )
        # Prefer strong-evidence matches (slug/PID) over weaker CWD match
        result = best_sid or cwd_sid
        if result and result == cwd_sid and not best_sid:
            logger.info(
                "_detect_successor_session: CWD match "
                "agent=%s candidate_sid=%s (earliest newer session)",
                agent_id, cwd_sid[:12],
            )
        return result

    def _spawn_successor_agent(
        self, old_agent_id: str, new_sid: str, project_path: str,
        worktree: str | None = None,
    ):
        """Stop the old agent and create a new SYNCING agent for the continued session."""
        from websocket import emit_agent_update

        db = SessionLocal()
        try:
            old_agent = db.get(Agent, old_agent_id)
            if not old_agent:
                return

            project_name = old_agent.project
            tmux_pane = old_agent.tmux_pane
            model = old_agent.model
            wt = worktree or old_agent.worktree

            # Stop old agent
            self._cancel_sync_task(old_agent_id)
            old_agent.status = AgentStatus.STOPPED
            old_agent.tmux_pane = None
            # Keep session_id so the UI can still show session size.
            # Resume is blocked by the successor check in the API.
            db.flush()
            self._emit(emit_agent_update(old_agent_id, "STOPPED", project_name))

            # Parse new session for name and turns
            new_fpath = _resolve_session_jsonl(new_sid, project_path, wt)
            agent_name = "CLI session (continued)"
            turns = _parse_session_turns(new_fpath)
            detected_model = _detect_session_model(new_fpath) or model
            for role, content, *_rest in turns:
                if role == "user" and content:
                    agent_name = (content or "")[:80]
                    break

            # Create new agent linked to the old one.  By the time we
            # reach here, the caller has already confirmed this is a
            # genuine continuation (tmux session name matches or session
            # has a 'result' event + PID match), not an unrelated
            # conversation.
            new_agent = Agent(
                project=project_name,
                name=agent_name,
                mode=AgentMode.AUTO,
                status=AgentStatus.SYNCING,
                model=detected_model,
                session_id=new_sid,
                cli_sync=True,
                tmux_pane=tmux_pane,
                parent_id=old_agent_id,
                worktree=wt,
                muted=old_agent.muted,
                last_message_preview=agent_name,
                last_message_at=_utcnow(),
            )
            db.add(new_agent)
            db.flush()

            # Import existing turns
            for role, content, *rest in turns:
                meta = rest[0] if rest else None
                meta_json = json.dumps(meta) if meta else None
                if role == "user":
                    msg = Message(
                        agent_id=new_agent.id,
                        role=MessageRole.USER,
                        content=content,
                        status=MessageStatus.COMPLETED,
                        source="cli",
                        completed_at=_utcnow(),
                    )
                elif role == "assistant":
                    msg = Message(
                        agent_id=new_agent.id,
                        role=MessageRole.AGENT,
                        content=content,
                        status=MessageStatus.COMPLETED,
                        source="cli",
                        meta_json=meta_json,
                        completed_at=_utcnow(),
                    )
                else:
                    continue
                db.add(msg)

            db.commit()
            self._emit(emit_agent_update(new_agent.id, "SYNCING", project_name))
            self.start_session_sync(new_agent.id, new_sid, project_path)

            # Rename the tmux session to match the new agent ID
            if tmux_pane:
                import subprocess as _sp
                try:
                    # Get session name from pane ID
                    res = _sp.run(
                        ["tmux", "display-message", "-t", tmux_pane,
                         "-p", "#{session_name}"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        rename_res = _sp.run(
                            ["tmux", "rename-session", "-t",
                             res.stdout.strip(), f"ah-{new_agent.id[:8]}"],
                            capture_output=True, text=True, timeout=5,
                        )
                        if rename_res.returncode != 0:
                            logger.warning(
                                "Failed to rename tmux session for successor %s: %s",
                                new_agent.id, rename_res.stderr.strip(),
                            )
                except (_sp.TimeoutExpired, OSError) as e:
                    logger.warning(
                        "tmux rename failed for successor %s: %s",
                        new_agent.id, e,
                    )

            logger.info(
                "Spawned successor agent %s for session %s (old: %s)",
                new_agent.id, new_sid[:12], old_agent_id,
            )
        finally:
            db.close()

    def _rotate_agent_session(
        self, agent_id: str, new_sid: str, project_path: str,
        worktree: str | None = None,
    ):
        """Rotate an agent to a new CLI session in-place.

        Unlike _spawn_successor_agent, this keeps the same agent ID and
        conversation history.  The sync loop restarts and reconciles
        turns from the new JSONL against existing DB messages — the
        dedup logic handles carried-forward history automatically.
        """
        from websocket import emit_agent_update

        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            if not agent:
                return
            old_sid = agent.session_id
            agent.session_id = new_sid
            new_fpath = _resolve_session_jsonl(
                new_sid, project_path, worktree or agent.worktree,
            )
            detected_model = _detect_session_model(new_fpath)
            if detected_model:
                agent.model = detected_model
            db.add(Message(
                agent_id=agent_id,
                role=MessageRole.SYSTEM,
                content="CLI session continued (new context)",
                status=MessageStatus.COMPLETED,
            ))
            agent.last_message_at = _utcnow()
            db.commit()
            self._emit(emit_agent_update(agent_id, "SYNCING", agent.project))
            logger.info(
                "Rotated agent %s session in-place: %s → %s",
                agent_id, (old_sid or "")[:12], new_sid[:12],
            )
        finally:
            db.close()

        # Cancel old sync task and start a fresh one.  The new sync
        # loop does initial reconciliation which deduplicates turns
        # already present in the DB.
        self._cancel_sync_task(agent_id)
        self.start_session_sync(agent_id, new_sid, project_path)

    async def _sync_session_loop(
        self, agent_id: str, session_id: str, project_path: str
    ):
        """Tail a CLI session JSONL and import new turns as they appear.

        Stays in SYNCING until the session JSONL contains a 'result' event
        (written by Claude Code when the session ends) or a new session file
        supersedes this one. Only then transitions to IDLE.
        """
        try:
            await self._sync_session_loop_inner(agent_id, session_id, project_path)
        except asyncio.CancelledError:
            logger.info("Sync loop cancelled for agent %s", agent_id)
        except Exception:
            logger.exception("Sync loop crashed for agent %s", agent_id)
            # Transition agent out of phantom SYNCING state so the UI
            # reflects reality instead of showing a stuck spinner.
            from websocket import emit_agent_update
            db = SessionLocal()
            try:
                agent = db.get(Agent, agent_id)
                if agent and agent.status == AgentStatus.SYNCING:
                    agent.status = AgentStatus.ERROR
                    db.add(Message(
                        agent_id=agent_id,
                        role=MessageRole.SYSTEM,
                        content="Sync loop crashed — check server logs for details",
                        status=MessageStatus.COMPLETED,
                    ))
                    db.commit()
                    self._emit(emit_agent_update(agent.id, "ERROR", agent.project))
                    logger.warning(
                        "Agent %s moved to ERROR after sync loop crash", agent_id
                    )
            except Exception:
                logger.exception("Failed to mark agent %s as ERROR after sync crash", agent_id)
            finally:
                db.close()
        finally:
            # Only clean up if this is still the active sync task.
            # _rotate_agent_session replaces the task, so the old one
            # must not remove the new entry from _sync_tasks.
            import asyncio as _aio
            if self._sync_tasks.get(agent_id) is _aio.current_task():
                self._sync_tasks.pop(agent_id, None)
            # Ensure generating state is cleaned up on any exit path
            if agent_id in self._generating_agents:
                self._stop_generating(agent_id)
            # Stop any tracked subagents when parent sync exits
            known_subs = self._known_subagents.pop(agent_id, {})
            if known_subs:
                db_sub = SessionLocal()
                try:
                    for cid, info in known_subs.items():
                        sub_ag = db_sub.get(Agent, info["agent_id"])
                        if sub_ag and sub_ag.status == AgentStatus.SYNCING:
                            sub_ag.status = AgentStatus.STOPPED
                    db_sub.commit()
                except Exception:
                    logger.warning("Error stopping subagents for %s", agent_id, exc_info=True)
                finally:
                    db_sub.close()

    async def _sync_session_loop_inner(
        self, agent_id: str, session_id: str, project_path: str
    ):
        """Inner sync loop — see _sync_session_loop for docs."""
        POLL_INTERVAL = 3  # seconds between checks

        from websocket import emit_agent_stream, emit_agent_update, emit_new_message

        # Cache agent name/project for notification payloads
        _sync_agent_name = ""
        _sync_project = ""
        _worktree = None
        db = SessionLocal()
        try:
            _ag = db.get(Agent, agent_id)
            if _ag:
                _sync_agent_name = _ag.name
                _sync_project = _ag.project
                _worktree = _ag.worktree
        finally:
            db.close()

        jsonl_path = _resolve_session_jsonl(session_id, project_path, _worktree)
        if _worktree and ".claude/worktrees" in jsonl_path:
            logger.info(
                "Agent %s using worktree session path: %s",
                agent_id, jsonl_path,
            )

        last_size = 0
        last_turn_count = 0
        last_tail_hash = ""  # Hash of last turn content to detect updates
        last_streamed_hash = ""  # Hash of last agent_stream content (avoid re-emit)
        is_generating = False
        _sync_gen_id: int | None = None  # current generation_id for sync streaming
        pending_push_body = ""  # Deferred notification until response stabilizes
        pending_push_idle = 0   # Consecutive idle polls since pending was set

        def _content_hash(content: str) -> str:
            """Fast hash of content for change detection."""
            import hashlib
            return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:16]

        # Get the current file size and turn count so we only import new turns
        try:
            with open(jsonl_path, "r", errors="replace") as f:
                last_size = f.seek(0, 2)  # seek to end
        except OSError as e:
            logger.warning(
                "Sync loop for agent %s: cannot read session JSONL %s: %s",
                agent_id, jsonl_path, e,
            )

        initial_turns = _parse_session_turns(jsonl_path)
        last_turn_count = len(initial_turns)
        if initial_turns:
            _init_tail = initial_turns[-1]
            _init_meta_sig = str(_init_tail[2]) if len(_init_tail) > 2 and _init_tail[2] else ""
            last_tail_hash = f"{_content_hash(_init_tail[1])}:{_init_meta_sig}"

        # Reconcile: full-scan comparison between JSONL turns and DB
        # messages.  Queue-operation user messages can appear anywhere
        # in the conversation (interspersed between assistant turns), so
        # we check every turn against the DB and insert any that are
        # missing, regardless of position.
        db = SessionLocal()
        try:
            conv_turns = [
                (r, c, m) for r, c, m in initial_turns
                if r in ("user", "assistant")
            ]

            if conv_turns:
                agent = db.get(Agent, agent_id)

                # Get ALL user/agent DB messages for signature matching
                all_db = db.query(Message).filter(
                    Message.agent_id == agent_id,
                    Message.role.in_([MessageRole.USER, MessageRole.AGENT]),
                ).all()
                # Build a multiset of (role_char, content_prefix) — use
                # a dict with counts so duplicate content is handled
                db_sig_counts: dict[tuple[str, str], int] = {}
                for m in all_db:
                    role_char = "u" if m.role == MessageRole.USER else "a"
                    sig = (role_char, _dedup_sig(m.content))
                    db_sig_counts[sig] = db_sig_counts.get(sig, 0) + 1

                # Walk through JSONL turns and collect missing ones
                missing: list[tuple[str, str, dict | None]] = []
                for r, c, mt in conv_turns:
                    role_char = "u" if r == "user" else "a"
                    content_sig = _dedup_sig(c)
                    sig = (role_char, content_sig)
                    if db_sig_counts.get(sig, 0) > 0:
                        db_sig_counts[sig] -= 1  # consume the match
                    else:
                        # Role may have changed (e.g. task-notification fixed
                        # from USER→AGENT); check the opposite role before
                        # declaring the turn missing.
                        alt = ("a" if role_char == "u" else "u", content_sig)
                        if db_sig_counts.get(alt, 0) > 0:
                            db_sig_counts[alt] -= 1
                        else:
                            missing.append((r, c, mt))

                if missing and agent:
                    for role, content, meta in missing:
                        meta_json = json.dumps(meta) if meta else None
                        if role == "user":
                            db.add(Message(
                                agent_id=agent_id,
                                role=MessageRole.USER,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                source="cli",
                                completed_at=_utcnow(),
                            ))
                        elif role == "assistant":
                            db.add(Message(
                                agent_id=agent_id,
                                role=MessageRole.AGENT,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                source="cli",
                                meta_json=meta_json,
                                completed_at=_utcnow(),
                            ))
                    agent.last_message_preview = (conv_turns[-1][1] or "")[:200]
                    agent.last_message_at = _utcnow()
                    db.commit()
                    self._emit(emit_new_message(
                        agent_id, "sync", _sync_agent_name, _sync_project,
                    ))
                    logger.info(
                        "Reconciled %d missing turns for agent %s",
                        len(missing), agent_id,
                    )
                elif agent:
                    # No missing turns — but update last agent msg if it grew
                    last_agent_msg = db.query(Message).filter(
                        Message.agent_id == agent_id,
                        Message.role == MessageRole.AGENT,
                    ).order_by(Message.created_at.desc()).first()
                    last_assistant = None
                    last_assistant_meta = None
                    for role, content, meta in reversed(conv_turns):
                        if role == "assistant":
                            last_assistant = content
                            last_assistant_meta = meta
                            break
                    if (
                        last_agent_msg and last_assistant
                        and len(last_agent_msg.content) < len(last_assistant)
                        and last_assistant.startswith(
                            last_agent_msg.content[:200]
                        )
                    ):
                        last_agent_msg.content = last_assistant
                        last_agent_msg.completed_at = _utcnow()
                        if last_assistant_meta is not None:
                            last_agent_msg.meta_json = _merge_interactive_meta(
                                last_agent_msg.meta_json, last_assistant_meta,
                            )
                        db.commit()
                        self._emit(emit_new_message(
                            agent_id, "sync", _sync_agent_name, _sync_project,
                        ))

                # Update stale interactive metadata (answers that arrived
                # after the assistant message was initially stored)
                if _update_stale_interactive_metadata(db, agent_id, initial_turns):
                    self._emit(emit_new_message(
                        agent_id, "sync", _sync_agent_name, _sync_project,
                    ))
                    logger.debug(
                        "Updated stale interactive metadata for agent %s "
                        "(initial reconciliation)", agent_id,
                    )
        finally:
            db.close()

        idle_polls = 0
        _getsize_error_count = 0
        _GETSIZE_ERROR_LIMIT = 20  # ~60s at 3s poll interval
        while True:
            await asyncio.sleep(POLL_INTERVAL)

            try:
                current_size = os.path.getsize(jsonl_path)
                _getsize_error_count = 0  # reset on success
            except OSError as e:
                _getsize_error_count += 1
                if _getsize_error_count == 1:
                    logger.warning(
                        "Sync loop: getsize failed for %s (agent %s): %s",
                        jsonl_path, agent_id, e,
                    )
                if _getsize_error_count >= _GETSIZE_ERROR_LIMIT:
                    logger.warning(
                        "Sync loop: session file missing for %d polls, "
                        "stopping agent %s",
                        _getsize_error_count, agent_id,
                    )
                    db = SessionLocal()
                    try:
                        agent = db.get(Agent, agent_id)
                        if agent and agent.status == AgentStatus.SYNCING:
                            agent.status = AgentStatus.STOPPED
                            agent.tmux_pane = None
                            db.add(Message(
                                agent_id=agent_id,
                                role=MessageRole.SYSTEM,
                                content="Session file not found — sync stopped",
                                status=MessageStatus.COMPLETED,
                            ))
                            db.commit()
                            self._emit(emit_agent_update(agent_id, "STOPPED", agent.project))
                    finally:
                        db.close()
                    break
                continue

            # Detect JSONL rewrite (e.g. /compact shrinks the file)
            if current_size < last_size:
                logger.info(
                    "Session file shrank for agent %s (%d → %d bytes, "
                    "likely /compact), resetting sync state",
                    agent_id, last_size, current_size,
                )
                turns = _parse_session_turns(jsonl_path)
                last_turn_count = len(turns)
                _t = turns[-1] if turns else ("", "", None)
                _meta_sig = str(_t[2]) if len(_t) > 2 and _t[2] else ""
                last_tail_hash = f"{_content_hash(_t[1])}:{_meta_sig}" if turns else ""
                last_size = current_size
                idle_polls = 0
                # Notify UI about the compact
                db_compact = SessionLocal()
                try:
                    compact_msg = Message(
                        agent_id=agent_id,
                        role=MessageRole.SYSTEM,
                        content="Context compacted — conversation history refreshed",
                        status=MessageStatus.COMPLETED,
                    )
                    db_compact.add(compact_msg)
                    db_compact.commit()
                    self._emit(emit_new_message(
                        agent_id, compact_msg.id, _sync_agent_name, _sync_project,
                    ))
                finally:
                    db_compact.close()
                continue

            if current_size <= last_size:
                idle_polls += 1
                # Heartbeat log every 30 idle polls (~90s) to confirm loop is alive
                if idle_polls % 30 == 0 and idle_polls > 0:
                    logger.info(
                        "Sync loop heartbeat for agent %s: idle_polls=%d, session=%s",
                        agent_id, idle_polls, session_id[:12],
                    )
                # File stopped growing — clear generating state
                if is_generating:
                    is_generating = False
                    self._stop_generating(agent_id)
                    _sync_gen_id = None
                # Flush deferred push notification after response stabilized
                # (require 3+ consecutive idle polls = ~9s of no file growth
                # to avoid firing during brief pauses in JSONL writes)
                if pending_push_body:
                    pending_push_idle += 1
                    if pending_push_idle >= 3:
                        logger.info(
                            "PUSH_DEBUG: flushing deferred notification for %s "
                            "after %d idle polls: %s",
                            agent_id, pending_push_idle, pending_push_body[:50],
                        )
                        db_push = SessionLocal()
                        try:
                            ag_push = db_push.get(Agent, agent_id)
                            if ag_push and not ag_push.muted:
                                self._refresh_pane_attached()
                                if not self._is_agent_in_use(agent_id, ag_push.tmux_pane):
                                    from push import send_push_notification, is_notification_enabled
                                    if is_notification_enabled("agents"):
                                        send_push_notification(
                                            title=_sync_agent_name or f"Agent {agent_id[:8]}",
                                            body=pending_push_body,
                                            url=f"/agents/{agent_id}",
                                        )
                        finally:
                            db_push.close()
                        pending_push_body = ""
                        pending_push_idle = 0
                # Periodically check if we should still be syncing
                if idle_polls % 10 == 0:
                    db = SessionLocal()
                    try:
                        agent = db.get(Agent, agent_id)
                        if not agent or agent.status != AgentStatus.SYNCING:
                            logger.info("Sync loop exiting for agent %s (status changed)", agent_id)
                            break
                        # Try to (re-)detect tmux pane if missing
                        if not agent.tmux_pane:
                            pane = _detect_tmux_pane_for_session(
                                session_id, project_path
                            )
                            if pane:
                                agent.tmux_pane = pane
                                db.commit()
                                self._emit(emit_agent_update(
                                    agent_id, "SYNCING", agent.project,
                                ))
                                logger.info(
                                    "Re-detected tmux pane %s for agent %s",
                                    pane, agent_id,
                                )
                    finally:
                        db.close()

                # After a few idle polls, check if Claude continued into
                # a new session (context too long → auto-continuation).
                # Only do this if the tmux process is still alive — if it's
                # dead, there's no Claude that could have continued.
                if idle_polls >= 3 and idle_polls % 3 == 0:
                    # Verify tmux pane is still alive before checking
                    pane_alive = False
                    db_check = SessionLocal()
                    try:
                        ag = db_check.get(Agent, agent_id)
                        if ag and ag.tmux_pane:
                            pm = _build_tmux_claude_map()
                            info = pm.get(ag.tmux_pane)
                            pane_alive = bool(info and not info["is_orchestrator"])
                    finally:
                        db_check.close()

                    if not pane_alive:
                        # tmux is dead — if we've been idle long enough
                        # with no pane, the CLI session is truly gone.
                        # Use 60 idle polls (~3 min) to give time for
                        # pane re-detection before giving up.
                        if idle_polls >= 60:
                            logger.info(
                                "Sync loop stopping for agent %s — "
                                "tmux pane dead for %d idle polls",
                                agent_id, idle_polls,
                            )
                            db_stop = SessionLocal()
                            try:
                                ag_stop = db_stop.get(Agent, agent_id)
                                if ag_stop and ag_stop.status == AgentStatus.SYNCING:
                                    ag_stop.status = AgentStatus.STOPPED
                                    ag_stop.tmux_pane = None
                                    sys_msg = Message(
                                        agent_id=agent_id,
                                        role=MessageRole.SYSTEM,
                                        content="CLI session ended — sync stopped (tmux pane gone)",
                                        status=MessageStatus.COMPLETED,
                                    )
                                    db_stop.add(sys_msg)
                                    ag_stop.last_message_at = _utcnow()
                                    db_stop.commit()
                                    self._emit(emit_agent_update(
                                        agent_id, "STOPPED", ag_stop.project,
                                    ))
                            finally:
                                db_stop.close()
                            return
                        continue  # tmux is dead, skip continuation check

                    # Look for a successor session.  The old JSONL may or
                    # may not have a 'result' event — Claude Code sometimes
                    # continues into a new session file (via --resume getting
                    # a new ID) without writing 'result' to the old one.
                    # The PID-match requirement in _detect_successor_session
                    # already prevents sub-agent sessions from being
                    # misidentified as continuations.
                    new_sid = self._detect_successor_session(
                        session_id, project_path, agent_id,
                        worktree=_worktree,
                    )
                    if new_sid:
                        logger.info(
                            "Session rotation detected for agent %s: "
                            "%s → %s — rotating in-place",
                            agent_id, session_id[:12], new_sid[:12],
                        )
                        self._rotate_agent_session(
                            agent_id, new_sid, project_path,
                            worktree=_worktree,
                        )
                        return  # new sync task started by _rotate_agent_session

                    if idle_polls % 30 == 0:
                        logger.debug(
                            "Successor check: no match for agent %s (idle_polls=%d, pane_alive=%s)",
                            agent_id, idle_polls, pane_alive,
                        )

                    # Pane is alive — agent stays syncing even if
                    # the session file hasn't grown.  The user may
                    # simply be idle in the tmux session.
                continue
            idle_polls = 0
            pending_push_idle = 0  # File grew — response may still be generating
            last_size = current_size

            # Parse full file for turns
            turns = _parse_session_turns(jsonl_path)

            # Detect turn count decrease (compact may produce a larger
            # file but with fewer turns if the summary is long)
            if len(turns) < last_turn_count:
                logger.info(
                    "Turn count decreased for agent %s (%d → %d, "
                    "likely /compact), resetting sync state",
                    agent_id, last_turn_count, len(turns),
                )
                last_turn_count = len(turns)
                _t = turns[-1] if turns else ("", "", None)
                _meta_sig = str(_t[2]) if len(_t) > 2 and _t[2] else ""
                last_tail_hash = f"{_content_hash(_t[1])}:{_meta_sig}" if turns else ""
                last_streamed_hash = ""  # Reset for fresh post-compact streaming
                # Notify UI about the compact
                db_compact = SessionLocal()
                try:
                    compact_msg = Message(
                        agent_id=agent_id,
                        role=MessageRole.SYSTEM,
                        content="Context compacted — conversation history refreshed",
                        status=MessageStatus.COMPLETED,
                    )
                    db_compact.add(compact_msg)
                    db_compact.commit()
                    self._emit(emit_new_message(
                        agent_id, compact_msg.id, _sync_agent_name, _sync_project,
                    ))
                finally:
                    db_compact.close()
                continue

            new_turns = turns[last_turn_count:]

            # Check if the last existing turn's content grew (same turn count
            # but the assistant accumulated more tool calls / text blocks)
            # Include metadata in the hash so answer updates are detected
            _tail_turn = turns[-1] if turns else ("", "", None)
            _tail_meta_sig = str(_tail_turn[2]) if len(_tail_turn) > 2 and _tail_turn[2] else ""
            tail_hash = f"{_content_hash(_tail_turn[1])}:{_tail_meta_sig}" if turns else ""
            last_turn_updated = (
                not new_turns
                and len(turns) == last_turn_count
                and tail_hash != last_tail_hash
                and turns
                and turns[-1][0] == "assistant"
            )

            if not new_turns and not last_turn_updated:
                # File grew but turns didn't change — Claude is mid-generation.
                # Stream the current assistant content so the frontend can
                # show a live preview instead of just typing dots.
                partial = ""
                if turns and turns[-1][0] == "assistant":
                    partial = turns[-1][1]
                # Only emit when content actually changed — otherwise the
                # frontend shows a streaming bubble duplicating the already-
                # committed message (the 1.5s WS lock expires before the
                # next 3s poll).
                p_hash = _content_hash(partial) if partial else ""
                if p_hash != last_streamed_hash:
                    last_streamed_hash = p_hash
                    if not is_generating:
                        is_generating = True
                        _sync_gen_id = self._start_generating(agent_id)
                    _sync_active_tool = _extract_last_tool_from_content(partial) if partial else None
                    self._emit(emit_agent_stream(agent_id, partial, generation_id=_sync_gen_id, active_tool=_sync_active_tool))
                continue

            db = SessionLocal()
            try:
                agent = db.get(Agent, agent_id)
                if not agent or agent.status != AgentStatus.SYNCING:
                    logger.info(
                        "Sync loop exiting for agent %s (status changed to %s during turn import)",
                        agent_id, agent.status if agent else "DELETED",
                    )
                    break

                if last_turn_updated:
                    # Update the last agent message in-place
                    last_msg = db.query(Message).filter(
                        Message.agent_id == agent_id,
                        Message.role == MessageRole.AGENT,
                    ).order_by(Message.created_at.desc()).first()
                    if last_msg:
                        _last_role, _last_content, *_last_rest = turns[-1]
                        _last_meta = _last_rest[0] if _last_rest else None
                        last_msg.content = _last_content
                        last_msg.completed_at = _utcnow()
                        if _last_meta is not None:
                            last_msg.meta_json = _merge_interactive_meta(
                                last_msg.meta_json, _last_meta,
                            )
                        agent.last_message_preview = (_last_content or "")[:200]
                        agent.last_message_at = _utcnow()
                        db.commit()
                        # Stream the updated content to connected clients
                        _sync_active_tool = _extract_last_tool_from_content(_last_content) if _last_content else None
                        self._emit(emit_agent_stream(
                            agent_id, _last_content, generation_id=_sync_gen_id,
                            active_tool=_sync_active_tool,
                        ))
                        self._emit(emit_new_message(agent.id, "sync", _sync_agent_name, _sync_project))
                        last_tail_hash = tail_hash
                        last_streamed_hash = _content_hash(_last_content) if _last_content else ""
                        # Turn is still growing — mark as generating so
                        # the frontend knows the agent is active.
                        if not is_generating:
                            is_generating = True
                            _sync_gen_id = self._start_generating(agent_id)
                        # Update deferred push body with latest content
                        if pending_push_body:
                            pending_push_body = (_last_content or "")[:120]
                            pending_push_idle = 0  # Reset — still generating
                            logger.info(
                                "PUSH_DEBUG: pending updated (turn grew) for %s: %s",
                                agent_id, pending_push_body[:50],
                            )
                        logger.info(
                            "Updated last turn content for agent %s (%s chars)",
                            agent_id, len(_last_content),
                        )
                else:
                    # Before importing new turns, check if the turn just
                    # before the new ones grew (assistant was mid-response
                    # last time, now finished and user sent a new message).
                    if last_turn_count > 0 and new_turns:
                        prev_role, prev_content, *prev_rest = turns[last_turn_count - 1]
                        prev_meta = prev_rest[0] if prev_rest else None
                        if prev_role == "assistant":
                            last_agent_msg = db.query(Message).filter(
                                Message.agent_id == agent_id,
                                Message.role == MessageRole.AGENT,
                            ).order_by(Message.created_at.desc()).first()
                            if (
                                last_agent_msg
                                and len(last_agent_msg.content) < len(prev_content)
                            ):
                                old_len = len(last_agent_msg.content)
                                last_agent_msg.content = prev_content
                                last_agent_msg.completed_at = _utcnow()
                                if prev_meta is not None:
                                    last_agent_msg.meta_json = _merge_interactive_meta(
                                        last_agent_msg.meta_json, prev_meta,
                                    )
                                logger.info(
                                    "Updated previous turn content for agent %s "
                                    "(%d -> %d chars)",
                                    agent_id, old_len, len(prev_content),
                                )

                    # Import new turns
                    for role, content, *rest in new_turns:
                        meta = rest[0] if rest else None
                        meta_json = json.dumps(meta) if meta else None
                        if role == "user":
                            # Dedup: skip if a matching web-sent (or sourceless)
                            # message already exists.  Use whitespace-normalised
                            # comparison because tmux converts tabs to spaces.
                            from sqlalchemy import or_
                            _norm = _dedup_sig(content)
                            _candidates = db.query(Message).filter(
                                Message.agent_id == agent_id,
                                Message.role == MessageRole.USER,
                                or_(Message.source == "web", Message.source.is_(None)),
                            ).all()
                            existing_web = any(
                                _dedup_sig(m.content) == _norm
                                for m in _candidates
                            )
                            if existing_web:
                                logger.debug(
                                    "Skipping duplicate user turn for agent %s "
                                    "(already sent via web)", agent_id,
                                )
                                continue
                            msg = Message(
                                agent_id=agent_id,
                                role=MessageRole.USER,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                source="cli",
                                completed_at=_utcnow(),
                            )
                        elif role == "assistant":
                            msg = Message(
                                agent_id=agent_id,
                                role=MessageRole.AGENT,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                source="cli",
                                meta_json=meta_json,
                                completed_at=_utcnow(),
                            )
                        elif role == "system":
                            msg = Message(
                                agent_id=agent_id,
                                role=MessageRole.SYSTEM,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                source="cli",
                                completed_at=_utcnow(),
                            )
                        else:
                            continue
                        db.add(msg)

                    agent.last_message_preview = (new_turns[-1][1] or "")[:200]
                    agent.last_message_at = _utcnow()
                    if not self._is_agent_in_use(agent.id, agent.tmux_pane):
                        agent.unread_count += len(new_turns)
                    db.commit()

                    last_turn_count = len(turns)
                    last_tail_hash = tail_hash
                    # Record committed content so mid-generation streaming
                    # won't re-emit the same content as a duplicate bubble.
                    _last_assistant = ""
                    for _r, _c, *_ in reversed(new_turns):
                        if _r == "assistant":
                            _last_assistant = _c
                            break
                    last_streamed_hash = _content_hash(_last_assistant) if _last_assistant else ""
                    if is_generating:
                        is_generating = False
                        self._stop_generating(agent_id)
                        _sync_gen_id = None
                    self._emit(emit_agent_update(
                        agent.id, agent.status.value, agent.project
                    ))
                    self._emit(emit_new_message(agent.id, "sync", _sync_agent_name, _sync_project))

                    # Defer push notification until response stabilizes
                    # (file stops growing), so we notify with final content
                    # instead of partial mid-generation text.
                    for _r, _c, *_rest in reversed(new_turns):
                        if _r == "assistant":
                            pending_push_body = _c[:120]
                            pending_push_idle = 0
                            logger.info(
                                "PUSH_DEBUG: pending set (new assistant turn) for %s: %s",
                                agent_id, pending_push_body[:50],
                            )
                            break
                        if _r == "system":
                            pending_push_body = _c[:120]
                            pending_push_idle = 0
                            logger.info(
                                "PUSH_DEBUG: pending set (new system turn) for %s: %s",
                                agent_id, pending_push_body[:50],
                            )
                            break

                    logger.info(
                        "Synced %d new turns for agent %s",
                        len(new_turns), agent_id,
                    )

                    # Generate video thumbnails for new assistant turns
                    for _r, _c, *_ in new_turns:
                        if _r == "assistant" and _c:
                            asyncio.ensure_future(asyncio.to_thread(
                                generate_thumbnails_for_message, _c, project_path,
                            ))

                # Update stale interactive metadata on EARLIER messages
                # (e.g. user answered an AskUserQuestion in terminal)
                if _update_stale_interactive_metadata(db, agent_id, turns):
                    self._emit(emit_new_message(
                        agent_id, "sync", _sync_agent_name, _sync_project,
                    ))
                    logger.debug(
                        "Updated stale interactive metadata for agent %s",
                        agent_id,
                    )
            finally:
                db.close()

            # Scan for subagent JSONLs spawned by this session
            self._process_subagents(
                agent_id, session_id, project_path,
                _worktree, _sync_agent_name, _sync_project,
            )

            # Check if the CLI session has ended by looking for a 'result' event
            if self._session_has_ended(jsonl_path):
                # Sync any final turns first
                db = SessionLocal()
                try:
                    turns = _parse_session_turns(jsonl_path)
                    final_new = turns[last_turn_count:]
                    agent = db.get(Agent, agent_id)
                    if agent and final_new:
                        for role, content, *rest in final_new:
                            meta = rest[0] if rest else None
                            meta_json = json.dumps(meta) if meta else None
                            if role == "user":
                                msg = Message(
                                    agent_id=agent_id,
                                    role=MessageRole.USER,
                                    content=content,
                                    status=MessageStatus.COMPLETED,
                                    completed_at=_utcnow(),
                                )
                            elif role == "assistant":
                                msg = Message(
                                    agent_id=agent_id,
                                    role=MessageRole.AGENT,
                                    content=content,
                                    status=MessageStatus.COMPLETED,
                                    meta_json=meta_json,
                                    completed_at=_utcnow(),
                                )
                            elif role == "system":
                                msg = Message(
                                    agent_id=agent_id,
                                    role=MessageRole.SYSTEM,
                                    content=content,
                                    status=MessageStatus.COMPLETED,
                                    completed_at=_utcnow(),
                                )
                            else:
                                continue
                            db.add(msg)
                        agent.last_message_preview = (final_new[-1][1] or "")[:200]
                        agent.last_message_at = _utcnow()
                        if not self._is_agent_in_use(agent.id, agent.tmux_pane):
                            agent.unread_count += len(final_new)
                        db.commit()
                        last_turn_count = len(turns)
                        self._emit(emit_agent_update(
                            agent.id, agent.status.value, agent.project
                        ))
                    _project_path = ""
                    if agent:
                        proj = db.get(Project, agent.project)
                        if proj:
                            _project_path = proj.path
                finally:
                    db.close()

                # If process is still alive, keep syncing (user may resume)
                if _project_path and _is_cli_session_alive(_project_path, agent.tmux_pane if agent else None):
                    logger.info(
                        "CLI session ended for agent %s but process alive — staying SYNCING",
                        agent_id,
                    )
                    continue

                # Flush any deferred push notification before stopping
                if pending_push_body:
                    logger.info(
                        "PUSH_DEBUG: flushing on session end for %s: %s",
                        agent_id, pending_push_body[:50],
                    )
                    db_push = SessionLocal()
                    try:
                        ag_push = db_push.get(Agent, agent_id)
                        if ag_push and not ag_push.muted:
                            self._refresh_pane_attached()
                            if not self._is_agent_in_use(agent_id, ag_push.tmux_pane):
                                from push import send_push_notification, is_notification_enabled
                                if is_notification_enabled("agents"):
                                    send_push_notification(
                                        title=_sync_agent_name or f"Agent {agent_id[:8]}",
                                        body=pending_push_body,
                                        url=f"/agents/{agent_id}",
                                    )
                    finally:
                        db_push.close()
                    pending_push_body = ""

                # Process is dead — transition to STOPPED
                logger.info(
                    "CLI session ended for agent %s — transitioning to STOPPED",
                    agent_id,
                )
                db = SessionLocal()
                try:
                    agent = db.get(Agent, agent_id)
                    if agent and agent.status == AgentStatus.SYNCING:
                        saved_pane = agent.tmux_pane
                        agent.status = AgentStatus.STOPPED
                        agent.tmux_pane = None
                        sys_msg = Message(
                            agent_id=agent_id,
                            role=MessageRole.SYSTEM,
                            content="CLI session ended — sync stopped",
                            status=MessageStatus.COMPLETED,
                        )
                        db.add(sys_msg)
                        agent.last_message_at = _utcnow()
                        db.commit()

                        self._emit(emit_agent_update(
                            agent.id, agent.status.value, agent.project
                        ))
                        self._emit(emit_new_message(agent.id, sys_msg.id, _sync_agent_name, _sync_project))

                        if not agent.muted and not self._is_agent_in_use(agent_id, saved_pane):
                            from push import send_push_notification, is_notification_enabled
                            if is_notification_enabled("agents"):
                                send_push_notification(
                                    title=f"\u2705 {_sync_agent_name or agent_id[:8]}",
                                    body="CLI session ended — sync complete",
                                    url=f"/agents/{agent_id}",
                                )
                finally:
                    db.close()
                break

    @staticmethod
    def _session_has_ended(jsonl_path: str) -> bool:
        """Check if a session JSONL contains a 'result' event (session ended)."""
        try:
            with open(jsonl_path, "rb") as f:
                # Read last 4KB — result event is always at the end
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 4096))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return False

        for line in tail.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if event.get("type") == "result":
                    return True
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return False

    # ---- Pane deduplication ----

    def _dedup_pane_agents(self, db: Session) -> set[str]:
        """Stop duplicate agents on the same tmux pane, keeping the freshest.

        Returns the set of agent IDs that were stopped.
        """
        from websocket import emit_agent_update

        syncing = db.query(Agent).filter(
            Agent.tmux_pane.is_not(None),
            Agent.status == AgentStatus.SYNCING,
        ).all()
        pane_agents: dict[str, list[Agent]] = {}
        for agent in syncing:
            pane_agents.setdefault(agent.tmux_pane, []).append(agent)

        stopped_ids: set[str] = set()
        for pane_id, dupes in pane_agents.items():
            if len(dupes) <= 1:
                continue

            def _session_mtime(a: Agent) -> float:
                if not a.session_id:
                    return 0.0
                p = db.get(Project, a.project)
                if not p:
                    return 0.0
                fpath = _resolve_session_jsonl(
                    a.session_id, p.path, a.worktree,
                )
                try:
                    return os.path.getmtime(fpath)
                except OSError:
                    return 0.0

            dupes.sort(key=_session_mtime, reverse=True)
            keeper = dupes[0]
            for stale in dupes[1:]:
                logger.info(
                    "Pane %s claimed by multiple agents — keeping %s, stopping %s",
                    pane_id, keeper.id, stale.id,
                )
                self._cancel_sync_task(stale.id)
                stale.status = AgentStatus.STOPPED
                stale.tmux_pane = None
                db.add(Message(
                    agent_id=stale.id, role=MessageRole.SYSTEM,
                    content="CLI session ended — another agent owns this tmux pane",
                    status=MessageStatus.COMPLETED,
                ))
                stopped_ids.add(stale.id)
                self._emit(emit_agent_update(stale.id, "STOPPED", stale.project))

        # Also detect agents sharing the same session_id (different panes)
        session_agents: dict[str, list[Agent]] = {}
        for agent in syncing:
            if agent.session_id and agent.id not in stopped_ids:
                session_agents.setdefault(agent.session_id, []).append(agent)

        for sid, dupes in session_agents.items():
            if len(dupes) <= 1:
                continue

            def _agent_freshness(a: Agent) -> float:
                if a.last_message_at:
                    return a.last_message_at.timestamp()
                return a.created_at.timestamp() if a.created_at else 0.0

            dupes.sort(key=_agent_freshness, reverse=True)
            keeper = dupes[0]
            for stale in dupes[1:]:
                logger.warning(
                    "Session %s claimed by multiple agents — "
                    "keeping %s, stopping %s",
                    sid[:12], keeper.id, stale.id,
                )
                self._cancel_sync_task(stale.id)
                stale.status = AgentStatus.STOPPED
                stale.tmux_pane = None
                db.add(Message(
                    agent_id=stale.id, role=MessageRole.SYSTEM,
                    content="Stopped — another agent already syncs this session",
                    status=MessageStatus.COMPLETED,
                ))
                stopped_ids.add(stale.id)
                self._emit(emit_agent_update(stale.id, "STOPPED", stale.project))

        return stopped_ids

    # ---- Recovery ----

    def _recover_agents(self):
        """On startup, clear stale state and recover agents."""
        db = SessionLocal()
        try:
            # Recover agents
            alive_statuses = [
                AgentStatus.IDLE, AgentStatus.EXECUTING,
                AgentStatus.STARTING, AgentStatus.SYNCING,
            ]
            agents = db.query(Agent).filter(
                Agent.status.in_(alive_statuses)
            ).all()

            # Collect agents that need sync restart (populated below,
            # scheduled after DB commit since start_session_sync is async).
            agents_to_sync: list[tuple[str, str, str]] = []  # (id, session_id, project_path)

            # Build pane map ONCE for definitive tmux session name matching.
            # Each agent launched via tmux has session name `ah-{id[:8]}`,
            # so we can resolve pane→agent without fragile CWD heuristics.
            pane_map = _build_tmux_claude_map()
            # session_name → pane_id for quick lookup
            session_name_to_pane: dict[str, str] = {
                info["session_name"]: pane_id
                for pane_id, info in pane_map.items()
                if not info["is_orchestrator"]
            }

            for agent in agents:
                if agent.status == AgentStatus.STARTING:
                    continue

                # Check if this CLI-synced agent has an active session
                if agent.cli_sync and agent.session_id and agent.status in (
                    AgentStatus.SYNCING, AgentStatus.IDLE,
                    AgentStatus.EXECUTING,
                ):
                    project = db.get(Project, agent.project)
                    if project:
                        import time as _time
                        project_path = project.path
                        jsonl_path = _resolve_session_jsonl(
                            agent.session_id, project_path, agent.worktree
                        )
                        # Session is active only if: file exists, no result
                        # event, AND either has a tmux pane or was recently written
                        session_active = False
                        if os.path.exists(jsonl_path) and not self._session_has_ended(jsonl_path):
                            # Resolve pane definitively via tmux session name
                            expected_name = f"ah-{agent.id[:8]}"
                            pane = session_name_to_pane.get(expected_name)
                            if not pane:
                                # Fallback: try generic detection
                                pane = _detect_tmux_pane_for_session(
                                    agent.session_id, project_path
                                )
                            if pane:
                                session_active = True
                            elif agent.cli_sync and agent.tmux_pane:
                                # cli_sync agents that previously had a tmux_pane
                                # should NOT fall back to file freshness — the
                                # pane is dead, so the session is dead.
                                session_active = False
                            else:
                                # No pane detected — fall back to session file
                                # freshness (works for CLI sessions running in
                                # non-tmux terminals or panes we can't match)
                                try:
                                    age = _time.time() - os.path.getmtime(jsonl_path)
                                    session_active = age < _STALE_SESSION_THRESHOLD
                                except OSError:
                                    pass

                        if session_active:
                            agent.status = AgentStatus.SYNCING
                            agent.tmux_pane = pane
                            agents_to_sync.append(
                                (agent.id, agent.session_id, project_path)
                            )
                            logger.info(
                                "Agent %s has active CLI session %s — will auto-sync",
                                agent.id, agent.session_id,
                            )
                            continue
                        else:
                            # Session not active — route cli_sync agents
                            # through the SYNCING liveness check below
                            # (regardless of their current status) so they
                            # get properly STOPPED instead of silently
                            # becoming normal IDLE agents.
                            logger.info(
                                "Agent %s (cli_sync) session %s not active "
                                "(was %s) — checking liveness",
                                agent.id, agent.session_id, agent.status.value,
                            )

                if agent.cli_sync or agent.status == AgentStatus.SYNCING:
                    # Check if this agent's own CLI process is still alive
                    project = db.get(Project, agent.project)
                    project_path = project.path if project else ""

                    # Try to find this agent's tmux pane — use session name
                    # for definitive matching first.
                    if not agent.tmux_pane:
                        expected_name = f"ah-{agent.id[:8]}"
                        pane = session_name_to_pane.get(expected_name)
                        if not pane and project_path and agent.session_id:
                            pane = _detect_tmux_pane_for_session(agent.session_id, project_path)
                        if pane:
                            agent.tmux_pane = pane

                    alive = False
                    if agent.tmux_pane:
                        # Has a pane — check that specific pane
                        alive = _is_cli_session_alive(project_path, agent.tmux_pane)
                    elif agent.cli_sync:
                        # cli_sync agents without a pane: pane detection already
                        # failed above.  Don't trust file freshness — the CLI
                        # session is gone.
                        alive = False
                    elif project_path and agent.session_id:
                        # Non-cli_sync SYNCING agent without pane — fall back
                        # to session file freshness
                        import time as _time
                        jsonl_path = _resolve_session_jsonl(
                            agent.session_id, project_path, agent.worktree,
                        )
                        try:
                            age = _time.time() - os.path.getmtime(jsonl_path)
                            alive = age < _STALE_SESSION_THRESHOLD
                        except OSError:
                            alive = False

                    if alive:
                        agent.status = AgentStatus.SYNCING
                        if agent.session_id:
                            agents_to_sync.append(
                                (agent.id, agent.session_id, project_path)
                            )
                        logger.info(
                            "Agent %s CLI process alive (pane=%s) — setting SYNCING",
                            agent.id, agent.tmux_pane,
                        )
                        continue

                    # Process is dead or session stale — stop
                    agent.status = AgentStatus.STOPPED
                    agent.tmux_pane = None
                    msg = Message(
                        agent_id=agent.id,
                        role=MessageRole.SYSTEM,
                        content="CLI session ended — sync stopped",
                        status=MessageStatus.COMPLETED,
                    )
                    db.add(msg)
                    continue

                if agent.status == AgentStatus.EXECUTING:
                    # Repair session JSONL if agent was mid-execution
                    if agent.session_id:
                        project = db.get(Project, agent.project)
                        if project:
                            repaired = repair_session_jsonl(
                                agent.session_id, project.path
                            )
                            if repaired:
                                logger.info(
                                    "Repaired session %s for agent %s",
                                    agent.session_id, agent.id,
                                )

                    agent.status = AgentStatus.IDLE
                    msg = Message(
                        agent_id=agent.id,
                        role=MessageRole.SYSTEM,
                        content="Agent recovered after restart — re-queuing pending messages",
                        status=MessageStatus.COMPLETED,
                    )
                    db.add(msg)

                # Re-queue EXECUTING messages so the original prompt is
                # re-dispatched automatically instead of being lost.
                # Also salvage any partial output from the crashed process.
                executing_msgs = db.query(Message).filter(
                    Message.agent_id == agent.id,
                    Message.status == MessageStatus.EXECUTING,
                ).all()
                for m in executing_msgs:
                    # Try to recover partial output from the predictable file
                    partial_file = f"/tmp/claude-output-{m.id}.log"
                    if os.path.exists(partial_file):
                        try:
                            with open(partial_file, "r", errors="replace") as f:
                                partial_logs = f.read()
                            if partial_logs.strip():
                                partial_text, partial_meta = _extract_result(partial_logs)
                                if partial_text and partial_text != "(no output)":
                                    partial_msg = Message(
                                        agent_id=agent.id,
                                        role=MessageRole.AGENT,
                                        content=f"*(partial — interrupted by restart)*\n\n{partial_text}",
                                        status=MessageStatus.COMPLETED,
                                        meta_json=partial_meta,
                                    )
                                    db.add(partial_msg)
                                    logger.info(
                                        "Recovered partial output for message %s (%d chars)",
                                        m.id, len(partial_text),
                                    )
                            # Clean up the temp file
                            os.unlink(partial_file)
                        except OSError:
                            pass

                    m.status = MessageStatus.PENDING
                    m.completed_at = None
                    m.error_message = None

            # Re-link STOPPED cli_sync agents whose tmux session is still alive.
            # These were skipped by the alive_statuses query above.
            stopped_cli = db.query(Agent).filter(
                Agent.status == AgentStatus.STOPPED,
                Agent.cli_sync == True,
            ).all()
            for agent in stopped_cli:
                expected_name = f"ah-{agent.id[:8]}"
                pane = session_name_to_pane.get(expected_name)
                if pane and agent.session_id:
                    project = db.get(Project, agent.project)
                    if project:
                        agent.status = AgentStatus.SYNCING
                        agent.tmux_pane = pane
                        agents_to_sync.append(
                            (agent.id, agent.session_id, project.path)
                        )
                        logger.info(
                            "Recovered STOPPED agent %s — tmux session %s still alive (pane=%s)",
                            agent.id, expected_name, pane,
                        )

            # Deduplicate pane ownership
            stopped_ids = self._dedup_pane_agents(db)
            agents_to_sync = [
                (aid, sid, pp) for aid, sid, pp in agents_to_sync
                if aid not in stopped_ids
            ]

            if agents:
                db.commit()
                logger.info("Recovered %d agents on startup", len(agents))

            # Schedule sync tasks for agents with active CLI sessions
            for aid, sid, ppath in agents_to_sync:
                self.start_session_sync(aid, sid, ppath)
        finally:
            db.close()
