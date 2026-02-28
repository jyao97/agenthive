# AgentHive

A self-hosted, mobile-friendly web UI for orchestrating multiple [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents across your projects. Submit tasks from your phone, approve plans, monitor agent progress in real time, and manage everything from a single dashboard.

AgentHive is **not a replacement for the Claude Code CLI** — it's a companion. Keep using `claude` in your terminal the way you always have. AgentHive adds a layer on top: sync your CLI sessions to the web for mobile monitoring, run multiple agents in parallel, and manage everything from one place. You don't have to change your workflow — just extend it.

## Features

### Core
- **Multi-project management** — Register any number of Git repositories; agents work in isolated worktrees per project
- **Mobile-first UI** — Responsive React frontend with PWA support (Add to Home Screen on iOS/Android)
- **Plan mode with approval** — Agents generate plans before executing; approve or reject from the UI
- **Agent lifecycle control** — Start, stop, resume, and monitor agents from the dashboard
- **Model selection** — Choose between Claude models (Opus, Sonnet, Haiku) per agent

### CLI Session Sync
- **Sync CLI sessions to the web app** — Import conversations from `claude` CLI sessions running on the same machine and view them in the web UI
- **Read-only live tailing** — The sync is strictly read-only; it tails the CLI's session JSONL without writing to it or interfering with the CLI process
- **Auto-resume on restart** — Active CLI syncs automatically resume when the server restarts
- **Generating indicator** — Shows a typing indicator while the CLI agent is mid-response
- **Content reconciliation** — Detects and updates messages whose content grew since initial import (e.g. long tool-call chains)

### Real-time
- **Live streaming output** — WebSocket-based streaming of agent responses as they generate, with tool call summaries
- **Browser notifications** — Notification API triggered directly from WebSocket events when the tab is in the background; no expiring push subscriptions needed
- **Push notifications (fallback)** — Server-side Web Push via VAPID for when the browser is fully closed, with auto-resubscribe on every page load

### Agent Management
- **Project folders** — Group projects by category (e.g. "robotics", "infrastructure") for clean organization
- **Starred sessions** — Pin important conversations for quick access across projects
- **Unread counts** — See which agents have new output at a glance, like a messaging app
- **iOS-style multi-select** — Batch stop/delete agents with swipe-friendly selection
- **Status filtering** — Instantly see which agents are running, idle, syncing, or errored

### Developer Experience
- **Session persistence** — Resume previous agent conversations; pick up exactly where you left off
- **Rich markdown rendering** — Code blocks, tables, headers, bold/italic, inline images in chat
- **Git integration** — View commit history, diffs, and branch status per project
- **Voice input** — Dictate tasks using OpenAI Whisper speech-to-text

### Operations
- **Inactivity-based timeouts** — Agents time out based on idle time, not wall clock, so long-running tasks aren't killed prematurely
- **Crash recovery** — Partial output recovery from interrupted agent processes; stale session repair on restart
- **Per-project concurrency limits** — Control how many agents can run per project
- **Automatic backups** — Hourly SQLite database backups with configurable retention
- **Dark/light themes** — System-aware theme toggle
- **GPU monitoring** — Built-in nvidia-smi integration for GPU-equipped machines
- **Security hardening** — Login rate limiting with exponential backoff, inactivity-based auth lock

## Screenshots

_(coming soon)_

## Prerequisites

- **Linux** host (Ubuntu 22.04+ recommended)
- **Node.js** 18+ and npm (for Claude Code CLI and frontend)
- **Python** 3.11+
- **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
- **Claude subscription** — Claude Max or Pro (uses OAuth token, no separate API billing)
- **OpenAI API key** (optional, for voice input)

## Folder Layout

After installation, your home directory will look like this:

```
~/
├── agenthive-main/              <- This repo (orchestrator, frontend, configs)
│   ├── install.sh               <- One-command installer
│   ├── run.sh                   <- Launch script
│   ├── orchestrator/            <- FastAPI backend
│   ├── frontend/                <- React + Vite frontend
│   ├── scripts/                 <- Helper scripts (init, add-project, restore)
│   ├── certs/                   <- Self-signed SSL certificates
│   ├── project-configs/         <- Project registry (registry.yaml)
│   ├── data/                    <- SQLite database
│   └── .env                     <- Environment variables
│
└── agenthive-projects/          <- All managed project code
    ├── crowd-nav/
    ├── vla-delivery/
    └── ...
```

`agenthive-main` contains the orchestration system itself. `agenthive-projects` contains the actual project repositories that agents work on. They are kept separate so you can back up, move, or resize them independently.

## Quick Install

Run the automated installer:

```bash
git clone https://github.com/jyao97/AgentHive.git agenthive-main
cd agenthive-main
chmod +x install.sh
./install.sh
```

The installer handles everything: system packages, Node.js, Python venv, Claude CLI, SSL certs, `.env` setup, and service startup. It creates `~/agenthive-projects/` automatically.

After installation, open `https://<your-ip>:3000` in a browser.

## Manual Installation

If you prefer to set things up step by step:

### 1. Clone the repository

```bash
git clone https://github.com/jyao97/AgentHive.git agenthive-main
cd agenthive-main
```

### 2. Install system dependencies

```bash
# Node.js 20 (if not already installed)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Python 3.11+
sudo apt-get install -y python3 python3-pip python3-venv

# Claude Code CLI
npm install -g @anthropic-ai/claude-code
```

### 3. Generate a Claude OAuth token

```bash
claude setup-token
```

This generates a long-lived OAuth token (~1 year) tied to your Claude subscription. Copy the token — you'll need it for `.env`.

### 4. Create `.env`

```bash
cp .env.example .env
nano .env
```

Fill in the required values:

```bash
# Required
HOST_PROJECTS_DIR=/home/YOUR_USERNAME/agenthive-projects
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-YOUR_TOKEN_HERE
HOST_USER_UID=1000    # output of: id -u

# Optional (for voice input)
OPENAI_API_KEY=sk-xxx
```

Also set `HOST_CLAUDE_DIR` to your `~/.claude` directory (used for session symlinks):

```bash
HOST_CLAUDE_DIR=/home/YOUR_USERNAME/.claude
```

### 5. Create projects directory

```bash
mkdir -p ~/agenthive-projects
```

### 6. Generate self-signed SSL certificates

HTTPS is required for microphone access on mobile devices:

```bash
mkdir -p certs
LAN_IP=$(hostname -I | awk '{print $1}')
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/selfsigned.key -out certs/selfsigned.crt \
  -subj "/CN=agenthive" \
  -addext "subjectAltName=DNS:agenthive,DNS:localhost,IP:127.0.0.1,IP:${LAN_IP}"
```

Install the cert into the system trust store:

```bash
sudo cp certs/selfsigned.crt /usr/local/share/ca-certificates/agenthive.crt
sudo update-ca-certificates
```

To avoid browser warnings on other devices, see [Installing the CA Certificate](#installing-the-ca-certificate) below.

### 7. Set up and start services

```bash
# Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r orchestrator/requirements.txt
./run.sh

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

The frontend dev server proxies `/api` and `/ws` to the backend at `localhost:8080`.

### 8. Register a project

```bash
./scripts/add-project.sh my-project https://github.com/user/my-project.git
```

This clones the repo into `~/agenthive-projects/my-project/`, creates a `CLAUDE.md` template if missing, and registers it in `project-configs/registry.yaml`.

### 9. Access the UI

Open `https://<machine-ip>:3000` in your browser or phone.

On iPhone: Safari > Share > Add to Home Screen for a native app experience.

## Architecture

```
Browser / Phone (https://host:3000)
    |
    +-- Frontend (React SPA)
    |     port 3000 (HTTPS) -> reverse proxy /api, /ws
    |
    +-- Orchestrator (FastAPI)
          port 8080
          +-- Agent Dispatcher (async loop)
          +-- Process Manager (subprocess lifecycle)
          +-- Plan Manager (approve/reject)
          +-- Git Manager (log/diff/status)
          +-- WebSocket (real-time push)
          +-- Voice (Whisper STT)
          +-- Backup (hourly SQLite snapshots)
               |
               +-- Claude CLI Subprocesses (dynamic)
                     claude -p "..." --output-format stream-json
                     +-- Per-agent sessions with --resume
                     +-- Isolated git worktrees
                     +-- Configurable model per agent
```

## Configuration

All configuration is in `.env`. Key settings:

| Variable | Default | Description |
|---|---|---|
| `HOST_PROJECTS_DIR` | — | Absolute path to projects directory |
| `CLAUDE_CODE_OAUTH_TOKEN` | — | OAuth token from `claude setup-token` |
| `HOST_USER_UID` | `1000` | Host user UID (for file permission matching) |
| `HOST_CLAUDE_DIR` | — | Path to `~/.claude` on host |
| `MAX_CONCURRENT_WORKERS` | `5` | Max simultaneous agent processes |
| `MAX_IDLE_AGENTS` | `20` | Max idle agents kept alive |
| `TASK_TIMEOUT_SECONDS` | `600` | Default task timeout (10 min) |
| `CC_MODEL` | `claude-opus-4-6` | Default Claude model |
| `OPENAI_API_KEY` | — | OpenAI key for voice input (optional) |
| `PORT` | `8080` | Backend API port |
| `FRONTEND_PORT` | `3000` | Frontend HTTPS port |

## Common Commands

```bash
# Start services
./run.sh                                      # Start backend
cd frontend && npm run dev                    # Start frontend (separate terminal)

# Project management
./scripts/add-project.sh <name> <git-url>     # Register a project

# Backup
ls backups/                                   # List backups
./scripts/restore-backup.sh <backup-file>     # Restore from backup

# Logs
tail -f logs/orchestrator.log                 # View backend logs
```

## Troubleshooting

**Can't access from phone?**
Make sure port 3000 is open. On Ubuntu: `sudo ufw allow 3000`. The phone must accept the self-signed certificate (tap "Advanced" > "Proceed" on the browser warning).

**Agent fails to start?**
Check `logs/orchestrator.log`. Usually an expired OAuth token — run `claude setup-token` again and update `.env`.

**Voice input not working?**
Microphone requires HTTPS. Make sure you're accessing via `https://` and have valid SSL certs in `certs/`.

**Rate limited?**
Claude Max has usage limits. Reduce `MAX_CONCURRENT_WORKERS` or switch `CC_MODEL` to a smaller model.

## Installing the CA Certificate

AgentHive uses a self-signed SSL certificate. The installer adds it to the server's system trust store automatically, but **other devices** (your phone, laptop, etc.) will show a browser security warning unless you manually install the certificate.

### Download the certificate

From another machine on the same network:

```bash
scp user@server-ip:~/agenthive-main/certs/selfsigned.crt ~/agenthive.crt
```

### iPhone / iPad

1. AirDrop or email `selfsigned.crt` to your device
2. Open the file — a "Profile Downloaded" prompt appears
3. Go to **Settings > General > VPN & Device Management** > tap the profile > **Install**
4. Go to **Settings > General > About > Certificate Trust Settings** > toggle **full trust** for "agenthive"

### Android

1. Transfer `selfsigned.crt` to the device
2. Go to **Settings > Security > Encryption & credentials > Install a certificate > CA certificate**
3. Select the file and confirm

### macOS

```bash
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain agenthive.crt
```

### Windows

```powershell
certutil -addstore "Root" agenthive.crt
```

### Linux (other machines)

```bash
sudo cp agenthive.crt /usr/local/share/ca-certificates/agenthive.crt
sudo update-ca-certificates
```

After installing the certificate, restart your browser. The security warning should disappear.

## Security

- OAuth tokens are stored in `.env`, never exposed to agents directly
- Per-project concurrency limits prevent resource starvation
- Agents run as host subprocesses with configurable timeouts
- Login rate limiting with exponential backoff
- Inactivity-based auth lock (configurable timeout)

## License

MIT

<!-- AUTO-GENERATED: Project details from CLAUDE.md scaffold -->

### Project Overview

A self-hosted web UI for orchestrating multiple Claude Code agents across projects.
Agents run as **host subprocesses**, managed by a FastAPI backend.
The frontend is a mobile-first PWA built with React + TailwindCSS.

Core capabilities:
1. Web UI for agent management (with voice input), mobile-friendly PWA
2. Persistent agent conversations with session continuity (`--resume`)
3. Unified scheduling across multiple local projects
4. Two execution modes: INTERVIEW (read-only chat), AUTO (immediate execution)
5. Real-time streaming output via WebSocket
6. Automatic session caching and recovery
7. Hourly automatic database backups
8. Notifications via Web Push and Telegram Bot
9. tmux-based agent launch and CLI session sync

---

### Architecture Overview

```
Host Machine
│
├── cc-orchestrator/              ← This project directory
│   ├── orchestrator/             ← FastAPI backend (Python)
│   ├── frontend/                 ← React + Vite frontend
│   ├── data/                     ← SQLite database
│   ├── logs/                     ← Orchestrator and worker logs
│   ├── backups/                  ← Automatic DB backups + session cache
│   ├── project-configs/          ← Project registry (registry.yaml)
│   ├── certs/                    ← Self-signed SSL certs (for LAN mic access)
│   └── run.sh                    ← Host-mode launch script
│
├── ~/.claude/                    ← Claude Code session data
│   ├── projects/                 ← Session JSONL files per project
│   └── settings.json             ← cleanupPeriodDays=36500 (auto-set)
│
└── Projects (configured via PROJECTS_DIR)
    ├── project-a/
    ├── project-b/
    └── ...
```

---

### Tech Stack

- **Backend**: Python 3.12, FastAPI, Uvicorn
- **Frontend**: React 19, TailwindCSS 4, Vite 7
- **Database**: SQLite (WAL mode, SQLAlchemy 2.0)
- **Agent Execution**: `claude` CLI spawned as host subprocesses (`subprocess.Popen`)
- **Real-time**: WebSocket (FastAPI native) + Web Push (pywebpush/VAPID) + Telegram Bot
- **Voice**: OpenAI Whisper API
- **Auth**: Password-based with custom JWT (HMAC-SHA256, stdlib only)
- **Testing**: Vitest (frontend)
- **Deployment**: `./run.sh` on host machine

---

### Directory Structure

```
cc-orchestrator/
├── CLAUDE.md                  # This file
├── PROGRESS.md                # Lessons learned
├── QUICKSTART.md              # Quick start guide
├── run.sh                     # Host-mode launch script
├── .env                       # Environment variables (API keys etc.)
├── .env.example               # Environment variable template
│
├── orchestrator/              # FastAPI backend
│   ├── main.py                # FastAPI entry point (~50+ API routes)
│   ├── agent_dispatcher.py    # Core: persistent agent scheduling loop
│   ├── dispatcher.py          # Legacy: ephemeral task dispatcher
│   ├── worker_manager.py      # Subprocess lifecycle (Popen, kill, logs)
│   ├── session_cache.py       # Incremental session JSONL backup/restore/repair
│   ├── models.py              # SQLAlchemy ORM models
│   ├── schemas.py             # Pydantic request/response schemas
│   ├── database.py            # SQLite session management + migrations
│   ├── config.py              # Environment variable configuration
│   ├── auth.py                # Password hashing, JWT tokens, rate limiting
│   ├── git_manager.py         # Git operations (log, branches, status, merge)
│   ├── voice.py               # Whisper speech-to-text
│   ├── backup.py              # Automatic hourly database backups
│   ├── websocket.py           # WebSocket connection manager + event emitters
│   ├── push.py                # Notifications (Web Push + Telegram Bot)
│   ├── log_config.py          # Structured logging setup
│   └── requirements.txt       # Python dependencies
│
├── frontend/                  # React PWA
│   ├── src/
│   │   ├── App.jsx            # Router + auth guard + WebSocket provider
│   │   ├── main.jsx           # Entry point
│   │   ├── index.css          # TailwindCSS v4 theme
│   │   ├── pages/
│   │   │   ├── LoginPage.jsx         # Password setup & login
│   │   │   ├── ProjectsPage.jsx      # Project list
│   │   │   ├── ProjectDetailPage.jsx # Project dashboard (agents, sessions)
│   │   │   ├── AgentsPage.jsx        # All agents (multi-select mode)
│   │   │   ├── AgentChatPage.jsx     # Chat interface with an agent
│   │   │   ├── NewPage.jsx           # Create agent or project
│   │   │   ├── TasksPage.jsx         # Legacy task list
│   │   │   ├── MonitorPage.jsx       # System stats (GPU, disk, memory)
│   │   │   ├── GitPage.jsx           # Git history across projects
│   │   │   └── TrashPage.jsx         # Archived projects
│   │   ├── components/
│   │   │   ├── BotIcon.jsx           # Animated bot avatar
│   │   │   ├── FilePreview.jsx       # Image/video/CSV preview
│   │   │   ├── FilterTabs.jsx        # Tabbed filter UI
│   │   │   ├── ModeBadge.jsx         # INTERVIEW/AUTO badge
│   │   │   ├── PageHeader.jsx        # Header with theme toggle
│   │   │   ├── ProjectSelector.jsx   # Project dropdown
│   │   │   ├── StatusBadge.jsx       # Agent/task status indicator
│   │   │   ├── TaskCard.jsx          # Task summary card
│   │   │   ├── TaskDetail.jsx        # Full task view
│   │   │   ├── VoiceRecorder.jsx     # Mic button + waveform
│   │   │   ├── WaveformVisualizer.jsx
│   │   │   └── WorktreePicker.jsx    # Git worktree selection
│   │   ├── hooks/
│   │   │   ├── useWebSocket.js       # Real-time event stream
│   │   │   ├── useTheme.js           # Dark/light toggle
│   │   │   ├── useIdleLock.js        # Inactivity-based lock
│   │   │   ├── useHealthStatus.js    # Health polling
│   │   │   ├── useProjects.js        # Project list fetching
│   │   │   └── useVoiceRecorder.js   # Mic recording + transcription
│   │   └── lib/
│   │       ├── api.js                # Centralized fetch wrapper (~40+ functions)
│   │       ├── constants.js          # Status/mode colors, model options
│   │       ├── formatters.jsx        # relativeTime(), renderMarkdown(), etc.
│   │       └── pushNotifications.js  # Web Push API integration
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
│
├── data/                      # SQLite database
│   └── orchestrator.db
├── logs/                      # Orchestrator + worker logs
├── backups/                   # Hourly DB backups + session cache
├── project-configs/           # Project registry
│   └── registry.yaml
└── certs/                     # Self-signed SSL certs
```

---

### Agent Execution Model

Agents run as **host subprocesses** via `subprocess.Popen`.

#### Worker Manager (`worker_manager.py`)

```python
# Spawns claude CLI as a subprocess
process = subprocess.Popen(
    [CLAUDE_BIN, "-p", prompt,
     "--dangerously-skip-permissions",
     "--output-format", "stream-json",
     "--verbose"],
    cwd=project_path,
    stdout=output_file,
    stderr=subprocess.STDOUT,
    start_new_session=True,        # Own process group for clean kill
    env=clean_env,                 # Strips CLAUDECODE vars to avoid nesting
)
```

Key details:
- Output goes to `/tmp/claude-output-{id}.log` (read by dispatcher)
- `--resume {session_id}` used for conversation continuity
- `--worktree {name}` for isolated git worktrees
- `--model {model}` for per-agent model selection
- `--dangerously-skip-permissions` controlled per-agent via `skip_permissions` flag
- Process tracked by PID string in `_processes` dict
- Graceful SIGTERM → wait 10s → SIGKILL on stop/timeout
- Environment cleaned to prevent nested Claude Code detection (`AGENTHIVE_MANAGED=1` set)

#### Two Worker Types

1. **Ephemeral workers** (`start_worker`): One-shot task execution, legacy
2. **Agent workers** (`exec_claude_in_agent`): Persistent conversations with `--resume`

#### Agent Dispatcher Loop (`agent_dispatcher.py`)

Runs every 2 seconds:

```
1. Harvest completed execs     → parse stream-json, create response Message
2. Check exec timeouts         → SIGTERM/SIGKILL, mark TIMEOUT
3. Start new agents            → validate project dir, set IDLE
4. Dispatch pending messages   → match IDLE agents to PENDING messages, spawn exec
```

Respects `MAX_CONCURRENT_WORKERS` (global) and `project.max_concurrent` (per-project).

#### Session Continuity (`session_cache.py`)

- Incrementally caches session JSONL files (append-only, like git packfiles)
- Restores from cache when `--resume` fails (stale session)
- Repairs truncated JSONL from process kills
- Disables Claude Code auto-cleanup (`cleanupPeriodDays: 36500`)
- Evicts old cache when Claude assigns a new session_id (new file is a superset)

#### Worktree Session Directories (CRITICAL)

Claude Code stores session JSONL files in a directory derived from the **CWD** where it was launched:

```
~/.claude/projects/{encoded-project-path}/{session_id}.jsonl
```

When an agent uses `--worktree {name}`, Claude CLI runs in a subdirectory:
```
{project_path}/.claude/worktrees/{name}/
```

This means its sessions are stored in a **different** encoded directory:
```
~/.claude/projects/-home-user-projects-myproject--claude-worktrees-test/
```
NOT in:
```
~/.claude/projects/-home-user-projects-myproject/
```

**Key rule**: Any code that looks up a session JSONL must use `_resolve_session_jsonl(session_id, project_path, worktree)` which checks both directories. Using `session_source_dir(project_path)` alone will **miss all worktree sessions**.

Functions that must be worktree-aware (all fixed as of 2026-02-26):
- `_dispatch_pending_messages()` — resume pre-check
- `_sync_session_loop_inner()` — session parsing
- `import_session_history()` — history import
- `_reap_dead_agents()` — liveness freshness check
- `_detect_successor_session()` — must scan both session dirs
- `_spawn_successor_agent()` — new session parsing
- `_dedup_pane_agents()` — mtime comparison
- `_recover_agents()` — recovery JSONL path + liveness
- `_auto_detect_cli_sessions()` — must scan worktree session dirs too

#### tmux Session Naming

Each agent launched via tmux gets session name `ah-{agent_id[:8]}`. This enables **definitive** pane-to-agent matching:

```python
# Build once at startup/recovery
pane_map = _build_tmux_claude_map()
session_name_to_pane = {
    info["session_name"]: pane_id
    for pane_id, info in pane_map.items()
    if not info["is_orchestrator"]
}

# Match agent to its pane definitively
expected_name = f"ah-{agent.id[:8]}"
pane = session_name_to_pane.get(expected_name)
```

This is more reliable than PID/session-file matching, which can mis-assign panes during recovery.

#### CWD Matching for Worktree Agents

When matching a tmux pane's CWD to a project, use **subdirectory matching** (not exact match):

```python
def _cwd_matches(cwd: str, project_path: str) -> bool:
    return cwd == project_path or cwd.startswith(project_path + "/")
```

Worktree agents have CWDs like `{project}/.claude/worktrees/{name}`, which fail exact `==` checks.

#### Auto-detect Tiered Matching (`_auto_detect_cli_sessions`)

Detection of orphaned tmux Claude sessions uses tiered matching:
- **Tier 0**: tmux session name `ah-{id}` → direct agent lookup (most reliable)
- **Tier 1**: Session JSONL matching (finds session file in project or worktree dir)
- **Tier 2**: PID/CWD-based matching (least reliable)

#### Streaming Output

During execution, an asyncio task tails the output file every 0.5s, parses stream-json events, and broadcasts incremental content via WebSocket (`agent_stream` event) for live display in the chat UI.

---

### Database Models

#### Agent (persistent Claude sessions)

```python
class Agent:
    id: str               # 12-char hex
    project: str          # Project name (FK to projects)
    name: str             # Display name
    mode: AgentMode       # INTERVIEW | AUTO
    status: AgentStatus   # STARTING | IDLE | EXECUTING | SYNCING | ERROR | STOPPED
    branch: str | None
    worktree: str | None
    session_id: str | None   # Claude session ID for --resume
    cli_sync: bool           # Import history from CLI session and live-sync
    tmux_pane: str | None    # tmux pane ID for tmux-launched agents
    model: str | None        # Claude model override
    last_message_preview: str | None
    last_message_at: datetime | None
    unread_count: int
    created_at: datetime
    timeout_seconds: int     # Default 1800
    skip_permissions: bool   # --dangerously-skip-permissions (default True)
```

#### Message (agent conversation entries)

```python
class Message:
    id: str
    agent_id: str            # FK to agents
    role: MessageRole        # USER | AGENT | SYSTEM
    content: str
    status: MessageStatus    # PENDING | EXECUTING | COMPLETED | FAILED | TIMEOUT
    stream_log: str | None   # Raw stream-json output
    error_message: str | None
    source: str | None       # "web" | "cli" | None
    created_at: datetime
    completed_at: datetime | None
    scheduled_at: datetime | None  # For scheduled message delivery
```

#### Project

```python
class Project:
    name: str               # Primary key
    display_name: str
    path: str               # Absolute path on host
    git_remote: str | None
    description: str | None
    max_concurrent: int     # Default 2
    default_model: str      # Default "claude-opus-4-6"
    archived: bool
```

#### Supporting Models

- **Task**: Legacy ephemeral tasks (PID tracked via container_id field)
- **StarredSession**: Bookmarked Claude sessions
- **PushSubscription**: Web Push endpoints (endpoint, p256dh_key, auth_key)
- **SystemConfig**: Key-value store (jwt_secret, password_hash)

---

### API Endpoints

#### Authentication
```
POST   /api/auth/check              Check if auth is set up
POST   /api/auth/set-password       First-time password setup
POST   /api/auth/login              Login (returns JWT)
POST   /api/auth/change-password    Change password
```

#### Projects
```
GET    /api/projects                List projects with stats
POST   /api/projects                Create/register project
GET    /api/projects/folders        List non-archived projects
GET    /api/projects/trash          List archived projects
PUT    /api/projects/{name}/rename  Rename project
POST   /api/projects/{name}/archive Archive project
DELETE /api/projects/{name}         Delete project
DELETE /api/projects/trash/{name}   Permanently delete archived
POST   /api/projects/trash/{name}/restore  Restore archived
GET    /api/projects/{name}/agents  List agents in project
GET    /api/projects/{name}/sessions       List Claude sessions
GET    /api/projects/{name}/worktrees      List git worktrees
PUT    /api/projects/{name}/sessions/{sid}/star    Star session
DELETE /api/projects/{name}/sessions/{sid}/star    Unstar session
```

#### Agents
```
POST   /api/agents                  Create agent (starts persistent session)
POST   /api/agents/launch-tmux      Launch agent in tmux pane
POST   /api/agents/scan             Detect tmux sessions to link as agents
GET    /api/agents                  List all agents
GET    /api/agents/unread           Count unread across agents
GET    /api/agents/{id}             Get agent details
PUT    /api/agents/{id}             Update agent settings
DELETE /api/agents/{id}             Stop agent (soft delete)
POST   /api/agents/{id}/resume     Resume stopped agent
GET    /api/agents/{id}/messages   Get conversation (paginated)
POST   /api/agents/{id}/messages   Send message to agent (supports scheduling)
PUT    /api/agents/{id}/messages/{mid}  Edit/reschedule a pending message
DELETE /api/agents/{id}/messages/{mid}  Delete a pending message
PUT    /api/agents/{id}/read       Mark messages as read
```

#### Tasks (legacy)
```
GET    /api/tasks                   List tasks (derived from agent messages)
GET    /api/tasks/{id}              Task detail with conversation thread
```

#### Git
```
GET    /api/git/{project}/log       Commit history
GET    /api/git/{project}/branches  List branches
GET    /api/git/{project}/status    Working tree status
POST   /api/git/{project}/merge/{branch}  Merge branch
```

#### System
```
GET    /api/health                  Health check (DB, Claude CLI)
GET    /api/system/stats            System stats (disk, memory, GPU)
POST   /api/test/notify             Test push notifications
GET    /api/processes               Active Claude processes
GET    /api/workers                 Worker statuses (legacy)
GET    /api/logs                    Recent log entries
```

#### Files, Voice, Push
```
GET    /api/files/{project}/{path}  Serve project files (images, videos, CSVs)
POST   /api/voice                   Whisper speech-to-text
GET    /api/push/vapid-public-key   VAPID public key
POST   /api/push/subscribe          Subscribe to push
POST   /api/push/unsubscribe        Unsubscribe from push
```

#### WebSocket
```
ws://host:8080/ws                   Real-time events (auth via ?token=jwt)
```

Events: `agent_update`, `agent_stream`, `new_message`, `task_update`, `worker_update`, `system_alert`, `pong`

---

### Configuration

Environment variables (see `config.py`):

```python
# Worker
MAX_CONCURRENT_WORKERS = 5         # Global max parallel agent processes
TASK_TIMEOUT_SECONDS = 1800        # Default 30 min timeout per message
MAX_RETRIES = 3                    # Auto-retry failed tasks (legacy)
MAX_IDLE_AGENTS = 20               # Max idle agents kept alive
CC_MODEL = "claude-opus-4-6"       # Default Claude model
CLAUDE_BIN = "claude"              # Path to Claude CLI binary

# Projects
PROJECTS_DIR = ""                  # Host path to projects directory

# Auth
AUTH_TIMEOUT_MINUTES = 30          # Frontend inactivity lock timeout

# Voice
OPENAI_API_KEY = ""                # For Whisper transcription (optional)

# Backup
BACKUP_INTERVAL_HOURS = 1          # Database backup frequency
MAX_BACKUPS = 48                   # Max retained backups

# Session cache
SESSION_CACHE_INTERVAL = 30        # Seconds between session cache checks
CLAUDE_HOME = "~/.claude"          # Claude Code home directory

# Paths
DB_PATH = "./data/orchestrator.db"
LOG_DIR = "./logs"
BACKUP_DIR = "./backups"
PROJECT_CONFIGS_PATH = "./project-configs"

# Web Push (optional)
VAPID_PRIVATE_KEY = ""
VAPID_PUBLIC_KEY = ""
VAPID_SUBJECT = "mailto:agenthive@example.com"

# Telegram Bot (optional)
TELEGRAM_BOT_TOKEN = ""            # Bot token from @BotFather
TELEGRAM_CHAT_ID = ""              # Chat ID for notifications
```

---

### Authentication

1. First visit: `/login` prompts for password setup
2. Password stored as SHA-256 hash (salt:hash) in `SystemConfig` table
3. Login returns a custom JWT (HMAC-SHA256, 24h server-side expiry)
4. Token stored in `localStorage`, sent in `Authorization: Bearer` header
5. Frontend auto-locks after `AUTH_TIMEOUT_MINUTES` of inactivity (`useIdleLock` hook)
6. Rate limiting: exponential backoff after 5 failed attempts (in-memory, clears on restart)
7. WebSocket auth via `?token=<jwt>` query parameter

---

### Development Standards

#### Code Style
1. PEP 8 (backend Python)
2. ESLint (frontend JavaScript)
3. SQLAlchemy 2.0 mapped_column style for models
4. Pydantic v2 for API schemas
5. Use `logging` module with clear level differentiation

#### Worker Behavior
1. Agent commits use format: `[agent-{id}] short description`
2. INTERVIEW mode agents must not modify files
3. Session continuity via `--resume` when possible
4. Graceful degradation: restore from cache, re-queue on failure

#### Error Handling
1. Stale session recovery with retry limit (max 3 attempts)
2. Truncated JSONL repair on crash recovery
3. Partial output salvage from interrupted processes
4. WebSocket auto-reconnect on disconnect
