import os
from typing import Any

import requests

from config.settings import config


class CISEOBridge:
    """
    Unified bridge to CI SEO capabilities.
    Preference order:
    1) Local direct module execution (no separate ci-seo-agent service needed)
    2) HTTP fallback (for backward compatibility)
    """

    def __init__(self):
        host = os.getenv("SEO_API_HOST", "127.0.0.1")
        self.base_url = os.getenv("SEO_API_BASE_URL", f"http://{host}:{config.SEO_API_PORT}")
        self.secret = config.SEO_API_SECRET
        self.session = requests.Session()

        self._scheduler = None
        self._vector_store = None
        self._gsc_extended = None
        self._ga4_auditor = None
        self._reference_docs_trainer = None
        self._cfg = None
        self._init_local_engine()

    def _init_local_engine(self):
        try:
            from agents.seo_agent import scheduler as ci_scheduler
            from agents.seo_agent.vector_store import vector_store as ci_vector_store
            from agents.seo_agent.gsc_extended import gsc_extended as ci_gsc_extended
            from agents.seo_agent.ga4_conversion_auditor import GA4ConversionAuditor
            from agents.seo_agent.reference_docs import reference_docs_trainer
            from agents.seo_agent.seo_config import cfg as ci_cfg

            ci_vector_store.init()
            self._scheduler = ci_scheduler
            self._vector_store = ci_vector_store
            self._gsc_extended = ci_gsc_extended
            self._ga4_auditor = GA4ConversionAuditor()
            self._reference_docs_trainer = reference_docs_trainer
            self._cfg = ci_cfg
        except Exception:
            self._scheduler = None
            self._vector_store = None
            self._gsc_extended = None
            self._ga4_auditor = None
            self._reference_docs_trainer = None
            self._cfg = None

    def _headers(self) -> dict[str, str]:
        return {"X-API-Secret": self.secret} if self.secret else {}

    def _get_http(self, path: str, timeout: int = 30) -> dict[str, Any]:
        r = self.session.get(f"{self.base_url}{path}", headers=self._headers(), timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post_http(self, path: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
        r = self.session.post(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=payload or {},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()

    def status(self) -> dict[str, Any]:
        if self._scheduler and self._vector_store:
            state = self._scheduler.get_state()
            try:
                chroma_stats = self._vector_store.stats()
            except Exception as e:
                chroma_stats = {"status": "degraded", "error": str(e)}
            return {"status": "success", "state": state, "chroma_stats": chroma_stats}
        return self._get_http("/status")

    def latest_report(self) -> dict[str, Any]:
        if self._vector_store:
            report = self._vector_store.get_latest_report()
            return report or {}
        return self._get_http("/report/latest")

    def pending_actions(self) -> dict[str, Any]:
        if self._vector_store:
            actions = self._vector_store.get_pending_actions(limit=50)
            return {"count": len(actions), "actions": actions}
        return self._get_http("/actions")

    def all_actions(self, status: str | None = None) -> dict[str, Any]:
        if self._vector_store:
            items = self._vector_store.get_all_actions(status=status)
            return {
                "count": len(items),
                "status_filter": status,
                "actions": [a.get("metadata", {}) for a in items],
            }
        path = f"/actions?status={status}" if status else "/actions"
        return self._get_http(path)

    def report_history(self, limit: int = 30) -> dict[str, Any]:
        if self._vector_store:
            return {"reports": self._vector_store.get_report_history(limit=limit)}
        return self._get_http(f"/report/history?limit={limit}")

    def metrics(self) -> dict[str, Any]:
        if self._vector_store and self._scheduler:
            try:
                chroma = self._vector_store.stats()
            except Exception as e:
                chroma = {"status": "degraded", "error": str(e)}
            try:
                llm_metrics = self._vector_store.get_llm_metrics_summary()
            except Exception as e:
                llm_metrics = {"status": "degraded", "error": str(e)}
            try:
                provider_statuses = self._vector_store.list_provider_statuses()
            except Exception as e:
                provider_statuses = [{"status": "degraded", "error": str(e)}]
            return {
                "chroma": chroma,
                "state": self._scheduler.get_state(),
                "llm_metrics": llm_metrics,
                "provider_statuses": provider_statuses,
            }
        return self._get_http("/metrics")

    def logs(self, lines: int = 100) -> dict[str, Any]:
        if self._cfg:
            log_file = os.path.join(str(self._cfg.LOGS_DIR), "agent.log")
            if not os.path.exists(log_file):
                return {"lines": []}
            with open(log_file, "r", encoding="utf-8") as fh:
                all_lines = fh.readlines()
            return {"lines": all_lines[-lines:]}
        return self._get_http(f"/logs?lines={lines}")

    def run_pipeline(self) -> dict[str, Any]:
        if self._scheduler:
            report_id = self._scheduler.step_fetch_and_analyze()
            return {"success": bool(report_id), "report_id": report_id}
        return self._post_http("/run-now")

    def run_fetch_only(self) -> dict[str, Any]:
        if self._scheduler and self._vector_store:
            from agents.seo_agent.gsc_client import GSCClient

            gsc = GSCClient()
            snapshot = gsc.fetch_full_snapshot()
            summary = gsc.compute_summary_stats(snapshot)
            snapshot_id = self._vector_store.store_gsc_snapshot(snapshot, summary)
            return {"success": True, "snapshot_id": snapshot_id}
        return self._post_http("/run-fetch-only")

    def run_implement(self) -> dict[str, Any]:
        if self._scheduler:
            state = self._scheduler.get_state()
            report_id = state.get("pending_approval_report_id") or state.get("last_report_id")
            if not report_id:
                return {"success": False, "message": "No report_id available for implementation"}
            results = self._scheduler.step_implement_approved(report_id, approved_by="seo_agent_manual")
            return {"success": True, "report_id": report_id, "results": results}
        return self._post_http("/run-implement")

    def run_validate(self) -> dict[str, Any]:
        if self._scheduler:
            self._scheduler.step_validate_evening()
            return {"success": True}
        return self._post_http("/run-validate")

    def run_extended(self) -> dict[str, Any]:
        if self._gsc_extended and self._vector_store and self._scheduler:
            report = self._gsc_extended.fetch_full_extended_report(top_url_count=15)
            snapshot_id = self._scheduler.get_state().get("last_snapshot_id", "manual")
            report_id = self._vector_store.store_analysis_report(
                {
                    **report,
                    "type": "extended",
                    "snapshot_id": snapshot_id,
                    "summary": str(report.get("summary", {})),
                    "fetch_date": report.get("generated_at", ""),
                }
            )
            return {"success": True, "report_id": report_id}
        return self._post_http("/run-extended")

    def latest_extended_report(self) -> dict[str, Any]:
        if self._vector_store:
            reports = self._vector_store.get_report_history(limit=20)
            extended = [
                r for r in reports
                if r.get("type") == "extended" or "extended" in r.get("report_id", "")
            ]
            if not extended:
                return {}
            latest_id = extended[0].get("report_id")
            col = self._vector_store._col(config.CHROMA_COLLECTION_REPORTS)
            res = col.get(ids=[latest_id], include=["metadatas"])
            if res.get("ids"):
                import json
                return json.loads(res["metadatas"][0].get("full_report", "{}"))
            return extended[0]
        return self._get_http("/extended-report/latest")

    def search(self, query: str, collection: str = "gsc", n: int = 5) -> dict[str, Any]:
        if self._vector_store:
            if collection == "ga":
                results = self._vector_store.search_ga_data(query, n=n)
            elif collection == "actions":
                col = self._vector_store._col(config.CHROMA_COLLECTION_ACTIONS)
                if col.count() == 0:
                    results = []
                else:
                    raw = col.query(
                        query_texts=[query],
                        n_results=min(n, col.count()),
                        include=["documents", "metadatas", "distances"],
                    )
                    results = [
                        {"document": doc, "metadata": meta, "distance": dist}
                        for doc, meta, dist in zip(
                            raw["documents"][0], raw["metadatas"][0], raw["distances"][0]
                        )
                    ]
            else:
                results = self._vector_store.search_similar_keywords(query, n=n)
            return {"query": query, "collection": collection, "results": results}
        return self._get_http(f"/search?q={query}&n={n}&collection={collection}")

    def ga4_summary(self) -> dict[str, Any]:
        if self._vector_store:
            return self._vector_store.get_latest_ga_summary() or {}
        return self._get_http("/ga4/summary")

    def ga4_page_metrics(self, page_path: str) -> dict[str, Any]:
        if self._vector_store:
            return self._vector_store.get_ga_page_metrics(page_path) or {}
        return self._get_http(f"/ga4/page?page_path={page_path}")

    def ga4_fetch(self) -> dict[str, Any]:
        if self._vector_store:
            from agents.seo_agent.ga_client import ga_client

            snapshot = ga_client.fetch_full_snapshot(days=config.GSC_DAYS_HISTORY)
            summary = ga_client.compute_summary_stats(snapshot)
            snap_id = self._vector_store.store_ga_snapshot(snapshot, summary)
            return {"success": True, "snapshot_id": snap_id}
        return self._post_http("/ga4/fetch")

    def ga4_snapshots(self) -> dict[str, Any]:
        if self._vector_store:
            return {"snapshot_dates": self._vector_store.list_ga_snapshot_dates()}
        return self._get_http("/ga4/snapshots")

    def ga4_conversion_audit(self, days: int = 28) -> dict[str, Any]:
        if self._ga4_auditor:
            return {"status": "ok", "report": self._ga4_auditor.run_full_audit(days=days)}
        return self._get_http(f"/ga4/conversion-audit?days={days}")

    def ga4_attribution_data(self, days: int = 28) -> dict[str, Any]:
        if self._ga4_auditor:
            return {"status": "ok", "data": self._ga4_auditor.get_attribution_summary(days=days)}
        return self._get_http(f"/ga4/attribution-data?days={days}")

    def ga4_funnel_report(self, days: int = 28) -> dict[str, Any]:
        if self._ga4_auditor:
            return {"status": "ok", "report": self._ga4_auditor.audit_funnel_conversion(days=days)}
        return self._get_http(f"/ga4/funnel-report?days={days}")

    def docs_search(self, query: str, n: int = 8, source: str | None = None) -> dict[str, Any]:
        if self._vector_store:
            results = self._vector_store.search_reference_docs(query, n=n, source=source)
            return {"query": query, "source": source, "results": results}
        path = f"/docs/search?query={query}&n={n}" + (f"&source={source}" if source else "")
        return self._get_http(path)

    def docs_sources(self) -> dict[str, Any]:
        if self._vector_store:
            stats = self._vector_store.stats()
            return {
                "reference_docs_count": stats.get("reference_docs_count", 0),
                "sources": self._vector_store.list_reference_doc_sources(),
            }
        return self._get_http("/docs/sources")

    def docs_train(self, max_pages: int = 120, max_depth: int = 3) -> dict[str, Any]:
        if self._reference_docs_trainer:
            result = self._reference_docs_trainer.train_google_search_docs(
                max_pages=max_pages, max_depth=max_depth
            )
            return {"success": True, "result": result}
        return self._post_http(f"/docs/train?max_pages={max_pages}&max_depth={max_depth}")

    def approve(self, report_id: str) -> dict[str, Any]:
        if self._scheduler:
            self._scheduler.update_state(pending_approval_report_id=report_id, approval_received=True)
            results = self._scheduler.step_implement_approved(report_id, approved_by="seo_agent_approve")
            return {"success": True, "report_id": report_id, "results": results}
        return self._post_http(f"/approve/{report_id}")
