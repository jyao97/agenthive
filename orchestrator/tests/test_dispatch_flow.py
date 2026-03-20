"""Tests for the task dispatch flow: creation → dispatch → agent → harvest.

Covers:
- Dispatch endpoint (API level)
- _dispatch_pending_tasks (task → agent creation)
- _create_task_agent (agent + message creation)
- _build_task_prompt (prompt assembly)
- _harvest_task_completions (agent done → task review/failed)
- Concurrency controls (per-project + global)
- Auto-dispatch on creation
- Scheduled task dispatch
- Retry flow (rejected/failed → re-dispatch)
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models import (
    Agent,
    AgentMode,
    AgentStatus,
    Message,
    MessageRole,
    MessageStatus,
    Project,
    Task,
    TaskStatus,
)
from task_state_machine import InvalidTransitionError, can_transition, validate_transition
from worker_manager import WorkerManager


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def proj(db_session):
    """Insert a project for dispatch tests."""
    p = Project(name="dispatch-proj", display_name="DP", path="/tmp/dispatch-proj", max_concurrent=2)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def proj2(db_session):
    """Second project for cross-project tests."""
    p = Project(name="dispatch-proj2", display_name="DP2", path="/tmp/dispatch-proj2", max_concurrent=1)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def pending_task(db_session, proj):
    """A PENDING task ready for dispatch."""
    t = Task(title="Build feature A", description="Build the feature", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


@pytest.fixture()
def inbox_task(db_session, proj):
    """An INBOX task not yet dispatched."""
    t = Task(title="Bug fix B", description="Fix the bug", project_name=proj.name, status=TaskStatus.INBOX)
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


@pytest.fixture()
def dispatcher(db_session):
    """Create an AgentDispatcher with a mocked WorkerManager."""
    from agent_dispatcher import AgentDispatcher
    mock_wm = MagicMock(spec=WorkerManager)
    d = AgentDispatcher(mock_wm)
    # Suppress websocket emits in tests
    d._emit = MagicMock()
    return d


# ---------------------------------------------------------------------------
# 1. Dispatch endpoint (POST /api/v2/tasks/{id}/dispatch)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_dispatch_endpoint_inbox_to_pending(client, db_engine):
    """Dispatch should move INBOX task to PENDING."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="ep-proj", display_name="EP", path="/tmp/ep"))
    db.commit()

    resp = await client.post("/api/v2/tasks", json={
        "title": "Endpoint test task",
        "project_name": "ep-proj",
    })
    task_id = resp.json()["id"]
    assert resp.json()["status"] == "INBOX"

    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING"
    db.close()


@pytest.mark.anyio
async def test_dispatch_endpoint_requires_project(client):
    """Dispatch without project_name should return 400."""
    resp = await client.post("/api/v2/tasks", json={"title": "No project"})
    task_id = resp.json()["id"]

    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_dispatch_endpoint_requires_title(client, db_engine):
    """Dispatch without title should return 400."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="notitle", display_name="NT", path="/tmp/nt"))
    db.commit()

    # Create task with project but empty title → auto-generates "Untitled task"
    resp = await client.post("/api/v2/tasks", json={"project_name": "notitle"})
    task_id = resp.json()["id"]
    # Title should be auto-generated, so dispatch should work
    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    # "Untitled task" is still a title, so this should succeed
    assert resp.status_code == 200
    db.close()


@pytest.mark.anyio
async def test_dispatch_from_complete_invalid(client, db_engine):
    """Dispatch from COMPLETE state should return 409."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="term-proj", display_name="TP", path="/tmp/tp2"))
    t = Task(title="Done task", project_name="term-proj", status=TaskStatus.COMPLETE)
    db.add(t)
    db.commit()
    task_id = t.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_dispatch_from_executing_invalid(client, db_engine):
    """Dispatch from EXECUTING state should return 409."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="exec-proj", display_name="EX", path="/tmp/ex"))
    t = Task(title="Running task", project_name="exec-proj", status=TaskStatus.EXECUTING)
    db.add(t)
    db.commit()
    task_id = t.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_dispatch_retry_from_rejected(client, db_engine):
    """Dispatch from REJECTED should increment attempt_number and clear fields."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="rej-proj", display_name="RJ", path="/tmp/rj"))
    t = Task(
        title="Rejected task",
        project_name="rej-proj",
        status=TaskStatus.REJECTED,
        attempt_number=1,
        agent_summary="Previous attempt summary",
        agent_id=None,
        started_at=_utcnow(),
        completed_at=_utcnow(),
    )
    db.add(t)
    db.commit()
    task_id = t.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["attempt_number"] == 2
    # retry_context is only in TaskDetailOut, not in dispatch response (TaskOut)
    assert data["agent_summary"] is None
    assert data["started_at"] is None
    assert data["completed_at"] is None


