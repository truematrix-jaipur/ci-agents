import sys
import os
import logging
import datetime
import time
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from config.settings import config
from agents.seo_agent.subagents.speed_optimizer import SpeedOptimizerAgent
from agents.seo_agent.ci_bridge import CISEOBridge
from agents.seo_agent.scheduler import create_scheduler

logger = logging.getLogger(__name__)

class SEOAgent(BaseAgent):
    AGENT_ROLE = "seo_agent"
    SYSTEM_PROMPT = """You are the master SEO Orchestrator.
    You manage the SEO health of connected websites. 
    You do not hallucinate data. You must spawn subagents (like speed optimizer) 
    or request data from the Data Analyser agent to gather factual insights."""
    METRICS_REDIS_KEY = "agent_runtime_metrics:seo_agent"
    METRICS_EVENTS_REDIS_KEY = "agent_runtime_metrics_events:seo_agent"
    GOAL_REDIS_KEY = "agent_goal_profile:seo_agent"

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        self.ci_bridge = CISEOBridge()
        self._scheduler = None
        self._metrics = {"tasks_total": 0, "success": 0, "warning": 0, "error": 0}
        if config.SEO_AUTONOMOUS_SCHEDULER_ENABLED:
            self._start_scheduler()

    def _start_scheduler(self):
        try:
            scheduler = create_scheduler()
            scheduler.start()
            self._scheduler = scheduler
            logger.info("SEO autonomous scheduler started (embedded in seo_agent).")
        except Exception as e:
            logger.error(f"Failed to start SEO autonomous scheduler: {e}")

    def handle_task(self, task_data):
        logger.info(f"SEO Agent {self.agent_id} handling task: {task_data}")
        payload = self._extract_task_payload(task_data)
        task_type = payload.get("type")
        started_at = time.perf_counter()
        route = task_type or "unknown"

        if task_type == "full_audit":
            result = self._full_audit(task_data)
        elif task_type == "run_autonomous_pipeline":
            result = self._run_pipeline(task_data)
        elif task_type == "get_latest_report":
            result = self._get_latest_report(task_data)
        elif task_type == "list_pending_actions":
            result = self._list_pending_actions(task_data)
        elif task_type == "approve_report":
            result = self._approve_report(task_data)
        elif task_type == "run_implementation":
            result = self._run_implementation(task_data)
        elif task_type == "run_validation":
            result = self._run_validation(task_data)
        elif task_type == "status":
            result = self._status(task_data)
        elif task_type == "report_history":
            result = self._report_history(task_data)
        elif task_type == "list_actions":
            result = self._list_actions(task_data)
        elif task_type == "metrics":
            result = self._metrics(task_data)
        elif task_type == "get_logs":
            result = self._logs(task_data)
        elif task_type == "run_fetch_only":
            result = self._run_fetch_only(task_data)
        elif task_type == "run_extended":
            result = self._run_extended(task_data)
        elif task_type == "get_extended_report":
            result = self._get_extended_report(task_data)
        elif task_type == "search_seo_data":
            result = self._search_seo_data(task_data)
        elif task_type == "get_ga4_summary":
            result = self._get_ga4_summary(task_data)
        elif task_type == "get_ga4_page_metrics":
            result = self._get_ga4_page_metrics(task_data)
        elif task_type == "ga4_fetch":
            result = self._ga4_fetch(task_data)
        elif task_type == "ga4_snapshots":
            result = self._ga4_snapshots(task_data)
        elif task_type == "ga4_conversion_audit":
            result = self._ga4_conversion_audit(task_data)
        elif task_type == "ga4_attribution_data":
            result = self._ga4_attribution_data(task_data)
        elif task_type == "ga4_funnel_report":
            result = self._ga4_funnel_report(task_data)
        elif task_type == "search_reference_docs":
            result = self._search_reference_docs(task_data)
        elif task_type == "reference_doc_sources":
            result = self._reference_doc_sources(task_data)
        elif task_type == "train_reference_docs":
            result = self._train_reference_docs(task_data)
        elif task_type == "set_goal_target":
            result = self._set_goal_target(task_data)
            route = "set_goal_target"
        elif task_type == "get_goal_target":
            result = self._get_goal_target(task_data)
            route = "get_goal_target"
        elif task_type == "manual_command":
            result = self._handle_manual_command(task_data)
            route = "manual_command"
        else:
            result = super().handle_task(task_data)
            route = "fallback"
        return self._finalize_result(result=result, started_at=started_at, route=route)

    @staticmethod
    def _safe_int(value, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        if min_value is not None and parsed < min_value:
            parsed = min_value
        if max_value is not None and parsed > max_value:
            parsed = max_value
        return parsed

    def _finalize_result(self, result, started_at: float, route: str):
        out = result if isinstance(result, dict) else {"status": "success", "result": result}
        status = str(out.get("status", "success")).lower()
        if status not in {"success", "warning", "error"}:
            status = "success"
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        if not hasattr(self, "_metrics") or not isinstance(self._metrics, dict):
            self._metrics = {"tasks_total": 0, "success": 0, "warning": 0, "error": 0}
        self._metrics["tasks_total"] += 1
        self._metrics[status] += 1
        out.setdefault("execution", {})
        out["execution"].update(
            {
                "route": route,
                "duration_ms": elapsed_ms,
                "metrics_snapshot": dict(self._metrics),
            }
        )
        self._persist_metrics(route=route, status=status, duration_ms=elapsed_ms)
        return out

    def _persist_metrics(self, route: str, status: str, duration_ms: int):
        redis_client = getattr(self, "redis_client", None)
        if redis_client is None:
            return
        now = datetime.datetime.now(datetime.UTC).isoformat()
        try:
            mapping = {
                "tasks_total": str(self._metrics.get("tasks_total", 0)),
                "success": str(self._metrics.get("success", 0)),
                "warning": str(self._metrics.get("warning", 0)),
                "error": str(self._metrics.get("error", 0)),
                "last_route": route,
                "last_status": status,
                "last_duration_ms": str(int(duration_ms)),
                "updated_at_utc": now,
            }
            redis_client.hset(self.METRICS_REDIS_KEY, mapping=mapping)
            redis_client.expire(self.METRICS_REDIS_KEY, 3600 * 24 * 30)
            event = {
                "timestamp_utc": now,
                "route": route,
                "status": status,
                "duration_ms": int(duration_ms),
                "tasks_total": int(self._metrics.get("tasks_total", 0)),
            }
            redis_client.rpush(self.METRICS_EVENTS_REDIS_KEY, json.dumps(event))
            redis_client.ltrim(self.METRICS_EVENTS_REDIS_KEY, -5000, -1)
            redis_client.expire(self.METRICS_EVENTS_REDIS_KEY, 3600 * 24 * 30)
        except Exception:
            return

    def _handle_manual_command(self, task_data):
        payload = self._extract_task_payload(task_data)
        command = str(payload.get("command", "")).strip()
        if not command:
            return {"status": "error", "message": "command is required for manual_command"}
        cmd = command.lower()
        if "goal" in cmd and ("show" in cmd or "get" in cmd or "view" in cmd):
            result = self._get_goal_target(task_data)
            result["manual_command_routed"] = "get_goal_target"
            return result
        if "status" in cmd:
            result = self._status(task_data)
            result["manual_command_routed"] = "status"
            return result
        if ("pipeline" in cmd or "autonomous" in cmd) and ("run" in cmd or "trigger" in cmd):
            result = self._run_pipeline(task_data)
            result["manual_command_routed"] = "run_autonomous_pipeline"
            return result
        if "pending" in cmd and "action" in cmd:
            result = self._list_pending_actions(task_data)
            result["manual_command_routed"] = "list_pending_actions"
            return result
        if "latest" in cmd and "report" in cmd:
            result = self._get_latest_report(task_data)
            result["manual_command_routed"] = "get_latest_report"
            return result
        return {
            "status": "success",
            "message": (
                "Manual command not executed to avoid non-deterministic LLM fallback. "
                "Use structured task types like: status, run_autonomous_pipeline, get_latest_report, "
                "list_pending_actions, report_history, ga4_fetch."
            ),
            "skipped_llm": True,
            "manual_command": command,
        }

    def _set_goal_target(self, task_data):
        payload = self._extract_task_payload(task_data)
        goal_input = payload.get("goal")
        if not isinstance(goal_input, dict):
            goal_input = {
                "site": payload.get("site") or payload.get("url") or "https://indogenmed.org",
                "objectives": payload.get("objectives") or [],
                "scope": payload.get("scope") or [],
                "priority": payload.get("priority") or "high",
                "notes": payload.get("notes") or payload.get("goal_notes") or "",
            }

        objectives = goal_input.get("objectives") or []
        if isinstance(objectives, str):
            objectives = [x.strip() for x in objectives.split(",") if x.strip()]
        if not isinstance(objectives, list):
            objectives = []

        scope = goal_input.get("scope") or []
        if isinstance(scope, str):
            scope = [x.strip() for x in scope.split(",") if x.strip()]
        if not isinstance(scope, list):
            scope = []

        normalized = {
            "site": goal_input.get("site") or "https://indogenmed.org",
            "objectives": objectives,
            "scope": scope,
            "priority": goal_input.get("priority") or "high",
            "notes": goal_input.get("notes") or "",
            "updated_at_utc": datetime.datetime.now(datetime.UTC).isoformat(),
            "source": payload.get("source") or "manual_set_goal_target",
        }

        self._goal_profile = normalized
        redis_client = getattr(self, "redis_client", None)
        if redis_client is not None:
            try:
                redis_client.hset(
                    self.GOAL_REDIS_KEY,
                    mapping={
                        "goal_profile_json": json.dumps(normalized),
                        "updated_at_utc": normalized["updated_at_utc"],
                        "site": str(normalized["site"]),
                    },
                )
                redis_client.expire(self.GOAL_REDIS_KEY, 3600 * 24 * 30)
            except Exception:
                pass

        return {
            "status": "success",
            "message": "SEO goal target updated.",
            "goal_profile": normalized,
        }

    def _get_goal_target(self, task_data):
        redis_client = getattr(self, "redis_client", None)
        if redis_client is not None:
            try:
                raw = redis_client.hgetall(self.GOAL_REDIS_KEY) or {}
                goal_json = raw.get("goal_profile_json")
                if isinstance(goal_json, bytes):
                    goal_json = goal_json.decode("utf-8", errors="replace")
                if goal_json:
                    return {"status": "success", "goal_profile": json.loads(goal_json)}
            except Exception:
                pass
        local_goal = getattr(self, "_goal_profile", None)
        if isinstance(local_goal, dict):
            return {"status": "success", "goal_profile": local_goal}
        return {
            "status": "warning",
            "message": "No SEO goal target is set yet.",
            "goal_profile": None,
        }

    def _full_audit(self, task_data):
        payload = self._extract_task_payload(task_data)
        target_url = payload.get("url") or payload.get("target_url") or "https://indogenmed.org"
        speed_report = {}
        try:
            speed_report = self.spawn_subagent(SpeedOptimizerAgent, {"url": target_url}) or {}
        except Exception as e:
            logger.error(f"SpeedOptimizerAgent failed: {e}")
        dispatched = {}
        try:
            sales_days = self._safe_int(payload.get("sales_window_days", 30), default=30, min_value=1, max_value=365)
            dispatched["sales_summary_task_id"] = self.publish_task_to_agent(
                "data_analyser",
                {
                    "type": "summarize_sales_trend",
                    "days": sales_days,
                    "database": "erpnext",
                    "source": "seo_full_audit",
                },
            )
        except Exception as e:
            logger.warning(f"DataAnalyser sales summary dispatch failed: {e}")

        # Optional legacy path: only query traffic stats if explicitly enabled in this environment.
        traffic_stats_enabled = str(os.getenv("SEO_ENABLE_TRAFFIC_STATS_QUERY", "0")).strip().lower() in {"1", "true", "yes", "on"}
        if traffic_stats_enabled:
            table = (os.getenv("SEO_TRAFFIC_STATS_TABLE", "traffic_stats") or "traffic_stats").strip()
            query = f"SELECT page_views FROM {table} WHERE url = %s"
            try:
                dispatched["traffic_stats_task_id"] = self.publish_task_to_agent(
                    "data_analyser",
                    {
                        "type": "query_db",
                        "database": "mysql",
                        "query": query,
                        "params": [target_url],
                        "source": "seo_full_audit",
                    },
                )
            except Exception as e:
                logger.warning(f"Traffic stats dispatch failed: {e}")

        self.log_execution(
            task=task_data,
            thought_process="Spawned SpeedOptimizer and dispatched scalable data signals for SEO audit.",
            action_taken="Generated partial audit report with async data collection."
        )
        return {
            "status": "success",
            "target_url": target_url,
            "speed_metrics": speed_report.get("metrics", {}),
            "speed_recommendations": speed_report.get("recommendations", []),
            "dispatched": dispatched,
        }

    def _status(self, task_data):
        try:
            data = self.ci_bridge.status()
            return {"status": "success", "ci_status": data}
        except Exception as e:
            return {"status": "error", "message": f"CI SEO status failed: {e}"}

    def _run_pipeline(self, task_data):
        try:
            resp = self.ci_bridge.run_pipeline()
            self.log_execution(
                task=task_data,
                thought_process="Triggered CI SEO fetch+analyze pipeline via local API.",
                action_taken=f"Pipeline trigger response: {resp}",
                status="success",
            )
            return {"status": "success", "message": "Autonomous SEO pipeline triggered.", "details": resp}
        except Exception as e:
            return {"status": "error", "message": f"Pipeline trigger failed: {e}"}

    def _get_latest_report(self, task_data):
        try:
            report = self.ci_bridge.latest_report()
            return {"status": "success", "report": report}
        except Exception as e:
            return {"status": "error", "message": f"Latest report fetch failed: {e}"}

    def _list_pending_actions(self, task_data):
        try:
            actions = self.ci_bridge.pending_actions()
            return {"status": "success", "actions": actions}
        except Exception as e:
            return {"status": "error", "message": f"Pending actions fetch failed: {e}"}

    def _approve_report(self, task_data):
        payload = self._extract_task_payload(task_data)
        report_id = payload.get("report_id")
        if not report_id:
            try:
                latest = self.ci_bridge.latest_report()
                report_id = latest.get("report_id")
            except Exception:
                report_id = None
        if not report_id:
            return {"status": "error", "message": "report_id is required and could not be auto-detected"}
        try:
            resp = self.ci_bridge.approve(report_id)
            return {"status": "success", "message": f"Report {report_id} approved.", "details": resp}
        except Exception as e:
            return {"status": "error", "message": f"Approve report failed: {e}"}

    def _run_implementation(self, task_data):
        try:
            resp = self.ci_bridge.run_implement()
            return {"status": "success", "message": "Implementation run triggered.", "details": resp}
        except Exception as e:
            return {"status": "error", "message": f"Implementation trigger failed: {e}"}

    def _run_validation(self, task_data):
        try:
            resp = self.ci_bridge.run_validate()
            return {"status": "success", "message": "Validation run triggered.", "details": resp}
        except Exception as e:
            return {"status": "error", "message": f"Validation trigger failed: {e}"}

    def _report_history(self, task_data):
        payload = self._extract_task_payload(task_data)
        limit = self._safe_int(payload.get("limit", 30), default=30, min_value=1, max_value=500)
        try:
            return {"status": "success", "history": self.ci_bridge.report_history(limit=limit)}
        except Exception as e:
            return {"status": "error", "message": f"Report history failed: {e}"}

    def _list_actions(self, task_data):
        payload = self._extract_task_payload(task_data)
        status_filter = payload.get("status")
        try:
            return {"status": "success", "actions": self.ci_bridge.all_actions(status=status_filter)}
        except Exception as e:
            return {"status": "error", "message": f"Action list failed: {e}"}

    def _metrics(self, task_data):
        try:
            return {"status": "success", "metrics": self.ci_bridge.metrics()}
        except Exception as e:
            return {"status": "error", "message": f"Metrics fetch failed: {e}"}

    def _logs(self, task_data):
        payload = self._extract_task_payload(task_data)
        lines = self._safe_int(payload.get("lines", 100), default=100, min_value=1, max_value=2000)
        try:
            return {"status": "success", "logs": self.ci_bridge.logs(lines=lines)}
        except Exception as e:
            return {"status": "error", "message": f"Logs fetch failed: {e}"}

    def _run_fetch_only(self, task_data):
        try:
            return {"status": "success", "details": self.ci_bridge.run_fetch_only()}
        except Exception as e:
            return {"status": "error", "message": f"Fetch-only run failed: {e}"}

    def _run_extended(self, task_data):
        try:
            return {"status": "success", "details": self.ci_bridge.run_extended()}
        except Exception as e:
            return {"status": "error", "message": f"Extended run failed: {e}"}

    def _get_extended_report(self, task_data):
        try:
            return {"status": "success", "report": self.ci_bridge.latest_extended_report()}
        except Exception as e:
            return {"status": "error", "message": f"Extended report fetch failed: {e}"}

    def _search_seo_data(self, task_data):
        payload = self._extract_task_payload(task_data)
        query = payload.get("query", "")
        collection = payload.get("collection", "gsc")
        n = self._safe_int(payload.get("n", 5), default=5, min_value=1, max_value=50)
        if not query:
            return {"status": "error", "message": "query is required"}
        try:
            return {"status": "success", "results": self.ci_bridge.search(query=query, collection=collection, n=n)}
        except Exception as e:
            return {"status": "error", "message": f"SEO search failed: {e}"}

    def _get_ga4_summary(self, task_data):
        try:
            return {"status": "success", "ga4": self.ci_bridge.ga4_summary()}
        except Exception as e:
            return {"status": "error", "message": f"GA4 summary failed: {e}"}

    def _get_ga4_page_metrics(self, task_data):
        payload = self._extract_task_payload(task_data)
        page_path = payload.get("page_path") or payload.get("url")
        if not page_path:
            return {"status": "error", "message": "page_path/url is required"}
        try:
            return {"status": "success", "ga4": self.ci_bridge.ga4_page_metrics(page_path)}
        except Exception as e:
            return {"status": "error", "message": f"GA4 page metrics failed: {e}"}

    def _ga4_fetch(self, task_data):
        try:
            return {"status": "success", "details": self.ci_bridge.ga4_fetch()}
        except Exception as e:
            return {"status": "error", "message": f"GA4 fetch failed: {e}"}

    def _ga4_snapshots(self, task_data):
        try:
            return {"status": "success", "snapshots": self.ci_bridge.ga4_snapshots()}
        except Exception as e:
            return {"status": "error", "message": f"GA4 snapshots failed: {e}"}

    def _ga4_conversion_audit(self, task_data):
        payload = self._extract_task_payload(task_data)
        days = self._safe_int(payload.get("days", 28), default=28, min_value=1, max_value=365)
        try:
            return {"status": "success", "audit": self.ci_bridge.ga4_conversion_audit(days=days)}
        except Exception as e:
            return {"status": "error", "message": f"GA4 conversion audit failed: {e}"}

    def _ga4_attribution_data(self, task_data):
        payload = self._extract_task_payload(task_data)
        days = self._safe_int(payload.get("days", 28), default=28, min_value=1, max_value=365)
        try:
            return {"status": "success", "attribution": self.ci_bridge.ga4_attribution_data(days=days)}
        except Exception as e:
            return {"status": "error", "message": f"GA4 attribution data failed: {e}"}

    def _ga4_funnel_report(self, task_data):
        payload = self._extract_task_payload(task_data)
        days = self._safe_int(payload.get("days", 28), default=28, min_value=1, max_value=365)
        try:
            return {"status": "success", "funnel": self.ci_bridge.ga4_funnel_report(days=days)}
        except Exception as e:
            return {"status": "error", "message": f"GA4 funnel report failed: {e}"}

    def _search_reference_docs(self, task_data):
        payload = self._extract_task_payload(task_data)
        query = payload.get("query") or payload.get("q")
        n = self._safe_int(payload.get("n", 8), default=8, min_value=1, max_value=100)
        source = payload.get("source")
        if not query:
            return {"status": "error", "message": "query/q is required"}
        try:
            return {"status": "success", "docs": self.ci_bridge.docs_search(query=query, n=n, source=source)}
        except Exception as e:
            return {"status": "error", "message": f"Reference docs search failed: {e}"}

    def _reference_doc_sources(self, task_data):
        try:
            return {"status": "success", "sources": self.ci_bridge.docs_sources()}
        except Exception as e:
            return {"status": "error", "message": f"Reference docs sources failed: {e}"}

    def _train_reference_docs(self, task_data):
        payload = self._extract_task_payload(task_data)
        max_pages = self._safe_int(payload.get("max_pages", 120), default=120, min_value=1, max_value=1000)
        max_depth = self._safe_int(payload.get("max_depth", 3), default=3, min_value=1, max_value=10)
        try:
            return {
                "status": "success",
                "message": "Reference docs training completed.",
                "details": self.ci_bridge.docs_train(max_pages=max_pages, max_depth=max_depth),
            }
        except Exception as e:
            return {"status": "error", "message": f"Reference docs training failed: {e}"}

if __name__ == "__main__":
    agent = SEOAgent()
    agent.run()
