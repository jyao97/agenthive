# TASKS.md — Implementation Task Breakdown

Ordered by dependency. Each task can be assigned to a single CC instance for independent execution.

---

## Phase 0: Environment Setup

### Task 0.1: Environment Validation + Init Script
```
Priority: P0
Est. time: 5 min
Depends on: None

Create scripts/init.sh with the following functionality:
1. Check host environment:
   - Node.js 18+ installed
   - Python 3.11+ installed
   - Claude Code CLI installed
   - Disk space > 20GB remaining
2. Create .env file (copy from .env.example, prompt user to fill values)
3. Create projects directory
4. Print "Initialization complete, run ./run.sh to start"

Create .env.example:
  HOST_PROJECTS_DIR=/home/YOUR_USERNAME/agenthive-projects
  CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-YOUR_TOKEN_HERE
  OPENAI_API_KEY=sk-xxx
  MAX_CONCURRENT_WORKERS=5
  PORT=8080

Done when: ./scripts/init.sh runs successfully, all checks pass
```

### Task 0.5: Project Registration + Init Script
```
Priority: P0
Est. time: 5 min
Depends on: 0.1

Create scripts/add-project.sh:

Usage: ./scripts/add-project.sh <project-name> <git-remote-url>

Functionality:
1. Clone project into projects directory
2. Append project config to projects/registry.yaml
3. If project has no CLAUDE.md, copy one from projects/templates/project-claude.md
4. Print "Project {name} registered successfully"

Create projects/registry.yaml (initially empty):
  projects: []

Create projects/templates/project-claude.md:
  # CLAUDE.md — {PROJECT_NAME}

  ## Project Description
  (please fill in)

  ## Tech Stack
  (please fill in)

  ## Development Rules
  - Commit after each meaningful step
  - All existing tests must pass
  - When uncertain, choose the conservative approach
  - Write lessons learned to PROGRESS.md after completion
  - Output EXIT_SUCCESS on completion, EXIT_FAILURE: {reason} on failure

Create scripts/restore-backup.sh:
  Usage: ./scripts/restore-backup.sh <backup-file>
  Functionality: Restore SQLite database from backup

Done when: add-project.sh successfully registers a project, registry.yaml is correctly updated
```

---

## Phase 1: Scheduler Core (Day 1)

### Task 1.1: Database Schema
```
Priority: P0
Est. time: 5 min
Depends on: Phase 0

Create orchestrator/models.py:

Table: tasks
  - id: UUID (PK)
  - project: String (not null, matches name in registry.yaml)
  - prompt: Text (not null)
  - priority: Enum(P0, P1, P2) default P1
  - status: Enum(PENDING, PLANNING, PLAN_REVIEW, EXECUTING, COMPLETED, FAILED, TIMEOUT, CANCELLED)
  - plan: Text (nullable)
  - plan_approved: Boolean (default False)
  - container_id: String (nullable, process ID)
  - branch: String (nullable)
  - retries: Integer (default 0)
  - result_summary: Text (nullable)
  - stream_log: Text (nullable)
  - error_message: Text (nullable)
  - created_at: DateTime (auto)
  - started_at: DateTime (nullable)
  - completed_at: DateTime (nullable)
  - timeout_seconds: Integer (default 600)

Table: projects (caches registry.yaml data)
  - name: String (PK)
  - display_name: String
  - path: String
  - git_remote: String (nullable)
  - max_concurrent: Integer (default 2)
  - default_model: String

Table: system_config
  - key: String (PK)
  - value: Text

Done when: DB is auto-created with correct schema on startup
```

### Task 1.2: FastAPI Skeleton + CRUD
```
Priority: P0
Est. time: 10 min
Depends on: 1.1

Implement orchestrator/main.py:
- FastAPI app
- CORS allow all (dev phase)
- On startup: init DB + load registry.yaml

Basic CRUD:
- POST /api/tasks          Create task (project + prompt + priority)
- GET  /api/tasks          List (?project= &status= filters)
- GET  /api/tasks/{id}     Details
- DELETE /api/tasks/{id}   Cancel

- GET  /api/projects       Return project list from registry.yaml
- GET  /api/health         Health check (DB writable)

Done when: curl can CRUD tasks, data persists in SQLite
```

