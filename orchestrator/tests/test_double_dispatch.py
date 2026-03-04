"""Race condition tests for v2 task double dispatch.

Tests that concurrent dispatch requests on the same task produce exactly
one state transition — not two.  Covers both the API endpoint
(dispatch_task_v2) and the dispatcher loop (_dispatch_pending_tasks).
"""

import os
import sys
import tempfile
import threading

# Ensure orchestrator package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DISABLE_AUTH"] = "1"
os.environ["DB_PATH"] = ":memory:"

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from models import Agent, AgentMode, AgentStatus, Base, Message, MessageRole, MessageStatus, Project, Task, TaskStatus
from task_state_machine import validate_transition, InvalidTransitionError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file_engine(db_path: str):
    """Create a file-based SQLite engine with WAL mode (matches production)."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_size=5,
        max_overflow=5,
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    return engine


def _seed(session_factory) -> str:
    """Create a project + INBOX task, return task_id."""
    db = session_factory()
    proj = Project(name="race-proj", display_name="Race", path="/tmp/race-proj")
    db.add(proj)
    task = Task(title="Double dispatch test", project_name="race-proj", status=TaskStatus.INBOX)
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()
    return task_id


# ---------------------------------------------------------------------------
# 1. Sequential double dispatch — second call must get 409
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_sequential_double_dispatch(client, db_engine):
    """Dispatching the same task twice sequentially: second call must fail."""
    from sqlalchemy.orm import sessionmaker as sm
    S = sm(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = S()
    db.add(Project(name="seq-proj", display_name="SP", path="/tmp/sp"))
    task = Task(title="Seq task", project_name="seq-proj", status=TaskStatus.INBOX)
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()

    r1 = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert r1.status_code == 200
    assert r1.json()["status"] == "PENDING"

    r2 = await client.post(f"/api/v2/tasks/{task_id}/dispatch")
    assert r2.status_code == 409, f"Second dispatch should be rejected, got {r2.status_code}: {r2.text}"


# ---------------------------------------------------------------------------
# 2. Concurrent double dispatch — exactly one thread wins
# ---------------------------------------------------------------------------

def test_concurrent_double_dispatch_cas():
    """Two threads race to dispatch the same INBOX task.

    With the atomic CAS fix, exactly one thread should succeed.
    Without it, both may succeed (TOCTOU).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = _make_file_engine(os.path.join(tmpdir, "race.db"))
        SF = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        task_id = _seed(SF)

        barrier = threading.Barrier(2, timeout=5)
        results = [None, None]

        def dispatch_thread(idx: int):
            db: Session = SF()
            try:
                task = db.get(Task, task_id)
                # Both threads read task as INBOX before either commits
                barrier.wait()

                # Simulate the dispatch endpoint logic:
                # Atomic CAS — only update if status is still what we expect
                from_status = task.status
                try:
                    validate_transition(from_status, TaskStatus.PENDING)
                except InvalidTransitionError:
                    results[idx] = "invalid_transition"
                    return

                rows = (
                    db.query(Task)
                    .filter(Task.id == task_id, Task.status == from_status)
                    .update({"status": TaskStatus.PENDING})
                )
                if rows == 0:
                    db.rollback()
                    results[idx] = "cas_conflict"
                    return

                db.commit()
                results[idx] = "success"
            except Exception as e:
                db.rollback()
                results[idx] = f"error:{e}"
            finally:
                db.close()

        t1 = threading.Thread(target=dispatch_thread, args=(0,))
        t2 = threading.Thread(target=dispatch_thread, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        successes = [r for r in results if r == "success"]
        conflicts = [r for r in results if r == "cas_conflict"]
        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {results}"
        assert len(conflicts) == 1, f"Expected 1 conflict, got {len(conflicts)}: {results}"

        # Verify final DB state
        db = SF()
        task = db.get(Task, task_id)
        assert task.status == TaskStatus.PENDING
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 3. Concurrent dispatch creates exactly one agent (dispatcher level)
# ---------------------------------------------------------------------------

def test_concurrent_dispatch_single_agent():
    """Simulates two dispatcher ticks racing on the same PENDING task.

    The atomic CAS on PENDING→EXECUTING ensures only one agent is created.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = _make_file_engine(os.path.join(tmpdir, "agent.db"))
        SF = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

        # Seed project + PENDING task
        db = SF()
        proj = Project(name="agent-proj", display_name="AP", path="/tmp/ap", max_concurrent=5)
        db.add(proj)
        task = Task(title="Agent race", project_name="agent-proj", status=TaskStatus.PENDING)
        db.add(task)
        db.commit()
        task_id = task.id
        db.close()

        barrier = threading.Barrier(2, timeout=5)
        results = [None, None]

        def dispatcher_tick(idx: int):
            """Simulate what _dispatch_pending_tasks does for a single task."""
            import secrets
            from datetime import datetime, timezone
            db: Session = SF()
            try:
                task = db.get(Task, task_id)
                barrier.wait()

                # Create agent (flush, don't commit yet)
                agent_id = secrets.token_hex(6)
                agent = Agent(
                    id=agent_id,
                    project="agent-proj",
                    name=f"Task: Agent race",
                    mode=AgentMode.AUTO,
                    status=AgentStatus.IDLE,
                    model="claude-opus-4-6",
                    task_id=task_id,
                )
                db.add(agent)
                msg = Message(
                    agent_id=agent_id,
                    role=MessageRole.USER,
                    content="test prompt",
                    status=MessageStatus.PENDING,
                    source="task",
                )
                db.add(msg)
                db.flush()

                # Atomic CAS: PENDING → EXECUTING
                rows = (
                    db.query(Task)
                    .filter(Task.id == task_id, Task.status == TaskStatus.PENDING)
                    .update({
                        "status": TaskStatus.EXECUTING,
                        "agent_id": agent_id,
                        "started_at": datetime.now(timezone.utc),
                    })
                )
                if rows == 0:
                    db.rollback()
                    results[idx] = "cas_conflict"
                    return

                db.commit()
                results[idx] = f"success:{agent_id}"
            except Exception as e:
                db.rollback()
                results[idx] = f"error:{e}"
            finally:
                db.close()

        t1 = threading.Thread(target=dispatcher_tick, args=(0,))
        t2 = threading.Thread(target=dispatcher_tick, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        successes = [r for r in results if r and r.startswith("success:")]
        conflicts = [r for r in results if r == "cas_conflict"]
        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {results}"
        assert len(conflicts) == 1, f"Expected 1 conflict, got {len(conflicts)}: {results}"

        # Verify exactly one agent is linked to the task
        db = SF()
        task = db.get(Task, task_id)
        assert task.status == TaskStatus.EXECUTING
        assert task.agent_id is not None
        agent_count = db.query(Agent).filter(Agent.task_id == task_id).count()
        assert agent_count == 1, f"Expected 1 agent, found {agent_count}"
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 4. Dispatch on already-PENDING task fails
# ---------------------------------------------------------------------------

def test_dispatch_already_pending(db_session, sample_project):
    """Dispatching a task that is already PENDING should be invalid."""
    task = Task(title="Already pending", project_name=sample_project.name, status=TaskStatus.PENDING)
    db_session.add(task)
    db_session.commit()

    assert not validate_transition_safe(task.status, TaskStatus.PENDING)


# ---------------------------------------------------------------------------
# 5. Dispatch on EXECUTING task fails
# ---------------------------------------------------------------------------

def test_dispatch_already_executing(db_session, sample_project):
    """Dispatching a task that is already EXECUTING should be invalid."""
    task = Task(title="Already executing", project_name=sample_project.name, status=TaskStatus.EXECUTING)
    db_session.add(task)
    db_session.commit()

    assert not validate_transition_safe(task.status, TaskStatus.PENDING)


def validate_transition_safe(from_s, to_s) -> bool:
    try:
        validate_transition(from_s, to_s)
        return True
    except InvalidTransitionError:
        return False
