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


## 2026-03-14 — Daily Insights

1. `UserPromptSubmit` hook fires BEFORE Claude writes to JSONL — the sync loop wakes but sees no new data, so `delivered_at` was only set on the next 30s poll cycle, causing 20+ second delivery delays; fix: mark `delivered_at` directly in the hook handler (`main.py:hook_agent_user_prompt`).
2. "Thinking" was a synthetic `tool_activity("start")` emitted by the backend with no matching `"end"` event — caused permanent double "Thinking" bubbles; fix: removed the fake event entirely since the frontend's existing `TypingIndicator` already handles the pre-tool state naturally.
3. `useStreamingAgents` had a `useEffect` that polled `agents` and overwrote WebSocket-driven `tool_activity` signals based on `is_generating` — agents visibly executing tools showed "syncing" on the list page; fix: simplified to pure hook-driven logic (any hook → active, Stop hook → inactive), removed `agents` parameter entirely.
4. Stale JSONL auto-kill (`agent_dispatcher.py:5731`) used `kill_tmux=False`, but `_auto_detect_cli_sessions` revived any stopped agent with an alive tmux pane — creating an infinite stop/revive loop every ~60 seconds; fix: removed all three auto-kill paths entirely.
5. `_patch_interactive_answer` iterated messages DESC and early-returned on the first match (a dismiss answer), leaving a duplicate message with `answer=null` that permanently blocked the chat input; fix: changed to `continue` past dismiss answers and patch all copies.
6. `parser_interactive_by_id` was reset to `{}` at 3 sites (sync loop start, compact handler, turn-count-decrease fallback) after every full parse, so incremental parses couldn't link `tool_result` entries back to interactive cards — plan approvals submitted in tmux were silently lost; fix: seed from parsed turns' metadata at all 3 reset points.
7. `PreCompact` hook was setting `compact_notified = True` before JSONL rewrite completed, causing `sync_handle_compact` to re-parse the old file; fix: `PreCompact` now only sets `compact_in_progress` (pauses sync), `SessionStart(source="compact")` sets `compact_notified` after rewrite is done.
8. Content-based dedup in `_parse_session_turns` (line 1564) drops legitimate repeated user prompts with different UUIDs but identical text — repeated "continue" commands, identical follow-ups silently collapse into one turn.
9. Hook-created interactive cards use `tool_use_id` as `jsonl_uuid` while sync import uses JSONL entry UUID — different namespaces cause duplicate messages for the same interactive card, and empty `content=""` makes prefix dedup checks degenerate.
10. CLI-triggered `UserPromptSubmit` hook can falsely mark an unrelated pending web message as delivered — the FIFO query (`source="web"`, `delivered_at IS NULL`, `order_by(created_at.asc())`) has no way to verify which prompt actually triggered the hook.
11. `delivered_at` is used as both transport acknowledgement and conversation sort key (`COALESCE(delivered_at, far_future)`) — makes message ordering unstable and can temporarily place an assistant reply above the user message that caused it.
12. Assistant turn import in `sync_import_new_turns` has no UUID/content dedup — only user turns are deduplicated, so hook-created and sync-imported assistant messages can create duplicates.
13. Frontend placeholder priority `tmuxMode > disabled` masks the real input-blocking reason — user sees "Send via tmux..." instead of "Answer the question above first" when an unanswered interactive card exists.
14. Signal-based interactive blocking proposed as replacement for message-scanning approach: `PreToolUse` hook → emit `interactive_block`, answer endpoint or Stop hook → emit `interactive_unblock`, with API fallback for page refresh.
15. There are no Claude Code hooks for thinking/extended thinking — only tool execution (`PreToolUse`/`PostToolUse`), session lifecycle, and permissions; any "thinking" indicator must be synthetic.
16. Claude Code model IDs (`claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`) in `constants.js` are already current; the 1M context window is inherent to the Opus 4.6 model with no CLI flag needed; the L/M/H toggle controls `--effort` (reasoning effort), not context size.
17. Orphan tmux sessions (e.g., `cc-test` alongside `ah-c3cb9ec3`) result from auto-kill + manual resume — the orchestrator rotates to a new `ah-*` session but the original shell persists; the UI correctly shows only managed agents.
18. `main.py` at 7600 lines and `agent_dispatcher.py` at 6400 lines are the biggest maintainability risks — splitting into route modules is the highest-ROI refactor.
19. The project's architecture maturity comes from a convergence pattern visible in git history: complex solution → real bugs expose fragility → simplify to minimal reliable approach (session detection: 5 paths → 2, sync: incremental → full+hook, delivery: matching → direct marking).
20. Inbox UI redesigned: "Tasks" renamed to "Inbox", filter tabs (Planning/Executing/Review/Done) removed to show only inbox perspective, nav reordered to Inbox→Projects→New→Agents→Git, collapsed card tags removed.
21. Send button and AI batch button on inbox cards were using different flows from standard task dispatch (`_build_task_prompt` + `_build_agent_prompt`); unified to use the `/api/v2/tasks/{task_id}/dispatch` endpoint.
22. Drag-to-reorder on inbox cards was too easily triggered during scrolling — increased hold threshold to 350ms and disabled horizontal movement animation during vertical reordering.
23. `_update_stale_interactive_metadata` only backfills answers found in JSONL — if a conversation moves past an interactive card without a parseable `tool_result`, the card stays `answer=null` forever with no correction mechanism.
24. Stress tests confirmed: 10 rapid-fire messages and messages up to 5000 chars are accepted, but agents under load only process 1-2 of 5 queued messages before session stops — the queue dispatch pipeline doesn't reliably drain under concurrent load.
25. Direct tmux send is non-atomic: text is sent to tmux first, then the `Message` row is created — a server crash between those steps means Claude received the prompt but the DB never records it.

