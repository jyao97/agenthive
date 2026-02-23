# Quick Start — Lab Computer Deployment Guide

## Prerequisites

- Docker 24.0+ and Docker Compose v2 installed on lab computer
- Your user is in the `docker` group (`groups $USER` to check)
- Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code`)
- Anthropic API key (Claude Max subscription recommended)
- OpenAI API key (for voice recognition, optional)

---

## Step 0: Get files onto lab computer

```bash
# scp the entire folder, or git clone
scp -r cc-orchestrator/ lab-machine:~/cc-orchestrator/
ssh lab-machine
cd ~/cc-orchestrator
```

---

## Step 1: Initialize environment

```bash
chmod +x scripts/*.sh
./scripts/init.sh
```

This will:
- Verify Docker environment
- Create `.env` file (you need to fill in API keys)
- Create Docker volumes

Then edit `.env`:
```bash
nano .env
# Fill in:
# ANTHROPIC_API_KEY=sk-ant-xxx
# OPENAI_API_KEY=sk-xxx  (optional)
```

---

## Step 2: Build Docker images and start

```bash
# Build worker image
docker build -t cc-worker:latest ./worker/

# Start all services
docker compose up -d --build

# Check status
docker compose ps
curl http://localhost:8080/api/health
```

---

## Step 3: Register your projects

```bash
# Add projects (auto-clones into Docker volume)
./scripts/add-project.sh crowd-nav https://github.com/you/crowd-nav.git
./scripts/add-project.sh vla-delivery https://github.com/you/vla-delivery.git
./scripts/add-project.sh thermal-3dgs https://github.com/you/thermal-3dgs.git
```

Each project needs a `CLAUDE.md` in its root to give CC context and rules.
If missing, the script creates one from the template — you should edit it.

---

## Step 4: Start using

Open `http://lab-machine-ip:3000` in your browser.

On iPhone: Safari → Share → Add to Home Screen

1. Select project
2. Enter task (text or voice)
3. Wait for plan generation
4. Approve plan
5. CC worker executes inside Docker container
6. View results

---

## Step 5: Use CC to continue building this system

Once Phase 0 is running, remaining phases can be built by CC:

```bash
cd ~/cc-orchestrator

# Execute specific tasks
claude -p "Read TASKS.md and execute Task 1.1: Database Schema. Follow CLAUDE.md conventions strictly. Commit when done." \
  --dangerously-skip-permissions

# Or push through a whole phase
claude -p "Read TASKS.md and execute all Phase 1 tasks in order. Commit after each task." \
  --dangerously-skip-permissions
```

Once the dispatcher can run, it can schedule its own remaining tasks.

---

## Common Commands

```bash
# Check service status
docker compose ps

# View orchestrator logs
docker compose logs -f orchestrator

# List all worker containers
docker ps --filter "name=cc-worker-"

# Manually stop a worker
docker stop cc-worker-abc12345

# Clean up all exited workers
docker container prune --filter "label=cc-worker"

# Restart all services
docker compose restart

# Stop everything
docker compose down

# Check volume disk usage
docker system df -v
```

---

## Safety Checklist

After deployment, verify:

- [ ] Worker containers cannot access host home directory
  ```bash
  docker run --rm cc-worker:latest ls /home/  # Should only see ccworker
  ```
- [ ] Worker containers have no SSH keys
  ```bash
  docker run --rm cc-worker:latest ls -la /home/ccworker/.ssh  # Should not exist
  ```
- [ ] Worker resource limits are enforced
  ```bash
  docker stats  # Check CPU/MEM limits
  ```
- [ ] Backups are running
  ```bash
  docker exec cc-orchestrator ls /app/backups/
  ```

---

## Troubleshooting

**Q: Worker container fails to start**
A: Check `docker logs cc-worker-xxx` — usually an API key issue or image not built

**Q: CC credit exhausted**
A: Claude Max has rate limits. Reduce MAX_CONCURRENT_WORKERS, or use sonnet instead of opus

**Q: Disk full**
A: `docker system prune -a` to clean unused images, check backup volume size

**Q: Tasks keep FAILING**
A: Check the project's CLAUDE.md for clarity, review PROGRESS.md for failure records

**Q: Can't access from phone**
A: Make sure port 3000 is open on the lab machine's network, may need firewall rules

**Q: Need to restore a backup**
A: `./scripts/restore-backup.sh orchestrator_20260222_120000.db`
