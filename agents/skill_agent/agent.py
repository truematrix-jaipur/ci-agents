import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class SkillAgent(BaseAgent):
    AGENT_ROLE = "skill_agent"
    SYSTEM_PROMPT = """You are the Knowledge & Skill Acquisition Agent.
    Your mission is to continuously find the best documentations, processes, 
    and best practices from the internet or other AI models.
    
    You process this raw knowledge into structured data and send it to the 
    Training Agent to enhance the capabilities of other agents in the swarm."""

    def handle_task(self, task_data):
        logger.info(f"Skill Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "fetch_best_practices":
            return self._fetch_best_practices(task_data)
        elif task_type == "fetch_documentation":
            return self._fetch_documentation(task_data)
        else:
            return super().handle_task(task_data)

    def _fetch_best_practices(self, task_data):
        topic = task_data.get("task", {}).get("topic")
        target_agent = task_data.get("task", {}).get("target_agent")
        
        # Use LLM to simulate web browsing and best practice extraction
        prompt = f"Research and provide a detailed list of current best practices for: {topic}. Output as structured technical guidelines."
        knowledge_content = self.execute_llm(prompt, provider="gemini", use_knowledge=False)
        
        # Dispatch to Training Agent
        training_payload = {
            "type": "train_agent",
            "target_agent": target_agent,
            "knowledge_content": knowledge_content,
            "source": f"LLM Research on {topic}"
        }
        self.publish_task_to_agent("training_agent", training_payload)
        
        self.log_execution(
            task=task_data,
            thought_process=f"Researched best practices for {topic} using Gemini.",
            action_taken=f"Dispatched training data for {target_agent} to Training Agent."
        )
        return {"status": "success", "message": f"Knowledge for {topic} sent to Training Agent."}

    def _fetch_documentation(self, task_data):
        tool_name = task_data.get("task", {}).get("tool")
        target_agent = task_data.get("task", {}).get("target_agent")
        
        prompt = f"Provide a comprehensive technical guide and command reference for: {tool_name}. Include common troubleshooting steps."
        knowledge_content = self.execute_llm(prompt, provider="anthropic", use_knowledge=False)
        
        training_payload = {
            "type": "train_agent",
            "target_agent": target_agent,
            "knowledge_content": knowledge_content,
            "source": f"Documentation fetch for {tool_name}"
        }
        self.publish_task_to_agent("training_agent", training_payload)
        
        return {"status": "success", "message": f"Documentation for {tool_name} sent to Training Agent."}

if __name__ == "__main__":
    agent = SkillAgent()
    agent.run()
