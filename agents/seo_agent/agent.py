import sys
import os
import logging

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

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        self.ci_bridge = CISEOBridge()
        self._scheduler = None
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
        task_type = task_data.get("task", {}).get("type")
        target_url = task_data.get("task", {}).get("url")

        if task_type == "full_audit":
            # 1. Spawn Speed Optimizer subagent
            speed_report = {}
            try:
                speed_report = self.spawn_subagent(SpeedOptimizerAgent, {"url": target_url}) or {}
            except Exception as e:
                logger.error(f"SpeedOptimizerAgent failed: {e}")

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
                "speed_recommendations": speed_report.get("recommendations", []),
            }

            self.log_execution(
                task=task_data,
                thought_process="Spawned SpeedOptimizer. Published to Data Analyser.",
                action_taken="Generated partial audit report pending traffic data."
            )
            return audit_report
        elif task_type == "run_autonomous_pipeline":
            return self._run_pipeline(task_data)
        elif task_type == "get_latest_report":
            return self._get_latest_report(task_data)
        elif task_type == "list_pending_actions":
            return self._list_pending_actions(task_data)
        elif task_type == "approve_report":
            return self._approve_report(task_data)
        elif task_type == "run_implementation":
            return self._run_implementation(task_data)
        elif task_type == "run_validation":
            return self._run_validation(task_data)
        elif task_type == "status":
            return self._status(task_data)
        elif task_type == "report_history":
            return self._report_history(task_data)
        elif task_type == "list_actions":
            return self._list_actions(task_data)
        elif task_type == "metrics":
            return self._metrics(task_data)
        elif task_type == "get_logs":
            return self._logs(task_data)
        elif task_type == "run_fetch_only":
            return self._run_fetch_only(task_data)
        elif task_type == "run_extended":
            return self._run_extended(task_data)
        elif task_type == "get_extended_report":
            return self._get_extended_report(task_data)
        elif task_type == "search_seo_data":
            return self._search_seo_data(task_data)
        elif task_type == "get_ga4_summary":
            return self._get_ga4_summary(task_data)
        elif task_type == "get_ga4_page_metrics":
            return self._get_ga4_page_metrics(task_data)
        elif task_type == "ga4_fetch":
            return self._ga4_fetch(task_data)
        elif task_type == "ga4_snapshots":
            return self._ga4_snapshots(task_data)
        elif task_type == "ga4_conversion_audit":
            return self._ga4_conversion_audit(task_data)
        elif task_type == "ga4_attribution_data":
            return self._ga4_attribution_data(task_data)
        elif task_type == "ga4_funnel_report":
            return self._ga4_funnel_report(task_data)
        elif task_type == "search_reference_docs":
            return self._search_reference_docs(task_data)
        elif task_type == "reference_doc_sources":
            return self._reference_doc_sources(task_data)
        elif task_type == "train_reference_docs":
            return self._train_reference_docs(task_data)
        else:
            return super().handle_task(task_data)

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
        report_id = task_data.get("task", {}).get("report_id")
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
        limit = int(task_data.get("task", {}).get("limit", 30))
        try:
            return {"status": "success", "history": self.ci_bridge.report_history(limit=limit)}
        except Exception as e:
            return {"status": "error", "message": f"Report history failed: {e}"}

    def _list_actions(self, task_data):
        status_filter = task_data.get("task", {}).get("status")
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
        lines = int(task_data.get("task", {}).get("lines", 100))
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
        payload = task_data.get("task", {})
        query = payload.get("query", "")
        collection = payload.get("collection", "gsc")
        n = int(payload.get("n", 5))
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
        page_path = task_data.get("task", {}).get("page_path") or task_data.get("task", {}).get("url")
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
        days = int(task_data.get("task", {}).get("days", 28))
        try:
            return {"status": "success", "audit": self.ci_bridge.ga4_conversion_audit(days=days)}
        except Exception as e:
            return {"status": "error", "message": f"GA4 conversion audit failed: {e}"}

    def _ga4_attribution_data(self, task_data):
        days = int(task_data.get("task", {}).get("days", 28))
        try:
            return {"status": "success", "attribution": self.ci_bridge.ga4_attribution_data(days=days)}
        except Exception as e:
            return {"status": "error", "message": f"GA4 attribution data failed: {e}"}

    def _ga4_funnel_report(self, task_data):
        days = int(task_data.get("task", {}).get("days", 28))
        try:
            return {"status": "success", "funnel": self.ci_bridge.ga4_funnel_report(days=days)}
        except Exception as e:
            return {"status": "error", "message": f"GA4 funnel report failed: {e}"}

    def _search_reference_docs(self, task_data):
        payload = task_data.get("task", {})
        query = payload.get("query") or payload.get("q")
        n = int(payload.get("n", 8))
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
        payload = task_data.get("task", {})
        max_pages = int(payload.get("max_pages", 120))
        max_depth = int(payload.get("max_depth", 3))
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