### 2026-03-17 | Task: Switch license from MIT to Apache 2.0 | Status: success
- What: Created root LICENSE file with Apache 2.0 text, updated README.md reference from MIT to Apache 2.0
- Lesson: Straightforward — no issues

### 2026-03-17 | Task: Rewrite README as user-facing documentation | Status: success
- What: Rewrote README from developer-internal docs to user-facing project documentation. Removed ~600 lines of internal architecture (models, API endpoints, execution details — already in CLAUDE.md). Added compelling feature highlights (zero migration, voice capture, monitoring, concurrency, sessions, project memory), Tailscale section, collapsible cert install instructions. Kept installation, config, troubleshooting.
- Lesson: Straightforward — the key was separating "what users need to know" from "what developers need to know" (the latter already lives in CLAUDE.md)

### 2026-03-17 | Task: NewTaskPage launch agent button | Status: success
- What: Added "Launch Agent" button (cyan send icon) to NewTaskPage bottom sheet that appears when a project is selected. Calls `launchTmuxAgent` directly, navigates to agent chat page. Grid columns adjust dynamically (6 → 7 cols when project selected).
- Lesson: Straightforward — reused existing `launchTmuxAgent` API and matched the button pattern from NewAgentForm

### 2026-03-17 | Task: Monitor page token usage auto-refresh (10 min) | Status: success
- What: Token usage was manual-refresh only. Added `fetchUsage` to mount effect and a 10-minute `setInterval` in the active polling `useEffect`. Backend already has 120s cache TTL so no rate-limit concerns.
- Lesson: Straightforward — `fetchUsage` was already defined but just wasn't wired into any polling interval

### 2026-03-17 | Task: Voice toggle OFF doesn't stop recording | Status: success
- What: Voice toggle (`autoVoice`) only gated auto-start on mount. Toggling OFF didn't stop active recording, and the mic button stayed visible/clickable. Fixed by: (1) adding `useEffect` to stop recording when `autoVoice` turns OFF, (2) hiding mic button + timer when `autoVoice` is OFF. Persistence was already implemented correctly via localStorage.
- Lesson: A toggle that controls "auto-start" behavior must also have side effects on the current state — otherwise the UI becomes contradictory (toggle OFF but recording active)

### 2026-03-17 | Task: Make voice recording duration configurable | Status: success
- What: `useVoiceRecorder` had a hardcoded `MAX_RECORDING_MS`. Refactored to accept `maxDurationMs` param (default `DEFAULT_MAX_RECORDING_MS = 300000`). Used a ref (`limitRef`) so in-flight timer closures always read the latest limit. Added effect to reset countdown display when limit changes while idle.
- Lesson: Any value captured inside `useCallback` closures with minimal deps arrays must use refs to avoid stale reads — especially timers set once at recording start.

