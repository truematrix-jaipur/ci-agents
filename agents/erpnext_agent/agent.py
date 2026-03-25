import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from core.db_connectors.db_manager import db_manager

logger = logging.getLogger(__name__)

class ERPNextAgent(BaseAgent):
    AGENT_ROLE = "erpnext_agent"
    SYSTEM_PROMPT = """You are an expert ERPNext Consultant Agent.
    Your domain is functional workflows in Frappe/ERPNext.
    
    You manage Sales Orders, Customers, Items, and Inventories.
    You have direct access to the ERPNext MySQL database for read/write.
    
    CRITICAL: You never guess a customer or item name. You query the database 
    to find matching records or create new ones properly."""

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        self.conn = db_manager.get_erpnext_mysql_connection()

    def handle_task(self, task_data):
        logger.info(f"ERPNext Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "create_sales_order":
            return self._create_sales_order(task_data)
        elif task_type == "get_customer_id":
            return self._find_customer(task_data)
        else:
            return super().handle_task(task_data)

    def _find_customer(self, task_data):
        email = task_data.get("task", {}).get("email")
        if not email:
            return {"status": "error", "message": "Email required for lookup"}

        try:
            cursor = self.conn.cursor(dictionary=True)
            cursor.execute("SELECT name FROM `tabCustomer` WHERE email_id = %s", (email,))
            customer = cursor.fetchone()
            
            self.log_execution(
                task=task_data,
                thought_process=f"Looked up customer by email {email}",
                action_taken="DB Query SELECT executed on tabCustomer."
            )
            return {"status": "success", "customer_id": customer['name'] if customer else None}
        except Exception as e:
            logger.error(f"Customer lookup failed: {e}")
            return {"status": "error", "message": str(e)}

    def _create_sales_order(self, task_data):
        # Implementation for creating a new Sales Order record via DB or Frappe REST API
        # For simplicity in this demo, we'll return a simulated success
        return {"status": "success", "message": "Sales Order created simulation"}

if __name__ == "__main__":
    agent = ERPNextAgent()
    agent.run()
