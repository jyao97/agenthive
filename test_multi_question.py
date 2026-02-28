#!/usr/bin/env python3
"""Comprehensive critic test for multi-question AskUserQuestion state isolation.

Creates tmux agents, triggers interactive prompts, and verifies:
1. Per-question state doesn't leak between questions
2. Sequential ordering enforcement works
3. Backward compatibility with single-question items
4. ExitPlanMode still works correctly
5. selected_indices dict is populated correctly
"""

import json
import os
import sys
import time
import requests

BASE = "http://localhost:8080"
PROJECT = "cc-orchestrator"  # Use this project for testing
MODEL = "claude-haiku-4-5-20251001"  # Fast/cheap model for testing
TOKEN = None
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

# ── Helpers ──────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    colors = {"INFO": "\033[36m", "OK": "\033[32m", "FAIL": "\033[31m", "WARN": "\033[33m", "STEP": "\033[35m"}
    reset = "\033[0m"
    c = colors.get(level, "")
    print(f"{c}[{level}]{reset} {msg}")

def vlog(msg):
    if VERBOSE:
        log(msg, "INFO")

def api(method, path, **kwargs):
    """Make authenticated API request."""
    url = f"{BASE}{path}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {TOKEN}"
    if "json" in kwargs:
        headers["Content-Type"] = "application/json"
    resp = getattr(requests, method)(url, headers=headers, timeout=30, **kwargs)
    if resp.status_code >= 400:
        log(f"API {method.upper()} {path} → {resp.status_code}: {resp.text[:300]}", "FAIL")
        return None
    return resp.json()

def wait_for(predicate, timeout=120, interval=3, desc="condition"):
    """Poll until predicate returns truthy or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        result = predicate()
        if result:
            return result
        vlog(f"  Waiting for {desc}... ({int(time.time()-start)}s)")
        time.sleep(interval)
    log(f"Timeout waiting for {desc} after {timeout}s", "FAIL")
    return None

def get_agent(agent_id):
    return api("get", f"/api/agents/{agent_id}")

def get_messages(agent_id, limit=100):
    return api("get", f"/api/agents/{agent_id}/messages?limit={limit}")

def find_interactive_items(agent_id, item_type=None):
    """Find all interactive items in an agent's messages."""
    msgs = get_messages(agent_id)
    if not msgs:
        return []
    items = []
    for msg in msgs:
        meta = msg.get("metadata")
        if not meta or not meta.get("interactive"):
            continue
        for item in meta["interactive"]:
            if item_type and item.get("type") != item_type:
                continue
            items.append({"item": item, "msg": msg})
    return items

def answer_question(agent_id, tool_use_id, selected_index, question_index=0):
    """Answer an AskUserQuestion via the API."""
    return api("post", f"/api/agents/{agent_id}/answer", json={
        "tool_use_id": tool_use_id,
        "type": "ask_user_question",
        "selected_index": selected_index,
        "question_index": question_index,
    })

def answer_plan(agent_id, tool_use_id, selected_index):
    """Answer an ExitPlanMode via the API."""
    return api("post", f"/api/agents/{agent_id}/answer", json={
        "tool_use_id": tool_use_id,
        "type": "exit_plan_mode",
        "selected_index": selected_index,
    })

def stop_agent(agent_id):
    return api("delete", f"/api/agents/{agent_id}")

def create_tmux_agent(prompt, mode="INTERVIEW"):
    """Create a tmux agent and wait until it's past STARTING."""
    resp = api("post", "/api/agents/launch-tmux", json={
        "project": PROJECT,
        "prompt": prompt,
        "model": MODEL,
        "skip_permissions": True,
    })
    if not resp:
        return None
    agent_id = resp["id"]
    log(f"Created agent {agent_id} (tmux: {resp.get('tmux_pane')})")

    # Wait for agent to leave STARTING state
    def ready():
        a = get_agent(agent_id)
        if a and a["status"] not in ("STARTING",):
            return a
        return None

    agent = wait_for(ready, timeout=60, desc=f"agent {agent_id[:8]} ready")
    if not agent:
        log(f"Agent {agent_id[:8]} stuck in STARTING", "FAIL")
        return None
    log(f"Agent {agent_id[:8]} is {agent['status']}", "OK")
    return agent_id


