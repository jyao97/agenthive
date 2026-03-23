"""Tests for incremental JSONL sync — _read_new_lines, sync_parse_incremental,
sync_reset_incremental, and the turn-boundary tracking in SyncContext."""

import json
import os
import sys

# Ensure orchestrator package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from sync_engine import (
    SyncContext,
    _read_new_lines,
    sync_parse_incremental,
    sync_reset_incremental,
)
from agent_dispatcher import (
    _parse_session_turns,
    _parse_session_turns_from_lines,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path, entries):
    """Write a list of dicts as a JSONL file."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _append_jsonl(path, entries):
    """Append entries to an existing JSONL file."""
    with open(path, "a") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _user_entry(content, uuid=None):
    e = {"type": "user", "message": {"role": "user", "content": content}, "sessionId": "s1"}
    if uuid:
        e["uuid"] = uuid
    return e


def _assistant_entry(text, uuid=None):
    e = {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}, "sessionId": "s1"}
    if uuid:
        e["uuid"] = uuid
    return e


def _tool_entry(name, tool_input=None, uuid=None):
    e = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": tool_input or {}, "id": f"tool-{name}"}]},
        "sessionId": "s1",
    }
    if uuid:
        e["uuid"] = uuid
    return e


def _make_ctx(tmp_path, entries=None):
    """Create a SyncContext pointing at a tmp JSONL file."""
    jsonl = tmp_path / "session.jsonl"
    if entries:
        _write_jsonl(jsonl, entries)
    else:
        jsonl.write_text("")
    return SyncContext(
        agent_id="test-agent",
        session_id="test-session",
        project_path=str(tmp_path),
        jsonl_path=str(jsonl),
    )


# ===========================================================================
# 1. _parse_session_turns_from_lines — refactored parser
# ===========================================================================

class TestParseFromLines:
    def test_basic_user_assistant(self):
        lines = [
            json.dumps(_user_entry("Hello", "u1")),
            json.dumps(_assistant_entry("Hi there", "a1")),
        ]
        turns = _parse_session_turns_from_lines(lines)
        assert len(turns) == 2
        assert turns[0] == ("user", "Hello", None, "u1")
        assert turns[1][0] == "assistant"
        assert "Hi there" in turns[1][1]

    def test_matches_file_based_parser(self, tmp_path):
        """_parse_session_turns_from_lines should produce identical output
        to _parse_session_turns when given the same lines."""
        entries = [
            _user_entry("First question", "u1"),
            _assistant_entry("First answer part 1", "a1"),
            _assistant_entry("First answer part 2", "a2"),
            _user_entry("Second question", "u2"),
            _assistant_entry("Second answer", "a3"),
        ]
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, entries)

        file_turns = _parse_session_turns(str(jsonl))
        line_turns = _parse_session_turns_from_lines(
            [json.dumps(e) for e in entries]
        )
        assert file_turns == line_turns

    def test_empty_lines(self):
        assert _parse_session_turns_from_lines([]) == []

    def test_assistant_grouping(self):
        """Multiple assistant entries between user messages should be
        grouped into a single assistant turn."""
        lines = [
            json.dumps(_user_entry("Q", "u1")),
            json.dumps(_assistant_entry("Part1", "a1")),
            json.dumps(_assistant_entry("Part2", "a2")),
            json.dumps(_assistant_entry("Part3")),
        ]
        turns = _parse_session_turns_from_lines(lines)
        assert len(turns) == 2  # 1 user + 1 grouped assistant
        assert turns[1][0] == "assistant"
        assert "Part1" in turns[1][1]
        assert "Part2" in turns[1][1]
        assert "Part3" in turns[1][1]
        # UUID should be first assistant entry's
        assert turns[1][3] == "a1"


# ===========================================================================
# 2. _read_new_lines — byte-offset incremental reading
# ===========================================================================

class TestReadNewLines:
    def test_read_from_zero(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [_user_entry("Hello", "u1")])
        lines, offset = _read_new_lines(str(jsonl), 0)
        assert len(lines) == 1
        assert "Hello" in lines[0]
        assert offset > 0

    def test_read_incremental(self, tmp_path):
        """Read initial lines, then append more and read only the new ones."""
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [_user_entry("First", "u1")])
        lines1, offset1 = _read_new_lines(str(jsonl), 0)
        assert len(lines1) == 1

        _append_jsonl(jsonl, [_assistant_entry("Reply", "a1")])
        lines2, offset2 = _read_new_lines(str(jsonl), offset1)
        assert len(lines2) == 1
        assert "Reply" in lines2[0]
        assert offset2 > offset1

    def test_no_new_data(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [_user_entry("Hello")])
        _, offset = _read_new_lines(str(jsonl), 0)
        # Read again with same offset — no new data
        lines, offset2 = _read_new_lines(str(jsonl), offset)
        assert lines == []
        assert offset2 == offset

    def test_partial_line_excluded(self, tmp_path):
        """A partial line (no trailing newline) should NOT be returned."""
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [_user_entry("Complete")])
        offset1 = os.path.getsize(str(jsonl))

        # Append a partial line (no trailing newline)
        with open(str(jsonl), "a") as f:
            f.write('{"type":"user","message":{"content":"partial"}')  # no \n

        lines, offset2 = _read_new_lines(str(jsonl), offset1)
        assert lines == []
        # Offset should NOT advance past the partial line
        assert offset2 == offset1

    def test_partial_then_complete(self, tmp_path):
        """After a partial line gets completed (newline added), it should
        be returned on the next read."""
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [_user_entry("First")])
        offset1 = os.path.getsize(str(jsonl))

        # Write partial line
        with open(str(jsonl), "a") as f:
            f.write('{"type":"user","message":{"content":"Second"}}')
        lines1, offset2 = _read_new_lines(str(jsonl), offset1)
        assert lines1 == []  # not yet complete

        # Complete the line
        with open(str(jsonl), "a") as f:
            f.write("\n")
        lines2, offset3 = _read_new_lines(str(jsonl), offset2)
        assert len(lines2) == 1
        assert "Second" in lines2[0]

    def test_missing_file(self, tmp_path):
        lines, offset = _read_new_lines(str(tmp_path / "nope.jsonl"), 0)
        assert lines == []
        assert offset == 0

    def test_multiple_new_lines(self, tmp_path):
        jsonl = tmp_path / "session.jsonl"
        _write_jsonl(jsonl, [_user_entry("Q1", "u1")])
        _, offset = _read_new_lines(str(jsonl), 0)

        _append_jsonl(jsonl, [
            _assistant_entry("A1", "a1"),
            _user_entry("Q2", "u2"),
            _assistant_entry("A2", "a2"),
        ])
        lines, offset2 = _read_new_lines(str(jsonl), offset)
        assert len(lines) == 3


# ===========================================================================
# 3. sync_parse_incremental — full incremental pipeline
# ===========================================================================

class TestSyncParseIncremental:
    def test_initial_read(self, tmp_path):
        """First call reads all existing content."""
        entries = [
            _user_entry("Hello", "u1"),
            _assistant_entry("Hi", "a1"),
        ]
        ctx = _make_ctx(tmp_path, entries)
        turns = sync_parse_incremental(ctx)
        assert len(turns) == 2
        assert turns[0][0] == "user"
        assert turns[1][0] == "assistant"

    def test_incremental_new_turn(self, tmp_path):
        """After initial read, appending a new turn should be picked up
        without re-reading old data."""
        entries = [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1", "a1"),
        ]
        ctx = _make_ctx(tmp_path, entries)
        turns1 = sync_parse_incremental(ctx)
        assert len(turns1) == 2

        # Append a new exchange
        _append_jsonl(ctx.jsonl_path, [
            _user_entry("Q2", "u2"),
            _assistant_entry("A2", "a2"),
        ])
        turns2 = sync_parse_incremental(ctx)
        assert len(turns2) == 4
        assert turns2[2][0] == "user"
        assert turns2[2][1] == "Q2"
        assert turns2[3][0] == "assistant"

    def test_streaming_assistant_update(self, tmp_path):
        """Assistant turn that grows (multiple entries added incrementally)
        should be correctly reflected."""
        ctx = _make_ctx(tmp_path, [
            _user_entry("Q1", "u1"),
            _assistant_entry("Part1", "a1"),
        ])
        turns1 = sync_parse_incremental(ctx)
        assert len(turns1) == 2

        # More assistant content arrives (same turn)
        _append_jsonl(ctx.jsonl_path, [
            _assistant_entry("Part2", "a2"),
        ])
        turns2 = sync_parse_incremental(ctx)
        assert len(turns2) == 2  # still 2 turns — assistant grew
        assert "Part1" in turns2[1][1]
        assert "Part2" in turns2[1][1]

    def test_matches_full_parse(self, tmp_path):
        """Incremental result should match a full parse of the same file."""
        entries = [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1-part1", "a1"),
            _assistant_entry("A1-part2", "a2"),
            _user_entry("Q2", "u2"),
            _assistant_entry("A2", "a3"),
        ]
        # Build up incrementally
        ctx = _make_ctx(tmp_path, entries[:2])
        sync_parse_incremental(ctx)  # initial

        _append_jsonl(ctx.jsonl_path, entries[2:4])
        sync_parse_incremental(ctx)  # add part2 + Q2

        _append_jsonl(ctx.jsonl_path, entries[4:])
        incremental_turns = sync_parse_incremental(ctx)  # add A2

        # Full parse for comparison
        full_turns = _parse_session_turns(ctx.jsonl_path)
        assert len(incremental_turns) == len(full_turns)
        for inc, full in zip(incremental_turns, full_turns):
            assert inc[0] == full[0], f"Role mismatch: {inc[0]} vs {full[0]}"
            assert inc[1] == full[1], f"Content mismatch: {inc[1][:50]} vs {full[1][:50]}"

    def test_empty_file(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        turns = sync_parse_incremental(ctx)
        assert turns == []

    def test_assistant_only_no_boundary(self, tmp_path):
        """File with only assistant entries (no user boundary) should
        still parse correctly."""
        ctx = _make_ctx(tmp_path, [
            _assistant_entry("Just assistant", "a1"),
        ])
        turns = sync_parse_incremental(ctx)
        assert len(turns) == 1
        assert turns[0][0] == "assistant"

    def test_tool_use_in_assistant(self, tmp_path):
        """Tool use entries within assistant turns should be preserved."""
        ctx = _make_ctx(tmp_path, [
            _user_entry("Read a file", "u1"),
            _assistant_entry("Let me read that", "a1"),
            _tool_entry("Read", {"file_path": "/tmp/test"}, "a2"),
        ])
        turns = sync_parse_incremental(ctx)
        assert len(turns) == 2
        assert turns[1][0] == "assistant"
        # The tool use should appear in the content
        assert "Read" in turns[1][1]

    def test_many_incremental_steps(self, tmp_path):
        """Simulate a realistic multi-step conversation built incrementally."""
        ctx = _make_ctx(tmp_path, [_user_entry("Q1", "u1")])
        sync_parse_incremental(ctx)

        # Step-by-step growth
        for i in range(5):
            _append_jsonl(ctx.jsonl_path, [
                _assistant_entry(f"A{i+1}", f"a{i+1}"),
                _user_entry(f"Q{i+2}", f"u{i+2}"),
            ])
            turns = sync_parse_incremental(ctx)

        # Final assistant
        _append_jsonl(ctx.jsonl_path, [_assistant_entry("Final", "a-final")])
        final_turns = sync_parse_incremental(ctx)

        full_turns = _parse_session_turns(ctx.jsonl_path)
        assert len(final_turns) == len(full_turns)
        for inc, full in zip(final_turns, full_turns):
            assert inc[0] == full[0]
            assert inc[1] == full[1]


# ===========================================================================
# 4. sync_reset_incremental
# ===========================================================================

class TestSyncReset:
    def test_reset_clears_state(self, tmp_path):
        ctx = _make_ctx(tmp_path, [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1", "a1"),
        ])
        sync_parse_incremental(ctx)
        assert ctx.cached_lines  # populated
        assert ctx.last_offset > 0

        sync_reset_incremental(ctx)
        assert ctx.cached_lines == []
        assert ctx.stable_boundary == 0
        assert ctx.stable_turn_count == 0
        assert ctx.incremental_turns == []
        assert ctx.last_offset == 0

    def test_reset_then_reparse(self, tmp_path):
        """After reset, next parse should re-read from start and match
        a full parse."""
        entries = [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1", "a1"),
        ]
        ctx = _make_ctx(tmp_path, entries)
        sync_parse_incremental(ctx)
        sync_reset_incremental(ctx)

        # Re-parse from scratch
        turns = sync_parse_incremental(ctx)
        full = _parse_session_turns(ctx.jsonl_path)
        assert len(turns) == len(full)


# ===========================================================================
# 5. Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_queue_operation(self, tmp_path):
        """Queue operations should be handled correctly in incremental mode."""
        ctx = _make_ctx(tmp_path, [
            _user_entry("Q1", "u1"),
            _assistant_entry("Working...", "a1"),
        ])
        sync_parse_incremental(ctx)

        _append_jsonl(ctx.jsonl_path, [
            {"type": "queue-operation", "operation": "enqueue",
             "content": "Follow up", "sessionId": "s1"},
        ])
        turns = sync_parse_incremental(ctx)
        full = _parse_session_turns(ctx.jsonl_path)
        assert len(turns) == len(full)

    def test_system_entry(self, tmp_path):
        """System entries (compact summary) should be handled correctly."""
        ctx = _make_ctx(tmp_path, [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1", "a1"),
        ])
        sync_parse_incremental(ctx)

        _append_jsonl(ctx.jsonl_path, [
            {"type": "system", "subtype": "init", "content": "session started",
             "sessionId": "s1"},
            _user_entry("Q2", "u2"),
        ])
        turns = sync_parse_incremental(ctx)
        full = _parse_session_turns(ctx.jsonl_path)
        assert len(turns) == len(full)

    def test_concurrent_growth(self, tmp_path):
        """Simulate rapid growth — multiple entries between reads."""
        ctx = _make_ctx(tmp_path, [_user_entry("Q1", "u1")])
        sync_parse_incremental(ctx)

        # Burst of entries all at once
        _append_jsonl(ctx.jsonl_path, [
            _assistant_entry("A1-p1", "a1"),
            _assistant_entry("A1-p2", "a2"),
            _tool_entry("Bash", {"command": "ls"}),
            _assistant_entry("A1-p3", "a3"),
            _user_entry("Q2", "u2"),
            _assistant_entry("A2", "a4"),
        ])
        turns = sync_parse_incremental(ctx)
        full = _parse_session_turns(ctx.jsonl_path)
        assert len(turns) == len(full)
        for inc, ful in zip(turns, full):
            assert inc[0] == ful[0]
            assert inc[1] == ful[1]

    def test_stable_boundary_advances(self, tmp_path):
        """The stable_boundary should advance as new user entries arrive,
        reducing the amount of re-parsing needed."""
        ctx = _make_ctx(tmp_path, [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1", "a1"),
        ])
        sync_parse_incremental(ctx)
        boundary_after_init = ctx.stable_boundary

        _append_jsonl(ctx.jsonl_path, [
            _user_entry("Q2", "u2"),
            _assistant_entry("A2", "a2"),
        ])
        sync_parse_incremental(ctx)
        boundary_after_q2 = ctx.stable_boundary

        # Boundary should have advanced since a new user entry appeared
        assert boundary_after_q2 > boundary_after_init

    def test_stable_turn_count_accuracy(self, tmp_path):
        """stable_turn_count should accurately count turns before the boundary."""
        ctx = _make_ctx(tmp_path, [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1", "a1"),
            _user_entry("Q2", "u2"),
            _assistant_entry("A2", "a2"),
            _user_entry("Q3", "u3"),
        ])
        sync_parse_incremental(ctx)
        # Last user entry is Q3 — stable turns before Q3 boundary = Q1+A1+Q2+A2 = 4
        assert ctx.stable_turn_count == 4

    def test_no_duplication_after_init_with_preloaded_state(self, tmp_path):
        """Simulate the real sync loop init pattern: cached_lines and
        incremental_turns are pre-populated, then sync_parse_incremental
        is called when new data arrives.  Must NOT duplicate turns.

        Regression test for: stable_turn_count set to full count at init
        while stable_boundary stays 0 → splice duplicates all turns."""
        entries = [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1", "a1"),
        ]
        ctx = _make_ctx(tmp_path, entries)
        # Simulate what _sync_session_loop_inner does at init:
        # - cached_lines populated from file
        # - incremental_turns populated from full parse
        # - last_turn_count = full count
        # - stable_turn_count stays 0 (default)
        ctx.last_offset = os.path.getsize(ctx.jsonl_path)
        ctx.incremental_turns = list(_parse_session_turns(ctx.jsonl_path))
        ctx.last_turn_count = len(ctx.incremental_turns)
        with open(ctx.jsonl_path, "r") as f:
            for raw in f:
                s = raw.strip()
                if s:
                    ctx.cached_lines.append(s)

        # Now new assistant data arrives (same turn grows)
        _append_jsonl(ctx.jsonl_path, [_assistant_entry("A1-part2", "a1b")])
        turns = sync_parse_incremental(ctx)
        full = _parse_session_turns(ctx.jsonl_path)
        # Must match full parse — no duplicates
        assert len(turns) == len(full), (
            f"Duplication! incremental={len(turns)} vs full={len(full)}"
        )
        assert turns[0][0] == "user"
        assert turns[1][0] == "assistant"

    def test_no_duplication_new_user_after_init(self, tmp_path):
        """After init, a new user entry arrives. Must not duplicate
        existing turns."""
        entries = [
            _user_entry("Q1", "u1"),
            _assistant_entry("A1", "a1"),
        ]
        ctx = _make_ctx(tmp_path, entries)
        ctx.last_offset = os.path.getsize(ctx.jsonl_path)
        ctx.incremental_turns = list(_parse_session_turns(ctx.jsonl_path))
        ctx.last_turn_count = len(ctx.incremental_turns)
        with open(ctx.jsonl_path, "r") as f:
            for raw in f:
                s = raw.strip()
                if s:
                    ctx.cached_lines.append(s)

        _append_jsonl(ctx.jsonl_path, [
            _user_entry("Q2", "u2"),
            _assistant_entry("A2", "a2"),
        ])
        turns = sync_parse_incremental(ctx)
        full = _parse_session_turns(ctx.jsonl_path)
        assert len(turns) == len(full), (
            f"Duplication! incremental={len(turns)} vs full={len(full)}"
        )
        # Verify ordering: user bubbles separate assistant bubbles
        assert turns[0] == ("user", "Q1", None, "u1")
        assert turns[1][0] == "assistant"
        assert turns[2] == ("user", "Q2", None, "u2")
        assert turns[3][0] == "assistant"
