#!/bin/bash
# AgentHive — host-mode launch script
# Delegates to systemd user services for process management.
# Usage:
#   ./run.sh           — restart both backend + frontend
#   ./run.sh stop      — stop both
#   ./run.sh status    — show service status
#   ./run.sh logs      — follow journalctl logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_DIR="$HOME/.config/systemd/user"
BACKEND_UNIT="cc-orchestrator.service"
FRONTEND_UNIT="cc-frontend.service"

# ── Ensure systemd unit files are installed & up-to-date ──────────────
_install_units() {
    mkdir -p "$SYSTEMD_DIR"

    # Resolve current paths (handles nvm / venv changes)
    local venv_python="$SCRIPT_DIR/.venv/bin/python3"
    local venv_uvicorn="$SCRIPT_DIR/.venv/bin/uvicorn"
    local node_bin
    node_bin="$(dirname "$(which node)")"
    local npx_bin="$node_bin/npx"

    local needs_reload=0

    # Backend unit
    local backend_unit_content
    backend_unit_content="$(cat <<EOF
[Unit]
Description=CC Orchestrator Backend (FastAPI/Uvicorn)
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR/orchestrator
ExecStart=$venv_uvicorn main:app --host 0.0.0.0 --port 8080
EnvironmentFile=$SCRIPT_DIR/.env
Environment=PATH=$node_bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PROJECTS_DIR=${HOST_PROJECTS_DIR:-$HOME/agenthive-projects}
Environment=DB_PATH=$SCRIPT_DIR/data/orchestrator.db
Environment=LOG_DIR=$SCRIPT_DIR/logs
Environment=BACKUP_DIR=$SCRIPT_DIR/backups
Environment=PROJECT_CONFIGS_PATH=$SCRIPT_DIR/project-configs
Environment=AGENTHIVE_MANAGED=1
UnsetEnvironment=CLAUDECODE CLAUDE_CODE_ENTRYPOINT
KillMode=process
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
)"

    if [ ! -f "$SYSTEMD_DIR/$BACKEND_UNIT" ] || \
       [ "$(cat "$SYSTEMD_DIR/$BACKEND_UNIT")" != "$backend_unit_content" ]; then
        echo "$backend_unit_content" > "$SYSTEMD_DIR/$BACKEND_UNIT"
        needs_reload=1
        echo "Updated $BACKEND_UNIT"
    fi

    # Frontend unit
    local frontend_unit_content
    frontend_unit_content="$(cat <<EOF
[Unit]
Description=CC Orchestrator Frontend (Vite Dev Server)
After=cc-orchestrator.service
BindsTo=cc-orchestrator.service

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR/frontend
ExecStart=$npx_bin vite --host 0.0.0.0 --port 3000
Environment=PATH=$node_bin:/usr/local/bin:/usr/bin:/bin
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
)"

    if [ ! -f "$SYSTEMD_DIR/$FRONTEND_UNIT" ] || \
       [ "$(cat "$SYSTEMD_DIR/$FRONTEND_UNIT")" != "$frontend_unit_content" ]; then
        echo "$frontend_unit_content" > "$SYSTEMD_DIR/$FRONTEND_UNIT"
        needs_reload=1
        echo "Updated $FRONTEND_UNIT"
    fi

    if [ "$needs_reload" -eq 1 ]; then
        systemctl --user daemon-reload
    fi
}

# ── Ensure required directories exist ─────────────────────────────────
mkdir -p "$SCRIPT_DIR/data" "$SCRIPT_DIR/logs" "$SCRIPT_DIR/backups" "$SCRIPT_DIR/project-configs"

# ── Load .env for variable resolution ─────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ── Command dispatch ──────────────────────────────────────────────────
CMD="${1:-restart}"

case "$CMD" in
    stop)
        echo "Stopping AgentHive..."
        systemctl --user stop "$FRONTEND_UNIT" "$BACKEND_UNIT" 2>/dev/null || true
        echo "Stopped."
        ;;
    status)
        systemctl --user status "$BACKEND_UNIT" "$FRONTEND_UNIT" 2>&1 || true
        ;;
    logs)
        journalctl --user -u "$BACKEND_UNIT" -u "$FRONTEND_UNIT" -f
        ;;
    restart|start)
        _install_units
        systemctl --user enable "$BACKEND_UNIT" "$FRONTEND_UNIT" 2>/dev/null

        echo "Restarting AgentHive..."
        systemctl --user restart "$BACKEND_UNIT"
        # Frontend auto-restarts via BindsTo dependency, but ensure it's started
        systemctl --user restart "$FRONTEND_UNIT"

        # Wait for backend health
        echo -n "Waiting for backend..."
        for i in $(seq 1 30); do
            if curl -sf http://localhost:8080/api/health >/dev/null 2>&1; then
                echo " ready!"
                break
            fi
            echo -n "."
            sleep 1
        done

        # Verify frontend
        echo -n "Waiting for frontend..."
        for i in $(seq 1 15); do
            if curl -sfk https://localhost:3000 >/dev/null 2>&1; then
                echo " ready!"
                break
            fi
            echo -n "."
            sleep 1
        done

        echo ""
        systemctl --user status "$BACKEND_UNIT" "$FRONTEND_UNIT" --no-pager -l 2>&1 | head -20
        echo ""
        echo "AgentHive running at https://localhost:3000"
        ;;
    *)
        echo "Usage: ./run.sh [start|stop|restart|status|logs]"
        exit 1
        ;;
esac
