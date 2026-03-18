# AgentHive

**A self-hosted command center for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — run multiple agents, monitor everything, and manage your projects from your phone.**

AgentHive is not a replacement for the Claude Code CLI. It's a companion. Keep using `claude` in your terminal the way you always have. AgentHive adds a layer on top: sync your CLI sessions to the web, run multiple agents in parallel, and manage everything from a single dashboard — including from your phone while you're away from your desk.

## Why AgentHive?

### Zero Migration Cost

Already using Claude Code? AgentHive plugs right in. It wraps the same `claude` CLI you already know — launched inside tmux sessions on your machine, managed through a web UI. Your existing workflow, CLAUDE.md files, and project setup all carry over. The only new dependencies are **tmux** (for session management) and optionally **Tailscale** (for secure remote access). No new APIs, no vendor lock-in, no relearning.

### Capture Ideas Anywhere

Walking your dog and had a breakthrough? Open AgentHive on your phone — dictate a task with voice input, type a quick note, or queue up a batch of ideas. Tasks land in your inbox and wait until you're ready to dispatch them. Stop losing ideas to "I'll remember it later."

### Global Monitoring

All your agents, all your projects, one screen. See which agents are running, which are waiting for input, and which just finished — in real time. WebSocket-powered live streaming shows agent output as it generates. Push notifications alert you when agents need attention, finish a task, or hit an error. A system monitor tracks disk, memory, and GPU usage. You always know what's happening.

### Multi-Agent Concurrency

Run 5, 10, or more Claude agents in parallel across different projects. Each agent gets its own isolated git worktree, so they never step on each other's code. The dispatcher manages concurrency limits per-project and globally, queues messages when agents are busy, and handles timeouts and crash recovery automatically. Think of it as tabs for your AI workforce — switch between conversations, monitor progress, and keep everything moving.

### Session Management

Every agent conversation is persisted and resumable. Star important sessions for quick access. Browse session history across projects. AgentHive automatically backs up the SQLite database hourly and caches session JSONL files incrementally. If an agent crashes mid-conversation, it recovers partial output and picks up where it left off. Your work is never lost.

### Project Memory

Each project carries its own CLAUDE.md context that agents read on every task. As you work, the context grows — coding conventions, past decisions, known gotchas. New agents inherit everything previous agents learned. AgentHive also syncs your existing CLI sessions, so conversations you had in the terminal are visible and searchable in the web UI.

## Features

| Category | What you get |
|---|---|
| **Agent Control** | Start, stop, resume agents. Choose model per agent (Opus/Sonnet/Haiku). Set timeouts, permissions, and concurrency limits. |
| **Chat Interface** | Rich markdown rendering (code blocks, tables, images). Plan mode with approve/reject. Interactive cards for tool confirmations. |
| **Mobile PWA** | Add to Home Screen on iOS/Android. Full functionality on mobile — voice input, push notifications, task management. |
| **CLI Session Sync** | Import and live-tail your terminal Claude Code sessions. Read-only — never interferes with the CLI process. |
| **Voice Input** | Dictate tasks using speech-to-text (OpenAI Whisper). Great for quick ideas on mobile. |
| **Task Inbox** | Capture tasks as they come, organize by project, dispatch when ready. Drag to reorder priorities. |
| **Push Notifications** | Get notified when agents finish, need input, or error out. Per-agent mute, global toggles, smart suppression when you're already looking. |
| **Git Integration** | View commit history, diffs, and branch status per project. Agents work in isolated worktrees. |
| **System Monitor** | Disk, memory, and GPU usage at a glance. Health checks for the backend and Claude CLI. |
| **Security** | Password auth with rate limiting. Inactivity-based lock. Self-signed SSL for LAN encryption. |
| **Backups** | Automatic hourly database backups. Session JSONL caching. Crash recovery with partial output salvage. |
| **Dark/Light Theme** | System-aware theme toggle. |

## How It Works

```
Your Phone / Browser
    |
    +-- AgentHive Frontend (React PWA, HTTPS)
    |
    +-- AgentHive Backend (FastAPI)
          |
          +-- Agent Dispatcher (manages lifecycle, queues, timeouts)
          +-- tmux Sessions (one per agent)
          |     +-- claude CLI (the same CLI you use in your terminal)
          |     +-- Isolated git worktrees per agent
          +-- Session Sync (tails CLI JSONL files, read-only)
          +-- Push Notifications (Web Push / VAPID)
          +-- SQLite Database (conversations, state, config)
```

AgentHive runs on your Linux machine (or any machine reachable over the network). It launches Claude Code CLI instances inside tmux sessions, streams their output to the web UI via WebSocket, and manages their lifecycle. You interact through the browser — from the same machine, from your laptop, or from your phone over Tailscale.

## Getting Started

### Prerequisites

