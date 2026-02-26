"""Agent Dispatcher — scheduling loop for persistent agent processes."""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config import CLAUDE_HOME, MAX_CONCURRENT_WORKERS
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
)
from session_cache import (
    session_source_dir,
    cache_session,
    evict_session,
    repair_session_jsonl,
    restore_session,
)
from worker_manager import WorkerManager

logger = logging.getLogger("orchestrator.agent_dispatcher")


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


def _parse_stream_parts(logs: str) -> tuple[list[tuple[str, str]], dict | None]:
    """Parse stream-json logs into an ordered list of (kind, content) parts.

    Returns (parts, result_event) where parts is a list of
    ("text", text_string) or ("tool", summary_string) tuples.
    """
    parts = []
    result_event = None
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
                            parts.append(("text", block["text"]))
                        elif block.get("type") == "tool_use":
                            summary = _format_tool_summary(
                                block.get("name", ""),
                                block.get("input", {}),
                            )
                            if summary:
                                parts.append(("tool", summary))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return parts, result_event


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


def _extract_result(logs: str) -> str:
    """Extract agent response text and tool call summaries from stream-json."""
    parts, result_event = _parse_stream_parts(logs)

    # Friendly error messages for known error patterns
    if result_event and result_event.get("is_error"):
        errors = result_event.get("errors", [])
        for err in errors:
            if isinstance(err, str) and "No conversation found with session ID" in err:
                return "This session's conversation data is no longer available. It may have been cleaned up or created on a different machine. Please start a new conversation instead."

    text = _format_parts(parts)
    if text:
        return text

    # Fallback: return last chunk of raw output
    lines = logs.strip().splitlines()
    return "\n".join(lines[-20:]) if lines else "(no output)"


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
        except (json.JSONDecodeError, KeyError, TypeError):
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
        except (json.JSONDecodeError, KeyError, TypeError):
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
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except OSError:
        pass
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
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return None


