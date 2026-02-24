"""Task Dispatcher — core scheduling loop for CC workers."""

import asyncio
import logging
import shutil
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config import MAX_CONCURRENT_WORKERS, MAX_RETRIES, SKIP_PLAN_FOR_P2
from database import SessionLocal
from log_config import save_worker_log
from models import Priority, Project, Task, TaskStatus
from plan_manager import PlanManager
from worker_manager import WorkerManager

logger = logging.getLogger("orchestrator.dispatcher")

# Zombie detection: kill containers silent for this many seconds
ZOMBIE_SILENCE_THRESHOLD = 120
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
        self.plan_mgr = PlanManager(worker_manager)
        self.running = False
        self._tick_count = 0
        self._paused_disk = False
        self._docker_available = True

    async def run(self):
        """Start the dispatcher loop."""
        self.running = True
        logger.info("Dispatcher started (max_workers=%d)", MAX_CONCURRENT_WORKERS)

        # Recover any tasks left in EXECUTING from a previous crash
        self._recover_stale_tasks()

        while self.running:
            try:
                # Check Docker daemon is reachable
                if not self._check_docker():
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
        try:
            asyncio.ensure_future(coro)
        except Exception:
            pass

    def _tick(self, db: Session):
        """Single iteration of the dispatch loop."""
        self._tick_count += 1

        # 1. Harvest completed workers (both execution and planning)
        self._harvest_completed(db)
        self._harvest_planners(db)

        # 2. Timeout detection
        self._check_timeouts(db)

        # 3. Auto-retry failed tasks
        self._auto_retry(db)

        # 4. Periodic housekeeping (disk check, orphan cleanup)
        if self._tick_count % HOUSEKEEPING_INTERVAL == 0:
            self._check_disk_usage()
            self._cleanup_orphan_containers(db)

        # 5. Start planning for new tasks, then assign approved tasks
        if not self._paused_disk:
            self._start_planning(db)
            self._assign_tasks(db)

        db.commit()

    # ---- Step 1: Harvest ----

    def _harvest_completed(self, db: Session):
        """Check executing tasks whose containers have exited."""
        executing = (
            db.query(Task)
            .filter(Task.status == TaskStatus.EXECUTING)
            .filter(Task.container_id.is_not(None))
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
                task.status = TaskStatus.COMPLETED
                task.result_summary = _extract_summary(logs)
                logger.info("Task %s completed successfully", task.id)
            elif "EXIT_FAILURE" in logs:
                task.status = TaskStatus.FAILED
                task.error_message = _extract_error(logs)
                logger.warning("Task %s failed: %s", task.id, task.error_message)
            else:
                # Container exited without clear signal — treat as failure
                task.status = TaskStatus.FAILED
                task.error_message = "Worker exited without EXIT_SUCCESS or EXIT_FAILURE signal"
                logger.warning("Task %s: worker exited without status signal", task.id)

            # Save worker log to file and emit WebSocket event
            save_worker_log(task.id, logs)
            from websocket import emit_task_update
            self._emit(emit_task_update(task.id, task.status.value, task.project))

            # Clean up container
            if status != "removed":
                self.worker_mgr.stop_worker(task.container_id)

    # ---- Step 2: Timeouts ----

    def _check_timeouts(self, db: Session):
        """Kill workers that have exceeded their timeout."""
        executing = (
            db.query(Task)
            .filter(Task.status.in_([TaskStatus.EXECUTING, TaskStatus.PLANNING]))
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
                # Grab logs before killing
                if task.container_id:
                    task.stream_log = _truncate(
                        self.worker_mgr.get_logs(task.container_id), 50000
                    )
                    self.worker_mgr.stop_worker(task.container_id)
                task.status = TaskStatus.TIMEOUT
                task.error_message = f"Timed out after {int(elapsed)}s"
                task.completed_at = now

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

    # ---- Step 4: Assign ----

    def _assign_tasks(self, db: Session):
        """Assign plan-approved pending tasks to worker containers."""
        # Count currently active containers (executing + planning)
        active_statuses = [TaskStatus.EXECUTING, TaskStatus.PLANNING]
        active_tasks = db.query(Task).filter(Task.status.in_(active_statuses)).all()
        total_active = len(active_tasks)
        project_counts: dict[str, int] = {}
        for t in active_tasks:
            project_counts[t.project] = project_counts.get(t.project, 0) + 1

        if total_active >= MAX_CONCURRENT_WORKERS:
            return

        # Only assign tasks that have been plan-approved (or skipped planning)
        pending = (
            db.query(Task)
            .filter(Task.status == TaskStatus.PENDING)
            .filter(Task.plan_approved == True)  # noqa: E712
            .order_by(Task.priority.asc(), Task.created_at.asc())
            .all()
        )

        for task in pending:
            if total_active >= MAX_CONCURRENT_WORKERS:
                break

            # Check per-project concurrency limit
            project = db.get(Project, task.project)
            if not project:
                task.status = TaskStatus.FAILED
                task.error_message = f"Project '{task.project}' not found"
                continue

            proj_active = project_counts.get(task.project, 0)
            if proj_active >= project.max_concurrent:
                continue

            # Start worker
            try:
                container_id = self.worker_mgr.start_worker(task, project)
                task.container_id = container_id
                task.status = TaskStatus.EXECUTING
                task.started_at = _utcnow()
                total_active += 1
                project_counts[task.project] = proj_active + 1
                logger.info(
                    "Assigned task %s to worker (project: %s, active: %d/%d)",
                    task.id, task.project, total_active, MAX_CONCURRENT_WORKERS,
                )
                from websocket import emit_worker_update
                self._emit(emit_worker_update("created", f"cc-worker-{task.id[:8]}", task.project))
            except Exception:
                logger.exception("Failed to start worker for task %s", task.id)
                task.status = TaskStatus.FAILED
                task.error_message = "Failed to start worker container"
                task.completed_at = _utcnow()

    # ---- Planning ----

    def _start_planning(self, db: Session):
        """Start planning workers for new PENDING tasks that need plans."""
        # Count active containers (executing + planning)
        active_statuses = [TaskStatus.EXECUTING, TaskStatus.PLANNING]
        total_active = db.query(Task).filter(Task.status.in_(active_statuses)).count()
        if total_active >= MAX_CONCURRENT_WORKERS:
            return

        pending = (
            db.query(Task)
            .filter(Task.status == TaskStatus.PENDING)
            .filter(Task.plan_approved == False)  # noqa: E712
            .order_by(Task.priority.asc(), Task.created_at.asc())
            .all()
        )

        for task in pending:
            if total_active >= MAX_CONCURRENT_WORKERS:
                break

            # P2 tasks skip planning if configured
            if SKIP_PLAN_FOR_P2 and task.priority == Priority.P2:
                task.plan_approved = True
                logger.info("Task %s (P2) skipping plan — auto-approved", task.id)
                continue

            project = db.get(Project, task.project)
            if not project:
                task.status = TaskStatus.FAILED
                task.error_message = f"Project '{task.project}' not found"
                continue

            try:
                container_id = self.plan_mgr.start_planning(task, project)
                task.container_id = container_id
                task.status = TaskStatus.PLANNING
                task.started_at = _utcnow()
                total_active += 1
                logger.info("Started planning for task %s", task.id)
            except Exception:
                logger.exception("Failed to start planner for task %s", task.id)
                task.status = TaskStatus.FAILED
                task.error_message = "Failed to start planning container"
                task.completed_at = _utcnow()

    def _harvest_planners(self, db: Session):
        """Check planning tasks whose containers have exited."""
        planning = (
            db.query(Task)
            .filter(Task.status == TaskStatus.PLANNING)
            .filter(Task.container_id.is_not(None))
            .all()
        )
        for task in planning:
            status = self.worker_mgr.get_status(task.container_id)
            if status not in ("exited", "removed"):
                continue

            logs = self.worker_mgr.get_logs(task.container_id)

            if "EXIT_SUCCESS" in logs:
                task.plan = PlanManager.extract_plan(logs)
                task.status = TaskStatus.PLAN_REVIEW
                task.container_id = None
                logger.info("Task %s plan ready for review", task.id)
                from websocket import emit_plan_ready
                self._emit(emit_plan_ready(task.id, task.project))
            else:
                task.plan = "(Planning failed — worker did not produce a plan)"
                task.status = TaskStatus.PLAN_REVIEW
                task.container_id = None
                logger.warning("Task %s planning did not get EXIT_SUCCESS", task.id)

            if status != "removed":
                self.worker_mgr.stop_worker(task.container_id) if task.container_id else None
                # Container ID was already cleared, use the old one from logs check
            # Clean up using the container status we already checked
            try:
                containers = self.worker_mgr.docker_client.containers.list(
                    all=True, filters={"name": f"cc-planner-{task.id}"}
                )
                for c in containers:
                    c.remove(force=True)
            except Exception:
                pass

    # ---- Housekeeping ----

    def _check_docker(self) -> bool:
        """Verify Docker daemon is reachable. Returns False if unavailable."""
        try:
            self.worker_mgr.docker_client.ping()
            if not self._docker_available:
                logger.info("Docker daemon reconnected")
                self._docker_available = True
            return True
        except Exception:
            if self._docker_available:
                logger.error("Docker daemon unavailable — pausing task assignment")
                self._docker_available = False
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
                    # Try to emit alert (async-safe)
                    try:
                        import asyncio
                        from websocket import emit_system_alert
                        asyncio.ensure_future(emit_system_alert(
                            f"Disk usage {fraction*100:.0f}% — new tasks paused", "error"
                        ))
                    except Exception:
                        pass
            else:
                if self._paused_disk:
                    logger.info("Disk usage back to %.1f%% — resuming", fraction * 100)
                    self._paused_disk = False
        except Exception:
            logger.debug("Could not check disk usage")

    def _cleanup_orphan_containers(self, db: Session):
        """Remove cc-worker-* and cc-planner-* containers not tracked in task table."""
        try:
            known_ids = set()
            active = db.query(Task).filter(
                Task.status.in_([TaskStatus.EXECUTING, TaskStatus.PLANNING]),
                Task.container_id.is_not(None),
            ).all()
            for t in active:
                known_ids.add(t.container_id)

            for prefix in ("cc-worker-", "cc-planner-"):
                containers = self.worker_mgr.docker_client.containers.list(
                    all=True, filters={"name": prefix}
                )
                for c in containers:
                    if c.id not in known_ids and c.short_id not in known_ids:
                        # Check it's actually an orphan (not just starting up)
                        if c.status in ("exited", "dead"):
                            logger.info("Removing orphan container %s (%s)", c.name, c.status)
                            c.remove(force=True)
        except Exception:
            logger.debug("Orphan cleanup failed", exc_info=True)

    # ---- Recovery ----

    def _recover_stale_tasks(self):
        """On startup, check for tasks stuck in EXECUTING from a previous crash."""
        db = SessionLocal()
        try:
            stale = (
                db.query(Task)
                .filter(Task.status.in_([TaskStatus.EXECUTING, TaskStatus.PLANNING]))
                .all()
            )
            for task in stale:
                if task.container_id:
                    status = self.worker_mgr.get_status(task.container_id)
                    if status in ("removed", "exited"):
                        logs = self.worker_mgr.get_logs(task.container_id)
                        task.stream_log = _truncate(logs, 50000)
                        if status != "removed":
                            self.worker_mgr.stop_worker(task.container_id)

                task.status = TaskStatus.FAILED
                task.error_message = "Orchestrator restarted while task was executing"
                task.completed_at = _utcnow()
                logger.warning("Recovered stale task %s → FAILED", task.id)

            if stale:
                db.commit()
                logger.info("Recovered %d stale tasks", len(stale))
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
    # Look for EXIT_SUCCESS and grab context around it
    for i, line in enumerate(lines):
        if "EXIT_SUCCESS" in line:
            # Return last few lines before EXIT_SUCCESS as summary
            start = max(0, i - 5)
            return "\n".join(lines[start:i + 1])
    # Fallback: last 10 lines
    return "\n".join(lines[-10:])


def _extract_error(logs: str) -> str:
    """Extract error message from EXIT_FAILURE line."""
    for line in logs.strip().splitlines():
        if "EXIT_FAILURE" in line:
            # Format: EXIT_FAILURE: reason
            idx = line.find("EXIT_FAILURE:")
            if idx >= 0:
                return line[idx + len("EXIT_FAILURE:"):].strip()
            return line.strip()
    return "Unknown error (no EXIT_FAILURE found)"