- **Linux** host (Ubuntu 22.04+ recommended)
- **Node.js** 18+ and npm
- **Python** 3.11+
- **tmux** (usually pre-installed; `sudo apt install tmux` if not)
- **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
- **Claude subscription** — Claude Max or Pro (uses your existing subscription, no separate API billing)
- **OpenAI API key** _(optional, for voice input)_

### Quick Start

```bash
# 1. Clone
git clone https://github.com/jyao97/AgentHive.git agenthive-main
cd agenthive-main

# 2. Install system deps (if needed)
sudo apt-get install -y python3 python3-pip python3-venv tmux
npm install -g @anthropic-ai/claude-code

# 3. Configure
cp .env.example .env
nano .env   # Set HOST_PROJECTS_DIR, optionally OPENAI_API_KEY

# 4. Set up Python
python3 -m venv .venv
source .venv/bin/activate
pip install -r orchestrator/requirements.txt

# 5. Create projects directory
mkdir -p ~/agenthive-projects

# 6. Generate SSL certs (needed for mobile mic access)
mkdir -p certs
LAN_IP=$(hostname -I | awk '{print $1}')
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/selfsigned.key -out certs/selfsigned.crt \
  -subj "/CN=agenthive" \
  -addext "subjectAltName=DNS:agenthive,DNS:localhost,IP:127.0.0.1,IP:${LAN_IP}"
sudo cp certs/selfsigned.crt /usr/local/share/ca-certificates/agenthive.crt
sudo update-ca-certificates

# 7. Start backend
./run.sh

# 8. Start frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Open `https://<machine-ip>:3000` in your browser. Set a password on first visit.

On iPhone: Safari > Share > **Add to Home Screen** for a native app experience.

For detailed setup, server management, and production deployment, see [QUICKSTART.md](QUICKSTART.md).

## Configuration

All settings live in `.env`:

| Variable | Default | Description |
|---|---|---|
| `HOST_PROJECTS_DIR` | _(required)_ | Absolute path to your projects directory |
| `MAX_CONCURRENT_WORKERS` | `5` | Max simultaneous agent processes |
| `MAX_IDLE_AGENTS` | `20` | Max idle agents kept alive |
| `TASK_TIMEOUT_SECONDS` | `1800` | Default agent timeout (30 min) |
| `CC_MODEL` | `claude-opus-4-6` | Default Claude model |
| `OPENAI_API_KEY` | — | OpenAI key for voice input _(optional)_ |
| `PORT` | `8080` | Backend API port |
| `FRONTEND_PORT` | `3000` | Frontend HTTPS port |
| `VAPID_PRIVATE_KEY` | — | Web Push private key _(optional)_ |
| `VAPID_PUBLIC_KEY` | — | Web Push public key _(optional)_ |

## Remote Access with Tailscale

The easiest way to access AgentHive from your phone outside your LAN:

1. Install [Tailscale](https://tailscale.com) on your server and phone
2. `tailscale up` on both devices
3. Access AgentHive at `https://<tailscale-ip>:3000`

No port forwarding, no public exposure. Tailscale creates a secure WireGuard tunnel between your devices.

## Folder Layout

```
~/
├── agenthive-main/              <- This repo
│   ├── run.sh                   <- Launch script
│   ├── orchestrator/            <- FastAPI backend
│   ├── frontend/                <- React PWA
│   ├── certs/                   <- SSL certificates
│   ├── project-configs/         <- Project registry
│   ├── data/                    <- SQLite database
│   └── .env                     <- Configuration
│
└── agenthive-projects/          <- Your project repositories
    ├── my-web-app/
    ├── ml-pipeline/
    └── ...
```

## Troubleshooting

**Can't access from phone?**
Make sure port 3000 is open (`sudo ufw allow 3000`). Accept the self-signed certificate in your browser (tap "Advanced" > "Proceed").

**Agent fails to start?**
Check `logs/orchestrator.log`. Usually an expired OAuth token — run `claude setup-token` again.

**Voice input not working?**
Microphone requires HTTPS. Make sure you're accessing via `https://` and have valid SSL certs.

**Rate limited?**
Claude Max has usage limits. Reduce `MAX_CONCURRENT_WORKERS` or switch to a smaller model (Sonnet/Haiku).

**"Address already in use"?**
Another process is using the port. Kill it: `lsof -ti:8080 | xargs kill`

## Installing the CA Certificate

AgentHive uses a self-signed SSL certificate. Your server trusts it after setup, but other devices will show a browser warning until you install the cert.

**Download the cert** from another machine:
```bash
scp user@server-ip:~/agenthive-main/certs/selfsigned.crt ~/agenthive.crt
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

## Security

- Password authentication with exponential backoff rate limiting
- Inactivity-based session lock (configurable timeout)
- All traffic encrypted via HTTPS (self-signed or custom cert)
- OAuth tokens stored in `.env`, never exposed to agents
- Agents run as host subprocesses with configurable timeouts and permission controls
- Per-project concurrency limits prevent resource starvation

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.
