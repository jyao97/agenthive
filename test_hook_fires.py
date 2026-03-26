#!/usr/bin/env python3
"""Test: Does UserPromptSubmit hook fire for tmux-injected input?

Creates a tmux agent, sends a message via the web API (which uses tmux
send-keys under the hood), and checks whether the UserPromptSubmit hook
actually fires by tailing the orchestrator log.

Expected finding: UserPromptSubmit does NOT fire for tmux send-keys input.
Stop hook DOES fire (proving the agent processes the message).
"""

import os
import sys
import time
import threading
import requests

BASE = os.getenv("AHIVE_URL", "http://localhost:8080")
PROJECT = "cc-orchestrator"
LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "orchestrator.log")
TIMEOUT_AGENT_READY = 90
TIMEOUT_HOOK = 45


def api(method, path, **kw):
    url = f"{BASE}{path}" if path.startswith("/") else path
    kw.setdefault("timeout", 30)
    r = getattr(requests, method)(url, **kw)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


# ------------------------------------------------------------------
# Step 1: Create a tmux agent
# ------------------------------------------------------------------
log("Creating tmux agent...")
sc, body = api("post", "/api/agents/launch-tmux", json={
    "project": PROJECT,
    "prompt": "Say exactly: HOOK_TEST_READY",
    "model": "claude-haiku-4-5-20251001",
    "skip_permissions": True,
})
assert sc == 201, f"Failed to create agent: {sc} {body}"
agent_id = body["id"]
agent_short = agent_id[:8]
log(f"Agent created: {agent_id}")

# ------------------------------------------------------------------
# Step 2: Wait for agent to reach IDLE
# ------------------------------------------------------------------
log("Waiting for agent to reach IDLE...")
deadline = time.time() + TIMEOUT_AGENT_READY
while time.time() < deadline:
    sc, agent = api("get", f"/api/agents/{agent_id}")
    if sc == 200 and agent["status"] == "IDLE":
        log(f"Agent status: {agent['status']}")
        break
    time.sleep(2)
else:
    log(f"Agent never reached IDLE (last: {agent.get('status', '?')})", "FAIL")
    sys.exit(1)

# ------------------------------------------------------------------
# Step 3: Wait for initial response (Stop hook from first prompt)
# ------------------------------------------------------------------
log("Waiting for initial response (Stop hook)...")
deadline = time.time() + TIMEOUT_HOOK
initial_stop_seen = False
while time.time() < deadline:
    sc, agent = api("get", f"/api/agents/{agent_id}")
    if sc == 200 and agent["status"] == "IDLE":
        # Check if there's an agent response
        sc2, msgs_data = api("get", f"/api/agents/{agent_id}/messages?limit=10")
        if sc2 == 200:
            msgs = msgs_data.get("messages", [])
            agent_msgs = [m for m in msgs if m["role"] == "AGENT"]
            if agent_msgs:
                initial_stop_seen = True
                log(f"Initial response received ({len(agent_msgs)} agent messages)")
                break
    time.sleep(2)

if not initial_stop_seen:
    log("No initial response — agent may not have processed first prompt", "WARN")

# ------------------------------------------------------------------
# Step 4: Set up log tailer BEFORE sending the test message
# ------------------------------------------------------------------
hook_events = {"UserPromptSubmit": [], "Stop": []}
stop_tailing = threading.Event()


def tail_log():
    """Tail orchestrator log for hook events for our agent."""
    try:
        with open(LOG_FILE, "r") as f:
            f.seek(0, 2)  # start at end
            while not stop_tailing.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                if agent_short not in line:
                    continue
                if "hook_agent_user_prompt" in line or "UserPromptSubmit" in line:
                    hook_events["UserPromptSubmit"].append(line.strip())
                    log(f"  LOG: {line.strip()[-120:]}", "HOOK")
                if "hook_agent_stop" in line or "Stop hook" in line:
                    hook_events["Stop"].append(line.strip())
                    log(f"  LOG: {line.strip()[-120:]}", "HOOK")
                if "delivered" in line.lower() and agent_short in line:
                    log(f"  LOG: {line.strip()[-120:]}", "DLVR")
    except Exception as e:
        log(f"Log tailer error: {e}", "WARN")


tailer = threading.Thread(target=tail_log, daemon=True)
tailer.start()

# ------------------------------------------------------------------
# Step 5: Send a message via web API (triggers tmux send-keys)
# ------------------------------------------------------------------
test_content = f"Say exactly: HOOK_TEST_{int(time.time())}"
log(f"Sending message via web API: '{test_content}'")
send_time = time.time()

sc, msg_body = api("post", f"/api/agents/{agent_id}/messages", json={
    "content": test_content,
    "queue": True,
})
assert sc == 201, f"Failed to send message: {sc} {msg_body}"
msg_id = msg_body["id"]
log(f"Message sent: {msg_id}")

# ------------------------------------------------------------------
# Step 6: Wait for Stop hook (proves agent processed the message)
# ------------------------------------------------------------------
log(f"Waiting up to {TIMEOUT_HOOK}s for hooks to fire...")
deadline = time.time() + TIMEOUT_HOOK
stop_received = False
while time.time() < deadline:
    if hook_events["Stop"]:
        stop_received = True
        elapsed = time.time() - send_time
        log(f"Stop hook fired after {elapsed:.1f}s")
        # Give a few more seconds for UserPromptSubmit to arrive
        time.sleep(5)
        break
    time.sleep(0.5)

stop_tailing.set()
tailer.join(timeout=2)

# ------------------------------------------------------------------
# Step 7: Report results
# ------------------------------------------------------------------
print("\n" + "=" * 60)
print("RESULTS")
print("=" * 60)

user_prompt_count = len(hook_events["UserPromptSubmit"])
stop_count = len(hook_events["Stop"])

print(f"  UserPromptSubmit fires:  {user_prompt_count}")
print(f"  Stop fires:              {stop_count}")

if stop_count > 0 and user_prompt_count == 0:
    print("\n  CONFIRMED: UserPromptSubmit does NOT fire for tmux send-keys input.")
    print("  Stop hook DOES fire, proving the agent processed the prompt.")
    print("  Root cause: tmux send-keys bypasses the TUI input handler that")
    print("  triggers UserPromptSubmit. Delivery tracking must use Stop hook")
    print("  or another signal instead.")
elif stop_count > 0 and user_prompt_count > 0:
    print("\n  UNEXPECTED: Both hooks fired. UserPromptSubmit DOES fire for tmux input.")
    print("  The delivery delay has a different root cause.")
elif stop_count == 0:
    print("\n  INCONCLUSIVE: Stop hook didn't fire either. Agent may not have")
    print("  processed the message within the timeout.")

# Also check delivery status
sc, msgs_data = api("get", f"/api/agents/{agent_id}/messages?limit=50")
if sc == 200:
    msgs = msgs_data.get("messages", [])
    our_msg = next((m for m in msgs if m["id"] == msg_id), None)
    if our_msg:
        delivered = our_msg.get("delivered_at")
        elapsed_total = time.time() - send_time
        print(f"\n  Message delivered_at: {delivered or 'NULL (never delivered)'}")
        print(f"  Time since send: {elapsed_total:.1f}s")
    else:
        print(f"\n  Message {msg_id} not found in agent messages")

print("=" * 60)

# ------------------------------------------------------------------
# Cleanup: stop the agent
# ------------------------------------------------------------------
log("Stopping test agent...")
api("post", f"/api/agents/{agent_id}/stop")
log("Done.")
