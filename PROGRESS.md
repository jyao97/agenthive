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

### 2026-03-22 | Task: Incremental JSONL sync | Status: success
- What: Replaced full-file `_parse_session_turns(path)` in sync_import_new_turns with turn-boundary-aware incremental reading
- Attempts: Previous attempts failed — (1) acbe56a tried merge-based incremental parsing, collapsed distinct assistant turns → infinite compact loops. (2) b84a55d tried hook-first message creation, `created_at DESC` ordering targeted wrong message → content loss.
- Resolution: Three-layer approach — `_read_new_lines()` reads only new bytes via seek, `sync_parse_incremental()` re-parses only from last turn boundary (not the merge approach), `_parse_session_turns_from_lines()` refactored to accept lines directly. Compact/reset does full re-read to repopulate cache.
- Lesson: Don't try to merge partial assistant turn state incrementally — instead track the last user/system entry as a "stable boundary" and re-parse from there with the proven flush_assistant() logic. The I/O savings (seek-based read) are safe; the CPU savings (only parse tail) are safe because we re-parse from a known turn boundary, not from an arbitrary byte offset.
- Gotcha 1: `_read_new_lines` must use binary mode (`rb`) for byte-offset tracking. Text mode + manual byte counting drifts with multi-byte UTF-8 chars.
- Gotcha 2: The boundary scanner must match the parser's turn-boundary semantics EXACTLY. tool_result user entries (list content) are NOT turn boundaries — the parser skips them. If the scanner treats them as boundaries, it splits assistant turns → compact purge deletes the finer-grained messages → bubbles "disappear."
- Gotcha 3: Don't preset `stable_turn_count` at init while `stable_boundary` is 0 — the splice duplicates all turns.

### 2026-03-22 | Task: Modularize main.py into APIRouter modules | Status: success
- What: Split the 8197-line monolithic main.py into 11 router modules under orchestrator/routers/, plus route_helpers.py for shared code. main.py → 374 lines (app setup, middleware, lifespan, router includes).
- Attempts: Spawned 5 parallel agents for large routers (system, projects, tasks, hooks, agents), wrote 6 small routers directly. Needed post-fix pass for cross-router imports.
- Resolution: All 116 API routes preserved. Verified via import checks, route enumeration, and server smoke test.
- Lesson: When agents create router modules, they tend to import from the original `main` module for cross-cutting helpers — must fix these to proper cross-router imports (deferred) before the final main.py rewrite. Duplicate helper functions across routers are inevitable; move them to a shared helpers module (route_helpers.py) and alias them in each router to preserve original call-site names.

### 2026-03-23 | Task: Fix duplicate message bubbles (chat ebd418428b1e) | Status: success
- What: `_is_turn_boundary()` in sync_engine.py didn't match `_parse_session_turns_from_lines()` in agent_dispatcher.py. `queue-operation remove/dequeue` and filtered system subtypes (`turn_duration`, `stop_hook_summary`) were treated as boundaries by the incremental parser but ignored by the full parser, creating phantom duplicate assistant turns. Added UNIQUE partial index on `(agent_id, jsonl_uuid)` as defense-in-depth, with best-row dedup cleanup and per-row savepoint handling.
- Gotcha 1: When two functions answer "is this a turn boundary?" they MUST stay in exact sync. Any entry that one treats as a boundary but the other skips will cause phantom turns during incremental parsing.
- Gotcha 2: "Keep oldest duplicate" is wrong — the phantom (truncated) row is often older. Keep the best row (non-null metadata, longer content, later timestamps).
- Gotcha 3: Hooks overload `jsonl_uuid` with `hook-{tool_use_id}` — unique index must exclude `hook-%` to avoid constraining hook-created rows.
- Gotcha 4: Batch `db.commit()` with a unique index needs per-row `db.begin_nested()` (SAVEPOINT) — one duplicate conflict would otherwise roll back all valid turns.

