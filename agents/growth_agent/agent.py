import sys
import os
import logging
import json
import csv
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import requests

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
    ERPNext, GA4, GSC, and validated external/custom reports."""

    def handle_task(self, task_data):
        logger.info(f"Growth Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "plan_quarterly_growth":
            return self._execute_with_goal_target(task_data, self._plan_growth, "plan_quarterly_growth")
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
        include_sources = task.get(
            "include_sources",
            ["erpnext_sales", "ga4", "gsc", "wordpress", "external_reports", "semrush", "ahrefs"],
        )

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
                include_sources=include_sources,
            )
        return self._run_closed_loop_async(
            task_data=task_data,
            window_days=window_days,
            total_budget=total_budget,
            campaign_name=campaign_name,
            include_sources=include_sources,
        )

    def _run_closed_loop_sync(
        self, task_data, window_days: int, total_budget: float, campaign_name: str, include_sources: list[str]
    ):
        inputs = self._collect_inputs_sync(window_days, task_data.get("task", {}), include_sources)
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

    def _run_closed_loop_async(
        self, task_data, window_days: int, total_budget: float, campaign_name: str, include_sources: list[str]
    ):
        # Async closed-loop executes by dispatching all required steps with traceable task IDs.
        dispatched: dict[str, Any] = {}
        if "erpnext_sales" in include_sources:
            dispatched["sales_data_task_id"] = self.publish_task_to_agent(
                "data_analyser",
                {"type": "summarize_sales_trend", "days": window_days, "database": "erpnext"},
            )
        if "ga4" in include_sources:
            dispatched["ga4_summary_task_id"] = self.publish_task_to_agent("seo_agent", {"type": "get_ga4_summary"})
        if "gsc" in include_sources:
            dispatched["gsc_performance_task_id"] = self.publish_task_to_agent(
                "google_agent", {"type": "get_gsc_performance", "days": window_days}
            )
        if "wordpress" in include_sources and task_data.get("task", {}).get("wordpress_site_path"):
            dispatched["wordpress_health_task_id"] = self.publish_task_to_agent(
                "wordpress_tech",
                {"type": "health_check", "site_path": task_data.get("task", {}).get("wordpress_site_path")},
            )

        dispatched["campaign_plan_task_id"] = self.publish_task_to_agent(
            "campaign_planner_agent",
            {
                "type": "plan_campaign",
                "campaign_name": campaign_name,
                "google_budget": round(total_budget * 0.6, 2),
                "fb_budget": round(total_budget * 0.4, 2),
            },
        )
        dispatched["seo_pipeline_task_id"] = self.publish_task_to_agent("seo_agent", {"type": "run_autonomous_pipeline"})

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
            "include_sources": include_sources,
            "dispatched": dispatched,
            "message": "Async closed-loop growth workflow dispatched.",
        }

    def _collect_inputs_sync(self, window_days: int, task: dict, include_sources: list[str]):
        from agents.data_analyser.agent import DataAnalyserAgent
        from agents.seo_agent.agent import SEOAgent

        inputs: dict[str, Any] = {"status": "success"}

        if "erpnext_sales" in include_sources:
            sales = self.spawn_subagent(
                DataAnalyserAgent, {"task": {"type": "summarize_sales_trend", "days": window_days, "database": "erpnext"}}
            )
            inputs["sales"] = sales
            if not isinstance(sales, dict) or sales.get("status") != "success":
                return {"status": "error", "message": "Failed to collect sales trend input", **inputs}

        if "ga4" in include_sources:
            ga4 = self.spawn_subagent(SEOAgent, {"task": {"type": "get_ga4_summary"}})
            inputs["ga4"] = ga4
            if not isinstance(ga4, dict) or ga4.get("status") != "success":
                return {"status": "error", "message": "Failed to collect GA4 input", **inputs}

        if "gsc" in include_sources:
            try:
                from agents.google_agent.agent import GoogleAgent

                gsc = self.spawn_subagent(GoogleAgent, {"task": {"type": "get_gsc_performance", "days": window_days}})
                inputs["gsc"] = gsc
            except Exception as e:
                inputs["gsc"] = {"status": "error", "message": f"GSC signal unavailable: {e}"}
            # GSC is optional signal in loop; do not hard fail.

        if "wordpress" in include_sources and task.get("wordpress_site_path"):
            try:
                from agents.wordpress_tech.agent import WordPressTechAgent

                wp_health = self.spawn_subagent(
                    WordPressTechAgent, {"task": {"type": "health_check", "site_path": task.get("wordpress_site_path")}}
                )
                inputs["wordpress"] = wp_health
            except Exception as e:
                inputs["wordpress"] = {"status": "error", "message": f"WordPress signal unavailable: {e}"}

        external = self._collect_external_sources(task)
        if external:
            inputs["external"] = external

        return inputs

    def _build_diagnosis(self, inputs: dict):
        sales = inputs.get("sales", {})
        ga4 = inputs.get("ga4", {})
        gsc = inputs.get("gsc", {})
        external = inputs.get("external", {})

        trend = sales.get("trend", {})
        pct_change = float(trend.get("percent_change", 0.0))
        revenue_direction = trend.get("direction", "flat")

        ga4_payload = ga4.get("ga4", {}) if isinstance(ga4, dict) else {}
        sessions = self._pick_number(ga4_payload, ["sessions", "total_sessions", "users", "total_users"], 0.0)
        conversions = self._pick_number(
            ga4_payload, ["conversions", "total_conversions", "transactions", "purchase_count"], 0.0
        )
        conversion_rate = round((conversions / sessions) * 100.0, 3) if sessions > 0 else 0.0
        gsc_clicks = self._pick_number(gsc, ["clicks", "total_clicks"], 0.0)
        gsc_impressions = self._pick_number(gsc, ["impressions", "total_impressions"], 0.0)
        keyword_signals = self._extract_keyword_signals(gsc=gsc, external=external)

        health = "healthy"
        if pct_change < -10 or conversion_rate < 1.0:
            health = "at_risk"
        if pct_change < -20 or conversion_rate < 0.5:
            health = "critical"
        if keyword_signals.get("top_keywords_count", 0) < 5 and health == "healthy":
            health = "at_risk"

        return {
            "revenue_trend_percent": pct_change,
            "revenue_direction": revenue_direction,
            "sessions": sessions,
            "conversions": conversions,
            "conversion_rate_percent": conversion_rate,
            "gsc_clicks": gsc_clicks,
            "gsc_impressions": gsc_impressions,
            "keyword_signals": keyword_signals,
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
                {"owner": "seo_agent", "task": "run_extended"},
                {"owner": "fb_campaign_manager", "task": "optimize_bidding"},
                {"owner": "skill_agent", "task": "fetch_best_practices"},
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
        seo_extended_res = self.spawn_subagent(SEOAgent, {"task": {"type": "run_extended"}})
        fb_res = self.spawn_subagent(
            FBCampaignManagerAgent,
            {"task": {"type": "optimize_bidding", "campaign_id": campaign_name}},
        )
        from agents.skill_agent.agent import SkillAgent

        keyword_topic = "high-intent SEO keywords from GA4/GSC and external SEO tools"
        skill_res = self.spawn_subagent(
            SkillAgent,
            {"task": {"type": "fetch_best_practices", "topic": keyword_topic, "target_agent": "seo_agent"}},
        )

        ok = all(
            isinstance(r, dict) and r.get("status") == "success"
            for r in [campaign_res, seo_res, seo_extended_res, fb_res, skill_res]
        )
        return {
            "status": "success" if ok else "warning",
            "results": {
                "campaign_planner_agent": campaign_res,
                "seo_agent": seo_res,
                "seo_extended": seo_extended_res,
                "fb_campaign_manager": fb_res,
                "skill_agent": skill_res,
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

    def _collect_external_sources(self, task: dict) -> dict[str, Any]:
        out: dict[str, Any] = {"reports": [], "errors": []}

        # 1) Optional SEMrush/Ahref API pull (user-supplied endpoint+token)
        for source_name in ["semrush", "ahrefs"]:
            endpoint = task.get(f"{source_name}_api_url")
            token = task.get(f"{source_name}_api_token")
            if endpoint and token:
                try:
                    data = self._fetch_external_api(endpoint, token)
                    out["reports"].append({"source": source_name, "format": "json", "data": data})
                except Exception as e:
                    out["errors"].append(f"{source_name}: {e}")

        # 2) Custom reports from files/URLs (CSV/XLSX/PDF/JSON)
        for item in task.get("custom_reports", []) or []:
            try:
                loaded = self._load_custom_report(item)
                out["reports"].append(loaded)
            except Exception as e:
                out["errors"].append(f"{item}: {e}")

        if not out["reports"] and not out["errors"]:
            return {}
        return out

    def _fetch_external_api(self, endpoint: str, token: str) -> Any:
        resp = requests.get(endpoint, headers={"Authorization": f"Bearer {token}"}, timeout=20)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "").lower()
        if "json" in ctype:
            return resp.json()
        return {"raw": resp.text[:5000]}

    def _load_custom_report(self, item: str) -> dict[str, Any]:
        # URL report
        if item.startswith("http://") or item.startswith("https://"):
            resp = requests.get(item, timeout=20)
            resp.raise_for_status()
            ext = Path(item.split("?")[0]).suffix.lower()
            return self._parse_report_bytes(source=item, ext=ext, content=resp.content, text=resp.text)

        # Local file report
        path = Path(item)
        if not path.exists():
            raise FileNotFoundError(f"custom report not found: {item}")
        ext = path.suffix.lower()
        content = path.read_bytes()
        text = None
        if ext in {".csv", ".json", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
        return self._parse_report_bytes(source=str(path), ext=ext, content=content, text=text)

    def _parse_report_bytes(self, source: str, ext: str, content: bytes, text: str | None) -> dict[str, Any]:
        if ext == ".csv":
            rows = list(csv.DictReader((text or "").splitlines()))
            return {"source": source, "format": "csv", "rows": rows[:200]}
        if ext in {".json"}:
            return {"source": source, "format": "json", "data": json.loads(text or "{}")}
        if ext in {".xlsx", ".xlsm"}:
            try:
                import openpyxl  # type: ignore
            except Exception as e:
                raise RuntimeError(f"openpyxl required for xlsx parsing: {e}")
            from io import BytesIO

            wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
            data: dict[str, list[list[Any]]] = {}
            for ws in wb.worksheets[:3]:
                rows = []
                for row in ws.iter_rows(min_row=1, max_row=200, values_only=True):
                    rows.append(list(row))
                data[ws.title] = rows
            return {"source": source, "format": "xlsx", "sheets": data}
        if ext == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception as e:
                raise RuntimeError(f"pypdf required for pdf parsing: {e}")
            from io import BytesIO

            reader = PdfReader(BytesIO(content))
            extracted = []
            for page in reader.pages[:20]:
                extracted.append((page.extract_text() or "")[:4000])
            return {"source": source, "format": "pdf", "pages": extracted}
        return {"source": source, "format": "text", "content": (text or content.decode(errors="ignore"))[:5000]}

    def _extract_keyword_signals(self, gsc: dict, external: dict) -> dict[str, Any]:
        keywords: list[dict[str, Any]] = []
        if isinstance(gsc, dict):
            for k in gsc.get("top_keywords", []) or []:
                if isinstance(k, dict):
                    keywords.append(
                        {
                            "keyword": k.get("query") or k.get("keyword"),
                            "clicks": k.get("clicks"),
                            "impressions": k.get("impressions"),
                            "position": k.get("position"),
                            "source": "gsc",
                        }
                    )

        for report in (external or {}).get("reports", []):
            if not isinstance(report, dict):
                continue
            rows = report.get("rows")
            if isinstance(rows, list):
                for row in rows[:100]:
                    if not isinstance(row, dict):
                        continue
                    keyword = row.get("keyword") or row.get("query") or row.get("Keyword")
                    if keyword:
                        keywords.append(
                            {
                                "keyword": keyword,
                                "traffic": row.get("traffic") or row.get("Traffic"),
                                "position": row.get("position") or row.get("Position"),
                                "source": report.get("source", "external"),
                            }
                        )

        unique = {}
        for k in keywords:
            key = str(k.get("keyword") or "").strip().lower()
            if key and key not in unique:
                unique[key] = k
        deduped = list(unique.values())
        return {"top_keywords_count": len(deduped), "sample": deduped[:20]}

if __name__ == "__main__":
    agent = GrowthAgent()
    agent.run()
