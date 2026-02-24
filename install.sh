#!/bin/bash
# AgentHive — Full Installation Script
# Installs all dependencies and sets up the entire system.
# Requires: sudo access, Ubuntu 22.04+ (or Debian-based distro)
#
# Usage:
#   chmod +x install.sh
#   ./install.sh

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }
info() { echo -e "  ${BLUE}→${NC} $1"; }
header() { echo -e "\n${BOLD}$1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${BOLD}==========================================${NC}"
echo -e "${BOLD}  AgentHive — Full Installation${NC}"
echo -e "${BOLD}==========================================${NC}"
echo ""

# ─────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────
header "Pre-flight checks"

# Must not run as root (but must have sudo)
if [ "$(id -u)" -eq 0 ]; then
    fail "Do not run this script as root. Run as your normal user (sudo will be used when needed)."
fi

if ! sudo -v 2>/dev/null; then
    fail "This script requires sudo access. Please run as a user with sudo privileges."
fi
ok "sudo access confirmed"

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    ok "OS: $PRETTY_NAME"
else
    warn "Could not detect OS — proceeding anyway"
fi

# ─────────────────────────────────────────────
# Step 1: System packages
# ─────────────────────────────────────────────
header "Step 1/8: System packages"

sudo apt-get update -qq
sudo apt-get install -y -qq \
    git curl wget ca-certificates gnupg lsb-release \
    python3 python3-pip python3-venv \
    openssl jq \
    > /dev/null 2>&1
ok "System packages installed"

# ─────────────────────────────────────────────
# Step 2: Docker
# ─────────────────────────────────────────────
header "Step 2/8: Docker"

if command -v docker &>/dev/null; then
    docker_version=$(docker --version | grep -oP '\d+\.\d+' | head -1)
    docker_major=$(echo "$docker_version" | cut -d. -f1)
    if [ "$docker_major" -ge 24 ]; then
        ok "Docker $docker_version already installed"
    else
        warn "Docker $docker_version is too old (need 24.0+) — upgrading"
        curl -fsSL https://get.docker.com | sudo sh
        ok "Docker upgraded"
    fi
else
    info "Installing Docker..."
    curl -fsSL https://get.docker.com | sudo sh
    ok "Docker installed"
fi

# Ensure user is in docker group
if ! groups "$USER" | grep -q docker; then
    sudo usermod -aG docker "$USER"
    warn "Added $USER to docker group — you may need to log out and back in (or run 'newgrp docker')"
fi

# Verify Docker Compose v2
if docker compose version &>/dev/null; then
    ok "Docker Compose $(docker compose version --short)"
else
    fail "Docker Compose v2 not available. Install the docker-compose-plugin package."
fi

# ─────────────────────────────────────────────
# Step 3: Node.js + Claude Code CLI
# ─────────────────────────────────────────────
header "Step 3/8: Node.js & Claude Code CLI"

if command -v node &>/dev/null; then
    node_major=$(node --version | grep -oP '\d+' | head -1)
    if [ "$node_major" -ge 18 ]; then
        ok "Node.js $(node --version) already installed"
    else
        warn "Node.js $(node --version) too old — installing Node 20"
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y -qq nodejs > /dev/null 2>&1
        ok "Node.js 20 installed"
    fi
else
    info "Installing Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs > /dev/null 2>&1
    ok "Node.js 20 installed"
fi

if command -v claude &>/dev/null; then
    ok "Claude Code CLI already installed ($(claude --version 2>/dev/null || echo 'unknown version'))"
else
    info "Installing Claude Code CLI..."
    sudo npm install -g @anthropic-ai/claude-code
    ok "Claude Code CLI installed"
fi

# ─────────────────────────────────────────────
# Step 4: Python venv (for host-mode development)
# ─────────────────────────────────────────────
header "Step 4/8: Python environment"

if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    python3 -m venv "$SCRIPT_DIR/.venv"
    ok "Python venv created"
else
    ok "Python venv already exists"
fi

source "$SCRIPT_DIR/.venv/bin/activate"
pip install --quiet -r "$SCRIPT_DIR/orchestrator/requirements.txt"
ok "Python dependencies installed"
deactivate

# ─────────────────────────────────────────────
# Step 5: Frontend dependencies
# ─────────────────────────────────────────────
header "Step 5/8: Frontend dependencies"

cd "$SCRIPT_DIR/frontend"
npm ci --silent 2>/dev/null
ok "Frontend dependencies installed"
cd "$SCRIPT_DIR"

# ─────────────────────────────────────────────
# Step 6: SSL certificates
# ─────────────────────────────────────────────
header "Step 6/8: SSL certificates"

if [ -f "$SCRIPT_DIR/certs/selfsigned.key" ] && [ -f "$SCRIPT_DIR/certs/selfsigned.crt" ]; then
    ok "SSL certificates already exist"
else
    mkdir -p "$SCRIPT_DIR/certs"
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$SCRIPT_DIR/certs/selfsigned.key" \
        -out "$SCRIPT_DIR/certs/selfsigned.crt" \
        -subj "/CN=agenthive" \
        2>/dev/null
    ok "Self-signed SSL certificates generated (valid 365 days)"
fi

# ─────────────────────────────────────────────
# Step 7: Environment configuration
# ─────────────────────────────────────────────
header "Step 7/8: Environment configuration"

PROJECTS_DIR="$HOME/cc-projects"
mkdir -p "$PROJECTS_DIR"
ok "Projects directory: $PROJECTS_DIR"

if [ -f "$SCRIPT_DIR/.env" ]; then
    ok ".env already exists — skipping creation"
    warn "Review .env to make sure all values are correct"
else
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"

    # Auto-fill host-specific values
    sed -i "s|/home/YOUR_USERNAME/cc-projects|$PROJECTS_DIR|g" "$SCRIPT_DIR/.env"
    sed -i "s|HOST_USER_UID=1000|HOST_USER_UID=$(id -u)|g" "$SCRIPT_DIR/.env"

    # Add HOST_CLAUDE_DIR if not present
    if ! grep -q "HOST_CLAUDE_DIR" "$SCRIPT_DIR/.env"; then
        echo "" >> "$SCRIPT_DIR/.env"
        echo "# === Host Claude directory (for session symlinks) ===" >> "$SCRIPT_DIR/.env"
        echo "HOST_CLAUDE_DIR=$HOME/.claude" >> "$SCRIPT_DIR/.env"
    else
        sed -i "s|HOST_CLAUDE_DIR=.*|HOST_CLAUDE_DIR=$HOME/.claude|g" "$SCRIPT_DIR/.env"
    fi

    ok ".env created from template with auto-filled paths"

    # Prompt for OAuth token
    echo ""
    echo -e "  ${YELLOW}Claude OAuth token is required.${NC}"
    echo ""
    echo "  If you already have a token, paste it below."
    echo "  If not, run 'claude setup-token' in another terminal to generate one."
    echo "  (Press Enter to skip for now — you can edit .env later)"
    echo ""
    read -r -p "  CLAUDE_CODE_OAUTH_TOKEN: " oauth_token

    if [ -n "$oauth_token" ]; then
        sed -i "s|CLAUDE_CODE_OAUTH_TOKEN=.*|CLAUDE_CODE_OAUTH_TOKEN=$oauth_token|g" "$SCRIPT_DIR/.env"
        ok "OAuth token saved to .env"
    else
        warn "No token provided — edit .env and set CLAUDE_CODE_OAUTH_TOKEN before starting"
    fi

    # Optional: OpenAI key
    echo ""
    echo "  OpenAI API key (optional, for voice input). Press Enter to skip."
    read -r -p "  OPENAI_API_KEY: " openai_key

    if [ -n "$openai_key" ]; then
        sed -i "s|OPENAI_API_KEY=.*|OPENAI_API_KEY=$openai_key|g" "$SCRIPT_DIR/.env"
        ok "OpenAI key saved to .env"
    else
        info "Voice input disabled (no OpenAI key)"
    fi
fi

# ─────────────────────────────────────────────
# Step 8: Build Docker images & start services
# ─────────────────────────────────────────────
header "Step 8/8: Build & start services"

info "Building worker image..."
docker build -t cc-worker:latest "$SCRIPT_DIR/worker/" -q
ok "Worker image built"

info "Building and starting services..."
docker compose up -d --build 2>&1 | tail -5
ok "Services started"

# Wait for health check
echo ""
info "Waiting for orchestrator to become healthy..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8080/api/health > /dev/null 2>&1; then
        ok "Orchestrator is healthy"
        break
    fi
    if [ "$i" -eq 30 ]; then
        warn "Orchestrator not responding yet — check 'docker compose logs orchestrator'"
    fi
    sleep 2
done

# ─────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────
echo ""
echo -e "${BOLD}==========================================${NC}"
echo -e "${GREEN}${BOLD}  AgentHive installation complete!${NC}"
echo -e "${BOLD}==========================================${NC}"
echo ""

# Get machine IP for LAN access
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo "  Access the UI:"
echo -e "    ${BOLD}https://${LAN_IP}:3000${NC}  (from other devices)"
echo -e "    ${BOLD}https://localhost:3000${NC}   (from this machine)"
echo ""
echo "  On iPhone/Android: open the URL in Safari/Chrome,"
echo "  then Share → Add to Home Screen for a native app experience."
echo ""
echo "  Next steps:"
echo "    1. Register a project:"
echo "       ./scripts/add-project.sh my-project https://github.com/user/repo.git"
echo ""
echo "    2. Open the UI and start submitting tasks!"
echo ""
echo "  Useful commands:"
echo "    docker compose ps          # Check service status"
echo "    docker compose logs -f     # View logs"
echo "    docker compose restart     # Restart services"
echo "    docker compose down        # Stop everything"
echo ""
