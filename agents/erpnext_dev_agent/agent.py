import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from core.db_connectors.db_manager import db_manager

logger = logging.getLogger(__name__)

class ERPNextDevAgent(BaseAgent):
    AGENT_ROLE = "erpnext_dev_agent"
    SYSTEM_PROMPT = """You are an expert Frappe/ERPNext Developer Agent.
    You create DocTypes, write Server Scripts, and develop custom apps 
    within the Frappe framework.
    
    You follow the bench command standards and write clean, maintainable 
    Python and JS code."""

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        # Developers often need direct access to the app paths
        self.bench_path = "/home/erpnext/frappe_docker"

    def handle_task(self, task_data):
        logger.info(f"ERPNext Dev Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "create_doctype":
            return self._create_doctype(task_data)
        elif task_type == "apply_fix":
            return self._apply_fix(task_data)
        else:
            return super().handle_task(task_data)

    def _create_doctype(self, task_data):
        doctype_name = task_data.get("task", {}).get("name")
        return {"status": "success", "message": f"DocType {doctype_name} created (simulated)."}

    def _apply_fix(self, task_data):
        return {"status": "success", "message": "Code fix applied (simulated)."}

if __name__ == "__main__":
    agent = ERPNextDevAgent()
    agent.run()
