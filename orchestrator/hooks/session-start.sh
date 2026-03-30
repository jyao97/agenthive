#!/bin/bash
# SessionStart hook: notifies the orchestrator about ALL session starts.
# Installed globally (~/.claude/settings.json) so every claude process
# is detected, whether or not AgentHive spawned it.
#
# Two modes:
#   Managed (AHIVE_AGENT_ID set): session rotation signal for existing agent
#   Unmanaged: pending-session entry for user to confirm in the UI
#
# Tries HTTP POST first; falls back to local file when orchestrator is offline.

PAYLOAD=$(cat)
export SESSION_ID=$(echo "$PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0
export SESSION_SOURCE=$(echo "$PAYLOAD" | python3 -c "import sys,json; print(json.load(sys.stdin).get('source',''))" 2>/dev/null)

PORT="${AHIVE_PORT:-8080}"
AGENT_ID="${AHIVE_AGENT_ID:-}"

# Try HTTP POST to orchestrator
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
  -X POST "http://localhost:${PORT}/api/hooks/agent-session-start" \
  -H "Content-Type: application/json" \
  -H "X-Agent-Id: ${AGENT_ID}" \
  -H "X-Session-Cwd: ${PWD}" \
  -H "X-Tmux-Pane: ${TMUX_PANE:-}" \
  -d "$(python3 -c "import json,os; print(json.dumps({'session_id': os.environ['SESSION_ID'], 'source': os.environ.get('SESSION_SOURCE','')}))" )" \
  2>/dev/null)

[ "$HTTP_CODE" = "200" ] && exit 0

# Orchestrator offline — persist signal file for later pickup

# Managed agent: write signal file for session rotation detection
if [ -n "$AGENT_ID" ]; then
  echo "$SESSION_ID" > "/tmp/ahive-${AGENT_ID}.newsession" 2>/dev/null
fi
