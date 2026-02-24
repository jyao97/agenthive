#!/bin/bash
set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <project-name> <git-remote-url>"
    echo "Example: $0 crowd-nav https://github.com/user/crowd-nav.git"
    exit 1
fi

PROJECT_NAME="$1"
GIT_REMOTE="$2"
REGISTRY="projects/registry.yaml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok() { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

# Check if project already registered
if grep -q "name: $PROJECT_NAME" "$REGISTRY" 2>/dev/null; then
    fail "Project '$PROJECT_NAME' is already registered in registry.yaml"
fi

# Load HOST_PROJECTS_DIR from .env
if [ -f .env ]; then
    source .env 2>/dev/null || true
fi
PROJECTS_DIR="${HOST_PROJECTS_DIR:-$HOME/agenthive-projects}"

echo "Registering project: $PROJECT_NAME"
echo "Git remote: $GIT_REMOTE"
echo "Projects dir: $PROJECTS_DIR"
echo ""

# Ensure projects directory exists
mkdir -p "$PROJECTS_DIR"

# Clone into host projects directory
echo "Cloning project..."
if [ -d "$PROJECTS_DIR/$PROJECT_NAME" ]; then
    warn "Directory $PROJECTS_DIR/$PROJECT_NAME already exists — skipping clone"
else
    git clone "$GIT_REMOTE" "$PROJECTS_DIR/$PROJECT_NAME" 2>&1 || {
        # If clone fails (e.g. private repo), create empty directory
        echo "Git clone failed — creating empty project directory..."
        mkdir -p "$PROJECTS_DIR/$PROJECT_NAME"
    }
fi
ok "Project code is ready at $PROJECTS_DIR/$PROJECT_NAME"

# Check for CLAUDE.md, create from template if missing
if [ ! -f "$PROJECTS_DIR/$PROJECT_NAME/CLAUDE.md" ]; then
    echo "No CLAUDE.md found — creating from template..."
    if [ -f "projects/templates/project-claude.md" ]; then
        sed "s/{PROJECT_NAME}/$PROJECT_NAME/g" projects/templates/project-claude.md \
            > "$PROJECTS_DIR/$PROJECT_NAME/CLAUDE.md"
    else
        echo "# CLAUDE.md — $PROJECT_NAME" > "$PROJECTS_DIR/$PROJECT_NAME/CLAUDE.md"
    fi
    echo "⚠️  Please edit the project's CLAUDE.md with project-specific info"
fi

# Ensure PROGRESS.md exists
if [ ! -f "$PROJECTS_DIR/$PROJECT_NAME/PROGRESS.md" ]; then
    printf '# PROGRESS.md\n\n(CC worker lessons learned)\n' > "$PROJECTS_DIR/$PROJECT_NAME/PROGRESS.md"
fi

# Append to registry.yaml
# If registry has "projects: []", replace with content format
if grep -q "^projects: \[\]" "$REGISTRY"; then
    sed -i "s/^projects: \[\]/projects:/" "$REGISTRY"
fi

cat >> "$REGISTRY" << EOF

  - name: ${PROJECT_NAME}
    display_name: "${PROJECT_NAME}"
    path: /projects/${PROJECT_NAME}
    git_remote: ${GIT_REMOTE}
    default_model: claude-opus-4-6
    max_concurrent: 2
EOF

ok "Added to $REGISTRY"

echo ""
echo "========================================="
echo -e "${GREEN}Project '$PROJECT_NAME' registered successfully!${NC}"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Edit projects/registry.yaml to adjust config (display_name, max_concurrent, etc.)"
echo "  2. Edit project CLAUDE.md: $PROJECTS_DIR/$PROJECT_NAME/CLAUDE.md"
echo "  3. Restart orchestrator: docker compose restart orchestrator"
echo ""
