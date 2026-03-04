"""Task lifecycle state machine for v2 tasks."""

from models import TaskStatus


class InvalidTransitionError(Exception):
    """Raised when a task state transition is not allowed."""
    pass


VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.INBOX: {TaskStatus.PLANNING, TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.PLANNING: {TaskStatus.PENDING, TaskStatus.INBOX, TaskStatus.CANCELLED},
    TaskStatus.PENDING: {TaskStatus.EXECUTING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.EXECUTING: {TaskStatus.REVIEW, TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.MERGING, TaskStatus.REJECTED, TaskStatus.CANCELLED},
    TaskStatus.MERGING: {TaskStatus.COMPLETE, TaskStatus.CONFLICT, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.CONFLICT: {TaskStatus.MERGING, TaskStatus.CANCELLED},
    TaskStatus.REJECTED: {TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.FAILED: {TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.TIMEOUT: {TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.COMPLETE: set(),
    TaskStatus.CANCELLED: set(),
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
