#!/usr/bin/env python3
"""Comprehensive test suite for the Tasks v2 module.

Mocks multiple users exercising every API endpoint, state transition,
validation rule, and edge case in the tasks system.

Usage:
    python3 test_tasks_module.py [--base-url http://localhost:8080]

Produces a structured report at the end.
"""

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8080"
RESULTS: list[dict] = []
TEST_TASK_IDS: list[str] = []  # Track created task IDs for cleanup


def api(method: str, path: str, body: dict | None = None, expect_status: int = 200) -> dict | list | None:
    """Make an API call and return parsed JSON."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            status = resp.status
            result = json.loads(resp.read().decode())
            if expect_status and status != expect_status:
                raise AssertionError(f"Expected HTTP {expect_status}, got {status}")
            return result
    except HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        if expect_status and e.code == expect_status:
            try:
                return json.loads(body_text)
            except Exception:
                return {"raw": body_text}
        raise AssertionError(f"HTTP {e.code} (expected {expect_status}): {body_text}")


def api_err(method: str, path: str, body: dict | None = None, expect_status: int = 400) -> dict:
    """Make an API call expecting an error status code."""
    return api(method, path, body, expect_status=expect_status)


def record(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append({"name": name, "status": status, "detail": detail})
    symbol = "\033[32m✓\033[0m" if passed else "\033[31m✗\033[0m"
    print(f"  {symbol} {name}" + (f"  ({detail})" if detail and not passed else ""))


def run_test(name: str):
    """Decorator to run a test function and record result."""
    def decorator(fn):
        def wrapper():
            try:
                fn()
                record(name, True)
            except Exception as e:
                record(name, False, str(e))
                traceback.print_exc()
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


def cleanup_test_tasks():
    """Cancel all test tasks we created."""
    for tid in TEST_TASK_IDS:
        try:
            api("POST", f"/api/v2/tasks/{tid}/cancel")
        except Exception:
            pass


# ===========================================================================
# TEST GROUP 1: Task Creation
# ===========================================================================

def test_group_creation():
    print("\n\033[1m=== GROUP 1: Task Creation ===\033[0m")

    # 1.1 Create basic task (INBOX)
    @run_test("1.1 Create basic task → INBOX status")
    def test():
        result = api("POST", "/api/v2/tasks", {
            "title": "Test task basic",
            "description": "A simple test task",
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["status"] == "INBOX", f"Expected INBOX, got {result['status']}"
        assert result["title"] == "Test task basic"
        assert result["description"] == "A simple test task"
        assert result["project_name"] == "cc-orchestrator"
        assert result["priority"] == 0
        assert result["attempt_number"] == 1
        assert result["use_worktree"] is True
        assert result["skip_permissions"] is True
    test()

    # 1.2 Create with auto-dispatch (should go to PENDING)
    @run_test("1.2 Create with auto_dispatch → PENDING status")
    def test():
        result = api("POST", "/api/v2/tasks", {
            "title": "Auto-dispatch test",
            "description": "Should go straight to PENDING",
            "project_name": "cc-orchestrator",
            "auto_dispatch": True,
        }, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["status"] == "PENDING", f"Expected PENDING, got {result['status']}"
    test()

    # 1.3 Auto-dispatch without project_name → INBOX (not PENDING)
    @run_test("1.3 Auto-dispatch without project_name → INBOX")
    def test():
        result = api("POST", "/api/v2/tasks", {
            "title": "Auto-dispatch no project",
            "description": "Missing project should stay INBOX",
            "auto_dispatch": True,
        }, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["status"] == "INBOX", f"Expected INBOX, got {result['status']}"
    test()

    # 1.4 Auto-title from short description
    @run_test("1.4 Auto-title from short description (<=60 chars)")
    def test():
        result = api("POST", "/api/v2/tasks", {
            "description": "Fix the login button color",
        }, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["title"] == "Fix the login button color", f"Title: {result['title']}"
    test()

    # 1.5 Auto-title from long description (truncated at word boundary)
    @run_test("1.5 Auto-title from long description (truncated + ...)")
    def test():
        long_desc = "This is a very long description that should be truncated at a word boundary because it exceeds sixty characters"
        result = api("POST", "/api/v2/tasks", {
            "description": long_desc,
        }, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["title"].endswith("..."), f"Expected ... suffix, got: {result['title']}"
        assert len(result["title"]) <= 64, f"Title too long: {len(result['title'])}"
    test()

    # 1.6 No title no description → "Untitled task"
    @run_test("1.6 No title or description → 'Untitled task'")
    def test():
        result = api("POST", "/api/v2/tasks", {}, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["title"] == "Untitled task", f"Title: {result['title']}"
    test()

    # 1.7 All optional fields
    @run_test("1.7 Create with all optional fields")
    def test():
        result = api("POST", "/api/v2/tasks", {
            "title": "Full options task",
            "description": "Testing all fields",
            "project_name": "cc-orchestrator",
            "priority": 1,
            "model": "claude-sonnet-4-6",
            "effort": "high",
            "skip_permissions": False,
            "sync_mode": True,
            "use_worktree": False,
        }, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["priority"] == 1
        assert result["model"] == "claude-sonnet-4-6"
        assert result["effort"] == "high"
        assert result["skip_permissions"] is False
        assert result["sync_mode"] is True
        assert result["use_worktree"] is False
    test()

    # 1.8 Title max length validation
    @run_test("1.8 Title exceeding 300 chars → 422 validation error")
    def test():
        api_err("POST", "/api/v2/tasks", {
            "title": "x" * 301,
        }, expect_status=422)
    test()

    # 1.9 Priority out of range
    @run_test("1.9 Priority > 1 → 422 validation error")
    def test():
        api_err("POST", "/api/v2/tasks", {
            "title": "Bad priority",
            "priority": 5,
        }, expect_status=422)
    test()

    # 1.10 Created_at is set automatically
    @run_test("1.10 created_at is set automatically")
    def test():
        result = api("POST", "/api/v2/tasks", {
            "title": "Timestamp test",
        }, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["created_at"] is not None
    test()

    # 1.11 Whitespace-only title auto-generates
    @run_test("1.11 Whitespace-only title auto-generates from description")
    def test():
        result = api("POST", "/api/v2/tasks", {
            "title": "   ",
            "description": "Real description here",
        }, expect_status=201)
        TEST_TASK_IDS.append(result["id"])
        assert result["title"] == "Real description here"
    test()


# ===========================================================================
# TEST GROUP 2: Task Listing and Filtering
# ===========================================================================

def test_group_listing():
    print("\n\033[1m=== GROUP 2: Task Listing and Filtering ===\033[0m")

    # Create some tasks in known states
    inbox_task = api("POST", "/api/v2/tasks", {
        "title": "List test inbox",
        "project_name": "cc-orchestrator",
    }, expect_status=201)
    TEST_TASK_IDS.append(inbox_task["id"])

    pending_task = api("POST", "/api/v2/tasks", {
        "title": "List test pending",
        "project_name": "cc-orchestrator",
        "auto_dispatch": True,
    }, expect_status=201)
    TEST_TASK_IDS.append(pending_task["id"])

    # 2.1 List all tasks
    @run_test("2.1 List all tasks (no filter)")
    def test():
        result = api("GET", "/api/v2/tasks")
        assert isinstance(result, list)
        assert len(result) >= 2, f"Expected >=2 tasks, got {len(result)}"
    test()

    # 2.2 Filter by single status
    @run_test("2.2 Filter by single status=INBOX")
    def test():
        result = api("GET", "/api/v2/tasks?status=INBOX")
        assert all(t["status"] == "INBOX" for t in result), "Non-INBOX task found"
    test()

    # 2.3 Filter by multiple statuses
    @run_test("2.3 Filter by statuses=INBOX,PENDING")
    def test():
        result = api("GET", "/api/v2/tasks?statuses=INBOX,PENDING")
        for t in result:
            assert t["status"] in ("INBOX", "PENDING"), f"Unexpected status: {t['status']}"
    test()

    # 2.4 Filter by project
    @run_test("2.4 Filter by project=cc-orchestrator")
    def test():
        result = api("GET", "/api/v2/tasks?project=cc-orchestrator")
        for t in result:
            assert t["project_name"] == "cc-orchestrator", f"Wrong project: {t['project_name']}"
    test()

    # 2.5 Combined filter (status + project)
    @run_test("2.5 Combined filter status=INBOX&project=cc-orchestrator")
    def test():
        result = api("GET", "/api/v2/tasks?status=INBOX&project=cc-orchestrator")
        for t in result:
            assert t["status"] == "INBOX"
            assert t["project_name"] == "cc-orchestrator"
    test()

    # 2.6 Limit parameter
    @run_test("2.6 Limit parameter (limit=2)")
    def test():
        result = api("GET", "/api/v2/tasks?limit=2")
        assert len(result) <= 2, f"Expected <=2, got {len(result)}"
    test()

    # 2.7 Invalid status returns 400
    @run_test("2.7 Invalid status=BOGUS → 400")
    def test():
        api_err("GET", "/api/v2/tasks?status=BOGUS", expect_status=400)
    test()

    # 2.8 Invalid status in statuses returns 400
    @run_test("2.8 Invalid status in statuses=INBOX,BOGUS → 400")
    def test():
        api_err("GET", "/api/v2/tasks?statuses=INBOX,BOGUS", expect_status=400)
    test()

    # 2.9 Tasks ordered by created_at desc
    @run_test("2.9 Tasks ordered by created_at descending")
    def test():
        result = api("GET", "/api/v2/tasks?limit=10")
        if len(result) >= 2:
            for i in range(len(result) - 1):
                assert result[i]["created_at"] >= result[i+1]["created_at"], \
                    f"Not sorted: {result[i]['created_at']} < {result[i+1]['created_at']}"
    test()

    # 2.10 Non-existent project returns empty list
    @run_test("2.10 Non-existent project returns empty list")
    def test():
        result = api("GET", "/api/v2/tasks?project=nonexistent-project-xyz")
        assert result == [], f"Expected empty list, got {len(result)} tasks"
    test()


# ===========================================================================
# TEST GROUP 3: Task Counts
# ===========================================================================

def test_group_counts():
    print("\n\033[1m=== GROUP 3: Task Counts ===\033[0m")

    # 3.1 Counts endpoint returns expected keys
    @run_test("3.1 Counts endpoint returns all expected keys")
    def test():
        result = api("GET", "/api/v2/tasks/counts")
        expected_keys = {"INBOX", "QUEUE", "ACTIVE", "REVIEW", "DONE", "DONE_COMPLETED",
                        "weekly_total", "weekly_completed", "weekly_success_pct",
                        "weekly_failed", "weekly_timeout", "weekly_cancelled", "weekly_rejected"}
        assert expected_keys.issubset(set(result.keys())), f"Missing keys: {expected_keys - set(result.keys())}"
    test()

    # 3.2 Counts are non-negative integers
    @run_test("3.2 All counts are non-negative integers")
    def test():
        result = api("GET", "/api/v2/tasks/counts")
        for key, val in result.items():
            assert isinstance(val, int), f"{key} is not int: {type(val)}"
            assert val >= 0, f"{key} is negative: {val}"
    test()

    # 3.3 weekly_success_pct is 0-100
    @run_test("3.3 weekly_success_pct is between 0 and 100")
    def test():
        result = api("GET", "/api/v2/tasks/counts")
        pct = result["weekly_success_pct"]
        assert 0 <= pct <= 100, f"Percentage out of range: {pct}"
    test()

    # 3.4 INBOX count matches filtered list
    @run_test("3.4 INBOX count matches filtered list length")
    def test():
        counts = api("GET", "/api/v2/tasks/counts")
        inbox_list = api("GET", "/api/v2/tasks?status=INBOX")
        assert counts["INBOX"] == len(inbox_list), \
            f"Count {counts['INBOX']} != list length {len(inbox_list)}"
    test()


# ===========================================================================
# TEST GROUP 4: Task Detail
# ===========================================================================

def test_group_detail():
    print("\n\033[1m=== GROUP 4: Task Detail ===\033[0m")

    # Create a task to inspect
    task = api("POST", "/api/v2/tasks", {
        "title": "Detail test task",
        "description": "For detail endpoint testing",
        "project_name": "cc-orchestrator",
    }, expect_status=201)
    TEST_TASK_IDS.append(task["id"])

    # 4.1 Get task detail
    @run_test("4.1 Get task detail returns TaskDetailOut fields")
    def test():
        result = api("GET", f"/api/v2/tasks/{task['id']}")
        assert result["id"] == task["id"]
        assert result["title"] == "Detail test task"
        assert "conversation" in result
        assert "retry_context" in result
        assert "review_artifacts" in result
    test()

    # 4.2 Non-existent task → 404
    @run_test("4.2 Non-existent task_id → 404")
    def test():
        api_err("GET", "/api/v2/tasks/nonexistent99", expect_status=404)
    test()

    # 4.3 Conversation is empty for new task (no agent)
    @run_test("4.3 New task has empty conversation")
    def test():
        result = api("GET", f"/api/v2/tasks/{task['id']}")
        assert result["conversation"] == [], f"Expected empty, got {len(result['conversation'])} messages"
    test()


# ===========================================================================
# TEST GROUP 5: Task Update
# ===========================================================================

def test_group_update():
    print("\n\033[1m=== GROUP 5: Task Update ===\033[0m")

    task = api("POST", "/api/v2/tasks", {
        "title": "Update test task",
        "description": "Original description",
        "project_name": "cc-orchestrator",
    }, expect_status=201)
    TEST_TASK_IDS.append(task["id"])

    # 5.1 Update title
    @run_test("5.1 Update title")
    def test():
        result = api("PUT", f"/api/v2/tasks/{task['id']}", {
            "title": "Updated title",
        })
        assert result["title"] == "Updated title"
    test()

    # 5.2 Update description
    @run_test("5.2 Update description")
    def test():
        result = api("PUT", f"/api/v2/tasks/{task['id']}", {
            "description": "New description",
        })
        assert result["description"] == "New description"
    test()

    # 5.3 Update priority
    @run_test("5.3 Update priority to 1")
    def test():
        result = api("PUT", f"/api/v2/tasks/{task['id']}", {
            "priority": 1,
        })
        assert result["priority"] == 1
    test()

    # 5.4 Update model and effort
    @run_test("5.4 Update model and effort")
    def test():
        result = api("PUT", f"/api/v2/tasks/{task['id']}", {
            "model": "claude-haiku-4-5-20251001",
            "effort": "low",
        })
        assert result["model"] == "claude-haiku-4-5-20251001"
        assert result["effort"] == "low"
    test()

    # 5.5 Update project_name
    @run_test("5.5 Update project_name")
    def test():
        result = api("PUT", f"/api/v2/tasks/{task['id']}", {
            "project_name": "cc-orchestrator",
        })
        assert result["project_name"] == "cc-orchestrator"
    test()

    # 5.6 Cannot update non-existent task
    @run_test("5.6 Update non-existent task → 404")
    def test():
        api_err("PUT", "/api/v2/tasks/nonexist123", {"title": "x"}, expect_status=404)
    test()

    # 5.7 Empty title validation
    @run_test("5.7 Update with empty title → 422 (min_length=1)")
    def test():
        api_err("PUT", f"/api/v2/tasks/{task['id']}", {
            "title": "",
        }, expect_status=422)
    test()

    # 5.8 Cannot update task not in INBOX/PENDING
    @run_test("5.8 Cannot update task after dispatch (not INBOX/PENDING)")
    def test():
        # Create and cancel a task first
        t = api("POST", "/api/v2/tasks", {"title": "Cancel then update"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        api_err("PUT", f"/api/v2/tasks/{t['id']}", {"title": "Should fail"}, expect_status=400)
    test()

    # 5.9 Partial update (only send one field)
    @run_test("5.9 Partial update preserves other fields")
    def test():
        # Set specific values first
        api("PUT", f"/api/v2/tasks/{task['id']}", {
            "title": "Preserved title",
            "priority": 1,
        })
        # Update only description
        result = api("PUT", f"/api/v2/tasks/{task['id']}", {
            "description": "Only desc changed",
        })
        assert result["title"] == "Preserved title", f"Title changed: {result['title']}"
        assert result["priority"] == 1, f"Priority changed: {result['priority']}"
        assert result["description"] == "Only desc changed"
    test()


# ===========================================================================
# TEST GROUP 6: Task Dispatch
# ===========================================================================

def test_group_dispatch():
    print("\n\033[1m=== GROUP 6: Task Dispatch ===\033[0m")

    # 6.1 Dispatch INBOX task → PENDING
    @run_test("6.1 Dispatch INBOX task → PENDING")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Dispatch test",
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        result = api("POST", f"/api/v2/tasks/{t['id']}/dispatch")
        assert result["status"] == "PENDING", f"Expected PENDING, got {result['status']}"
    test()

    # 6.2 Dispatch without project_name → 400
    @run_test("6.2 Dispatch without project_name → 400")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "No project dispatch",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/dispatch", expect_status=400)
    test()

    # 6.3 Dispatch without title → 400
    @run_test("6.3 Dispatch task requiring title validation")
    def test():
        # Note: tasks always get at least "Untitled task" title, so this tests that path
        t = api("POST", "/api/v2/tasks", {
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        # Should succeed since auto-title was assigned
        result = api("POST", f"/api/v2/tasks/{t['id']}/dispatch")
        assert result["status"] == "PENDING"
    test()

    # 6.4 Dispatch non-existent task → 404
    @run_test("6.4 Dispatch non-existent task → 404")
    def test():
        api_err("POST", "/api/v2/tasks/bogustask99/dispatch", expect_status=404)
    test()

    # 6.5 Double dispatch (already PENDING) → 409
    @run_test("6.5 Double dispatch (PENDING → PENDING) → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Double dispatch",
            "project_name": "cc-orchestrator",
            "auto_dispatch": True,  # Already PENDING
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/dispatch", expect_status=409)
    test()

    # 6.6 Dispatch CANCELLED task → 409 (invalid transition)
    @run_test("6.6 Dispatch CANCELLED task → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Cancel then dispatch",
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        api_err("POST", f"/api/v2/tasks/{t['id']}/dispatch", expect_status=409)
    test()


# ===========================================================================
# TEST GROUP 7: Task Cancel
# ===========================================================================

def test_group_cancel():
    print("\n\033[1m=== GROUP 7: Task Cancel ===\033[0m")

    # 7.1 Cancel INBOX task
    @run_test("7.1 Cancel INBOX task → CANCELLED")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Cancel inbox",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        result = api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        assert result["status"] == "CANCELLED"
        assert result["completed_at"] is not None
    test()

    # 7.2 Cancel PENDING task
    @run_test("7.2 Cancel PENDING task → CANCELLED")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Cancel pending",
            "project_name": "cc-orchestrator",
            "auto_dispatch": True,
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        result = api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        assert result["status"] == "CANCELLED"
    test()

    # 7.3 Double cancel (already CANCELLED) → 409
    @run_test("7.3 Double cancel → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "Double cancel"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        api_err("POST", f"/api/v2/tasks/{t['id']}/cancel", expect_status=409)
    test()

    # 7.4 Cancel non-existent task → 404
    @run_test("7.4 Cancel non-existent task → 404")
    def test():
        api_err("POST", "/api/v2/tasks/nonexist444/cancel", expect_status=404)
    test()


# ===========================================================================
# TEST GROUP 8: Task Reject
# ===========================================================================

def test_group_reject():
    print("\n\033[1m=== GROUP 8: Task Reject ===\033[0m")

    # 8.1 Reject requires REVIEW status — INBOX can't be rejected
    @run_test("8.1 Reject INBOX task → 409 (invalid transition)")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "Reject inbox"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/reject",
                {"reason": "not good"}, expect_status=409)
    test()

    # 8.2 Reject PENDING task → 409
    @run_test("8.2 Reject PENDING task → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Reject pending",
            "project_name": "cc-orchestrator",
            "auto_dispatch": True,
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/reject",
                {"reason": "nope"}, expect_status=409)
    test()

    # 8.3 Reject non-existent → 404
    @run_test("8.3 Reject non-existent task → 404")
    def test():
        api_err("POST", "/api/v2/tasks/fake12345/reject",
                {"reason": "gone"}, expect_status=404)
    test()

    # 8.4 Reject without reason → 422
    @run_test("8.4 Reject without reason body → 422")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "No reason reject"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/reject", {}, expect_status=422)
    test()

    # 8.5 Reject with empty reason → 422
    @run_test("8.5 Reject with empty reason → 422")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "Empty reason"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/reject",
                {"reason": ""}, expect_status=422)
    test()


# ===========================================================================
# TEST GROUP 9: Task Approve
# ===========================================================================

def test_group_approve():
    print("\n\033[1m=== GROUP 9: Task Approve ===\033[0m")

    # 9.1 Approve INBOX task → 409 (must be REVIEW)
    @run_test("9.1 Approve INBOX task → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "Approve inbox"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/approve", expect_status=409)
    test()

    # 9.2 Approve PENDING task → 409
    @run_test("9.2 Approve PENDING task → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Approve pending",
            "project_name": "cc-orchestrator",
            "auto_dispatch": True,
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/approve", expect_status=409)
    test()

    # 9.3 Approve non-existent → 404
    @run_test("9.3 Approve non-existent task → 404")
    def test():
        api_err("POST", "/api/v2/tasks/nonexist888/approve", expect_status=404)
    test()

    # 9.4 Approve CANCELLED task → 409
    @run_test("9.4 Approve CANCELLED task → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "Approve cancelled"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        api_err("POST", f"/api/v2/tasks/{t['id']}/approve", expect_status=409)
    test()


# ===========================================================================
# TEST GROUP 10: Try/Revert Changes
# ===========================================================================

def test_group_try_revert():
    print("\n\033[1m=== GROUP 10: Try/Revert Changes ===\033[0m")

    # 10.1 Try changes on non-REVIEW task → 409
    @run_test("10.1 Try changes on INBOX task → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "Try inbox"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/try-changes", expect_status=409)
    test()

    # 10.2 Revert with no try_base_commit → 409
    @run_test("10.2 Revert when no changes tried → 409")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "Revert nothing"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api_err("POST", f"/api/v2/tasks/{t['id']}/revert-try", expect_status=409)
    test()

    # 10.3 Try changes non-existent → 404
    @run_test("10.3 Try changes non-existent task → 404")
    def test():
        api_err("POST", "/api/v2/tasks/fake999/try-changes", expect_status=404)
    test()

    # 10.4 Revert non-existent → 404
    @run_test("10.4 Revert non-existent task → 404")
    def test():
        api_err("POST", "/api/v2/tasks/fake999/revert-try", expect_status=404)
    test()


# ===========================================================================
# TEST GROUP 11: State Machine Transitions
# ===========================================================================

def test_group_state_machine():
    print("\n\033[1m=== GROUP 11: State Machine Transitions ===\033[0m")

    # We test valid and invalid transitions by trying to dispatch/cancel/reject

    # 11.1 INBOX → PENDING (dispatch) ✓
    @run_test("11.1 INBOX → PENDING via dispatch ✓")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "SM test 1",
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        result = api("POST", f"/api/v2/tasks/{t['id']}/dispatch")
        assert result["status"] == "PENDING"
    test()

    # 11.2 INBOX → CANCELLED (cancel) ✓
    @run_test("11.2 INBOX → CANCELLED via cancel ✓")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "SM test 2"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        result = api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        assert result["status"] == "CANCELLED"
    test()

    # 11.3 PENDING → CANCELLED (cancel) ✓
    @run_test("11.3 PENDING → CANCELLED via cancel ✓")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "SM test 3",
            "project_name": "cc-orchestrator",
            "auto_dispatch": True,
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        result = api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        assert result["status"] == "CANCELLED"
    test()

    # 11.4 COMPLETE → anything is invalid (terminal)
    @run_test("11.4 COMPLETE is terminal (cancel fails)")
    def test():
        # We can't easily get a COMPLETE task without the full flow,
        # so we check existing complete tasks
        all_tasks = api("GET", "/api/v2/tasks?status=COMPLETE&limit=1")
        if all_tasks:
            tid = all_tasks[0]["id"]
            api_err("POST", f"/api/v2/tasks/{tid}/cancel", expect_status=409)
        else:
            record("11.4 COMPLETE is terminal (cancel fails)", True, "skipped - no COMPLETE tasks")
            raise Exception("SKIP")
    test()

    # 11.5 CANCELLED → anything is invalid (terminal)
    @run_test("11.5 CANCELLED is terminal (dispatch fails)")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "SM terminal",
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        api_err("POST", f"/api/v2/tasks/{t['id']}/dispatch", expect_status=409)
    test()


# ===========================================================================
# TEST GROUP 12: Concurrent / Multi-User Scenarios
# ===========================================================================

def test_group_concurrent():
    print("\n\033[1m=== GROUP 12: Concurrent / Multi-User Scenarios ===\033[0m")

    # 12.1 Rapid fire creation (simulates multiple users)
    @run_test("12.1 Rapid creation of 10 tasks (multi-user)")
    def test():
        ids = []
        for i in range(10):
            t = api("POST", "/api/v2/tasks", {
                "title": f"Concurrent test {i}",
                "description": f"Created by mock user {i % 3}",
                "project_name": "cc-orchestrator",
                "priority": i % 2,
            }, expect_status=201)
            ids.append(t["id"])
            TEST_TASK_IDS.append(t["id"])
        assert len(set(ids)) == 10, "Duplicate IDs generated!"
    test()

    # 12.2 Each task has unique ID
    @run_test("12.2 All test task IDs are unique")
    def test():
        assert len(TEST_TASK_IDS) == len(set(TEST_TASK_IDS)), "Duplicate task IDs found!"
    test()

    # 12.3 Create + dispatch + cancel rapid sequence
    @run_test("12.3 Create → dispatch → cancel rapid sequence")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Rapid lifecycle",
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api("POST", f"/api/v2/tasks/{t['id']}/dispatch")
        result = api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        assert result["status"] == "CANCELLED"
    test()

    # 12.4 Counts consistency after bulk operations
    @run_test("12.4 Counts consistent after bulk operations")
    def test():
        counts = api("GET", "/api/v2/tasks/counts")
        # Just verify it doesn't crash and returns valid data
        assert isinstance(counts["INBOX"], int)
        total_from_counts = counts["INBOX"] + counts["QUEUE"] + counts["ACTIVE"] + counts["REVIEW"] + counts["DONE"]
        # Get all tasks to verify
        all_tasks = api("GET", "/api/v2/tasks?limit=500")
        assert total_from_counts == len(all_tasks), \
            f"Counts total {total_from_counts} != list total {len(all_tasks)}"
    test()


# ===========================================================================
# TEST GROUP 13: Edge Cases and Boundary Tests
# ===========================================================================

def test_group_edge_cases():
    print("\n\033[1m=== GROUP 13: Edge Cases and Boundary Tests ===\033[0m")

    # 13.1 Unicode in title and description
    @run_test("13.1 Unicode in title and description")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "任务：修复BUG 🐛",
            "description": "Détails de la tâche avec des caractères spéciaux: ñ, ü, ø, 日本語",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        detail = api("GET", f"/api/v2/tasks/{t['id']}")
        assert "🐛" in detail["title"]
        assert "日本語" in detail["description"]
    test()

    # 13.2 Very long description
    @run_test("13.2 Very long description (10KB)")
    def test():
        long_desc = "x" * 10000
        t = api("POST", "/api/v2/tasks", {
            "title": "Long desc test",
            "description": long_desc,
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        detail = api("GET", f"/api/v2/tasks/{t['id']}")
        assert len(detail["description"]) == 10000
    test()

    # 13.3 Special characters in title
    @run_test("13.3 Special characters in title (quotes, backslashes)")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": 'Fix "path\\to\\file" issue <script>alert(1)</script>',
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        detail = api("GET", f"/api/v2/tasks/{t['id']}")
        assert "<script>" in detail["title"]  # Should store as-is (XSS is a frontend concern)
    test()

    # 13.4 Null description is valid
    @run_test("13.4 Null/missing description is valid")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "No description",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        assert t["description"] is None
    test()

    # 13.5 Empty body creates untitled task
    @run_test("13.5 Empty JSON body creates untitled task")
    def test():
        t = api("POST", "/api/v2/tasks", {}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        assert t["title"] == "Untitled task"
        assert t["status"] == "INBOX"
    test()

    # 13.6 Extra fields in body are ignored
    @run_test("13.6 Extra/unknown fields in body are ignored")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Extra fields",
            "unknown_field": "should be ignored",
            "extra": 123,
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        assert t["title"] == "Extra fields"
    test()

    # 13.7 Negative priority → 422
    @run_test("13.7 Negative priority → 422")
    def test():
        api_err("POST", "/api/v2/tasks", {
            "title": "Bad priority",
            "priority": -1,
        }, expect_status=422)
    test()

    # 13.8 Task ID format (12 hex chars)
    @run_test("13.8 Task ID is 12 hex chars")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "ID format"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        assert len(t["id"]) == 12, f"ID length: {len(t['id'])}"
        assert all(c in "0123456789abcdef" for c in t["id"]), f"Non-hex ID: {t['id']}"
    test()

    # 13.9 Scheduled_at field
    @run_test("13.9 Scheduled_at accepts ISO datetime")
    def test():
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        t = api("POST", "/api/v2/tasks", {
            "title": "Scheduled task",
            "scheduled_at": future,
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        assert t["scheduled_at"] is not None
    test()


# ===========================================================================
# TEST GROUP 14: v1 Legacy Endpoints
# ===========================================================================

def test_group_legacy():
    print("\n\033[1m=== GROUP 14: v1 Legacy Endpoints ===\033[0m")

    # 14.1 GET /api/tasks returns a list
    @run_test("14.1 GET /api/tasks returns list")
    def test():
        result = api("GET", "/api/tasks")
        assert isinstance(result, list)
    test()

    # 14.2 GET /api/tasks/{id} for non-existent → 404
    @run_test("14.2 GET /api/tasks/{id} non-existent → 404")
    def test():
        api_err("GET", "/api/tasks/nonexist999", expect_status=404)
    test()


# ===========================================================================
# TEST GROUP 15: Data Integrity
# ===========================================================================

def test_group_integrity():
    print("\n\033[1m=== GROUP 15: Data Integrity ===\033[0m")

    # 15.1 Create and immediately read back
    @run_test("15.1 Create → immediate read back matches")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Integrity check",
            "description": "Should be exactly this",
            "project_name": "cc-orchestrator",
            "priority": 1,
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        detail = api("GET", f"/api/v2/tasks/{t['id']}")
        assert detail["title"] == "Integrity check"
        assert detail["description"] == "Should be exactly this"
        assert detail["project_name"] == "cc-orchestrator"
        assert detail["priority"] == 1
        assert detail["status"] == "INBOX"
    test()

    # 15.2 Update → read back matches
    @run_test("15.2 Update → read back matches")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Before update",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        api("PUT", f"/api/v2/tasks/{t['id']}", {
            "title": "After update",
            "description": "Added description",
            "priority": 1,
        })
        detail = api("GET", f"/api/v2/tasks/{t['id']}")
        assert detail["title"] == "After update"
        assert detail["description"] == "Added description"
        assert detail["priority"] == 1
    test()

    # 15.3 Cancel sets completed_at
    @run_test("15.3 Cancel sets completed_at timestamp")
    def test():
        t = api("POST", "/api/v2/tasks", {"title": "Cancel timestamp"}, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        assert t["completed_at"] is None
        result = api("POST", f"/api/v2/tasks/{t['id']}/cancel")
        assert result["completed_at"] is not None
    test()

    # 15.4 Dispatch does not set started_at (dispatcher does that)
    @run_test("15.4 Dispatch to PENDING: started_at still null")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Started at check",
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        result = api("POST", f"/api/v2/tasks/{t['id']}/dispatch")
        assert result["started_at"] is None, f"started_at should be None, got {result['started_at']}"
    test()

    # 15.5 Task appears in correct status filter
    @run_test("15.5 Task appears in correct status filter after state change")
    def test():
        t = api("POST", "/api/v2/tasks", {
            "title": "Filter check",
            "project_name": "cc-orchestrator",
        }, expect_status=201)
        TEST_TASK_IDS.append(t["id"])
        # Check in INBOX
        inbox = api("GET", "/api/v2/tasks?status=INBOX")
        assert any(x["id"] == t["id"] for x in inbox), "Not found in INBOX"
        # Dispatch
        api("POST", f"/api/v2/tasks/{t['id']}/dispatch")
        # Check in PENDING
        pending = api("GET", "/api/v2/tasks?status=PENDING")
        assert any(x["id"] == t["id"] for x in pending), "Not found in PENDING"
        # Check NOT in INBOX anymore
        inbox2 = api("GET", "/api/v2/tasks?status=INBOX")
        assert not any(x["id"] == t["id"] for x in inbox2), "Still in INBOX after dispatch!"
    test()


# ===========================================================================
# TEST GROUP 16: Response Schema Validation
# ===========================================================================

def test_group_schema():
    print("\n\033[1m=== GROUP 16: Response Schema Validation ===\033[0m")

    t = api("POST", "/api/v2/tasks", {
        "title": "Schema test",
        "project_name": "cc-orchestrator",
    }, expect_status=201)
    TEST_TASK_IDS.append(t["id"])

    # 16.1 TaskOut has all required fields
    @run_test("16.1 TaskOut response has all required fields")
    def test():
        required = ["id", "title", "status", "priority", "created_at",
                    "attempt_number", "use_worktree", "skip_permissions", "sync_mode"]
        for field in required:
            assert field in t, f"Missing field: {field}"
    test()

    # 16.2 TaskDetailOut has extra fields
    @run_test("16.2 TaskDetailOut has conversation, retry_context, review_artifacts")
    def test():
        detail = api("GET", f"/api/v2/tasks/{t['id']}")
        assert "conversation" in detail
        assert "retry_context" in detail
        assert "review_artifacts" in detail
    test()

    # 16.3 Counts response has all perspective keys
    @run_test("16.3 Counts has INBOX, QUEUE, ACTIVE, REVIEW, DONE keys")
    def test():
        counts = api("GET", "/api/v2/tasks/counts")
        for key in ["INBOX", "QUEUE", "ACTIVE", "REVIEW", "DONE"]:
            assert key in counts, f"Missing count key: {key}"
    test()

    # 16.4 List response is array of TaskOut
    @run_test("16.4 List response is array with TaskOut objects")
    def test():
        result = api("GET", "/api/v2/tasks?limit=1")
        assert isinstance(result, list)
        if result:
            item = result[0]
            assert "id" in item
            assert "title" in item
            assert "status" in item
    test()


# ===========================================================================
# Main
# ===========================================================================

def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description="Tasks module test suite")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--no-cleanup", action="store_true", help="Don't cancel test tasks after")
    args = parser.parse_args()
    BASE_URL = args.base_url

    # Check server is alive
    try:
        health = api("GET", "/api/health")
        print(f"Server: {BASE_URL} — {health.get('status', 'unknown')}")
    except Exception as e:
        print(f"ERROR: Cannot reach server at {BASE_URL}: {e}")
        sys.exit(1)

    # Record initial counts
    before = api("GET", "/api/v2/tasks/counts")
    print(f"Initial counts: INBOX={before['INBOX']} QUEUE={before['QUEUE']} "
          f"ACTIVE={before['ACTIVE']} REVIEW={before['REVIEW']} DONE={before['DONE']}")

    # Run all test groups
    test_group_creation()
    test_group_listing()
    test_group_counts()
    test_group_detail()
    test_group_update()
    test_group_dispatch()
    test_group_cancel()
    test_group_reject()
    test_group_approve()
    test_group_try_revert()
    test_group_state_machine()
    test_group_concurrent()
    test_group_edge_cases()
    test_group_legacy()
    test_group_integrity()
    test_group_schema()

    # Cleanup
    if not args.no_cleanup:
        print("\n\033[1mCleaning up test tasks...\033[0m")
        cleanup_test_tasks()

    # Report
    print("\n" + "=" * 60)
    print("\033[1mTEST RESULTS SUMMARY\033[0m")
    print("=" * 60)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    total = len(RESULTS)
    print(f"  Total:  {total}")
    print(f"  \033[32mPassed: {passed}\033[0m")
    print(f"  \033[31mFailed: {failed}\033[0m")
    print(f"  Rate:   {passed/total*100:.1f}%")

    if failed:
        print(f"\n\033[31mFailed tests:\033[0m")
        for r in RESULTS:
            if r["status"] == "FAIL":
                print(f"  ✗ {r['name']}: {r['detail']}")

    # Write report to file
    report_path = "/home/jyao073/cc-orchestrator/test_tasks_report.json"
    with open(report_path, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server": BASE_URL,
            "total": total,
            "passed": passed,
            "failed": failed,
            "results": RESULTS,
            "test_task_ids": TEST_TASK_IDS,
        }, f, indent=2)
    print(f"\nFull report: {report_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
