"""Integration tests for the UUID-based dedup system.

Tests the building blocks used by _sync_agent_messages_impl reconciliation:
  - UUID-primary dedup (jsonl_uuid set lookup)
  - Content-based fallback (_dedup_sig)
  - Marker filtering and msg_id backfill
  - Content growth with UUID / prefix matching
  - Import dedup via _import_turns_as_messages
"""

import json
from datetime import datetime, timezone

import pytest

from models import (
    Agent,
    AgentMode,
    AgentStatus,
    Message,
    MessageRole,
    MessageStatus,
    Project,
)
from agent_dispatcher import (
    _dedup_sig,
    _parse_agenthive_marker,
    _AGENTHIVE_PROMPT_MARKER,
    _is_wrapped_prompt,
    _write_session_owner,
    _read_session_owner,
    AgentDispatcher,
)


def _utcnow():
    return datetime.now(timezone.utc)


class DummyWorkerManager:
    def ensure_project_ready(self, _project):
        pass


@pytest.fixture()
def dispatcher():
    return AgentDispatcher(DummyWorkerManager())


# ---------------------------------------------------------------------------
# 1. UUID-primary dedup tests
# ---------------------------------------------------------------------------


class TestUUIDPrimaryDedup:
    """Test the UUID-based dedup logic used in reconciliation."""

    def test_uuid_dedup_skips_known_uuid(self, db_session, sample_agent):
        """A JSONL turn whose uuid already exists in DB should be detected as dup."""
        msg = Message(
            agent_id=sample_agent.id,
            role=MessageRole.USER,
            content="hello",
            status=MessageStatus.COMPLETED,
            source="cli",
            jsonl_uuid="uuid-1",
        )
        db_session.add(msg)
        db_session.commit()

        # Build uuid set (same logic as reconciliation at line ~5433)
        db_uuids = {
            m.jsonl_uuid
            for m in db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.jsonl_uuid.is_not(None),
            )
            .all()
        }

        assert "uuid-1" in db_uuids

    def test_uuid_dedup_allows_new_uuid(self, db_session, sample_agent):
        """A turn with a uuid NOT in DB should pass the dedup check."""
        msg = Message(
            agent_id=sample_agent.id,
            role=MessageRole.USER,
            content="existing",
            status=MessageStatus.COMPLETED,
            source="cli",
            jsonl_uuid="uuid-1",
        )
        db_session.add(msg)
        db_session.commit()

        db_uuids = {
            m.jsonl_uuid
            for m in db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.jsonl_uuid.is_not(None),
            )
            .all()
        }

        assert "uuid-new" not in db_uuids

    def test_content_fallback_when_no_uuid(self, db_session, sample_agent):
        """Messages without jsonl_uuid should match via _dedup_sig content."""
        msg = Message(
            agent_id=sample_agent.id,
            role=MessageRole.USER,
            content="Hello   world\ttabs",
            status=MessageStatus.COMPLETED,
            source="web",
            jsonl_uuid=None,
        )
        db_session.add(msg)
        db_session.commit()

        # Build content-based sig counts (same logic as reconciliation ~5439)
        all_db = (
            db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.role.in_([MessageRole.USER, MessageRole.AGENT]),
            )
            .all()
        )
        db_sig_counts: dict[tuple[str, str], int] = {}
        for m in all_db:
            role_char = "u" if m.role == MessageRole.USER else "a"
            sig = (role_char, _dedup_sig(m.content))
            db_sig_counts[sig] = db_sig_counts.get(sig, 0) + 1

        # The JSONL turn has different whitespace but same semantic content
        incoming_content = "Hello world tabs"
        incoming_sig = ("u", _dedup_sig(incoming_content))
        assert db_sig_counts.get(incoming_sig, 0) > 0

    def test_dedup_sig_whitespace_normalization(self):
        """_dedup_sig collapses all whitespace and truncates at 200 chars."""
        assert _dedup_sig("a  b\tc\nd") == "a b c d"
        # Truncation at 200
        long_text = "word " * 100  # 500 chars
        assert len(_dedup_sig(long_text)) == 200


