"""Tests for task v2 CRUD, state machine, and endpoints."""

import pytest

from models import Task, TaskStatus
from task_state_machine import InvalidTransitionError, can_transition, validate_transition


# ---- State machine unit tests ----

def test_inbox_to_pending():
    """INBOX -> PENDING should be valid."""
    assert can_transition(TaskStatus.INBOX, TaskStatus.PENDING) is True


def test_inbox_to_cancelled():
    """INBOX -> CANCELLED should be valid."""
    assert can_transition(TaskStatus.INBOX, TaskStatus.CANCELLED) is True


def test_inbox_to_executing():
    """INBOX -> EXECUTING should be valid (direct tmux dispatch)."""
    assert can_transition(TaskStatus.INBOX, TaskStatus.EXECUTING) is True


def test_pending_to_executing():
    """PENDING -> EXECUTING should be valid."""
    assert can_transition(TaskStatus.PENDING, TaskStatus.EXECUTING) is True


def test_executing_to_review_invalid():
    """EXECUTING -> REVIEW is no longer valid (REVIEW is a legacy status)."""
    assert can_transition(TaskStatus.EXECUTING, TaskStatus.REVIEW) is False


def test_executing_to_complete():
    """EXECUTING -> COMPLETE should be valid."""
    assert can_transition(TaskStatus.EXECUTING, TaskStatus.COMPLETE) is True


def test_complete_is_terminal():
    """COMPLETE should have no valid outgoing transitions."""
    for status in TaskStatus:
        assert can_transition(TaskStatus.COMPLETE, status) is False


def test_cancelled_is_terminal():
    """CANCELLED should have no valid outgoing transitions."""
    for status in TaskStatus:
        assert can_transition(TaskStatus.CANCELLED, status) is False


def test_rejected_to_pending_invalid():
    """REJECTED -> PENDING is no longer valid (REJECTED is a legacy status)."""
    assert can_transition(TaskStatus.REJECTED, TaskStatus.PENDING) is False


def test_failed_to_pending():
    """FAILED -> PENDING should be valid (retry)."""
    assert can_transition(TaskStatus.FAILED, TaskStatus.PENDING) is True


def test_validate_transition_raises():
    """validate_transition should raise InvalidTransitionError on invalid transition."""
    with pytest.raises(InvalidTransitionError):
        validate_transition(TaskStatus.COMPLETE, TaskStatus.PENDING)


def test_validate_transition_ok():
    """validate_transition should not raise on valid transition."""
    validate_transition(TaskStatus.INBOX, TaskStatus.PENDING)  # Should not raise


# ---- Model tests ----

def test_task_model_defaults(db_session, sample_project):
    """Task should get sensible defaults."""
    task = Task(
        title="Test task",
        project_name=sample_project.name,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    assert len(task.id) == 12
    assert task.status == TaskStatus.INBOX
    assert task.priority == 0
    assert task.attempt_number == 1
    assert task.skip_permissions is True
    assert task.sync_mode is False
    assert task.use_worktree is True
    assert task.created_at is not None


# ---- Endpoint tests ----

@pytest.mark.anyio
async def test_create_task_v2(client, db_engine):
    """POST /api/v2/tasks should create a task."""
    from sqlalchemy.orm import sessionmaker
    from models import Project
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="task-proj", display_name="TP", path="/tmp/tp"))
    db.commit()
    db.close()

    resp = await client.post("/api/v2/tasks", json={
        "title": "Build feature X",
        "description": "Implement the new feature",
        "project_name": "task-proj",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Build feature X"
    assert data["status"] == "INBOX"
    assert data["project_name"] == "task-proj"


@pytest.mark.anyio
async def test_create_task_auto_title_from_description(client, db_engine):
    """Task with empty title should auto-generate from description."""
    from sqlalchemy.orm import sessionmaker
    from models import Project
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="auto-title", display_name="AT", path="/tmp/at"))
    db.commit()
    db.close()

    resp = await client.post("/api/v2/tasks", json={
        "title": "",
        "description": "Fix the broken CSS styling on the sidebar",
        "project_name": "auto-title",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] != ""
    assert "Untitled" not in data["title"]


@pytest.mark.anyio
async def test_create_task_untitled(client):
    """Task with no title and no description should be 'Untitled task'."""
    resp = await client.post("/api/v2/tasks", json={})
    assert resp.status_code == 201
    assert resp.json()["title"] == "Untitled task"


@pytest.mark.anyio
async def test_list_tasks_v2_empty(client):
    """GET /api/v2/tasks should return empty list initially."""
    resp = await client.get("/api/v2/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_get_task_v2_not_found(client):
    """GET /api/v2/tasks/<id> should return 404 for unknown ID."""
    resp = await client.get("/api/v2/tasks/nonexistent1")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_update_task_v2(client, db_engine):
    """PUT /api/v2/tasks/<id> should update editable fields."""
    from sqlalchemy.orm import sessionmaker
    from models import Project
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="upd-proj", display_name="UP", path="/tmp/up"))
    db.commit()
    db.close()

    # Create a task first
    create_resp = await client.post("/api/v2/tasks", json={
        "title": "Original title",
        "project_name": "upd-proj",
    })
    task_id = create_resp.json()["id"]

    # Update it
    resp = await client.put(f"/api/v2/tasks/{task_id}", json={
        "title": "Updated title",
        "priority": 1,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated title"
    assert data["priority"] == 1


@pytest.mark.anyio
async def test_task_counts(client, db_engine):
    """GET /api/v2/tasks/counts should return perspective counts."""
    from sqlalchemy.orm import sessionmaker
    from models import Project
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="cnt-proj", display_name="CP", path="/tmp/cp"))
    db.commit()
    db.close()

    # Create a couple of tasks
    await client.post("/api/v2/tasks", json={"title": "T1", "project_name": "cnt-proj"})
    await client.post("/api/v2/tasks", json={"title": "T2", "project_name": "cnt-proj"})

    resp = await client.get("/api/v2/tasks/counts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["INBOX"] >= 2
    assert "QUEUE" in data
    assert "ACTIVE" in data
    assert "DONE" in data
    assert "weekly_total" in data
