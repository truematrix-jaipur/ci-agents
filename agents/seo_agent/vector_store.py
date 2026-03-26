"""
CI SEO Agent — ChromaDB Vector Store
Handles all persistence: GSC snapshots, action items, analysis reports, page cache
Uses local ONNX all-MiniLM-L6-v2 embeddings via ChromaDB DefaultEmbeddingFunction (no API key needed)
"""
import json
import logging
import re
import uuid
from datetime import datetime, date, timedelta
from typing import Optional, Any

import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI

from agents.seo_agent.seo_config import cfg

logger = logging.getLogger("ci.vector_store")


class VectorStore:
    def __init__(self):
        self._client: Optional[chromadb.HttpClient] = None
        self._openai: Optional[OpenAI] = None
        self._ef = None  # embedding function

    # ── Init ───────────────────────────────────────────────────────────────

    def init(self, chroma_client=None):
        """Initialize ChromaDB and embedding function."""
        if self._client is not None and self._ef is not None:
            return self

        if chroma_client is not None:
            logger.info("Using provided ChromaDB client")
            self._client = chroma_client
        else:
            logger.info(f"Connecting to ChromaDB at {cfg.CHROMA_SERVER_HOST}:{cfg.CHROMA_SERVER_PORT}")
            self._client = chromadb.HttpClient(
                host=cfg.CHROMA_SERVER_HOST,
                port=cfg.CHROMA_SERVER_PORT
            )

        self._ef = embedding_functions.DefaultEmbeddingFunction()
        try:
            self._ensure_collections()
        except Exception as e:
            logger.warning(f"Primary Chroma backend failed, falling back to local persistent client: {e}")
            self._client = chromadb.PersistentClient(path=cfg.CHROMA_DB_PATH)
            self._ensure_collections()

        logger.info("ChromaDB initialized — collections ready")
        return self

    def _ensure_collections(self):
        # Ensure all collections exist
        self._get_or_create(cfg.CHROMA_COLLECTION_GSC)
        self._get_or_create(cfg.CHROMA_COLLECTION_GA)
        self._get_or_create(cfg.CHROMA_COLLECTION_ACTIONS)
        self._get_or_create(cfg.CHROMA_COLLECTION_REPORTS)
        self._get_or_create(cfg.CHROMA_COLLECTION_PAGES)
        # Telemetry/metrics collection
        try:
            self._get_or_create(cfg.CHROMA_COLLECTION_METRICS)
        except Exception:
            # Older deployments may not have this config — ignore if missing
            pass

    def _get_or_create(self, name: str):
        return self._resolve_collection(name)

    def _col(self, name: str):
        return self._resolve_collection(name)

    def _resolve_collection(self, name: str):
        if self._client is None or self._ef is None:
            self.init()
        last_exc = None
        attempts = [
            ("get_collection+ef", lambda: self._client.get_collection(name=name, embedding_function=self._ef)),
            ("get_collection", lambda: self._client.get_collection(name=name)),
            ("get_or_create+ef", lambda: self._client.get_or_create_collection(name=name, embedding_function=self._ef)),
            ("get_or_create", lambda: self._client.get_or_create_collection(name=name)),
        ]
        for label, fn in attempts:
            try:
                return fn()
            except Exception as e:
                last_exc = e
                logger.warning(f"Collection resolve attempt failed ({label}) for '{name}': {e}")
                continue
        raise RuntimeError(f"Unable to resolve Chroma collection '{name}': {last_exc}")

    # ── GSC Snapshots ──────────────────────────────────────────────────────

    def store_gsc_snapshot(self, snapshot: dict, summary: dict) -> str:
        """
        Store a GSC snapshot in ChromaDB.
        Creates one document per keyword+page combo (top 500) + summary documents.
        Returns snapshot_id.
        """
        col = self._col(cfg.CHROMA_COLLECTION_GSC)
        snapshot_id = f"snapshot_{snapshot['fetched_at']}"
        fetch_date = snapshot["fetched_at"]

        documents = []
        metadatas = []
        ids = []

        # Store top keyword+page rows as searchable documents
        rows = snapshot["data"].get("query_page", [])
        rows_sorted = sorted(rows, key=lambda r: r.get("clicks", 0), reverse=True)[
            :500
        ]

        for i, row in enumerate(rows_sorted):
            keys = row.get("keys", [])
            keyword = keys[0] if len(keys) > 0 else ""
            page = keys[1] if len(keys) > 1 else ""

            doc_text = (
                f"keyword: {keyword} | page: {page} | "
                f"clicks: {row.get('clicks', 0)} | "
                f"impressions: {row.get('impressions', 0)} | "
                f"ctr: {round(row.get('ctr', 0) * 100, 2)}% | "
                f"position: {round(row.get('position', 0), 1)}"
            )
            doc_id = f"{snapshot_id}_row_{i}"

            documents.append(doc_text)
            metadatas.append(
                {
                    "type": "gsc_row",
                    "snapshot_id": snapshot_id,
                    "fetch_date": fetch_date,
                    "keyword": keyword,
                    "page": page,
                    "clicks": int(row.get("clicks", 0)),
                    "impressions": int(row.get("impressions", 0)),
                    "ctr": float(row.get("ctr", 0)),
                    "position": float(row.get("position", 0)),
                }
            )
            ids.append(doc_id)

        # Store summary as a single document
        summary_text = json.dumps(summary, indent=2)
        documents.append(summary_text)
        metadatas.append(
            {
                "type": "gsc_summary",
                "snapshot_id": snapshot_id,
                "fetch_date": fetch_date,
                "total_clicks": summary.get("query_summary", {}).get(
                    "total_clicks", 0
                ),
                "total_impressions": summary.get("query_summary", {}).get(
                    "total_impressions", 0
                ),
            }
        )
        ids.append(f"{snapshot_id}_summary")

        # Upsert in batches of 100
        batch_size = 100
        for start in range(0, len(documents), batch_size):
            col.upsert(
                documents=documents[start : start + batch_size],
                metadatas=metadatas[start : start + batch_size],
                ids=ids[start : start + batch_size],
            )

        logger.info(
            f"Stored GSC snapshot {snapshot_id}: {len(documents)} documents"
        )
        return snapshot_id

    def get_previous_snapshot_summary(self, days_ago: int = 7) -> Optional[dict]:
        """Retrieve the most recent previous snapshot summary for comparison."""
        col = self._col(cfg.CHROMA_COLLECTION_GSC)
        target_date = (
            date.today()
            .__class__
            .fromordinal(date.today().toordinal() - days_ago)
            .isoformat()
        )
        try:
            results = col.get(
                where={"type": {"$eq": "gsc_summary"}},
                include=["documents", "metadatas"],
            )
            # Filter by date in Python (ChromaDB doesn't support string $lte)
            if results["documents"]:
                pairs = list(zip(results["metadatas"], results["documents"]))
                pairs = [(m, d) for m, d in pairs if m.get("fetch_date", "") <= target_date]
                pairs.sort(key=lambda x: x[0].get("fetch_date", ""), reverse=True)
                if pairs:
                    return json.loads(pairs[0][1])
        except Exception as e:
            logger.warning(f"Could not retrieve previous snapshot: {e}")
        return None

    def search_similar_keywords(self, keyword: str, n: int = 10) -> list[dict]:
        """Find historically similar keywords from past snapshots."""
        col = self._col(cfg.CHROMA_COLLECTION_GSC)
        if col.count() == 0:
            return []
        results = col.query(
            query_texts=[f"keyword: {keyword}"],
            n_results=min(n, col.count()),
            where={"type": {"$eq": "gsc_row"}},
            include=["documents", "metadatas", "distances"],
        )
        out = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            out.append({"document": doc, "metadata": meta, "distance": dist})
        return out

    def list_snapshot_dates(self) -> list[str]:
        """Return all unique snapshot dates stored."""
        col = self._col(cfg.CHROMA_COLLECTION_GSC)
        try:
            results = col.get(
                where={"type": {"$eq": "gsc_summary"}},
                include=["metadatas"],
            )
            dates = sorted(
                {m["fetch_date"] for m in results["metadatas"]}, reverse=True
            )
            return dates
        except Exception:
            return []

    # ── GA4 Data ───────────────────────────────────────────────────────────

    def store_ga_snapshot(self, snapshot: dict, summary: dict) -> str:
        """
        Store a GA4 snapshot in ChromaDB.
        Creates per-page documents + channel/geo documents + a summary document.
        Returns ga_snapshot_id.
        """
        col = self._col(cfg.CHROMA_COLLECTION_GA)
        snap_id   = f"ga_snapshot_{snapshot.get('fetched_at', datetime.utcnow().date().isoformat())}"
        fetch_date = snapshot.get("fetched_at", datetime.utcnow().date().isoformat())

        documents = []
        metadatas = []
        ids       = []

        # ── Per-page performance rows ──────────────────────────────────────
        pages = snapshot.get("page_performance", [])
        for i, row in enumerate(pages[:300]):
            path  = row.get("pagePath", "")
            title = row.get("pageTitle", "")
            views = int(row.get("screenPageViews", 0))
            users = int(row.get("activeUsers", 0))
            bounce = round(float(row.get("bounceRate", 0)) * 100, 2)
            dur   = round(float(row.get("averageSessionDuration", 0)), 1)
            eng   = round(float(row.get("engagementRate", 0)) * 100, 2)

            doc_text = (
                f"page: {path} | title: {title} | "
                f"views: {views} | users: {users} | "
                f"bounce_rate: {bounce}% | avg_duration: {dur}s | "
                f"engagement_rate: {eng}%"
            )
            documents.append(doc_text)
            metadatas.append({
                "type":         "ga_page",
                "snap_id":      snap_id,
                "fetch_date":   fetch_date,
                "page_path":    path,
                "page_title":   title[:200],
                "views":        views,
                "users":        users,
                "bounce_rate":  bounce,
                "avg_duration": dur,
                "engagement":   eng,
            })
            ids.append(f"{snap_id}_page_{i}")

        # ── Channel rows ──────────────────────────────────────────────────
        for i, row in enumerate(snapshot.get("source_channels", [])[:20]):
            channel = row.get("sessionDefaultChannelGroup", "")
            source  = row.get("sessionSource", "")
            medium  = row.get("sessionMedium", "")
            sessions = int(row.get("sessions", 0))
            doc_text = (
                f"channel: {channel} | source: {source} | medium: {medium} | "
                f"sessions: {sessions} | users: {int(row.get('activeUsers', 0))} | "
                f"bounce: {round(float(row.get('bounceRate', 0))*100,2)}%"
            )
            documents.append(doc_text)
            metadatas.append({
                "type":       "ga_channel",
                "snap_id":    snap_id,
                "fetch_date": fetch_date,
                "channel":    channel,
                "source":     source,
                "medium":     medium,
                "sessions":   sessions,
            })
            ids.append(f"{snap_id}_channel_{i}")

        # ── Geo rows ──────────────────────────────────────────────────────
        for i, row in enumerate(snapshot.get("geo", [])[:20]):
            country  = row.get("country", "")
            sessions = int(row.get("sessions", 0))
            doc_text = (
                f"country: {country} | sessions: {sessions} | "
                f"users: {int(row.get('activeUsers', 0))} | "
                f"bounce: {round(float(row.get('bounceRate', 0))*100,2)}%"
            )
            documents.append(doc_text)
            metadatas.append({
                "type":       "ga_geo",
                "snap_id":    snap_id,
                "fetch_date": fetch_date,
                "country":    country,
                "sessions":   sessions,
            })
            ids.append(f"{snap_id}_geo_{i}")

        # ── Summary document ──────────────────────────────────────────────
        ov = summary.get("overview", {})
        ec = summary.get("ecommerce", {})
        summary_text = (
            f"GA4 snapshot {fetch_date}: "
            f"sessions={ov.get('total_sessions',0)}, "
            f"users={ov.get('total_users',0)}, "
            f"pageviews={ov.get('total_pageviews',0)}, "
            f"organic_sessions={ov.get('organic_sessions',0)} ({ov.get('organic_pct',0)}%), "
            f"avg_bounce={ov.get('avg_bounce_rate_pct',0)}%, "
            f"avg_duration={ov.get('avg_session_duration_sec',0)}s, "
            f"transactions={ec.get('total_transactions',0)}, "
            f"revenue=${ec.get('total_revenue_usd',0)}"
        )
        documents.append(summary_text)
        metadatas.append({
            "type":             "ga_summary",
            "snap_id":          snap_id,
            "fetch_date":       fetch_date,
            "total_sessions":   ov.get("total_sessions", 0),
            "total_users":      ov.get("total_users", 0),
            "total_pageviews":  ov.get("total_pageviews", 0),
            "organic_sessions": ov.get("organic_sessions", 0),
            "transactions":     ec.get("total_transactions", 0),
            "revenue_usd":      ec.get("total_revenue_usd", 0),
            "full_summary":     json.dumps(summary)[:8000],
        })
        ids.append(f"{snap_id}_summary")

        # Upsert in batches of 100
        for start_i in range(0, len(documents), 100):
            col.upsert(
                documents=documents[start_i: start_i + 100],
                metadatas=metadatas[start_i: start_i + 100],
                ids=ids[start_i: start_i + 100],
            )

        logger.info(f"Stored GA4 snapshot {snap_id}: {len(documents)} documents")
        return snap_id

    def get_latest_ga_summary(self) -> Optional[dict]:
        """Return the most recent GA4 summary stats."""
        col = self._col(cfg.CHROMA_COLLECTION_GA)
        try:
            results = col.get(
                where={"type": {"$eq": "ga_summary"}},
                include=["documents", "metadatas"],
            )
            if not results["ids"]:
                return None
            pairs = list(zip(results["metadatas"], results["documents"]))
            pairs.sort(key=lambda x: x[0].get("fetch_date", ""), reverse=True)
            meta, doc = pairs[0]
            try:
                return json.loads(meta.get("full_summary", "{}"))
            except Exception:
                return {"summary_text": doc, "metadata": meta}
        except Exception as e:
            logger.warning(f"get_latest_ga_summary error: {e}")
            return None

    def search_ga_data(self, query: str, n: int = 10, data_type: str = None) -> list[dict]:
        """Semantic search across GA4 data (pages, channels, geo)."""
        col = self._col(cfg.CHROMA_COLLECTION_GA)
        if col.count() == 0:
            return []
        kwargs = dict(
            query_texts=[query],
            n_results=min(n, col.count()),
            include=["documents", "metadatas", "distances"],
        )
        if data_type:
            kwargs["where"] = {"type": {"$eq": f"ga_{data_type}"}}
        results = col.query(**kwargs)
        return [
            {"document": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    def get_ga_page_metrics(self, page_path: str) -> Optional[dict]:
        """Fetch GA4 metrics for a specific page path from the latest snapshot."""
        col = self._col(cfg.CHROMA_COLLECTION_GA)
        try:
            results = col.get(
                where={"type": {"$eq": "ga_page"}},
                include=["documents", "metadatas"],
            )
            if not results["ids"]:
                return None
            # Filter by page_path and get most recent
            matches = [
                m for m in results["metadatas"]
                if page_path in m.get("page_path", "")
            ]
            if not matches:
                return None
            matches.sort(key=lambda m: m.get("fetch_date", ""), reverse=True)
            return matches[0]
        except Exception as e:
            logger.warning(f"get_ga_page_metrics error: {e}")
            return None

    def list_ga_snapshot_dates(self) -> list[str]:
        """Return all unique GA4 snapshot fetch dates."""
        col = self._col(cfg.CHROMA_COLLECTION_GA)
        try:
            results = col.get(
                where={"type": {"$eq": "ga_summary"}},
                include=["metadatas"],
            )
            return sorted(
                {m["fetch_date"] for m in results["metadatas"]}, reverse=True
            )
        except Exception:
            return []

    # ── Action Items ───────────────────────────────────────────────────────

    def create_action_item(
        self,
        action_type: str,
        priority: str,
        title: str,
        description: str,
        target_url: str = "",
        target_keyword: str = "",
        implementation_data: dict = None,
        snapshot_id: str = "",
        data_signals: dict = None,
    ) -> str:
        """Store a new action item. Returns action_id."""
        col = self._col(cfg.CHROMA_COLLECTION_ACTIONS)
        action_id = f"action_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat()

        doc_text = (
            f"[{priority.upper()}] {action_type}: {title}\n"
            f"Target: {target_url or target_keyword}\n"
            f"Description: {description}"
        )

        signals = data_signals or {}
        col.upsert(
            documents=[doc_text],
            metadatas=[
                {
                    "action_id": action_id,
                    "action_type": action_type,
                    "priority": priority,
                    "title": title,
                    "description": description,
                    "target_url": target_url,
                    "target_keyword": target_keyword,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                    "snapshot_id": snapshot_id,
                    "implementation_data": json.dumps(implementation_data or {}),
                    "result": "",
                    "error": "",
                    # Baseline metrics for post-implementation impact comparison
                    "baseline_clicks":       float(signals.get("gsc_clicks", signals.get("clicks", 0))),
                    "baseline_impressions":  float(signals.get("gsc_impressions", signals.get("impressions", 0))),
                    "baseline_ctr":          float(signals.get("gsc_ctr_pct", signals.get("ctr", 0))),
                    "baseline_position":     float(signals.get("gsc_position", signals.get("position", 0))),
                    "baseline_ga4_pageviews": float(signals.get("ga4_pageviews", 0)),
                    "baseline_ga4_bounce":   float(signals.get("ga4_bounce_pct", 0)),
                    "conversion_revenue":    float(signals.get("conversion_revenue", 0)),
                    "backup": "",
                    "impact_measured_at": "",
                    "impact_data": "",
                }
            ],
            ids=[action_id],
        )
        return action_id

    def try_create_action_item(
        self,
        action_type: str,
        priority: str,
        title: str,
        description: str,
        target_url: str = "",
        target_keyword: str = "",
        implementation_data: dict = None,
        snapshot_id: str = "",
        data_signals: dict = None,
    ) -> str | None:
        """
        Best-effort action creation.
        Returns action_id on success, otherwise None without raising.
        """
        try:
            return self.create_action_item(
                action_type=action_type,
                priority=priority,
                title=title,
                description=description,
                target_url=target_url,
                target_keyword=target_keyword,
                implementation_data=implementation_data,
                snapshot_id=snapshot_id,
                data_signals=data_signals,
            )
        except Exception as e:
            logger.warning(f"try_create_action_item failed: {e}")
            return None

    def update_action_status(
        self,
        action_id: str,
        status: str,
        result: str = "",
        error: str = "",
        backup: dict = None,
    ):
        """Update action item status (pending → in_progress → done / failed / rolled_back)."""
        col = self._col(cfg.CHROMA_COLLECTION_ACTIONS)
        existing = col.get(ids=[action_id], include=["documents", "metadatas"])
        if not existing["ids"]:
            logger.warning(f"Action {action_id} not found")
            return

        meta = existing["metadatas"][0]
        current_status = meta.get("status", "pending")

        # Prevent races: only transition to in_progress from pending
        if status == "in_progress" and current_status != "pending":
            logger.warning(
                f"Skipping transition to in_progress for {action_id} because current status is {current_status}"
            )
            return

        # Don't overwrite a rolled_back state unless we are explicitly setting rolled_back
        if current_status == "rolled_back" and status != "rolled_back":
            logger.warning(f"Not overwriting rolled_back state for {action_id}")
            return

        meta["status"] = status
        meta["updated_at"] = datetime.utcnow().isoformat()
        meta["result"] = result
        meta["error"] = error
        if backup is not None:
            meta["backup"] = json.dumps(backup)

        col.update(
            ids=[action_id],
            documents=existing["documents"],
            metadatas=[meta],
        )

    def get_action_backup(self, action_id: str) -> dict:
        """Retrieve stored pre-change backup for an action (post_id + field values)."""
        col = self._col(cfg.CHROMA_COLLECTION_ACTIONS)
        try:
            existing = col.get(ids=[action_id], include=["metadatas"])
            if not existing["ids"]:
                return {}
            backup_str = existing["metadatas"][0].get("backup", "")
            if not backup_str:
                return {}
            return json.loads(backup_str)
        except Exception as e:
            logger.warning(f"get_action_backup error for {action_id}: {e}")
            return {}

    def get_actions_for_impact_check(self, min_days_old: int = 7, limit: int = 30) -> list[dict]:
        """
        Return done actions that:
          - were completed at least min_days_old days ago
          - have a target_url (something to re-check in GSC)
          - have not yet been impact-measured
        """
        col = self._col(cfg.CHROMA_COLLECTION_ACTIONS)
        try:
            results = col.get(
                where={"status": {"$eq": "done"}},
                include=["documents", "metadatas"],
            )
            cutoff = (datetime.utcnow() - timedelta(days=min_days_old)).isoformat()
            items = []
            for doc, meta in zip(results["documents"], results["metadatas"]):
                if meta.get("updated_at", "") > cutoff:
                    continue  # too recent — not enough time for GSC to show impact
                if meta.get("impact_measured_at"):
                    continue  # already measured
                if not meta.get("target_url"):
                    continue  # nothing to query in GSC
                meta["implementation_data"] = json.loads(meta.get("implementation_data", "{}"))
                items.append({"document": doc, "metadata": meta})
            # Oldest first so we measure longest-standing changes
            items.sort(key=lambda x: x["metadata"].get("updated_at", ""))
            return items[:limit]
        except Exception as e:
            logger.error(f"get_actions_for_impact_check error: {e}")
            return []

    def update_action_impact(self, action_id: str, impact_data: dict):
        """Store post-implementation GSC impact measurements on a completed action."""
        col = self._col(cfg.CHROMA_COLLECTION_ACTIONS)
        try:
            existing = col.get(ids=[action_id], include=["documents", "metadatas"])
            if not existing["ids"]:
                return
            meta = existing["metadatas"][0]
            meta["impact_measured_at"] = datetime.utcnow().isoformat()
            meta["impact_data"] = json.dumps(impact_data)[:2000]
            col.update(ids=[action_id], documents=existing["documents"], metadatas=[meta])
        except Exception as e:
            logger.warning(f"update_action_impact error for {action_id}: {e}")

    def get_pending_actions(self, limit: int = 20) -> list[dict]:
        """Return pending action items ordered by priority."""
        col = self._col(cfg.CHROMA_COLLECTION_ACTIONS)
        try:
            results = col.get(
                where={"status": {"$eq": "pending"}},
                include=["documents", "metadatas"],
                limit=limit,
            )
            items = []
            for doc, meta in zip(results["documents"], results["metadatas"]):
                meta["implementation_data"] = json.loads(
                    meta.get("implementation_data", "{}")
                )
                items.append({"document": doc, "metadata": meta})

            priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            items.sort(
                key=lambda x: priority_order.get(
                    x["metadata"].get("priority", "low"), 4
                )
            )
            return items
        except Exception as e:
            logger.error(f"get_pending_actions error: {e}")
            return []

    def get_all_actions(self, status: str = None) -> list[dict]:
        """Return all action items, optionally filtered by status."""
        col = self._col(cfg.CHROMA_COLLECTION_ACTIONS)
        try:
            where = {"status": {"$eq": status}} if status else None
            kwargs = {"include": ["documents", "metadatas"]}
            if where:
                kwargs["where"] = where
            results = col.get(**kwargs)
            items = []
            for doc, meta in zip(results["documents"], results["metadatas"]):
                meta["implementation_data"] = json.loads(
                    meta.get("implementation_data", "{}")
                )
                items.append({"document": doc, "metadata": meta})
            items.sort(key=lambda x: x["metadata"].get("created_at", ""), reverse=True)
            return items
        except Exception as e:
            logger.error(f"get_all_actions error: {e}")
            return []

    # ── Analysis Reports ───────────────────────────────────────────────────

    def store_analysis_report(self, report: dict) -> str:
        """Store an analysis report from the LLM."""
        col = self._col(cfg.CHROMA_COLLECTION_REPORTS)
        report_id = f"report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        doc_text = report.get("summary", json.dumps(report)[:2000])

        col.upsert(
            documents=[doc_text],
            metadatas=[
                {
                    "report_id": report_id,
                    "created_at": datetime.utcnow().isoformat(),
                    "snapshot_id": report.get("snapshot_id", ""),
                    "fetch_date": report.get("fetch_date", ""),
                    "action_count": report.get("action_count", 0),
                    "llm_provider": report.get("llm_provider", "unknown"),
                    "full_report": json.dumps(report)[:50000],
                }
            ],
            ids=[report_id],
        )
        return report_id

    def get_latest_report(self) -> Optional[dict]:
        """Return the most recent analysis report."""
        col = self._col(cfg.CHROMA_COLLECTION_REPORTS)
        try:
            results = col.get(include=["documents", "metadatas"])
            if not results["ids"]:
                return None
            # Sort by created_at desc
            pairs = list(zip(results["metadatas"], results["documents"]))
            pairs.sort(key=lambda x: x[0].get("created_at", ""), reverse=True)
            meta, doc = pairs[0]
            try:
                return json.loads(meta.get("full_report", "{}"))
            except Exception:
                return {"summary": doc, "metadata": meta}
        except Exception as e:
            logger.error(f"get_latest_report error: {e}")
            return None

    def get_report_history(self, limit: int = 30) -> list[dict]:
        """Return recent report summaries."""
        col = self._col(cfg.CHROMA_COLLECTION_REPORTS)
        try:
            results = col.get(include=["metadatas"])
            items = results["metadatas"]
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return items[:limit]
        except Exception:
            return []

    # ── Page Cache ─────────────────────────────────────────────────────────

    def upsert_page(self, url: str, metadata: dict):
        """Cache page metadata (title, description, etc.) for fast lookup."""
        col = self._col(cfg.CHROMA_COLLECTION_PAGES)
        page_id = f"page_{abs(hash(url)) % 10**12}"
        doc_text = (
            f"URL: {url}\n"
            f"Title: {metadata.get('title', '')}\n"
            f"Description: {metadata.get('description', '')}\n"
            f"H1: {metadata.get('h1', '')}"
        )
        meta_flat = {k: str(v)[:500] for k, v in metadata.items()}
        meta_flat["url"] = url
        col.upsert(documents=[doc_text], metadatas=[meta_flat], ids=[page_id])

    def upsert_reference_doc(self, url: str, title: str, content: str, metadata: dict | None = None):
        """Store a reference document (e.g. Google Search Central guidance)."""
        col = self._col(cfg.CHROMA_COLLECTION_PAGES)
        doc_id = f"ref_{abs(hash(url)) % 10**12}"
        meta = {
            "url": url,
            "title": title[:500],
            "type": "reference_doc",
            "source": (metadata or {}).get("source", "reference_doc"),
            "content_preview": content[:1000],
        }
        for key, value in (metadata or {}).items():
            meta[key] = str(value)[:500]
        doc_text = f"Title: {title}\nURL: {url}\n\n{content[:20000]}"
        col.upsert(documents=[doc_text], metadatas=[meta], ids=[doc_id])

    def search_pages(self, query: str, n: int = 5) -> list[dict]:
        """Search page cache by semantic similarity."""
        col = self._col(cfg.CHROMA_COLLECTION_PAGES)
        if col.count() == 0:
            return []
        results = col.query(
            query_texts=[query],
            n_results=min(n, col.count()),
            include=["documents", "metadatas"],
        )
        return [
            {"document": d, "metadata": m}
            for d, m in zip(results["documents"][0], results["metadatas"][0])
        ]

    def search_reference_docs(self, query: str, n: int = 8, source: str | None = None) -> list[dict]:
        """Search ingested reference docs by semantic similarity."""
        col = self._col(cfg.CHROMA_COLLECTION_PAGES)
        if col.count() == 0:
            return []
        where: dict[str, object] = {"type": {"$eq": "reference_doc"}}
        if source:
            where["source"] = {"$eq": source}
        try:
            results = col.query(
                query_texts=[query],
                n_results=min(n, col.count()),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            return [
                {"document": doc, "metadata": meta, "distance": dist}
                for doc, meta, dist in zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                )
            ]
        except Exception as exc:
            logger.warning(f"search_reference_docs semantic query failed, falling back to lexical scan: {exc}")
            rows = col.get(where=where, include=["documents", "metadatas"])
            query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
            scored = []
            for doc, meta in zip(rows.get("documents", []), rows.get("metadatas", [])):
                haystack = f"{meta.get('title', '')} {meta.get('content_preview', '')} {doc}".lower()
                haystack_terms = set(re.findall(r"[a-z0-9]+", haystack))
                overlap = len(query_terms & haystack_terms)
                if overlap <= 0:
                    continue
                scored.append(
                    {
                        "document": doc,
                        "metadata": meta,
                        "distance": round(1 / overlap, 4),
                        "_score": overlap,
                    }
                )
            scored.sort(key=lambda item: item["_score"], reverse=True)
            return [
                {k: v for k, v in item.items() if k != "_score"}
                for item in scored[:n]
            ]

    def list_reference_doc_sources(self) -> list[str]:
        """List known reference-doc sources stored in ChromaDB."""
        col = self._col(cfg.CHROMA_COLLECTION_PAGES)
        try:
            results = col.get(where={"type": {"$eq": "reference_doc"}}, include=["metadatas"])
            return sorted({m.get("source", "reference_doc") for m in results["metadatas"]})
        except Exception:
            return []

    # ── Telemetry / Metrics ─────────────────────────────────────────────────

    def record_metric(self, event_type: str, payload: dict) -> str:
        """Record a generic metric event into ChromaDB metrics collection.

        Returns the metric_id stored.
        """
        try:
            col = self._col(cfg.CHROMA_COLLECTION_METRICS)
        except Exception:
            # If metrics collection not configured, fall back to reports collection
            col = self._col(cfg.CHROMA_COLLECTION_REPORTS)

        metric_id = f"metric_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        doc_text = f"{event_type}: {json.dumps(payload, default=str)[:2000]}"
        metadata = {
            "type": "metric",
            "event_type": event_type,
            "payload": json.dumps(payload, default=str)[:8000],
            "created_at": datetime.utcnow().isoformat(),
        }
        try:
            col.upsert(documents=[doc_text], metadatas=[metadata], ids=[metric_id])
            logger.info(f"Recorded metric {event_type} id={metric_id}")
        except Exception as e:
            logger.warning(f"Failed to upsert metric {event_type}: {e}")
        return metric_id

    def record_llm_event(self, provider: str, event: str, message: str = "", details: dict = None) -> str:
        """Convenience wrapper to record LLM provider events.

        event examples: success, failure, disabled, analysis_generated
        """
        payload = {
            "provider": provider,
            "event": event,
            "message": (message or "")[:2000],
            "details": details or {},
        }
        return self.record_metric("llm_event", payload)

    def set_provider_status(self, provider: str, disabled: bool, reason: str = None, disabled_until: Optional[datetime] = None) -> str:
        """Persist provider disable/enabled status into metrics collection (provider_status type). Returns status_id."""
        try:
            col = self._col(cfg.CHROMA_COLLECTION_METRICS)
        except Exception:
            col = self._col(cfg.CHROMA_COLLECTION_REPORTS)
        status_id = f"provider_status_{provider}"
        metadata = {
            "type": "provider_status",
            "provider": provider,
            "disabled": bool(disabled),
            "reason": (reason or "")[:2000],
            "disabled_until": disabled_until.isoformat() if disabled_until else None,
            "updated_at": datetime.utcnow().isoformat(),
        }
        doc_text = f"provider_status: {provider} disabled={disabled} reason={reason or ''}"
        try:
            col.upsert(documents=[doc_text], metadatas=[metadata], ids=[status_id])
            logger.info(f"Set provider status {provider} disabled={disabled}")
        except Exception as e:
            logger.warning(f"Failed to upsert provider status {provider}: {e}")
        return status_id

    def get_provider_status(self, provider: str) -> dict:
        """Return provider status dict: {provider, disabled, reason, disabled_until}.
        Expire disabled status if disabled_until passed.
        """
        try:
            col = self._col(cfg.CHROMA_COLLECTION_METRICS)
        except Exception:
            col = self._col(cfg.CHROMA_COLLECTION_REPORTS)
        status_id = f"provider_status_{provider}"
        try:
            res = col.get(ids=[status_id], include=["metadatas", "documents"]) or {}
            metas = res.get("metadatas", [])
            if metas and metas[0]:
                meta = metas[0]
                disabled = bool(meta.get("disabled", False))
                disabled_until = meta.get("disabled_until")
                reason = meta.get("reason") or ""
                if disabled and disabled_until:
                    try:
                        dt = datetime.fromisoformat(disabled_until)
                        if dt < datetime.utcnow():
                            # expired
                            self.set_provider_status(provider, False, reason="expired")
                            return {"provider": provider, "disabled": False, "reason": reason, "disabled_until": None}
                    except Exception:
                        pass
                return {"provider": provider, "disabled": disabled, "reason": reason, "disabled_until": disabled_until}
        except Exception:
            pass
        return {"provider": provider, "disabled": False, "reason": None, "disabled_until": None}

    def list_provider_statuses(self) -> list:
        """List all provider status records."""
        try:
            col = self._col(cfg.CHROMA_COLLECTION_METRICS)
        except Exception:
            col = self._col(cfg.CHROMA_COLLECTION_REPORTS)
        try:
            res = col.get(where={"type": {"$eq": "provider_status"}}, include=["metadatas", "documents"]) or {}
            metas = res.get("metadatas", [])
            docs = res.get("documents", [])
            out = []
            for idx, meta in enumerate(metas):
                p = meta.get("provider")
                disabled = bool(meta.get("disabled", False))
                disabled_until = meta.get("disabled_until")
                reason = meta.get("reason")
                if disabled and disabled_until:
                    try:
                        dt = datetime.fromisoformat(disabled_until)
                        if dt < datetime.utcnow():
                            # expire
                            self.set_provider_status(p, False, reason="expired")
                            disabled = False
                            disabled_until = None
                    except Exception:
                        pass
                out.append({"provider": p, "disabled": disabled, "reason": reason, "disabled_until": disabled_until, "doc": docs[idx] if idx < len(docs) else None})
            return out
        except Exception:
            return []

    def get_llm_metrics_summary(self, limit: int = 1000) -> dict:
        """Aggregate LLM events by provider and event type (success/failure/disabled).
        Returns {provider: {success: n, failure: n, disabled: n, total: n}}
        """
        try:
            col = self._col(cfg.CHROMA_COLLECTION_METRICS)
        except Exception:
            col = self._col(cfg.CHROMA_COLLECTION_REPORTS)
        try:
            res = col.get(where={"event_type": {"$eq": "llm_event"}}, include=["metadatas"]) or {}
            metas = res.get("metadatas", [])
            summary = {}
            for meta in metas[:limit]:
                payload = meta.get("payload")
                try:
                    payload_obj = json.loads(payload) if isinstance(payload, str) else (payload or {})
                except Exception:
                    payload_obj = {}
                provider = payload_obj.get("provider")
                event = payload_obj.get("event")
                if not provider or not event:
                    continue
                s = summary.setdefault(provider, {"success": 0, "failure": 0, "disabled": 0, "total": 0})
                s["total"] += 1
                if event in s:
                    s[event] += 1
                else:
                    s[event] = s.get(event, 0) + 1
            return summary
        except Exception as e:
            logger.warning(f"Failed to aggregate llm metrics: {e}")
            return {}

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        def _safe_count(collection_name: str) -> int:
            try:
                return int(self._col(collection_name).count())
            except Exception as e:
                logger.warning(f"stats: failed count for {collection_name}: {e}")
                return 0

        reference_docs_count = 0
        try:
            reference_docs_count = len(
                self._col(cfg.CHROMA_COLLECTION_PAGES).get(
                    where={"type": {"$eq": "reference_doc"}},
                    include=["metadatas"],
                )["metadatas"]
            )
        except Exception:
            reference_docs_count = 0

        return {
            "gsc_data_count":    _safe_count(cfg.CHROMA_COLLECTION_GSC),
            "ga_data_count":     _safe_count(cfg.CHROMA_COLLECTION_GA),
            "action_items_count": _safe_count(cfg.CHROMA_COLLECTION_ACTIONS),
            "reports_count":     _safe_count(cfg.CHROMA_COLLECTION_REPORTS),
            "pages_count":       _safe_count(cfg.CHROMA_COLLECTION_PAGES),
            "metrics_count":     _safe_count(cfg.CHROMA_COLLECTION_METRICS),
            "reference_docs_count": reference_docs_count,
            "snapshot_dates":    self.list_snapshot_dates()[:5],
            "ga_snapshot_dates": self.list_ga_snapshot_dates()[:5],
        }


# Singleton
vector_store = VectorStore()
