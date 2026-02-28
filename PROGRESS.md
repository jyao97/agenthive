# PROGRESS.md
> Read this file at the start of every task. Append only, never delete entries.
> Updated when tasks complete — contains what worked, what failed, and why.

## cc-orchestrator — Lessons Learned

<!-- Entry format:
### YYYY-MM-DD | Task: {title} | Status: success/abandoned
- What: (one line summary)
- Attempts: (what was tried)
- Resolution: (what finally worked)
- Lesson: (what future agents should know)
-->

### 2026-02-23 | Task: Phase 1 Scheduler Core (1.1–1.4) | Status: success

### What was done
- **1.1 Database Schema**: models.py (Task, Project, SystemConfig tables), database.py (SQLite WAL mode, session factory), config.py (env vars)
- **1.2 FastAPI CRUD**: Full task lifecycle (create/list/get/cancel/retry), project listing, enhanced health check, Pydantic schemas, registry.yaml loading on startup
- **1.3 Worker Manager**: Subprocess lifecycle management — start/stop/logs/status/cleanup, resource limits
- **1.4 Task Dispatcher**: Async scheduling loop with harvest/timeout/retry/assign phases, startup crash recovery, concurrency limits (global + per-project)

### Problems encountered
- Test tasks kept retrying because .env has placeholder API keys

### Solutions
- Cancelled test tasks manually; auto-retry stops at MAX_RETRIES=3

### Lessons learned
- SQLAlchemy `expire_on_commit=False` is essential for reading task fields after commit in the same session
- SQLite WAL mode + `check_same_thread=False` needed for async dispatcher + sync API sharing the same DB
- `datetime.now(timezone.utc)` instead of `datetime.utcnow()` to avoid naive datetime comparison issues

### 2026-02-24 | Task: Session Persistence + Auth Simplification | Status: success

### What was done
1. **Session persistence**: Session files and refreshed tokens survive restarts. `--resume` works across restarts.
2. **Auth simplification**: Switched to `CLAUDE_CODE_OAUTH_TOKEN` env var (generated via `claude setup-token`, valid ~1 year). Simplified credential management.

### Problems encountered
- `plan_manager.py` (since removed) also imported old config vars — missed on first pass

### Solutions
- Updated all module imports alongside worker_manager.py

### Lessons learned
- `CLAUDE_CODE_OAUTH_TOKEN` is the officially recommended auth method — eliminates credential file management entirely
- When removing config vars, grep the entire codebase for imports — not just the file you're working on
- Plan mode was later removed entirely (commit ad1c2c9) — only INTERVIEW and AUTO modes remain

### 2026-02-26 | Task: Worktree Session Resolution + tmux Recovery | Status: success

### What was done

**Worktree session directory resolution (critical bug fix)**
- Claude Code stores session JSONL files based on the CWD where it launched, not the project root
- Worktree agents launch from `{project}/.claude/worktrees/{name}/`, so their sessions end up in a completely different `~/.claude/projects/` subdirectory
- Created `_resolve_session_jsonl(session_id, project_path, worktree)` helper that checks both project root and worktree session dirs
- Fixed **13 call sites** across agent_dispatcher.py that used `session_source_dir(project_path)` directly:
  - `_dispatch_pending_messages()` — resume pre-check
  - `_sync_session_loop_inner()` — session parsing
  - `import_session_history()` — history import
  - `_reap_dead_agents()` — liveness freshness check
  - `_detect_successor_session()` — scans both session dirs for successors
  - `_spawn_successor_agent()` — new session parsing
  - `_dedup_pane_agents()` — mtime comparison
  - `_recover_agents()` — recovery JSONL path + liveness
  - `_auto_detect_cli_sessions()` — scans worktree session dirs + Tier 0 agent lookup

**tmux recovery pane assignment (critical bug fix)**
- On server restart, `_recover_agents()` re-detects tmux panes for each agent
- `_detect_tmux_pane_for_session()` filters out panes owned by non-STOPPED agents via `_get_pane_owner()`
- During recovery, agents' OWN panes get filtered (they're non-STOPPED), making them match wrong panes
- Multiple agents got assigned the same pane → dedup conflict → one agent force-stopped
- Fix: Use tmux session name `ah-{agent_id[:8]}` for definitive matching (built `session_name_to_pane` dict once at startup)

**CWD matching for worktree agents (bug fix)**
- 4 locations used exact `cwd == project_path` comparison
- Worktree agent CWDs like `{project}/.claude/worktrees/streaming` failed this check
- Fix: subdirectory matching `cwd == proj or cwd.startswith(proj + "/")`

**Auto-detect Tier 0 matching (enhancement)**
- `_auto_detect_cli_sessions()` used PID/session-file matching, which sometimes picked the wrong stopped agent
- Added Tier 0: if tmux session name is `ah-{id}`, directly look up that agent by ID prefix

**Schema/API fixes**
- Added `session_id` to `AgentOut` and `AgentBrief` schemas
- Added `AgentStatus.SYNCING` to active agent count queries
- Added `metadata` column to messages table (migration in database.py)

### Problems encountered
1. Worktree agent had 5 messages from wrong session file (looked correct because first message happened to match)
2. Pane dedup caused silent agent termination on every restart
3. Wrong agent revived by auto-detect when multiple stopped agents existed for same project
4. `session_source_dir()` was called in 13+ places, each one a potential worktree bug

### Solutions
1. Created unified `_resolve_session_jsonl()` that checks both dirs, then did a comprehensive audit of ALL call sites
2. Used tmux session name as authoritative pane→agent mapping (not PID/session heuristics)
3. Added Tier 0 matching in auto-detect using tmux session name
4. Systematic grep + fix of every `session_source_dir()` call site

### Lessons learned
- **Claude Code session directory isolation**: Sessions are stored under `~/.claude/projects/{path-encoded-cwd}/`. Worktree CWD differs from project root → different directory. This is the #1 source of bugs for worktree features.
- **tmux session names are authoritative**: `ah-{agent_id[:8]}` is more reliable than any PID/file heuristic for pane matching. Always prefer session name matching over other methods.
- **Self-exclusion during recovery**: When recovering agents, their own panes appear "owned" by themselves (non-STOPPED), causing `_get_pane_owner()` to filter them out. Must handle this edge case.
- **Exact CWD matching breaks worktrees**: Always use subdirectory matching. A worktree agent's CWD is a subdirectory of the project.
- **Audit pattern**: When fixing a helper function bug, grep for ALL call sites of the old pattern. Don't assume you found them all — `session_source_dir` appeared 13+ times.
- **Wrong session file ≠ empty file**: A wrong session file can appear to work (has messages), just from a different session. Validate session IDs match, not just file existence.

### 2026-02-26 | Task: Notification + Streaming Investigation | Status: success

### What was done
- Audited all 6 `send_push_notification` call sites — **all are properly guarded** with `_is_agent_in_use()` check
- `_is_agent_in_use()` checks both WebSocket viewing state and tmux `session_attached`
- `_refresh_pane_attached()` runs tmux query and caches results in `_pane_attached` dict
- Verified tmux shows `session_attached=1` for active panes

### Still investigating
- Notification suppression may have race condition: `_refresh_pane_attached()` is called at start of dispatcher loop, but notification decisions happen later — if tmux state changes between calls, stale data could cause false notifications
- Streaming visibility may be intermittent due to sync loop timing or WebSocket connection state
