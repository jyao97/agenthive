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
