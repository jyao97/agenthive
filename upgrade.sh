#!/usr/bin/env bash
# AgentHive — upgrade to latest version
# Usage:  ./upgrade.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

B='\033[1m'; GREEN='\033[32m'; YELLOW='\033[33m'; R='\033[0m'
info()  { echo -e "  ${GREEN}+${R} $1"; }
warn()  { echo -e "  ${YELLOW}!${R} $1"; }

echo -e "\n  ${B}AgentHive Upgrade${R}\n"

# ── Pull latest code ────────────────────────────────────────────────
info "Pulling latest code..."
git pull --ff-only || { warn "git pull failed — resolve conflicts manually"; exit 1; }

# ── Update Python dependencies ──────────────────────────────────────
if [ -f "$SCRIPT_DIR/.venv/bin/pip" ]; then
    info "Updating Python dependencies..."
    "$SCRIPT_DIR/.venv/bin/pip" install -q -r "$SCRIPT_DIR/orchestrator/requirements.txt"
else
    warn "No .venv found — skipping Python deps (run ./setup.sh first)"
fi

# ── Update frontend dependencies ────────────────────────────────────
if [ -f "$SCRIPT_DIR/frontend/package.json" ]; then
    info "Updating frontend dependencies..."
    (cd "$SCRIPT_DIR/frontend" && npm install --silent)
fi

# ── Restart services ────────────────────────────────────────────────
if command -v pm2 >/dev/null 2>&1; then
    info "Restarting AgentHive..."
    "$SCRIPT_DIR/run.sh" restart
else
    warn "pm2 not found — start manually with ./run.sh"
fi

echo -e "\n  ${GREEN}${B}Upgrade complete!${R}\n"
