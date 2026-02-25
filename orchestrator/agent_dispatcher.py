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
from session_cache import cache_session, evict_session, repair_session_jsonl, restore_session
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
            self._emit(emit_new_message(agent.id, resp.id))

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
                self._emit(emit_new_message(agent.id, msg.id))

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
                self._emit(emit_new_message(agent.id, msg.id))

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
                self._emit(emit_new_message(agent.id, sys_msg.id))

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

            # Find the oldest pending user message
            pending_msg = (
                db.query(Message)
                .filter(
                    Message.agent_id == agent.id,
                    Message.role == MessageRole.USER,
                    Message.status == MessageStatus.PENDING,
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
                from session_cache import _session_source_dir
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

    # ---- Recovery ----

    def _recover_agents(self):
        """On startup, clear stale state and recover agents."""
        db = SessionLocal()
        try:
            # Recover agents
            alive_statuses = [
                AgentStatus.IDLE, AgentStatus.EXECUTING,
                AgentStatus.PLANNING, AgentStatus.PLAN_REVIEW,
                AgentStatus.STARTING,
            ]
            agents = db.query(Agent).filter(
                Agent.status.in_(alive_statuses)
            ).all()

            for agent in agents:
                if agent.status == AgentStatus.STARTING:
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
        finally:
            db.close()
