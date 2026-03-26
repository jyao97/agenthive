"""Comprehensive tests for Tasks v2 API endpoints, state machine, and model."""

import pytest

from models import Project, Task, TaskStatus
from task_state_machine import VALID_TRANSITIONS, can_transition, validate_transition, InvalidTransitionError


# ---- State machine comprehensive tests ----


def test_all_valid_transitions():
    """Verify every valid transition in VALID_TRANSITIONS returns True."""
    for from_status, targets in VALID_TRANSITIONS.items():
        for to_status in targets:
            assert can_transition(from_status, to_status) is True, (
                f"Expected {from_status.value} -> {to_status.value} to be valid"
            )
            # Also verify validate_transition does not raise
            validate_transition(from_status, to_status)


def test_all_invalid_transitions_from_terminal():
    """COMPLETE and CANCELLED have no outgoing transitions."""
    terminal = [TaskStatus.COMPLETE, TaskStatus.CANCELLED]
    for term in terminal:
        for status in TaskStatus:
            assert can_transition(term, status) is False, (
                f"Terminal state {term.value} should not transition to {status.value}"
            )
            with pytest.raises(InvalidTransitionError):
                validate_transition(term, status)


def test_planning_transitions():
    """PLANNING is a legacy status — only CANCELLED is valid."""
    valid_targets = {TaskStatus.CANCELLED}
    for target in valid_targets:
        assert can_transition(TaskStatus.PLANNING, target) is True

    invalid_targets = set(TaskStatus) - valid_targets
    for target in invalid_targets:
        assert can_transition(TaskStatus.PLANNING, target) is False


def test_merging_transitions():
    """MERGING is a legacy status — only CANCELLED is valid."""
    valid_targets = {TaskStatus.CANCELLED}
    for target in valid_targets:
        assert can_transition(TaskStatus.MERGING, target) is True

    invalid_targets = set(TaskStatus) - valid_targets
    for target in invalid_targets:
        assert can_transition(TaskStatus.MERGING, target) is False


def test_conflict_transitions():
    """CONFLICT is a legacy status — only CANCELLED is valid."""
    valid_targets = {TaskStatus.CANCELLED}
    for target in valid_targets:
        assert can_transition(TaskStatus.CONFLICT, target) is True

    invalid_targets = set(TaskStatus) - valid_targets
    for target in invalid_targets:
        assert can_transition(TaskStatus.CONFLICT, target) is False


def test_timeout_transitions():
    """TIMEOUT -> PENDING, TIMEOUT -> EXECUTING, TIMEOUT -> CANCELLED."""
    valid_targets = {TaskStatus.PENDING, TaskStatus.EXECUTING, TaskStatus.CANCELLED}
    for target in valid_targets:
        assert can_transition(TaskStatus.TIMEOUT, target) is True

    invalid_targets = set(TaskStatus) - valid_targets
    for target in invalid_targets:
        assert can_transition(TaskStatus.TIMEOUT, target) is False


def test_review_transitions():
    """REVIEW is a legacy status — only CANCELLED is valid."""
    valid_targets = {TaskStatus.CANCELLED}
    for target in valid_targets:
        assert can_transition(TaskStatus.REVIEW, target) is True

    invalid_targets = set(TaskStatus) - valid_targets
    for target in invalid_targets:
        assert can_transition(TaskStatus.REVIEW, target) is False


# ---- Task CRUD endpoint tests ----


