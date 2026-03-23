"""Reusable test infrastructure for deterministic sync engine testing.

Provides JSONL entry builders, FakeClaudeSession, MockDispatcher,
hook simulation functions, and invariant checkers.
"""

import json
import uuid as _uuid_mod
from pathlib import Path

from models import Message, MessageRole, MessageStatus, ToolActivity


# ---------------------------------------------------------------------------
# 1. JSONL Entry Builders
# ---------------------------------------------------------------------------

def user_entry(content, uuid=None):
    """Build a JSONL user entry matching _parse_session_turns_from_lines format."""
    e = {
        "type": "user",
        "message": {"role": "user", "content": content},
        "sessionId": "s1",
    }
    if uuid:
        e["uuid"] = uuid
    return e


def assistant_entry(text, uuid=None):
    """Build a JSONL assistant entry with a text content block."""
    e = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
        "sessionId": "s1",
    }
    if uuid:
        e["uuid"] = uuid
    return e


def tool_use_entry(tool_name, tool_input, tool_use_id, uuid=None):
    """Build a JSONL assistant entry with a tool_use content block."""
    e = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": tool_name,
                    "input": tool_input,
                    "id": tool_use_id,
                }
            ]
        },
        "sessionId": "s1",
    }
    if uuid:
        e["uuid"] = uuid
    return e


def tool_result_entry(tool_use_id, result):
    """Build a JSONL user entry with a tool_result content block."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result,
                }
            ],
        },
        "sessionId": "s1",
    }


def system_entry(subtype, content):
    """Build a JSONL system entry."""
    return {
        "type": "system",
        "subtype": subtype,
        "content": content,
        "sessionId": "s1",
    }


def queue_op_entry(content, operation="enqueue"):
    """Build a JSONL queue-operation entry."""
    return {
        "type": "queue-operation",
        "operation": operation,
        "content": content,
        "sessionId": "s1",
    }


# ---------------------------------------------------------------------------
# 2. FakeClaudeSession
# ---------------------------------------------------------------------------

class FakeClaudeSession:
    """Manages a temp JSONL file for deterministic sync testing."""

    def __init__(self, tmp_path, agent_id, session_id="test-session"):
        self.jsonl_path = str(tmp_path / f"{session_id}.jsonl")
        self.agent_id = agent_id
        self.session_id = session_id
        Path(self.jsonl_path).touch()

    def append(self, *entries):
        """Append JSONL entries to the file."""
        with open(self.jsonl_path, "a") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def write_user(self, content, uuid=None):
        self.append(user_entry(content, uuid))

    def write_assistant(self, text, uuid=None):
        self.append(assistant_entry(text, uuid))

    def write_tool_use(self, tool_name, tool_input, tool_use_id, uuid=None):
        self.append(tool_use_entry(tool_name, tool_input, tool_use_id, uuid))

    def write_tool_result(self, tool_use_id, result):
        self.append(tool_result_entry(tool_use_id, result))

    def write_system(self, subtype, content):
        self.append(system_entry(subtype, content))

    def write_queue_op(self, content, operation="enqueue"):
        self.append(queue_op_entry(content, operation))

    def compact(self, new_entries):
        """Rewrite JSONL with compacted content (simulates /compact)."""
        with open(self.jsonl_path, "w") as f:
            for entry in new_entries:
                f.write(json.dumps(entry) + "\n")

    def make_sync_context(self):
        """Create a SyncContext wired to this session's JSONL file."""
        from sync_engine import SyncContext
        return SyncContext(
            agent_id=self.agent_id,
            session_id=self.session_id,
            project_path="/tmp/test-project",
            jsonl_path=self.jsonl_path,
        )


# ---------------------------------------------------------------------------
# 3. MockDispatcher
# ---------------------------------------------------------------------------

class MockDispatcher:
    """Duck-type dispatcher providing what sync_engine functions need from `ad`.

    sync_engine calls ad._emit(coroutine) where coroutine is an awaitable
    returned by emit_* functions.  The real dispatcher wraps these with
    asyncio.ensure_future.  Here we just capture them and discard.
    """

    def __init__(self):
        self._generating_agents = set()
        self._generation_ids = {}
        self._sync_contexts = {}
        self._sync_wake = {}
        self._emitted = []

    def _emit(self, coro_or_dict):
        """Capture emitted events.  Close coroutines to avoid warnings."""
        self._emitted.append(coro_or_dict)
        # Close the coroutine to avoid RuntimeWarning
        if hasattr(coro_or_dict, "close"):
            try:
                coro_or_dict.close()
            except Exception:
                pass

    def _start_generating(self, agent_id):
        gid = self._next_generation_id(agent_id)
        self._generating_agents.add(agent_id)
        return gid

    def _stop_generating(self, agent_id):
        self._generating_agents.discard(agent_id)

    def _is_agent_in_use(self, agent_id, tmux_pane=None):
        return False

    async def _maybe_notify_message(self, agent):
        pass

    def _next_generation_id(self, agent_id):
        gid = self._generation_ids.get(agent_id, 0) + 1
        self._generation_ids[agent_id] = gid
        return gid

    def wake_sync(self, agent_id):
        ev = self._sync_wake.get(agent_id)
        if ev:
            ev.set()
            return True
        return False


