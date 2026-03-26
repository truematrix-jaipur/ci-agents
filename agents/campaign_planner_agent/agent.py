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
            return self._execute_with_goal_target(task_data, self._plan_campaign, "plan_campaign")
        else:
            return super().handle_task(task_data)

    def _plan_campaign(self, task_data):
        plan = task_data.get("task", {})
        google_budget = float(plan.get("google_budget", 5000))
        fb_budget = float(plan.get("fb_budget", 3000))
        campaign_name = plan.get("campaign_name", "default_campaign")

        # Notify sub-agents of the new plan with traceable task IDs.
        google_task_id = self.publish_task_to_agent(
            "google_agent",
            {
                "type": "set_new_budget",
                "budget": google_budget,
                "channel": "google_ads",
                "campaign_name": campaign_name,
            },
        )
        fb_task_id = self.publish_task_to_agent(
            "fb_campaign_manager",
            {
                "type": "set_new_budget",
                "budget": fb_budget,
                "campaign_id": campaign_name,
            },
        )

        return {
            "status": "success",
            "message": "Campaign plan dispatched.",
            "campaign_name": campaign_name,
            "google_budget": google_budget,
            "fb_budget": fb_budget,
            "total_budget": round(google_budget + fb_budget, 2),
            "dispatched_count": 2,
            "dispatched": {
                "google_task_id": google_task_id,
                "fb_task_id": fb_task_id,
            },
        }

if __name__ == "__main__":
    agent = CampaignPlannerAgent()
    agent.run()
