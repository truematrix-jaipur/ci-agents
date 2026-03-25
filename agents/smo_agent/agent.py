import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class SMOResponsiveAgent(BaseAgent):
    AGENT_ROLE = "smo_agent"
    SYSTEM_PROMPT = """You are a Social Media Optimization Agent.
    You manage social media posting schedules and analyze performance 
    on Meta, Instagram, and LinkedIn.
    
    You do not assume engagement metrics. You always fetch them from 
    platform APIs or reports."""

    def handle_task(self, task_data):
        logger.info(f"SMO Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "post_update":
            return self._post_update(task_data)
        else:
            return super().handle_task(task_data)

    def _post_update(self, task_data):
        platform = task_data.get("task", {}).get("platform")
        content = task_data.get("task", {}).get("content")
        return {"status": "success", "message": f"Posted to {platform}: {content[:20]}..."}

if __name__ == "__main__":
    agent = SMOResponsiveAgent()
    agent.run()
