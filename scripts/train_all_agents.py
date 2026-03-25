import sys
import os
import json
from pathlib import Path
import redis

# Append project root
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from core.db_connectors.db_manager import db_manager

def train_swarm():
    print("🚀 Initializing Swarm-Wide Training...")

    # 1. Read the Manual — path derived from project root, not hard-coded
    manual_path = _PROJECT_ROOT / "docs" / "swarm_operations_manual.md"
    with open(manual_path, "r") as f:
        manual_content = f.read()

    # 2. Define all agents
    agents = [
        "wordpress_tech", "seo_agent", "data_analyser", "integration_agent",
        "erpnext_agent", "erpnext_dev_agent", "devops_agent", "design_agent",
        "growth_agent", "campaign_planner_agent", "email_marketing_agent",
        "google_agent", "fb_campaign_manager", "smo_agent",
        "skill_agent", "training_agent", "agent_builder", "server_agent"
    ]

    redis_client = db_manager.get_redis_client()

    # 3. Dispatch training task for each agent
    for agent in agents:
        payload = {
            "source_agent": "system_bootstrap",
            "task": {
                "type": "train_agent",
                "target_agent": agent,
                "knowledge_content": manual_content,
                "source": "Core Swarm Operations Manual v1.0"
            }
        }
        # Send to training_agent queue
        redis_client.publish("task_queue_training_agent", json.dumps(payload))
        print(f"✅ Dispatched training for: {agent}")

    print("\n✨ Training sequence completed. Training Agent is processing in background.")

if __name__ == "__main__":
    train_swarm()
