"""Agent Dispatcher — scheduling loop for persistent agent processes."""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config import MAX_CONCURRENT_WORKERS
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
    Task,
)
from plan_manager import PlanManager
from session_cache import (
    _session_source_dir,
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

        elif entry_type == "system":
            # Auto-compaction summary — treat as system message
            flush_assistant()
            summary_data = entry.get("summary", "")
            if summary_data:
                turns.append(("system", f"*(context compressed)*"))

    # Flush any remaining assistant content
    flush_assistant()
    return turns


class AgentDispatcher:
    """Dispatch loop for persistent agent processes."""

    def __init__(self, worker_manager: WorkerManager):
        self.worker_mgr = worker_manager
        self.plan_mgr = PlanManager(worker_manager)
        self.running = False

        # In-memory tracking of active execs
        # agent_id -> {pid_str, output_file, message_id, started_at, last_activity}
        self._active_execs: dict[str, dict] = {}

        # Planner processes (ephemeral, for PLAN-mode agents)
        # agent_id -> pid_str
        self._active_planners: dict[str, str] = {}

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
                    AgentStatus.PLANNING, AgentStatus.PLAN_REVIEW,
                    AgentStatus.SYNCING,
                ]),
            ).all()
            results = []
            for agent in agents:
                project = db.get(Project, agent.project)
                if not project:
                    continue
                project_path = self.worker_mgr._get_project_path(project.name)
                results.append((agent.session_id, project_path))
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
        for agent_id, pid_str in self._active_planners.items():
            results.append({
                "agent_id": agent_id,
                "type": "planner",
                "started_at": None,
                "elapsed_seconds": None,
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
        # 1. Harvest completed execs
        self._harvest_completed_execs(db)

        # 2. Harvest completed planners
        self._harvest_planners(db)

        # 3. Check exec timeouts
        self._check_exec_timeouts(db)

        # 4. Start new agents
        self._start_new_agents(db)

        # 5. Start planning for agents that need it
        self._start_planning(db)

        # 6. Dispatch pending messages to idle agents
        self._dispatch_pending_messages(db)

        # 7. Auto-detect running CLI sessions (every ~30s)
        self._cli_detect_counter += 1
        if self._cli_detect_counter >= self._cli_detect_interval:
            self._cli_detect_counter = 0
            self._auto_detect_cli_sessions(db)
            self._reap_stale_syncing_agents(db)

        db.commit()

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
                    project_path = self.worker_mgr._get_project_path(project.name)
                    try:
                        cache_session(sid, project_path)
                        if previous_session_id and previous_session_id != sid:
                            evict_session(previous_session_id, project_path)
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
                    project_path = self.worker_mgr._get_project_path(
                        project.name
                    ) if project else None

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

    # ---- Step 2: Harvest planners ----

    def _harvest_planners(self, db: Session):
        """Check planning processes that have finished."""
        done = []
        for agent_id, pid_str in list(self._active_planners.items()):
            status = self.worker_mgr.get_status(pid_str)
            if status not in ("exited", "removed"):
                continue

            agent = db.get(Agent, agent_id)
            if not agent:
                done.append(agent_id)
                continue

            logs = self.worker_mgr.get_logs(pid_str)

            if "EXIT_SUCCESS" in logs:
                plan_text = PlanManager.extract_plan(logs)
                agent.plan = plan_text
                agent.status = AgentStatus.PLAN_REVIEW

                # Add plan as system message
                msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.SYSTEM,
                    content=f"## Plan\n\n{plan_text}",
                    status=MessageStatus.COMPLETED,
                )
                db.add(msg)
                agent.last_message_preview = "Plan ready for review"
                agent.last_message_at = _utcnow()
                agent.unread_count += 1

                from websocket import emit_agent_update, emit_new_message
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                self._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))

                from push import send_push_notification
                send_push_notification(
                    title=f"\U0001f4cb {agent.name}",
                    body="Plan ready for review",
                    url=f"/agents/{agent.id}",
                )
            else:
                agent.plan = "(Planning failed)"
                agent.status = AgentStatus.PLAN_REVIEW
                msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.SYSTEM,
                    content="Planning failed — worker did not produce a plan.",
                    status=MessageStatus.FAILED,
                )
                db.add(msg)
                agent.last_message_preview = "Planning failed"
                agent.last_message_at = _utcnow()
                agent.unread_count += 1

                from websocket import emit_agent_update, emit_new_message
                self._emit(emit_agent_update(agent.id, agent.status.value, agent.project))
                self._emit(emit_new_message(agent.id, msg.id, agent.name, agent.project))

                from push import send_push_notification
                send_push_notification(
                    title=f"\u274c {agent.name}",
                    body="Planning failed",
                    url=f"/agents/{agent.id}",
                )

            # Clean up planner process tracking
            self.worker_mgr._processes.pop(pid_str, None)

            done.append(agent_id)

        for agent_id in done:
            self._active_planners.pop(agent_id, None)

    # ---- Step 3: Timeouts ----

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
        """Validate project dirs for STARTING agents and set them to IDLE."""
        starting = db.query(Agent).filter(Agent.status == AgentStatus.STARTING).all()

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
                from push import send_push_notification
                send_push_notification(
                    title=f"\u274c {agent.name}",
                    body="Failed to start — project directory not found",
                    url=f"/agents/{agent.id}",
                )

    # ---- Step 5: Planning ----

    def _start_planning(self, db: Session):
        """Start planning for PLAN-mode agents that need it."""
        idle_agents = db.query(Agent).filter(
            Agent.status == AgentStatus.IDLE,
            Agent.plan_approved == False,  # noqa: E712
            Agent.mode == AgentMode.PLAN,
        ).all()

        # Auto-approve INTERVIEW and AUTO agents
        auto_agents = db.query(Agent).filter(
            Agent.status == AgentStatus.IDLE,
            Agent.plan_approved == False,  # noqa: E712
            Agent.mode.in_([AgentMode.AUTO, AgentMode.INTERVIEW]),
        ).all()
        for agent in auto_agents:
            agent.plan_approved = True

        planning_count = len(self._active_planners)
        executing_count = len(self._active_execs)
        total_active = planning_count + executing_count

        for agent in idle_agents:
            if agent.id in self._active_planners:
                continue
            if total_active >= MAX_CONCURRENT_WORKERS:
                break

            has_pending = db.query(Message).filter(
                Message.agent_id == agent.id,
                Message.role == MessageRole.USER,
                Message.status == MessageStatus.PENDING,
            ).first()
            if not has_pending:
                continue

            project = db.get(Project, agent.project)
            if not project:
                continue

            try:
                fake_task = Task(
                    id=agent.id,
                    project=agent.project,
                    prompt=has_pending.content,
                )
                pid_str = self.plan_mgr.start_planning(fake_task, project)
                self._active_planners[agent.id] = pid_str
                agent.status = AgentStatus.PLANNING
                total_active += 1

                sys_msg = Message(
                    agent_id=agent.id,
                    role=MessageRole.SYSTEM,
                    content="Generating plan...",
                    status=MessageStatus.COMPLETED,
                )
                db.add(sys_msg)

                logger.info("Started planning for agent %s", agent.id)
            except Exception:
                logger.exception("Failed to start planner for agent %s", agent.id)

    # ---- Step 6: Dispatch pending messages ----

    def _dispatch_pending_messages(self, db: Session):
        """For IDLE agents with PENDING user messages, exec claude."""
        idle_agents = db.query(Agent).filter(
            Agent.status == AgentStatus.IDLE,
            Agent.plan_approved == True,  # noqa: E712
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
                src_dir = _session_source_dir(project_path)
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

    def _build_agent_prompt(
        self, agent: Agent, project: Project, user_message: str,
        include_history: bool = False, db: Session | None = None,
    ) -> str:
        """Build the prompt sent to claude for an agent message.
        When include_history=True, injects recent conversation history so context
        is preserved even when the Claude Code session can't be resumed.
        """
        project_path = self.worker_mgr._get_project_path(project.name)

        history_block = ""
        if include_history and db:
            history_block = self._format_conversation_history(agent, db)

        base = (
            f"You are working in project: {project.display_name}\n"
            f"Project path: {project_path}\n"
            f"\n"
            f"First read the project's CLAUDE.md to understand project conventions.\n"
            f"{history_block}\n"
            f"{user_message}"
        )
        if agent.mode == AgentMode.INTERVIEW:
            return (
                f"{base}\n\n"
                f"You are in INTERVIEW mode. Answer questions, explore and explain code, "
                f"discuss approaches — but do NOT modify any files or make commits."
            )
        return (
            f"{base}\n\n"
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
        """Check for active CLI sessions by scanning session JSONL files.

        For each registered project, look for recently modified .jsonl files
        in ~/.claude/projects/. If found and not already linked to an agent,
        auto-create a syncing agent so it appears in the web UI.
        """
        import time

        # Get all registered (non-archived) projects
        projects = db.query(Project).filter(Project.archived == False).all()
        if not projects:
            return

        # Collect ALL session IDs already known to any agent (active or stopped)
        # to avoid creating duplicates for web-app-started agents or re-syncing
        # sessions that have already been tracked.
        known_session_ids = set()
        all_agents_with_session = db.query(Agent).filter(
            Agent.session_id.is_not(None),
        ).all()
        for a in all_agents_with_session:
            known_session_ids.add(a.session_id)

        # Also extract session_ids from currently-executing agents' output files,
        # since web-app agents don't get session_id until execution completes.
        # Track projects with web-app agents that are mid-execution but don't have
        # a session_id yet — skip ALL new detections for those projects to avoid
        # the race window between subprocess start and init event.
        projects_with_pending_webapp = set()
        for agent_id, info in self._active_execs.items():
            agent = db.get(Agent, agent_id)
            if not agent or agent.cli_sync:
                continue
            output_file = info.get("output_file", "")
            if output_file and os.path.isfile(output_file):
                sid = _extract_session_id_from_output(output_file)
                if sid:
                    known_session_ids.add(sid)
                    continue
            # Output file missing or no session_id yet — block this project
            projects_with_pending_webapp.add(agent.project)

        now = time.time()
        agents_to_sync: list[tuple[str, str, str]] = []  # (agent_id, session_id, project_path)

        for proj in projects:
            # Skip projects where a web-app agent is executing but hasn't
            # received its session_id yet — avoids race-condition duplicates.
            if proj.name in projects_with_pending_webapp:
                continue

            session_dir = _session_source_dir(proj.path)
            if not os.path.isdir(session_dir):
                continue

            # Find .jsonl files modified in the last 60 seconds
            try:
                for fname in os.listdir(session_dir):
                    if not fname.endswith(".jsonl"):
                        continue
                    fpath = os.path.join(session_dir, fname)
                    if not os.path.isfile(fpath):
                        continue

                    mtime = os.path.getmtime(fpath)
                    if now - mtime > 60:
                        continue  # Not actively being written

                    session_id = fname.replace(".jsonl", "")
                    if session_id in known_session_ids:
                        continue

                    # New active session found
                    logger.info(
                        "Auto-detected active CLI session %s in project %s",
                        session_id[:12], proj.name,
                    )

                    # Extract agent name and model from session
                    agent_name = "CLI session"
                    detected_model = None
                    try:
                        turns = _parse_session_turns(fpath)
                        for role, content in turns:
                            if role == "user" and content:
                                agent_name = (content or "")[:80]
                                break
                        detected_model = _detect_session_model(fpath)
                    except Exception:
                        turns = []

                    agent = Agent(
                        project=proj.name,
                        name=agent_name,
                        mode=AgentMode.AUTO,
                        status=AgentStatus.SYNCING,
                        model=detected_model,
                        session_id=session_id,
                        cli_sync=True,
                        plan_approved=True,
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
                    known_session_ids.add(session_id)
                    agents_to_sync.append((agent.id, session_id, proj.path))

                    from websocket import emit_agent_update
                    self._emit(emit_agent_update(agent.id, agent.status.value, proj.name))

            except OSError:
                continue

        # Start sync tasks (after commit)
        for aid, sid, ppath in agents_to_sync:
            self.start_session_sync(aid, sid, ppath)

    def _reap_stale_syncing_agents(self, db: Session):
        """Stop SYNCING agents whose session file hasn't been written to recently."""
        import time

        stale_threshold = 1800  # 30 minutes without writes → session is done
        syncing = db.query(Agent).filter(
            Agent.status == AgentStatus.SYNCING,
            Agent.cli_sync == True,
        ).all()

        for agent in syncing:
            if not agent.session_id:
                continue
            proj = db.get(Project, agent.project)
            if not proj:
                continue
            session_dir = _session_source_dir(proj.path)
            fpath = os.path.join(session_dir, f"{agent.session_id}.jsonl")
            try:
                age = time.time() - os.path.getmtime(fpath)
            except OSError:
                age = float("inf")

            if age > stale_threshold:
                logger.info(
                    "Stopping stale syncing agent %s (session file idle %.0fs)",
                    agent.id, age,
                )
                agent.status = AgentStatus.STOPPED
                from websocket import emit_agent_update
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
            _session_source_dir(project_path), f"{session_id}.jsonl"
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

    async def _sync_session_loop(
        self, agent_id: str, session_id: str, project_path: str
    ):
        """Tail a CLI session JSONL and import new turns as they appear.

        Stays in SYNCING until the session JSONL contains a 'result' event
        (written by Claude Code when the session ends) or a new session file
        supersedes this one. Only then transitions to IDLE.
        """
        POLL_INTERVAL = 3  # seconds between checks

        jsonl_path = os.path.join(
            _session_source_dir(project_path), f"{session_id}.jsonl"
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

        # Reconcile: update any existing DB messages whose content grew
        # since they were first imported (e.g. assistant was mid-response).
        db = SessionLocal()
        try:
            role_map = {"user": MessageRole.USER, "assistant": MessageRole.AGENT}
            existing_msgs = db.query(Message).filter(
                Message.agent_id == agent_id,
                Message.role.in_([MessageRole.USER, MessageRole.AGENT]),
            ).order_by(Message.created_at).all()
            updated = 0
            for msg, (role, content) in zip(existing_msgs, initial_turns):
                if role not in role_map:
                    continue
                if len(msg.content) < len(content):
                    msg.content = content
                    msg.completed_at = _utcnow()
                    updated += 1
            if updated:
                db.commit()
                self._emit(emit_new_message(agent_id, "sync", _sync_agent_name, _sync_project))
                logger.info(
                    "Reconciled %d stale messages for agent %s",
                    updated, agent_id,
                )
        finally:
            db.close()

        while True:
            await asyncio.sleep(POLL_INTERVAL)

            try:
                current_size = os.path.getsize(jsonl_path)
            except OSError:
                continue

            if current_size <= last_size:
                continue
            last_size = current_size

            # Parse full file for turns
            turns = _parse_session_turns(jsonl_path)
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
                        self._emit(emit_new_message(agent.id, "sync", _sync_agent_name, _sync_project))
                        last_tail_hash = tail_hash
                        is_generating = False
                        logger.info(
                            "Updated last turn content for agent %s",
                            agent_id,
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

                    from push import send_push_notification
                    send_push_notification(
                        title=_sync_agent_name or f"Agent {agent_id[:8]}",
                        body=f"New message ({_sync_project})" if _sync_project else "New message",
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
                logger.info(
                    "CLI session ended for agent %s — transitioning to IDLE",
                    agent_id,
                )
                db = SessionLocal()
                try:
                    # Final parse to catch any remaining turns
                    turns = _parse_session_turns(jsonl_path)
                    final_new = turns[last_turn_count:]
                    agent = db.get(Agent, agent_id)
                    if agent and agent.status == AgentStatus.SYNCING:
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

                        agent.status = AgentStatus.IDLE
                        sys_msg = Message(
                            agent_id=agent_id,
                            role=MessageRole.SYSTEM,
                            content="CLI session ended — sync complete",
                            status=MessageStatus.COMPLETED,
                        )
                        db.add(sys_msg)
                        if final_new:
                            agent.last_message_preview = (final_new[-1][1] or "")[:200]
                        agent.last_message_at = _utcnow()
                        db.commit()

                        self._emit(emit_agent_update(
                            agent.id, agent.status.value, agent.project
                        ))
                        self._emit(emit_new_message(agent.id, sys_msg.id, _sync_agent_name, _sync_project))

                        from push import send_push_notification
                        send_push_notification(
                            title=f"\u2705 {_sync_agent_name or agent_id[:8]}",
                            body="CLI session ended — sync complete",
                            url=f"/agents/{agent_id}",
                        )
                finally:
                    db.close()
                break

        # Clean up
        self._sync_tasks.pop(agent_id, None)

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
                AgentStatus.PLANNING, AgentStatus.PLAN_REVIEW,
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
                    AgentStatus.EXECUTING, AgentStatus.PLANNING,
                ):
                    project = db.get(Project, agent.project)
                    if project:
                        project_path = self.worker_mgr._get_project_path(
                            project.name
                        )
                        jsonl_path = os.path.join(
                            _session_source_dir(project_path),
                            f"{agent.session_id}.jsonl",
                        )
                        if (
                            os.path.exists(jsonl_path)
                            and not self._session_has_ended(jsonl_path)
                        ):
                            # CLI session is still active — sync it
                            agent.status = AgentStatus.SYNCING
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
                    # CLI session ended (or file missing) — go IDLE
                    agent.status = AgentStatus.IDLE
                    msg = Message(
                        agent_id=agent.id,
                        role=MessageRole.SYSTEM,
                        content="CLI session ended — sync complete",
                        status=MessageStatus.COMPLETED,
                    )
                    db.add(msg)
                    continue

                if agent.status in (AgentStatus.EXECUTING, AgentStatus.PLANNING):
                    # Repair session JSONL if agent was mid-execution
                    if agent.session_id:
                        project = db.get(Project, agent.project)
                        if project:
                            project_path = self.worker_mgr._get_project_path(
                                project.name
                            )
                            repaired = repair_session_jsonl(
                                agent.session_id, project_path
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
