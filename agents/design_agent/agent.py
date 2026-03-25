import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class DesignAgent(BaseAgent):
    AGENT_ROLE = "design_agent"
    SYSTEM_PROMPT = """You are a UI/UX and Graphic Design Agent.
    You create prompts for image generation, analyze website heatmaps, 
    and design conversion-optimized layouts.
    
    You do not assume what works for users. You always base your design 
    decisions on A/B test results from Google Analytics or Hotjar."""

    def handle_task(self, task_data):
        logger.info(f"Design Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "generate_image_prompt":
            return self._generate_prompt(task_data)
        else:
            return super().handle_task(task_data)

    def _generate_prompt(self, task_data):
        topic = task_data.get("task", {}).get("topic")
        # Use LLM to generate a high-quality Midjourney/DALL-E prompt
        prompt = self.execute_llm(f"Create a high-quality DALL-E prompt for {topic}", provider="openai")
        return {"status": "success", "design_prompt": prompt}

if __name__ == "__main__":
    agent = DesignAgent()
    agent.run()