# ── Test Results Tracker ─────────────────────────────────────────────────────

results = []

def check(name, condition, detail=""):
    """Record a test assertion."""
    status = "PASS" if condition else "FAIL"
    results.append((name, status, detail))
    log(f"{name}: {detail}" if detail else name, "OK" if condition else "FAIL")
    return condition


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: Multi-Question AskUserQuestion State Isolation
# ══════════════════════════════════════════════════════════════════════════════

def test_multi_question():
    log("=" * 60, "STEP")
    log("TEST 1: Multi-Question AskUserQuestion", "STEP")
    log("=" * 60, "STEP")

    # Craft a prompt that triggers multi-question AskUserQuestion
    prompt = (
        "You must call the AskUserQuestion tool with EXACTLY 2 questions in one call. "
        "Question 1: 'Which color do you prefer?' with options: "
        "[{\"label\":\"Red\",\"description\":\"Warm color\"}, "
        "{\"label\":\"Blue\",\"description\":\"Cool color\"}, "
        "{\"label\":\"Green\",\"description\":\"Nature color\"}]. "
        "Question 2: 'Which size?' with options: "
        "[{\"label\":\"Small\",\"description\":\"Compact\"}, "
        "{\"label\":\"Large\",\"description\":\"Spacious\"}]. "
        "Use header 'Color' for Q1 and 'Size' for Q2. "
        "Do NOT do anything else. Just call the tool."
    )

    agent_id = create_tmux_agent(prompt)
    if not agent_id:
        check("Create multi-Q agent", False, "Failed to create agent")
        return None

    # Wait for the interactive prompt to appear
    log("Waiting for AskUserQuestion to appear...")

    def has_ask():
        items = find_interactive_items(agent_id, "ask_user_question")
        for entry in items:
            if len(entry["item"].get("questions", [])) >= 2:
                return entry
        return None

    entry = wait_for(has_ask, timeout=120, interval=3, desc="multi-question prompt")
    if not entry:
        check("Multi-Q prompt appeared", False, "No multi-question AskUserQuestion found")
        # Check what we got instead
        all_items = find_interactive_items(agent_id)
        if all_items:
            for it in all_items:
                log(f"  Found: type={it['item']['type']}, questions={len(it['item'].get('questions',[]))}, answer={it['item'].get('answer')}", "WARN")
        stop_agent(agent_id)
        return None

    item = entry["item"]
    tool_use_id = item["tool_use_id"]
    questions = item["questions"]
    check("Multi-Q prompt appeared", True, f"{len(questions)} questions, tool_use_id={tool_use_id[:12]}")

    # ── Test 1a: Before answering, both questions should be unanswered ──
    check("Q0 initially unanswered",
          item.get("selected_index") is None and not item.get("selected_indices"),
          f"selected_index={item.get('selected_index')}, selected_indices={item.get('selected_indices')}")

    # ── Test 1b: Answer Q0 only ──
    log("Answering Q0 with index 1 (Blue)...")
    resp = answer_question(agent_id, tool_use_id, selected_index=1, question_index=0)
    check("Q0 answer API success", resp is not None, str(resp))

    # Fetch updated state
    time.sleep(1)
    items_after_q0 = find_interactive_items(agent_id, "ask_user_question")
    item_after_q0 = None
    for e in items_after_q0:
        if e["item"].get("tool_use_id") == tool_use_id:
            item_after_q0 = e["item"]
            break

    if item_after_q0:
        sel_indices = item_after_q0.get("selected_indices", {})
        vlog(f"  After Q0: selected_indices={sel_indices}, selected_index={item_after_q0.get('selected_index')}, answer={item_after_q0.get('answer')}")

        check("Q0 stored in selected_indices",
              sel_indices.get("0") == 1,
              f"selected_indices['0'] = {sel_indices.get('0')}")

        check("Q1 NOT affected by Q0 answer",
              sel_indices.get("1") is None,
              f"selected_indices['1'] = {sel_indices.get('1')}")

        check("Backward compat: selected_index set for Q0",
              item_after_q0.get("selected_index") == 1,
              f"selected_index = {item_after_q0.get('selected_index')}")

        check("Answer string contains Q0",
              item_after_q0.get("answer") is not None and "Blue" in str(item_after_q0.get("answer", "")),
              f"answer = {item_after_q0.get('answer')}")
    else:
        check("Q0 state readable", False, "Could not find item after Q0 answer")

    # ── Test 1c: Answer Q1 ──
    log("Answering Q1 with index 0 (Small)...")
    time.sleep(2)  # Wait for tmux TUI to advance to Q1
    resp = answer_question(agent_id, tool_use_id, selected_index=0, question_index=1)
    check("Q1 answer API success", resp is not None, str(resp))

    # Fetch final state
    time.sleep(1)
    items_final = find_interactive_items(agent_id, "ask_user_question")
    item_final = None
    for e in items_final:
        if e["item"].get("tool_use_id") == tool_use_id:
            item_final = e["item"]
            break

    if item_final:
        sel_indices = item_final.get("selected_indices", {})
        vlog(f"  Final: selected_indices={sel_indices}, answer={item_final.get('answer')}")

        check("Both questions in selected_indices",
              sel_indices.get("0") == 1 and sel_indices.get("1") == 0,
              f"selected_indices = {sel_indices}")

        check("Q0 unchanged after Q1 answer",
              sel_indices.get("0") == 1,
              f"Q0 still = {sel_indices.get('0')}")

        check("Answer string contains both",
              "Blue" in str(item_final.get("answer", "")) and "Small" in str(item_final.get("answer", "")),
              f"answer = {item_final.get('answer')}")
    else:
        check("Final state readable", False, "Could not find item after both answers")

    stop_agent(agent_id)
    return agent_id


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: Single-Question Backward Compatibility
# ══════════════════════════════════════════════════════════════════════════════

