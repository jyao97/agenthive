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

### 2026-03-03 | Task: Task System v2 + Subagent Tracking + Multi-bugfix | Status: success

### What was done

**Task system v2 improvements**
- Added `GET /api/v2/tasks/counts` endpoint — perspective-based counts (queue/executing/review/done) + weekly completion stats
- Added `_get_session_slug()` for detecting `/clear` transitions via slug matching (increased search depth from 5→20 lines)
- Fixed `_get_session_pid()` to fall through on dead PIDs instead of early return
- Integrated task stats into MonitorContext (30s polling) + wired into PageHeader
- State machine: added `InvalidTransitionError`, expanded valid transitions (EXECUTING→COMPLETE, etc.)
- WebSocket reconnect: seed `generating_agents` on reconnect
- Legacy dispatcher: skip v2 tasks in legacy dispatcher loop
- New UI components: EffortSelector, ModelSelector, PromptInputBar, DoneView DONE_COMPLETED count

**Subagent detection and tracking (2a26146)**
- Claude Code spawns subagents (Explore, Plan, etc.) — now detected and tracked
- Scans `{session_dir}/subagents/agent-*.jsonl` for subagent metadata
- Creates lightweight Agent records with `parent_id` and `is_subagent=True`
- Imports subagent conversation turns into Messages
- API: subagents filtered from main list, attached to parent detail view, cascade stop

**Successor detection after ExitPlanMode /clear (56614ed)**
- `_get_session_pid()` only matched `.claude.json.tmp.{PID}` pattern
- After `/clear`, new sessions may only write source code tmp files (e.g. `main.py.tmp.{PID}.{ts}`)
- Added broader fallback matching any `{file}.tmp.{PID}.{timestamp}` pattern
- Fixed agents stuck in SYNCING forever after "clear context & bypass"
- **Root cause investigation** (5 parallel agents): primary cause was silent sync loop death at line 4323 (`if not agent or agent.status != AgentStatus.SYNCING: break`) — NO logging on this exit path. Secondary: 57-second blind spot between `/clear` and first `.tmp` file write
- Slug-based matching added as PID-independent fallback (slug persists across `/clear`)

**Invalid model name fix (90e7ec1)**
- `registry.yaml` loader used hardcoded fallback `claude-sonnet-4-5-20250514` — not a valid model ID
- Added `VALID_MODELS` set in config.py, validation in create_agent/launch/load_registry
- Startup migration to fix 10 projects + 4 agents with invalid models in live DB

**Other bug fixes (via worktree agents)**
- Fix project task counts: use Task table instead of Message counts (ca303be)
- Fix back button unclickable when toast overlaps (2104949) — z-index issue
- Fix voice auto-start broken by React StrictMode double-mount (93cd06d)
- Auto-stop agent after merge task completes (ae5b16e) — was left in IDLE state

### Problems encountered
1. Session slug search only scanned first 5 lines — insufficient for some sessions
2. Dead PID in `_get_session_pid()` caused early return → successor never found
3. Hardcoded invalid model name propagated to 10 projects silently
4. Merge task completion left agents in IDLE without cleanup

### Lessons learned
- **Model validation at boundaries**: Always validate model names on input (create/launch/load), not just output. Add a `VALID_MODELS` set and check against it.
- **Slug-based session matching**: After `/clear`, session IDs change but the slug (human-readable session name) persists. Slug matching is more robust than PID matching for detecting session continuity.
- **Subagent lifecycle**: Claude Code subagents write to `{session_dir}/subagents/agent-{uuid}.jsonl`. They have their own session IDs and conversation turns. Must parse these separately from the parent session.
- **Worktree agent delegation works**: 6 bug fixes were completed by worktree agents in parallel — the merge-and-stop lifecycle is now reliable enough for routine fixes.

### 2026-03-03 (afternoon) | Task: UI Features + Task Stats + Try/Revert | Status: success

### What was done

**Task stats popover + daily success rate chart**
- Fixed `popRef` undefined error in `TaskStatsPopover` (caused crash on ring click)
- Increased popover card min-width from 220→260px to prevent number line-break
- Added `/api/v2/tasks/counts` `daily` field — returns 7-day `total`/`completed`/`success_pct` per day
- Pure SVG sparkline chart (no charting lib) with gradient fill, percentage labels, single-letter weekday
- Per-project task ring on ProjectsPage — 22px Apple Watch style ring in card top-right
- Backend: folders endpoint added `task_completed` field, fixed `Task.project` → `Task.project_name`

**Task completion notifications + global toggles (0bf81bb)**
- Push notification on task COMPLETE/FAILED
- Global mute toggle in settings

**Try It / Revert It buttons for branch preview (0d56a46, 64f7f5e, 7b78010)**
- Try Changes: merges task branch into main temporarily, saves `try_base_commit` for rollback
- Revert Try: `git reset --hard {try_base_commit}`, creates backup branch first for non-worktree tasks
- Unified logic for worktree and non-worktree tasks
- Review page: fixed button text wrapping, renamed to clearer labels

**Muted bell icon fix**
- Replaced distorted muted bell SVG in 3 files (TasksPage, AgentsPage, AgentChatPage) with clean bell + diagonal slash

**Bottom nav review count badge**
- Tasks tab shows orange badge with REVIEW count (polls `/api/v2/tasks/counts` every 10s)

**Double-tap to scroll to unread (WeChat-style)**
- Agents tab: double-tap scrolls to first unread agent, cyan highlight flash 1.5s
- Tasks tab: double-tap switches to Review tab, scrolls to first review task
- Uses custom `nav-scroll-to-unread` event + `data-agent-id`/`data-task-id` attributes

### 2026-03-03 (afternoon) | Task: Agent In-Use / Notification Hardening | Status: in-progress (uncommitted)

### What was done (edits applied but NOT committed)

**WebSocket viewing state fragility (HIGH)**
- `sendWsMessage` silently dropped viewing messages when WS not OPEN
- Fix: added `viewingAgentRef` that persists across reconnects and replays on `ws.onopen`
- Tab visibility gating: hidden tab should not suppress notifications

**`unread_count` not guarded (MEDIUM)**
- 4 sites in `agent_dispatcher.py` incremented `unread_count` without checking `_is_agent_in_use`
- Fix: added guards matching the push notification pattern

