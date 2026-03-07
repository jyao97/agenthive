"""Comprehensive tests for agent dispatcher internals."""

import pytest

from models import Agent, AgentMode, AgentStatus, Message, MessageRole, MessageStatus, Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class DummyWorkerManager:
    """Stub WorkerManager that should never be called in these tests."""

    def ensure_project_ready(self, _project):
        raise AssertionError("DummyWorkerManager.ensure_project_ready should not be called")


def _make_dispatcher(monkeypatch, *, max_retries=3):
    """Create an AgentDispatcher with common monkeypatches applied."""
    from agent_dispatcher import AgentDispatcher

    monkeypatch.setattr(
        "agent_dispatcher._detect_tmux_pane_for_session",
        lambda _sid, _path: None,
    )
    monkeypatch.setattr("agent_dispatcher.verify_tmux_pane", lambda _pane: False)

    dispatcher = AgentDispatcher(DummyWorkerManager())
    dispatcher._max_syncing_no_pane_retries = max_retries

    # Discard emitted WebSocket coroutines — no running event loop in tests.
    def _drop_coro(coro):
        try:
            coro.close()
        except Exception:
            pass

    dispatcher._emit = _drop_coro
    return dispatcher


def _setup_syncing_agent(db, *, agent_id="synctest1111", project_name="disp-proj"):
    """Insert a SYNCING cli_sync agent with a PENDING message and return (agent, message)."""
    existing = db.query(Project).filter(Project.name == project_name).first()
    if not existing:
        db.add(Project(name=project_name, display_name="DP", path="/tmp/dp"))
    agent = Agent(
        id=agent_id,
        project=project_name,
        name="Sync Test Agent",
        status=AgentStatus.SYNCING,
        cli_sync=True,
        tmux_pane=None,
        session_id="sess-disp",
    )
    db.add(agent)
    pending = Message(
        agent_id=agent.id,
        role=MessageRole.USER,
        content="queued message",
        status=MessageStatus.PENDING,
    )
    db.add(pending)
    db.commit()
    return agent, pending


# ===========================================================================
# State machine / dispatcher unit tests
# ===========================================================================


def test_agent_status_transitions_starting_to_syncing(db_engine):
    """Verify STARTING agent can transition to SYNCING."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="st-proj", display_name="ST", path="/tmp/st"))
    agent = Agent(
        id="start2sync11",
        project="st-proj",
        name="Transition Agent",
        status=AgentStatus.STARTING,
    )
    db.add(agent)
    db.commit()

    assert agent.status == AgentStatus.STARTING
    agent.status = AgentStatus.SYNCING
    db.commit()
    db.refresh(agent)
    assert agent.status == AgentStatus.SYNCING
    db.close()


def test_agent_status_transitions_syncing_to_idle(db_engine):
    """SYNCING -> IDLE when session ends."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="sy-proj", display_name="SY", path="/tmp/sy"))
    agent = Agent(
        id="sync2idle1111",
        project="sy-proj",
        name="Sync Idle Agent",
        status=AgentStatus.SYNCING,
        cli_sync=True,
    )
    db.add(agent)
    db.commit()

    assert agent.status == AgentStatus.SYNCING
    agent.status = AgentStatus.IDLE
    db.commit()
    db.refresh(agent)
    assert agent.status == AgentStatus.IDLE
    db.close()


