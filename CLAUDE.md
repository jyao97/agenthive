# CLAUDE.md — AgentHive (Multi-Instance Claude Code Orchestration System)

## Project Overview

A multi-instance Claude Code orchestration system running on a **shared Lab Computer**.
All CC workers run inside Docker containers to protect the host machine.

Core capabilities:
1. Web UI for task submission (with voice input), mobile-friendly
2. Unified scheduling across multiple local projects
3. Task dispatcher automatically assigns work to isolated CC worker containers
4. Plan mode with approval workflow
5. Automatic backups, zero risk to host machine

---

## ⚠️ Safety Principles (Lab Computer — READ THIS)

This is a shared lab machine. Safety is the #1 priority:

1. **CC workers ONLY run inside Docker containers** — never bare-metal on host
2. **`--dangerously-skip-permissions` only takes effect inside containers**
3. **Worker containers do NOT mount the host home directory** — only dedicated workspace volumes
4. **Worker containers have no SSH keys** — cannot access other lab resources
5. **Each worker has CPU/RAM limits** — won't starve shared resources
6. **Hourly automatic backups** — recoverable even if CC deletes files
7. **Orchestrator container only has network access** — no dangerous mounts

---

## Architecture Overview

```
Lab Computer (Host — DO NOT TOUCH)
│
├── agenthive/                 ← This project directory
│   ├── docker-compose.yml           ← One command to start everything
│   ├── orchestrator/                ← Scheduler container
│   ├── worker/                      ← CC worker container template
│   └── ...
│
├── Docker volumes (auto-managed):
│   ├── cc-orch-db          ← SQLite database
│   ├── cc-orch-backups     ← Automatic backups
│   ├── cc-projects         ← All project code
│   │   ├── crowd-nav/
│   │   ├── vla-delivery/
│   │   └── ...
│   └── cc-git-bare         ← Git bare repos (cross-instance sync)
│
└── Other lab resources (workers have ZERO access)
```

---

## Tech Stack

- **Containerization**: Docker + Docker Compose
- **Backend**: Python 3.11+ (FastAPI)
- **Frontend**: React + TailwindCSS (Vite)
- **Database**: SQLite (single user, sufficient)
- **Voice Recognition**: OpenAI Whisper API
- **CC Scheduling**: subprocess calling `claude` CLI (inside worker containers)
- **Version Control**: Git
- **Deployment**: `docker compose up -d` on lab computer

---

## Directory Structure

```
agenthive/
├── CLAUDE.md                  # This file (global instructions)
├── TASKS.md                   # Task breakdown
├── PROGRESS.md                # Lessons learned
├── QUICKSTART.md              # Quick start guide
│
├── docker-compose.yml         # Full service orchestration
├── .env                       # Environment variables (API keys etc.)
├── .env.example               # Environment variable template
│
├── orchestrator/              # Scheduler service
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                # FastAPI entry point
│   ├── dispatcher.py          # Core: CC worker scheduling
│   ├── task_queue.py          # Task queue
│   ├── worker_manager.py      # Docker worker lifecycle management
│   ├── plan_manager.py        # Plan mode approval
│   ├── project_manager.py     # Multi-project management
│   ├── voice.py               # Whisper speech-to-text
│   ├── git_manager.py         # Git operations
│   ├── models.py              # Database models
│   ├── config.py              # Configuration
│   ├── backup.py              # Automatic backups
│   └── websocket.py           # WebSocket real-time push
│
├── worker/                    # CC Worker container
│   ├── Dockerfile             # Includes claude CLI + git + node
│   ├── entrypoint.sh          # Container startup script
│   └── worker_claude.md       # CLAUDE.md injected into each worker
│
├── frontend/                  # Web UI
│   ├── Dockerfile
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── TaskInput.jsx
│   │   │   ├── TaskList.jsx
│   │   │   ├── PlanReview.jsx
│   │   │   ├── ProjectSelector.jsx   # Multi-project selection
│   │   │   ├── InstanceMonitor.jsx
│   │   │   ├── GitLog.jsx
│   │   │   └── VoiceButton.jsx
│   │   └── hooks/
│   │       └── useWebSocket.js
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
│
├── projects/                  # Project registration config
│   ├── registry.yaml          # Project manifest
│   └── templates/
│       └── project-claude.md  # CLAUDE.md template for new projects
│
├── scripts/
│   ├── init.sh                # First-time initialization
│   ├── add-project.sh         # Register a new project
│   └── restore-backup.sh      # Restore from backup
│
└── logs/                      # Logs (volume mount)
```

