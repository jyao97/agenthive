"""Tests for session parsing and marker functions in agent_dispatcher.py."""

import json
import os
import sys

# Ensure orchestrator package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent_dispatcher import (
    _dedup_sig,
    _get_first_user_uuid,
    _parse_agenthive_marker,
    _parse_session_turns,
    _strip_agent_preamble,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path, entries):
    """Write a list of dicts as a JSONL file."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ===========================================================================
# 1. _parse_agenthive_marker() tests
# ===========================================================================


class TestParseAgenthiveMarker:
    def test_parse_marker_new_format(self):
        text = "<!-- agenthive-prompt agent_id=abc123 msg_id=def456 -->"
        result = _parse_agenthive_marker(text)
        assert result == {"agent_id": "abc123", "msg_id": "def456"}

    def test_parse_marker_agent_id_only(self):
        text = "<!-- agenthive-prompt agent_id=abc123 -->"
        result = _parse_agenthive_marker(text)
        assert result == {"agent_id": "abc123"}

    def test_parse_marker_old_format(self):
        text = "<!-- agenthive-prompt -->"
        result = _parse_agenthive_marker(text)
        assert result == {}

    def test_parse_marker_missing(self):
        text = "no marker here"
        result = _parse_agenthive_marker(text)
        assert result is None

    def test_parse_marker_embedded_in_text(self):
        text = "some preamble content\n\n<!-- agenthive-prompt agent_id=xyz789 -->\nmore text"
        result = _parse_agenthive_marker(text)
        assert result is not None
        assert result["agent_id"] == "xyz789"


# ===========================================================================
# 2. _strip_agent_preamble() tests
# ===========================================================================


class TestStripAgentPreamble:
    def test_strip_preamble_new_marker_with_insights(self):
        text = (
            "<!-- agenthive-prompt agent_id=abc123 msg_id=def456 -->\n"
            "You are working in project: my-project\n"
            "Project path: /tmp/my-project\n\n"
            "First read the project's CLAUDE.md to understand project conventions.\n"
            "Relevant past insights for this task:\n"
            "  - Insight one\n"
            "  - Insight two\n"
            "\n"
            "Please fix the bug in main.py"
            "\n\nIf you make code changes, commit with message format: [agent-abc12345] short description"
        )
        result = _strip_agent_preamble(text)
        assert result == "Please fix the bug in main.py"

    def test_strip_preamble_old_marker(self):
        text = (
            "<!-- agenthive-prompt -->\n"
            "You are working in project: my-project\n"
            "Project path: /tmp/my-project\n\n"
            "First read the project's CLAUDE.md to understand project conventions.\n"
            "Do the thing"
        )
        result = _strip_agent_preamble(text)
        assert result == "Do the thing"

    def test_strip_preamble_no_marker(self):
        text = "Just a plain user message"
        result = _strip_agent_preamble(text)
        assert result == "Just a plain user message"

    def test_strip_preamble_postamble(self):
        text = (
            "Fix the login page"
            "\n\nIf you make code changes, commit with message format: [agent-abc12345] short description"
        )
        result = _strip_agent_preamble(text)
        assert result == "Fix the login page"


# ===========================================================================
# 3. _dedup_sig() tests
# ===========================================================================


class TestDedupSig:
    def test_dedup_sig_normalizes_whitespace(self):
        text = "hello\t\tworld   foo"
        result = _dedup_sig(text)
        assert result == "hello world foo"

    def test_dedup_sig_truncates_to_200(self):
        text = "a" * 300
        result = _dedup_sig(text)
        assert len(result) == 200

    def test_dedup_sig_strips(self):
        text = "  hello world  "
        result = _dedup_sig(text)
        assert result == "hello world"

    def test_dedup_sig_empty(self):
        result = _dedup_sig("")
        assert result == ""


# ===========================================================================
# 4. _parse_session_turns() tests
# ===========================================================================


class TestParseSessionTurns:
    def test_parse_turns_basic_user_assistant(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "user", "uuid": "uuid-1", "message": {"role": "user", "content": "Hello"}, "sessionId": "s1"},
            {"type": "assistant", "uuid": "uuid-2", "message": {"content": [{"type": "text", "text": "Hi there"}]}, "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        assert len(turns) == 2
        assert turns[0][0] == "user"
        assert turns[1][0] == "assistant"

    def test_parse_turns_returns_4_tuples(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "user", "uuid": "uuid-1", "message": {"role": "user", "content": "Hello"}, "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        assert len(turns) == 1
        role, content, meta, uuid = turns[0]
        assert role == "user"
        assert content == "Hello"
        assert meta is None
        assert uuid == "uuid-1"

    def test_parse_turns_extracts_user_uuid(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "user", "uuid": "user-uuid-42", "message": {"role": "user", "content": "Test"}, "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        assert turns[0][3] == "user-uuid-42"

    def test_parse_turns_extracts_assistant_uuid(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "Hi"}, "sessionId": "s1"},
            {"type": "assistant", "uuid": "asst-uuid-1", "message": {"content": [{"type": "text", "text": "Part 1"}]}, "sessionId": "s1"},
            {"type": "assistant", "uuid": "asst-uuid-2", "message": {"content": [{"type": "text", "text": "Part 2"}]}, "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        # The grouped assistant turn should use the FIRST assistant entry's uuid
        asst_turns = [t for t in turns if t[0] == "assistant"]
        assert len(asst_turns) == 1
        assert asst_turns[0][3] == "asst-uuid-1"

    def test_parse_turns_queue_operation_no_uuid(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "Hi"}, "sessionId": "s1"},
            {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "Working..."}]}, "sessionId": "s1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "Follow up question", "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        queue_turns = [t for t in turns if t[1] == "Follow up question"]
        assert len(queue_turns) == 1
        assert queue_turns[0][3] is None  # no uuid for queue-operation

    def test_parse_turns_skips_tool_result(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "Hello"}, "sessionId": "s1"},
            {"type": "user", "uuid": "u2", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "result data"}
            ]}, "sessionId": "s1"},
            {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "Done"}]}, "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        # tool_result user entry should be skipped; only real user + assistant
        roles = [t[0] for t in turns]
        assert roles == ["user", "assistant"]

    def test_parse_turns_skips_system_injected(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "<local-command-caveat>some caveat</local-command-caveat>"}, "sessionId": "s1"},
            {"type": "user", "uuid": "u2", "message": {"role": "user", "content": "<system-reminder>reminder text</system-reminder>"}, "sessionId": "s1"},
            {"type": "user", "uuid": "u3", "message": {"role": "user", "content": "<command-name>some cmd</command-name>"}, "sessionId": "s1"},
            {"type": "user", "uuid": "u4", "message": {"role": "user", "content": "<local-command-stdout>output</local-command-stdout>"}, "sessionId": "s1"},
            {"type": "user", "uuid": "u5", "message": {"role": "user", "content": "<task-notification>notification</task-notification>"}, "sessionId": "s1"},
            {"type": "user", "uuid": "u6", "message": {"role": "user", "content": "Real message"}, "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        user_turns = [t for t in turns if t[0] == "user"]
        assert len(user_turns) == 1
        assert user_turns[0][1] == "Real message"

    def test_parse_turns_missing_file(self):
        turns = _parse_session_turns("/nonexistent/path/session.jsonl")
        assert turns == []

    def test_parse_turns_malformed_json(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        with open(jsonl, "w") as f:
            f.write("not valid json\n")
            f.write('{"type": "user", "uuid": "u1", "message": {"role": "user", "content": "Valid"}, "sessionId": "s1"}\n')
            f.write("{truncated\n")
        turns = _parse_session_turns(str(jsonl))
        assert len(turns) == 1
        assert turns[0][1] == "Valid"

    def test_parse_turns_dedup_by_uuid(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "user", "uuid": "same-uuid", "message": {"role": "user", "content": "Hello"}, "sessionId": "s1"},
            {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "Hi"}]}, "sessionId": "s1"},
            {"type": "user", "uuid": "same-uuid", "message": {"role": "user", "content": "Hello"}, "sessionId": "s1"},
            {"type": "user", "uuid": "different-uuid", "message": {"role": "user", "content": "Hello"}, "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        user_turns = [t for t in turns if t[0] == "user"]
        # same-uuid appears twice → deduplicated to 1; different-uuid kept → total 2
        assert len(user_turns) == 2
        assert user_turns[0][3] == "same-uuid"
        assert user_turns[1][3] == "different-uuid"

    def test_parse_turns_dedup_by_content_no_uuid(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "queue-operation", "operation": "enqueue", "content": "Duplicate msg", "sessionId": "s1"},
            {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "Ok"}]}, "sessionId": "s1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "Duplicate msg", "sessionId": "s1"},
        ])
        turns = _parse_session_turns(str(jsonl))
        user_turns = [t for t in turns if t[0] == "user"]
        # queue-operation turns have no uuid; content-based dedup should remove the duplicate
        assert len(user_turns) == 1
        assert user_turns[0][1] == "Duplicate msg"


# ===========================================================================
# 5. _get_first_user_uuid() tests
# ===========================================================================


class TestGetFirstUserUuid:
    def test_get_first_user_uuid_valid(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "system", "subtype": "init", "content": "started"},
            {"type": "user", "uuid": "first-user-uuid", "message": {"role": "user", "content": "Hello"}, "sessionId": "s1"},
            {"type": "user", "uuid": "second-user-uuid", "message": {"role": "user", "content": "Second"}, "sessionId": "s1"},
        ])
        result = _get_first_user_uuid(str(jsonl))
        assert result == "first-user-uuid"

    def test_get_first_user_uuid_no_user(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [
            {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "Hi"}]}, "sessionId": "s1"},
            {"type": "system", "subtype": "init", "content": "started"},
        ])
        result = _get_first_user_uuid(str(jsonl))
        assert result is None

    def test_get_first_user_uuid_missing_file(self):
        result = _get_first_user_uuid("/nonexistent/path/session.jsonl")
        assert result is None
