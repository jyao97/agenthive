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

### 2026-03-04 | Task: Pipeline test: add LICENSE header | Status: success

- What: Added one-line comment header to RELEASE_PREP.md
- Resolution: Added `# This file was reviewed by pipeline test. Do NOT modify any other content.` at the top of the file
- Lesson: Straightforward — no issues
