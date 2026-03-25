import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class EmailMarketingAgent(BaseAgent):
    AGENT_ROLE = "email_marketing_agent"
    SYSTEM_PROMPT = """You are an expert Email Marketing Strategist.
    You manage campaigns, lists, and segments in the email marketing tool.
    
    You do not assume list growth. You always verify the latest subscriber 
    counts and bounce rates."""

    def handle_task(self, task_data):
        logger.info(f"Email Marketing Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "send_newsletter":
            return self._send_newsletter(task_data)
        else:
            return super().handle_task(task_data)

    def _send_newsletter(self, task_data):
        # Implementation for Mailpoller or other email tools
        return {"status": "success", "message": "Newsletter broadcast initiated."}

if __name__ == "__main__":
    agent = EmailMarketingAgent()
    agent.run()