---

## Docker Architecture — Detailed Design

### docker-compose.yml Core Structure

```yaml
services:
  orchestrator:
    build: ./orchestrator
    ports:
      - "8080:8080"          # Web UI + API
    volumes:
      - cc-orch-db:/app/db
      - cc-orch-backups:/app/backups
      - /var/run/docker.sock:/var/run/docker.sock  # Control worker containers
      - cc-projects:/projects:ro    # Read-only access to project code
      - ./logs:/app/logs
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    restart: unless-stopped
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 2G

  # Worker containers are dynamically created/destroyed by orchestrator
  # via Docker API — not statically defined in compose

volumes:
  cc-orch-db:
  cc-orch-backups:
  cc-projects:
  cc-git-bare:
  cc-logs:
```

### Worker Containers (Dynamically Created)

Orchestrator creates worker containers via Docker SDK (`docker-py`):

```python
# worker_manager.py core logic
container = docker_client.containers.run(
    image="cc-worker:latest",
    command=f'claude -p "{prompt}" --dangerously-skip-permissions '
            f'--output-format stream-json --verbose',
    volumes={
        'cc-projects': {'bind': '/projects', 'mode': 'rw'},
        'cc-git-bare': {'bind': '/git-bare', 'mode': 'rw'},
    },
    working_dir=f'/projects/{project_name}',
    environment={
        'ANTHROPIC_API_KEY': api_key,
    },
    # Resource limits — protect shared lab resources
    cpu_quota=200000,      # 2 CPU cores max
    mem_limit='4g',        # 4GB RAM max
    # Network isolation
    network_mode='cc-network',  # Whitelist-only domain access
    # Security
    read_only=False,       # Workers need to write files
    # Auto cleanup
    auto_remove=True,      # Container removed after completion
    detach=True,
    name=f'cc-worker-{task_id[:8]}',
)
```

### Worker Dockerfile

```dockerfile
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools
RUN apt-get update && apt-get install -y \
    git curl nodejs npm python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# No SSH client installed
# No keys placed anywhere
# No sudo installed

# Run as non-root user
RUN useradd -m -s /bin/bash ccworker
USER ccworker

WORKDIR /projects
ENTRYPOINT ["/entrypoint.sh"]
```

### Network Isolation

```yaml
# Defined in docker-compose.yml
networks:
  cc-network:
    driver: bridge
    # Workers can ONLY access:
    # - api.anthropic.com (Claude API)
    # - api.openai.com (Whisper, if needed by worker)
    # - github.com (git push/pull)
    # - Internal orchestrator (status reporting)
    # - DNS (53/udp)
    # Everything else is DROPPED
```

Optional: enforce with iptables rules in `scripts/init.sh`.

---

## Multi-Project Management

### projects/registry.yaml

```yaml
projects:
  - name: crowd-nav
    display_name: "Safe Crowd Navigation"
    path: /projects/crowd-nav
    git_remote: git@github.com:username/crowd-nav.git
    default_model: claude-sonnet-4-5-20250514
    max_concurrent: 2          # Max 2 workers for this project

  - name: vla-delivery
    display_name: "VLA Delivery Robot"
    path: /projects/vla-delivery
    git_remote: git@github.com:username/vla-delivery.git
    default_model: claude-sonnet-4-5-20250514
    max_concurrent: 2

  - name: thermal-3dgs
    display_name: "Thermal 3D Gaussian Splatting"
    path: /projects/thermal-3dgs
    git_remote: git@github.com:username/thermal-3dgs.git
    default_model: claude-sonnet-4-5-20250514
    max_concurrent: 1
```

### Task Submission with Project Selection

Frontend `ProjectSelector.jsx` provides a project dropdown.
Task model includes a `project` field.
Dispatcher sets `working_dir` and injects the corresponding CLAUDE.md when starting a worker.

### Per-Project Resource Control

Each project can configure `max_concurrent` to prevent one project from consuming all worker slots.
Global concurrency cap is controlled by `MAX_CONCURRENT_WORKERS`.

---

## Core Module Design

### Task Model

```python
class Task:
    id: UUID
    project: str              # Project name (from registry.yaml)
    prompt: str
    priority: P0 | P1 | P2   # P0 = highest
    status: PENDING | PLANNING | PLAN_REVIEW | EXECUTING | COMPLETED | FAILED | TIMEOUT | CANCELLED
    plan: str | None
    plan_approved: bool
    container_id: str | None  # Docker container ID
    branch: str | None
    retries: int
    result_summary: str | None
    stream_log: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    timeout_seconds: int = 600
```

