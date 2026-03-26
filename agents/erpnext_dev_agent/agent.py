import sys
import os
import logging
import json
import requests

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from config.settings import config

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
        module = task_data.get("task", {}).get("module", "Custom")
        if not doctype_name:
            return {"status": "error", "message": "name is required"}
        if not (config.ERP_URL and config.ERP_API_KEY and config.ERP_API_SECRET):
            return {"status": "error", "message": "ERP REST credentials are not configured"}
        url = f"{config.ERP_URL.rstrip('/')}/api/resource/DocType"
        headers = {
            "Authorization": f"token {config.ERP_API_KEY}:{config.ERP_API_SECRET}",
            "Content-Type": "application/json",
        }
        payload = {
            "name": doctype_name,
            "module": module,
            "custom": 1,
            "istable": 0,
            "track_changes": 1,
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code >= 400:
                return {"status": "error", "message": f"DocType create failed: HTTP {r.status_code} {r.text[:300]}"}
            data = r.json().get("data", {})
            return {"status": "success", "message": f"DocType {doctype_name} created.", "doctype": data.get("name")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _apply_fix(self, task_data):
        service = task_data.get("task", {}).get("service")
        if not service:
            return {"status": "error", "message": "service is required"}
        # Delegate restart/fix action to server_agent so operations stay centralized.
        delegated_id = self.publish_task_to_agent("server_agent", {"type": "fix_service", "service": service})
        return {"status": "success", "message": f"Fix delegated for service {service}.", "delegated_task_id": delegated_id}

if __name__ == "__main__":
    agent = ERPNextDevAgent()
    agent.run()
