import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from agents.server_agent.subagents.runtime_ops import RuntimeOpsSubagent

logger = logging.getLogger(__name__)

class DevOpsAgent(BaseAgent):
    AGENT_ROLE = "devops_agent"
    SYSTEM_PROMPT = """You are a compatibility DevOps agent.
    Runtime operations are consolidated with server_agent via RuntimeOpsSubagent.
    Maintain backward compatibility for existing devops task types while avoiding duplicated logic and duplicate ownership."""

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
        delegated_task_id = self.publish_task_to_agent("server_agent", {"type": "check_container_status"})
        result = self.spawn_subagent(RuntimeOpsSubagent, {"type": "check_container_status"})
        self.log_execution(
            task=task_data,
            thought_process="Delegated to canonical server_agent and executed shared RuntimeOpsSubagent for immediate compatibility response.",
            action_taken=f"Container status result: {result.get('status')}",
            status="success" if result.get("status") == "success" else "warning",
        )
        result["delegated_task_id"] = delegated_task_id
        result["routed_to"] = "server_agent"
        return result

    def _get_metrics(self, task_data):
        delegated_task_id = self.publish_task_to_agent("server_agent", {"type": "get_system_metrics"})
        result = self.spawn_subagent(RuntimeOpsSubagent, {"type": "get_system_metrics"})
        self.log_execution(
            task=task_data,
            thought_process="Delegated to canonical server_agent and executed shared RuntimeOpsSubagent for immediate compatibility response.",
            action_taken=f"System metrics result: {result.get('status')}",
            status="success" if result.get("status") == "success" else "warning",
        )
        result["delegated_task_id"] = delegated_task_id
        result["routed_to"] = "server_agent"
        return result

if __name__ == "__main__":
    agent = DevOpsAgent()
    agent.run()