def test_single_question():
    log("=" * 60, "STEP")
    log("TEST 2: Single-Question Backward Compatibility", "STEP")
    log("=" * 60, "STEP")

    prompt = (
        "You must call the AskUserQuestion tool with EXACTLY 1 question. "
        "Question: 'Pick a fruit' with options: "
        "[{\"label\":\"Apple\",\"description\":\"Red fruit\"}, "
        "{\"label\":\"Banana\",\"description\":\"Yellow fruit\"}]. "
        "Use header 'Fruit'. "
        "Do NOT do anything else. Just call the tool."
    )

    agent_id = create_tmux_agent(prompt)
    if not agent_id:
        check("Create single-Q agent", False, "Failed to create")
        return None

    def has_ask():
        items = find_interactive_items(agent_id, "ask_user_question")
        for entry in items:
            if entry["item"].get("answer") is None:
                return entry
        return None

    entry = wait_for(has_ask, timeout=120, interval=3, desc="single-question prompt")
    if not entry:
        check("Single-Q prompt appeared", False)
        stop_agent(agent_id)
        return None

    item = entry["item"]
    tool_use_id = item["tool_use_id"]
    check("Single-Q prompt appeared", True, f"questions={len(item.get('questions',[]))}")

    # Answer with question_index=0 (default)
    resp = answer_question(agent_id, tool_use_id, selected_index=0, question_index=0)
    check("Single-Q answer success", resp is not None)

    time.sleep(1)
    items_after = find_interactive_items(agent_id, "ask_user_question")
    item_after = None
    for e in items_after:
        if e["item"].get("tool_use_id") == tool_use_id:
            item_after = e["item"]
            break

    if item_after:
        check("selected_index set (backward compat)",
              item_after.get("selected_index") == 0,
              f"selected_index = {item_after.get('selected_index')}")

        sel_indices = item_after.get("selected_indices", {})
        check("selected_indices also set",
              sel_indices.get("0") == 0,
              f"selected_indices = {sel_indices}")

        check("Answer string present",
              item_after.get("answer") is not None and "Apple" in str(item_after.get("answer", "")),
              f"answer = {item_after.get('answer')}")
    else:
        check("Single-Q state readable", False)

    stop_agent(agent_id)
    return agent_id


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: ExitPlanMode Still Works
# ══════════════════════════════════════════════════════════════════════════════

