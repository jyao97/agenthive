# TASKS.md — Implementation Task Breakdown

Ordered by dependency. Each task can be assigned to a single CC instance for independent execution.

---

## Phase 0: Docker Infrastructure (Do first — run manually on host)

### Task 0.1: Docker Environment Validation + Init Script
```
Priority: P0
Est. time: 5 min
Depends on: None

Create scripts/init.sh with the following functionality:
1. Check host environment:
   - docker --version (requires 24.0+)
   - docker compose version (requires v2)
   - Disk space > 20GB remaining
   - Current user is in docker group
2. Create .env file (copy from .env.example, prompt user to fill API keys)
3. Create required Docker volumes:
   - cc-orch-db
   - cc-orch-backups
   - cc-projects
   - cc-git-bare
   - cc-logs
4. Print "Initialization complete, run docker compose up -d to start"

Create .env.example:
  ANTHROPIC_API_KEY=sk-ant-xxx
  OPENAI_API_KEY=sk-xxx
  MAX_CONCURRENT_WORKERS=5
  WORKER_CPU_LIMIT=2
  WORKER_MEM_LIMIT=4g
  PORT=8080

Done when: ./scripts/init.sh runs successfully on lab computer, all checks pass
```

### Task 0.2: Worker Docker Image
```
Priority: P0
Est. time: 10 min
Depends on: 0.1

Create worker/Dockerfile:

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools
RUN apt-get update && apt-get install -y \
    git curl wget ca-certificates \
    python3 python3-pip python3-venv \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Git config (worker level)
RUN git config --global user.name "CC Worker" && \
    git config --global user.email "cc-worker@localhost" && \
    git config --global init.defaultBranch main

# No SSH client installed
# No keys placed
# No sudo

# Non-root user
RUN useradd -m -s /bin/bash ccworker
USER ccworker

WORKDIR /projects

Create worker/entrypoint.sh:
#!/bin/bash
set -e

# Args: $1 = prompt, $2 = project_dir
cd "$2"

# Ensure CLAUDE.md exists
if [ ! -f CLAUDE.md ]; then
  echo "WARNING: No CLAUDE.md found in project"
fi

# Execute CC
exec claude -p "$1" \
  --dangerously-skip-permissions \
  --output-format stream-json \
  --verbose

Create worker/.dockerignore

Test:
docker build -t cc-worker:latest ./worker/
docker run --rm cc-worker:latest claude --version

Done when: cc-worker image builds successfully, claude CLI is available inside
```

### Task 0.3: Orchestrator Docker Image
```
Priority: P0
Est. time: 10 min
Depends on: 0.1

Create orchestrator/Dockerfile:

FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

Create orchestrator/requirements.txt:
  fastapi==0.115.*
  uvicorn[standard]==0.34.*
  sqlalchemy==2.0.*
  pydantic==2.*
  docker==7.*           # Docker SDK for Python
  httpx==0.28.*
  aiofiles==24.*
  python-multipart==0.0.*
  websockets==14.*
  pyyaml==6.*
  openai==1.*           # Whisper API

Done when: Orchestrator image builds successfully, FastAPI starts
```

### Task 0.4: Docker Compose Orchestration
```
Priority: P0
Est. time: 10 min
Depends on: 0.2, 0.3

Create docker-compose.yml:

services:
  orchestrator:
    build: ./orchestrator
    container_name: cc-orchestrator
    ports:
      - "${PORT:-8080}:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock   # Control worker containers
      - cc-orch-db:/app/db
      - cc-orch-backups:/app/backups
      - cc-projects:/projects:ro                     # Read-only project access
      - cc-logs:/app/logs
      - ./projects:/app/project-configs:ro           # Project configs
    env_file: .env
    restart: unless-stopped
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 2G
    networks:
      - cc-internal
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  frontend:
    build: ./frontend
    container_name: cc-frontend
    ports:
      - "${FRONTEND_PORT:-3000}:80"
    depends_on:
      - orchestrator
    networks:
      - cc-internal
    restart: unless-stopped

networks:
  cc-internal:
    driver: bridge
  cc-worker-net:
    driver: bridge
    # Worker network — can add iptables restrictions later

volumes:
  cc-orch-db:
  cc-orch-backups:
  cc-projects:
  cc-git-bare:
  cc-logs:

Notes:
- Worker containers are NOT defined in compose — orchestrator creates them dynamically via Docker SDK
- Workers use cc-worker-net network
- Orchestrator connects to cc-internal (frontend communication) and can create containers on cc-worker-net

Done when: docker compose up -d starts everything, curl http://localhost:8080/api/health returns ok
```

