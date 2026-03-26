import sys
import os
import logging
import json
from datetime import datetime, timedelta, timezone

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
        elif task_type == "summarize_sales_trend":
            return self._summarize_sales_trend(task_data)
        elif task_type == "autonomous_sales_monitor":
            return self._autonomous_sales_monitor(task_data)
        elif task_type == "manual_command":
            return self._handle_manual_command(task_data)
        else:
            return super().handle_task(task_data)

    def _execute_query(self, task_data):
        query = task_data.get("task", {}).get("query")
        params = task_data.get("task", {}).get("params", [])
        db_target = task_data.get("task", {}).get("database", "mysql")

        if not query:
            return {"status": "error", "message": "No query provided"}

        # Fetch a fresh connection per query to avoid using stale pool connections
        # Allow injected connections for tests or controlled one-off execution.
        if db_target == "erpnext" and getattr(self, "erpnext_conn", None):
            conn = self.erpnext_conn
        elif db_target != "erpnext" and getattr(self, "mysql_conn", None):
            conn = self.mysql_conn
        else:
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
            # Do not close injected test connections.
            if conn and not (
                (db_target == "erpnext" and getattr(self, "erpnext_conn", None) is conn)
                or (db_target != "erpnext" and getattr(self, "mysql_conn", None) is conn)
            ):
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

    def _handle_manual_command(self, task_data):
        """
        Manual chat commands are routed here so dashboard users can trigger deterministic
        DB-backed actions instead of free-form LLM output.
        """
        command = (task_data.get("task", {}).get("command") or "").strip()
        if not command:
            return {"status": "error", "message": "No command provided"}

        lowered = command.lower()

        # Quick deterministic shortcuts for common dashboard asks.
        if "sales trend" in lowered and "last" in lowered and "day" in lowered:
            days = self._extract_days_from_text(lowered) or 7
            return self._summarize_sales_trend(
                {"task": {"days": days, "database": "erpnext", "source": "manual_command"}}
            )

        # Manual SQL escape hatch for operators:
        # - /query <sql>                  (defaults to mysql)
        # - /query erpnext <sql>
        if lowered.startswith("/query "):
            raw = command[len("/query ") :].strip()
            db_target = "mysql"
            sql = raw
            if raw.lower().startswith("erpnext "):
                db_target = "erpnext"
                sql = raw.split(" ", 1)[1].strip() if " " in raw else ""
            elif raw.lower().startswith("mysql "):
                db_target = "mysql"
                sql = raw.split(" ", 1)[1].strip() if " " in raw else ""
            return self._execute_query({"task": {"type": "query_db", "database": db_target, "query": sql}})

        return {
            "status": "error",
            "message": (
                "Unsupported manual command. Use natural-language 'sales trend' requests or "
                "`/query [mysql|erpnext] <SELECT ...>`."
            ),
        }

    def _extract_days_from_text(self, text: str) -> int | None:
        tokens = text.split()
        for idx, tok in enumerate(tokens):
            if tok.isdigit():
                # Prefer numbers near day/days keywords.
                nearby = tokens[idx + 1] if idx + 1 < len(tokens) else ""
                if nearby.startswith("day"):
                    return int(tok)
        return None

    def _summarize_sales_trend(self, task_data):
        task = task_data.get("task", {})
        days = int(task.get("days", 7))
        if days <= 0 or days > 90:
            return {"status": "error", "message": "days must be between 1 and 90"}

        db_target = task.get("database", "erpnext")
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days - 1)
        query = """
            SELECT
              DATE(creation) AS day,
              COUNT(*) AS orders,
              COALESCE(SUM(grand_total), 0) AS revenue
            FROM `tabSales Order`
            WHERE creation >= %s
              AND docstatus < 2
            GROUP BY DATE(creation)
            ORDER BY day ASC
        """
        result = self._execute_query(
            {
                "task": {
                    "type": "query_db",
                    "database": db_target,
                    "query": query,
                    "params": [start_date.isoformat()],
                }
            }
        )
        if result.get("status") != "success":
            return result

        rows = result.get("data", []) or []
        by_day = {}
        for row in rows:
            raw_day = row.get("day")
            day = str(raw_day)[:10]
            by_day[day] = {
                "day": day,
                "orders": int(row.get("orders") or 0),
                "revenue": float(row.get("revenue") or 0.0),
            }

        timeline = []
        cur = start_date
        while cur <= end_date:
            key = cur.isoformat()
            timeline.append(by_day.get(key, {"day": key, "orders": 0, "revenue": 0.0}))
            cur += timedelta(days=1)

        total_revenue = round(sum(x["revenue"] for x in timeline), 2)
        total_orders = sum(x["orders"] for x in timeline)
        avg_daily_revenue = round(total_revenue / days, 2)

        # Split into two halves for trend direction.
        mid = max(1, days // 2)
        first_half = timeline[:mid]
        second_half = timeline[mid:]
        first_avg = (sum(x["revenue"] for x in first_half) / len(first_half)) if first_half else 0.0
        second_avg = (sum(x["revenue"] for x in second_half) / len(second_half)) if second_half else first_avg
        if first_avg == 0:
            pct_change = 0.0 if second_avg == 0 else 100.0
        else:
            pct_change = round(((second_avg - first_avg) / first_avg) * 100.0, 2)
        trend = "up" if pct_change > 2 else "down" if pct_change < -2 else "flat"

        summary = (
            f"Sales trend ({days}d): revenue={total_revenue}, orders={total_orders}, "
            f"avg_daily_revenue={avg_daily_revenue}, trend={trend} ({pct_change}%)."
        )

        self.log_execution(
            task=task_data,
            thought_process=f"Built {days}-day sales trend summary from ERPNext data.",
            action_taken=summary,
            status="success",
        )
        return {
            "status": "success",
            "summary": summary,
            "window_days": days,
            "trend": {
                "direction": trend,
                "percent_change": pct_change,
                "avg_revenue_first_half": round(first_avg, 2),
                "avg_revenue_second_half": round(second_avg, 2),
            },
            "totals": {
                "revenue": total_revenue,
                "orders": total_orders,
                "avg_daily_revenue": avg_daily_revenue,
            },
            "timeline": timeline,
        }

    def _autonomous_sales_monitor(self, task_data):
        task = task_data.get("task", {})
        days = int(task.get("days", 7))
        drop_alert_pct = float(task.get("drop_alert_pct", -20.0))
        auto_delegate = bool(task.get("auto_delegate", False))

        summary = self._summarize_sales_trend({"task": {"days": days, "database": "erpnext"}})
        if summary.get("status") != "success":
            return summary

        pct_change = float(summary.get("trend", {}).get("percent_change", 0.0))
        needs_alert = pct_change <= drop_alert_pct
        delegated_task_id = None
        if needs_alert and auto_delegate:
            delegated_task_id = self.publish_task_to_agent(
                "growth_agent",
                {
                    "type": "plan_quarterly_growth",
                    "context": "autonomous_sales_monitor_alert",
                    "sales_trend": summary.get("trend"),
                    "totals": summary.get("totals"),
                    "window_days": days,
                },
            )

        return {
            "status": "success",
            "mode": "autonomous_sales_monitor",
            "alert_triggered": needs_alert,
            "drop_alert_pct": drop_alert_pct,
            "delegated_task_id": delegated_task_id,
            "summary": summary,
        }

if __name__ == "__main__":
    agent = DataAnalyserAgent()
    agent.run()