### 2026-03-17 | Task: Unify split screen nav bar with main nav bar | Status: success
- What: SplitScreenPage had its own `paneTabs` with different tab order, labels ("Tasks" vs "Inbox"), icon sizes, and center button — visually and behaviorally inconsistent with the main App.jsx nav. Extracted `tabs`, `CenterFab`, and nav rendering into a shared `BottomNavBar` component (`frontend/src/components/BottomNavBar.jsx`). Both App.jsx and SplitScreenPage now reuse it.
- Lesson: Straightforward — no issues. Net reduction of ~56 lines. Key design: accept `badges`, `onDoubleTap`, `onProjectsTap`, and `className` as optional props so the same component works in both fixed-position (main app) and inline (split pane) contexts.

### 2026-03-20 | Task: Fix inbox task insights not rendering as bubbles | Status: success
- What: Inbox-dispatched tasks embedded RAG insights as raw text in the prompt content (`_build_task_prompt`) but never stored them in `meta_json["insights"]`. Direct-launch tasks used `_prepare_dispatch()` which stored insights in `meta_json`. Frontend's `InsightsBubble` reads `message.metadata.insights` — so inbox tasks showed raw text instead of collapsible bubbles. Fix: changed `_build_task_prompt` to return `(prompt, insights_list)` tuple, and `_create_task_agent` now stores insights in `meta_json`.
- Lesson: When two code paths produce the same data for different display, ensure both paths populate the same metadata field the frontend reads. The divergence was subtle — the insights were "there" in the prompt text, just not in the structured metadata the UI component expected.

### 2026-03-20 | Task: Unify task dispatch pipeline (tmux + subprocess) | Status: success
- What: Tasks had two separate dispatch pipelines — inbox dispatch created subprocess agents via the dispatcher loop, while NewTaskPage Launch created tmux agents by calling `launchTmuxAgent` directly (bypassing the dispatcher). Unified both through `dispatch_task_v2` endpoint: added `use_tmux` field to Task model/schemas, enhanced `dispatch_task_v2` to synchronously create either subprocess or tmux agents, replaced Voice toggle with Tmux toggle in NewTaskPage, changed Launch button to use `createTaskV2()` + `dispatchTask()`. Also added FAILED/TIMEOUT → EXECUTING transitions to the state machine so retries work with synchronous dispatch.
- Lesson: Extracted tmux launch logic into `_dispatch_task_tmux()` helper to avoid duplicating the ~100 lines from `launch_tmux_agent` endpoint. The dispatcher's `_dispatch_pending_tasks` loop skips `use_tmux=True` tasks since they need the synchronous endpoint for proper tmux session setup.

### 2026-03-20 | Task: Preserve model/effort/project settings across submissions | Status: success
- What: `clearAllDrafts()` in both NewTaskPage and ProjectDetailPage was clearing model, effort, and project selections along with title/description after each task submission. Fixed by only clearing content fields (title, description, prompt) — settings (model, effort, project) now persist via `useDraft` across form submissions. Also changed ProjectDetailPage's worktree/syncMode/skipPermissions from global `pref:project-agent:*` keys to per-project `pref:project-agent:${name}:*` keys so each project remembers its own settings independently.
- Lesson: Settings use two patterns — `useDraft` (with `draft:` prefix) already supported per-project via key template, but direct `localStorage` with `pref:` prefix was global. Both must include the project name for per-project persistence.

### 2026-03-21 | Task: Fix false session rotation from claude -p subprocesses (round 2) | Status: success
- What: Agent 063972e8's chat lost all messages after a `claude -p` agent-summary subprocess falsely rotated the agent's session. The previous fix (2026-03-20) only stripped `AHIVE_AGENT_ID` from `_generate_retry_summary_background`, but `_run_agent_summary_background` and two other `claude -p` call sites were missed. Also, `AHIVE_AGENT_ID` alone isn't enough — the unmanaged-session detection path matches by `TMUX_PANE`, which was also inherited. Created `_subprocess_clean_env()` helper that strips `AHIVE_AGENT_ID`, `TMUX`, and `TMUX_PANE` from all four `claude -p` subprocess calls.
- Lesson: Three independent paths can trigger false session rotation: (1) `X-Agent-Id` header from `AHIVE_AGENT_ID`, (2) `X-Tmux-Pane` header from `TMUX_PANE` matching agent pane, (3) CWD matching. Must strip ALL identifying env vars from `claude -p` subprocesses. Use a shared helper to avoid missing call sites.

