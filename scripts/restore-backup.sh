#!/bin/bash
set -e

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-file>"
    echo "Example: $0 orchestrator_20260222_120000.db"
    echo ""
    echo "Available backups:"
    docker run --rm -v cc-orch-backups:/backups alpine ls -la /backups/ 2>/dev/null || echo "  (cannot access backup volume)"
    exit 1
fi

BACKUP="$1"

echo "⚠️  This will OVERWRITE the current database!"
read -p "Confirm restore? (y/N) " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Cancelled"
    exit 0
fi

echo "Stopping orchestrator..."
docker compose stop orchestrator

echo "Restoring database..."
docker run --rm \
    -v cc-orch-backups:/backups:ro \
    -v cc-orch-db:/db \
    alpine \
    cp "/backups/$BACKUP" /db/orchestrator.db

echo "Restarting orchestrator..."
docker compose start orchestrator

echo "✓ Database restored from $BACKUP"