### 2026-03-23 | Task: Fix remaining message sync issues (#2, #4, #5, #8) | Status: success
- What: Fixed 4 additional issues found during the duplicate-bubble investigation: (1) tool-only messages hiding InteractiveBubbles, (2) hook+JSONL creating duplicate interactive cards, (3) windowed answer repair with unescaped LIKE, (4) system messages never purged after compact.
- Resolution: Three parallel agent teams — Team A: canonicalize hook rows at JSONL import time (upgrade `hook-{tool_use_id}` rows in-place instead of inserting duplicates), Team B: fallback ChatBubble in frontend for tool-only messages with interactive metadata, Team C: replace limit(10/20) scans with targeted tool_use_id LIKE prefilter + Counter-based system message purge.
- Lesson 1: Hook-created rows (`hook-{tool_use_id}`) and JSONL-synced rows represent the same assistant turn — canonicalize at import time by upgrading the hook row, not at render time or in a post-hoc reconciliation pass.
- Lesson 2: `LIKE '%{user_input}%'` needs `%` and `_` escaped. Prefer it as a prefilter with JSON verification after, not as the sole match.
- Lesson 3: System messages lack UUIDs — content-based purge needs Counter (multiset) not set, because identical system messages can legitimately appear multiple times.
- Deferred: #1 lossy user-turn dedup (needs parser provenance), #3 ToolActivity identity (schema redesign), #6 non-transactional offset (reconcile covers), #7 same-size rewrite (unlikely).

### 2026-03-23 | Investigation: session ebd418428b1e "lost messages" in web app | Status: investigation-only
- What: Agent ebd418428b1e (gsv-tc-fusion, 570+ messages, 9 compacts, 30 subagents) appeared to have missing/reordered messages in the web chat. Root cause is NOT data loss — only 2 trivial JSONL turns (1 compact marker, 1 task-notification) are missing from the DB.
- Root cause 1 (sort order disruption): `sync_reconcile_initial` content-dedup path sets `delivered_at = _utcnow()` on matched messages (sync_engine.py:424). Since the API sorts by `COALESCE(delivered_at, far_future)`, this jumps messages to a much later sort position. The "Confirmed" message was created at 08:01 UTC but sorted at 08:44 UTC — a 42-minute displacement.
- Root cause 2 (spurious compact detection): ANY turn count decrease (even ±1 from parser dedup fluctuation) triggers the compact path — purge + reconcile. 7 false-compact events logged for this agent, purging 13 messages and reconciling 9. Each reconcile resets `delivered_at` on content-matched messages. **Critical: Claude Code JSONL is append-only — compact appends a `compact_boundary` system entry but never deletes/modifies existing entries.** The turn count decreases are entirely from the parser's content-based dedup producing different results on consecutive full parses, NOT from JSONL changes. The sync engine is purging messages based on a phantom signal.
- Root cause 3 (duplicate web messages): User resent identical messages (3× "Yeah, we still want…", 2× "So we need two more gen3c…"). No client-side dedup. Session continuation also re-imports user content as cli-source duplicates.
- Lesson 1: `delivered_at` serves double duty as transport ACK and sort key — reconcile resetting it breaks chronological order. Fix: use `created_at` or a separate `sort_at` column for ordering; reserve `delivered_at` for delivery tracking only.
- Lesson 2: Turn count decrease ≠ compact. Claude Code JSONL is **append-only** — the file never shrinks. Detect compacts by checking for new `compact_boundary` system entries (subtype="compact_boundary"), not by turn count delta. The parser's content-based dedup makes turn counts non-deterministic.
- Lesson 3: Client-side dedup needed for web message sends — prevent duplicate submissions within a short window.
- Lesson 4: Queue-operations in JSONL have NO UUIDs (verified: 324 entries, 0 with UUID). Parser comment at agent_dispatcher.py:1417 is correct. Turns created from queue-ops are inherently uuid-less, making them invisible to UUID-based dedup/purge — only content-based matching applies.

### 2026-03-23 | Fix: plan card option index mismatch after Claude Code v2.1.81 | Status: done
- What: Selecting "Yes, clear context & bypass" on the plan card did not clear context. Investigation revealed Claude Code v2.1.81 hides the "clear context" option by default (restorable via `showClearContextOnPlanAccept: true`). Our frontend and backend still had 4 options with "clear context" at index 0, causing all tmux key mappings to be off by one vs the actual 3-option TUI.
- Fix: Removed phantom "clear context & bypass" option from PLAN_OPTIONS (frontend), _PLAN_LABELS (backend), _PLAN_LABELS_LOWER (sync), and updated all index references (legacy fallback, planning agent handoff, keyword detection).
- Lesson: When Claude Code upgrades change TUI options, our hardcoded index mappings silently break. The tmux pane capture logs showed the real 3-option TUI but nobody noticed the mismatch. Future: parse pane content to detect available options dynamically instead of hardcoding indices.

