"""Task lifecycle state machine for v2 tasks."""

from models import TaskStatus


class InvalidTransitionError(Exception):
    """Raised when a task state transition is not allowed."""
    pass


VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.INBOX: {TaskStatus.PENDING, TaskStatus.EXECUTING, TaskStatus.COMPLETE, TaskStatus.CANCELLED},
    TaskStatus.PENDING: {TaskStatus.EXECUTING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.EXECUTING: {TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED, TaskStatus.INBOX},
    TaskStatus.COMPLETE: {TaskStatus.EXECUTING, TaskStatus.INBOX},
    TaskStatus.CANCELLED: set(),
    TaskStatus.FAILED: {TaskStatus.PENDING, TaskStatus.EXECUTING, TaskStatus.CANCELLED},
    TaskStatus.TIMEOUT: {TaskStatus.PENDING, TaskStatus.EXECUTING, TaskStatus.CANCELLED},
    # Legacy statuses kept for DB compatibility — only allow cancel for cleanup
    TaskStatus.PLANNING: {TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.CANCELLED},
    TaskStatus.MERGING: {TaskStatus.CANCELLED},
    TaskStatus.CONFLICT: {TaskStatus.CANCELLED},
    TaskStatus.REJECTED: {TaskStatus.CANCELLED},
}

TERMINAL_STATES = {TaskStatus.COMPLETE, TaskStatus.CANCELLED}


def can_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """Check if a transition is allowed."""
    return to_status in VALID_TRANSITIONS.get(from_status, set())


def validate_transition(from_status: TaskStatus, to_status: TaskStatus) -> None:
    """Raise InvalidTransitionError if the transition is invalid."""
    if not can_transition(from_status, to_status):
        raise InvalidTransitionError(
            f"Invalid task transition: {from_status.value} -> {to_status.value}"
        )