**Agent not stopped on terminal task transitions (HIGH)**
- Non-worktree approve left agent in IDLE state
- Cancel from REVIEW didn't stop agent (guard only checked EXECUTING/MERGING)
- Fix: added agent stop + tmux kill on all terminal transitions

**IDLE counted as "active" (MEDIUM)**
- Frontend Active tabs and backend stats included IDLE agents
- Fix: excluded IDLE alongside STOPPED

**tmux_pane = None race (MEDIUM)**
- Line 4759 set `agent.tmux_pane = None`, line 4775 passed it to `_is_agent_in_use` (always None)
- Fix: saved pane reference before nulling

**Dead config removed**
- `MAX_IDLE_AGENTS` defined but never used

### NOTE
These changes remain uncommitted on master. They were applied across 3 interactive sessions.

### 2026-03-03 (afternoon) | Task: Hardening Sprint | Status: success

### What was done

**CORS security (af9a435)**
- Replaced `allow_origins=["*"]` with configurable `CORS_ORIGINS` env var
- Default: `["http://localhost:5173"]` (dev only)

**Backend unit tests (4eab247)**
- 64 comprehensive tests added
- Fixed broken mixed content assertion in FilePreview tests

**Constants dedup (4254163, ffbbbbf)**
- Frontend: extracted magic numbers to `constants.js`
- Backend: extracted magic numbers to module-level constants in `main.py`

**Schema/code cleanup (312ba81, 8fdf930, 0216e82)**
- Deduplicated `AgentBrief`/`AgentOut` in `schemas.py`
- Cleaned up `agent_dispatcher.py`
- Extracted `PRAGMA table_info` helper in `database.py`

### 2026-03-03 (evening) | Task: Release Prep Assessment + Bug Fixes | Status: success

### What was done