def _detect_session_model(jsonl_path: str) -> str | None:
    """Extract the model ID from a session JSONL (from assistant messages)."""
    try:
        with open(jsonl_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                model = entry.get("message", {}).get("model")
                if model:
                    return model
    except OSError:
        pass
    return None


def _parse_session_turns(jsonl_path: str) -> list[tuple[str, str]]:
    """Parse a Claude Code session JSONL into conversation turns.

    Returns a list of (role, content) tuples where role is "user" or "assistant".
    Skips tool_result entries (intermediate tool calls) and queue-operations.
    Groups consecutive assistant entries into a single turn using _format_parts style.
    """
    turns: list[tuple[str, str]] = []

    try:
        with open(jsonl_path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return turns

    # Accumulate assistant blocks between user messages
    assistant_parts: list[tuple[str, str]] = []

    def flush_assistant():
        if assistant_parts:
            text = _format_parts(assistant_parts)
            if text.strip():
                turns.append(("assistant", text))
            assistant_parts.clear()

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
            # Real user message = string content (not tool_result list)
            if isinstance(content, str) and content.strip():
                stripped = content.strip()
                # Skip system-injected messages that aren't real user input
                if (
                    stripped.startswith("<local-command-caveat>")
                    or stripped.startswith("<command-name>")
                    or stripped.startswith("<local-command-stdout>")
                    or stripped.startswith("<system-reminder>")
                ):
                    continue
                # Compact summary → system message instead of user
                if stripped.startswith(
                    "This session is being continued from a previous conversation"
                ):
                    flush_assistant()
                    turns.append(("system", content))
                    continue
                flush_assistant()
                turns.append(("user", content))
            # list content = tool_result, skip (belongs to assistant turn)

        elif entry_type == "assistant":
            msg = entry.get("message", {})
            # Skip subagent messages
            if entry.get("parent_tool_use_id"):
                continue
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    assistant_parts.append(("text", block["text"]))
                elif block.get("type") == "tool_use":
                    summary = _format_tool_summary(
                        block.get("name", ""),
                        block.get("input", {}),
                    )
                    if summary:
                        assistant_parts.append(("tool", summary))

        elif entry_type == "queue-operation":
            # User messages sent while assistant is working (queued prompts)
            if entry.get("operation") == "enqueue":
                queued_content = entry.get("content", "")
                if isinstance(queued_content, str) and queued_content.strip():
                    flush_assistant()
                    turns.append(("user", queued_content.strip()))

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
                turns.append(("system", label))

    # Flush any remaining assistant content
    flush_assistant()
    return turns


# ---- tmux helpers ----

import subprocess as _sp

_CLAUDE_DEBUG_DIR = os.path.join(CLAUDE_HOME, "debug")


def _get_session_pid(session_id: str) -> int | None:
    """Extract the PID that owns a session from its debug log.

    Claude Code writes ``~/.claude/debug/{session_id}.txt`` and the first
    line matching ``Acquired PID lock for ... (PID \\d+)`` tells us which
    OS process owns the session.
    """
    debug_file = os.path.join(_CLAUDE_DEBUG_DIR, f"{session_id}.txt")
    try:
        with open(debug_file, "r") as f:
            for line in f:
                if "Acquired PID lock" in line:
                    m = re.search(r"\(PID (\d+)\)", line)
                    if m:
                        pid = int(m.group(1))
                        # Verify process is still alive
                        if os.path.exists(f"/proc/{pid}"):
                            return pid
                        return None
    except OSError:
        pass
    return None


def _is_orchestrator_process(pid: int) -> bool:
    """Check if a PID is an orchestrator-spawned claude process.

    Orchestrator-managed subprocesses (via WorkerManager) have the
    AGENTHIVE_MANAGED=1 environment variable set.  Tmux-launched CLI
    sessions (including those started from the web UI) do NOT have this
    var, so they are correctly detected as sync candidates.
    """
    try:
        with open(f"/proc/{pid}/environ", "r") as f:
            environ = f.read()
        return "AGENTHIVE_MANAGED=1" in environ
    except OSError:
        return False


def _build_tmux_claude_map() -> dict[str, dict]:
    """Build a map of all tmux panes running claude.

    Walks each pane's process tree downward from its shell PID to find
    claude child processes. This is authoritative because a pane's
    process tree is unambiguous.

    Returns: {pane_id: {"pid": int, "cwd": str, "is_orchestrator": bool}}
    """
    try:
        result = _sp.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {}
    except (_sp.TimeoutExpired, FileNotFoundError, OSError):
        return {}

    pane_map = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pane_id, shell_pid = parts

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
                    except OSError:
                        cwd = ""
                    pane_map[pane_id] = {
                        "pid": cpid,
                        "cwd": cwd,
                        "is_orchestrator": _is_orchestrator_process(cpid),
                    }
                    break
        except (_sp.TimeoutExpired, OSError, ValueError):
            continue

    return pane_map


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
                except (OSError, ValueError):
                    continue
    except (_sp.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # ---- Tier 2: pane-first process tree walk ----
    pane_map = _build_tmux_claude_map()
    user_candidates = [
        (pane_id, info)
        for pane_id, info in pane_map.items()
        if not info["is_orchestrator"] and info["cwd"] == real_project
    ]

    if len(user_candidates) == 1:
        return user_candidates[0][0]

    if len(user_candidates) > 1:
        # ---- Tier 3: match via debug-log PID ----
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

    # If we have a specific pane, ONLY check that one — don't match others
    if tmux_pane:
        info = pane_map.get(tmux_pane)
        return bool(info and not info["is_orchestrator"] and info["cwd"] == real_project)

    # No specific pane — broad scan (used for initial detection only)
    for pane_id, info in pane_map.items():
        if not info["is_orchestrator"] and info["cwd"] == real_project:
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
                    if cwd == real_project:
                        return True
                except (OSError, ValueError):
                    continue
    except (_sp.TimeoutExpired, FileNotFoundError, OSError):
        pass

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


def verify_tmux_pane(pane_id: str) -> bool:
    """Check if a tmux pane still exists."""
    try:
        result = _sp.run(
            ["tmux", "display-message", "-t", pane_id, "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == pane_id
    except (_sp.TimeoutExpired, FileNotFoundError, OSError):
        return False


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

        # Streaming output loops: agent_id -> asyncio.Task
        self._stream_tasks: dict[str, asyncio.Task] = {}

        # CLI session sync tasks: agent_id -> asyncio.Task
        self._sync_tasks: dict[str, asyncio.Task] = {}

        # CLI auto-detect tick counter (run every ~30s, not every 2s tick)
        self._cli_detect_counter = 0
        self._cli_detect_interval = 15  # ticks (15 * 2s = 30s)

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
        logger.info("Agent dispatcher started")

        self._recover_agents()

        while self.running:
            try:
                if not self.worker_mgr.ping():
                    await asyncio.sleep(5)
                    continue

                db = SessionLocal()
                try:
                    self._tick(db)
                finally:
                    db.close()
            except Exception:
                logger.exception("Agent dispatcher tick failed")
            await asyncio.sleep(2)

        logger.info("Agent dispatcher stopped")

    def stop(self):
        self.running = False

    def _emit(self, coro):
        try:
            asyncio.ensure_future(coro)
        except Exception:
            pass

    def _tick(self, db: Session):
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
        self._dispatch_tmux_scheduled(db)

        # 5. Auto-detect running CLI sessions (every ~30s)
        self._cli_detect_counter += 1
        if self._cli_detect_counter >= self._cli_detect_interval:
            self._cli_detect_counter = 0
            self._auto_detect_cli_sessions(db)
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
                done_agents.append(agent_id)
                continue

            if self.worker_mgr.is_exec_running(info["pid_str"]):
                continue

            # Exec finished — read output

            logs = self.worker_mgr.read_exec_output(
                info["pid_str"], info["output_file"]
            )
            result_text = _extract_result(logs)

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
                            evict_session(previous_session_id, project.path)
                    except Exception:
                        logger.debug("Failed to cache session %s", sid)

            # Update the message that triggered this exec
            message = db.get(Message, info["message_id"])
            if message:
                message.status = MessageStatus.COMPLETED
                message.completed_at = _utcnow()

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
                        agent.session_id = None

                    if message:
                        message.status = MessageStatus.PENDING
                        message.completed_at = None
                    agent.status = AgentStatus.IDLE
                    done_agents.append(agent_id)
                    continue

            if is_error:
                resp = Message(
                    agent_id=agent.id,
                    role=MessageRole.AGENT,
                    content=result_text or "Agent encountered an error",
                    status=MessageStatus.FAILED,
                    stream_log=_truncate(logs, 50000),
                    error_message=result_text[:200] if result_text else "Unknown error",
                )
                db.add(resp)
                agent.status = AgentStatus.IDLE
            else:
                resp = Message(
                    agent_id=agent.id,
                    role=MessageRole.AGENT,
                    content=result_text,
                    status=MessageStatus.COMPLETED,
                    stream_log=_truncate(logs, 50000),
                )
                db.add(resp)
                agent.status = AgentStatus.IDLE
                # Successful completion — reset stale session retry counter
                self._stale_session_retries.pop(agent_id, None)

            # Update agent denormalized fields
            preview = (result_text or "")[:200]
            agent.last_message_preview = preview
            agent.last_message_at = _utcnow()
            agent.unread_count += 1

            save_worker_log(f"agent-{agent.id}", logs)

            from websocket import emit_agent_update, emit_new_message
            self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
            self._emit(emit_new_message(agent.id, resp.id, agent.name, agent.project))

            if not agent.muted:
                from push import send_push_notification
                status_emoji = "\u274c" if is_error else "\u2705"
                send_push_notification(
                    title=f"{status_emoji} {agent.name}",
                    body=preview[:100],
                    url=f"/agents/{agent.id}",
                )

            done_agents.append(agent_id)

        for agent_id in done_agents:
            self._active_execs.pop(agent_id, None)
            self._cancel_stream_task(agent_id)

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

                # Update message
                message = db.get(Message, info["message_id"])
                if message:
                    message.status = MessageStatus.TIMEOUT
                    message.error_message = f"Timed out after {int(idle_seconds)}s of inactivity"
                    message.completed_at = now

                # Create system message
                sys_msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.SYSTEM,
                    content=f"Timed out after {int(idle_seconds)}s of inactivity (ran {int(elapsed)}s total)",
                    status=MessageStatus.COMPLETED,
                )
                db.add(sys_msg)

                agent.status = AgentStatus.IDLE
                agent.last_message_preview = f"Timed out after {int(idle_seconds)}s of inactivity"
                agent.last_message_at = now
                agent.unread_count += 1

                from websocket import emit_agent_update, emit_new_message
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                self._emit(emit_new_message(agent.id, sys_msg.id, agent.name, agent.project))

                if not agent.muted:
                    from push import send_push_notification
                    send_push_notification(
                        title=f"\u23f0 {agent.name}",
                        body=f"Timed out after {int(idle_seconds)}s of inactivity",
                        url=f"/agents/{agent.id}",
                    )

                timed_out.append(agent_id)

        for agent_id in timed_out:
            self._active_execs.pop(agent_id, None)
            self._cancel_stream_task(agent_id)

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

                from websocket import emit_agent_update
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                if not agent.muted:
                    from push import send_push_notification
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

                from websocket import emit_agent_update
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                if not agent.muted:
                    from push import send_push_notification
                    send_push_notification(
                        title=f"\u274c {agent.name}",
                        body="Failed to start — project directory not found",
                        url=f"/agents/{agent.id}",
                    )

    # ---- Step 4: Dispatch pending messages ----

    def _dispatch_pending_messages(self, db: Session):
        """For IDLE agents with PENDING user messages, exec claude."""
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

            # Ensure project directory exists
            try:
                project_path = self.worker_mgr.ensure_project_ready(project)
            except Exception:
                logger.exception("Project dir not ready for %s", project.name)
                continue

            # Use --resume with session_id if available.
            # Pre-check: if the session file is missing, restore from cache
            # now instead of waiting for Claude to error out (~5s wasted).
            resume_session_id = agent.session_id or None
            if resume_session_id:
                src_dir = session_source_dir(project_path)
                jsonl_path = os.path.join(
                    src_dir, f"{resume_session_id}.jsonl"
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
                        agent.session_id = None
                        resume_session_id = None

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
                }
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
                from websocket import emit_agent_update
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
            except Exception:
                logger.exception(
                    "Failed to exec claude for agent %s", agent.id
                )
                pending_msg.status = MessageStatus.FAILED
                pending_msg.error_message = "Failed to start claude process"

    def _dispatch_tmux_scheduled(self, db: Session):
        """Send due scheduled messages to SYNCING agents via tmux."""
        syncing_agents = db.query(Agent).filter(
            Agent.status == AgentStatus.SYNCING,
            Agent.cli_sync == True,
            Agent.tmux_pane.is_not(None),
        ).all()

        for agent in syncing_agents:
            due_msg = (
                db.query(Message)
                .filter(
                    Message.agent_id == agent.id,
                    Message.role == MessageRole.USER,
                    Message.status == MessageStatus.PENDING,
                    Message.scheduled_at.is_not(None),
                    Message.scheduled_at <= _utcnow(),
                )
                .order_by(Message.created_at.asc())
                .first()
            )
            if not due_msg:
                continue

            if not verify_tmux_pane(agent.tmux_pane):
                agent.tmux_pane = None
                continue

            ok = send_tmux_message(agent.tmux_pane, due_msg.content)
            if ok:
                due_msg.status = MessageStatus.COMPLETED
                due_msg.completed_at = _utcnow()
                due_msg.scheduled_at = None
                logger.info(
                    "Dispatched scheduled message %s to SYNCING agent %s via tmux",
                    due_msg.id, agent.id,
                )
            else:
                due_msg.status = MessageStatus.FAILED
                due_msg.error_message = "Failed to send via tmux"
                logger.warning(
                    "Failed to dispatch scheduled message %s via tmux for agent %s",
                    due_msg.id, agent.id,
                )

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

    # ---- Auto-detect running CLI sessions ----

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

        # Collect session IDs / tmux panes already owned by active agents
        active_session_ids: set[str] = set()
        active_tmux_panes: set[str] = set()
        stopped_session_agents: dict[str, Agent] = {}
        for a in db.query(Agent).filter(Agent.session_id.is_not(None)).all():
            if a.status == AgentStatus.STOPPED:
                stopped_session_agents[a.session_id] = a
            else:
                active_session_ids.add(a.session_id)
                if a.tmux_pane:
                    active_tmux_panes.add(a.tmux_pane)

        # Build map of tmux panes → interactive claude processes
        pane_map = _build_tmux_claude_map()
        agents_to_sync: list[tuple[str, str, str]] = []

        # Group untracked panes by project path, keyed by PID for matching
        panes_per_project: dict[str, list[tuple[str, int]]] = {}  # realpath -> [(pane_id, pid)]
        for pane_id, info in pane_map.items():
            if info["is_orchestrator"] or pane_id in active_tmux_panes:
                continue
            cwd = info["cwd"]
            if cwd in proj_by_path:
                panes_per_project.setdefault(cwd, []).append(
                    (pane_id, info["pid"])
                )

        for proj_path, pane_entries in panes_per_project.items():
            proj = proj_by_path[proj_path]
            session_dir = session_source_dir(proj.path)
            if not os.path.isdir(session_dir):
                continue

            # Collect available session JSONLs sorted by mtime descending
            candidates: list[tuple[str, str, float]] = []  # (sid, fpath, mtime)
            try:
                for fname in os.listdir(session_dir):
                    if not fname.endswith(".jsonl"):
                        continue
                    fpath = os.path.join(session_dir, fname)
                    if not os.path.isfile(fpath):
                        continue
                    sid = fname.replace(".jsonl", "")
                    if sid in active_session_ids:
                        continue
                    candidates.append((sid, fpath, os.path.getmtime(fpath)))
            except OSError:
                continue
            candidates.sort(key=lambda x: x[2], reverse=True)

            # Build PID→session map from debug logs for deterministic matching.
            # Fall back to mtime-based assignment for sessions without debug info.
            pid_to_session: dict[int, tuple[str, str]] = {}  # pid -> (sid, fpath)
            unmatched: list[tuple[str, str, float]] = []
            for sid, fpath, mtime in candidates:
                session_pid = _get_session_pid(sid)
                if session_pid:
                    pid_to_session[session_pid] = (sid, fpath)
                else:
                    unmatched.append((sid, fpath, mtime))

            # Assign sessions to panes: PID match first, then mtime fallback
            for pane_id, pane_pid in pane_entries:
                best_sid, best_fpath = None, None

                # Try exact PID match via debug log
                if pane_pid in pid_to_session:
                    best_sid, best_fpath = pid_to_session.pop(pane_pid)
                elif unmatched:
                    best_sid, best_fpath, _ = unmatched.pop(0)
                else:
                    # Try leftover PID-matched sessions (PID may have changed)
                    if pid_to_session:
                        _, (best_sid, best_fpath) = pid_to_session.popitem()

                if not best_sid:
                    continue

                active_session_ids.add(best_sid)
                active_tmux_panes.add(pane_id)

                # --- Try to revive a stopped agent that owns this session ---
                stopped_agent = stopped_session_agents.get(best_sid)
                if stopped_agent:
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

                # --- Try to revive a recently-stopped cli_sync agent in same project ---
                now = _utcnow()
                recently_stopped = [
                    a for a in stopped_session_agents.values()
                    if a.project == proj.name
                    and a.cli_sync
                    and a.last_message_at
                    and (now - a.last_message_at.replace(tzinfo=timezone.utc)).total_seconds() < 3600
                ]
                if recently_stopped:
                    candidate = max(recently_stopped, key=lambda a: a.last_message_at)
                    candidate.session_id = best_sid
                    candidate.status = AgentStatus.SYNCING
                    candidate.tmux_pane = pane_id
                    candidate.last_message_at = _utcnow()
                    db.flush()
                    logger.info(
                        "Revived stopped agent %s with new session %s (tmux=%s)",
                        candidate.id, best_sid[:12], pane_id,
                    )
                    agents_to_sync.append((candidate.id, best_sid, proj.path))
                    self._emit(emit_agent_update(candidate.id, "SYNCING", proj.name))
                    continue

                # --- Check for existing SYNCING agent with same tmux pane ---
                existing_pane_agent = db.query(Agent).filter(
                    Agent.status == AgentStatus.SYNCING,
                    Agent.tmux_pane == pane_id,
                    Agent.cli_sync == True,
                ).first()
                if existing_pane_agent:
                    old_sid = existing_pane_agent.session_id
                    self._cancel_sync_task(existing_pane_agent.id)
                    existing_pane_agent.session_id = best_sid
                    existing_pane_agent.last_message_at = _utcnow()
                    db.flush()
                    logger.info(
                        "Reused SYNCING agent %s (pane %s) — session %s → %s",
                        existing_pane_agent.id, pane_id,
                        (old_sid or "")[:12], best_sid[:12],
                    )
                    agents_to_sync.append((existing_pane_agent.id, best_sid, proj.path))
                    self._emit(emit_agent_update(existing_pane_agent.id, "SYNCING", proj.name))
                    continue

                # --- Create new SYNCING agent ---
                agent_name = "CLI session"
                detected_model = None
                turns = []
                if best_fpath:
                    try:
                        turns = _parse_session_turns(best_fpath)
                        for role, content in turns:
                            if role == "user" and content:
                                agent_name = (content or "")[:80]
                                break
                        detected_model = _detect_session_model(best_fpath)
                    except Exception:
                        turns = []

                logger.info(
                    "Auto-detected tmux CLI session %s in project %s (pane %s)",
                    best_sid[:12], proj.name, pane_id,
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
                    last_message_preview=agent_name,
                    last_message_at=_utcnow(),
                )
                db.add(agent)
                db.flush()

                # Import existing turns as messages
                try:
                    for role, content in turns:
                        if role == "user":
                            msg = Message(
                                agent_id=agent.id,
                                role=MessageRole.USER,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                completed_at=_utcnow(),
                            )
                        elif role == "assistant":
                            msg = Message(
                                agent_id=agent.id,
                                role=MessageRole.AGENT,
                                content=content,
                                status=MessageStatus.COMPLETED,
                                completed_at=_utcnow(),
                            )
                        else:
                            continue
                        db.add(msg)
                except Exception:
                    logger.debug("Failed to import turns for auto-detected session", exc_info=True)

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

        stale_threshold = 1800  # 30 minutes without writes → session is done

        # Include STARTING so launched tmux agents that never got a
        # session_id are still reaped when their process dies.
        candidates = db.query(Agent).filter(
            Agent.status.in_([
                AgentStatus.STARTING, AgentStatus.SYNCING,
                AgentStatus.IDLE, AgentStatus.ERROR,
                AgentStatus.EXECUTING,
            ]),
        ).all()

        # Build the tmux pane map once (expensive), reuse for all agents
        pane_map = _build_tmux_claude_map()

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
                    src_dir = session_source_dir(proj.path)
                    jsonl_path = os.path.join(
                        src_dir, f"{agent.session_id}.jsonl"
                    )
                    try:
                        mtime = os.path.getmtime(jsonl_path)
                        age = time.time() - mtime
                        alive = age < stale_threshold
                    except OSError:
                        alive = False

            # For STARTING agents without pane or session_id, give them
            # a grace period (60s) for the background task to set things up.
            if (
                not alive
                and agent.status == AgentStatus.STARTING
                and not agent.tmux_pane
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

        file_pos = 0
        last_content = ""
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
            except (FileNotFoundError, OSError):
                continue

            if not new_data:
                continue

            # New output arrived — refresh inactivity timeout
            info = self._active_execs.get(agent_id)
            if info:
                info["last_activity"] = _utcnow()

            # Re-read entire file to parse from scratch (stream-json
            # events can span multiple reads and we need the full picture)
            try:
                with open(output_file, "r", errors="replace") as f:
                    full_logs = f.read()
            except (FileNotFoundError, OSError):
                continue

            parts, _ = _parse_stream_parts(full_logs)
            content = _format_parts(parts)

            if content and content != last_content:
                last_content = content
                self._emit(emit_agent_stream(agent_id, content))

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
        jsonl_path = os.path.join(
            session_source_dir(project_path), f"{session_id}.jsonl"
        )
        turns = _parse_session_turns(jsonl_path)
        if not turns:
            return 0

        # Detect the actual model used in the CLI session
        session_model = _parse_session_model(jsonl_path)

        db = SessionLocal()
        try:
            imported = 0
            for role, content in turns:
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

    def _cancel_sync_task(self, agent_id: str):
        """Cancel and clean up a sync task."""
        task = self._sync_tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()

    def _detect_successor_session(
        self, current_sid: str, project_path: str, agent_id: str,
    ) -> str | None:
        """Check if a newer session JSONL supersedes the current one.

        Returns the new session_id if found, otherwise None.
        Used to detect when Claude auto-continues into a new session
        (e.g. context too long).
        """
        session_dir = session_source_dir(project_path)
        try:
            current_mtime = os.path.getmtime(
                os.path.join(session_dir, f"{current_sid}.jsonl")
            )
        except OSError:
            return None

        # Get the tmux pane PID for this agent so we can verify ownership
        pane_pid: int | None = None
        db = SessionLocal()
        try:
            agent = db.get(Agent, agent_id)
            if agent and agent.tmux_pane:
                pane_info = _build_tmux_claude_map().get(agent.tmux_pane)
                if pane_info:
                    pane_pid = pane_info["pid"]

            # Collect ALL session IDs ever assigned to any agent (including
            # stopped ones) to avoid re-adopting a dead session that was just
            # reaped moments ago.
            active_sids: set[str] = set()
            for a in db.query(Agent).filter(
                Agent.session_id.is_not(None),
            ).all():
                active_sids.add(a.session_id)
        finally:
            db.close()

        # Look for a JSONL newer than the current one
        best_sid, best_mtime = None, current_mtime
        try:
            for fname in os.listdir(session_dir):
                if not fname.endswith(".jsonl"):
                    continue
                sid = fname.replace(".jsonl", "")
                if sid == current_sid or sid in active_sids:
                    continue
                fpath = os.path.join(session_dir, fname)
                mtime = os.path.getmtime(fpath)
                if mtime > best_mtime:
                    # Verify it belongs to the same tmux pane via PID
                    session_pid = _get_session_pid(sid)
                    if session_pid is not None:
                        if pane_pid is not None and session_pid != pane_pid:
                            continue  # belongs to a different pane
                    else:
                        # No debug log yet — only accept if notably newer
                        if mtime - current_mtime < 5:
                            continue
                    best_sid, best_mtime = sid, mtime
        except OSError:
            pass
        return best_sid

    def _spawn_successor_agent(
        self, old_agent_id: str, new_sid: str, project_path: str,
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

            # Stop old agent
            self._cancel_sync_task(old_agent_id)
            old_agent.status = AgentStatus.STOPPED
            old_agent.tmux_pane = None
            db.flush()
            self._emit(emit_agent_update(old_agent_id, "STOPPED", project_name))

            # Parse new session for name and turns
            new_fpath = os.path.join(
                session_source_dir(project_path), f"{new_sid}.jsonl"
            )
            agent_name = "CLI session (continued)"
            turns = []
            detected_model = model
            try:
                turns = _parse_session_turns(new_fpath)
                for role, content in turns:
                    if role == "user" and content:
                        agent_name = (content or "")[:80]
                        break
                detected_model = _detect_session_model(new_fpath) or model
            except Exception:
                turns = []

            # Create new agent
            new_agent = Agent(
                project=project_name,
                name=agent_name,
                mode=AgentMode.AUTO,
                status=AgentStatus.SYNCING,
                model=detected_model,
                session_id=new_sid,
                cli_sync=True,
                tmux_pane=tmux_pane,
                last_message_preview=agent_name,
                last_message_at=_utcnow(),
            )
            db.add(new_agent)
            db.flush()

            # Import existing turns
            for role, content in turns:
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
                        completed_at=_utcnow(),
                    )
                else:
                    continue
                db.add(msg)

            db.commit()
            self._emit(emit_agent_update(new_agent.id, "SYNCING", project_name))
            self.start_session_sync(new_agent.id, new_sid, project_path)

            logger.info(
                "Spawned successor agent %s for session %s (old: %s)",
                new_agent.id, new_sid[:12], old_agent_id,
            )
        finally:
            db.close()

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
        finally:
            self._sync_tasks.pop(agent_id, None)

    async def _sync_session_loop_inner(
        self, agent_id: str, session_id: str, project_path: str
    ):
        """Inner sync loop — see _sync_session_loop for docs."""
        POLL_INTERVAL = 3  # seconds between checks

        jsonl_path = os.path.join(
            session_source_dir(project_path), f"{session_id}.jsonl"
        )

        from websocket import emit_agent_stream, emit_agent_update, emit_new_message

        # Cache agent name/project for notification payloads
        _sync_agent_name = ""
        _sync_project = ""
        db = SessionLocal()
        try:
            _ag = db.get(Agent, agent_id)
            if _ag:
                _sync_agent_name = _ag.name
                _sync_project = _ag.project
        finally:
            db.close()

        last_size = 0
        last_turn_count = 0
        last_tail_hash = ""  # Hash of last turn content to detect updates
        is_generating = False

        # Get the current file size and turn count so we only import new turns
        try:
            with open(jsonl_path, "r", errors="replace") as f:
                last_size = f.seek(0, 2)  # seek to end
        except OSError:
            pass

        initial_turns = _parse_session_turns(jsonl_path)
        last_turn_count = len(initial_turns)
        if initial_turns:
            last_tail_hash = str(len(initial_turns[-1][1]))

        # Reconcile: full-scan comparison between JSONL turns and DB
        # messages.  Queue-operation user messages can appear anywhere
        # in the conversation (interspersed between assistant turns), so
        # we check every turn against the DB and insert any that are
        # missing, regardless of position.
        db = SessionLocal()
        try:
            conv_turns = [
                (r, c) for r, c in initial_turns
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
                    sig = (role_char, m.content[:200])
                    db_sig_counts[sig] = db_sig_counts.get(sig, 0) + 1

                # Walk through JSONL turns and collect missing ones
                missing: list[tuple[str, str]] = []
                for r, c in conv_turns:
                    role_char = "u" if r == "user" else "a"
                    sig = (role_char, c[:200])
                    if db_sig_counts.get(sig, 0) > 0:
                        db_sig_counts[sig] -= 1  # consume the match
                    else:
                        missing.append((r, c))

                if missing and agent:
                    for role, content in missing:
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
                    for role, content in reversed(conv_turns):
                        if role == "assistant":
                            last_assistant = content
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
                        db.commit()
                        self._emit(emit_new_message(
                            agent_id, "sync", _sync_agent_name, _sync_project,
                        ))
        finally:
            db.close()

        idle_polls = 0
        while True:
            await asyncio.sleep(POLL_INTERVAL)

            try:
                current_size = os.path.getsize(jsonl_path)
            except OSError:
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
                last_tail_hash = str(len(turns[-1][1])) if turns else ""
                last_size = current_size
                idle_polls = 0
                continue

            if current_size <= last_size:
                idle_polls += 1
                # Periodically check if we should still be syncing
                if idle_polls % 10 == 0:
                    db = SessionLocal()
                    try:
                        agent = db.get(Agent, agent_id)
                        if not agent or agent.status != AgentStatus.SYNCING:
                            logger.info("Sync loop exiting for agent %s (status changed)", agent_id)
                            break
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
                        continue  # tmux is dead, skip continuation check

                    new_sid = self._detect_successor_session(
                        session_id, project_path, agent_id,
                    )
                    if new_sid:
                        logger.info(
                            "Session continuation detected for agent %s: "
                            "%s → %s — stopping old, creating new",
                            agent_id, session_id[:12], new_sid[:12],
                        )
                        self._spawn_successor_agent(
                            agent_id, new_sid, project_path,
                        )
                        return
                continue
            idle_polls = 0
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
                last_tail_hash = str(len(turns[-1][1])) if turns else ""
                continue

            new_turns = turns[last_turn_count:]

            # Check if the last existing turn's content grew (same turn count
            # but the assistant accumulated more tool calls / text blocks)
            tail_hash = str(len(turns[-1][1])) if turns else ""
            last_turn_updated = (
                not new_turns
                and len(turns) == last_turn_count
                and tail_hash != last_tail_hash
                and turns
                and turns[-1][0] == "assistant"
            )

            if not new_turns and not last_turn_updated:
                # Stream partial content when the assistant is generating
                # (file size unchanged but we know it's active)
                if not is_generating:
                    is_generating = True
                self._emit(emit_agent_stream(agent_id, ""))
                continue

            db = SessionLocal()
            try:
                agent = db.get(Agent, agent_id)
                if not agent or agent.status != AgentStatus.SYNCING:
                    break

                if last_turn_updated:
                    # Update the last agent message in-place
                    last_msg = db.query(Message).filter(
                        Message.agent_id == agent_id,
                        Message.role == MessageRole.AGENT,
                    ).order_by(Message.created_at.desc()).first()
                    if last_msg:
                        last_msg.content = turns[-1][1]
                        last_msg.completed_at = _utcnow()
                        agent.last_message_preview = (turns[-1][1] or "")[:200]
                        agent.last_message_at = _utcnow()
                        db.commit()
                        # Stream the updated content to connected clients
                        self._emit(emit_agent_stream(
                            agent_id, turns[-1][1],
                        ))
                        self._emit(emit_new_message(agent.id, "sync", _sync_agent_name, _sync_project))
                        last_tail_hash = tail_hash
                        is_generating = False
                        logger.debug(
                            "Updated last turn content for agent %s (%s chars)",
                            agent_id, len(turns[-1][1]),
                        )
                else:
                    # Before importing new turns, check if the turn just
                    # before the new ones grew (assistant was mid-response
                    # last time, now finished and user sent a new message).
                    if last_turn_count > 0 and new_turns:
                        prev_role, prev_content = turns[last_turn_count - 1]
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
                                logger.info(
                                    "Updated previous turn content for agent %s "
                                    "(%d -> %d chars)",
                                    agent_id, old_len, len(prev_content),
                                )

                    # Import new turns
                    for role, content in new_turns:
                        if role == "user":
                            # Dedup: skip if a matching web-sent (or sourceless) message already exists
                            from sqlalchemy import or_
                            existing_web = db.query(Message).filter(
                                Message.agent_id == agent_id,
                                Message.role == MessageRole.USER,
                                or_(Message.source == "web", Message.source.is_(None)),
                                Message.content == content,
                            ).first()
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
                    agent.unread_count += len(new_turns)
                    db.commit()

                    last_turn_count = len(turns)
                    last_tail_hash = tail_hash
                    is_generating = False
                    self._emit(emit_agent_update(
                        agent.id, agent.status.value, agent.project
                    ))
                    self._emit(emit_new_message(agent.id, "sync", _sync_agent_name, _sync_project))

                    # Only notify for agent/system turns (not user messages)
                    if not agent.muted:
                        _push_body = ""
                        for _r, _c in reversed(new_turns):
                            if _r == "assistant":
                                _push_body = _c[:120]
                                break
                            if _r == "system":
                                _push_body = _c[:120]
                                break
                        if _push_body:
                            from push import send_push_notification
                            send_push_notification(
                                title=_sync_agent_name or f"Agent {agent_id[:8]}",
                                body=_push_body,
                                url=f"/agents/{agent_id}",
                            )

                    logger.info(
                        "Synced %d new turns for agent %s",
                        len(new_turns), agent_id,
                    )
            finally:
                db.close()

            # Check if the CLI session has ended by looking for a 'result' event
            if self._session_has_ended(jsonl_path):
                # Sync any final turns first
                db = SessionLocal()
                try:
                    turns = _parse_session_turns(jsonl_path)
                    final_new = turns[last_turn_count:]
                    agent = db.get(Agent, agent_id)
                    if agent and final_new:
                        for role, content in final_new:
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

                # Process is dead — transition to IDLE
                logger.info(
                    "CLI session ended for agent %s — transitioning to IDLE",
                    agent_id,
                )
                db = SessionLocal()
                try:
                    agent = db.get(Agent, agent_id)
                    if agent and agent.status == AgentStatus.SYNCING:
                        agent.status = AgentStatus.IDLE
                        sys_msg = Message(
                            agent_id=agent_id,
                            role=MessageRole.SYSTEM,
                            content="CLI session ended — sync complete",
                            status=MessageStatus.COMPLETED,
                        )
                        db.add(sys_msg)
                        agent.last_message_at = _utcnow()
                        db.commit()

                        self._emit(emit_agent_update(
                            agent.id, agent.status.value, agent.project
                        ))
                        self._emit(emit_new_message(agent.id, sys_msg.id, _sync_agent_name, _sync_project))

                        if not agent.muted:
                            from push import send_push_notification
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
                        jsonl_path = os.path.join(
                            session_source_dir(project_path),
                            f"{agent.session_id}.jsonl",
                        )
                        # Session is active only if: file exists, no result
                        # event, AND either has a tmux pane or was recently written
                        session_active = False
                        if os.path.exists(jsonl_path) and not self._session_has_ended(jsonl_path):
                            pane = _detect_tmux_pane_for_session(
                                agent.session_id, project_path
                            )
                            if pane:
                                session_active = True
                            elif not agent.cli_sync:
                                # Non-tmux agents: accept freshness as liveness
                                try:
                                    age = _time.time() - os.path.getmtime(jsonl_path)
                                    session_active = age < 1800  # 30 min
                                except OSError:
                                    pass
                            # cli_sync agents without a pane → not active

                        if session_active:
                            agent.status = AgentStatus.SYNCING
                            agent.tmux_pane = pane
                            msg = Message(
                                agent_id=agent.id,
                                role=MessageRole.SYSTEM,
                                content="Auto-syncing active CLI session after restart",
                                status=MessageStatus.COMPLETED,
                            )
                            db.add(msg)
                            agents_to_sync.append(
                                (agent.id, agent.session_id, project_path)
                            )
                            logger.info(
                                "Agent %s has active CLI session %s — will auto-sync",
                                agent.id, agent.session_id,
                            )
                            continue

                if agent.status == AgentStatus.SYNCING:
                    # Check if this agent's own CLI process is still alive
                    project = db.get(Project, agent.project)
                    project_path = project.path if project else ""

                    # Try to find this agent's tmux pane
                    if project_path and agent.session_id and not agent.tmux_pane:
                        pane = _detect_tmux_pane_for_session(agent.session_id, project_path)
                        if pane:
                            agent.tmux_pane = pane

                    alive = False
                    if agent.tmux_pane:
                        # Has a pane — check that specific pane
                        alive = _is_cli_session_alive(project_path, agent.tmux_pane)
                    elif project_path and agent.session_id and not agent.cli_sync:
                        # Non-tmux agents: accept session file freshness
                        import time as _time
                        src_dir = session_source_dir(project_path)
                        jsonl_path = os.path.join(src_dir, f"{agent.session_id}.jsonl")
                        try:
                            age = _time.time() - os.path.getmtime(jsonl_path)
                            alive = age < 1800
                        except OSError:
                            alive = False
                    # cli_sync agents without a tmux pane → dead

                    if alive:
                        if agent.session_id:
                            agents_to_sync.append(
                                (agent.id, agent.session_id, project_path)
                            )
                        logger.info(
                            "Agent %s CLI process alive (pane=%s) — keeping SYNCING",
                            agent.id, agent.tmux_pane,
                        )
                        continue

                    # Process is dead or session stale — stop
                    agent.status = AgentStatus.STOPPED
                    agent.tmux_pane = None
                    msg = Message(
                        agent_id=agent.id,
                        role=MessageRole.SYSTEM,
                        content="CLI session ended — sync complete",
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
                                partial_text = _extract_result(partial_logs)
                                if partial_text and partial_text != "(no output)":
                                    partial_msg = Message(
                                        agent_id=agent.id,
                                        role=MessageRole.AGENT,
                                        content=f"*(partial — interrupted by restart)*\n\n{partial_text}",
                                        status=MessageStatus.COMPLETED,
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

            if agents:
                db.commit()
                logger.info("Recovered %d agents on startup", len(agents))

            # Schedule sync tasks for agents with active CLI sessions
            for aid, sid, ppath in agents_to_sync:
                self.start_session_sync(aid, sid, ppath)
        finally:
            db.close()