# ---------------------------------------------------------------------------
# 4. Hook Simulation Functions
# ---------------------------------------------------------------------------

def simulate_pre_tool_use_hook(db, agent_id, tool_name, tool_use_id, tool_input):
    """Mirror hooks.py:394-449 — create hook-created interactive card.

    Creates a Message with jsonl_uuid=f"hook-{tool_use_id}" and meta_json
    containing the interactive item, exactly as the PreToolUse hook does.
    """
    from utils import utcnow as _utcnow

    if tool_name == "AskUserQuestion":
        item = {
            "type": "ask_user_question",
            "tool_use_id": tool_use_id,
            "questions": tool_input.get("questions", []),
            "answer": None,
        }
    elif tool_name == "ExitPlanMode":
        item = {
            "type": "exit_plan_mode",
            "tool_use_id": tool_use_id,
            "allowedPrompts": tool_input.get("allowedPrompts", []),
            "plan": tool_input.get("plan", ""),
            "answer": None,
        }
    else:
        return None

    meta = json.dumps({"interactive": [item]})
    now = _utcnow()
    msg = Message(
        agent_id=agent_id,
        role=MessageRole.AGENT,
        content="",
        status=MessageStatus.COMPLETED,
        source="cli",
        meta_json=meta,
        jsonl_uuid=f"hook-{tool_use_id}",
        completed_at=now,
        delivered_at=now,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def simulate_post_tool_use_hook(db, agent_id, tool_use_id, answer):
    """Mirror hooks.py:466-499 — backfill answer on interactive card.

    Finds message(s) with tool_use_id in meta_json, patches answer.
    """
    msgs = (
        db.query(Message)
        .filter(
            Message.agent_id == agent_id,
            Message.meta_json.isnot(None),
        )
        .all()
    )
    patched = False
    for msg in msgs:
        try:
            meta = json.loads(msg.meta_json)
        except (json.JSONDecodeError, TypeError):
            continue
        msg_changed = False
        for item in meta.get("interactive", []):
            if item.get("tool_use_id") != tool_use_id:
                continue
            if item.get("answer") is not None:
                continue
            item["answer"] = str(answer)[:500]
            msg_changed = True
        if msg_changed:
            msg.meta_json = json.dumps(meta)
            patched = True
    if patched:
        db.commit()
    return patched


def simulate_user_prompt_hook(db, agent_id, ad):
    """Mirror hooks.py:117-163 — mark delivery + start generating.

    Marks the oldest undelivered web-sent message as delivered and
    starts generating state on the dispatcher.
    """
    from utils import utcnow as _utcnow

    msg = (
        db.query(Message)
        .filter(
            Message.agent_id == agent_id,
            Message.role == MessageRole.USER,
            Message.source.in_(("web", "task", "plan_continue")),
            Message.delivered_at.is_(None),
        )
        .order_by(Message.created_at.asc())
        .first()
    )
    if msg:
        msg.delivered_at = _utcnow()
        db.commit()
    ad._start_generating(agent_id)


def simulate_stop_hook(ad, agent_id):
    """Mirror hooks.py:203-207 — clear generating."""
    ad._stop_generating(agent_id)


# ---------------------------------------------------------------------------
# 5. Invariant Checker
# ---------------------------------------------------------------------------

def check_invariants(db, agent_id):
    """Universal invariants that must hold after every sync operation."""
    msgs = db.query(Message).filter(Message.agent_id == agent_id).all()

    # 1. No duplicate jsonl_uuids (excluding hook- prefix and None)
    real_uuids = [
        m.jsonl_uuid for m in msgs
        if m.jsonl_uuid and not m.jsonl_uuid.startswith("hook-")
    ]
    dupes = [u for u in real_uuids if real_uuids.count(u) > 1]
    assert len(real_uuids) == len(set(real_uuids)), (
        f"Duplicate jsonl_uuids: {dupes}"
    )

    # 2. No duplicate tool_use_id interactive cards
    tids = []
    for m in msgs:
        if m.meta_json:
            try:
                meta = json.loads(m.meta_json)
            except (json.JSONDecodeError, TypeError):
                continue
            for item in meta.get("interactive", []):
                tid = item.get("tool_use_id")
                if tid:
                    tids.append(tid)
    tid_dupes = [t for t in tids if tids.count(t) > 1]
    assert len(tids) == len(set(tids)), (
        f"Duplicate tool_use_ids: {tid_dupes}"
    )

    # 3. No empty-content agent messages without interactive metadata
    for m in msgs:
        if m.role == MessageRole.AGENT and (not m.content or not m.content.strip()):
            assert m.meta_json is not None, (
                f"Empty agent message {m.id} without metadata"
            )