**Release preparation assessment (RELEASE_PREP.md)**
- Full codebase audit for OSS release readiness
- Identified Linux-only code paths (`/proc/meminfo`, `/proc/loadavg`) — macOS compat blocker
- Missing deps: `python-dotenv`, `requests`, `psutil` not in `requirements.txt`
- Missing files: `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `install.sh`, `scripts/`
- Package name still `"vite-temp"` in `package.json`
- No CI/CD pipeline (no `.github/` directory)
- `ca.pem` and personal paths in git history
- `.env.example` missing `CORS_ORIGINS`, `DISABLE_AUTH`, `HOST_CLAUDE_DIR` vars
- Still unfixed from prior audit: `_detectPlanIdx()` defaults to index 0 on parse failure (P0 safety)

**Make task merging synchronous (dd9e0e4)**
- Approve was launching an unnecessary agent for `git merge --no-ff`
- Changed to direct synchronous git merge (completes in <100ms)
- Conflict handling: auto `git merge --abort` → task status CONFLICT

**Successor "Continued →" link bug fix**
- `_compute_successor_id` found sub-agents via `parent_id` — sub-agent mistakenly shown as successor
- Fix: added `Agent.is_subagent == False` filter in `_compute_successor_id`
- Same fix in `resume_agent` — sub-agents were blocking resume of parent agent

**Replace robot icons with folder icons (11c2683)**
- ProjectsPage: robot → folder icons for project cards

**Reject task orphan agent fix**
- Rejecting a task left the old agent running (orphan process)
- Now: reject stops agent (STOPPED status + kill tmux session) before setting task REJECTED

**Worktree unconditional creation bug (in d2e7204)**
- `_create_task_agent()` always created worktree regardless of user toggle
- Added `use_worktree` field to Task model, `TaskCreate`/`TaskOut` schemas, DB migration
- 6 files changed: models, schemas, database, main, agent_dispatcher, NewTaskPage

**TaskStatus enum typo (HTTP 500)**
- `TaskStatus.COMPLETED` used but enum defines `TaskStatus.COMPLETE`
- Crashed `GET /api/projects` → both Git page and Create Task page returned 500
- Lesson: enum member names must exactly match — no auto-completion inference

### Problems encountered
1. `popRef` undefined caused silent crash on popover click — React didn't show useful error
2. Merging via agent was unnecessary overhead for a simple `git merge --no-ff`
3. Sub-agents shared `parent_id` with real successors → ambiguous queries
4. Reject didn't stop the associated agent → orphan processes accumulated
5. Worktree toggle on frontend had no effect — backend ignored it
6. `COMPLETED` vs `COMPLETE` enum typo caused cascading HTTP 500s

### Lessons learned
- **Enum member names are exact**: `TaskStatus.COMPLETED` ≠ `TaskStatus.COMPLETE`. Always grep for the enum definition before using a status value. Consider adding a linter or test that validates all enum references.
- **Sub-agent vs successor disambiguation**: Both use `parent_id` but serve different purposes. Always filter `is_subagent == False` when looking for real successors. This applies to `_compute_successor_id`, `resume_agent`, and any future parent→child queries.
- **Synchronous git operations > agent-based**: For simple deterministic operations like `git merge --no-ff`, direct subprocess is better than spawning an agent. Agents are for tasks requiring intelligence/iteration.
- **Reject must clean up agent**: Any state transition that obsoletes an agent (REJECTED, CANCELLED) should stop the agent and kill its tmux session. Check all terminal transitions for cleanup.
- **Schema field propagation**: Adding a new toggle requires changes in: model → schema → DB migration → API endpoint → dispatcher logic → frontend. Missing any one link silently breaks the feature.
- **Release prep checklist**: Before OSS release: LICENSE file, install.sh, requirements.txt completeness, remove personal paths, add CI/CD, macOS compat, screenshots/demo GIF.

### 2026-03-04 (early morning) | Task: Comprehensive Tasks Module Testing | Status: success

### What was done

**89/89 API tests passed** (`test_tasks_module.py`)
- 16 test groups: creation, filtering, counts, detail, update, dispatch, cancel, reject, approve, try/revert, state machine, concurrency, edge cases, legacy v1, integrity, schema
- Test helper bug: `if body` treats `{}` as falsy — fixed to `if body is not None`

**3 parallel critic agents identified 21 issues:**

HIGH severity:
1. `completed_at` not set in FAILED paths of `_harvest_task_completions` — failed tasks excluded from weekly stats
2. No locking for concurrent git operations on same project — parallel approve/try-changes can corrupt repo
3. No row-level locking on state transitions — TOCTOU races (dispatch+cancel, approve+reject)

MEDIUM severity:
4. `try-changes` doesn't verify current branch before merging
5. `approve_task_v2` bypasses MERGING state — transition not in state machine
6. `reset --hard` in revert-try can destroy post-merge commits
7. No task-level timeout — executing tasks can run forever
8. Worktree/branch not cleaned on FAILED tasks — orphans accumulate
9. No `max_length` on description field
10. No double-click protection on list-view action buttons
11. Race condition between dismiss and submit in NewTaskPage
12. Orphaned worktrees from test dispatches (cleaned manually)

LOW severity:
13. `tmux_pane` not cleared in `reject_task_v2`
14. `auto_dispatch` with invalid project creates stuck PENDING tasks
15-21. Various frontend UX gaps (polling, pagination, keyboard access, dead code)

**20/21 fixes implemented in follow-up session:**
- Backend: `completed_at` set on all FAILED paths, `validate_transition()` in harvest, `tmux_pane` cleared on reject, main branch checkout before try-merge, commit SHA validation before `reset --hard`, project validation on auto_dispatch, `limit` capped with `Query(ge=1, le=1000)`, validation constraints on `TaskCreate`
- Frontend: loading states on dispatch/delete/reject/cancel buttons, polling stopped for terminal-state tasks, dismiss/submit race guarded with `submittingRef`, keyboard accessibility on ReviewCard, dead code removed
- 88/89 backend tests pass (1 pre-existing), 20 pre-existing frontend test failures unchanged

### Lessons learned
- **Test `if body` carefully**: Python treats `{}` as falsy. Use `if body is not None` for optional dict params.
- **Git operations need locking**: Concurrent approve/try-changes on same project can corrupt the repo. Need per-project mutex for git operations.
- **TOCTOU in state transitions**: Without row-level locking, two concurrent requests can both read REVIEW and one approves while the other rejects. Consider `SELECT ... FOR UPDATE` or optimistic locking.
- **Critic agent pattern works**: Launching 3 specialized critics (backend, frontend, logs) in parallel catches issues that automated tests miss — especially architectural/design issues.

### 2026-03-04 (early morning) | Task: Plan Mode Agent Bug + Strategic Analysis | Status: success

### What was done

**Plan mode exec agent bug fix**
- Agent `717b61f5d9a8` wrote plan via `ExitPlanMode` but no follow-up agent was spawned to execute it
- Root cause: exec mode agents (`claude -p`) exit after `ExitPlanMode` auto-approve → `_harvest_completed_execs` transitions to IDLE → task goes to REVIEW. **No code path to create a follow-up agent.**
- Fix: added auto-continue logic in `_harvest_completed_execs` — detect `ExitPlanMode` in exec result metadata, create PENDING follow-up message with `source="plan_continue"` to execute the plan
- Only applies to non-`cli_sync` agents (sync agents have their own successor detection)

**Strategic gap analysis (vs Hu Yuanming's article on managing 10 Claude Code instances)**
Priority-ranked architectural gaps:
1. **Plan Mode ("review plan before execution")** — biggest gap. AgentHive executes first, reviews after. Saves 90% wasted compute when direction is wrong.
2. **Cross-task knowledge (PROGRESS.md)** — agents are "memoryless islands." Same mistakes repeated by different agents. Need shared experience injection.
3. **AI-manages-AI layer** — dispatcher is a "blind scheduler" (sees status, not content). Article uses Manager Claude for real-time monitoring.
4. **Closed-loop verification** — agents don't self-verify (no test runs, no build checks in prompts).
5. **Success rate self-optimization** — no failure pattern classification, no automatic prompt tuning.
6. **Task creation friction** — too many fields. Need "one voice command, auto-infer everything" fast path.
7. **Batch operations** — no batch approve/reject for multiple REVIEW tasks.

### Problems encountered
1. Exec mode vs sync mode lifecycle is fundamentally different — exec agents have no mechanism for successor detection or auto-continue
2. `preventDefault()` cannot be called after `await` in async click handlers — must check double-tap synchronously

### Lessons learned
- **Exec vs sync mode divergence**: Every interactive feature (ExitPlanMode, plan continuation, successor detection) needs separate handling for each mode. Exec agents exit after completion; sync agents have ongoing tmux sessions.
- **Plan Mode is highest leverage**: Reviewing a 30-second plan vs reviewing a 30-minute execution. Maps directly to the plan agent bug — system partially supports plan mode but lifecycle is broken for exec agents.
- **Inject shared knowledge into task prompts**: `_build_task_prompt()` should include PROGRESS.md or equivalent cross-task context to prevent repeated mistakes.
- **Async click handler pattern**: `preventDefault()` must happen synchronously before first `await`. Check condition synchronously, prevent immediately, then do async work.

### 2026-03-04 | Task: Pipeline test: add hello.txt | Status: success
- What: Created hello_pipeline_test.txt in project root with content "Hello from pipeline test!"
- Resolution: Straightforward file creation and commit
- Lesson: No issues — pipeline test successful

### 2026-03-04 | Task: Pipeline test: add LICENSE header | Status: success

- What: Added one-line comment header to RELEASE_PREP.md
- Resolution: Added `# This file was reviewed by pipeline test. Do NOT modify any other content.` at the top of the file
- Lesson: When Edit tool doesn't show up in git status, use bash to verify filesystem changes before assuming success

### 2026-03-04 | Task: Concurrent test 2: HIGH priority | Status: success

- What: Execute high-priority concurrent test task
- Resolution: Ran `echo task2_high_priority_done` and verified output
- Lesson: Straightforward — no issues

### 2026-03-04 | Task: Pipeline test: non-worktree echo | Status: success

- What: Run echo and date commands in non-worktree context
- Resolution: Executed `echo Pipeline test successful!` (output: "Pipeline test successful!") and `date` (output: "Wed Mar  4 02:22:33 PM PST 2026")
- Lesson: Straightforward — no issues

### 2026-03-04 | Task: Pipeline A: add test marker file | Status: success

- What: Created marker file for pipeline A test verification
- Resolution: Created `test_pipeline_marker_a.txt` with content `PIPELINE_A_MARKER_SUCCESS` and committed to master
- Lesson: Straightforward — no issues

### 2026-03-04 | Task: Concurrent test 1: normal priority | Status: success

- What: Execute normal priority concurrent test task
- Resolution: Ran `echo task1_normal_priority_done` and verified output
- Lesson: Straightforward — no issues

### 2026-03-04 | Task: Pipeline B: intentionally incomplete task | Status: success