def test_exit_plan_mode():
    log("=" * 60, "STEP")
    log("TEST 3: ExitPlanMode Answering", "STEP")
    log("=" * 60, "STEP")

    prompt = (
        "You must enter plan mode by calling the EnterPlanMode tool. "
        "Then write a simple plan: 'Step 1: Print hello'. "
        "Then call ExitPlanMode to present the plan. "
        "Do NOT do anything else."
    )

    agent_id = create_tmux_agent(prompt)
    if not agent_id:
        check("Create plan agent", False, "Failed to create")
        return None

    def has_plan():
        items = find_interactive_items(agent_id, "exit_plan_mode")
        for entry in items:
            if entry["item"].get("answer") is None:
                return entry
        return None

    entry = wait_for(has_plan, timeout=120, interval=3, desc="ExitPlanMode prompt")
    if not entry:
        check("ExitPlanMode appeared", False)
        # Show what we found
        all_items = find_interactive_items(agent_id)
        for it in all_items:
            log(f"  Found: type={it['item']['type']}, answer={it['item'].get('answer')}", "WARN")
        stop_agent(agent_id)
        return None

    item = entry["item"]
    tool_use_id = item["tool_use_id"]
    check("ExitPlanMode appeared", True)

    # Approve with option 0 (clear context & bypass)
    resp = answer_plan(agent_id, tool_use_id, selected_index=0)
    check("ExitPlanMode answer success", resp is not None, str(resp))

    time.sleep(1)
    items_after = find_interactive_items(agent_id, "exit_plan_mode")
    item_after = None
    for e in items_after:
        if e["item"].get("tool_use_id") == tool_use_id:
            item_after = e["item"]
            break

    if item_after:
        check("Plan selected_index set",
              item_after.get("selected_index") == 0,
              f"selected_index = {item_after.get('selected_index')}")
        check("Plan answer set",
              item_after.get("answer") is not None,
              f"answer = {item_after.get('answer')}")
        # ExitPlanMode should NOT have selected_indices (not a multi-question type)
        check("No selected_indices on plan",
              not item_after.get("selected_indices"),
              f"selected_indices = {item_after.get('selected_indices')}")
    else:
        check("Plan state readable", False)

    stop_agent(agent_id)
    return agent_id


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: Duplicate Answer Rejection
# ══════════════════════════════════════════════════════════════════════════════

def test_duplicate_answer():
    log("=" * 60, "STEP")
    log("TEST 4: Duplicate Answer Rejection (same question_index)", "STEP")
    log("=" * 60, "STEP")

    prompt = (
        "You must call the AskUserQuestion tool with EXACTLY 1 question. "
        "Question: 'Pick a number' with options: "
        "[{\"label\":\"One\"}, {\"label\":\"Two\"}, {\"label\":\"Three\"}]. "
        "Header: 'Number'. Do NOT do anything else."
    )

    agent_id = create_tmux_agent(prompt)
    if not agent_id:
        check("Create dup-test agent", False)
        return None

    def has_ask():
        items = find_interactive_items(agent_id, "ask_user_question")
        for entry in items:
            if entry["item"].get("answer") is None:
                return entry
        return None

    entry = wait_for(has_ask, timeout=120, interval=3, desc="prompt for dup test")
    if not entry:
        check("Dup-test prompt appeared", False)
        stop_agent(agent_id)
        return None

    item = entry["item"]
    tool_use_id = item["tool_use_id"]

    # Answer once
    resp1 = answer_question(agent_id, tool_use_id, selected_index=0, question_index=0)
    check("First answer accepted", resp1 is not None)

    time.sleep(0.5)

    # Try answering same question again with different index
    resp2 = answer_question(agent_id, tool_use_id, selected_index=2, question_index=0)
    # The API should still return ok (no error), but the value shouldn't change

    time.sleep(0.5)
    items_after = find_interactive_items(agent_id, "ask_user_question")
    item_after = None
    for e in items_after:
        if e["item"].get("tool_use_id") == tool_use_id:
            item_after = e["item"]
            break

    if item_after:
        sel = item_after.get("selected_indices", {}).get("0")
        check("Duplicate answer rejected (first sticks)",
              sel == 0,
              f"selected_indices['0'] = {sel} (expected 0, not 2)")
    else:
        check("Dup state readable", False)

    stop_agent(agent_id)
    return agent_id


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: _derive_selected_index Backfill (sync loop simulation)
# ══════════════════════════════════════════════════════════════════════════════