# ---------------------------------------------------------------------------
# 2. Marker-based dedup tests
# ---------------------------------------------------------------------------


class TestMarkerDedup:
    """Test marker filtering and msg_id backfill."""

    def test_marker_turn_skipped_in_conv_turns(self):
        """Turns starting with <!-- agenthive-prompt should be filtered out."""
        marker_content = "<!-- agenthive-prompt agent_id=abc123 msg_id=msg456 -->\nActual prompt"
        normal_content = "A normal user message"

        raw_turns = [
            ("user", marker_content, None, "uuid-m1"),
            ("user", normal_content, None, "uuid-m2"),
            ("assistant", "Response", None, "uuid-m3"),
        ]

        # Apply same filter as reconciliation — uses _is_wrapped_prompt
        conv_turns = [
            t
            for t in raw_turns
            if t[0] in ("user", "assistant")
            and not (t[0] == "user" and _is_wrapped_prompt(t[1]))
        ]

        assert len(conv_turns) == 2
        assert conv_turns[0][1] == normal_content
        assert conv_turns[1][1] == "Response"

    def test_marker_msg_id_backfill(self, db_session, sample_agent):
        """When a wrapped prompt is seen in JSONL with a uuid, the most recent
        unlinked web message for this agent gets jsonl_uuid backfilled."""
        # Create a web-originated message (no jsonl_uuid yet)
        web_msg = Message(
            id="webmsg123456",
            agent_id=sample_agent.id,
            role=MessageRole.USER,
            content="Original user prompt",
            status=MessageStatus.COMPLETED,
            source="web",
            jsonl_uuid=None,
        )
        db_session.add(web_msg)
        db_session.commit()

        # Simulate incremental sync encountering the wrapped prompt turn.
        # New prompts use preamble prefix; old ones used marker tag.
        wrapped_content = "You are working in project: test\nProject path: /tmp\n\nOriginal user prompt"
        jsonl_uuid = "jsonl-uuid-999"

        # This is the new backfill logic from incremental sync
        if _is_wrapped_prompt(wrapped_content):
            if jsonl_uuid:
                _web_msg = db_session.query(Message).filter(
                    Message.agent_id == sample_agent.id,
                    Message.role == MessageRole.USER,
                    Message.source == "web",
                    Message.jsonl_uuid.is_(None),
                ).order_by(Message.created_at.desc()).first()
                if _web_msg:
                    _web_msg.jsonl_uuid = jsonl_uuid

        db_session.commit()
        db_session.refresh(web_msg)

        assert web_msg.jsonl_uuid == "jsonl-uuid-999"

    def test_parse_agenthive_marker_extracts_attrs(self):
        """_parse_agenthive_marker extracts agent_id and msg_id."""
        text = "<!-- agenthive-prompt agent_id=abc123 msg_id=msg456 -->\nPrompt"
        attrs = _parse_agenthive_marker(text)
        assert attrs is not None
        assert attrs["agent_id"] == "abc123"
        assert attrs["msg_id"] == "msg456"

    def test_parse_agenthive_marker_old_format(self):
        """Old-format markers (no attributes) return empty dict."""
        text = "<!-- agenthive-prompt -->\nPrompt"
        attrs = _parse_agenthive_marker(text)
        assert attrs == {}

    def test_parse_agenthive_marker_no_match(self):
        """Text without a marker returns None."""
        text = "Just a normal message"
        attrs = _parse_agenthive_marker(text)
        assert attrs is None


# ---------------------------------------------------------------------------
# 3. Content growth with UUID tests
# ---------------------------------------------------------------------------