@pytest.mark.anyio
async def test_syncing_agent_no_pane_retry_counter_increments(db_engine, monkeypatch):
    """Each tick with no pane increments retry counter."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    agent, _pending = _setup_syncing_agent(db, agent_id="retrycnt1111")
    dispatcher = _make_dispatcher(monkeypatch, max_retries=10)

    # Tick 1
    dispatcher._dispatch_pending_messages(db)
    db.flush()
    assert dispatcher._syncing_no_pane_retries.get(agent.id) == 1

    # Tick 2
    dispatcher._dispatch_pending_messages(db)
    db.flush()
    assert dispatcher._syncing_no_pane_retries.get(agent.id) == 2

    # Tick 3
    dispatcher._dispatch_pending_messages(db)
    db.flush()
    assert dispatcher._syncing_no_pane_retries.get(agent.id) == 3

    # Agent should still be SYNCING since max_retries=10
    assert agent.status == AgentStatus.SYNCING
    db.close()


# ===========================================================================
# Liveness check tests (_reap_dead_agents)
# ===========================================================================


@pytest.mark.anyio
async def test_liveness_check_skips_cli_sync_agents(db_engine, monkeypatch):
    """cli_sync=True SYNCING agents should not be stopped by _reap_dead_agents
    when they still have a tmux pane in the pane map."""
    from sqlalchemy.orm import sessionmaker
    from agent_dispatcher import AgentDispatcher

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="live-proj", display_name="LP", path="/tmp/lp"))
    agent = Agent(
        id="livecli11111",
        project="live-proj",
        name="CLI Sync Agent",
        status=AgentStatus.SYNCING,
        cli_sync=True,
        tmux_pane="%5",
        session_id="sess-live",
    )
    db.add(agent)
    db.commit()

    dispatcher = AgentDispatcher(DummyWorkerManager())

    def _drop_coro(coro):
        try:
            coro.close()
        except Exception:
            pass

    dispatcher._emit = _drop_coro

    # Return a pane map that claims our agent's pane has claude running
    monkeypatch.setattr(
        "agent_dispatcher._build_tmux_claude_map",
        lambda: {"%5": {"is_orchestrator": False}},
    )
    dispatcher._tmux_map_cache = None
    monkeypatch.setattr("agent_dispatcher.verify_tmux_pane", lambda _pane: True)

    dispatcher._reap_dead_agents(db)
    db.flush()

    # Agent should remain SYNCING — not stopped
    assert agent.status == AgentStatus.SYNCING
    db.close()


@pytest.mark.anyio
async def test_liveness_check_skips_idle_agents(db_engine, monkeypatch):
    """IDLE orchestrator agents (cli_sync=False) should not be affected by _reap_dead_agents."""
    from sqlalchemy.orm import sessionmaker
    from agent_dispatcher import AgentDispatcher

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="idle-proj", display_name="IP", path="/tmp/ip"))
    agent = Agent(
        id="idleagent111",
        project="idle-proj",
        name="Idle Agent",
        status=AgentStatus.IDLE,
        cli_sync=False,
    )
    db.add(agent)
    db.commit()

    dispatcher = AgentDispatcher(DummyWorkerManager())

    def _drop_coro(coro):
        try:
            coro.close()
        except Exception:
            pass

    dispatcher._emit = _drop_coro

    monkeypatch.setattr(
        "agent_dispatcher._build_tmux_claude_map",
        lambda: {},
    )
    dispatcher._tmux_map_cache = None

    dispatcher._reap_dead_agents(db)
    db.flush()

    # IDLE orchestrator agent should remain IDLE
    assert agent.status == AgentStatus.IDLE
    db.close()


@pytest.mark.anyio
async def test_liveness_check_skips_stopped_agents(db_engine, monkeypatch):
    """STOPPED agents should not be re-processed by _reap_dead_agents."""
    from sqlalchemy.orm import sessionmaker
    from agent_dispatcher import AgentDispatcher

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="stop-proj", display_name="SP", path="/tmp/sp"))
    agent = Agent(
        id="stopped11111",
        project="stop-proj",
        name="Stopped Agent",
        status=AgentStatus.STOPPED,
        cli_sync=True,
    )
    db.add(agent)
    db.commit()

    dispatcher = AgentDispatcher(DummyWorkerManager())

    def _drop_coro(coro):
        try:
            coro.close()
        except Exception:
            pass

    dispatcher._emit = _drop_coro

    monkeypatch.setattr(
        "agent_dispatcher._build_tmux_claude_map",
        lambda: {},
    )
    dispatcher._tmux_map_cache = None

    dispatcher._reap_dead_agents(db)
    db.flush()

    # STOPPED agent should remain STOPPED (not re-processed)
    assert agent.status == AgentStatus.STOPPED
    db.close()


# ===========================================================================
# Grace period for SYNCING no-pane (split into three ticks)
# ===========================================================================


@pytest.mark.anyio
async def test_syncing_grace_period_tick1(db_engine, monkeypatch):
    """First tick: agent should remain SYNCING."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    agent, pending = _setup_syncing_agent(db, agent_id="gracetick1_1")
    dispatcher = _make_dispatcher(monkeypatch, max_retries=3)

    dispatcher._dispatch_pending_messages(db)
    db.flush()

    assert agent.status == AgentStatus.SYNCING
    assert pending.status == MessageStatus.PENDING
    db.close()


