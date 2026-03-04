"""Tests for agent CRUD and model behavior."""

import pytest

from models import Agent, AgentMode, AgentStatus, Message, MessageRole, MessageStatus, Project


# ---- Model tests ----

def test_agent_model_defaults(db_session):
    """Agent should get sensible defaults on creation."""
    proj = Project(name="agt-proj", display_name="Agt", path="/tmp/agt")
    db_session.add(proj)
    db_session.commit()

    agent = Agent(
        project="agt-proj",
        name="Test Agent",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    assert len(agent.id) == 12
    assert agent.mode == AgentMode.AUTO
    assert agent.status == AgentStatus.STARTING
    assert agent.unread_count == 0
    assert agent.muted is False
    assert agent.is_subagent is False
    assert agent.cli_sync is False
    assert agent.timeout_seconds == 1800
    assert agent.skip_permissions is True
    assert agent.created_at is not None


def test_agent_status_enum_values():
    """All expected agent status values should be valid."""
    expected = {"STARTING", "IDLE", "EXECUTING", "SYNCING", "ERROR", "STOPPED"}
    actual = {s.value for s in AgentStatus}
    assert actual == expected


def test_agent_mode_enum_values():
    """AgentMode should have INTERVIEW and AUTO."""
    expected = {"INTERVIEW", "AUTO"}
    actual = {m.value for m in AgentMode}
    assert actual == expected


# ---- Endpoint tests ----

@pytest.mark.anyio
async def test_list_agents_empty(client):
    """List agents should return empty list when no agents exist."""
    resp = await client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_agents_with_data(client, db_engine):
    """List agents should return agent data."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="proj-b", display_name="B", path="/tmp/b"))
    db.add(Agent(
        id="bbbb11112222",
        project="proj-b",
        name="Agent B",
        status=AgentStatus.IDLE,
    ))
    db.commit()
    db.close()

    resp = await client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "bbbb11112222"
    assert data[0]["name"] == "Agent B"
    assert data[0]["status"] == "IDLE"


@pytest.mark.anyio
async def test_list_agents_filter_by_project(client, db_engine):
    """List agents should filter by project query param."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="proj-x", display_name="X", path="/tmp/x"))
    db.add(Project(name="proj-y", display_name="Y", path="/tmp/y"))
    db.add(Agent(id="xxxx11111111", project="proj-x", name="AX", status=AgentStatus.IDLE))
    db.add(Agent(id="yyyy11111111", project="proj-y", name="AY", status=AgentStatus.IDLE))
    db.commit()
    db.close()

    resp = await client.get("/api/agents?project=proj-x")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["project"] == "proj-x"


@pytest.mark.anyio
async def test_get_agent_not_found(client):
    """Getting a non-existent agent should return 404."""
    resp = await client.get("/api/agents/nonexistent1")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_agent_found(client, db_engine):
    """Getting an existing agent should return full details."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="proj-g", display_name="G", path="/tmp/g"))
    db.add(Agent(id="gggg11112222", project="proj-g", name="Agent G", status=AgentStatus.IDLE))
    db.commit()
    db.close()

    resp = await client.get("/api/agents/gggg11112222")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "gggg11112222"
    assert data["name"] == "Agent G"


@pytest.mark.anyio
async def test_list_agents_excludes_subagents(client, db_engine):
    """Subagents should not appear in the top-level agent list."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="proj-s", display_name="S", path="/tmp/s"))
    db.add(Agent(id="parent111111", project="proj-s", name="Parent", status=AgentStatus.IDLE, is_subagent=False))
    db.add(Agent(id="child1111111", project="proj-s", name="Child", status=AgentStatus.IDLE, is_subagent=True, parent_id="parent111111"))
    db.commit()
    db.close()

    resp = await client.get("/api/agents")
    data = resp.json()
    ids = [a["id"] for a in data]
    assert "parent111111" in ids
    assert "child1111111" not in ids