class TestContentGrowth:
    """Test content growth detection via UUID and prefix matching."""

    def test_content_growth_uuid_match(self, db_session, sample_agent):
        """Existing message with same uuid gets updated when content grows."""
        existing = Message(
            agent_id=sample_agent.id,
            role=MessageRole.AGENT,
            content="Short response",
            status=MessageStatus.COMPLETED,
            source="cli",
            jsonl_uuid="uuid-grow-1",
        )
        db_session.add(existing)
        db_session.commit()

        # Simulate the content-growth check from reconciliation (~line 5491-5502)
        new_content = "Short response — now with more details and a longer body"
        new_uuid = "uuid-grow-1"

        _existing_agent_msgs = (
            db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.role == MessageRole.AGENT,
            )
            .all()
        )

        updated = False
        for em in _existing_agent_msgs:
            if new_uuid and em.jsonl_uuid == new_uuid:
                if len(em.content) < len(new_content):
                    em.content = new_content
                    em.completed_at = _utcnow()
                updated = True
                break

        db_session.commit()
        db_session.refresh(existing)

        assert updated is True
        assert existing.content == new_content

    def test_content_growth_prefix_fallback(self, db_session, sample_agent):
        """Existing message (no uuid) gets updated via content prefix match."""
        original_content = "This is the start of a long response that will grow"
        existing = Message(
            agent_id=sample_agent.id,
            role=MessageRole.AGENT,
            content=original_content,
            status=MessageStatus.COMPLETED,
            source="cli",
            jsonl_uuid=None,
        )
        db_session.add(existing)
        db_session.commit()

        # New content extends the original
        new_content = original_content + " with additional paragraphs and details."
        new_uuid = "uuid-new-backfill"

        _existing_agent_msgs = (
            db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.role == MessageRole.AGENT,
            )
            .all()
        )

        # Simulate prefix fallback logic (~line 5504-5518)
        updated = False
        for em in _existing_agent_msgs:
            if (
                len(em.content) < len(new_content)
                and new_content.startswith(em.content[:200])
            ):
                em.content = new_content
                em.completed_at = _utcnow()
                if new_uuid and not em.jsonl_uuid:
                    em.jsonl_uuid = new_uuid
                updated = True
                break

        db_session.commit()
        db_session.refresh(existing)

        assert updated is True
        assert existing.content == new_content
        # UUID should be backfilled
        assert existing.jsonl_uuid == "uuid-new-backfill"

    def test_content_growth_no_match_creates_new(self, db_session, sample_agent):
        """When no existing message matches, a new message should be created."""
        existing = Message(
            agent_id=sample_agent.id,
            role=MessageRole.AGENT,
            content="Completely different response",
            status=MessageStatus.COMPLETED,
            source="cli",
            jsonl_uuid="uuid-other",
        )
        db_session.add(existing)
        db_session.commit()

        new_content = "A brand new response that doesn't match anything"
        new_uuid = "uuid-brand-new"

        _existing_agent_msgs = (
            db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.role == MessageRole.AGENT,
            )
            .all()
        )

        # Walk the same logic: UUID match, then prefix fallback
        updated = False
        for em in _existing_agent_msgs:
            if new_uuid and em.jsonl_uuid == new_uuid:
                if len(em.content) < len(new_content):
                    em.content = new_content
                updated = True
                break
            if (
                len(em.content) < len(new_content)
                and new_content.startswith(em.content[:200])
            ):
                em.content = new_content
                updated = True
                break

        # Nothing matched — we'd create a new message
        assert updated is False

        # Create the new message (same as reconciliation ~line 5520-5530)
        if not updated:
            db_session.add(
                Message(
                    agent_id=sample_agent.id,
                    role=MessageRole.AGENT,
                    content=new_content,
                    status=MessageStatus.COMPLETED,
                    source="cli",
                    jsonl_uuid=new_uuid,
                    completed_at=_utcnow(),
                )
            )
        db_session.commit()

        count = (
            db_session.query(Message)
            .filter(Message.agent_id == sample_agent.id)
            .count()
        )
        assert count == 2


# ---------------------------------------------------------------------------
# 4. Import dedup integration
# ---------------------------------------------------------------------------