### 2026-03-20 | Task: Fix agent conversation discontinuity from claude -p session rotation | Status: success
- What: Agent 709ed0f8's conversation became discontinuous because its `claude -p` subprocess (for retry summary generation) inherited `AHIVE_AGENT_ID` from the tmux env. The subprocess fired SessionStart hook with the agent's ID, causing the sync loop to rotate the agent to the one-shot `claude -p` session. Fixed by stripping `AHIVE_AGENT_ID` from the env passed to `subprocess.run` in `_generate_retry_summary_background`.
- Lesson: Any `claude -p` subprocess started from within an agent's tmux session inherits `AHIVE_AGENT_ID`, which makes its SessionStart hook look like a session rotation for the parent agent. Always strip `AHIVE_AGENT_ID` from env when spawning `claude -p` subprocesses.


## 2026-03-21 — 709ed0f80ee2
1. `claude -p` subprocess run inside an agent's tmux session inherits `AHIVE_AGENT_ID`, causing SessionStart hook to fire with that agent's ID — the sync loop then falsely adopts the one-shot session as a "session rotation," replacing the agent's real conversation with the subprocess output.
2. Fix in `_generate_retry_summary_background` (main.py): strip `AHIVE_AGENT_ID` from the env dict passed to `subprocess.run` so the `claude -p` subprocess doesn't trigger session rotation. Minimal one-line change, no-op when the function runs from the orchestrator backend (which lacks that env var).
3. `worker_manager.py` already has `_clean_env()` for subprocess calls but the orchestrator backend process normally doesn't have `AHIVE_AGENT_ID` set — the bug only manifests when an agent itself invokes `claude -p` from within its tmux session (e.g., via a Bash tool call that imports `main.py` inline).

## 2026-03-20 — 8adc1e40f3b8
1. Added "Insights" filter tab to Agents page — filters agents with `has_pending_suggestions`. Three touch points: FILTER_TABS array, statusFiltered memo, filterCounts memo. Straightforward — no issues.


## 2026-03-21 — 这里为什么insight不抓取bubble了？这是normal non-tmux agents的问题？详细调查一下
1. Non-tmux (subprocess) task agents were storing the full internal prompt (boilerplate, insights, guidelines) in `Message.content`, causing chat bubbles to display raw markdown instead of clean user-facing text. Tmux agents stored only `task.description`. Fix: unified both paths to store only clean display content in `Message.content`, with full prompt assembly deferred to dispatch time in `_prepare_dispatch()`.
2. Subprocess task agents had a double-wrapping bug: `_build_agent_prompt(_build_task_prompt())` was applied at message creation AND again at dispatch. Fix: `_create_task_agent()` now stores clean content; `_prepare_dispatch()` detects `agent.task_id`, calls `_build_task_prompt()` then wraps with `_build_agent_prompt()` once.


## 2026-03-21 — Voice toggle关闭后仍启动录音，设置项缺少持久化
1. Voice toggle in `NewTaskPage.jsx` only gated auto-start on mount — turning it OFF didn't stop an active recording or hide the mic button, creating a contradictory UI state (toggle OFF but recording running). Fix: added `useEffect` to stop recording when `autoVoice` turns OFF, and conditionally hide mic button/timer when Voice is disabled.


## 2026-03-21 — Improve retry prompt: mark original question, emphasize user feedback
1. `_build_task_prompt` was only injecting `retry_context` (one-line user feedback) for retries — the AI-generated `agent_summary` (what was tried, outcomes) was never included in the prompt, only shown in the frontend card. Fix: restructured retry prompt into clear sections: original task → what was tried (agent_summary) → user feedback (IMPORTANT label) → instructions.
2. `_generate_retry_summary_background` prompt was covering user feedback, causing duplication with the separately-injected `retry_context`. Fix: refocused the summary prompt on approaches and outcomes only.
3. Stop modal feedback was a plain textarea — no way to attach screenshots or use voice. Fix: added file upload (paste + file picker), attachment previews, and voice recording to the feedback input. Reused the existing `useVoiceRecorder` instance via `voiceTargetRef` to avoid duplicate mic access. Attachments appended as `[Attached file: ...]` to `incompleteReason`.

## 2026-03-21 — Show full descriptions when expanding task cards
1. InboxCard expanded description container used `flex-1 min-h-[60px]` inside a `flex-col` with auto height. `flex: 1 1 0%` set flex-basis to 0, so the element resolved to just `min-height: 60px`. Text content overflowed this 60px box and was clipped by CardShell's `overflow-hidden` — making expanded look identical to collapsed (~2-3 lines). Fix: removed `flex-1` so the container sizes to its content height naturally.