- What: Created marker file for pipeline B test verification
- Resolution: Created `test_pipeline_marker_b.txt` with content `MARKER_B` and committed to worktree
- Lesson: Straightforward — no issues

### 2026-03-04 | Task: Pipeline test: add LICENSE header (REDO, attempt #2) | Status: success

- What: Fix incorrect LICENSE header comment in RELEASE_PREP.md
- Attempts: Previous attempt #1 added wrong wording: `# This file was reviewed by pipeline test. Do NOT modify any other content.` instead of the required `# Reviewed by AgentHive pipeline test v2.`
- Resolution: Changed comment to exact required wording: `# Reviewed by AgentHive pipeline test v2.` at top of RELEASE_PREP.md
- Lesson: Follow exact rejection feedback for wording — minor variations break requirements

### 2026-03-04 | Task: Concurrent test 3: normal priority | Status: success

- What: Execute normal priority concurrent test task
- Resolution: Ran `echo task3_normal_priority_done` and verified output
- Lesson: Straightforward — no issues

### 2026-03-04 | Task: Race test v2: double dispatch | Status: success
- What: Fixed TOCTOU race in `dispatch_task_v2` and `_dispatch_pending_tasks` using atomic compare-and-swap (CAS) pattern. Added 5 race condition tests.
- Fix: `db.query(Task).filter(Task.id == id, Task.status == expected).update(...)` instead of read-modify-write. Returns `rows == 0` when another thread already changed the status.
- Also: `_create_task_agent` now flushes instead of committing, so the caller can rollback agent+message if the CAS fails.
- Lesson: **Atomic CAS for SQLite concurrency**: `UPDATE ... WHERE status=:expected` is the correct pattern for SQLite (no row-level locks). The WHERE clause acts as a compare-and-swap — if another transaction changed the status, `rowcount == 0` and the caller knows to abort. Always flush (not commit) in sub-functions when the caller needs atomic multi-step operations.

### 2026-03-04 | Task: Dispatch flow test | Status: success

### What was done
- Added `test_dispatch_flow.py` with **59 tests** covering the full task dispatch lifecycle
- 10 test groups: dispatch endpoint, auto-dispatch, `_dispatch_pending_tasks`, `_create_task_agent`, `_build_task_prompt`, `_harvest_task_completions`, `_check_scheduled_tasks`, full flow integration, state machine edge cases, edge cases
- All 123 backend tests pass (59 new + 64 existing)

### Lessons learned
- **FK constraints in tests**: When mocking `_create_task_agent` to return an agent ID, that ID must exist in the agents table to satisfy FK constraints. Pre-create agent records before dispatch.
- **Task model defaults not set outside DB**: Creating `Task(title="X")` without `db.add()/commit()` leaves `attempt_number=None` (not the column default `1`). Always set required fields explicitly in unit tests.
- **Push module uses its own SessionLocal**: `push.send_push_notification` and `is_notification_enabled` create their own DB sessions via `SessionLocal`, which connect to a different in-memory DB in tests. Patch `push.is_notification_enabled` to `return False` to avoid this.
- **Dispatcher methods don't commit**: `_check_scheduled_tasks` relies on `_tick()` to commit at the end. When testing individual methods, call `db.commit()` after.
- **Pre-existing bug**: `GET /api/v2/tasks/{id}` detail endpoint crashes with `TaskDetailOut() got multiple values for keyword argument 'review_artifacts'` — `review_artifacts` exists in both `TaskOut` (base) and the `**TaskOut.model_validate(task).model_dump()` spread.

## 2026-03-06 — Daily Insights
1. Try/Revert has a multi-task conflict bug: if Task A is tried (merged to main) and then Task B is also tried, reverting Task A via `git reset --hard` silently rolls back Task B's merge too — `try_base_commit` of Task B now points to a dangling intermediate state.
2. Non-worktree tasks have `try_base_commit` set at agent creation time (`agent_dispatcher.py:1779`), which means the "Try" button never appears in the frontend — only "Revert" shows, creating a semantic mismatch.
3. `approve_task_v2` endpoint (`main.py:2523-2575`) fails to clear `try_base_commit` after approval; `reject_task_v2` correctly clears it at line 2605 — asymmetry bug.
4. Global `ToastContext` created (`frontend/src/contexts/ToastContext.jsx`) to replace per-page toast implementations — all pages should use `useToast()` instead of local state.
5. AgentHive vs OpenClaw differentiators: Try/Revert, Retry-with-context (feeding `agent_summary` to next attempt), and the full REVIEW→MERGING→CONFLICT merge state machine.
6. PROGRESS.md corruption by auto-summary: LLM was asked to output entire file plus new content, repeatedly truncated history — fix: generate only new section and append programmatically.
7. `_last_summary_date` was a local variable in dispatcher `run()`, reset on every restart — moved to `SystemConfig` DB table for persistence.
8. Auto-summary output validation: two guards for (1) conversational refusal markers and (2) output length <60% of original.
9. Anthropic token usage API (`/v1/oauth/usage`) returns 429 without `User-Agent` header — adding `User-Agent: claude-code/{version}` resolves it.
10. Token usage polling changed from auto 30s to manual refresh + 120s server-side cache.
11. Mobile clipboard "operation not supported" root cause: scroll-triggered `touchend` events lack browser "user activation", so `navigator.clipboard.writeText()` fails.
12. Clipboard fix: track `touchStartY`, skip double-tap if moved >10px (scroll), plus `.catch(() => {})` on all 7 `clipboard.writeText()` call sites.
13. Claude CLI image metadata text (`[Image: original NxN...]`) leaking into chat UI — `_is_image_metadata()` regex filter added to `_parse_stream_parts` and `_parse_session_turns`.
14. Orphan session JSONL cleanup: `cleanup_source_session()` added at all 4 orphan-producing code paths (session rotation, stale exhaustion, no-cache fallback, missing file at launch).
15. `permanently_delete_agent()` was missing session subdirectory and cache cleanup — updated to use `cleanup_source_session()` + `evict_session()`.
16. Session cascade bug: CWD-based detection without content verification causes agents on same project to adopt each other's sessions — content-based verification (matching first user message) added.
17. Task time properties redesigned: `scheduled_at` split into `notify_at` (push only) and `dispatch_at` (auto-dispatch to PENDING queue), with DB migration preserving existing data.
18. `_check_scheduled_tasks` rewritten into two passes: one for `notify_at` (notification) and one for `dispatch_at` (auto-dispatch).
19. Pydantic `model_fields_set` used to distinguish "field not sent" from "field explicitly set to null" in task update endpoint.
20. Split-screen uses `MemoryRouter` per pane with `RouterIsolator` wrapper (resetting `UNSAFE_LocationContext`, `UNSAFE_NavigationContext`, `UNSAFE_RouteContext`) to bypass React Router v7 nested router prohibition.
21. Cross-pane state sync uses `CustomEvent` dispatching: `agent-mute-changed`, `agent-star-changed`, `agent-renamed`, `agents-data-changed`, `projects-data-changed`.
22. `DraggableFab` uses `{right, bottom}` offsets (not absolute `{x, y}`), adapts on resize; dynamically detects fixed/sticky bottom bars via DOM measurement for `minBottom` boundary.
23. Known issue: split-screen `window.location.pathname` always returns `/split` instead of per-pane MemoryRouter path, breaking WS notification suppression logic.
24. PLANNING state is purely passive — does not auto-spawn agent or generate plan; tasks remain until manually dispatched.
25. Remaining gaps from Hu article: Plan Mode (propose before execute), closed-loop verification (Verify button spawning sub-agent), failure mode classification with auto-tuning.


