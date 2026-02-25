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
chmod +x scripts/*.sh
./scripts/init.sh
```

This will:
- Verify system dependencies (Node.js, Python, Claude CLI)
- Create `.env` file from template

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

```bash
mkdir -p ~/agenthive-projects
./scripts/add-project.sh crowd-nav https://github.com/you/crowd-nav.git
./scripts/add-project.sh vla-delivery https://github.com/you/vla-delivery.git
```

Each project needs a `CLAUDE.md` in its root to give Claude context and rules.
If missing, the script creates one from the template — you should edit it.

---

## Step 4: Start services

```bash
# Backend
./run.sh

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

---

## Step 5: Start using

Open `https://<machine-ip>:3000` in your browser.

On iPhone: Safari > Share > Add to Home Screen

1. Select project
2. Enter task (text or voice)
3. Wait for plan generation
4. Approve plan
5. Agent executes the task
6. View results

---

## Common Commands

```bash
# Start/stop services
./run.sh                                      # Start backend
cd frontend && npm run dev                    # Start frontend

# Project management
./scripts/add-project.sh <name> <git-url>     # Register a project

# Logs
tail -f logs/orchestrator.log                 # View backend logs

# Backups
ls backups/                                   # List backups
./scripts/restore-backup.sh <backup-file>     # Restore from backup
```

---

## Troubleshooting

**Q: Agent fails to start**
A: Check `logs/orchestrator.log` — usually an expired OAuth token. Run `claude setup-token` again and update `.env`.

**Q: Rate limited**
A: Claude Max has rate limits. Reduce `MAX_CONCURRENT_WORKERS` in `.env`, or use Sonnet instead of Opus.

**Q: Tasks keep failing**
A: Check the project's `CLAUDE.md` for clarity, review `PROGRESS.md` for failure records.

**Q: Can't access from phone**
A: Make sure port 3000 is open (`sudo ufw allow 3000`). Accept the self-signed certificate in your browser.

**Q: Need to restore a backup**
A: `./scripts/restore-backup.sh <backup-filename>`
