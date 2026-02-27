#!/bin/bash
# AgentHive — host-mode launch script
# Run the orchestrator directly on the host (no Docker)

set -euo pipefail

# Load .env if present
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Resolve PROJECTS_DIR from either name
export PROJECTS_DIR="${PROJECTS_DIR:-${HOST_PROJECTS_DIR:-}}"

# Resolve absolute paths from project root (before cd orchestrator)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export DB_PATH="${DB_PATH:-${SCRIPT_DIR}/data/orchestrator.db}"
export LOG_DIR="${LOG_DIR:-${SCRIPT_DIR}/logs}"
export BACKUP_DIR="${BACKUP_DIR:-${SCRIPT_DIR}/backups}"
export PROJECT_CONFIGS_PATH="${PROJECT_CONFIGS_PATH:-${SCRIPT_DIR}/project-configs}"

# Ensure directories exist
mkdir -p "${SCRIPT_DIR}/data" "${SCRIPT_DIR}/logs" "${SCRIPT_DIR}/backups" "${SCRIPT_DIR}/project-configs"

# Activate venv if present
if [ -d .venv ]; then
    source .venv/bin/activate
fi

# Clear Claude Code nesting-detection vars so spawned agents don't think
# they're running inside another Claude Code session.
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT 2>/dev/null || true

# Kill stale processes LISTENING on the port (not proxy clients like Vite)
PORT="${PORT:-8080}"
for pid in $(lsof -ti :"$PORT" -sTCP:LISTEN 2>/dev/null); do
    if [ "$pid" != "$$" ]; then
        echo "Killing stale process $pid on port $PORT"
        kill "$pid" 2>/dev/null
    fi
done
# Wait for port to be free
for i in $(seq 1 30); do
    lsof -ti :"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.3
done
# Force-kill any listener still clinging
for pid in $(lsof -ti :"$PORT" -sTCP:LISTEN 2>/dev/null); do
    echo "Force-killing process $pid on port $PORT"
    kill -9 "$pid" 2>/dev/null
done

# Start the orchestrator
cd orchestrator && exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