## 2026-03-07 — Daily Insights
1. Claude Code v2.1.71 broke session auto-detection: debug logs (`~/.claude/debug/{session_id}.txt`) are no longer written by default, so Tier 1 PID-based matching in `_auto_detect_cli_sessions` and `_scan_for_session_jsonl` always returns `None` for new sessions.
2. Tier 2 mtime-based session detection added: when debug-log PID matching fails, match tmux panes to session JSONLs by correlating process start time (`/proc/{pid}/stat` field 22) with JSONL file creation time — picks the candidate with smallest time delta.
3. `_MAX_START_DELTA = 1800` (30 min) threshold prevents stale Claude processes (hours-old) from stealing sessions belonging to newly launched processes on the same project.
4. Random tmux sessions (non `ah-*` names) now auto-detected via Tier 2 within ~15 seconds, then tmux session is renamed to `ah-{agent_id[:8]}` for reliable future matching via Tier 0.
5. `_scan_for_session_jsonl` in `_launch_tmux_background` (main.py) also got a mtime-based fallback using `launch_start` timestamp to find the JSONL created after launch, fixing the "no session JSONL after 5 attempts" error on v2.1.71.
6. `_detectPlanIdx()` P0 safety fix: generic "yes"/"approve" now maps to index 2 (manual approval) instead of index 0 ("clear context & bypass"), preventing accidental destructive action on ambiguous user input.
7. WebSocket 403 reconnect loop root cause: `WebSocketContext.jsx` attempted connection even when `getAuthToken()` returned null, creating a permanent 403→reconnect→403 cycle that broke all real-time UI updates (messages, agent sync, streaming).
8. Duplicate pane ownership bug: agent in ERROR status retains `tmux_pane` assignment, blocking discovery loop from assigning the pane to a new SYNCING agent — ERROR agents should have `tmux_pane` cleared.
9. Dead pane in `_dispatch_tmux_pending`: when `verify_tmux_pane` fails, code clears `tmux_pane` but doesn't transition agent status — agent stays SYNCING forever with no pane, making queued messages permanently undeliverable.
10. Error-swallowing try/except audit: converted ~20 silent `except: pass/continue` blocks to `logger.warning` across `agent_dispatcher.py` (14), `websocket.py` (2), `orphan_cleanup.py` (3), and `main.py` (1) — these were hiding real failures in sync, pane detection, and cleanup paths.
11. Service Worker Workbox `NetworkFirst` caching on `/api/*` routes breaks Safari/iOS video playback: `<video>` requires HTTP Range protocol (206 Partial Content), but Workbox intercepts Range requests and returns cached full responses without `Content-Range` headers, causing `NotSupportedError`.
12. Video fix: added `NetworkOnly` route for `/api/(files|uploads)/` in `vite.config.js` `runtimeCaching`, placed after thumbnail `CacheFirst` route so `.thumb.jpg` files retain 7-day cache while video/file requests bypass SW entirely.
13. Auto-summary data source changed from "completed tasks only" to "all non-subagent agent sessions with messages that day" — ensures insights are captured even when no tasks reach COMPLETE status.
14. Auto-summary two-pass context strategy implemented: Pass 1 guarantees every session has at least a slim summary (header + first user msg + last assistant reply, each ≤1500 chars); Pass 2 distributes remaining budget evenly to expand full conversations.
15. Auto-summary deduplication: existing PROGRESS.md content (last 50K chars) is now fed to the LLM with explicit instructions to skip already-captured insights and use "Updated: X is now Y" format for corrections.
16. Auto-summary prompt changed to stdin pipe (`[CLAUDE_BIN, "-p", "-"]` with `input=prompt`) instead of CLI argument to avoid OS `ARG_MAX` limit on large session contexts (500K+ chars).
17. Auto-summary output format changed from per-session sections to flat numbered insights list with `## {date} — Daily Insights` heading, organized by conversation with `[HH:MM–HH:MM]` timestamp headers in the input context.
18. `DraggableFab` drag lag root cause: Tailwind's `transition-all` (150ms ease) was animating every `transform: translate3d()` update during drag, causing the button to "chase" the finger instead of following it — measured 61.8px average lag, reduced to 34.8px (-44%) after fix.
19. `backdrop-blur-sm` on the split-screen exit button caused GPU-intensive recomposition on every drag frame — replaced with opaque `bg-surface` background; `will-change: transform` added to promote element to its own compositing layer.
20. DraggableFab click vs drag separation: moved tap detection from `click` event to `mouseup`/`touchend` handler checking `moved` flag, then blocking all `click` events via `preventDefault` + `stopPropagation` to prevent drag-end from triggering navigation.
21. Split enter/exit buttons now share a single `storageKey` (`ah:fab-pos-split-v3`) so the button position persists across mode transitions instead of jumping between two saved positions.
22. Agent report fabrication detected: an agent attributed bugs to specific commits (`d3e1130`, `2760f85`) that never touched the claimed code paths — the "regressions" were actually pre-existing gaps, not caused by those commits. Always verify agent-generated root cause attribution against actual `git diff`.
23. Semantic RAG with `sentence-transformers/all-MiniLM-L6-v2` implemented and benchmarked (+28% precision, +38% recall) but shelved to `feature/semantic-rag` branch — PyTorch ~2GB dependency contradicts the lightweight self-hosted tool positioning.
24. Lightweight FTS5 RAG (`store_insights`, `query_insights`, `ProgressInsight` model, backfill endpoint) cherry-picked back to master as the production RAG solution — zero extra dependencies, uses SQLite's built-in full-text search with 7-day recency boost.
25. Release readiness assessment: with Tasks page disabled, remaining modules (Projects, Agents, Git, Monitor, Split Screen) are complete enough to ship; P0 blockers are screenshots/demo GIF for README, `install.sh` script, complete `.env.example`, and macOS compatibility (code depends on `/proc/meminfo`).


