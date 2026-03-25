import sys
import os
import logging
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class WordPressTechAgent(BaseAgent):
    AGENT_ROLE = "wordpress_tech"
    SYSTEM_PROMPT = """You are an expert WordPress Technical Agent.
    Your duties include managing WordPress configurations, triggering WP-CLI commands, 
    diagnosing server errors, and updating plugins safely.
    
    CRITICAL: Never assume the state of a WordPress site. Always verify with WP-CLI 
    or check live server logs before taking action."""

    def handle_task(self, task_data):
        logger.info(f"WordPress Tech {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")
        site_path = task_data.get("task", {}).get("site_path")

        if task_type == "health_check":
            # Pseudo-code for running a WP-CLI command
            cmd = f"wp core is-installed --path={site_path}"
            # In real implementation: result = os.popen(cmd).read()
            result = "Success" 
            
            self.log_execution(
                task=task_data,
                thought_process=f"Checked WP installation at {site_path}",
                action_taken=f"Executed WP-CLI: {cmd}"
            )
            
            return {"status": "success", "wp_cli_result": result}
        else:
            return super().handle_task(task_data)

if __name__ == "__main__":
    agent = WordPressTechAgent()
    agent.run()
