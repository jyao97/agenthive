#!/bin/bash
set -e

echo "========================================="
echo "  AgentHive — Initialization"
echo "========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok() { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

# 1. Check Docker
echo "Checking environment..."
command -v docker >/dev/null 2>&1 || fail "Docker is not installed"
docker_version=$(docker --version | grep -oP '\d+\.\d+' | head -1)
docker_major=$(echo "$docker_version" | cut -d. -f1)
if [ "$docker_major" -lt 24 ]; then
    fail "Docker $docker_version is too old — requires 24.0+"
fi
ok "Docker $docker_version"

docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is not installed"
ok "Docker Compose $(docker compose version --short)"

# Check docker group membership
if groups $USER | grep -q docker; then
    ok "User $USER is in the docker group"
else
    warn "User $USER is NOT in the docker group — may need sudo"
    warn "Run: sudo usermod -aG docker $USER && newgrp docker"
fi

# 2. Check disk space
available_gb=$(df -BG . | awk 'NR==2{print $4}' | tr -d 'G')
if [ "$available_gb" -lt 20 ]; then
    warn "Only ${available_gb}GB disk remaining — recommend at least 20GB"
else
    ok "Disk space: ${available_gb}GB available"
fi

# 3. Create .env
echo ""
echo "Setting up environment variables..."
if [ ! -f .env ]; then
    cp .env.example .env
    # Auto-fill HOST_PROJECTS_DIR and HOST_USER_UID
    sed -i "s|/home/YOUR_USERNAME/cc-projects|$HOME/cc-projects|g" .env
    sed -i "s|/home/YOUR_USERNAME/.claude|$HOME/.claude|g" .env
    sed -i "s|HOST_USER_UID=1000|HOST_USER_UID=$(id -u)|g" .env
    ok "Created .env (copied from .env.example, auto-filled paths)"
    echo ""
    echo -e "  ${YELLOW}Please review .env and fill in any missing values:${NC}"
    echo "  nano .env"
    echo ""
else
    ok ".env already exists, skipping"
fi

# 3b. Create host projects directory
source .env 2>/dev/null || true
PROJECTS_DIR="${HOST_PROJECTS_DIR:-$HOME/cc-projects}"
if [ ! -d "$PROJECTS_DIR" ]; then
    mkdir -p "$PROJECTS_DIR"
    ok "Created projects directory: $PROJECTS_DIR"
else
    ok "Projects directory exists: $PROJECTS_DIR"
fi

# 4. Create Docker volumes
echo "Creating Docker volumes..."
for vol in cc-orch-db cc-orch-backups cc-projects cc-git-bare cc-logs; do
    if docker volume inspect $vol >/dev/null 2>&1; then
        ok "Volume $vol already exists"
    else
        docker volume create $vol >/dev/null
        ok "Volume $vol created"
    fi
done

# 5. Create necessary directories
mkdir -p logs
mkdir -p projects
ok "Local directories created"

# 6. Initialize projects/registry.yaml
if [ ! -f projects/registry.yaml ]; then
    cat > projects/registry.yaml << 'EOF'
# AgentHive Project Registry
# Use ./scripts/add-project.sh to add projects, or edit manually

projects: []

# Example:
# projects:
#   - name: my-project
#     display_name: "My Project"
#     path: /projects/my-project
#     git_remote: https://github.com/user/my-project.git
#     default_model: claude-sonnet-4-5-20250514
#     max_concurrent: 2
EOF
    ok "projects/registry.yaml created"
else
    ok "projects/registry.yaml already exists"
fi

# 7. Create project CLAUDE.md template
mkdir -p projects/templates
if [ ! -f projects/templates/project-claude.md ]; then
    cat > projects/templates/project-claude.md << 'TMPL'
# CLAUDE.md — {PROJECT_NAME}

## Project Description
(please fill in)

## Tech Stack
(please fill in languages, frameworks, tools)

## Directory Structure
(please describe key directories and files)

## Development Rules
- Commit after each meaningful step, message format: `[task-{id}] short description`
- All existing tests must pass
- When uncertain, choose the most conservative approach
- Write lessons learned to PROGRESS.md after completion
- Do not modify CLAUDE.md (unless the task explicitly requires it)
- Output EXIT_SUCCESS on completion
- Output EXIT_FAILURE: {reason} on failure
TMPL
    ok "Project CLAUDE.md template created"
fi

echo ""
echo "========================================="
echo -e "  ${GREEN}Initialization complete!${NC}"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Edit .env and fill in your API keys"
echo "  2. docker build -t cc-worker:latest ./worker/"
echo "  3. docker compose up -d --build"
echo "  4. ./scripts/add-project.sh <name> <git-url>"
echo "  5. Open http://localhost:3000 in browser"
echo ""