### Task 1.3: Worker Manager (Subprocess Integration)
```
Priority: P0
Est. time: 20 min
Depends on: 1.2

Implement orchestrator/worker_manager.py:

class WorkerManager:

  start_worker(self, task: Task, project: Project) -> str:
    """Start a worker subprocess, return process ID"""

    process = subprocess.Popen(
        ["claude", "-p", prompt,
         "--dangerously-skip-permissions",
         "--output-format", "stream-json",
         "--verbose"],
        cwd=project.path,
        stdout=output_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return str(process.pid)

  get_status(self, pid: str) -> str:
    """Get process status: running / exited / error"""

  stop_worker(self, pid: str):
    """Stop and clean up process (SIGTERM -> SIGKILL)"""

Done when: Can start a worker subprocess, execute a simple CC task, read output, clean up correctly
```

### Task 1.4: Task Dispatcher
```
Priority: P0
Est. time: 15 min
Depends on: 1.3

Implement orchestrator/dispatcher.py:

class TaskDispatcher:

  Core loop (runs every 2 seconds):

  1. Harvest completed workers (parse output, update task status)
  2. Timeout detection (kill long-running processes)
  3. Retry failed tasks (up to MAX_RETRIES)
  4. Assign new tasks (respect concurrency limits)
  5. Persist state to DB

Done when:
- Submitting a task automatically starts a worker process
- Task status correctly updates on completion
- Timeout kills the process
- Failed tasks auto-retry
- Concurrency limits are respected
```

---

## Phase 2: Plan Mode + Git (Day 2)

### Task 2.1: Plan Manager
```
Priority: P1
Est. time: 10 min
Depends on: 1.4

Implement orchestrator/plan_manager.py:

New task flow:
1. status = PLANNING
2. Start worker with planning prompt (read-only analysis, no code changes)
3. Parse worker output -> task.plan
4. status = PLAN_REVIEW
5. Notify frontend via WebSocket

API:
- PUT /api/tasks/{id}/approve -> status=PENDING (re-queue for execution)
- PUT /api/tasks/{id}/reject -> body contains revision notes, update prompt, re-plan

Done when: Complete plan -> review -> approve -> execute flow works
```

### Task 2.2: Git Manager
```
Priority: P1
Est. time: 10 min
Depends on: 1.4

Implement orchestrator/git_manager.py:

Read-only operations from orchestrator:
- get_recent_commits(project) -> git log
- get_branches(project)
- get_diff(branch)

Merge operations:
- merge_branch(project, branch) -> run git merge
- Conflicts are NOT auto-resolved — notify user

API:
- GET  /api/git/{project}/log
- GET  /api/git/{project}/branches
- POST /api/git/{project}/merge/{branch}

Done when: Can view commit history, can trigger merges
```

---

## Phase 3: Web Frontend (Day 2-3)

### Task 3.1: Frontend Scaffold + PWA
```
Priority: P0
Est. time: 10 min
Depends on: Phase 0

Initialize Vite + React + TailwindCSS:
  - PWA manifest.json
  - viewport meta for mobile
  - Dark theme
  - Bottom tab bar: Home | Tasks | Monitor | Git

iPhone SE (375px) as minimum supported width.
All tappable elements >= 44x44px.

Done when: App shell loads in browser
```

### Task 3.2: Task Input + Project Selection
```
Priority: P0
Est. time: 10 min
Depends on: 3.1, 1.2

Implement:
- ProjectSelector.jsx: Project dropdown (from GET /api/projects)
- TaskInput.jsx:
  - Multi-line textarea (auto-expand)
  - Priority selector P0/P1/P2 (default P1)
  - VoiceButton.jsx: MediaRecorder recording -> POST /api/voice -> fill textarea
  - Submit button -> POST /api/tasks

Done when: Can select project, input text/voice, submit task
```

### Task 3.3: Task List + Plan Approval
```
Priority: P0
Est. time: 15 min
Depends on: 3.1, 2.1

TaskList.jsx:
- Group by project + status
- Each task card: prompt excerpt | project badge | status | elapsed time
- Click to expand details

PlanReview.jsx:
- Pending approval list (real-time via WebSocket)
- Shows: project | prompt | plan content
- Two big buttons: Approve / Reject
- Reject shows input for revision notes

Done when: Can see all task statuses, can approve/reject plans
```

