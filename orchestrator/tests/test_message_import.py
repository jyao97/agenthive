"""Tests for Message.jsonl_uuid field and _import_turns_as_messages()."""

import json

import pytest

from models import Message, MessageRole, MessageStatus


# ---------------------------------------------------------------------------
# 1. Message.jsonl_uuid model tests
# ---------------------------------------------------------------------------

def test_message_jsonl_uuid_persists(db_session, sample_agent):
    """jsonl_uuid should survive commit + refresh round-trip."""
    msg = Message(
        agent_id=sample_agent.id,
        role=MessageRole.USER,
        content="persist test",
        status=MessageStatus.COMPLETED,
        jsonl_uuid="uuid-abc-123",
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    assert msg.jsonl_uuid == "uuid-abc-123"


def test_message_jsonl_uuid_nullable(db_session, sample_agent):
    """jsonl_uuid=None should be accepted and remain None."""
    msg = Message(
        agent_id=sample_agent.id,
        role=MessageRole.AGENT,
        content="nullable test",
        status=MessageStatus.COMPLETED,
        jsonl_uuid=None,
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    assert msg.jsonl_uuid is None


def test_message_jsonl_uuid_queryable(db_session, sample_agent):
    """Should be able to filter messages by jsonl_uuid."""
    m1 = Message(
        agent_id=sample_agent.id,
        role=MessageRole.USER,
        content="first",
        status=MessageStatus.COMPLETED,
        jsonl_uuid="uuid-111",
    )
    m2 = Message(
        agent_id=sample_agent.id,
        role=MessageRole.USER,
        content="second",
        status=MessageStatus.COMPLETED,
        jsonl_uuid="uuid-222",
    )
    db_session.add_all([m1, m2])
    db_session.commit()

    found = db_session.query(Message).filter(Message.jsonl_uuid == "uuid-222").one()
    assert found.content == "second"
    assert found.jsonl_uuid == "uuid-222"


# ---------------------------------------------------------------------------
# 2. _import_turns_as_messages() tests
# ---------------------------------------------------------------------------

def _make_dispatcher():
    """Build a minimal AgentDispatcher without a real WorkerManager."""
    from agent_dispatcher import AgentDispatcher

    class DummyWorkerManager:
        def ensure_project_ready(self, _project):
            pass

    return AgentDispatcher(DummyWorkerManager())


def test_import_turns_creates_user_message(db_session, sample_agent):
    """User turn should produce Message with role=USER and source='cli'."""
    dispatcher = _make_dispatcher()
    turns = [("user", "hello from user")]
    dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
    db_session.commit()

    msgs = db_session.query(Message).filter(
        Message.agent_id == sample_agent.id,
        Message.role == MessageRole.USER,
    ).all()
    assert len(msgs) == 1
    assert msgs[0].content == "hello from user"
    assert msgs[0].source == "cli"


def test_import_turns_creates_agent_message(db_session, sample_agent):
    """Assistant turn should map to role=AGENT."""
    dispatcher = _make_dispatcher()
    turns = [("assistant", "agent reply")]
    dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
    db_session.commit()

    msg = db_session.query(Message).filter(
        Message.agent_id == sample_agent.id,
        Message.role == MessageRole.AGENT,
    ).one()
    assert msg.content == "agent reply"
    assert msg.source == "cli"


def test_import_turns_creates_system_message(db_session, sample_agent):
    """System turn should map to role=SYSTEM."""
    dispatcher = _make_dispatcher()
    turns = [("system", "system note")]
    dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
    db_session.commit()

    msg = db_session.query(Message).filter(
        Message.agent_id == sample_agent.id,
        Message.role == MessageRole.SYSTEM,
    ).one()
    assert msg.content == "system note"
    assert msg.source == "cli"


def test_import_turns_stores_jsonl_uuid(db_session, sample_agent):
    """Turn with uuid should populate Message.jsonl_uuid."""
    dispatcher = _make_dispatcher()
    turns = [("user", "with uuid", None, "uuid-xyz")]
    dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
    db_session.commit()

    msg = db_session.query(Message).filter(
        Message.agent_id == sample_agent.id,
    ).one()
    assert msg.jsonl_uuid == "uuid-xyz"


def test_import_turns_stores_none_uuid(db_session, sample_agent):
    """Turn without uuid (None) should leave jsonl_uuid as None."""
    dispatcher = _make_dispatcher()
    turns = [("user", "no uuid")]
    dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
    db_session.commit()

    msg = db_session.query(Message).filter(
        Message.agent_id == sample_agent.id,
    ).one()
    assert msg.jsonl_uuid is None


def test_import_turns_stores_meta_json(db_session, sample_agent):
    """Turn with meta dict should serialize to meta_json."""
    dispatcher = _make_dispatcher()
    meta = {"tool": "bash", "exit_code": 0}
    turns = [("user", "with meta", meta)]
    dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
    db_session.commit()

    msg = db_session.query(Message).filter(
        Message.agent_id == sample_agent.id,
    ).one()
    assert msg.meta_json is not None
    assert json.loads(msg.meta_json) == {"tool": "bash", "exit_code": 0}


def test_import_turns_returns_count(db_session, sample_agent):
    """Return value should equal number of imported messages."""
    dispatcher = _make_dispatcher()
    turns = [
        ("user", "one"),
        ("assistant", "two"),
        ("system", "three"),
    ]
    count = dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
    assert count == 3


def test_import_turns_skips_unknown_role(db_session, sample_agent):
    """Unrecognised role should be silently skipped."""
    dispatcher = _make_dispatcher()
    turns = [("other", "ignored")]
    count = dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
    db_session.commit()

    assert count == 0
    msgs = db_session.query(Message).filter(
        Message.agent_id == sample_agent.id,
    ).all()
    assert len(msgs) == 0


def test_import_turns_custom_source(db_session, sample_agent):
    """source=None should propagate to Message.source."""
    dispatcher = _make_dispatcher()
    turns = [("user", "null source")]
    dispatcher._import_turns_as_messages(
        db_session, sample_agent.id, turns, source=None,
    )
    db_session.commit()

    msg = db_session.query(Message).filter(
        Message.agent_id == sample_agent.id,
    ).one()
    assert msg.source is None
