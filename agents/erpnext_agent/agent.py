import sys
import os
import logging
import json
import requests

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from core.db_connectors.db_manager import db_manager
from config.settings import config

logger = logging.getLogger(__name__)

class ERPNextAgent(BaseAgent):
    AGENT_ROLE = "erpnext_agent"
    SYSTEM_PROMPT = """You are an expert ERPNext Consultant Agent.
    Your domain is functional workflows in Frappe/ERPNext.
    
    You manage Sales Orders, Customers, Items, and Inventories.
    You have direct access to the ERPNext MySQL database for read/write.
    
    CRITICAL: You never guess a customer or item name. You query the database 
    to find matching records or create new ones properly."""

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

        conn = getattr(self, "conn", None) or db_manager.get_erpnext_mysql_connection()
        if not conn:
            return {"status": "error", "message": "ERPNext database connection failed"}

        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
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
        finally:
            if cursor:
                cursor.close()
            # Keep injected test connection alive.
            if conn and not (getattr(self, "conn", None) is conn):
                conn.close()

    def _create_sales_order(self, task_data):
        customer = task_data.get("task", {}).get("customer")
        items = task_data.get("task", {}).get("items", [])
        wc_order_id = task_data.get("task", {}).get("wc_order_id")

        if not customer:
            return {"status": "error", "message": "Customer is required"}
        if not isinstance(items, list) or not items:
            return {"status": "error", "message": "At least one line item is required"}
        if not (config.ERP_URL and config.ERP_API_KEY and config.ERP_API_SECRET):
            return {
                "status": "error",
                "message": "ERP REST credentials are not configured (ERP_URL, ERP_API_KEY, ERP_API_SECRET).",
            }

        erp_items = []
        for row in items:
            item_code = row.get("sku") or row.get("name") or row.get("id")
            qty = float(row.get("quantity", 1))
            rate = float(row.get("price", 0) or row.get("subtotal", 0) or 0)
            if not item_code:
                continue
            erp_items.append({"item_code": str(item_code), "qty": qty, "rate": rate})

        if not erp_items:
            return {"status": "error", "message": "No valid items could be mapped to ERP line items"}

        payload = {
            "customer": customer,
            "items": erp_items,
            "po_no": str(wc_order_id) if wc_order_id else None,
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        url = f"{config.ERP_URL.rstrip('/')}/api/resource/Sales Order"
        headers = {
            "Authorization": f"token {config.ERP_API_KEY}:{config.ERP_API_SECRET}",
            "Content-Type": "application/json",
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code >= 400:
                return {
                    "status": "error",
                    "message": f"ERP create Sales Order failed: HTTP {r.status_code} {r.text[:300]}",
                }
            data = r.json().get("data", {})
            so_name = data.get("name")
            self.log_execution(
                task=task_data,
                thought_process="Mapped incoming order to ERP Sales Order payload and called ERP REST API.",
                action_taken=f"Created Sales Order {so_name or 'unknown'} via {url}.",
            )
            return {"status": "success", "message": "Sales Order created", "sales_order": so_name, "response": data}
        except Exception as e:
            logger.error(f"Sales order create failed: {e}")
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    agent = ERPNextAgent()
    agent.run()