## 2026-03-08 — Daily Insights
1. Backend thumbnail system added: `/api/thumbs/{project}/{path}` endpoint generates max-1200px JPEG thumbnails (quality 80) via Pillow, cached in `.thumbcache/` directories — 35MB PNG → 275KB JPEG (127x compression), first generation ~1s, cached serves in 2ms.
2. Initial thumbnail size of 400px was too blurry for fullscreen lightbox on phones — increased to 1200px (230-275KB) which is sharp enough on any phone screen while still loading fast.
3. `resolveFileUrl()` in `formatters.jsx` didn't handle `.agenthive/uploads/` paths — user-uploaded images echoed in agent messages routed to `/api/files/{project}/...` → 404; fixed with `uploadMatch` branch routing to `/api/uploads/`.
4. `ImagePreview` error handling redesigned from single `error` state (silently hid image) to two-stage: `thumbFailed` → try full-res `src` → then show error UI with Retry button.
5. Lightbox progressive loading: shows 1200px thumbnail immediately, preloads full-res via `new Image()` in background, auto-swaps when loaded; if full-res never loads, user still sees usable thumbnail.
6. iOS Safari PWA completely ignores `<a download>` attribute and programmatic `a.click()` — the only reliable download method is `navigator.share({ files: [file] })` which opens the native share sheet for "Save Image".
7. `window.open` in PWA standalone mode opens Safari in a new tab where self-signed certs aren't accepted — results in white screen; must stay within PWA browsing context.
8. Download strategy by platform: iOS PWA standalone (detected via `(display-mode: standalone)` + `ontouchend`) → `navigator.share`; desktop/Android → blob URL + `<a download>`; fallback → `window.location.href` with `Content-Disposition: attachment`.
9. `permanently_delete_agent()` had file-before-DB ordering bug — deleted files first, then if `db.commit()` failed, files were gone but DB still referenced them; fixed to commit DB first.
10. `Message.agent_id` and `Task.agent_id` ForeignKey missing `ondelete="CASCADE"` — deleting an Agent without first deleting Messages triggers constraint error.
11. `evict_session()` had no worktree support — didn't clean worktree session cache directories, causing disk leak; also lacked try/except around `os.unlink`/`shutil.rmtree`.
12. 10+ code paths set `agent.status = AgentStatus.STOPPED` but only 2-3 called `tmux kill-session` — created `_kill_agent_tmux()` helper and added it to all stop sites.
13. `orphan_cleanup.py` only cleaned JSONL files and logs, never checked tmux sessions — added `ah-*` pattern scanning against DB to detect and kill orphan tmux sessions.
14. SYNCING agent with `tmux_pane` skipped stale check due to `elif` in `_reap_dead_agents` — Claude CLI idle at prompt counted as "alive" forever; fixed by adding session file freshness check even when pane is alive.
15. `send_tmux_message` Ink TUI timing: Escape/C-a/C-k control sequences are treated as literal text by Claude CLI's Ink TUI (regression); reverted to C-u only, kept increased delays (0.05s → 0.2s) and Enter retry logic.
16. Auto-summary March 7 bug confirmed: trigger fires at UTC midnight (16:00 PST) but queried "today UTC" (empty) instead of "yesterday UTC"; fix in `05ee345` added `target_date=yesterday` but March 7 data required manual backfill from backup DB.
17. RAG insights not injected on initial agent launch: `create_agent` → `_launch_tmux_background` bypassed `_build_agent_prompt` entirely — first message never got RAG; fixed by calling `_build_agent_prompt` before launch.
18. Five independent message dispatch paths (`launch_tmux_agent`, `create_agent` non-tmux, `send_agent_message` direct, `_dispatch_tmux_pending`, `_dispatch_pending_messages`) each independently handled RAG, message creation, and prompt wrapping — consolidated into single `_prepare_dispatch` method.
19. `_AGENTHIVE_PROMPT_MARKER` (`<!-- agenthive-prompt -->`) embedded in wrapped prompts prevents sync loop from re-importing them as duplicate USER messages — replaces fragile string-prefix matching.
20. Codebase fragmentation audit found 10 anti-patterns: 18 agent-stop paths, 5 message-failure paths, 24 direct task-status assignments, 11 session/pane clearing paths, inconsistent WS broadcasts — each with different side effects.
21. Four consolidation refactors completed via parallel worktree agents: `stop_agent_cleanup()` (26 inline stops → 1), `_fail_message()`/`_fail_pending_messages()` (5 FAILED paths → 2), `TaskStateMachine.transition()` (24 assignments → validated transitions with auto-timestamps), `_clear_agent_session()`/`_clear_agent_pane()` (18 clears → 2 with consistent notification).
22. Critical silent-hang bug fixed: max-retries-exhausted path in `_reap_dead_agents` cleared `session_id` with zero notification (no system message, no WS emit) — agent appeared frozen in UI indefinitely.
23. `orchestrator/utils.py` created to deduplicate `utcnow()` (5 identical copies across models/dispatcher/agent_dispatcher/task_state/main) and `truncate()` (2 copies).
24. Frontend deduplication: `useAsyncHandler` hook replaced 9 identical async handler patterns; `ErrorAlert` component replaced 4 identical error banners; `ReviewCard` ternary chains replaced with lookup map.
25. Backup system made configurable: `BACKUP_ENABLED` toggle and `BACKUP_DIR` custom path in `.env`/`config.py`; Monitor page got Backup card with snapshot count, total size display, and "Purge all" button.


