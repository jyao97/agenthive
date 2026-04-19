# TODO

Priority-ordered backlog. Move items between sections as priorities shift.
PROGRESS.md tracks completed work; this file tracks what's next.

## High

_(empty)_

## Medium

_(empty)_

## Low

### Project state reconciliation + orphan cleanup
Unify the two divergent project-listing endpoints (`/api/projects` reads DB,
`/api/projects/folders` scans filesystem) into a single reconcile pipeline,
and add a manually-triggered orphan cleanup script.

**Status.** The immediate user-visible symptom — xylocopa repo missing
from the Projects grid — was hand-patched on 2026-04-18 by deleting the
stale `agenthive` DB row and creating
`~/xylocopa-projects/xylocopa → /home/jyao073/xylocopa` symlink. This
TODO is structural prevention, not a pending bug.

**Background.** The grid page (`/api/projects/folders`,
`orchestrator/routers/projects.py:815`) lists filesystem dirs in
`PROJECTS_DIR` joined with DB stats. The picker (`/api/projects`, line 723)
reads DB rows filtered by `archived=False`. Projects whose `Project.path`
falls outside `PROJECTS_DIR` (e.g. xylocopa self-hosting) appear in the
picker but not the grid. Manual fs deletes leave DB orphans; manual fs
adds leave unregistered dirs. `registry.yaml` is a third source of truth
seeded into DB on startup (`main.py:79`), and `_remove_from_registry()`
writes back — so all three must stay in sync.

**Proposed design.**
- Single reconcile pass on app startup + manual refresh button:
  scan `PROJECTS_DIR` and DB together, classify into Active /
  Inactive (archived) / Unregistered (fs-only) / External (DB row, path
  outside PROJECTS_DIR but exists) / Orphan (DB row, path missing).
- One-way update direction: FS → DB → `registry.yaml`.
- Picker shows only Active. Grid shows Active + Inactive (Inactive
  cannot receive new tasks). Orphans live in a separate monitor view
  with cleanup actions.

**Orphan cleanup script (`orchestrator/reconcile.py`, dry-run + apply):**
- Project layer: missing fs path, dead symlinks, unregistered dirs,
  registry.yaml ↔ DB drift.
- FK orphans: `agents.project` / `tasks.project_name` → missing project;
  `messages.agent_id` → missing agent; `agents.task_id` → missing task;
  `starred_sessions.project` → missing.
- Session layer: `agents.session_id` → missing JSONL;
  `~/.claude/projects/<path>/` for deleted projects;
  starred sessions for missing JSONL.
- Residue: stale `xy-*` tmux sessions, `.trash/` entries older than
  N days (report only, no auto-purge), session_cache stale entries.

**Triggers for promoting to Medium/High.**
- A second fs/DB-divergence bug surfaces.
- Project count grows past ~100 (perf concern).
- Self-hosting / external-path projects become a regular pattern.

**Quick wins available without the full refactor.**
- Add union in folders endpoint: include DB rows whose `path` is not in
  PROJECTS_DIR, so xylocopa-style external projects show in the grid
  automatically without requiring a manual symlink.
