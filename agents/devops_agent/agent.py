import sys
import os
import logging
import json
import subprocess

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class DevOpsAgent(BaseAgent):
    AGENT_ROLE = "devops_agent"
    SYSTEM_PROMPT = """You are an expert DevOps and Site Reliability Engineer Agent.
    You manage Docker environments, CI/CD pipelines, and server health.
    
    You have the capability to execute shell commands, check container logs, 
    and verify system resource usage.
    
    CRITICAL: Never execute destructive commands (like rm -rf /) unless explicitly 
    confirmed via a two-step validation. Always dry-run when possible."""

    def handle_task(self, task_data):
        logger.info(f"DevOps Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "check_container_status":
            return self._check_containers(task_data)
        elif task_type == "get_system_metrics":
            return self._get_metrics(task_data)
        else:
            return super().handle_task(task_data)

    def _check_containers(self, task_data):
        try:
            result = subprocess.run(["docker", "ps", "--format", "{{.Names}}: {{.Status}}"], capture_output=True, text=True)
            self.log_execution(
                task=task_data,
                thought_process="Checked Docker container statuses using subprocess.",
                action_taken="Executed docker ps command."
            )
            return {"status": "success", "containers": result.stdout.strip().split("\n")}
        except Exception as e:
            logger.error(f"Docker check failed: {e}")
            return {"status": "error", "message": str(e)}

    def _get_metrics(self, task_data):
        try:
            load = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
            mem = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.strip()
            return {"status": "success", "load": load, "memory": mem}
        except Exception as e:
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    agent = DevOpsAgent()
    agent.run()