@pytest.mark.anyio
async def test_syncing_grace_period_tick2(db_engine, monkeypatch):
    """Second tick: agent should still be SYNCING."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    agent, pending = _setup_syncing_agent(db, agent_id="gracetick2_1")
    dispatcher = _make_dispatcher(monkeypatch, max_retries=3)

    dispatcher._dispatch_pending_messages(db)
    db.flush()
    assert agent.status == AgentStatus.SYNCING

    dispatcher._dispatch_pending_messages(db)
    db.flush()
    assert agent.status == AgentStatus.SYNCING
    assert pending.status == MessageStatus.PENDING
    db.close()


@pytest.mark.anyio
async def test_syncing_grace_period_tick3_stops(db_engine, monkeypatch):
    """Third tick: grace exhausted — agent STOPPED, pending message FAILED."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    agent, pending = _setup_syncing_agent(db, agent_id="gracetick3_1")
    dispatcher = _make_dispatcher(monkeypatch, max_retries=3)

    # Tick 1
    dispatcher._dispatch_pending_messages(db)
    db.flush()
    assert agent.status == AgentStatus.SYNCING

    # Tick 2
    dispatcher._dispatch_pending_messages(db)
    db.flush()
    assert agent.status == AgentStatus.SYNCING

    # Tick 3 — grace exhausted
    dispatcher._dispatch_pending_messages(db)
    db.flush()
    assert agent.status == AgentStatus.STOPPED
    assert pending.status == MessageStatus.FAILED
    assert pending.error_message == "Agent tmux session no longer exists"

    # Verify a SYSTEM message was recorded
    system_messages = (
        db.query(Message)
        .filter(
            Message.agent_id == agent.id,
            Message.role == MessageRole.SYSTEM,
        )
        .all()
    )
    assert any("tmux pane not found" in (m.content or "") for m in system_messages)
    db.close()


# ===========================================================================
# Pane matching
# ===========================================================================


def test_agent_session_name_format():
    """Session name should be ah-{agent_id[:8]}."""
    agent_id = "abcdef123456"
    session_name = f"ah-{agent_id[:8]}"
    assert session_name == "ah-abcdef12"
    assert session_name.startswith("ah-")
    assert len(session_name) == 11  # "ah-" (3) + 8 chars


# ===========================================================================
# Model-level tests
# ===========================================================================


def test_agent_cli_sync_default_false(db_engine):
    """Default cli_sync is False."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="cs-proj", display_name="CS", path="/tmp/cs"))
    agent = Agent(
        id="clisyncdef11",
        project="cs-proj",
        name="CLI Default Agent",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    assert agent.cli_sync is False
    db.close()


def test_agent_tmux_pane_nullable(db_engine):
    """tmux_pane can be None."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="tp-proj", display_name="TP", path="/tmp/tp"))
    agent = Agent(
        id="tmuxpanenu11",
        project="tp-proj",
        name="No Pane Agent",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    assert agent.tmux_pane is None
    db.close()


def test_agent_session_id_nullable(db_engine):
    """session_id can be None."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="si-proj", display_name="SI", path="/tmp/si"))
    agent = Agent(
        id="sessidnull11",
        project="si-proj",
        name="No Session Agent",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    assert agent.session_id is None
    db.close()


def test_agent_timeout_default(db_engine):
    """Default timeout_seconds=1800."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="to-proj", display_name="TO", path="/tmp/to"))
    agent = Agent(
        id="timeoutdef11",
        project="to-proj",
        name="Timeout Agent",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    assert agent.timeout_seconds == 1800
    db.close()


def test_agent_parent_child_relationship(db_engine):
    """parent_id links to another agent."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="pc-proj", display_name="PC", path="/tmp/pc"))
    parent = Agent(
        id="parentag1111",
        project="pc-proj",
        name="Parent Agent",
        status=AgentStatus.IDLE,
    )
    db.add(parent)
    db.commit()

    child = Agent(
        id="childag11111",
        project="pc-proj",
        name="Child Agent",
        status=AgentStatus.IDLE,
        parent_id=parent.id,
        is_subagent=True,
    )
    db.add(child)
    db.commit()
    db.refresh(child)

    assert child.parent_id == "parentag1111"
    assert child.is_subagent is True

    # Verify the parent exists and can be loaded
    loaded_parent = db.get(Agent, child.parent_id)
    assert loaded_parent is not None
    assert loaded_parent.id == parent.id
    assert loaded_parent.name == "Parent Agent"
    db.close()