### Task 0.5: Project Registration + Init Script
```
Priority: P0
Est. time: 5 min
Depends on: 0.4

Create scripts/add-project.sh:

Usage: ./scripts/add-project.sh <project-name> <git-remote-url>

Functionality:
1. Clone project into cc-projects volume
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

### Task 0.6: Network Security Hardening (Optional but Recommended)
```
Priority: P1
Est. time: 10 min
Depends on: 0.4

Create scripts/setup-network.sh:

Functionality:
1. Configure iptables rules for cc-worker-net so workers can only access:
   - api.anthropic.com (Claude API)
   - api.openai.com (if Whisper needed)
   - github.com (git operations)
   - Internal orchestrator (status reporting)
   - DNS (53/udp)
   Everything else is DROPPED

2. Verify rules work:
   - From worker container: curl api.anthropic.com → success
   - From worker container: curl example.com → fail

Note: iptables rules need to be reapplied after host reboot.
Optional: persist rules to /etc/iptables/rules.v4

Done when: Worker containers can only access whitelisted domains
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
  - container_id: String (nullable, Docker container ID)
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

Create init_db.py — auto-initialize on orchestrator container startup.

Done when: Container starts and db/orchestrator.db is auto-created with correct schema
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
- GET  /api/health         Health check (Docker daemon available + DB writable)

Done when: curl can CRUD tasks, data persists in SQLite
```

### Task 1.3: Worker Manager (Docker Integration)
```
Priority: P0
Est. time: 20 min
Depends on: 1.2

Implement orchestrator/worker_manager.py:

This is the core module that interacts with the Docker daemon.

class WorkerManager:

  __init__(self):
    self.docker_client = docker.from_env()
    # Verify cc-worker image exists
    self.docker_client.images.get("cc-worker:latest")

  start_worker(self, task: Task, project: Project) -> str:
    """Start a worker container, return container_id"""
    
    # Build prompt (wrap original prompt + worker rules)
    wrapped_prompt = f"""
    You are working in project {project.display_name}.
    Project path: /projects/{project.name}
    
    First read the project's CLAUDE.md to understand project conventions.
    
    Task: {task.prompt}
    
    When done:
    1. git add + commit, message format: [task-{task.id[:8]}] short description
    2. Append lessons learned to PROGRESS.md
    3. Output EXIT_SUCCESS
    
    If you fail, output EXIT_FAILURE: reason
    """
    
    container = self.docker_client.containers.run(
        image="cc-worker:latest",
        command=["bash", "-c", 
                 f'claude -p "{escaped_prompt}" '
                 f'--dangerously-skip-permissions '
                 f'--output-format stream-json --verbose'],
        volumes={
            'cc-projects': {'bind': '/projects', 'mode': 'rw'},
            'cc-git-bare': {'bind': '/git-bare', 'mode': 'rw'},
        },
        working_dir=f'/projects/{project.name}',
        environment={
            'ANTHROPIC_API_KEY': os.environ['ANTHROPIC_API_KEY'],
        },
        cpu_quota=int(WORKER_CPU_LIMIT * 100000),
        mem_limit=WORKER_MEM_LIMIT,
        network='cc-worker-net',
        auto_remove=False,  # Don't auto-remove yet — need to read logs
        detach=True,
        name=f'cc-worker-{task.id[:8]}',
    )
    return container.id

  stream_logs(self, container_id: str) -> AsyncGenerator:
    """Async stream container stdout"""
    container = self.docker_client.containers.get(container_id)
    for line in container.logs(stream=True, follow=True):
        yield line.decode('utf-8')

  get_status(self, container_id: str) -> str:
    """Get container status: running / exited / error"""
    try:
        container = self.docker_client.containers.get(container_id)
        return container.status
    except docker.errors.NotFound:
        return "removed"

  stop_worker(self, container_id: str):
    """Stop and clean up container"""
    try:
        container = self.docker_client.containers.get(container_id)
        container.stop(timeout=10)
        container.remove(force=True)
    except docker.errors.NotFound:
        pass

  cleanup_exited(self):
    """Clean up all exited worker containers"""
    containers = self.docker_client.containers.list(
        all=True, 
        filters={"name": "cc-worker-", "status": "exited"}
    )
    for c in containers:
        c.remove()

