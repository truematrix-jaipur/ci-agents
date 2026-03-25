import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class CampaignPlannerAgent(BaseAgent):
    AGENT_ROLE = "campaign_planner_agent"
    SYSTEM_PROMPT = """You are a Media and Campaign Planner Agent.
    You design cross-channel marketing campaigns across Facebook, Google, 
    and Social Media.
    
    You coordinate with FB Manager and Google Agent to ensure consistent 
    messaging and effective budget allocation."""

    def handle_task(self, task_data):
        logger.info(f"Campaign Planner {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "plan_campaign":
            return self._plan_campaign(task_data)
        else:
            return super().handle_task(task_data)

    def _plan_campaign(self, task_data):
        # Notify sub-agents of the new plan
        self.publish_task_to_agent("google_agent", {"type": "set_new_budget", "budget": 5000})
        self.publish_task_to_agent("fb_campaign_manager", {"type": "set_new_budget", "budget": 3000})
        
        return {"status": "success", "message": "Campaign plan dispatched."}

if __name__ == "__main__":
    agent = CampaignPlannerAgent()
    agent.run()
