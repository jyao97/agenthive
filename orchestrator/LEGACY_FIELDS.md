# Legacy Task Fields — Removal Plan

These 9 columns on the `Task` model (`orchestrator/models.py`) belong to the v1 task/worker
system. They are superseded by v2 fields (`title`, `description`, `project_name`, `branch_name`,
`attempt_number`, `agent_summary`, etc.) but cannot be removed yet because the v1 dispatcher
(`dispatcher.py` + `worker_manager.py`) still uses them.

## Field Inventory

| Field | Read Locations | Removal Prerequisite |
|-------|---------------|---------------------|
| `prompt` | `dispatcher.py:140` (push notification body fallback), `worker_manager.py:115` (`_build_prompt`) | Remove v1 dispatcher or migrate push notification to use `task.title` |
| `project` | `dispatcher.py` (~20 refs: harvest, timeout, retry, assign, recovery), `agent_dispatcher.py:2946,3017,3023` (fallback `project_name or task.project`), `main.py:1076,1492,1677` (project rename/delete queries) | Remove v1 dispatcher; update agent_dispatcher fallback to use only `project_name`; update main.py project rename/delete to use `project_name` |
| `mode` | No reads outside model definition | Can be dropped once DB migration runs (Agent.mode used instead) |
| `container_id` | `dispatcher.py` (~12 refs: harvest, timeout, assign, recovery — stores PID string for v1 worker processes) | Remove v1 dispatcher |
| `branch` | No reads outside model definition | Can be dropped once DB migration runs (`branch_name` used instead) |
| `retries` | `dispatcher.py:188,194,203` (v1 auto-retry logic) | Remove v1 dispatcher (v2 uses `attempt_number` + `retry_context`) |
| `result_summary` | `dispatcher.py:120` (v1 harvest extracts summary from worker logs) | Remove v1 dispatcher (v2 uses `agent_summary`) |
| `stream_log` | `dispatcher.py:115,173` (v1 harvest/timeout stores truncated worker logs) | Remove v1 dispatcher |
| `error_message` | `dispatcher.py` (v1 harvest/timeout/retry/assign/recovery), `agent_dispatcher.py:2989,3029,3066,3098` (v2 task harvest), `main.py:3093,3100,3115,3127,3143` (v2 merge flow), `schemas.py` TaskOut (v2 API response) | **Cannot drop** — actively used by both v1 and v2 systems. Promote to v2 field. |

### Notes
- `mode` and `branch` have **zero reads** and could be dropped immediately with a migration.
- `error_message` is the only field actively shared between v1 and v2 — it should be reclassified
  as a v2 field rather than dropped.
- `project` has the most reads (~20 in dispatcher.py alone) because the entire v1 dispatch loop
  keys on it. The v2 system uses `project_name` (FK to projects table) instead.

## Phase 2: Frontend migration
- Migrate `TaskDetail.jsx` from `/api/tasks/{id}` to `/api/v2/tasks/{id}`
  - `TaskDetail.jsx` uses `fetchTask()` which calls `GET /api/tasks/{id}` (v1 endpoint)
  - The v1 endpoint constructs `AgentTaskDetail` from Message + Agent rows, not from Task columns
  - `TaskCard.jsx` renders `task.prompt`, `task.project`, `task.mode` from `AgentTaskBrief`
- Remove `fetchTask()` from `frontend/src/lib/api.js` (line 134)
- Remove `TaskCard.jsx` and `TaskDetail.jsx` components (v1 task views)
- Remove `/api/tasks` GET endpoint and `AgentTaskBrief`/`AgentTaskDetail` schemas from
  `schemas.py` and `main.py`

## Phase 3: Column removal
- Write migration in `database.py` (this project uses manual migrations, NOT Alembic)
- Reclassify `error_message` as a v2 field (move it above the legacy comment block)
- Drop the remaining 8 legacy columns after verifying zero remaining reads:
  `prompt`, `project`, `mode`, `container_id`, `branch`, `retries`, `result_summary`, `stream_log`
- Update `database.py` backfill queries (lines 251-259) that reference `prompt` and `project`