### Dispatcher Ralph Loop

```
while True:
    # 1. Harvest completed worker containers
    for container in running_containers:
        if container.status == 'exited':
            logs = container.logs()
            parse_result(logs)
            update_task_status()
            # container auto_remove=True, auto cleanup

    # 2. Timeout detection
    for task in executing_tasks:
        if elapsed > task.timeout_seconds:
            kill_container(task.container_id)
            mark_timeout(task)

    # 3. Assign new tasks (respect per-project concurrency limits)
    for task in pending_tasks_by_priority():
        project = get_project(task.project)
        if project_concurrent_count(project) < project.max_concurrent:
            if total_concurrent < MAX_CONCURRENT_WORKERS:
                start_worker_container(task, project)

    await asyncio.sleep(2)
```

### Worker Lifecycle

```
Orchestrator                          Docker
    │                                    │
    ├─ create container ────────────────►│ worker-{task_id}
    │   (image: cc-worker,               │
    │    volume: cc-projects,            │
    │    working_dir: /projects/{name})  │
    │                                    │
    ├─ stream logs ◄─────────────────────│ claude -p "..." --stream-json
    │   (parse JSON events)              │   ├─ read project CLAUDE.md
    │                                    │   ├─ execute task
    │                                    │   ├─ git commit
    │                                    │   └─ write PROGRESS.md
    │                                    │
    ├─ detect exit ◄─────────────────────│ EXIT_SUCCESS / EXIT_FAILURE
    │                                    │
    ├─ collect result                    │ (auto_remove, container destroyed)
    └─ update task status                │
```

---

## API Design

```
POST   /api/tasks                    Create task (project + prompt + priority)
GET    /api/tasks                    List tasks (?project=&status=)
GET    /api/tasks/{id}               Task details
PUT    /api/tasks/{id}/approve       Approve plan
PUT    /api/tasks/{id}/reject        Reject plan
DELETE /api/tasks/{id}               Cancel task
POST   /api/tasks/{id}/retry         Retry task

GET    /api/projects                 List projects
POST   /api/projects                 Register new project
GET    /api/projects/{name}/status   Project status (active worker count etc.)

GET    /api/workers                  All worker container statuses
GET    /api/workers/{id}/log         Worker real-time log

POST   /api/voice                   Speech to text

GET    /api/git/{project}/log        Project Git history
POST   /api/git/{project}/merge      Manual merge

GET    /api/system/health            System health (Docker, disk, memory)
GET    /api/system/config            Configuration
PUT    /api/system/config            Update configuration

ws://host/ws/status                  WebSocket real-time push
```

---

## Configuration

```python
# Worker config
MAX_CONCURRENT_WORKERS = 5         # Global max parallel workers
WORKER_CPU_LIMIT = 2               # Max 2 CPUs per worker
WORKER_MEM_LIMIT = "4g"            # Max 4GB RAM per worker
TASK_TIMEOUT_SECONDS = 600         # Default 10 min timeout
MAX_RETRIES = 3
CC_MODEL = "claude-sonnet-4-5-20250514"

# Plan mode
SKIP_PLAN_FOR_P2 = True
AUTO_APPROVE_TIMEOUT = 300         # Auto-approve after 5 min (can disable)

# Voice
OPENAI_API_KEY = "sk-..."

# Backup
BACKUP_INTERVAL_HOURS = 1
MAX_BACKUPS = 48

# Server
HOST = "0.0.0.0"
PORT = 8080
```

---

## Development Standards

### Worker Behavior Rules (injected into every worker's CLAUDE.md)

1. **Only modify files related to the current task**
2. **Commit after each meaningful step** — format: `[task-{id}] short description`
3. **When uncertain, choose the most conservative approach**
4. **Write lessons learned to PROGRESS.md after completion**
5. **Do not modify CLAUDE.md** (unless the task explicitly requires it)
6. **All existing tests must pass**
7. **Add spaces between CJK and Latin characters** (if applicable)
8. **Output `EXIT_SUCCESS` on completion, `EXIT_FAILURE: {reason}` on failure**

### Orchestrator Development Standards

1. PEP 8 (backend)
2. ESLint + Prettier (frontend)
3. Docstrings on all API endpoints
4. Robust error handling (container crash, Docker daemon disconnect, disk full)
5. Use `logging` module with clear level differentiation
6. SQLAlchemy for SQLite
7. Sensitive config in .env, never hardcoded
8. **All host machine operations must go through Docker API — no direct shell commands**
