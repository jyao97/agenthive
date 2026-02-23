#!/bin/bash
set -e

# CC Worker Container Entrypoint
# Args: $1 = prompt, $2 = project_dir (optional, defaults to current dir)

if [ -n "$2" ]; then
    cd "$2"
fi

# Warn if no CLAUDE.md in project
if [ ! -f CLAUDE.md ]; then
    echo "WARNING: No CLAUDE.md found in project directory $(pwd)"
fi

# Execute Claude Code CLI
# --dangerously-skip-permissions is safe here: we are inside an isolated container
exec claude -p "$1" \
    --dangerously-skip-permissions \
    --output-format stream-json \
    --verbose
