import sys
import os
import logging
import json
from datetime import datetime, timezone

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
        elif task_type == "get_growth_plan_status":
            return self._get_growth_plan_status(task_data)
        else:
            return super().handle_task(task_data)

    def _plan_growth(self, task_data):
        task = task_data.get("task", {})
        execution_mode = str(task.get("execution_mode", "sync")).lower()
        window_days = int(task.get("window_days", 90))
        total_budget = float(task.get("total_budget", 8000))
        campaign_name = task.get("campaign_name", f"growth_{datetime.now(timezone.utc).strftime('%Y%m%d')}")

        if execution_mode not in {"sync", "async"}:
            return {"status": "error", "message": "execution_mode must be sync or async"}
        if window_days <= 0 or window_days > 365:
            return {"status": "error", "message": "window_days must be between 1 and 365"}
        if total_budget <= 0:
            return {"status": "error", "message": "total_budget must be > 0"}

        if execution_mode == "sync":
            return self._run_closed_loop_sync(
                task_data=task_data,
                window_days=window_days,
                total_budget=total_budget,
                campaign_name=campaign_name,
            )
        return self._run_closed_loop_async(
            task_data=task_data,
            window_days=window_days,
            total_budget=total_budget,
            campaign_name=campaign_name,
        )

    def _run_closed_loop_sync(self, task_data, window_days: int, total_budget: float, campaign_name: str):
        inputs = self._collect_inputs_sync(window_days)
        if inputs.get("status") != "success":
            return inputs

        diagnosis = self._build_diagnosis(inputs)
        plan = self._build_action_plan(diagnosis=diagnosis, total_budget=total_budget, campaign_name=campaign_name)
        execution = self._execute_plan_sync(plan)

        self.log_execution(
            task=task_data,
            thought_process="Collected market signals, built growth diagnosis, and executed a closed-loop plan.",
            action_taken=f"Closed-loop growth plan executed in sync mode for campaign {campaign_name}.",
            status="success" if execution.get("status") == "success" else "warning",
        )
        return {
            "status": execution.get("status", "success"),
            "mode": "sync",
            "campaign_name": campaign_name,
            "inputs": inputs,
            "diagnosis": diagnosis,
            "plan": plan,
            "execution": execution,
        }

    def _run_closed_loop_async(self, task_data, window_days: int, total_budget: float, campaign_name: str):
        # Async closed-loop executes by dispatching all required steps with traceable task IDs.
        dispatched = {
            "sales_data_task_id": self.publish_task_to_agent(
                "data_analyser",
                {"type": "summarize_sales_trend", "days": window_days, "database": "erpnext"},
            ),
            "ga4_summary_task_id": self.publish_task_to_agent("seo_agent", {"type": "get_ga4_summary"}),
            "campaign_plan_task_id": self.publish_task_to_agent(
                "campaign_planner_agent",
                {
                    "type": "plan_campaign",
                    "campaign_name": campaign_name,
                    "google_budget": round(total_budget * 0.6, 2),
                    "fb_budget": round(total_budget * 0.4, 2),
                },
            ),
            "seo_pipeline_task_id": self.publish_task_to_agent("seo_agent", {"type": "run_autonomous_pipeline"}),
        }

        self.log_execution(
            task=task_data,
            thought_process="Queued async closed-loop growth plan tasks for collection and execution.",
            action_taken=f"Dispatched async closed-loop tasks for campaign {campaign_name}.",
            status="success",
        )
        return {
            "status": "success",
            "mode": "async",
            "campaign_name": campaign_name,
            "window_days": window_days,
            "total_budget": total_budget,
            "dispatched": dispatched,
            "message": "Async closed-loop growth workflow dispatched.",
        }

    def _collect_inputs_sync(self, window_days: int):
        from agents.data_analyser.agent import DataAnalyserAgent
        from agents.seo_agent.agent import SEOAgent

        sales = self.spawn_subagent(
            DataAnalyserAgent, {"task": {"type": "summarize_sales_trend", "days": window_days, "database": "erpnext"}}
        )
        ga4 = self.spawn_subagent(SEOAgent, {"task": {"type": "get_ga4_summary"}})

        if not isinstance(sales, dict) or sales.get("status") != "success":
            return {"status": "error", "message": "Failed to collect sales trend input", "sales": sales, "ga4": ga4}
        if not isinstance(ga4, dict) or ga4.get("status") != "success":
            return {"status": "error", "message": "Failed to collect GA4 input", "sales": sales, "ga4": ga4}

        return {"status": "success", "sales": sales, "ga4": ga4}

    def _build_diagnosis(self, inputs: dict):
        sales = inputs.get("sales", {})
        ga4 = inputs.get("ga4", {})

        trend = sales.get("trend", {})
        pct_change = float(trend.get("percent_change", 0.0))
        revenue_direction = trend.get("direction", "flat")

        ga4_payload = ga4.get("ga4", {}) if isinstance(ga4, dict) else {}
        sessions = self._pick_number(ga4_payload, ["sessions", "total_sessions", "users", "total_users"], 0.0)
        conversions = self._pick_number(
            ga4_payload, ["conversions", "total_conversions", "transactions", "purchase_count"], 0.0
        )
        conversion_rate = round((conversions / sessions) * 100.0, 3) if sessions > 0 else 0.0

        health = "healthy"
        if pct_change < -10 or conversion_rate < 1.0:
            health = "at_risk"
        if pct_change < -20 or conversion_rate < 0.5:
            health = "critical"

        return {
            "revenue_trend_percent": pct_change,
            "revenue_direction": revenue_direction,
            "sessions": sessions,
            "conversions": conversions,
            "conversion_rate_percent": conversion_rate,
            "health": health,
        }

    def _build_action_plan(self, diagnosis: dict, total_budget: float, campaign_name: str):
        health = diagnosis.get("health", "healthy")
        revenue_direction = diagnosis.get("revenue_direction", "flat")

        if health == "critical":
            google_share = 0.45
            fb_share = 0.55
            priority = "recovery"
        elif health == "at_risk" or revenue_direction == "down":
            google_share = 0.55
            fb_share = 0.45
            priority = "stabilize"
        else:
            google_share = 0.65
            fb_share = 0.35
            priority = "scale"

        google_budget = round(total_budget * google_share, 2)
        fb_budget = round(total_budget * fb_share, 2)
        return {
            "priority": priority,
            "campaign_name": campaign_name,
            "budget": {
                "total_budget": round(total_budget, 2),
                "google_budget": google_budget,
                "fb_budget": fb_budget,
            },
            "actions": [
                {"owner": "campaign_planner_agent", "task": "plan_campaign"},
                {"owner": "seo_agent", "task": "run_autonomous_pipeline"},
                {"owner": "fb_campaign_manager", "task": "optimize_bidding"},
            ],
        }

    def _execute_plan_sync(self, plan: dict):
        from agents.campaign_planner_agent.agent import CampaignPlannerAgent
        from agents.seo_agent.agent import SEOAgent
        from agents.fb_campaign_manager.agent import FBCampaignManagerAgent

        budget = plan.get("budget", {})
        campaign_name = plan.get("campaign_name")
        campaign_res = self.spawn_subagent(
            CampaignPlannerAgent,
            {
                "task": {
                    "type": "plan_campaign",
                    "campaign_name": campaign_name,
                    "google_budget": budget.get("google_budget"),
                    "fb_budget": budget.get("fb_budget"),
                }
            },
        )
        seo_res = self.spawn_subagent(SEOAgent, {"task": {"type": "run_autonomous_pipeline"}})
        fb_res = self.spawn_subagent(
            FBCampaignManagerAgent,
            {"task": {"type": "optimize_bidding", "campaign_id": campaign_name}},
        )

        ok = all(isinstance(r, dict) and r.get("status") == "success" for r in [campaign_res, seo_res, fb_res])
        return {
            "status": "success" if ok else "warning",
            "results": {
                "campaign_planner_agent": campaign_res,
                "seo_agent": seo_res,
                "fb_campaign_manager": fb_res,
            },
        }

    def _get_growth_plan_status(self, task_data):
        # Lightweight status endpoint for dashboard/manual checks.
        return {
            "status": "success",
            "message": "Use plan_quarterly_growth with execution_mode=sync for immediate closed-loop execution or async for dispatched workflow.",
        }

    @staticmethod
    def _pick_number(payload: dict, keys: list[str], default: float) -> float:
        if not isinstance(payload, dict):
            return default
        for key in keys:
            if key in payload:
                try:
                    return float(payload[key] or 0.0)
                except Exception:
                    continue
        # Scan nested dicts for common keys.
        for value in payload.values():
            if isinstance(value, dict):
                for key in keys:
                    if key in value:
                        try:
                            return float(value[key] or 0.0)
                        except Exception:
                            continue
        return default

if __name__ == "__main__":
    agent = GrowthAgent()
    agent.run()
