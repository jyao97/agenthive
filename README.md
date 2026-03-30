# AgentHive

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)

> [**Getting Started**](#getting-started) · [**The Loop**](#the-loop) · [**Features**](#features) · [**Contributing**](CONTRIBUTING.md)

**A web-based task management system for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — capture tasks, dispatch them to AI agents, and iterate until they're right.**

AgentHive is not a replacement for the Claude Code CLI. It's the layer that turns it from a synchronous terminal tool into an asynchronous, agentic workflow. You keep using `claude` the way you always have — AgentHive adds the ability to capture ideas from your phone or by voice, dispatch to parallel agents on isolated worktrees, monitor progress in real time, and iterate with auto-summarized context when agents miss the mark. Your existing CLAUDE.md files, project setup, and CLI sessions all carry over, and project knowledge grows with every session.

## The Loop

Traditional task management tracks what **you** need to do. AgentHive tracks what your **agents** are doing.

### 1. Capture

Get ideas out of your head and into the system — fast, from anywhere.

- **Inbox** — a persistent queue for tasks across all your projects. Tasks wait here until you're ready to dispatch them.
- **Voice input** — dictate tasks using speech-to-text. Great for quick ideas on your phone while walking the dog.
- **Lightning input** — rapid task creation with minimal friction. Title, project, go.
- **Draft persistence** — edits are cached locally as you type. Close the app, lose connection, or switch tasks — your unsaved work is still there when you come back.

### 2. Dispatch

Assign tasks to AI agents and let them work.

- **Task → Agent** — turn any task into an autonomous agent with one click. Pick a model (Opus/Sonnet/Haiku), set permissions, and let the agent do the work while you move on.
- **Parallel execution** — run 5, 10, or more agents in parallel across different projects. Each agent gets its own isolated git worktree so they never step on each other's code.
- **AI batch processing** — got a pile of tasks in your inbox? One click to let AI triage and dispatch them in bulk, instead of handling each one manually.
- **RAG-powered context** — when dispatching a task, AgentHive automatically retrieves relevant history from past agent sessions. Your new agent starts with the lessons learned, not from scratch.

### 3. Monitor

Watch everything happen in real time — from your desk or your phone.

- **Mobile-first web UI** — a full PWA you can add to your Home Screen. Works on any device, any screen size.
- **Split screen** — monitor 2, 3, or 4 agents side by side (2-column, 3-column, 2x2 grid on desktop; stacked on mobile). Each pane navigates independently.
- **Rich chat interface** — markdown rendering, inline image and media preview, interactive cards for tool approvals and plan review. Approve, deny, or respond to agents directly in the conversation.
- **Dual-directional CLI sync** — CLI sessions appear in the web app, web app sessions are resumable from the CLI. One conversation history, two interfaces.
- **Smart notifications** — Web Push and Telegram with dual-channel in-use detection: if you're viewing an agent in the browser (WebSocket presence) or attached to its tmux pane, notifications are suppressed. Permission requests always cut through.
- **System & usage monitoring** — disk, memory, GPU status, and token usage at a glance.

### 4. Review

Check results, give feedback, and keep the knowledge growing.

- **Mark done** — review agent output, approve the work, mark the task complete.
- **Try → Summarize → Retry** — agent didn't nail it? Stop the agent, add your feedback, and AgentHive auto-generates a summary of what was tried. Re-dispatch with full context — the next agent picks up where the last one left off. Iterate until it's right.
- **Git operations** — view diffs, commit history, and branch status per project. One-click cleanup and push when you're satisfied.
- **Growing intelligence** — each project carries its own CLAUDE.md context that agents read on every task. As you work, the context accumulates — coding conventions, past decisions, known gotchas. New agents inherit everything previous agents learned.

### 5. Maintain

Your conversations with agents are valuable. Don't lose them.

- **Automatic backups** — database, session history, and project configs are backed up on a configurable schedule. Crash recovery salvages partial output.
- **Session archive** — every agent conversation is persisted and searchable. Star important sessions for quick access. Browse history across projects.
- **Resume anytime** — pick up any agent conversation right where it left off, whether it finished yesterday or last month.
- **Full-text search** — find any task, message, or agent session across your entire history.
- **Progress tracking** — weekly completion stats show how much your agents are getting done. See the trend, not just the backlog.
- **Project memory** — accumulated insights live in per-project CLAUDE.md files that survive across agents, sessions, and time.

## Why AgentHive?

### Zero Migration Cost

Already using Claude Code? AgentHive plugs right in. It wraps the same `claude` CLI you already know — launched inside tmux sessions on your machine, managed through a web UI. Your existing CLAUDE.md files, project setup, and workflow all carry over. The only new dependencies are **tmux** and optionally **Tailscale** for remote access. No new APIs, no vendor lock-in, no relearning.

### Built for Reliability

AgentHive hooks into Claude Code's native event system — not polling, not heuristics. Notifications, message delivery, and session sync are all event-driven. Messages reach agents through stop-hook dispatch with guaranteed ordering. Session lifecycle is tracked via SessionStart/SessionEnd hooks. Each agent runs in its own tmux session with a dedicated git worktree, with configurable timeouts and automatic crash recovery.

## Features

| Category | What you get |
|---|---|
| **Task Management** | Inbox with drag-to-reorder. Voice input. Lightning capture. Draft persistence. Per-project organization. Retry with auto-summarization. |
| **Agent Control** | Start, stop, resume agents. Per-agent model selection (Opus/Sonnet/Haiku). Configurable timeouts and permission modes. AI batch dispatch. RAG-powered context from past sessions. |
| **Chat Interface** | Rich markdown rendering (code blocks, tables, images). Inline media preview. Plan mode with approve/reject. Interactive tool confirmation cards. |
| **Monitoring** | Split screen (up to 4 panes). Real-time WebSocket streaming. System monitor (disk, memory, GPU, tokens). Weekly progress stats. |
| **Mobile PWA** | Add to Home Screen on iOS/Android. Full functionality — voice input, push notifications, task management. |
| **CLI Session Sync** | Dual-directional: CLI sessions in the web app, web app sessions resumable from CLI. |
| **Push Notifications** | Web Push (VAPID) and Telegram. Per-agent mute, global toggles. Dual-channel in-use detection for smart suppression. |
| **Git Integration** | Commit history, diffs, branch status per project. Agents work in isolated worktrees. One-click cleanup and push. |
| **Session History** | Every conversation persisted and searchable. Star sessions. Resume any agent anytime. Full-text search. |
| **Security** | Password auth with exponential-backoff rate limiting. Inactivity lock. HTTPS encryption. |
| **Backups** | Automatic database backups. Session JSONL caching. Crash recovery with partial output salvage. |

## How It Works

```
Your Phone / Browser
    |
    +-- AgentHive Frontend (React PWA, HTTPS)
    |     +-- WebSocket  <-- real-time agent output, status, permissions
    |     +-- REST API   <-- agent control, tasks, file uploads
    |
    +-- AgentHive Backend (FastAPI + Uvicorn)
          |
          +-- Agent Dispatcher
          |     +-- tmux sessions (one per agent)
          |     |     +-- claude CLI (the same CLI you use in your terminal)
          |     +-- Isolated git worktrees per agent
          |     +-- Permission manager (tool approve/deny)
          |     +-- Timeout & crash recovery
          |
          +-- Sync Engine (tails CLI JSONL files, read-only)
          |     +-- Hook integration (SessionStart/End, tool events)
          |
          +-- Push Notifications (Web Push / VAPID + Telegram)
          |
          +-- SQLite Database + Automatic Backups
```

AgentHive runs on your machine and launches Claude Code CLI instances inside tmux sessions. It streams their output to the web UI via WebSocket and manages their lifecycle. You interact through the browser — from the same machine, from your laptop, or from your phone over Tailscale.

## Getting Started

### Prerequisites

- **Linux** or **macOS** host (Ubuntu 22.04+ / macOS 13+ recommended)
- **Node.js** 18+ and npm
- **Python** 3.11+
- **tmux** (usually pre-installed; `sudo apt install tmux` if not)
- **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
- **Claude subscription** — Claude Max or Pro (uses your existing subscription, no separate API billing)
- **OpenAI API key** _(optional, for voice input)_

### Quick Start

```bash
# 1. Clone
git clone https://github.com/jyao97/agenthive.git && cd agenthive

# 2. Run automated setup (installs deps, creates venv, generates SSL certs)
chmod +x setup.sh && ./setup.sh

# 3. Configure
nano .env   # Set HOST_PROJECTS_DIR (required), optionally OPENAI_API_KEY

# 4. Start
./run.sh start
```

Open `https://<machine-ip>:3000` in your browser. Set a password on first visit.

On iPhone: Safari > Share > **Add to Home Screen** for a native app experience.

<details>
<summary><strong>Manual setup (without setup.sh)</strong></summary>

```bash
# Install system deps
sudo apt-get install -y python3 python3-pip python3-venv tmux
npm install -g @anthropic-ai/claude-code

# Set up Python
python3 -m venv .venv
source .venv/bin/activate
pip install -r orchestrator/requirements.txt

# Install frontend deps
cd frontend && npm install && cd ..

# Create projects directory
mkdir -p ~/agenthive-projects

# Configure
cp .env.example .env
nano .env

# Generate SSL certs (needed for mobile mic access)
mkdir -p certs
LAN_IP=$(hostname -I | awk '{print $1}')
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/selfsigned.key -out certs/selfsigned.crt \
  -subj "/CN=agenthive" \
  -addext "subjectAltName=DNS:agenthive,DNS:localhost,IP:127.0.0.1,IP:${LAN_IP}"

# Start
./run.sh start
```

</details>

## Remote Access with Tailscale

The easiest way to access AgentHive from your phone outside your LAN:

1. Install [Tailscale](https://tailscale.com) on your server and phone
2. `tailscale up` on both devices
3. Access AgentHive at `https://<tailscale-ip>:3000`

No port forwarding, no public exposure. Tailscale creates a secure WireGuard tunnel between your devices.

## Folder Layout

```
~/
├── agenthive/                      <- This repo
│   ├── run.sh                      <- Launch script (systemd services)
│   ├── setup.sh                    <- First-time setup script
│   ├── orchestrator/               <- FastAPI backend
│   ├── frontend/                   <- React PWA (Vite + TailwindCSS)
│   ├── certs/                      <- SSL certificates
│   ├── project-configs/            <- Project registry
│   ├── data/                       <- SQLite database
│   ├── backups/                    <- Automatic database backups
│   ├── logs/                       <- Server and orchestrator logs
│   └── .env                        <- Configuration
│
└── agenthive-projects/             <- Your project repositories
    ├── my-web-app/
    ├── ml-pipeline/
    └── ...
```

## Installing the CA Certificate

AgentHive uses a self-signed SSL certificate. Your server trusts it after setup, but other devices will show a browser warning until you install the cert.

**Download the cert** from another machine:
```bash
scp user@server-ip:~/agenthive/certs/selfsigned.crt ~/agenthive.crt
```

<details>
<summary><strong>iPhone / iPad</strong></summary>

1. AirDrop or email `selfsigned.crt` to your device
2. Open the file — "Profile Downloaded" prompt appears
3. **Settings > General > VPN & Device Management** > tap the profile > **Install**
4. **Settings > General > About > Certificate Trust Settings** > toggle full trust for "agenthive"
</details>

<details>
<summary><strong>Android</strong></summary>

1. Transfer `selfsigned.crt` to the device
2. **Settings > Security > Encryption & credentials > Install a certificate > CA certificate**
3. Select the file and confirm
</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain agenthive.crt
```
</details>

<details>
<summary><strong>Windows</strong></summary>

```powershell
certutil -addstore "Root" agenthive.crt
```
</details>

<details>
<summary><strong>Linux (other machines)</strong></summary>

```bash
sudo cp agenthive.crt /usr/local/share/ca-certificates/agenthive.crt
sudo update-ca-certificates
```
</details>

After installing, restart your browser.

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Reporting bugs and suggesting features
- Setting up a development environment
- Running tests and submitting pull requests

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.