Done when: Can start a worker container, execute a simple CC task, read output, clean up correctly
```

### Task 1.4: Task Dispatcher
```
Priority: P0
Est. time: 15 min
Depends on: 1.3

Implement orchestrator/dispatcher.py:

class TaskDispatcher:
  
  Core Ralph Loop:
  
  async def run(self):
    while self.running:
      # 1. Harvest completed workers
      for task in self.executing_tasks():
        status = self.worker_mgr.get_status(task.container_id)
        if status == "exited":
          logs = self.worker_mgr.get_logs(task.container_id)
          if "EXIT_SUCCESS" in logs:
            task.status = COMPLETED
            task.result_summary = extract_summary(logs)
          elif "EXIT_FAILURE" in logs:
            task.status = FAILED
            task.error_message = extract_error(logs)
          task.completed_at = now()
          self.worker_mgr.stop_worker(task.container_id)  # cleanup
          await self.broadcast_update(task)

      # 2. Timeout detection
      for task in self.executing_tasks():
        if (now() - task.started_at).seconds > task.timeout_seconds:
          self.worker_mgr.stop_worker(task.container_id)
          task.status = TIMEOUT
          await self.broadcast_update(task)

      # 3. Retry failed tasks
      for task in self.failed_or_timeout_tasks():
        if task.retries < MAX_RETRIES:
          task.retries += 1
          task.status = PENDING
          task.prompt += f"\n\n[RETRY #{task.retries}] Previous failure: {task.error_message}. Avoid the same mistake."

      # 4. Assign new tasks
      for task in self.pending_by_priority():
        project = get_project(task.project)
        if self.project_worker_count(project) >= project.max_concurrent:
          continue
        if self.total_worker_count() >= MAX_CONCURRENT_WORKERS:
          break
        
        container_id = self.worker_mgr.start_worker(task, project)
        task.container_id = container_id
        task.status = EXECUTING
        task.started_at = now()
        await self.broadcast_update(task)

      # 5. Persist state to DB
      self.db.commit()

      await asyncio.sleep(2)

  Start dispatcher in FastAPI startup event:
    @app.on_event("startup")
    async def start_dispatcher():
      dispatcher = TaskDispatcher(...)
      asyncio.create_task(dispatcher.run())

Done when:
- Submitting a task automatically starts a worker container
- Task status correctly updates on completion
- Timeout kills the container
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
2. Start worker container with modified prompt:
   "You are a task planner. Analyze the task and output an execution plan.
    Do NOT make any code changes.
    Task: {prompt}
    Output: 1. Files to modify 2. Change summary 3. Complexity estimate 4. Risks 5. Test strategy"
3. Parse worker output → task.plan
4. status = PLAN_REVIEW
5. Notify frontend via WebSocket

API:
- PUT /api/tasks/{id}/approve → status=PENDING (re-queue for execution)
- PUT /api/tasks/{id}/reject → body contains revision notes, update prompt, re-plan

Config: When SKIP_PLAN_FOR_P2=True, P2 tasks skip plan review

Done when: Complete plan → review → approve → execute flow works
```

### Task 2.2: Git Manager
```
Priority: P1
Est. time: 10 min
Depends on: 1.4

Implement orchestrator/git_manager.py:

Note: Orchestrator container has **read-only** access to project code.
Git operations fall into two categories:

