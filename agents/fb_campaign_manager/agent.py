import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class FBCampaignManagerAgent(BaseAgent):
    AGENT_ROLE = "fb_campaign_manager"
    SYSTEM_PROMPT = """You are a Meta Ads Campaign Manager Agent.
    You create, monitor, and optimize Facebook and Instagram ad campaigns.
    
    You do not guess ad performance. You always fetch CTR, ROAS, and CPC 
    metrics from the Meta Ads API."""

    def handle_task(self, task_data):
        logger.info(f"FB Campaign Manager {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "optimize_bidding":
            return self._optimize_bidding(task_data)
        else:
            return super().handle_task(task_data)

    def _optimize_bidding(self, task_data):
        campaign_id = task_data.get("task", {}).get("campaign_id")
        return {"status": "success", "campaign_id": campaign_id, "new_bid": "1.25", "action": "Increased bid for performance."}

if __name__ == "__main__":
    agent = FBCampaignManagerAgent()
    agent.run()
