#!/bin/bash
# AgentHive — cross-platform launch script (Linux + macOS)
# Uses pm2 for process management (replaces systemd).
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

# ── Ensure pm2 is available ──────────────────────────────────────────
if ! command -v pm2 >/dev/null 2>&1; then
    echo "pm2 not found — installing globally..."
    npm install -g pm2
fi

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
        pm2 start "$ECOSYSTEM"
        pm2 save
        pm2 startup
        echo "Follow the instructions above if prompted."
        ;;
    restart|start)
        echo "Restarting AgentHive..."
        pm2 restart "$ECOSYSTEM" 2>/dev/null || pm2 start "$ECOSYSTEM"

        # Wait for backend health
        PORT=$(grep -E '^PORT=' "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 8080)
        PORT=${PORT:-8080}
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
        FPORT=$(grep -E '^FRONTEND_PORT=' "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 3000)
        FPORT=${FPORT:-3000}
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
