#!/bin/bash
set -euo pipefail

# Start all agents + API server in the background with nohup
echo "Starting TrueMatrix Swarm Agents..."

# Ensure log directory exists before any agent starts
mkdir -p logs

# Clean up environment to avoid conflicts with old keys
unset GOOGLE_SERVICE_ACCOUNT_PATH
unset GOOGLE_API_KEY
unset GOOGLE_APPLICATION_CREDENTIALS

# ── Core API Server ────────────────────────────────────────────────────────
nohup python3 core/api_server.py > logs/api_server.log 2>&1 &

# ── Specialized Agents ─────────────────────────────────────────────────────
nohup python3 agents/wordpress_tech/agent.py > logs/wordpress_tech.log 2>&1 &
nohup python3 agents/seo_agent/agent.py > logs/seo_agent.log 2>&1 &
nohup python3 agents/data_analyser/agent.py > logs/data_analyser.log 2>&1 &
nohup python3 agents/integration_agent/agent.py > logs/integration_agent.log 2>&1 &
nohup python3 agents/erpnext_agent/agent.py > logs/erpnext_agent.log 2>&1 &
nohup python3 agents/erpnext_dev_agent/agent.py > logs/erpnext_dev_agent.log 2>&1 &
nohup python3 agents/devops_agent/agent.py > logs/devops_agent.log 2>&1 &
nohup python3 agents/growth_agent/agent.py > logs/growth_agent.log 2>&1 &
nohup python3 agents/campaign_planner_agent/agent.py > logs/campaign_planner_agent.log 2>&1 &
nohup python3 agents/email_marketing_agent/agent.py > logs/email_marketing_agent.log 2>&1 &
nohup python3 agents/google_agent/agent.py > logs/google_agent.log 2>&1 &
nohup python3 agents/fb_campaign_manager/agent.py > logs/fb_campaign_manager.log 2>&1 &
nohup python3 agents/smo_agent/agent.py > logs/smo_agent.log 2>&1 &
nohup python3 agents/design_agent/agent.py > logs/design_agent.log 2>&1 &
nohup python3 agents/skill_agent/agent.py > logs/skill_agent.log 2>&1 &
nohup python3 agents/training_agent/agent.py > logs/training_agent.log 2>&1 &
nohup python3 agents/agent_builder/agent.py > logs/agent_builder.log 2>&1 &
nohup python3 agents/server_agent/agent.py > logs/server_agent.log 2>&1 &

echo "All agents and API server started. Logs available in logs/ directory."