### Task 3.4: Worker Monitor + System Status
```
Priority: P1
Est. time: 10 min
Depends on: 3.1, 1.4

InstanceMonitor.jsx:
- One card per active agent process
- Shows: project | task | status | runtime
- Click to expand stream log (last 50 lines)
- Summary: active agents / total cap, tasks completed today

System health panel:
- Disk usage
- Memory usage
- Agent distribution by project

Done when: Can see agent status in real-time
```

### Task 3.5: Git History + Merge
```
Priority: P2
Est. time: 10 min
Depends on: 3.1, 2.2

GitLog.jsx:
- Project selection tabs
- Per project: recent 30 commits + branches pending merge
- Merge button + result toast

Done when: Can view commits, can merge branches
```

---

## Phase 4: Voice + WebSocket (Day 3)

### Task 4.1: Whisper Voice Recognition
```
Priority: P1
Est. time: 5 min
Depends on: 1.2

orchestrator/voice.py:

POST /api/voice:
- Accept audio file (multipart/form-data)
- Call OpenAI Whisper API (model=whisper-1, auto-detect language)
- Return {"text": "..."}
- Error handling: audio too short / too large / API error

Done when: Upload audio file, get correct transcription back
```

### Task 4.2: WebSocket Real-time Push
```
Priority: P1
Est. time: 10 min
Depends on: 1.4

orchestrator/websocket.py:

ws://host/ws/status pushes:
- task_update: task status change
- worker_update: agent created/destroyed
- plan_ready: new plan pending approval
- new_commit: new git commit
- system_alert: system error etc.

Frontend useWebSocket.js: auto-reconnect + event dispatch

Done when: Frontend receives status updates without manual refresh
```

---

## Phase 5: Hardening (Day 3-4)

### Task 5.1: Automatic Backup
```
Priority: P1
Est. time: 5 min
Depends on: 1.1

orchestrator/backup.py:
- asyncio scheduled task, hourly backup of:
  - SQLite DB
  - All projects' PROGRESS.md
  - registry.yaml
- Keep last MAX_BACKUPS copies

Done when: Backups run automatically, old ones are cleaned up
```

### Task 5.2: Error Recovery + Cleanup
```
Priority: P1
Est. time: 10 min
Depends on: 1.4

Enhance dispatcher:
1. Startup recovery: Check DB for EXECUTING tasks, verify their processes still exist. If not, mark FAILED + retry
2. Zombie detection: stream log silent for 60s -> kill process
3. Disk monitoring: >90% usage -> pause new tasks + alert
4. Orphan cleanup: Periodically scan for stale processes not in task table, clean them up

Done when: Various failure scenarios don't crash the system
```

### Task 5.3: Logging System
```
Priority: P2
Est. time: 5 min
Depends on: 1.4

- Orchestrator logs: file + console, daily rotation, 7-day retention
- Agent logs: each agent's stream log saved to logs directory
- API: GET /api/logs?level=ERROR&limit=100

Done when: Can diagnose issues from logs
```

---

## Phase 6: Enhancements (Day 4+)

### Task 6.1: Task Templates
```
Priority: P2
Per-project configurable common templates.
New task_templates DB table, frontend template dropdown.
```

### Task 6.2: Statistics Dashboard
```
Priority: P2
Tasks completed today/week, success rate, avg duration, project distribution.
Use recharts.
```

### Task 6.3: Push Notifications
```
Priority: P2
Notify on plan pending approval / task failure.
Web Push API + service worker.
```

---

## Execution Order

```
=== Setup ===
Phase 0: 0.1 -> 0.5

=== Core development ===
Day 1:   1.1 -> 1.2 -> 1.3 -> 1.4
Day 2:   2.1 + 2.2 (parallel) + 3.1
Day 2-3: 3.2 + 3.3 (parallel)
Day 3:   3.4 + 4.1 + 4.2 (parallel)
Day 3-4: 3.5 + 5.1 + 5.2 + 5.3 (parallel)
Day 4+:  Phase 6 as needed
```