## 2026-03-10 — Daily Insights
1. Session list inflation root cause: `list_project_sessions` (main.py:1810) showed 1266 sessions because 902/994 agents without `session_id` were subagents — subagents never get their own `session_id` (conversations embedded in parent JSONL), so they bypassed dedup and each appeared as a separate session; fixed by adding `Agent.is_subagent == False` filter.
2. Parent-subagent bidirectional relationship added: `Agent.children` self-referential relationship via `parent_id` with `remote()` annotation; `permanently_delete_agent()` now recursively deletes all children agents and their messages before deleting the parent.
3. Stale agent cleanup system added to `orphan_cleanup.py`: `scan_stale_agents()` and `cleanup_stale_agents()` delete stopped/error parent agents older than 30 days (cascading to children), skipping starred agents and those with active tmux sessions; also detects 271 orphan subagents whose `parent_id` points to already-deleted parents.
4. Task card multi-select mode (matching Agents page pattern): permanent checkboxes removed from all card types; clipboard icon in PageHeader's `selectAction` slot enters selection mode with conditional checkboxes, select-all toolbar, and bottom action bar for bulk Move/Dispatch/Delete.
5. `CardShell` shared component created (`cards/CardShell.jsx`) to DRY pop-out expand styling across all 6 card types — exports `CardShell` wrapper (scale/shadow/ring transitions) and `cardPadding()` helper; cards expand to ~50% taller with spring easing `cubic-bezier(0.34,1.56,0.64,1)`.
6. Tappable inline tags on task cards: model cycles through Opus/Sonnet/Haiku, effort cycles through l/m/h, priority toggles N/H — each calls `updateTask` API on tap; `TagPicker` popover component added for selection UI with smooth transitions.
7. Git page merge UX redesigned: two filled directional buttons (green ←Merge, purple →Merge) replaced with single ghost "Merge..." button opening a dropdown with "Merge {branch} → {current}" and "Merge {current} → {branch}" options — natural source→destination naming, cyan soft fill (`bg-cyan-500/15`) matching existing app button patterns.
8. "Commit & Push" button added to Git Status section — upload icon, only visible when uncommitted changes exist, spawns AUTO agent that reviews diffs, stages files (skips secrets), writes commit message, and pushes to origin.
9. Branch cleanup selection mode: "Clean" button on Branches section enters checkbox mode for user to select which branches to process; confirm spawns agent to merge-then-delete selected branches; Worktrees section keeps "Merge All" for one-click cleanup — separation avoids destructive surprises.
10. Removed "Work autonomously — do not ask for confirmation" and "Complete the entire task in a single turn" from `_build_task_prompt` — agents can now investigate, plan, and ask clarifying questions naturally instead of being forced into single-pass execution.
11. Auto-summary March 8 data loss confirmed: sub-agent of code review agent `a7462c1aef86` ran `git reset --hard e273bd3` at 13:28 PDT March 9 on the main repo, destroying uncommitted PROGRESS.md; root cause is `_auto_apply_progress_summary()` writes to working tree but never commits; data recoverable from 4 dangling git stash objects and intact in FTS5 `progress_insights` table.
12. Auto-summary auto-commit fix implemented: `_auto_apply_progress_summary()` now runs `git add PROGRESS.md && git commit` immediately after writing, and `git_manager.py:reset_hard()` stashes PROGRESS.md before executing (protects orchestrator-initiated resets only, not agent bash commands).
13. March 9 cc-orchestrator auto-summary not triggered: pre-fix code had silent `_progress_job_get()` returning truthy → bare `continue` with zero log output, causing cc-orchestrator to be silently skipped while gsv-tc-fusion proceeded normally; fixed in commit `7d8a229` but server wasn't restarted before the 17:00 UTC trigger.
14. GSV duplication root cause was NOT dispatcher restarts: a single investigation agent (173d2fbc4618) called `_auto_apply_progress_summary()` 5 times for backfill before dedup guard existed; the agent itself authored commit `26cbbe7` adding the `if date_heading in existing` guard, after which the 6th attempt was correctly skipped.
15. GSV auto-summary quality assessment: March 8 section scored 8/10 (high specificity, verifiable file/function names, no fabrications detected), March 9 scored 7/10 (good quantitative claims matching code, some conceptual overlap between items).
16. Dangerous operations audit identified 4 CRITICAL endpoints: `git_manager.reset_hard()`, `purge_backups()` (deletes ALL backups, no confirmation), `delete_trash_folder()` (bypasses recycle bin), `permanent_delete_agent()` (cascade delete); HIGH risk: all agents run with `--dangerously-skip-permissions` enabling arbitrary bash commands including `git reset --hard`.
17. Planning agent pipeline produced `git_safe_wrapper.sh` design: a PATH-prepended shell script that intercepts destructive git commands (`reset --hard`, `clean -f`, `checkout -- .`, `push --force`) when `AGENTHIVE_MANAGED=1` is set; allows `reset --hard` only inside `.claude/worktrees/` directories; `AGENTHIVE_REAL_GIT` env var points to real binary for passthrough.
18. AI batch triage agents created for task inbox processing — correctly identify junk tasks (gibberish titles) vs real tasks, but all 3 attempts asked for user confirmation instead of executing updates autonomously; triage prompt needs explicit "execute without asking" instruction.
19. Todoist flat-list task design (hairline dividers, no card backgrounds, 56-64px row height) was implemented and immediately reverted by user — user prefers card-based design with substance over spreadsheet-style density.
20. `TaskExpandedContent` compact inline editor: title + description merged into single capsule prompt textarea, project selector + notify_at rendered side-by-side below — also reverted; final direction is pop-out height expansion only, with step-by-step user-guided additions.
21. Git nav icon visual weight investigation: the git branch icon (circles + lines) appears heavier than other nav icons (simple lines); reducing from `w-6 h-6` to `w-5 h-5` made it too small; `SplitScreenPage` already used `w-5 h-5` — maintained `w-6 h-6` status quo.
22. `POST /api/git/{project}/checkout/{branch}` endpoint added using existing `GitManager.checkout()` — enables double-click branch checkout from GitPage with loading spinner and automatic data refresh.
23. Updated: Auto-summary write path correctly targets main repo (reads `Project.path` from DB), NOT the agent's worktree — the vulnerability was purely the lack of git commit after write, not writing to the wrong location.


