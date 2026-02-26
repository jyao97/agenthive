# Quick Start Guide

## Prerequisites

- Linux host (Ubuntu 22.04+ recommended)
- Node.js 18+ and npm
- Python 3.11+
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)
- Claude Max or Pro subscription
- OpenAI API key (optional, for voice recognition)

---

## Step 0: Get files onto your machine

```bash
git clone https://github.com/jyao97/AgentHive.git agenthive-main
cd agenthive-main
```

---

## Step 1: Initialize environment

```bash
cp .env.example .env
```

Then edit `.env`:
```bash
nano .env
# Fill in:
# HOST_PROJECTS_DIR=/home/YOUR_USERNAME/agenthive-projects
# CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-YOUR_TOKEN_HERE
# OPENAI_API_KEY=sk-xxx  (optional)
```

Generate an OAuth token:
```bash
claude setup-token
```

---

## Step 2: Set up Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r orchestrator/requirements.txt
```

---

## Step 3: Register your projects

Create your projects directory and register projects via the web UI or API:

```bash
mkdir -p ~/agenthive-projects
# Clone or create projects in this directory
cd ~/agenthive-projects
git clone https://github.com/you/crowd-nav.git
git clone https://github.com/you/vla-delivery.git
```

Then register them via the web UI (New > Project) or API:
```bash
curl -X POST http://localhost:8080/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "crowd-nav"}'
```

Each project should have a `CLAUDE.md` in its root to give Claude context and rules.

---

## Step 4: Start services

```bash
# Backend (terminal 1)
./run.sh

# Frontend (terminal 2)
cd frontend
npm install
npm run dev
```

Both servers must be running. The backend serves the API on port 8080, the frontend dev server proxies API requests to it and serves the UI on port 3000.

---

## Step 5: Start using

Open `https://<machine-ip>:3000` in your browser.

On iPhone: Safari > Share > Add to Home Screen

1. Set a password on first visit
2. Create a project (or it auto-loads from registry.yaml)
3. Create an agent — pick INTERVIEW (chat only) or AUTO (executes immediately)
4. Enter task (text or voice)
5. Agent executes the task
6. View results in the chat interface

---

## Server Management

### Starting

```bash
# Start backend (foreground — logs go to stdout)
./run.sh

# Start backend in background
nohup ./run.sh > logs/orchestrator.log 2>&1 &

# Start frontend dev server
cd frontend && npm run dev

# Start frontend in background
cd frontend && nohup npm run dev > /dev/null 2>&1 &
```

### Stopping

```bash
# Stop backend (find and kill the uvicorn process)
lsof -ti:8080 | xargs kill

# Stop frontend dev server
lsof -ti:3000 | xargs kill

# Stop both
lsof -ti:8080,:3000 | xargs kill
```

### Restarting

```bash
# Restart backend
lsof -ti:8080 | xargs kill; sleep 1; nohup ./run.sh > logs/orchestrator.log 2>&1 &

# Restart frontend
lsof -ti:3000 | xargs kill; sleep 1; cd frontend && nohup npm run dev > /dev/null 2>&1 &
```

### Changing Ports

Default ports: **8080** (backend) and **3000** (frontend).

**Backend port** — set `PORT` in `.env`:
```bash
# .env
PORT=9090
```

**Frontend port** — set `FRONTEND_PORT` in `.env` or pass `--port` directly:
```bash
# Option 1: .env
FRONTEND_PORT=4000

# Option 2: command line
cd frontend && npm run dev -- --port 4000
```

**Important:** If you change the backend port, you must also update the frontend's proxy config in `frontend/vite.config.js` so API requests reach the backend:

```js
// frontend/vite.config.js — update both proxy targets
proxy: {
  '/api': 'http://localhost:9090',       // match your PORT
  '/ws': { target: 'ws://localhost:9090', ws: true },
},
```

If you change the frontend port, update your firewall rules accordingly:
```bash
sudo ufw allow 4000
```

### Production Build

For production, build the frontend into static files and serve everything from the backend:

```bash
# Build frontend
cd frontend && npm run build

# The built files are in frontend/dist/
# Serve with any static file server, or configure a reverse proxy (nginx, caddy)
```

---

## Logs and Backups

```bash
# View backend logs (if running in foreground, logs go to stdout)
tail -f logs/orchestrator.log

# List database backups (automatic hourly backups)
ls -lh backups/

# Restore a backup
cp backups/orchestrator-YYYYMMDD-HHMMSS.db data/orchestrator.db
lsof -ti:8080 | xargs kill; sleep 1; ./run.sh
```

---

## Troubleshooting

**Q: "Address already in use" when starting**
A: Another process is using the port. Kill it first: `lsof -ti:8080 | xargs kill`

**Q: Agent fails to start**
A: Check `logs/orchestrator.log` — usually an expired OAuth token. Run `claude setup-token` again and update `.env`.

**Q: Rate limited**
A: Claude Max has rate limits. Reduce `MAX_CONCURRENT_WORKERS` in `.env`, or use Sonnet instead of Opus.

**Q: Tasks keep failing**
A: Check the project's `CLAUDE.md` for clarity, review `PROGRESS.md` for failure records.

**Q: Can't access from phone**
A: Make sure the frontend port is open (`sudo ufw allow 3000`). Accept the self-signed certificate in your browser.

**Q: Need to restore a backup**
A: Copy a backup from `backups/` over `data/orchestrator.db` and restart the backend.