@pytest.mark.anyio
async def test_create_task_with_all_fields(client, db_engine):
    """POST /api/v2/tasks with priority, model, effort, skip_permissions etc."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="full-proj", display_name="Full", path="/tmp/full"))
    db.commit()
    db.close()

    resp = await client.post("/api/v2/tasks", json={
        "title": "Full field task",
        "description": "A task with all optional fields set",
        "project_name": "full-proj",
        "priority": 1,
        "model": "claude-sonnet-4-6",
        "effort": "high",
        "skip_permissions": False,
        "sync_mode": True,
        "use_worktree": False,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Full field task"
    assert data["description"] == "A task with all optional fields set"
    assert data["project_name"] == "full-proj"
    assert data["priority"] == 1
    assert data["model"] == "claude-sonnet-4-6"
    assert data["effort"] == "high"
    assert data["skip_permissions"] is False
    assert data["sync_mode"] is True
    assert data["use_worktree"] is False
    assert data["status"] == "INBOX"


@pytest.mark.anyio
async def test_create_task_project_validation(client, db_engine):
    """Task with a valid project_name creates successfully."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="nonexistent-project", display_name="NE", path="/tmp/ne"))
    db.commit()
    db.close()

    resp = await client.post("/api/v2/tasks", json={
        "title": "Orphan task",
        "project_name": "nonexistent-project",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Orphan task"
    assert data["project_name"] == "nonexistent-project"
    assert data["status"] == "INBOX"


@pytest.mark.anyio
async def test_get_task_v2_found(client, db_engine):
    """Create a task then GET it by id."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="get-proj", display_name="GP", path="/tmp/gp"))
    db.commit()
    db.close()

    create_resp = await client.post("/api/v2/tasks", json={
        "title": "Task to fetch",
        "project_name": "get-proj",
    })
    assert create_resp.status_code == 201
    task_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v2/tasks/{task_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == task_id
    assert data["title"] == "Task to fetch"
    assert data["project_name"] == "get-proj"
    assert data["status"] == "INBOX"
    # TaskDetailOut includes conversation field
    assert "conversation" in data


@pytest.mark.anyio
async def test_list_tasks_v2_with_data(client, db_engine):
    """Create multiple tasks, list returns all."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="list-proj", display_name="LP", path="/tmp/lp"))
    db.commit()
    db.close()

    await client.post("/api/v2/tasks", json={"title": "Task A", "project_name": "list-proj"})
    await client.post("/api/v2/tasks", json={"title": "Task B", "project_name": "list-proj"})
    await client.post("/api/v2/tasks", json={"title": "Task C", "project_name": "list-proj"})

    resp = await client.get("/api/v2/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 3
    titles = [t["title"] for t in data]
    assert "Task A" in titles
    assert "Task B" in titles
    assert "Task C" in titles


@pytest.mark.anyio
async def test_list_tasks_v2_filter_by_project(client, db_engine):
    """GET /api/v2/tasks?project= filters by project_name."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="proj-alpha", display_name="Alpha", path="/tmp/alpha"))
    db.add(Project(name="proj-beta", display_name="Beta", path="/tmp/beta"))
    db.commit()
    db.close()

    await client.post("/api/v2/tasks", json={"title": "Alpha task", "project_name": "proj-alpha"})
    await client.post("/api/v2/tasks", json={"title": "Beta task", "project_name": "proj-beta"})

    resp = await client.get("/api/v2/tasks?project=proj-alpha")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["project_name"] == "proj-alpha"
    assert data[0]["title"] == "Alpha task"


@pytest.mark.anyio
async def test_list_tasks_v2_filter_by_status(client, db_engine):
    """GET /api/v2/tasks?status= filters by status."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="st-proj", display_name="SP", path="/tmp/sp"))
    # Insert one task as INBOX via API, another directly as COMPLETE via DB
    db.commit()
    db.close()

    # Create INBOX task via API
    await client.post("/api/v2/tasks", json={"title": "Inbox task", "project_name": "st-proj"})

    # Insert COMPLETE task directly in DB
    db2 = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)()
    db2.add(Task(
        title="Done task",
        project_name="st-proj",
        status=TaskStatus.COMPLETE,
    ))
    db2.commit()
    db2.close()

    resp = await client.get("/api/v2/tasks?status=INBOX")
    assert resp.status_code == 200
    data = resp.json()
    titles = [t["title"] for t in data]
    assert "Inbox task" in titles
    assert "Done task" not in titles


@pytest.mark.anyio
async def test_update_task_description(client, db_engine):
    """PUT /api/v2/tasks/{id} with description change."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="desc-proj", display_name="DP", path="/tmp/dp"))
    db.commit()
    db.close()

    create_resp = await client.post("/api/v2/tasks", json={
        "title": "Desc task",
        "project_name": "desc-proj",
    })
    task_id = create_resp.json()["id"]

    resp = await client.put(f"/api/v2/tasks/{task_id}", json={
        "description": "Updated description text",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "Updated description text"
    assert data["title"] == "Desc task"  # title unchanged


@pytest.mark.anyio
async def test_update_task_not_found(client):
    """PUT /api/v2/tasks/<nonexistent> returns 404."""
    resp = await client.put("/api/v2/tasks/doesnotexist", json={
        "title": "Nope",
    })
    assert resp.status_code == 404


# ---- Task counts tests ----


@pytest.mark.anyio
async def test_task_counts_multiple_statuses(client, db_engine):
    """Create tasks in different statuses, verify counts."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="cnt2-proj", display_name="C2", path="/tmp/c2"))
    db.commit()

    # Insert tasks with various statuses directly in DB
    db.add(Task(title="Inbox 1", project_name="cnt2-proj", status=TaskStatus.INBOX))
    db.add(Task(title="Inbox 2", project_name="cnt2-proj", status=TaskStatus.INBOX))
    db.add(Task(title="Pending 1", project_name="cnt2-proj", status=TaskStatus.PENDING))
    db.add(Task(title="Executing 1", project_name="cnt2-proj", status=TaskStatus.EXECUTING))
    db.add(Task(title="Complete 1", project_name="cnt2-proj", status=TaskStatus.COMPLETE))
    db.commit()
    db.close()

    resp = await client.get("/api/v2/tasks/counts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["INBOX"] >= 2
    assert data["QUEUE"] >= 1  # PENDING maps to QUEUE perspective
    assert data["ACTIVE"] >= 1  # EXECUTING maps to ACTIVE perspective
    assert data["DONE"] >= 1  # COMPLETE maps to DONE perspective
    assert "weekly_total" in data


@pytest.mark.anyio
async def test_task_counts_empty(client):
    """No tasks returns zero counts."""
    resp = await client.get("/api/v2/tasks/counts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["INBOX"] == 0
    assert data["QUEUE"] == 0
    assert data["ACTIVE"] == 0
    assert data["DONE"] == 0


# ---- Task cancel tests ----


@pytest.mark.anyio
async def test_cancel_inbox_task(client, db_engine):
    """POST /api/v2/tasks/{id}/cancel from INBOX succeeds."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="canc-proj", display_name="CC", path="/tmp/cc"))
    db.commit()
    db.close()

    create_resp = await client.post("/api/v2/tasks", json={
        "title": "Cancel me",
        "project_name": "canc-proj",
    })
    task_id = create_resp.json()["id"]
    assert create_resp.json()["status"] == "INBOX"

    resp = await client.post(f"/api/v2/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CANCELLED"
    assert data["completed_at"] is not None


@pytest.mark.anyio
async def test_cancel_pending_task(client, db_engine):
    """POST /api/v2/tasks/{id}/cancel from PENDING succeeds."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="canc2-proj", display_name="C2", path="/tmp/c2p"))
    db.commit()

    # Insert task directly in PENDING status
    task = Task(
        title="Pending cancel",
        project_name="canc2-proj",
        status=TaskStatus.PENDING,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CANCELLED"
    assert data["completed_at"] is not None


@pytest.mark.anyio
async def test_cancel_complete_task_fails(client, db_engine):
    """POST /api/v2/tasks/{id}/cancel from COMPLETE returns 409."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()

    task = Task(
        title="Already done",
        status=TaskStatus.COMPLETE,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/cancel")
    assert resp.status_code == 409


# ---- Task reject endpoint removed (REVIEW/REJECTED are legacy statuses) ----


@pytest.mark.anyio
async def test_reject_endpoint_removed(client, db_engine):
    """POST /api/v2/tasks/{id}/reject endpoint no longer exists."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="rej-proj", display_name="RJ", path="/tmp/rj"))
    db.commit()

    task = Task(
        title="Review me",
        project_name="rej-proj",
        status=TaskStatus.REVIEW,
    )
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/reject", json={
        "reason": "Code quality is insufficient",
    })
    # Endpoint removed — expect 404 (Method Not Allowed) or 404 (Not Found)
    assert resp.status_code in (404, 405)


# ---- Task model enum test ----


def test_task_status_enum_all_values():
    """Verify all 12 status values exist in TaskStatus."""
    expected = {
        "INBOX", "PLANNING", "PENDING", "EXECUTING",
        "REVIEW", "MERGING", "CONFLICT", "COMPLETE",
        "REJECTED", "CANCELLED", "FAILED", "TIMEOUT",
    }
    actual = {s.value for s in TaskStatus}
    assert actual == expected
    assert len(TaskStatus) == 12