### 2026-03-23 | Task: Sync architecture redesign (tool_use_id, session_seq, scan-as-audit) | Status: success
- What: Replaced the fragile multi-layer sync architecture (5 dedup layers, 4+2 sync flows, 45+ special-case branches) with explicit identity, explicit ordering, and audit-based drift detection.
- Resolution: 4 rounds, 7 agent teams, 9 commits:
  - Round 1: Added `tool_use_id` and `session_seq` columns to Message/ToolActivity + SyncDrift model + migration/backfill + integration test harness with 8 scenarios
  - Round 2: Replaced all LIKE queries on meta_json with tool_use_id column lookups; set session_seq on all message creation paths
  - Round 3: Switched API/frontend ordering from delivered_at heuristics to session_seq
  - Round 4: Converted scan from silent repair to audit — sync_reconcile_initial went from 280 lines to 20; removed content-sig fallback from import path; added sync_audit/sync_repair functions + admin endpoints
- Lesson 1: Add explicit identity columns (tool_use_id) early — eliminates LIKE scans, JSON parsing, and prefix-matching heuristics in one move.
- Lesson 2: Separate ordering from delivery tracking — session_seq (monotonic JSONL turn index) is deterministic; delivered_at serves double duty and gets corrupted by reconciliation.
- Lesson 3: Audit vs repair must be separate code paths. Silent repair (content-sig matching, stale metadata sweep) hides bugs and adds complexity. Explicit drift records make problems visible.
- Lesson 4: sync_reconcile_initial was the biggest complexity source — 280 lines of interleaved check+fix. Replacing it with import+audit cut complexity dramatically.

### 2026-03-23 | Task: Voice LLM post-processing (口误修正) | Status: success
- What: Added GPT-4o-mini post-processing to refine voice transcription — corrects speech errors, grammar, and punctuation before text is injected into input field. Inspired by Type4Me's approach.
- Attempts: Straightforward — no issues. Integrated refinement inside useVoiceRecorder hook so all 7+ call sites get it automatically without individual changes.
- Lesson: Hook-level integration > call-site-level for cross-cutting concerns — adding the refine step inside the hook avoided touching every caller.

### 2026-03-24 | Task: Keep display files for stopped agents | Status: success
- What: Stopped/errored agents showed empty chat because `stop_agent_cleanup` and `error_agent_cleanup` deleted the display JSONL file. Removed those deletions so chat history persists. Moved display file cleanup to `permanently_delete_agent` (which was also missing it).
- Resolution: Straightforward — removed 2 `delete_agent()` calls, added cleanup to permanent delete endpoint.
- Lesson: Display files are the frontend's sole source of truth for chat history — deleting them on stop is premature. Only delete on permanent removal.

### 2026-03-24 | Task: Stack insight/attachment bubbles below chat bubbles | Status: success
- What: User message wrapper had `flex items-center gap-2` causing attachments and insights to render horizontally beside the bubble. Changed to `flex flex-col items-end` with an inner `flex items-center gap-2` row for just the warning icon + bubble.
- Resolution: Straightforward — two edits in ChatBubble's return JSX in AgentChatPage.jsx.
- Lesson: When a flex container holds both primary content (bubble) and secondary content (attachments, insights), use flex-col for the outer wrapper and an inner flex-row only for elements that truly belong side-by-side.

### 2026-03-24 | Task: Fix duplicate initial prompt bubble in task agents | Status: success
- What: Task agents showed two user bubbles — the clean description (from `_create_task_agent`) and the full wrapped prompt (from JSONL sync). Root cause: `is_wrapped_prompt()` only detected `_build_agent_prompt` preamble (`"You are working in project:"`), but the JSONL parser already strips that. The remaining `_build_task_prompt` output (`# Task: ...`) was not recognized as wrapped, so `_promote_or_create_user_msg` fell to the raw-prompt path (exact content match, no FIFO fallback) and created a duplicate CLI message.
- Resolution: Extracted matching logic from `_promote_or_create_user_msg` into `ContentMatcher` class (`content_matcher.py`) with 6-strategy cascade: exact → task-stripped → normalized → task-normalized → contained → FIFO. Handles task prompt wrapping (`# Task:`, insights, guidelines), tmux tab→space normalization, and retry format (`## Original Task`). Also added `# Task:` detection to `is_wrapped_prompt()` for reconciliation filtering.
- Lesson: When content passes through multiple strip layers (parser strips `_build_agent_prompt`, then sync checks `is_wrapped_prompt`), the detection must recognize all intermediate states. A dedicated matcher class is cleaner than inline branching — it encapsulates all transformation awareness in one place.
