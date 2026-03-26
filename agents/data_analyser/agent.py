import sys
import os
import logging
import json

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from core.db_connectors.db_manager import db_manager

logger = logging.getLogger(__name__)

class DataAnalyserAgent(BaseAgent):
    AGENT_ROLE = "data_analyser"
    SYSTEM_PROMPT = """You are an expert Data Analyser Agent. 
    Your primary role is to execute complex queries against MySQL databases, parse JSON, 
    analyze statistical trends, and provide definitive, factual reports to other agents.
    
    CRITICAL: Never invent data. If a query returns empty, report that there is no data.
    You have direct read access to `mysql_db` and `erpnext_mysql_db`.
    """

    def handle_task(self, task_data):
        """Processes incoming requests from other agents."""
        logger.info(f"Data Analyser {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "query_db":
            return self._execute_query(task_data)
        elif task_type == "analyze_metrics":
            return self._analyze_metrics(task_data)
        else:
            return super().handle_task(task_data)

    def _execute_query(self, task_data):
        query = task_data.get("task", {}).get("query")
        params = task_data.get("task", {}).get("params", [])
        db_target = task_data.get("task", {}).get("database", "mysql")

        if not query:
            return {"status": "error", "message": "No query provided"}

        # Fetch a fresh connection per query to avoid using stale pool connections
        conn = (
            db_manager.get_erpnext_mysql_connection()
            if db_target == "erpnext"
            else db_manager.get_mysql_connection()
        )
        if not conn:
            return {"status": "error", "message": "Database connection failed"}

        try:
            normalized = query.strip().lower()
            if not normalized.startswith("select"):
                return {"status": "error", "message": "Only SELECT queries are allowed"}
            if ";" in query:
                return {"status": "error", "message": "Multi-statement SQL is blocked"}
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query, tuple(params) if params else None)
            results = cursor.fetchall()
            
            # Log execution for memory
            self.log_execution(
                task=task_data,
                thought_process=f"Executed parameterized query on {db_target}: {query}",
                action_taken=f"Fetched {len(results)} rows.",
                status="success"
            )
            
            # Send result back if requested via pubsub, or return if called directly
            return {"status": "success", "data": results}
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if conn:
                conn.close()

    def _analyze_metrics(self, task_data):
        raw_data = task_data.get("task", {}).get("data", [])
        prompt = f"Analyze the following JSON data and provide a summary of trends: {json.dumps(raw_data)}"
        
        # Use LLM to analyze the structured data
        analysis_result = self.execute_llm(prompt=prompt, provider="anthropic")
        
        self.log_execution(
            task=task_data,
            thought_process="Analyzed raw metrics using Claude 3.",
            action_taken="Generated textual analysis.",
            status="success"
        )
        
        return {"status": "success", "analysis": analysis_result}

if __name__ == "__main__":
    agent = DataAnalyserAgent()
    agent.run()