class TestImportDedup:
    """Test _import_turns_as_messages and subsequent UUID-based dedup."""

    def test_import_turns_stores_uuid(self, db_session, sample_agent, dispatcher):
        """_import_turns_as_messages should store jsonl_uuid on each message."""
        turns = [
            ("user", "First question", None, "uuid-imp-1"),
            ("assistant", "First answer", None, "uuid-imp-2"),
            ("user", "Second question", None, "uuid-imp-3"),
        ]

        count = dispatcher._import_turns_as_messages(
            db_session, sample_agent.id, turns
        )
        db_session.commit()
        assert count == 3

        db_uuids = {
            m.jsonl_uuid
            for m in db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.jsonl_uuid.is_not(None),
            )
            .all()
        }

        assert "uuid-imp-1" in db_uuids
        assert "uuid-imp-2" in db_uuids
        assert "uuid-imp-3" in db_uuids

    def test_import_skips_existing_uuid_messages(
        self, db_session, sample_agent, dispatcher
    ):
        """After importing turns, reconciliation should detect all uuids as dups."""
        turns = [
            ("user", "Question A", None, "uuid-a"),
            ("assistant", "Answer A", None, "uuid-b"),
            ("user", "Question B", None, "uuid-c"),
            ("assistant", "Answer B", None, "uuid-d"),
        ]

        dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
        db_session.commit()

        # Build uuid set from DB (same as reconciliation)
        db_uuids = {
            m.jsonl_uuid
            for m in db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.jsonl_uuid.is_not(None),
            )
            .all()
        }

        # Every imported uuid should be detected as duplicate
        for _, _, _, uuid in turns:
            if uuid:
                assert uuid in db_uuids

        # Simulate re-import: walk through turns and check which are "missing"
        missing = []
        for r, c, mt, uuid in turns:
            if uuid and uuid in db_uuids:
                continue
            missing.append((r, c, mt, uuid))

        assert len(missing) == 0

    def test_import_mixed_uuid_and_no_uuid(
        self, db_session, sample_agent, dispatcher
    ):
        """Turns without uuid fall through to content-based dedup."""
        turns = [
            ("user", "With UUID", None, "uuid-mix-1"),
            ("assistant", "Response without uuid", None, None),
            ("user", "Another without uuid", None, None),
        ]

        dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
        db_session.commit()

        all_msgs = (
            db_session.query(Message)
            .filter(Message.agent_id == sample_agent.id)
            .all()
        )
        assert len(all_msgs) == 3

        # Build both dedup structures
        db_uuids = {m.jsonl_uuid for m in all_msgs if m.jsonl_uuid}
        db_sig_counts: dict[tuple[str, str], int] = {}
        for m in all_msgs:
            role_char = "u" if m.role == MessageRole.USER else "a"
            sig = (role_char, _dedup_sig(m.content))
            db_sig_counts[sig] = db_sig_counts.get(sig, 0) + 1

        # UUID turn is caught by primary dedup
        assert "uuid-mix-1" in db_uuids

        # Non-uuid turns are caught by content fallback
        assert db_sig_counts.get(("a", _dedup_sig("Response without uuid")), 0) > 0
        assert db_sig_counts.get(("u", _dedup_sig("Another without uuid")), 0) > 0

    def test_import_respects_role_mapping(self, db_session, sample_agent, dispatcher):
        """Verify role mapping: user->USER, assistant->AGENT, system->SYSTEM."""
        turns = [
            ("user", "User msg", None, "uuid-r1"),
            ("assistant", "Agent msg", None, "uuid-r2"),
            ("system", "System msg", None, "uuid-r3"),
        ]

        dispatcher._import_turns_as_messages(db_session, sample_agent.id, turns)
        db_session.commit()

        msgs = (
            db_session.query(Message)
            .filter(Message.agent_id == sample_agent.id)
            .order_by(Message.created_at)
            .all()
        )

        assert msgs[0].role == MessageRole.USER
        assert msgs[1].role == MessageRole.AGENT
        assert msgs[2].role == MessageRole.SYSTEM


# ---------------------------------------------------------------------------
# 5. Full reconciliation flow (simulated)
# ---------------------------------------------------------------------------


