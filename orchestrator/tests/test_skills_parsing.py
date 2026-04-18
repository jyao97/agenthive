"""Tests for skill folding in jsonl_parser + decoupled skills module."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jsonl_parser import format_tool_summary, parse_session_turns_from_lines
from skills import (
    BUNDLED_SKILLS,
    format_skill_summary,
    is_hidden_meta_entry,
    skill_turn_metadata,
)


# ---------------------------------------------------------------------------
# skills.py — pure helpers
# ---------------------------------------------------------------------------

class TestSkillHelpers:
    def test_format_skill_summary_with_name(self):
        assert format_skill_summary({"skill": "debug"}) == "> `Skill` debug"

    def test_format_skill_summary_missing_name(self):
        assert format_skill_summary({}) == "> `Skill` "

    def test_skill_turn_metadata(self):
        assert skill_turn_metadata({"skill": "loop"}) == {"skill_name": "loop"}

    def test_skill_turn_metadata_missing(self):
        assert skill_turn_metadata({}) == {"skill_name": ""}

    def test_is_hidden_meta_entry_true(self):
        assert is_hidden_meta_entry({"isMeta": True}) is True

    def test_is_hidden_meta_entry_false(self):
        assert is_hidden_meta_entry({"isMeta": False}) is False

    def test_is_hidden_meta_entry_missing(self):
        assert is_hidden_meta_entry({}) is False

    def test_bundled_skills_have_name_and_description(self):
        assert len(BUNDLED_SKILLS) > 0
        for s in BUNDLED_SKILLS:
            assert s["name"] and isinstance(s["name"], str)
            assert "description" in s


# ---------------------------------------------------------------------------
# format_tool_summary integration
# ---------------------------------------------------------------------------

class TestFormatToolSummarySkill:
    def test_skill_routes_through_helper(self):
        assert format_tool_summary("Skill", {"skill": "simplify"}) == "> `Skill` simplify"


# ---------------------------------------------------------------------------
# parse_session_turns_from_lines — folding behavior
# ---------------------------------------------------------------------------

def _line(entry: dict) -> str:
    return json.dumps(entry) + "\n"


class TestSkillFolding:
    def test_skill_tool_use_emits_one_turn_with_skill_name(self):
        """Skill tool_use becomes a single assistant turn carrying skill_name."""
        lines = [
            _line({
                "type": "user",
                "uuid": "u1",
                "timestamp": "2026-04-18T00:00:00Z",
                "message": {"role": "user", "content": "/debug"},
            }),
            _line({
                "type": "assistant",
                "uuid": "a1",
                "timestamp": "2026-04-18T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Skill",
                        "input": {"skill": "debug"},
                    }],
                },
            }),
        ]
        turns = parse_session_turns_from_lines(lines)
        skill_turns = [t for t in turns if t[2] and t[2].get("tool_name") == "Skill"]
        assert len(skill_turns) == 1
        role, content, meta, _uuid, kind, _ts = skill_turns[0]
        assert role == "assistant"
        assert content == "> `Skill` debug"
        assert meta["skill_name"] == "debug"
        assert kind == "tool_use"

    def test_ismeta_user_entries_dropped(self):
        """isMeta:true user entries (skill bodies, system reminders) are filtered out."""
        lines = [
            _line({
                "type": "user",
                "uuid": "u1",
                "timestamp": "2026-04-18T00:00:00Z",
                "message": {"role": "user", "content": "real user message"},
            }),
            _line({
                "type": "user",
                "uuid": "u2",
                "isMeta": True,
                "timestamp": "2026-04-18T00:00:01Z",
                "message": {"role": "user", "content": "<<SKILL BODY>>"},
            }),
            _line({
                "type": "user",
                "uuid": "u3",
                "timestamp": "2026-04-18T00:00:02Z",
                "message": {"role": "user", "content": "second real message"},
            }),
        ]
        turns = parse_session_turns_from_lines(lines)
        contents = [t[1] for t in turns if t[0] == "user"]
        assert "<<SKILL BODY>>" not in contents
        assert "real user message" in contents
        assert "second real message" in contents
