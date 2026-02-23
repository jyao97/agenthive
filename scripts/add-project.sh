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
NC='\033[0m'

ok() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

# Check if project already registered
if grep -q "name: $PROJECT_NAME" "$REGISTRY" 2>/dev/null; then
    fail "Project '$PROJECT_NAME' is already registered in registry.yaml"
fi

echo "Registering project: $PROJECT_NAME"
echo "Git remote: $GIT_REMOTE"
echo ""

# Clone into Docker volume
echo "Cloning project into Docker volume..."
docker run --rm \
    -v cc-projects:/projects \
    alpine/git \
    clone "$GIT_REMOTE" "/projects/$PROJECT_NAME" 2>&1 || {
    # If clone fails (e.g. private repo), create empty directory
    echo "Git clone failed — creating empty project directory..."
    docker run --rm \
        -v cc-projects:/projects \
        alpine \
        mkdir -p "/projects/$PROJECT_NAME"
}
ok "Project code is ready"

# Check for CLAUDE.md, create from template if missing
docker run --rm \
    -v cc-projects:/projects \
    alpine \
    test -f "/projects/$PROJECT_NAME/CLAUDE.md" 2>/dev/null || {
    echo "No CLAUDE.md found in project — creating from template..."
    docker run --rm \
        -v cc-projects:/projects \
        -v "$(pwd)/projects/templates:/templates:ro" \
        alpine \
        sh -c "sed 's/{PROJECT_NAME}/$PROJECT_NAME/g' /templates/project-claude.md > /projects/$PROJECT_NAME/CLAUDE.md"
    echo "⚠️  Please edit the project's CLAUDE.md with project-specific info"
}

# Ensure PROGRESS.md exists
docker run --rm \
    -v cc-projects:/projects \
    alpine \
    sh -c "test -f /projects/$PROJECT_NAME/PROGRESS.md || echo '# PROGRESS.md\n\n(CC worker lessons learned)' > /projects/$PROJECT_NAME/PROGRESS.md"

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
    default_model: claude-sonnet-4-5-20250514
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
echo "  2. Edit project CLAUDE.md: docker run --rm -it -v cc-projects:/projects alpine vi /projects/$PROJECT_NAME/CLAUDE.md"
echo "  3. Restart orchestrator: docker compose restart orchestrator"
echo ""
