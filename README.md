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