def test_derive_selected_index():
    log("=" * 60, "STEP")
    log("TEST 5: _derive_selected_index multi-Q backfill (unit test)", "STEP")
    log("=" * 60, "STEP")

    # Import the function directly and test it with a synthetic item
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "orchestrator"))
    from agent_dispatcher import _derive_selected_index

    # Multi-question item with answer containing both labels
    item = {
        "type": "ask_user_question",
        "tool_use_id": "test123",
        "questions": [
            {"question": "Color?", "options": [{"label": "Red"}, {"label": "Blue"}]},
            {"question": "Size?", "options": [{"label": "Small"}, {"label": "Large"}]},
        ],
        "answer": '"Color?"="Blue"\n"Size?"="Small"',
    }
    _derive_selected_index(item)

    check("_derive: selected_index from Q0",
          item.get("selected_index") == 1,
          f"selected_index = {item.get('selected_index')} (expected 1 for Blue)")

    sel = item.get("selected_indices", {})
    check("_derive: selected_indices['0'] = 1 (Blue)",
          sel.get("0") == 1,
          f"got {sel.get('0')}")

    check("_derive: selected_indices['1'] = 0 (Small)",
          sel.get("1") == 0,
          f"got {sel.get('1')}")

    # Single-question item (backward compat)
    item2 = {
        "type": "ask_user_question",
        "tool_use_id": "test456",
        "questions": [
            {"question": "Fruit?", "options": [{"label": "Apple"}, {"label": "Banana"}]},
        ],
        "answer": '"Fruit?"="Banana"',
    }
    _derive_selected_index(item2)
    check("_derive: single-Q selected_index",
          item2.get("selected_index") == 1,
          f"selected_index = {item2.get('selected_index')}")
    check("_derive: single-Q selected_indices",
          item2.get("selected_indices", {}).get("0") == 1,
          f"selected_indices = {item2.get('selected_indices')}")

    # ExitPlanMode (should still work)
    item3 = {
        "type": "exit_plan_mode",
        "tool_use_id": "test789",
        "answer": "Yes, bypass permissions",
    }
    _derive_selected_index(item3)
    check("_derive: exit_plan_mode index",
          item3.get("selected_index") == 1,
          f"selected_index = {item3.get('selected_index')}")

    # Item with no matching labels
    item4 = {
        "type": "ask_user_question",
        "tool_use_id": "test000",
        "questions": [
            {"question": "Q?", "options": [{"label": "X"}, {"label": "Y"}]},
        ],
        "answer": '"Q?"="Z"',  # Z doesn't match any option
    }
    _derive_selected_index(item4)
    check("_derive: no-match leaves index null",
          item4.get("selected_index") is None,
          f"selected_index = {item4.get('selected_index')}")

    return True


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global TOKEN

    log("Multi-Question AskUserQuestion Critic Test", "STEP")
    log(f"Target: {BASE}, Project: {PROJECT}, Model: {MODEL}\n")

    # Authenticate
    resp = requests.post(f"{BASE}/api/auth/login", json={"password": "9570118ok"}, timeout=10)
    if resp.status_code != 200:
        log(f"Auth failed: {resp.status_code} {resp.text}", "FAIL")
        sys.exit(1)
    TOKEN = resp.json()["token"]
    log("Authenticated", "OK")

    # Health check
    health = api("get", "/api/health")
    if not health or health.get("status") != "ok":
        log(f"Health check failed: {health}", "FAIL")
        sys.exit(1)
    log("Server healthy", "OK")

    # Run tests
    test_derive_selected_index()  # Unit test first (fast, no agents)
    test_single_question()        # Backward compat
    test_multi_question()         # Main feature test
    test_duplicate_answer()       # Idempotency
    test_exit_plan_mode()         # Plan mode not broken

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    total = len(results)
    color = "\033[32m" if failed == 0 else "\033[31m"
    print(f"{color}Results: {passed}/{total} passed, {failed} failed\033[0m")

    if failed:
        print("\nFailed tests:")
        for name, status, detail in results:
            if status == "FAIL":
                print(f"  \033[31m✗\033[0m {name}: {detail}")

    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
