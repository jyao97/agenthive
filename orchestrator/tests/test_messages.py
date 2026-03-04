"""Tests for message creation, listing, and pagination."""

import json
from datetime import datetime, timezone

import pytest

from models import Agent, AgentMode, AgentStatus, Message, MessageRole, MessageStatus, Project
from schemas import MessageOut


def _utcnow():
    return datetime.now(timezone.utc)


# ---- Model tests ----

def test_message_model_defaults(db_session, sample_agent):
    """Message should get sensible defaults."""
    msg = Message(
        agent_id=sample_agent.id,
        role=MessageRole.USER,
        content="Test content",
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    assert len(msg.id) == 12
    assert msg.status == MessageStatus.COMPLETED
    assert msg.created_at is not None
    assert msg.source is None
    assert msg.meta_json is None


def test_message_role_enum_values():
    """MessageRole should have USER, AGENT, SYSTEM."""
    expected = {"USER", "AGENT", "SYSTEM"}
    actual = {r.value for r in MessageRole}
    assert actual == expected


def test_message_status_enum_values():
    """MessageStatus should have all expected values."""
    expected = {"PENDING", "EXECUTING", "COMPLETED", "FAILED", "TIMEOUT"}
    actual = {s.value for s in MessageStatus}
    assert actual == expected


def test_message_metadata_json_parsing():
    """MessageOut schema should parse JSON string from meta_json."""
    msg = Message(
        id="testmsg12345",
        agent_id="aaaa11112222",
        role=MessageRole.AGENT,
        content="Response",
        status=MessageStatus.COMPLETED,
        meta_json=json.dumps({"key": "value", "count": 42}),
        created_at=_utcnow(),
    )
    out = MessageOut.model_validate(msg, from_attributes=True)
    assert out.metadata == {"key": "value", "count": 42}


def test_message_metadata_none():
    """MessageOut should handle None meta_json gracefully."""
    msg = Message(
        id="testmsg00000",
        agent_id="aaaa11112222",
        role=MessageRole.USER,
        content="Hello",
        status=MessageStatus.COMPLETED,
        meta_json=None,
        created_at=_utcnow(),
    )
    out = MessageOut.model_validate(msg, from_attributes=True)
    assert out.metadata is None


def test_message_metadata_invalid_json():
    """MessageOut should return None for unparseable meta_json."""
    msg = Message(
        id="testmsg99999",
        agent_id="aaaa11112222",
        role=MessageRole.USER,
        content="Hello",
        status=MessageStatus.COMPLETED,
        meta_json="not valid json {{{",
        created_at=_utcnow(),
    )
    out = MessageOut.model_validate(msg, from_attributes=True)
    assert out.metadata is None


# ---- Endpoint tests ----

@pytest.mark.anyio
async def test_get_messages_agent_not_found(client):
    """Getting messages for a non-existent agent should return 404."""
    resp = await client.get("/api/agents/nonexistent1/messages")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_messages_empty(client, db_engine):
    """Agent with no messages should return empty list."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="msg-proj", display_name="MP", path="/tmp/mp"))
    db.add(Agent(id="mmmm11112222", project="msg-proj", name="Msg Agent", status=AgentStatus.IDLE))
    db.commit()
    db.close()

    resp = await client.get("/api/agents/mmmm11112222/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == []
    assert data["has_more"] is False


@pytest.mark.anyio
async def test_get_messages_with_data(client, db_engine):
    """Should return messages for an agent."""
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timezone, timedelta
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="msg-proj2", display_name="MP2", path="/tmp/mp2"))
    db.add(Agent(id="msg2agent1111", project="msg-proj2", name="Agent M", status=AgentStatus.IDLE))
    now = datetime.now(timezone.utc)
    for i in range(3):
        db.add(Message(
            agent_id="msg2agent1111",
            role=MessageRole.USER if i % 2 == 0 else MessageRole.AGENT,
            content=f"Message {i}",
            status=MessageStatus.COMPLETED,
            created_at=now + timedelta(seconds=i),
        ))
    db.commit()
    db.close()

    resp = await client.get("/api/agents/msg2agent1111/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 3
    # Messages should be ordered oldest-first
    assert data["messages"][0]["content"] == "Message 0"
    assert data["messages"][2]["content"] == "Message 2"


@pytest.mark.anyio
async def test_get_messages_pagination_limit(client, db_engine):
    """Requesting with a small limit should paginate."""
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timezone, timedelta
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="pag-proj", display_name="PP", path="/tmp/pp"))
    db.add(Agent(id="pagagent1111", project="pag-proj", name="Pag Agent", status=AgentStatus.IDLE))
    now = datetime.now(timezone.utc)
    for i in range(10):
        db.add(Message(
            agent_id="pagagent1111",
            role=MessageRole.USER,
            content=f"Msg {i}",
            status=MessageStatus.COMPLETED,
            created_at=now + timedelta(seconds=i),
        ))
    db.commit()
    db.close()

    resp = await client.get("/api/agents/pagagent1111/messages?limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 3
    assert data["has_more"] is True


@pytest.mark.anyio
async def test_message_search_too_short(client):
    """Message search with query < 2 chars should return 400."""
    resp = await client.get("/api/messages/search?q=x")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_message_search_no_results(client):
    """Message search with no matching content should return empty."""
    resp = await client.get("/api/messages/search?q=zzz_nonexistent_query")
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["total"] == 0