class TestReconciliationFlow:
    """End-to-end simulation of the reconciliation dedup logic."""

    def test_full_reconciliation_mixed_scenario(
        self, db_session, sample_agent, dispatcher
    ):
        """Simulate a full reconciliation: some turns already in DB, some new."""
        # Pre-existing DB messages (e.g., from a previous sync)
        db_session.add(
            Message(
                agent_id=sample_agent.id,
                role=MessageRole.USER,
                content="First prompt",
                status=MessageStatus.COMPLETED,
                source="cli",
                jsonl_uuid="uuid-existing-1",
            )
        )
        db_session.add(
            Message(
                agent_id=sample_agent.id,
                role=MessageRole.AGENT,
                content="First response",
                status=MessageStatus.COMPLETED,
                source="cli",
                jsonl_uuid="uuid-existing-2",
            )
        )
        # Legacy message without uuid
        db_session.add(
            Message(
                agent_id=sample_agent.id,
                role=MessageRole.USER,
                content="Legacy prompt no uuid",
                status=MessageStatus.COMPLETED,
                source="web",
                jsonl_uuid=None,
            )
        )
        db_session.commit()

        # Incoming JSONL turns (mix of existing and new)
        conv_turns = [
            ("user", "First prompt", None, "uuid-existing-1"),       # dup by UUID
            ("assistant", "First response", None, "uuid-existing-2"), # dup by UUID
            ("user", "Legacy prompt no uuid", None, None),            # dup by content
            ("user", "Brand new question", None, "uuid-new-1"),       # NEW
            ("assistant", "Brand new answer", None, "uuid-new-2"),    # NEW
        ]

        # Replicate the reconciliation logic
        all_db = (
            db_session.query(Message)
            .filter(
                Message.agent_id == sample_agent.id,
                Message.role.in_([MessageRole.USER, MessageRole.AGENT]),
            )
            .all()
        )

        db_uuids = {m.jsonl_uuid for m in all_db if m.jsonl_uuid}
        db_sig_counts: dict[tuple[str, str], int] = {}
        for m in all_db:
            role_char = "u" if m.role == MessageRole.USER else "a"
            sig = (role_char, _dedup_sig(m.content))
            db_sig_counts[sig] = db_sig_counts.get(sig, 0) + 1

        missing = []
        for r, c, mt, uuid in conv_turns:
            if uuid and uuid in db_uuids:
                continue
            role_char = "u" if r == "user" else "a"
            content_sig = _dedup_sig(c)
            sig = (role_char, content_sig)
            if db_sig_counts.get(sig, 0) > 0:
                db_sig_counts[sig] -= 1
                continue
            missing.append((r, c, mt, uuid))

        assert len(missing) == 2
        assert missing[0] == ("user", "Brand new question", None, "uuid-new-1")
        assert missing[1] == ("assistant", "Brand new answer", None, "uuid-new-2")


# ---------------------------------------------------------------------------
# 6. Session ownership sidecar files
# ---------------------------------------------------------------------------


class TestSessionOwnerSidecar:
    """Test _write_session_owner / _read_session_owner sidecar files."""

    def test_write_and_read_owner(self, tmp_path):
        sid = "test-session-123"
        agent_id = "agent-abc"
        _write_session_owner(str(tmp_path), sid, agent_id)
        assert _read_session_owner(str(tmp_path), sid) == agent_id

    def test_read_missing_returns_none(self, tmp_path):
        assert _read_session_owner(str(tmp_path), "nonexistent") is None

    def test_overwrite_owner(self, tmp_path):
        sid = "test-session-456"
        _write_session_owner(str(tmp_path), sid, "agent-old")
        _write_session_owner(str(tmp_path), sid, "agent-new")
        assert _read_session_owner(str(tmp_path), sid) == "agent-new"

    def test_is_wrapped_prompt_new_format(self):
        """New prompts start with 'You are working in project:'."""
        content = "You are working in project: test\nProject path: /tmp\n\nHello"
        assert _is_wrapped_prompt(content) is True

    def test_is_wrapped_prompt_old_format(self):
        """Old prompts start with '<!-- agenthive-prompt'."""
        content = "<!-- agenthive-prompt agent_id=abc -->\nYou are working in project: test"
        assert _is_wrapped_prompt(content) is True

    def test_is_wrapped_prompt_normal_content(self):
        """Normal user content should not be detected as wrapped."""
        content = "Please fix the bug in main.py"
        assert _is_wrapped_prompt(content) is False


