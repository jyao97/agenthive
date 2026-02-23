# PROGRESS.md — Lessons Learned

> Each CC worker should append here after completing a task. Never make the same mistake twice.

---

## General Lessons

### Docker
- (to be filled)

### CC Instance Scheduling
- (to be filled)

### Frontend
- (to be filled)

---

## Task Log

(CC workers append below after each task, using this format)

## [2026-02-23] Task 0.1: Docker Environment Validation + Init Script | Project: cc-orchestrator

### What was done
- Verified existing scripts/init.sh covers all Task 0.1 requirements
- Added Docker version 24.0+ enforcement check (was only printing version, not validating)
- Made all scripts executable (chmod +x)
- Committed project scaffolding: .gitignore, .env.example, CLAUDE.md, TASKS.md, PROGRESS.md, QUICKSTART.md, scripts/

### Problems encountered
- init.sh existed but didn't enforce Docker 24.0+ minimum version — just printed version number

### Solutions
- Added `docker_major` extraction and numeric comparison to fail if < 24

### Lessons learned
- Always check that "version display" also means "version enforcement" — printing isn't validating

---

## [2026-02-23] Task 0.2: Worker Docker Image | Project: cc-orchestrator

### What was done
- Created worker/Dockerfile (Ubuntu 24.04, git, python3, nodejs, claude CLI, non-root user)
- Created worker/entrypoint.sh (accepts prompt + project dir, runs claude with --dangerously-skip-permissions)
- Created worker/.dockerignore
- COPY entrypoint.sh into image with correct ownership

### Problems encountered
- None

### Lessons learned
- entrypoint.sh needs to be COPY'd in Dockerfile with --chown for non-root user to execute it

---

## [2026-02-23] Task 0.3: Orchestrator Docker Image | Project: cc-orchestrator

### What was done
- Created orchestrator/Dockerfile (python:3.11-slim, git, curl, pip deps)
- Created orchestrator/requirements.txt (fastapi, uvicorn, sqlalchemy, docker SDK, etc.)
- Created orchestrator/main.py (minimal FastAPI app with /api/health endpoint, CORS, lifespan hooks)
- Created orchestrator/.dockerignore

### Problems encountered
- None

### Lessons learned
- Keep main.py minimal for Phase 0 — just health endpoint. Phase 1 adds CRUD and dispatcher.

---

## [2026-02-23] Task 0.4: Docker Compose Orchestration | Project: cc-orchestrator

### What was done
- Created docker-compose.yml with orchestrator + frontend services
- Defined cc-internal (service comms) and cc-worker-net (worker containers) networks
- cc-worker-net uses `name:` key so dynamically created containers can reference it by name
- 5 named volumes: cc-orch-db, cc-orch-backups, cc-projects, cc-git-bare, cc-logs
- Frontend placeholder: nginx with reverse proxy for /api/* and /ws/* to orchestrator
- Static landing page with dark theme and backend connectivity check
- Added projects/registry.yaml and project CLAUDE.md template

### Problems encountered
- logs/.gitkeep rejected by git add because logs/ is in .gitignore — skipped it

### Lessons learned
- Don't try to track directories that are in .gitignore, even with .gitkeep
- Use `name:` on Docker networks that need to be referenced by containers created outside compose
- Docker Compose only creates networks used by at least one service — unused network definitions are silently skipped. Orchestrator must be on cc-worker-net to talk to workers.

---

## [2026-02-23] Task 1.1–1.4: Phase 1 Scheduler Core | Project: cc-orchestrator

### What was done
- **1.1 Database Schema**: models.py (Task, Project, SystemConfig tables), database.py (SQLite WAL mode, session factory), config.py (env vars)
- **1.2 FastAPI CRUD**: Full task lifecycle (create/list/get/cancel/retry), project listing, enhanced health check (DB + Docker), Pydantic schemas, registry.yaml loading on startup
- **1.3 Worker Manager**: Docker SDK integration for container lifecycle — start/stop/logs/status/cleanup, resource limits, network isolation, shell-safe prompt quoting
- **1.4 Task Dispatcher**: Async scheduling loop with harvest/timeout/retry/assign phases, startup crash recovery, concurrency limits (global + per-project)

### Problems encountered
- Worker entrypoint.sh received wrong args when worker_manager passed `command=["bash", "-c", ...]` — Docker concatenates ENTRYPOINT + CMD, so entrypoint.sh got `bash` as `$1` and `-c` as `$2`
- Test tasks kept retrying because .env has placeholder API keys

### Solutions
- Override entrypoint in worker_manager: `entrypoint=["bash", "-c"]` bypasses the Dockerfile's ENTRYPOINT and runs the command string directly
- Cancelled test tasks manually; auto-retry stops at MAX_RETRIES=3

### Lessons learned
- When Dockerfile has ENTRYPOINT and you pass a command via Docker SDK, the command becomes ARGS to the entrypoint — use `entrypoint=` override to bypass
- SQLAlchemy `expire_on_commit=False` is essential for reading task fields after commit in the same session
- SQLite WAL mode + `check_same_thread=False` needed for async dispatcher + sync API sharing the same DB
- `datetime.now(timezone.utc)` instead of `datetime.utcnow()` to avoid naive datetime comparison issues
