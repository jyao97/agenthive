"""Comprehensive tests for the Messages API endpoints.

Covers: send, get (pagination), delete, update, and search.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from models import Agent, AgentMode, AgentStatus, Message, MessageRole, MessageStatus, Project
from schemas import MessageOut


def _utcnow():
    return datetime.now(timezone.utc)


def _make_session(db_engine):
    """Helper to create a DB session from an engine."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    return Session()


def _seed_agent(db, *, agent_id, project_name="test-proj", status=AgentStatus.IDLE):
    """Insert a project + agent pair and return the agent id."""
    existing = db.get(Project, project_name)
    if not existing:
        db.add(Project(name=project_name, display_name=project_name.title(), path=f"/tmp/{project_name}"))
        db.flush()
    db.add(Agent(
        id=agent_id,
        project=project_name,
        name=f"Agent {agent_id[:6]}",
        mode=AgentMode.AUTO,
        status=status,
        model="claude-opus-4-7",
    ))
    db.commit()
    return agent_id


def _encode_cursor(dt):
    """URL-encode an ISO datetime string so '+' in timezone offset is preserved."""
    return quote(dt.isoformat(), safe="")


# ===========================================================================
# Send message tests
# ===========================================================================

@pytest.mark.anyio
async def test_send_message_to_idle_agent(client, db_engine):
    """POST /api/agents/{id}/messages to an IDLE agent returns 201 with PENDING status."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="idle11112222", status=AgentStatus.IDLE)
    db.close()

    resp = await client.post(
        "/api/agents/idle11112222/messages",
        json={"content": "Hello idle agent"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["content"] == "Hello idle agent"
    assert data["agent_id"] == "idle11112222"
    assert data["role"] == "USER"
    assert data["source"] == "web"

    # Verify persisted in DB
    db = _make_session(db_engine)
    msg = db.get(Message, data["id"])
    assert msg is not None
    assert msg.status == MessageStatus.PENDING
    db.close()


@pytest.mark.anyio
async def test_send_message_to_stopped_agent(client, db_engine):
    """POST to a STOPPED agent returns 400."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="stop11112222", status=AgentStatus.STOPPED)
    db.close()

    resp = await client.post(
        "/api/agents/stop11112222/messages",
        json={"content": "Hello stopped agent"},
    )
    assert resp.status_code == 400
    assert "stopped" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_send_message_empty_content(client, db_engine):
    """POST with empty content should return 422 validation error (min_length=1)."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="empt11112222", status=AgentStatus.IDLE)
    db.close()

    resp = await client.post(
        "/api/agents/empt11112222/messages",
        json={"content": ""},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_send_message_agent_not_found(client):
    """POST to a non-existent agent returns 404."""
    resp = await client.post(
        "/api/agents/nope11112222/messages",
        json={"content": "Hello?"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_send_message_with_queue_flag(client, db_engine):
    """POST with queue=true should queue message as PENDING."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="queu11112222", status=AgentStatus.IDLE)
    db.close()

    resp = await client.post(
        "/api/agents/queu11112222/messages",
        json={"content": "Queued message", "queue": True},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["content"] == "Queued message"


@pytest.mark.anyio
async def test_send_message_with_scheduled_at(client, db_engine):
    """POST with scheduled_at should persist that timestamp on the message."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="sched1112222", status=AgentStatus.IDLE)
    db.close()

    future = (_utcnow() + timedelta(hours=1)).isoformat()
    resp = await client.post(
        "/api/agents/sched1112222/messages",
        json={"content": "Scheduled msg", "scheduled_at": future},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["scheduled_at"] is not None
    assert data["status"] == "PENDING"


# ===========================================================================
# Get messages (pagination) tests
# ===========================================================================

def _seed_messages(db, agent_id, count=10):
    """Insert `count` messages with incrementing timestamps and session_seq, return list of session_seq values."""
    now = _utcnow()
    seqs = []
    for i in range(count):
        ts = now + timedelta(seconds=i)
        db.add(Message(
            agent_id=agent_id,
            role=MessageRole.USER if i % 2 == 0 else MessageRole.AGENT,
            content=f"Message number {i}",
            status=MessageStatus.COMPLETED,
            created_at=ts,
            delivered_at=ts,
            session_seq=i,
        ))
        seqs.append(i)
    db.commit()
    return seqs


@pytest.mark.anyio
async def test_get_messages_pagination_before_cursor(client, db_engine):
    """Using before= param returns older messages."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="bfre11112222", project_name="pag-before")
    seqs = _seed_messages(db, "bfre11112222", count=10)
    db.close()

    # Use the 6th message session_seq as cursor (index 5) -- should return msgs 0..4
    cursor = seqs[5]
    resp = await client.get(f"/api/agents/bfre11112222/messages?before={cursor}&limit=50")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 5
    # Messages ordered oldest-first
    assert data["messages"][0]["content"] == "Message number 0"
    assert data["messages"][-1]["content"] == "Message number 4"


@pytest.mark.anyio
async def test_get_messages_pagination_after_cursor(client, db_engine):
    """Using after= param returns newer messages."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="aftr11112222", project_name="pag-after")
    seqs = _seed_messages(db, "aftr11112222", count=10)
    db.close()

    # Use the 4th message session_seq as cursor (index 3) -- should return msgs 4..9
    cursor = seqs[3]
    resp = await client.get(f"/api/agents/aftr11112222/messages?after={cursor}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 6
    assert data["messages"][0]["content"] == "Message number 4"
    assert data["messages"][-1]["content"] == "Message number 9"
    assert data["has_more"] is False


@pytest.mark.anyio
async def test_get_messages_resets_unread_on_initial_load(client, db_engine):
    """Initial load (no cursor) should reset agent.unread_count to 0."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="unrd11112222", project_name="unread-proj")
    _seed_messages(db, "unrd11112222", count=3)
    # Set unread_count > 0
    agent = db.get(Agent, "unrd11112222")
    agent.unread_count = 5
    db.commit()
    db.close()

    resp = await client.get("/api/agents/unrd11112222/messages")
    assert resp.status_code == 200
    assert len(resp.json()["messages"]) == 3

    # Verify unread_count was reset
    db = _make_session(db_engine)
    agent = db.get(Agent, "unrd11112222")
    assert agent.unread_count == 0
    db.close()


@pytest.mark.anyio
async def test_get_messages_with_after_does_not_reset_unread(client, db_engine):
    """Using after= cursor should NOT reset unread_count."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="nourst112222", project_name="nounread-proj")
    seqs = _seed_messages(db, "nourst112222", count=5)
    agent = db.get(Agent, "nourst112222")
    agent.unread_count = 3
    db.commit()
    db.close()

    cursor = seqs[2]
    resp = await client.get(f"/api/agents/nourst112222/messages?after={cursor}")
    assert resp.status_code == 200

    # unread_count should remain unchanged
    db = _make_session(db_engine)
    agent = db.get(Agent, "nourst112222")
    assert agent.unread_count == 3
    db.close()


# ===========================================================================
# Delete message tests
# ===========================================================================

@pytest.mark.anyio
async def test_delete_pending_message(client, db_engine):
    """DELETE a PENDING message should succeed with 200."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="delp11112222", project_name="del-proj")
    msg = Message(
        agent_id="delp11112222",
        role=MessageRole.USER,
        content="To be cancelled",
        status=MessageStatus.PENDING,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    msg_id = msg.id
    db.close()

    resp = await client.delete(f"/api/agents/delp11112222/messages/{msg_id}")
    assert resp.status_code == 200
    assert resp.json()["detail"] == "Message cancelled"

    # Soft-cancel: row stays, status flips to CANCELLED so the bubble can
    # render greyed-out instead of vanishing.
    db = _make_session(db_engine)
    cancelled = db.get(Message, msg_id)
    assert cancelled is not None
    assert cancelled.status == MessageStatus.CANCELLED
    db.close()


@pytest.mark.anyio
async def test_delete_completed_message_rejected(client, db_engine):
    """DELETE a non-PENDING (COMPLETED) message returns 400."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="delc11112222", project_name="delc-proj")
    msg = Message(
        agent_id="delc11112222",
        role=MessageRole.USER,
        content="Already done",
        status=MessageStatus.COMPLETED,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    msg_id = msg.id
    db.close()

    resp = await client.delete(f"/api/agents/delc11112222/messages/{msg_id}")
    assert resp.status_code == 400
    assert "PENDING" in resp.json()["detail"]


@pytest.mark.anyio
async def test_delete_message_not_found(client, db_engine):
    """DELETE a non-existent message returns 404."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="deln11112222", project_name="deln-proj")
    db.close()

    resp = await client.delete("/api/agents/deln11112222/messages/nonexistent99")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_delete_message_wrong_agent(client, db_engine):
    """DELETE where the message belongs to a different agent returns 404."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="delw11112222", project_name="delw-proj")
    _seed_agent(db, agent_id="delw22222222", project_name="delw-proj")
    msg = Message(
        agent_id="delw11112222",
        role=MessageRole.USER,
        content="Belongs to agent1",
        status=MessageStatus.PENDING,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    msg_id = msg.id
    db.close()

    # Try to delete via agent2
    resp = await client.delete(f"/api/agents/delw22222222/messages/{msg_id}")
    assert resp.status_code == 404


# ===========================================================================
# Update message tests
# ===========================================================================

@pytest.mark.anyio
async def test_update_pending_message_content(client, db_engine):
    """PUT to update content of a PENDING message should succeed."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="updt11112222", project_name="updt-proj")
    msg = Message(
        agent_id="updt11112222",
        role=MessageRole.USER,
        content="Original content",
        status=MessageStatus.PENDING,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    msg_id = msg.id
    db.close()

    resp = await client.put(
        f"/api/agents/updt11112222/messages/{msg_id}",
        json={"content": "Updated content"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "Updated content"
    assert data["id"] == msg_id

    # Verify in DB
    db = _make_session(db_engine)
    msg = db.get(Message, msg_id)
    assert msg.content == "Updated content"
    db.close()


@pytest.mark.anyio
async def test_update_completed_message_rejected(client, db_engine):
    """PUT on a COMPLETED message returns 400."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="updc11112222", project_name="updc-proj")
    msg = Message(
        agent_id="updc11112222",
        role=MessageRole.USER,
        content="Done message",
        status=MessageStatus.COMPLETED,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    msg_id = msg.id
    db.close()

    resp = await client.put(
        f"/api/agents/updc11112222/messages/{msg_id}",
        json={"content": "Trying to update"},
    )
    assert resp.status_code == 400
    assert "PENDING" in resp.json()["detail"]


@pytest.mark.anyio
async def test_update_message_empty_content(client, db_engine):
    """PUT with empty content (whitespace-only) returns 400."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="upde11112222", project_name="upde-proj")
    msg = Message(
        agent_id="upde11112222",
        role=MessageRole.USER,
        content="Some content",
        status=MessageStatus.PENDING,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    msg_id = msg.id
    db.close()

    resp = await client.put(
        f"/api/agents/upde11112222/messages/{msg_id}",
        json={"content": "   "},
    )
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


# ===========================================================================
# Search tests
# ===========================================================================

@pytest.mark.anyio
async def test_message_search_with_results(client, db_engine):
    """Search should find messages matching the query string."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="srch11112222", project_name="search-proj")
    db.add(Message(
        agent_id="srch11112222",
        role=MessageRole.USER,
        content="The quick brown fox jumps over the lazy dog",
        status=MessageStatus.COMPLETED,
        created_at=_utcnow(),
    ))
    db.add(Message(
        agent_id="srch11112222",
        role=MessageRole.AGENT,
        content="Another message without the keyword",
        status=MessageStatus.COMPLETED,
        created_at=_utcnow() + timedelta(seconds=1),
    ))
    db.add(Message(
        agent_id="srch11112222",
        role=MessageRole.USER,
        content="The brown fox strikes again",
        status=MessageStatus.COMPLETED,
        created_at=_utcnow() + timedelta(seconds=2),
    ))
    db.commit()
    db.close()

    resp = await client.get("/api/messages/search?q=brown fox")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["results"]) == 2
    # Both results should contain the search term in the snippet
    for result in data["results"]:
        assert "brown fox" in result["content_snippet"].lower()


@pytest.mark.anyio
async def test_message_search_filter_by_project(client, db_engine):
    """Search with project= param should only return messages from that project."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="srchp1112222", project_name="alpha-proj")
    _seed_agent(db, agent_id="srchp2222222", project_name="beta-proj")
    db.add(Message(
        agent_id="srchp1112222",
        role=MessageRole.USER,
        content="Unique zebra content in alpha",
        status=MessageStatus.COMPLETED,
        created_at=_utcnow(),
    ))
    db.add(Message(
        agent_id="srchp2222222",
        role=MessageRole.USER,
        content="Unique zebra content in beta",
        status=MessageStatus.COMPLETED,
        created_at=_utcnow() + timedelta(seconds=1),
    ))
    db.commit()
    db.close()

    resp = await client.get("/api/messages/search?q=zebra&project=alpha-proj")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["project"] == "alpha-proj"


@pytest.mark.anyio
async def test_message_search_filter_by_role(client, db_engine):
    """Search with role= param should only return messages of that role."""
    db = _make_session(db_engine)
    _seed_agent(db, agent_id="srchr1112222", project_name="role-proj")
    db.add(Message(
        agent_id="srchr1112222",
        role=MessageRole.USER,
        content="Unique giraffe from user",
        status=MessageStatus.COMPLETED,
        created_at=_utcnow(),
    ))
    db.add(Message(
        agent_id="srchr1112222",
        role=MessageRole.AGENT,
        content="Unique giraffe from agent",
        status=MessageStatus.COMPLETED,
        created_at=_utcnow() + timedelta(seconds=1),
    ))
    db.commit()
    db.close()

    resp = await client.get("/api/messages/search?q=giraffe&role=USER")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["role"] == "USER"