1. Executed inside worker containers (worker does these):
   - git add, commit (done by worker's CC instance)
   - Write PROGRESS.md

2. Viewed from orchestrator (read-only operations):
   - get_recent_commits(project) → git log (via docker exec in temp container)
   - get_branches(project)
   - get_diff(branch)

3. Merge operations (via temp container):
   - merge_branch(project, branch) → start temp container to run git merge
   - Conflicts are NOT auto-resolved — notify user

API:
- GET  /api/git/{project}/log
- GET  /api/git/{project}/branches
- POST /api/git/{project}/merge/{branch}

Done when: Can view commit history, can trigger merges
```

---

## Phase 3: Web Frontend (Day 2-3)

### Task 3.1: Frontend Scaffold + PWA + Nginx
```
Priority: P0
Est. time: 10 min
Depends on: Phase 0

Create frontend/Dockerfile:
  FROM node:20-slim AS build
  WORKDIR /app
  COPY package*.json .
  RUN npm ci
  COPY . .
  RUN npm run build

  FROM nginx:alpine
  COPY --from=build /app/dist /usr/share/nginx/html
  COPY nginx.conf /etc/nginx/conf.d/default.conf

Create frontend/nginx.conf:
  - Serve static files
  - Proxy /api/* to orchestrator:8080
  - WebSocket proxy /ws/* to orchestrator:8080

Initialize Vite + React + TailwindCSS:
  - PWA manifest.json
  - viewport meta for mobile
  - Dark theme
  - Bottom tab bar: Home | Tasks | Monitor | Git

iPhone SE (375px) as minimum supported width.
All tappable elements >= 44x44px.

Done when: docker compose up shows the app shell in browser
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
  - VoiceButton.jsx: MediaRecorder recording → POST /api/voice → fill textarea
  - Submit button → POST /api/tasks

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
- Two big buttons: ✅ Approve / ❌ Reject
- Reject shows input for revision notes

Done when: Can see all task statuses, can approve/reject plans
```

### Task 3.4: Worker Monitor + System Status
```
Priority: P1
Est. time: 10 min
Depends on: 3.1, 1.4

InstanceMonitor.jsx:
- One card per worker container
- Shows: container name | project | task | status | runtime
- Click to expand stream log (last 50 lines)
- Summary: active workers / total cap, tasks completed today

System health panel:
- Docker daemon status
- Disk usage
- Memory usage
- Worker distribution by project

Done when: Can see worker status in real-time
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
- worker_update: worker created/destroyed
- plan_ready: new plan pending approval
- new_commit: new git commit
- system_alert: disk full / Docker error etc.

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
- Store in cc-orch-backups volume

Done when: Backups run automatically, old ones are cleaned up
```

### Task 5.2: Error Recovery + Container Cleanup
```
Priority: P1
Est. time: 10 min
Depends on: 1.4

Enhance dispatcher:
1. Startup recovery: Check DB for EXECUTING tasks, verify their containers still exist. If not, mark FAILED + retry
2. Zombie detection: stream log silent for 60s → kill container
3. Disk monitoring: >90% usage → pause new tasks + alert
4. Orphan cleanup: Periodically scan for name=cc-worker-* containers not in task table, remove them
5. Docker daemon unavailable: graceful degradation, pause assignment, keep retrying connection

Done when: Various failure scenarios don't crash the system
```

### Task 5.3: Logging System
```
Priority: P2
Est. time: 5 min
Depends on: 1.4

- Orchestrator logs: file + console, daily rotation, 7-day retention
- Worker logs: each worker's stream log saved to cc-logs volume:
  logs/worker-{task_id}.json
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
=== Run manually on host machine ===
Phase 0: 0.1 → 0.2 + 0.3 (parallel) → 0.4 → 0.5 → 0.6

=== Can be executed by CC from here ===
Day 1:   1.1 → 1.2 → 1.3 → 1.4
Day 2:   2.1 + 2.2 (parallel) + 3.1
Day 2-3: 3.2 + 3.3 (parallel)
Day 3:   3.4 + 4.1 + 4.2 (parallel)
Day 3-4: 3.5 + 5.1 + 5.2 + 5.3 (parallel)
Day 4+:  Phase 6 as needed
```

**Critical**: Phase 0 must be completed on the lab computer first to ensure the Docker environment works. Subsequent phases can be progressively built by CC itself.
