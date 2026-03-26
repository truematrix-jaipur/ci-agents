#!/bin/bash
set -euo pipefail

# Start API server (agents are auto-managed by FastAPI startup watcher)
echo "Starting TrueMatrix Swarm runtime..."

# Ensure log directory exists before any agent starts
mkdir -p logs

# Clean up environment to avoid conflicts with old keys
unset GOOGLE_SERVICE_ACCOUNT_PATH
unset GOOGLE_API_KEY
unset GOOGLE_APPLICATION_CREDENTIALS

if pgrep -f "/home/agents/core/api_server.py" >/dev/null 2>&1; then
  echo "API server already running; skipping launch."
else
  nohup python3 core/api_server.py > logs/api_server.log 2>&1 &
  echo "API server launched."
fi

echo "Runtime ready. API startup loop will auto-start and keep all agents alive."
