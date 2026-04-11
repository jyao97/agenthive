#!/bin/bash
# AgentHive — cross-platform launch script (Linux + macOS)
# Uses pm2 for process management.  Auto-migrates from systemd on first run.
# Usage:
#   ./run.sh           — restart both backend + frontend
#   ./run.sh stop      — stop both
#   ./run.sh status    — show service status
#   ./run.sh logs      — follow pm2 logs
#   ./run.sh startup   — enable auto-start on boot

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ECOSYSTEM="$SCRIPT_DIR/ecosystem.config.cjs"

# ── Ensure required directories exist ─────────────────────────────────
mkdir -p "$SCRIPT_DIR/data" "$SCRIPT_DIR/logs" "$SCRIPT_DIR/backups" "$SCRIPT_DIR/project-configs"

# ── Load .env for variable resolution ─────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# ── Migrate from systemd → pm2 (one-time, Linux only) ────────────────
_migrate_from_systemd() {
    local SYSTEMD_DIR="$HOME/.config/systemd/user"
    local BACKEND_UNIT="cc-orchestrator.service"
    local FRONTEND_UNIT="cc-frontend.service"
    local migrated=0

    if [ -f "$SYSTEMD_DIR/$BACKEND_UNIT" ] || [ -f "$SYSTEMD_DIR/$FRONTEND_UNIT" ]; then
        echo "Migrating from systemd to pm2..."
        # Stop and disable old services
        systemctl --user stop "$FRONTEND_UNIT" "$BACKEND_UNIT" 2>/dev/null || true
        systemctl --user disable "$FRONTEND_UNIT" "$BACKEND_UNIT" 2>/dev/null || true
        # Remove unit files
        rm -f "$SYSTEMD_DIR/$BACKEND_UNIT" "$SYSTEMD_DIR/$FRONTEND_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
        echo "Old systemd services removed."
        migrated=1
    fi

    return $migrated
}

# Only attempt migration on Linux where systemctl exists
if command -v systemctl >/dev/null 2>&1; then
    _migrate_from_systemd || true
fi

# ── Ensure pm2 is available ──────────────────────────────────────────
if ! command -v pm2 >/dev/null 2>&1; then
    echo "pm2 not found — installing globally..."
    npm install -g pm2
fi

# ── Port config ──────────────────────────────────────────────────────
PORT="${PORT:-8080}"
FPORT="${FRONTEND_PORT:-3000}"

# ── Command dispatch ──────────────────────────────────────────────────
CMD="${1:-restart}"

case "$CMD" in
    stop)
        echo "Stopping AgentHive..."
        pm2 stop "$ECOSYSTEM" 2>/dev/null || true
        echo "Stopped."
        ;;
    status)
        pm2 status
        ;;
    logs)
        pm2 logs --lines 50
        ;;
    startup)
        echo "Configuring auto-start on boot..."
        pm2 start "$ECOSYSTEM" 2>/dev/null || true
        pm2 save
        pm2 startup
        echo "Follow the instructions above if prompted."
        ;;
    restart|start)
        echo "Restarting AgentHive..."
        # Delete stale processes first — a prior crash or direct-kill can leave
        # PM2's process table referencing dead PIDs, causing TypeError crashes
        # on `pm2 restart`.  `delete` is idempotent and clears that state.
        pm2 delete agenthive-backend agenthive-frontend 2>/dev/null || true
        sleep 1   # let PM2 daemon finish cleanup to avoid stale-process race
        pm2 start "$ECOSYSTEM"

        # Wait for backend health
        echo -n "Waiting for backend..."
        for i in $(seq 1 30); do
            if curl -sf "http://localhost:${PORT}/api/health" >/dev/null 2>&1; then
                echo " ready!"
                break
            fi
            echo -n "."
            sleep 1
        done

        # Verify frontend
        echo -n "Waiting for frontend..."
        for i in $(seq 1 15); do
            if curl -sfk "https://localhost:${FPORT}" >/dev/null 2>&1; then
                echo " ready!"
                break
            fi
            echo -n "."
            sleep 1
        done

        echo ""
        pm2 status
        echo ""
        echo "AgentHive running at https://localhost:${FPORT}"
        ;;
    *)
        echo "Usage: ./run.sh [start|stop|restart|status|logs|startup]"
        exit 1
        ;;
esac
