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
