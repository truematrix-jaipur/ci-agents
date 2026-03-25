import sys
import os
import logging
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from agents.seo_agent.subagents.speed_optimizer import SpeedOptimizerAgent

logger = logging.getLogger(__name__)

class SEOAgent(BaseAgent):
    AGENT_ROLE = "seo_agent"
    SYSTEM_PROMPT = """You are the master SEO Orchestrator.
    You manage the SEO health of connected websites. 
    You do not hallucinate data. You must spawn subagents (like speed optimizer) 
    or request data from the Data Analyser agent to gather factual insights."""

    def handle_task(self, task_data):
        logger.info(f"SEO Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")
        target_url = task_data.get("task", {}).get("url")

        if task_type == "full_audit":
            # 1. Spawn Speed Optimizer subagent
            speed_report = self.spawn_subagent(SpeedOptimizerAgent, {"url": target_url})
            
            # 2. Ask Data Analyser for traffic data via PubSub
            # (In a real implementation, this would involve waiting for a callback/async response)
            data_req_payload = {
                "type": "query_db",
                "database": "mysql",
                "query": "SELECT page_views FROM traffic_stats WHERE url = %s",
                "params": [target_url],
            }
            self.publish_task_to_agent("data_analyser", data_req_payload)
            
            # 3. Compile report
            audit_report = {
                "status": "success",
                "target_url": target_url,
                "speed_metrics": speed_report.get("metrics", {}),
                "speed_recommendations": speed_report.get("recommendations", [])
            }
            
            self.log_execution(
                task=task_data,
                thought_process="Spawned SpeedOptimizer. Published to Data Analyser.",
                action_taken="Generated partial audit report pending traffic data."
            )
            return audit_report
        else:
            return super().handle_task(task_data)

if __name__ == "__main__":
    agent = SEOAgent()
    agent.run()
