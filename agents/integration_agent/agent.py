import sys
import os
import logging
import json
import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from config.settings import config

logger = logging.getLogger(__name__)

class IntegrationAgent(BaseAgent):
    AGENT_ROLE = "integration_agent"
    SYSTEM_PROMPT = """You are an Integration Architect Agent.
    Your specialty is connecting external systems like WooCommerce to internal ERPNext systems.
    
    You do not assume data is synced. You always verify the latest record IDs from both systems.
    You have credentials for WooCommerce (Indogenmed) and ERPNext API access."""

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        self.wc_ck = os.getenv("WC_INDOGENMED_CK")
        self.wc_cs = os.getenv("WC_INDOGENMED_CS")
        self.wc_url = os.getenv("WC_URL")
        self._session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def handle_task(self, task_data):
        logger.info(f"Integration Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "sync_order_to_erpnext":
            return self._sync_order(task_data)
        elif task_type == "check_stock_levels":
            return self._check_stock(task_data)
        else:
            return super().handle_task(task_data)

    def _sync_order(self, task_data):
        order_id = task_data.get("task", {}).get("order_id")
        if not order_id:
             return {"status": "error", "message": "Order ID required"}
        if not (self.wc_url and self.wc_ck and self.wc_cs):
            return {"status": "error", "message": "WooCommerce credentials are not configured"}

        # Fetch from WooCommerce
        wc_api_url = f"{self.wc_url}/wp-json/wc/v3/orders/{order_id}"
        try:
            response = self._session.get(
                wc_api_url,
                auth=HTTPBasicAuth(self.wc_ck, self.wc_cs),
                timeout=20,
            )
            response.raise_for_status()
            wc_order = response.json()
            
            # Now, request ERPNext Agent to create the Sales Order
            erpnext_payload = {
                "type": "create_sales_order",
                "customer": wc_order.get("billing", {}).get("first_name"),
                "items": wc_order.get("line_items", []),
                "wc_order_id": order_id
            }
            self.publish_task_to_agent("erpnext_agent", erpnext_payload)
            
            self.log_execution(
                task=task_data,
                thought_process=f"Fetched order {order_id} from WC. Triggered ERPNext creation.",
                action_taken="WC API GET successful. PubSub sent to erpnext_agent."
            )
            return {"status": "success", "message": "Sync initiated", "wc_order_summary": wc_order.get("total")}
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return {"status": "error", "message": str(e)}

    def _check_stock(self, task_data):
        sku = task_data.get("task", {}).get("sku")
        if not sku:
            return {"status": "error", "message": "sku is required"}
        if not (self.wc_url and self.wc_ck and self.wc_cs):
            return {"status": "error", "message": "WooCommerce credentials are not configured"}
        try:
            wc_api_url = f"{self.wc_url}/wp-json/wc/v3/products"
            response = self._session.get(
                wc_api_url,
                auth=HTTPBasicAuth(self.wc_ck, self.wc_cs),
                params={"sku": sku, "per_page": 1},
                timeout=20,
            )
            response.raise_for_status()
            products = response.json() or []
            if not products:
                return {"status": "error", "message": f"No product found for sku={sku}"}
            product = products[0]
            return {
                "status": "success",
                "sku": sku,
                "wc_product_id": product.get("id"),
                "stock_quantity": product.get("stock_quantity"),
                "stock_status": product.get("stock_status"),
                "manage_stock": product.get("manage_stock"),
            }
        except Exception as e:
            logger.error(f"Stock check failed: {e}")
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    agent = IntegrationAgent()
    agent.run()
