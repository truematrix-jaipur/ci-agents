import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class AgentBuilder(BaseAgent):
    AGENT_ROLE = "agent_builder"
    SYSTEM_PROMPT = """You are the Swarm Architect & Agent Builder.
    Your role is to dynamically create new specialized agents by 
    generating Python code based on a standard template.
    
    You follow the project's directory structure and ensure every 
    new agent inherits from the `BaseAgent` class and adheres to 
    the anti-hallucination policy."""

    def handle_task(self, task_data):
        logger.info(f"Agent Builder {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "build_new_agent":
            return self._build_agent(task_data)
        else:
            return super().handle_task(task_data)

    def _build_agent(self, task_data):
        agent_name = task_data.get("task", {}).get("name")
        description = task_data.get("task", {}).get("description")
        
        if not agent_name:
            return {"status": "error", "message": "Agent name required"}

        # Sanitization: Only alphanumeric and underscores
        import re
        agent_name = re.sub(r'[^a-zA-Z0-9_]', '', agent_name)
        
        if not agent_name:
            return {"status": "error", "message": "Invalid agent name"}

        # Define directory and file paths
        agent_dir = f"/home/agents/agents/{agent_name}"
        agent_file = f"{agent_dir}/agent.py"
        init_file = f"{agent_dir}/__init__.py"
        
        if os.path.exists(agent_dir):
            return {"status": "error", "message": f"Agent {agent_name} already exists"}

        # Use LLM to generate the implementation based on the template
        template_prompt = f"""Generate a Python implementation for a new agent named '{agent_name}'.
        Description: {description}
        
        Requirements:
        1. Inherit from `BaseAgent`.
        2. Define `AGENT_ROLE` as '{agent_name}'.
        3. Create a specialized `SYSTEM_PROMPT` including anti-hallucination rules.
        4. Implement `handle_task` with at least two relevant example methods.
        5. Use standard imports and path appends from the project.
        
        Output ONLY the raw Python code."""
        
        agent_code = self.execute_llm(template_prompt, provider="anthropic", use_knowledge=False)
        
        # Strip markdown code blocks if present
        if agent_code.startswith("```python"):
            agent_code = agent_code.split("```python")[1].split("```")[0].strip()
        elif agent_code.startswith("```"):
            agent_code = agent_code.split("```")[1].split("```")[0].strip()

        # Create directory and write file
        try:
            os.makedirs(agent_dir, exist_ok=True)
            with open(init_file, "w") as f:
                f.write("") # Create empty __init__.py
            
            with open(agent_file, "w") as f:
                f.write(agent_code)
            
            self.log_execution(
                task=task_data,
                thought_process=f"Generated code for {agent_name} using template.",
                action_taken=f"Created directory {agent_dir} and file {agent_file}."
            )
            
            return {
                "status": "success", 
                "message": f"New agent {agent_name} built successfully.",
                "path": agent_file
            }
        except Exception as e:
            logger.error(f"Agent building failed: {e}")
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    agent = AgentBuilder()
    agent.run()
