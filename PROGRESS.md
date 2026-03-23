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
