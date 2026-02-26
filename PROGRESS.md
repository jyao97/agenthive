# PROGRESS.md — Lessons Learned

> Each CC worker should append here after completing a task. Never make the same mistake twice.

---

## General Lessons

### CC Instance Scheduling
- (to be filled)

### Frontend
- (to be filled)

---

## Task Log

(CC workers append below after each task, using this format)

## [2026-02-23] Task 1.1–1.4: Phase 1 Scheduler Core | Project: cc-orchestrator

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

---

## [2026-02-24] Session Persistence + Auth Simplification | Project: cc-orchestrator

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