class TestWebTmuxContentDedup:
    """Tests for the web→tmux round-trip content dedup in incremental sync.

    When a web message is sent via tmux to a SYNCING agent, the JSONL
    records it as a user entry.  The sync loop must detect that this
    turn corresponds to the existing web Message and skip import.
    """

    def test_unlinked_web_message_skips_import(self, db_session, sample_agent):
        """User turn matching an unlinked web message should be skipped
        and the web message should get jsonl_uuid backfilled."""
        from sqlalchemy import or_

        # Simulate web-originated message (no jsonl_uuid yet)
        web_msg = Message(
            id="web_dedup_01",
            agent_id=sample_agent.id,
            role=MessageRole.USER,
            content="What is 2+2?",
            status=MessageStatus.COMPLETED,
            source="web",
            jsonl_uuid=None,
        )
        db_session.add(web_msg)
        db_session.commit()

        # Simulate incremental sync encountering the same content
        # as a user turn with a new JSONL UUID (web→tmux round-trip)
        content = "What is 2+2?"
        jsonl_uuid = "uuid-from-jsonl-001"

        # This replicates the secondary content dedup logic
        _norm = _dedup_sig(content)
        _unlinked = db_session.query(Message).filter(
            Message.agent_id == sample_agent.id,
            Message.role == MessageRole.USER,
            or_(
                Message.source == "web",
                Message.source == "plan_continue",
            ),
            Message.jsonl_uuid.is_(None),
        ).all()
        _match = next(
            (m for m in _unlinked if _dedup_sig(m.content) == _norm),
            None,
        )

        assert _match is not None, "Should find unlinked web message"
        assert _match.id == "web_dedup_01"

        # Backfill UUID
        if jsonl_uuid:
            _match.jsonl_uuid = jsonl_uuid
        db_session.commit()
        db_session.refresh(web_msg)

        assert web_msg.jsonl_uuid == "uuid-from-jsonl-001"

    def test_linked_web_message_not_matched(self, db_session, sample_agent):
        """Already-linked web messages should not match (prevents false
        suppression when a user sends the same content twice)."""
        from sqlalchemy import or_

        # Web message already linked to a JSONL UUID
        web_msg = Message(
            id="web_dedup_02",
            agent_id=sample_agent.id,
            role=MessageRole.USER,
            content="Hello again",
            status=MessageStatus.COMPLETED,
            source="web",
            jsonl_uuid="uuid-already-linked",
        )
        db_session.add(web_msg)
        db_session.commit()

        # Second send of same content → new JSONL UUID
        content = "Hello again"
        _norm = _dedup_sig(content)
        _unlinked = db_session.query(Message).filter(
            Message.agent_id == sample_agent.id,
            Message.role == MessageRole.USER,
            or_(
                Message.source == "web",
                Message.source == "plan_continue",
            ),
            Message.jsonl_uuid.is_(None),
        ).all()
        _match = next(
            (m for m in _unlinked if _dedup_sig(m.content) == _norm),
            None,
        )

        assert _match is None, "Linked message should not match"

    def test_plan_continue_source_matched(self, db_session, sample_agent):
        """plan_continue messages should also be matched for dedup."""
        from sqlalchemy import or_

        plan_msg = Message(
            id="plan_dedup_01",
            agent_id=sample_agent.id,
            role=MessageRole.USER,
            content="Continue with the plan",
            status=MessageStatus.COMPLETED,
            source="plan_continue",
            jsonl_uuid=None,
        )
        db_session.add(plan_msg)
        db_session.commit()

        content = "Continue with the plan"
        _norm = _dedup_sig(content)
        _unlinked = db_session.query(Message).filter(
            Message.agent_id == sample_agent.id,
            Message.role == MessageRole.USER,
            or_(
                Message.source == "web",
                Message.source == "plan_continue",
            ),
            Message.jsonl_uuid.is_(None),
        ).all()
        _match = next(
            (m for m in _unlinked if _dedup_sig(m.content) == _norm),
            None,
        )

        assert _match is not None
        assert _match.id == "plan_dedup_01"
