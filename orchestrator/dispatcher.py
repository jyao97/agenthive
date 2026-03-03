"""Task Dispatcher — core scheduling loop for CC workers."""

import asyncio
import logging
import shutil
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config import MAX_CONCURRENT_WORKERS, MAX_RETRIES
from database import SessionLocal
from log_config import save_worker_log
from models import Project, Task, TaskStatus
from worker_manager import WorkerManager

logger = logging.getLogger("orchestrator.dispatcher")

# Disk usage threshold (fraction) — pause new tasks above this
DISK_USAGE_THRESHOLD = 0.90
# How often (in ticks, ~2s each) to run housekeeping checks
HOUSEKEEPING_INTERVAL = 30  # ~60 seconds


def _utcnow():
    return datetime.now(timezone.utc)


class TaskDispatcher:
    """Main scheduling loop: harvest results, detect timeouts, assign tasks."""

    def __init__(self, worker_manager: WorkerManager):
        self.worker_mgr = worker_manager
        self.running = False
        self._tick_count = 0
        self._paused_disk = False
        self._claude_available = True

    async def run(self):
        """Start the dispatcher loop."""
        self.running = True
        logger.info("Dispatcher started (max_workers=%d)", MAX_CONCURRENT_WORKERS)

        # Recover any tasks left in EXECUTING from a previous crash
        self._recover_stale_tasks()

        while self.running:
            try:
                # Check Claude CLI is reachable
                if not self._check_claude():
                    await asyncio.sleep(5)
                    continue

                db = SessionLocal()
                try:
                    self._tick(db)
                finally:
                    db.close()
            except Exception:
                logger.exception("Dispatcher tick failed")
            await asyncio.sleep(2)

        logger.info("Dispatcher stopped")

    def stop(self):
        """Signal the dispatcher to stop."""
        self.running = False

    def _emit(self, coro):
        """Fire-and-forget an async event (WebSocket broadcast)."""
        asyncio.ensure_future(coro)

    def _tick(self, db: Session):
        """Single iteration of the dispatch loop."""
        self._tick_count += 1

        # 1. Harvest completed workers
        self._harvest_completed(db)

        # 2. Timeout detection
        self._check_timeouts(db)

        # 3. Auto-retry failed tasks
        self._auto_retry(db)

        # 4. Periodic housekeeping (disk check, orphan cleanup)
        if self._tick_count % HOUSEKEEPING_INTERVAL == 0:
            self._check_disk_usage()
            self._cleanup_orphan_processes(db)

        # 5. Assign pending tasks
        if not self._paused_disk:
            self._assign_tasks(db)

        db.commit()

    # ---- Step 1: Harvest ----

    def _harvest_completed(self, db: Session):
        """Check executing tasks whose processes have exited."""
        executing = (
            db.query(Task)
            .filter(Task.status == TaskStatus.EXECUTING)
            .filter(Task.container_id.is_not(None))  # pid_str stored in container_id column
            .all()
        )
        for task in executing:
            status = self.worker_mgr.get_status(task.container_id)
            if status not in ("exited", "removed"):
                continue

            # Read logs before cleanup
            logs = self.worker_mgr.get_logs(task.container_id)
            task.stream_log = _truncate(logs, 50000)
            task.completed_at = _utcnow()

            if "EXIT_SUCCESS" in logs:
                task.status = TaskStatus.COMPLETE
                task.result_summary = _extract_summary(logs)
                logger.info("Task %s completed successfully", task.id)
            elif "EXIT_FAILURE" in logs:
                task.status = TaskStatus.FAILED
                task.error_message = _extract_error(logs)
                logger.warning("Task %s failed: %s", task.id, task.error_message)
            else:
                task.status = TaskStatus.FAILED
                task.error_message = "Worker exited without EXIT_SUCCESS or EXIT_FAILURE signal"
                logger.warning("Task %s: worker exited without status signal", task.id)

            save_worker_log(task.id, logs)
            from websocket import emit_task_update
            self._emit(emit_task_update(task.id, task.status.value, task.project))

            # Clean up process tracking
            self.worker_mgr._processes.pop(task.container_id, None)

    # ---- Step 2: Timeouts ----

    def _check_timeouts(self, db: Session):
        """Kill workers that have exceeded their timeout."""
        executing = (
            db.query(Task)
            .filter(Task.status == TaskStatus.EXECUTING)
            .filter(Task.started_at.is_not(None))
            .all()
        )
        now = _utcnow()
        for task in executing:
            started = task.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (now - started).total_seconds()
            if elapsed > task.timeout_seconds:
                logger.warning(
                    "Task %s timed out after %ds (limit %ds)",
                    task.id, int(elapsed), task.timeout_seconds,
                )
                if task.container_id:
                    task.stream_log = _truncate(
                        self.worker_mgr.get_logs(task.container_id), 50000
                    )
                    self.worker_mgr.stop_worker(task.container_id)
                task.status = TaskStatus.TIMEOUT
                task.error_message = f"Timed out after {int(elapsed)}s"
                task.completed_at = now
                from websocket import emit_task_update
                self._emit(emit_task_update(task.id, task.status.value, task.project))

    # ---- Step 3: Auto-retry ----

    def _auto_retry(self, db: Session):
        """Re-queue failed/timed out tasks that haven't exceeded retry limit."""
        retriable = (
            db.query(Task)
            .filter(Task.status.in_([TaskStatus.FAILED, TaskStatus.TIMEOUT]))
            .filter(Task.retries < MAX_RETRIES)
            .all()
        )
        for task in retriable:
            task.retries += 1
            task.status = TaskStatus.PENDING
            task.container_id = None
            task.started_at = None
            task.completed_at = None
            prev_error = task.error_message or "unknown"
            task.error_message = None
            logger.info(
                "Task %s re-queued for retry #%d (was: %s)",
                task.id, task.retries, prev_error,
            )
            from websocket import emit_task_update
            self._emit(emit_task_update(task.id, task.status.value, task.project))

    # ---- Step 4: Assign ----

    def _assign_tasks(self, db: Session):
        """Assign pending tasks to worker processes."""
        active_statuses = [TaskStatus.EXECUTING]
        active_tasks = db.query(Task).filter(Task.status.in_(active_statuses)).all()
        total_active = len(active_tasks)
        project_counts: dict[str, int] = {}
        for t in active_tasks:
            project_counts[t.project] = project_counts.get(t.project, 0) + 1

        if total_active >= MAX_CONCURRENT_WORKERS:
            return

        pending = (
            db.query(Task)
            .filter(Task.status == TaskStatus.PENDING)
            .order_by(Task.created_at.asc())
            .all()
        )

        for task in pending:
            if total_active >= MAX_CONCURRENT_WORKERS:
                break

            # Skip v2 tasks (dispatched by agent_dispatcher, not this worker loop)
            if task.project_name and not task.project:
                continue

            project = db.get(Project, task.project)
            if not project:
                task.status = TaskStatus.FAILED
                task.error_message = f"Project '{task.project}' not found"
                from websocket import emit_task_update
                self._emit(emit_task_update(task.id, task.status.value, task.project))
                continue

            proj_active = project_counts.get(task.project, 0)
            if proj_active >= project.max_concurrent:
                continue

            try:
                pid_str = self.worker_mgr.start_worker(task, project)
                task.container_id = pid_str
                task.status = TaskStatus.EXECUTING
                task.started_at = _utcnow()
                total_active += 1
                project_counts[task.project] = proj_active + 1
                logger.info(
                    "Assigned task %s to worker (project: %s, active: %d/%d)",
                    task.id, task.project, total_active, MAX_CONCURRENT_WORKERS,
                )
                from websocket import emit_task_update, emit_worker_update
                self._emit(emit_task_update(task.id, task.status.value, task.project))
                self._emit(emit_worker_update("created", f"claude-worker-{task.id[:8]}", task.project))
            except Exception:
                logger.exception("Failed to start worker for task %s", task.id)
                task.status = TaskStatus.FAILED
                task.error_message = "Failed to start worker process"
                task.completed_at = _utcnow()
                from websocket import emit_task_update
                self._emit(emit_task_update(task.id, task.status.value, task.project))

    # ---- Housekeeping ----

    def _check_claude(self) -> bool:
        """Verify Claude CLI is reachable. Returns False if unavailable."""
        ok = self.worker_mgr.ping()
        if ok:
            if not self._claude_available:
                logger.info("Claude CLI reconnected")
                self._claude_available = True
            return True
        else:
            if self._claude_available:
                logger.error("Claude CLI unavailable — pausing task assignment")
                self._claude_available = False
            return False

    def _check_disk_usage(self):
        """Check disk usage and pause task assignment if above threshold."""
        try:
            usage = shutil.disk_usage("/")
            fraction = usage.used / usage.total
            if fraction > DISK_USAGE_THRESHOLD:
                if not self._paused_disk:
                    logger.warning(
                        "Disk usage %.1f%% exceeds %.0f%% threshold — pausing new tasks",
                        fraction * 100, DISK_USAGE_THRESHOLD * 100,
                    )
                    self._paused_disk = True
                    from websocket import emit_system_alert
                    asyncio.ensure_future(emit_system_alert(
                        f"Disk usage {fraction*100:.0f}% — new tasks paused", "error"
                    ))
            else:
                if self._paused_disk:
                    logger.info("Disk usage back to %.1f%% — resuming", fraction * 100)
                    self._paused_disk = False
        except Exception:
            logger.warning("Could not check disk usage", exc_info=True)

    def _cleanup_orphan_processes(self, db: Session):
        """Remove tracking entries for processes not tracked in task table."""
        try:
            known_pids = set()
            active = db.query(Task).filter(
                Task.status == TaskStatus.EXECUTING,
                Task.container_id.is_not(None),
            ).all()
            for t in active:
                known_pids.add(t.container_id)

            # Clean up exited processes not associated with active tasks
            for pid_str, info in list(self.worker_mgr._processes.items()):
                if pid_str in known_pids:
                    continue
                if info["process"].poll() is not None:
                    logger.info("Removing orphan process tracking: PID %s", pid_str)
                    self.worker_mgr._processes.pop(pid_str, None)
        except Exception:
            logger.warning("Orphan cleanup failed", exc_info=True)

    # ---- Recovery ----

    def _recover_stale_tasks(self):
        """On startup, mark tasks stuck in EXECUTING from a previous crash as FAILED."""
        db = SessionLocal()
        try:
            stale = (
                db.query(Task)
                .filter(Task.status == TaskStatus.EXECUTING)
                .all()
            )
            for task in stale:
                task.status = TaskStatus.FAILED
                task.error_message = "Orchestrator restarted while task was executing"
                task.completed_at = _utcnow()
                task.container_id = None
                logger.warning("Recovered stale task %s → FAILED", task.id)

            if stale:
                db.commit()
                logger.info("Recovered %d stale tasks", len(stale))
                # Emit WebSocket events so frontend reflects recovery
                from websocket import emit_task_update
                for task in stale:
                    proj_name = task.project_name or task.project or ""
                    self._emit(emit_task_update(
                        task.id, task.status.value, proj_name,
                        title=task.title,
                    ))
        finally:
            db.close()


# ---- Helpers ----

def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n... [truncated]"


def _extract_summary(logs: str) -> str:
    """Extract a brief summary from worker output."""
    lines = logs.strip().splitlines()
    for i, line in enumerate(lines):
        if "EXIT_SUCCESS" in line:
            start = max(0, i - 5)
            return "\n".join(lines[start:i + 1])
    return "\n".join(lines[-10:])


def _extract_error(logs: str) -> str:
    """Extract error message from EXIT_FAILURE line."""
    for line in logs.strip().splitlines():
        if "EXIT_FAILURE" in line:
            idx = line.find("EXIT_FAILURE:")
            if idx >= 0:
                return line[idx + len("EXIT_FAILURE:"):].strip()
            return line.strip()
    return "Unknown error (no EXIT_FAILURE found)"
