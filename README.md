# AgentHive

A self-hosted, mobile-friendly web UI for orchestrating multiple [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents across your projects. Submit tasks from your phone, approve plans, monitor agent progress in real time, and manage everything from a single dashboard.

AgentHive runs Claude Code CLI instances inside isolated Docker containers, so your host machine stays safe while agents work on your code autonomously.

## Features

- **Multi-project management** — Register any number of Git repositories; agents work in isolated containers per project
- **Mobile-first UI** — Responsive React frontend with PWA support (Add to Home Screen on iOS/Android)
- **Voice input** — Dictate tasks using OpenAI Whisper speech-to-text
- **Plan mode with approval** — Agents generate plans before executing; approve or reject from the UI
- **Real-time streaming** — WebSocket-based live output from running agents
- **Session persistence** — Resume previous agent conversations; star important sessions for quick access
- **Agent lifecycle control** — Start, stop, resume, and monitor agents from the dashboard
- **Git integration** — View commit history, diffs, and branch status per project
- **Resource limits** — CPU/RAM caps per agent container; per-project concurrency limits
- **Automatic backups** — Hourly SQLite database backups with configurable retention
- **Dark/light themes** — System-aware theme toggle
- **GPU monitoring** — Built-in nvidia-smi integration for GPU-equipped machines

## Screenshots

_(coming soon)_

## Prerequisites

- **Linux** host (Ubuntu 22.04+ recommended)
- **Docker** 24.0+ and Docker Compose v2
- **Node.js** 18+ and npm (for Claude Code CLI and frontend dev mode)
- **Python** 3.11+ (for host-mode backend or development)
- **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
- **Claude subscription** — Claude Max or Pro (uses OAuth token, no separate API billing)
- **OpenAI API key** (optional, for voice input)

## Quick Install

Run the automated installer (requires sudo for Docker and system dependencies):

```bash
git clone https://github.com/jyao97/AgentHive.git
cd AgentHive
chmod +x install.sh
./install.sh
```

The installer handles everything: system packages, Docker, Node.js, Python venv, Claude CLI, SSL certs, `.env` setup, Docker image builds, and service startup.

After installation, open `https://<your-ip>:3000` in a browser.

## Manual Installation

If you prefer to set things up step by step:

### 1. Clone the repository

```bash
git clone https://github.com/jyao97/AgentHive.git
cd AgentHive
```

### 2. Install system dependencies

```bash
# Docker (if not already installed)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

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
HOST_PROJECTS_DIR=/home/YOUR_USERNAME/cc-projects
CLAUDE_CODE_OAUTH_TOKEN=
HOST_USER_UID=1000    # output of: id -u

# Optional (for voice input)
OPENAI_API_KEY=
```

Also set `HOST_CLAUDE_DIR` to your `~/.claude` directory (used for session symlinks):

```bash
HOST_CLAUDE_DIR=/home/YOUR_USERNAME/.claude
```

### 5. Create projects directory

```bash
mkdir -p ~/cc-projects
```

### 6. Generate self-signed SSL certificates

HTTPS is required for microphone access on mobile devices:

```bash
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/selfsigned.key -out certs/selfsigned.crt \
  -subj "/CN=agenthive"
```

### 7. Build and start services

```bash
# Build worker image
docker build -t cc-worker:latest ./worker/

# Start all services (orchestrator + frontend)
docker compose up -d --build

# Verify
docker compose ps
curl -k https://localhost:3000
```

### 8. Register a project

```bash
./scripts/add-project.sh my-project https://github.com/user/my-project.git
```

This clones the repo into `~/cc-projects/my-project/`, creates a `CLAUDE.md` template if missing, and adds it to `projects/registry.yaml`.

### 9. Access the UI

Open `https://<machine-ip>:3000` in your browser or phone.

On iPhone: Safari > Share > Add to Home Screen for a native app experience.

## Development Mode (Host-mode, no Docker)

For local development without Docker containers for the orchestrator:

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

## Architecture

```
Browser / Phone (https://host:3000)
    │
    ├── Frontend (nginx + React SPA)
    │     port 3000 (HTTPS) → reverse proxy /api, /ws
    │
    └── Orchestrator (FastAPI)
          port 8080
          ├── Task CRUD API
          ├── Agent Dispatcher (async loop)
          ├── Worker Manager (Docker SDK)
          ├── Plan Manager (approve/reject)
          ├── Git Manager (log/diff/status)
          ├── WebSocket (real-time push)
          ├── Voice (Whisper STT)
          └── Backup (hourly SQLite snapshots)
               │
               └── Worker Containers (dynamic)
                     cc-worker:latest (Ubuntu 24.04 + Claude CLI)
                     ├── Isolated per-task
                     ├── CPU/RAM limited
                     ├── No SSH, no sudo, non-root
                     └── Volume: ~/cc-projects (shared)
```

## Configuration

All configuration is in `.env`. Key settings:

| Variable | Default | Description |
|---|---|---|
| `HOST_PROJECTS_DIR` | — | Absolute path to projects directory on host |
| `CLAUDE_CODE_OAUTH_TOKEN` | — | OAuth token from `claude setup-token` |
| `HOST_USER_UID` | `1000` | Host user UID (for file permission matching) |
| `HOST_CLAUDE_DIR` | — | Path to `~/.claude` on host |
| `MAX_CONCURRENT_WORKERS` | `5` | Max simultaneous agent containers |
| `MAX_IDLE_AGENTS` | `20` | Max idle agent containers kept alive |
| `WORKER_CPU_LIMIT` | `2` | CPU cores per worker container |
| `WORKER_MEM_LIMIT` | `4g` | RAM per worker container |
| `TASK_TIMEOUT_SECONDS` | `600` | Default task timeout (10 min) |
| `CC_MODEL` | `claude-sonnet-4-5-20250514` | Default Claude model |
| `OPENAI_API_KEY` | — | OpenAI key for voice input (optional) |
| `PORT` | `8080` | Backend API port |
| `FRONTEND_PORT` | `3000` | Frontend HTTPS port |

## Common Commands

```bash
# Service management
docker compose ps              # Check service status
docker compose logs -f orchestrator  # View orchestrator logs
docker compose restart         # Restart all services
docker compose down            # Stop everything

# Worker management
docker ps --filter "name=cc-worker-"     # List worker containers
docker stop cc-worker-abc12345           # Stop a specific worker
docker container prune --filter "label=cc-worker"  # Clean up exited workers

# Project management
./scripts/add-project.sh <name> <git-url>  # Register a project
nano projects/registry.yaml                # Edit project config

# Backup
docker exec agenthive ls /app/backups/             # List backups
./scripts/restore-backup.sh orchestrator_backup.db  # Restore

# Disk usage
docker system df -v            # Check Docker disk usage
docker system prune -a         # Clean unused images/containers
```

## Troubleshooting

**Can't access from phone?**
Make sure port 3000 is open. On Ubuntu: `sudo ufw allow 3000`. The phone must accept the self-signed certificate (tap "Advanced" > "Proceed" on the browser warning).

**Worker container fails to start?**
Check `docker logs cc-worker-xxx`. Usually an expired OAuth token — run `claude setup-token` again and update `.env`.

**Voice input not working?**
Microphone requires HTTPS. Make sure you're accessing via `https://` and have valid SSL certs in `certs/`.

**Rate limited?**
Claude Max has usage limits. Reduce `MAX_CONCURRENT_WORKERS` or switch `CC_MODEL` to a smaller model.

**Disk full?**
Run `docker system prune -a` to clean unused images. Check backup volume size with `docker system df -v`.

## Security

- Worker containers run as non-root with no sudo, no SSH keys
- Workers have CPU/RAM limits to prevent resource starvation
- Host home directory is never mounted into workers
- Project code is shared via a dedicated volume, not direct host mount
- OAuth tokens are injected via environment variable, not mounted files
- The orchestrator accesses Docker via socket — keep the socket secured

## License

MIT
