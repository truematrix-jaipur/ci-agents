import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class GrowthAgent(BaseAgent):
    AGENT_ROLE = "growth_agent"
    SYSTEM_PROMPT = """You are the Chief Growth Officer Agent.
    You orchestrate the strategy across all marketing agents: SEO, Smo, 
    FB Campaign, and Google.
    
    You do not execute low-level tasks yourself. You analyze data reports from 
    the Data Analyser and delegate work to specific agents.
    
    CRITICAL: Base all strategic decisions on actual conversion data from 
    ERPNext and GA4."""

    def handle_task(self, task_data):
        logger.info(f"Growth Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "plan_quarterly_growth":
            return self._plan_growth(task_data)
        else:
            return super().handle_task(task_data)

    def _plan_growth(self, task_data):
        # 1. Ask Data Analyser for sales data
        self.publish_task_to_agent("data_analyser", {"type": "query_db", "database": "erpnext", "query": "SELECT SUM(grand_total) FROM `tabSales Order` WHERE creation > DATE_SUB(NOW(), INTERVAL 3 MONTH)"})
        
        # 2. Ask Google Agent for traffic data
        self.publish_task_to_agent("google_agent", {"type": "get_ga4_conversions"})
        
        self.log_execution(
            task=task_data,
            thought_process="Requested sales and traffic data from respective agents.",
            action_taken="Strategy planning initiated. Data requests dispatched via PubSub."
        )
        return {"status": "success", "message": "Growth strategy data collection initiated."}

if __name__ == "__main__":
    agent = GrowthAgent()
    agent.run()
