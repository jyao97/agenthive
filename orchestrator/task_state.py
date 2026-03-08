"""TaskStateMachine — centralised status transitions with validation and timestamps."""

import logging
from datetime import datetime, timezone

from models import Task, TaskStatus
from task_state_machine import VALID_TRANSITIONS, can_transition
from utils import utcnow as _utcnow

logger = logging.getLogger("orchestrator.task_state")


# States that set completed_at — terminal states plus REJECTED (which records completion time
# even though it allows re-dispatch to PENDING)
COMPLETED_STATES = {TaskStatus.COMPLETE, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.REJECTED}

# States that should set started_at
STARTED_STATES = {TaskStatus.EXECUTING}


class TaskStateMachine:
    """Validate and apply task status transitions with consistent timestamps."""

    @staticmethod
    def transition(task: Task, new_status: TaskStatus, *, reason: str | None = None,
                   set_timestamps: bool = True) -> Task:
        """Validate and apply a task status transition.

        - Validates the transition is allowed (logs warning if not, still applies for backwards compat)
        - Sets started_at when transitioning to EXECUTING (if not already set)
        - Sets completed_at when transitioning to completion states (COMPLETE, FAILED, CANCELLED, TIMEOUT, REJECTED)
        - Returns the task for chaining
        """
        old_status = task.status

        if not can_transition(old_status, new_status):
            logger.warning(
                "Task %s: invalid transition %s -> %s (applying anyway for backwards compat)",
                task.id, old_status.value, new_status.value,
            )

        task.status = new_status

        if set_timestamps:
            if new_status in STARTED_STATES and not task.started_at:
                task.started_at = _utcnow()
            if new_status in COMPLETED_STATES and not task.completed_at:
                task.completed_at = _utcnow()

        return task
