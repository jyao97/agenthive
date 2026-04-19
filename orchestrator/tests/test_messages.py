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
    expected = {"PENDING", "QUEUED", "EXECUTING", "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}
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
    db.flush()
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
    db.flush()
    db.add(Agent(id="msg2agent1111", project="msg-proj2", name="Agent M", status=AgentStatus.IDLE))
    db.flush()
    now = datetime.now(timezone.utc)
    for i in range(3):
        ts = now + timedelta(seconds=i)
        db.add(Message(
            agent_id="msg2agent1111",
            role=MessageRole.USER if i % 2 == 0 else MessageRole.AGENT,
            content=f"Message {i}",
            status=MessageStatus.COMPLETED,
            created_at=ts,
            delivered_at=ts,
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
    db.flush()
    db.add(Agent(id="pagagent1111", project="pag-proj", name="Pag Agent", status=AgentStatus.IDLE))
    db.flush()
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


@pytest.mark.anyio
async def test_send_message_idle_tmux_pane_missing_falls_back_to_pending(
    client, db_engine, monkeypatch
):
    """IDLE agent with stale pane should queue message instead of 400."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="sync-proj", display_name="SP", path="/tmp/sp"))
    db.flush()
    db.add(Agent(
        id="syncmsg11111",
        project="sync-proj",
        name="Sync Agent",
        status=AgentStatus.IDLE,
        cli_sync=True,
        tmux_pane="%999",
        session_id="sess-abc",
    ))
    db.commit()
    db.close()

    monkeypatch.setattr("agent_dispatcher.verify_tmux_pane", lambda _pane: False)
    monkeypatch.setattr(
        "agent_dispatcher._detect_tmux_pane_for_session",
        lambda _sid, _path: None,
    )
    def _unexpected_send(_pane: str, _text: str) -> bool:
        raise AssertionError("should not send via tmux")

    monkeypatch.setattr("agent_dispatcher.send_tmux_message", _unexpected_send)

    resp = await client.post(
        "/api/agents/syncmsg11111/messages",
        json={"content": "hello from web"},
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["status"] == "PENDING"

    db = Session()
    agent = db.get(Agent, "syncmsg11111")
    assert agent.tmux_pane is None
    msg = db.get(Message, payload["id"])
    assert msg is not None
    assert msg.status == MessageStatus.PENDING
    assert msg.source == "web"
    db.close()


@pytest.mark.anyio
async def test_send_message_idle_tmux_recover_pane_and_send_direct(
    client, db_engine, monkeypatch
):
    """If old pane is stale but session pane can be recovered, send directly."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="sync-proj2", display_name="SP2", path="/tmp/sp2"))
    db.flush()
    db.add(Agent(
        id="syncmsg22222",
        project="sync-proj2",
        name="Sync Agent 2",
        status=AgentStatus.IDLE,
        cli_sync=True,
        tmux_pane="%111",
        session_id="sess-def",
    ))
    db.commit()
    db.close()

    sent = {}

    def _verify(pane: str) -> bool:
        return pane == "%222"

    def _send(pane: str, text: str) -> bool:
        sent["pane"] = pane
        sent["text"] = text
        return True

    monkeypatch.setattr("agent_dispatcher.verify_tmux_pane", _verify)
    monkeypatch.setattr(
        "agent_dispatcher._detect_tmux_pane_for_session",
        lambda _sid, _path: "%222",
    )
    monkeypatch.setattr("agent_dispatcher.send_tmux_message", _send)
    # display_writer.flush_agent uses its own SessionLocal (separate in-memory DB)
    monkeypatch.setattr("display_writer.flush_agent", lambda _agent_id: None)

    resp = await client.post(
        "/api/agents/syncmsg22222/messages",
        json={"content": "recover and send"},
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["status"] == "QUEUED"
    assert sent == {"pane": "%222", "text": "recover and send"}

    db = Session()
    agent = db.get(Agent, "syncmsg22222")
    assert agent.tmux_pane == "%222"
    msg = db.get(Message, payload["id"])
    assert msg is not None
    assert msg.status == MessageStatus.QUEUED
    db.close()