## 2026-03-11 — Daily Insights
1. Notification system refactored to hook-triggered, single-path architecture: `message` notifications now triggered solely by `/api/hooks/agent-stop` (Claude Code Stop hook). Eliminated JSONL polling triggers, frontend browser Notification API calls, and defer/flush heuristics (`pending_push_body`, `_hook_flush_ready`). Three-channel model in `notify.py`: `notify_at` (always sends), `task_complete` (global toggle only), `message` (global toggle + per-agent mute + in-use detection).
2. In-use detection combines two independent signals: WebSocket viewing state (`ws_manager.is_agent_viewed()` — frontend sends agent set on page navigation and `visibilitychange`) and tmux pane attached state (`_refresh_pane_attached()` — polls `tmux list-panes` every dispatcher tick). Either signal true → suppress `message` notification.
3. Task and agent notification paths are logically decoupled: `task_complete`/`notify_at` channels are fired from within `agent_dispatcher.py` (dispatcher-internal lifecycle events), while `message` channel is fired exclusively via the external `/api/hooks/agent-stop` endpoint. They share only the `notify()` gateway and `push.py` delivery backend.
4. Telegram Bot notification backend removed — `_send_telegram()`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` all deleted. Web Push (VAPID) is now the sole delivery backend. Cleaned references from config.py, .env.example, README.md, RELEASE_PREP.md.


## 2026-03-13 — Daily Insights
1. Tool activity log was pure frontend state (`toolLog`) that cleared on navigation/refresh; now persisted to `ToolActivity` DB model with `GET /api/agents/{agent_id}/tool-activities` endpoint, default collapsed showing last 5 entries.
2. Message delivery tracking landed: new `delivered_at` column on `Message`, `UserPromptSubmit` hook marks web-sent messages as delivered, `message_delivered` WS event added, frontend shows grey vs green checkmarks for pending vs delivered.
3. `_dispatch_pending_messages` in `agent_dispatcher.py:4443` sets `delivered_at` but only emits `message_update(EXECUTING)`, not `message_delivered` — frontend checkmarks won't update in real-time until next refresh.
4. Agents launched via `claude -p` (non-interactive) never trigger `UserPromptSubmit` hook, so their messages never get `delivered_at` set — fix is to mark as delivered immediately at creation time for pipe-mode agents since delivery is guaranteed.
5. The first message sent at agent launch time (the initial prompt) also doesn't get marked delivered because the hook fires before the orchestrator has associated the message with the agent session — a gap in the launch flow.
6. No `PostCompact`/`AfterCompact` hook exists in Claude Code; compact end detection relies on sync loop detecting JSONL file size decrease or turn count decrease — this is the correct and only approach.
7. `SessionStart` with `source="compact"` fires after compaction completes and can serve as an indirect compact-completion signal, as an alternative/complement to file-size-based detection.
8. `_parse_incremental_lines` has a bug: assistant turn merging (combining consecutive assistant content blocks) causes incremental turn count to differ from full-parse turn count, triggering false compact detection (`compact_turn_decrease`) in the sync loop.
9. Compact timer sometimes starts at 45s instead of 0s because the `PreCompact` hook HTTP callback has latency — the frontend receives the start event late and begins counting from the delayed timestamp rather than the actual compact start time.
10. `serverTime.js` calibrate function correctly handles first-call (`_offset = sample` direct assignment) vs subsequent calls (α=0.3 EMA smoothing); additionally, calibration was added to `api.js` `request()` using HTTP `Date` response header so clock offset is ready before WebSocket connects.
11. Message chat ordering changed from `created_at` to `delivered_at` — undelivered messages (pending, scheduled) sort to bottom using `COALESCE(delivered_at, '9999-12-31')`, with `created_at`, `scheduled_at`, and `delivered_at` as three independent logical fields.
12. `escapeUrgent` logic was simplified from a complex condition (including `hookActive`, `is_generating`, `hasTmux`) to just `isExecuting || hasPendingInteractive` — this may cause the ESC button to not turn red in some edge cases where hook activity is happening but `isExecuting` hasn't been set yet.
13. The system uses 9 Claude Code CLI hook event types (SessionStart, PreToolUse, PostToolUse, PostToolUseFailure, SubagentStart, SubagentStop, PreCompact, Stop, Notification) all calling back to orchestrator HTTP endpoints — this is the central nervous system of the architecture.
14. Frontend runs on HTTPS (`https://localhost:3000/`) with self-signed certs via Vite dev server — plain `http://` connections will fail silently, a common source of "can't connect" confusion.
15. Tool bubbles display regressed — they were supposed to naturally segment conversation bubbles but started stacking incorrectly at the top; root cause traced to a specific commit's CSS/rendering changes.
16. Push notifications regressed after working correctly with hook-triggered + in-use suppression; the notification pipeline broke in a subsequent commit despite the notification system itself being unchanged.
17. Permission acquisition cards stopped displaying despite previously working with plan/interview-consistent styling — a rendering regression introduced by recent commits.
18. Stress testing with 100 concurrent echo agents succeeded (all responded), but queued message delivery tests showed agents only process 1-2 of 5 queued messages before the session stops — the queue dispatch pipeline doesn't reliably drain under concurrent load.
19. CLI-injected messages (`CLI_TEST_*` sent via tmux) were never acknowledged by agents in stress tests while web-sent messages (`TEST_*`) were — confirms tmux input delivery is less reliable than web-based message delivery under load.
20. Duplicate user messages appear in agent conversations — each message shows up twice, suggesting both the queue-operation and user JSONL entries are being parsed instead of deduplicating (violating the "parse ONE, skip the other" CLAUDE.md rule).
21. Plan mode parsing broke in a recent commit — the parser stopped recognizing plan mode content, requiring investigation across commit history to find which change introduced the regression.
22. Commit `ba5a66d` was a large 14-file, +499/−61 line commit covering delivery tracking, persistent tool activities, and frontend UX — this monolithic commit made regression bisection difficult and violated the "small atomic commits" convention.
23. When investigating regressions, the pattern of spawning parallel sub-agents to analyze individual commits proved effective for quickly bisecting which change introduced a bug across a series of commits.
24. The `AHIVE_AGENT_ID` environment variable set in tmux sessions is how hooks identify which agent triggered them — if this isn't set correctly during launch, hook callbacks can't associate events with the right agent.
25. Three independent timestamp fields (`created_at`, `scheduled_at`, `delivered_at`) must remain logically separate: `created_at` is when the user composed it, `scheduled_at` is when it's scheduled for future delivery, `delivered_at` is when it actually entered the agent's context — conflating any two breaks ordering semantics.