@pytest.mark.anyio
async def test_dispatch_retry_from_failed(client, db_engine):
    """Dispatch from FAILED should increment attempt_number."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="fail-proj", display_name="FP", path="/tmp/fp"))
    t = Task(
        title="Failed task",
        project_name="fail-proj",
        status=TaskStatus.FAILED,
        attempt_number=2,
        agent_summary="Failed because of X",
    )
    db.add(t)
    db.commit()
    task_id = t.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["attempt_number"] == 3


@pytest.mark.anyio
async def test_dispatch_retry_from_timeout(client, db_engine):
    """Dispatch from TIMEOUT should work (retry)."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="to-proj", display_name="TO", path="/tmp/to"))
    t = Task(title="Timed out task", project_name="to-proj", status=TaskStatus.TIMEOUT)
    db.add(t)
    db.commit()
    task_id = t.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING"


@pytest.mark.anyio
async def test_dispatch_nonexistent_task(client):
    """Dispatch for a non-existent task should return 404."""
    resp = await client.post("/api/v2/tasks/doesnotexist/dispatch")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. Auto-dispatch on creation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_auto_dispatch_creates_pending(client, db_engine):
    """Task with auto_dispatch=true should start as PENDING."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="auto-proj", display_name="AP", path="/tmp/ap"))
    db.commit()
    db.close()

    resp = await client.post("/api/v2/tasks", json={
        "title": "Auto dispatch task",
        "project_name": "auto-proj",
        "auto_dispatch": True,
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "PENDING"


@pytest.mark.anyio
async def test_auto_dispatch_without_project_stays_inbox(client):
    """Auto-dispatch without project_name should remain INBOX."""
    resp = await client.post("/api/v2/tasks", json={
        "title": "No project auto",
        "auto_dispatch": True,
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "INBOX"


@pytest.mark.anyio
async def test_auto_dispatch_invalid_project(client):
    """Auto-dispatch with non-existent project should return 400."""
    resp = await client.post("/api/v2/tasks", json={
        "title": "Bad project",
        "project_name": "nonexistent-project",
        "auto_dispatch": True,
    })
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 3. _dispatch_pending_tasks (unit tests)
# ---------------------------------------------------------------------------

def test_dispatch_picks_up_pending_tasks(db_session, proj, dispatcher):
    """_dispatch_pending_tasks should pick up PENDING tasks and create agents."""
    t = Task(title="Test dispatch", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    # Create a real agent so FK constraint is satisfied
    agent = Agent(id="aabbccdd1122", project=proj.name, name="Test Agent", status=AgentStatus.IDLE)
    db_session.add(agent)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent', return_value="aabbccdd1122"):
        dispatcher._dispatch_pending_tasks(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.EXECUTING
    assert t.agent_id == "aabbccdd1122"
    assert t.started_at is not None


def test_dispatch_skips_missing_project(db_session, dispatcher):
    """Tasks with a NULL project_name should be skipped (no matching project)."""
    t = Task(title="Orphan task", project_name=None, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent') as mock_create:
        dispatcher._dispatch_pending_tasks(db_session)
        mock_create.assert_not_called()

    db_session.refresh(t)
    assert t.status == TaskStatus.PENDING  # stays pending


def test_dispatch_respects_project_concurrency(db_session, proj, dispatcher):
    """Tasks should not be dispatched when project is at capacity."""
    # Fill up capacity (max_concurrent=2)
    for i in range(2):
        db_session.add(Agent(
            id=f"exec{i:010d}",
            project=proj.name,
            name=f"Agent {i}",
            status=AgentStatus.EXECUTING,
        ))
    db_session.commit()

    t = Task(title="Blocked task", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent') as mock_create:
        dispatcher._dispatch_pending_tasks(db_session)
        mock_create.assert_not_called()

    db_session.refresh(t)
    assert t.status == TaskStatus.PENDING


def test_dispatch_starting_agents_count_toward_capacity(db_session, proj, dispatcher):
    """STARTING agents should count toward project capacity."""
    for i in range(2):
        db_session.add(Agent(
            id=f"start{i:09d}",
            project=proj.name,
            name=f"Starting Agent {i}",
            status=AgentStatus.STARTING,
        ))
    db_session.commit()

    t = Task(title="Blocked by starting", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent') as mock_create:
        dispatcher._dispatch_pending_tasks(db_session)
        mock_create.assert_not_called()

    db_session.refresh(t)
    assert t.status == TaskStatus.PENDING


def test_dispatch_idle_agents_dont_count(db_session, proj, dispatcher):
    """IDLE/STOPPED agents should not count toward capacity."""
    db_session.add(Agent(id="idle00000001", project=proj.name, name="Idle", status=AgentStatus.IDLE))
    db_session.add(Agent(id="stop00000001", project=proj.name, name="Stopped", status=AgentStatus.STOPPED))
    db_session.add(Agent(id="new000000001", project=proj.name, name="New", status=AgentStatus.IDLE))
    db_session.commit()

    t = Task(title="Should dispatch", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent', return_value="new000000001"):
        dispatcher._dispatch_pending_tasks(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.EXECUTING


def test_dispatch_priority_ordering(db_session, proj, dispatcher):
    """High priority tasks should be dispatched before normal priority."""
    # Pre-create agents so FK constraint is satisfied
    db_session.add(Agent(id="agent_000001", project=proj.name, name="A1", status=AgentStatus.IDLE))
    db_session.add(Agent(id="agent_000002", project=proj.name, name="A2", status=AgentStatus.IDLE))
    db_session.commit()

    t_normal = Task(title="Normal task", project_name=proj.name, status=TaskStatus.PENDING, priority=0)
    t_high = Task(title="High priority task", project_name=proj.name, status=TaskStatus.PENDING, priority=1)
    db_session.add(t_normal)
    db_session.add(t_high)
    db_session.commit()

    dispatched_order = []
    agent_ids = iter(["agent_000001", "agent_000002"])

    def mock_create(db, task, proj_obj):
        dispatched_order.append(task.title)
        return next(agent_ids)

    with patch.object(dispatcher, '_create_task_agent', side_effect=mock_create):
        dispatcher._dispatch_pending_tasks(db_session)

    assert dispatched_order[0] == "High priority task"
    assert dispatched_order[1] == "Normal task"


def test_dispatch_scheduled_future_not_dispatched(db_session, proj, dispatcher):
    """Tasks scheduled in the future should not be dispatched."""
    future = _utcnow() + timedelta(hours=1)
    t = Task(title="Future task", project_name=proj.name, status=TaskStatus.PENDING, scheduled_at=future)
    db_session.add(t)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent') as mock_create:
        dispatcher._dispatch_pending_tasks(db_session)
        mock_create.assert_not_called()


def test_dispatch_scheduled_past_dispatched(db_session, proj, dispatcher):
    """Tasks scheduled in the past should be dispatched."""
    past = _utcnow() - timedelta(hours=1)
    db_session.add(Agent(id="sched0000001", project=proj.name, name="Sched", status=AgentStatus.IDLE))
    db_session.commit()

    t = Task(title="Past scheduled", project_name=proj.name, status=TaskStatus.PENDING, scheduled_at=past)
    db_session.add(t)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent', return_value="sched0000001"):
        dispatcher._dispatch_pending_tasks(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.EXECUTING


def test_dispatch_limit_5_per_tick(db_session, proj, dispatcher):
    """At most 5 tasks should be dispatched per tick."""
    # Increase project capacity to not be a bottleneck
    proj.max_concurrent = 10
    db_session.commit()

    # Pre-create agents for FK
    for i in range(8):
        db_session.add(Agent(id=f"lim{i:09d}", project=proj.name, name=f"A{i}", status=AgentStatus.IDLE))
    db_session.commit()

    for i in range(8):
        db_session.add(Task(
            title=f"Task {i}", project_name=proj.name, status=TaskStatus.PENDING,
        ))
    db_session.commit()

    call_count = 0

    def mock_create(db, task, proj_obj):
        nonlocal call_count
        call_count += 1
        return f"lim{call_count - 1:09d}"

    with patch.object(dispatcher, '_create_task_agent', side_effect=mock_create):
        dispatcher._dispatch_pending_tasks(db_session)

    assert call_count == 5


def test_dispatch_handles_create_agent_failure(db_session, proj, dispatcher):
    """If _create_task_agent fails, task should remain PENDING."""
    t = Task(title="Fail create", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent', side_effect=Exception("agent creation failed")):
        dispatcher._dispatch_pending_tasks(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.PENDING


def test_dispatch_create_returns_none(db_session, proj, dispatcher):
    """If _create_task_agent returns None, task stays PENDING."""
    t = Task(title="Null agent", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent', return_value=None):
        dispatcher._dispatch_pending_tasks(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.PENDING


def test_dispatch_cross_project_independence(db_session, proj, proj2, dispatcher):
    """Projects should have independent concurrency limits."""
    # Fill proj2 (max_concurrent=1)
    db_session.add(Agent(id="p2exec000001", project=proj2.name, name="P2 Agent", status=AgentStatus.EXECUTING))
    # Pre-create agent for proj1 dispatch
    db_session.add(Agent(id="p1new0000001", project=proj.name, name="P1 New", status=AgentStatus.IDLE))
    db_session.commit()

    # Tasks for both projects
    t1 = Task(title="Proj1 task", project_name=proj.name, status=TaskStatus.PENDING)
    t2 = Task(title="Proj2 task", project_name=proj2.name, status=TaskStatus.PENDING)
    db_session.add_all([t1, t2])
    db_session.commit()

    dispatched = []

    def mock_create(db, task, proj_obj):
        dispatched.append(task.title)
        return "p1new0000001"

    with patch.object(dispatcher, '_create_task_agent', side_effect=mock_create):
        dispatcher._dispatch_pending_tasks(db_session)

    # proj1 should dispatch, proj2 should not (at capacity)
    assert "Proj1 task" in dispatched
    assert "Proj2 task" not in dispatched


# ---------------------------------------------------------------------------
# 4. _create_task_agent (unit tests)
# ---------------------------------------------------------------------------

def test_create_task_agent_produces_agent_and_message(db_session, proj, dispatcher):
    """_create_task_agent should create an Agent(IDLE) + Message(PENDING)."""
    t = Task(title="Agent creation test", description="Do something", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    agent_id = dispatcher._create_task_agent(db_session, t, proj)
    assert agent_id is not None
    assert len(agent_id) == 12

    # Verify agent record
    agent = db_session.get(Agent, agent_id)
    assert agent is not None
    assert agent.project == proj.name
    assert agent.status == AgentStatus.IDLE
    assert agent.mode == AgentMode.AUTO
    assert agent.task_id == t.id
    assert agent.name.startswith("Task: ")
    assert agent.effort == "high"  # default when task.effort is None

    # Verify pending message
    msg = db_session.query(Message).filter(Message.agent_id == agent_id).first()
    assert msg is not None
    assert msg.role == MessageRole.USER
    assert msg.status == MessageStatus.PENDING
    assert msg.source == "task"
    assert "Agent creation test" in msg.content


def test_create_task_agent_worktree(db_session, proj, dispatcher):
    """Worktree task should set worktree and branch on the task."""
    t = Task(title="Worktree task", project_name=proj.name, status=TaskStatus.PENDING, use_worktree=True)
    db_session.add(t)
    db_session.commit()

    agent_id = dispatcher._create_task_agent(db_session, t, proj)
    assert agent_id is not None

    agent = db_session.get(Agent, agent_id)
    assert agent.worktree is not None

    db_session.refresh(t)
    assert t.worktree_name is not None
    assert t.branch_name is not None
    assert t.branch_name.startswith(f"task/{t.id}/")


def test_create_task_agent_no_worktree(db_session, proj, dispatcher):
    """Non-worktree task should not set worktree on agent."""
    t = Task(title="No worktree", project_name=proj.name, status=TaskStatus.PENDING, use_worktree=False)
    db_session.add(t)
    db_session.commit()

    # Mock git rev-parse to avoid needing real repo
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="abcdef1234567890\n")
        agent_id = dispatcher._create_task_agent(db_session, t, proj)

    agent = db_session.get(Agent, agent_id)
    assert agent.worktree is None


def test_create_task_agent_uses_task_model(db_session, proj, dispatcher):
    """Agent should use task-level model override when present."""
    t = Task(title="Model test", project_name=proj.name, status=TaskStatus.PENDING, model="claude-sonnet-4-6")
    db_session.add(t)
    db_session.commit()

    agent_id = dispatcher._create_task_agent(db_session, t, proj)
    agent = db_session.get(Agent, agent_id)
    assert agent.model == "claude-sonnet-4-6"


def test_create_task_agent_falls_back_to_project_model(db_session, proj, dispatcher):
    """Agent should use project default_model when task.model is None."""
    t = Task(title="Default model", project_name=proj.name, status=TaskStatus.PENDING)
    db_session.add(t)
    db_session.commit()

    agent_id = dispatcher._create_task_agent(db_session, t, proj)
    agent = db_session.get(Agent, agent_id)
    assert agent.model == proj.default_model


def test_create_task_agent_unique_id(db_session, proj, dispatcher):
    """Multiple agent creations should produce unique IDs."""
    ids = set()
    for i in range(5):
        t = Task(title=f"Unique {i}", project_name=proj.name, status=TaskStatus.PENDING)
        db_session.add(t)
        db_session.commit()
        agent_id = dispatcher._create_task_agent(db_session, t, proj)
        assert agent_id is not None
        ids.add(agent_id)
    assert len(ids) == 5


# ---------------------------------------------------------------------------
# 5. _build_task_prompt (unit tests)
# ---------------------------------------------------------------------------

def test_build_prompt_basic(dispatcher):
    """Basic prompt should include title and guidelines."""
    t = Task(title="Simple task", attempt_number=1)
    prompt, insights = dispatcher._build_task_prompt(t)
    assert "# Task: Simple task" in prompt
    assert "## Guidelines" in prompt
    assert "Work autonomously" in prompt
    assert "PROGRESS.md" in prompt
    assert insights == []


def test_build_prompt_with_description(dispatcher):
    """Prompt should include description when present."""
    t = Task(title="Desc task", description="This is the detailed description", attempt_number=1)
    prompt, _insights = dispatcher._build_task_prompt(t)
    assert "This is the detailed description" in prompt


def test_build_prompt_retry_context(dispatcher):
    """Retry prompt should include previous attempt context."""
    t = Task(
        title="Retry task",
        attempt_number=2,
        retry_context="Previous attempt failed because of timeout",
    )
    prompt, _insights = dispatcher._build_task_prompt(t)
    assert "Previous Attempt Context" in prompt
    assert "attempt #2" in prompt
    assert "Previous attempt failed because of timeout" in prompt
    assert "Redo Context" in prompt


def test_build_prompt_rejection_reason(dispatcher):
    """Prompt should include rejection reason when present."""
    t = Task(
        title="Rejected task",
        attempt_number=1,
        rejection_reason="The CSS was not responsive",
    )
    prompt, _insights = dispatcher._build_task_prompt(t)
    assert "Rejection Reason" in prompt
    assert "The CSS was not responsive" in prompt


def test_build_prompt_no_redo_on_first_attempt(dispatcher):
    """First attempt should not include redo context."""
    t = Task(title="First attempt", attempt_number=1)
    prompt, _insights = dispatcher._build_task_prompt(t)
    assert "Redo Context" not in prompt
    assert "Previous Attempt" not in prompt


# ---------------------------------------------------------------------------
# 6. _harvest_task_completions (unit tests)
# ---------------------------------------------------------------------------

def test_harvest_agent_idle_no_pending_moves_to_review(db_session, proj, dispatcher):
    """Agent IDLE with no pending messages → task should move to REVIEW."""
    agent = Agent(id="harv00000001", project=proj.name, name="Done Agent", status=AgentStatus.IDLE)
    db_session.add(agent)
    db_session.flush()

    t = Task(title="Review me", project_name=proj.name, status=TaskStatus.EXECUTING, agent_id=agent.id)
    db_session.add(t)
    db_session.flush()

    # Add a completed agent message (the "output")
    msg = Message(agent_id=agent.id, role=MessageRole.AGENT, content="Task completed successfully", status=MessageStatus.COMPLETED)
    db_session.add(msg)
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.REVIEW
    assert t.agent_summary == "Task completed successfully"


def test_harvest_agent_idle_with_pending_stays_executing(db_session, proj, dispatcher):
    """Agent IDLE with pending messages should not trigger harvest."""
    agent = Agent(id="pend00000001", project=proj.name, name="Waiting Agent", status=AgentStatus.IDLE)
    db_session.add(agent)
    db_session.flush()

    t = Task(title="Still executing", project_name=proj.name, status=TaskStatus.EXECUTING, agent_id=agent.id)
    db_session.add(t)
    db_session.flush()

    # Pending message — agent hasn't started processing yet
    msg = Message(agent_id=agent.id, role=MessageRole.USER, content="Do this", status=MessageStatus.PENDING)
    db_session.add(msg)
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.EXECUTING  # stays


def test_harvest_agent_stopped_with_output_to_review(db_session, proj, dispatcher):
    """Agent STOPPED with output → task to REVIEW."""
    agent = Agent(id="stpd00000001", project=proj.name, name="Stopped Agent", status=AgentStatus.STOPPED)
    db_session.add(agent)
    db_session.flush()

    t = Task(title="Stopped task", project_name=proj.name, status=TaskStatus.EXECUTING, agent_id=agent.id)
    db_session.add(t)
    db_session.flush()

    msg = Message(agent_id=agent.id, role=MessageRole.AGENT, content="Done before stop", status=MessageStatus.COMPLETED)
    db_session.add(msg)
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.REVIEW


def test_harvest_agent_stopped_no_output_to_failed(db_session, proj, dispatcher):
    """Agent STOPPED without any agent output → task FAILED."""
    agent = Agent(id="noout0000001", project=proj.name, name="Silent Agent", status=AgentStatus.STOPPED)
    db_session.add(agent)
    db_session.flush()

    t = Task(title="No output task", project_name=proj.name, status=TaskStatus.EXECUTING, agent_id=agent.id)
    db_session.add(t)
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.FAILED
    assert t.completed_at is not None
    assert "without producing output" in (t.error_message or "")


def test_harvest_agent_deleted_fails_task(db_session, proj, dispatcher):
    """Missing agent → task should be FAILED."""
    # Create agent, task referencing it, then delete via FK-disabled raw SQL
    agent = Agent(id="ghost0000001", project=proj.name, name="Ghost", status=AgentStatus.IDLE)
    db_session.add(agent)
    db_session.flush()

    t = Task(title="Ghost agent task", project_name=proj.name, status=TaskStatus.EXECUTING, agent_id="ghost0000001")
    db_session.add(t)
    db_session.commit()

    # Temporarily disable FK to delete the agent while task still references it
    from sqlalchemy import text
    db_session.execute(text("PRAGMA foreign_keys=OFF"))
    db_session.execute(text("DELETE FROM agents WHERE id = 'ghost0000001'"))
    db_session.execute(text("PRAGMA foreign_keys=ON"))
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.FAILED
    assert t.completed_at is not None


def test_harvest_agent_error_fails_task(db_session, proj, dispatcher):
    """Agent in ERROR state → task should be FAILED."""
    agent = Agent(id="errr00000001", project=proj.name, name="Error Agent", status=AgentStatus.ERROR)
    db_session.add(agent)
    db_session.flush()

    t = Task(title="Error task", project_name=proj.name, status=TaskStatus.EXECUTING, agent_id=agent.id)
    db_session.add(t)
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.FAILED
    assert t.completed_at is not None


def test_harvest_agent_executing_no_change(db_session, proj, dispatcher):
    """Agent still EXECUTING → task should stay EXECUTING."""
    agent = Agent(id="busy00000001", project=proj.name, name="Busy Agent", status=AgentStatus.EXECUTING)
    db_session.add(agent)
    db_session.flush()

    t = Task(title="Busy task", project_name=proj.name, status=TaskStatus.EXECUTING, agent_id=agent.id)
    db_session.add(t)
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.EXECUTING


def test_harvest_summary_truncated_to_2000(db_session, proj, dispatcher):
    """Agent summary should be truncated to 2000 chars."""
    agent = Agent(id="long00000001", project=proj.name, name="Long Agent", status=AgentStatus.STOPPED)
    db_session.add(agent)
    db_session.flush()

    t = Task(title="Long summary", project_name=proj.name, status=TaskStatus.EXECUTING, agent_id=agent.id)
    db_session.add(t)
    db_session.flush()

    long_content = "x" * 5000
    msg = Message(agent_id=agent.id, role=MessageRole.AGENT, content=long_content, status=MessageStatus.COMPLETED)
    db_session.add(msg)
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.REVIEW
    assert len(t.agent_summary) == 2000


def test_harvest_stale_merging_fails(db_session, proj, dispatcher):
    """Stale MERGING tasks should be failed by the harvester."""
    t = Task(title="Stale merge", project_name=proj.name, status=TaskStatus.MERGING)
    db_session.add(t)
    db_session.commit()

    dispatcher._harvest_task_completions(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.FAILED
    assert "stale" in (t.error_message or "").lower()


# ---------------------------------------------------------------------------
# 7. _check_scheduled_tasks (unit tests)
# ---------------------------------------------------------------------------

def test_scheduled_inbox_clears_schedule(db_session, proj, dispatcher):
    """INBOX task with due scheduled_at should clear scheduled_at."""
    past = _utcnow() - timedelta(minutes=5)
    t = Task(title="Scheduled inbox", project_name=proj.name, status=TaskStatus.INBOX, scheduled_at=past)
    db_session.add(t)
    db_session.commit()

    # Patch push to avoid SessionLocal hitting a different in-memory DB
    with patch("push.is_notification_enabled", return_value=False):
        dispatcher._check_scheduled_tasks(db_session)
        db_session.commit()  # _check_scheduled_tasks relies on _tick's commit

    db_session.refresh(t)
    assert t.scheduled_at is None
    assert t.status == TaskStatus.INBOX  # stays INBOX, just notifies


def test_scheduled_planning_moves_to_pending(db_session, proj, dispatcher):
    """PLANNING task with due scheduled_at and project should move to PENDING."""
    past = _utcnow() - timedelta(minutes=5)
    t = Task(title="Scheduled planning", project_name=proj.name, status=TaskStatus.PLANNING, scheduled_at=past)
    db_session.add(t)
    db_session.commit()

    with patch("push.is_notification_enabled", return_value=False):
        dispatcher._check_scheduled_tasks(db_session)
        db_session.commit()  # _check_scheduled_tasks relies on _tick's commit

    db_session.refresh(t)
    assert t.status == TaskStatus.PENDING
    assert t.scheduled_at is None


def test_scheduled_future_not_triggered(db_session, proj, dispatcher):
    """Tasks scheduled in the future should not be triggered."""
    future = _utcnow() + timedelta(hours=1)
    t = Task(title="Future scheduled", project_name=proj.name, status=TaskStatus.PLANNING, scheduled_at=future)
    db_session.add(t)
    db_session.commit()

    dispatcher._check_scheduled_tasks(db_session)

    db_session.refresh(t)
    assert t.status == TaskStatus.PLANNING  # unchanged
    assert t.scheduled_at is not None


# ---------------------------------------------------------------------------
# 8. Full dispatch flow integration (API-level)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_full_flow_create_dispatch_verify(client, db_engine):
    """Create → dispatch → verify task is PENDING ready for agent pickup."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="flow-proj", display_name="FP", path="/tmp/fp2"))
    db.commit()

    # Create
    resp = await client.post("/api/v2/tasks", json={
        "title": "Full flow test",
        "description": "Test the complete flow",
        "project_name": "flow-proj",
        "priority": 1,
    })
    assert resp.status_code == 201
    task = resp.json()
    assert task["status"] == "INBOX"
    task_id = task["id"]

    # Dispatch
    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 200
    task = resp.json()
    assert task["status"] == "PENDING"
    assert task["priority"] == 1
    assert task["project_name"] == "flow-proj"

    # Verify via list endpoint
    resp = await client.get("/api/v2/tasks?status=PENDING")
    assert resp.status_code == 200
    tasks = resp.json()
    matching = [t for t in tasks if t["id"] == task_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "PENDING"
    db.close()


@pytest.mark.anyio
async def test_auto_dispatch_flow(client, db_engine):
    """Auto-dispatch should create task directly in PENDING state."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="autoflow", display_name="AF", path="/tmp/af"))
    db.commit()

    resp = await client.post("/api/v2/tasks", json={
        "title": "Auto flow test",
        "project_name": "autoflow",
        "auto_dispatch": True,
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "PENDING"
    db.close()


# ---------------------------------------------------------------------------
# 9. State machine edge cases in dispatch context
# ---------------------------------------------------------------------------

def test_all_dispatchable_states():
    """Verify which states can transition to PENDING (dispatchable)."""
    dispatchable = {s for s in TaskStatus if can_transition(s, TaskStatus.PENDING)}
    expected = {TaskStatus.INBOX, TaskStatus.PLANNING, TaskStatus.REJECTED, TaskStatus.FAILED, TaskStatus.TIMEOUT}
    assert dispatchable == expected


def test_pending_to_executing_valid():
    """PENDING → EXECUTING must be valid for dispatch to work."""
    assert can_transition(TaskStatus.PENDING, TaskStatus.EXECUTING)


def test_executing_to_review_valid():
    """EXECUTING → REVIEW must be valid for harvest to work."""
    assert can_transition(TaskStatus.EXECUTING, TaskStatus.REVIEW)


def test_executing_to_failed_valid():
    """EXECUTING → FAILED must be valid for error harvest."""
    assert can_transition(TaskStatus.EXECUTING, TaskStatus.FAILED)


def test_executing_to_cancelled_valid():
    """EXECUTING → CANCELLED must be valid for mid-execution cancel."""
    assert can_transition(TaskStatus.EXECUTING, TaskStatus.CANCELLED)


def test_review_to_rejected_valid():
    """REVIEW → REJECTED must be valid for rejection flow."""
    assert can_transition(TaskStatus.REVIEW, TaskStatus.REJECTED)


def test_rejected_to_pending_valid():
    """REJECTED → PENDING must be valid for retry flow."""
    assert can_transition(TaskStatus.REJECTED, TaskStatus.PENDING)


# ---------------------------------------------------------------------------
# 10. Edge cases
# ---------------------------------------------------------------------------

def test_dispatch_no_pending_tasks_noop(db_session, proj, dispatcher):
    """No pending tasks → _dispatch_pending_tasks should be a no-op."""
    # Add non-pending tasks
    db_session.add(Task(title="Inbox", project_name=proj.name, status=TaskStatus.INBOX))
    db_session.add(Task(title="Executing", project_name=proj.name, status=TaskStatus.EXECUTING))
    db_session.commit()

    with patch.object(dispatcher, '_create_task_agent') as mock_create:
        dispatcher._dispatch_pending_tasks(db_session)
        mock_create.assert_not_called()


def test_harvest_no_executing_tasks_noop(db_session, dispatcher):
    """No executing tasks → _harvest_task_completions should be a no-op."""
    # Should not raise
    dispatcher._harvest_task_completions(db_session)


@pytest.mark.anyio
async def test_dispatch_cancelled_task_invalid(client, db_engine):
    """Dispatch from CANCELLED (terminal) should fail."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="canc-proj", display_name="CP", path="/tmp/cp2"))
    t = Task(title="Cancelled", project_name="canc-proj", status=TaskStatus.CANCELLED)
    db.add(t)
    db.commit()
    task_id = t.id
    db.close()

    resp = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert resp.status_code == 409
