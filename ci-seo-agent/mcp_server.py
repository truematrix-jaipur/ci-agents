"""
CI SEO Agent — MCP Server
Exposes the agent's data and control surface as MCP tools + resources.
Runs as an ASGI app mounted at /mcp on the main FastAPI server.

Connect from Claude Code settings.json:
  "mcpServers": {
    "ci-seo-agent": {
      "type": "http",
      "url": "http://localhost:9001/mcp"
    }
  }
"""
import json
import logging
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource

from config import cfg
from mcp_config import build_http_mcp_config, build_stdio_mcp_config, sync_mcp_config_files
from reference_docs import reference_docs_trainer
from vector_store import vector_store

logger = logging.getLogger("ci.mcp")

# ── FastMCP instance ────────────────────────────────────────────────────────

mcp = FastMCP(
    name="ci-seo-agent",
    instructions=(
        "CI SEO Agent for IndogenMed.org. Provides access to Google Search Console + "
        "Google Analytics 4 data, SEO analysis reports, action items, and agent controls. "
        "Use get_agent_status first to understand the current state, then get_latest_report "
        "for SEO insights. All write actions (trigger_pipeline, approve_report) require a "
        "configured API secret."
    ),
)


# ── Helper ──────────────────────────────────────────────────────────────────

def _state() -> dict:
    """Import here to avoid circular import at module load time."""
    from scheduler import get_state
    return get_state()


def _ensure_vector_store():
    """Ensure ChromaDB is initialized for stdio/standalone MCP runs."""
    if getattr(vector_store, "_client", None) is None:
        vector_store.init()


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool(description="Get current CI SEO agent status, scheduled jobs, and ChromaDB collection counts.")
def get_agent_status() -> dict:
    """Current agent status snapshot."""
    _ensure_vector_store()
    import scheduler as sched
    state = sched.get_state()
    stats = vector_store.stats()
    return {
        "status":       state.get("status", "unknown"),
        "last_run":     state.get("last_run"),
        "last_error":   state.get("last_error"),
        "runs_today":   state.get("runs_today", 0),
        "pending_approval_report_id": state.get("pending_approval_report_id"),
        "chroma_stats": stats,
        "timestamp":    datetime.utcnow().isoformat(),
    }


@mcp.tool(description="Get the latest combined GSC + GA4 SEO analysis report including action plan and key findings.")
def get_latest_report() -> dict:
    """Return the most recent LLM analysis report."""
    _ensure_vector_store()
    report = vector_store.get_latest_report()
    if not report:
        return {"error": "No reports yet. Use trigger_pipeline to run the first analysis."}
    # Return a trimmed version to stay within token limits
    return {
        "report_id":       report.get("report_id", "unknown"),
        "analyzed_at":     report.get("analyzed_at", ""),
        "fetch_date":      report.get("fetch_date", ""),
        "executive_summary": report.get("executive_summary", report.get("summary", "")),
        "key_findings":    report.get("key_findings", [])[:10],
        "action_plan":     report.get("action_plan", [])[:10],
        "quick_wins":      report.get("quick_wins", []),
        "monitoring_alerts": report.get("monitoring_alerts", []),
        "ga4_insights":    report.get("ga4_insights", {}),
        "gsc_metrics":     report.get("gsc_metrics", {}),
        "ga4_metrics":     report.get("ga4_metrics", {}),
        "action_count":    report.get("action_count", 0),
    }


@mcp.tool(
    description=(
        "Get SEO action items. Filter by status: pending, in_progress, done, failed, blocked, rejected. "
        "Leave status empty for all actions."
    )
)
def get_pending_actions(status: str = "pending", limit: int = 20) -> dict:
    """Return action items filtered by status."""
    _ensure_vector_store()
    if status:
        items = vector_store.get_all_actions(status=status)
    else:
        items = vector_store.get_all_actions()
    trimmed = []
    for item in items[:limit]:
        m = item.get("metadata", {})
        trimmed.append({
            "action_id":    m.get("action_id"),
            "action_type":  m.get("action_type"),
            "priority":     m.get("priority"),
            "title":        m.get("title"),
            "description":  m.get("description", "")[:200],
            "target_url":   m.get("target_url"),
            "status":       m.get("status"),
            "created_at":   m.get("created_at"),
            "updated_at":   m.get("updated_at"),
            "result":       m.get("result", "")[:200],
            "error":        m.get("error", "")[:200],
        })
    return {"count": len(trimmed), "status_filter": status, "actions": trimmed}


@mcp.tool(description="Get the latest Google Analytics 4 summary: sessions, users, pageviews, bounce rate, top pages, channels, geo, e-commerce.")
def get_ga4_metrics() -> dict:
    """Return latest GA4 summary from vector store."""
    _ensure_vector_store()
    summary = vector_store.get_latest_ga_summary()
    if not summary:
        return {"error": "No GA4 data yet. Use trigger_pipeline to fetch GA4 data."}
    # Return structured overview
    ov = summary.get("overview", {})
    return {
        "fetched_at":       summary.get("fetch_date", "unknown"),
        "overview":         ov,
        "ecommerce":        summary.get("ecommerce", {}),
        "top_pages":        summary.get("top_pages_by_views", [])[:10],
        "high_bounce_pages": summary.get("high_bounce_pages", [])[:10],
        "top_channels":     summary.get("top_channels", [])[:8],
        "top_geos":         summary.get("top_geos", [])[:10],
        "devices":          summary.get("devices", []),
        "user_retention":   summary.get("user_retention", {}),
    }


@mcp.tool(description="Get GSC metrics: top keywords, top pages, low-CTR keywords from the latest snapshot.")
def get_gsc_metrics() -> dict:
    """Return latest GSC summary stats from vector store."""
    _ensure_vector_store()
    summary = vector_store.get_previous_snapshot_summary(days_ago=0)
    if not summary:
        return {"error": "No GSC data yet. Use trigger_pipeline to fetch GSC data."}
    return {
        "query_summary":       summary.get("query_summary", {}),
        "top_keywords":        summary.get("top_keywords", [])[:15],
        "low_ctr_keywords":    summary.get("low_ctr_keywords", [])[:15],
        "top_pages":           summary.get("top_pages", [])[:10],
        "underperforming_pages": summary.get("underperforming_pages", [])[:10],
    }


@mcp.tool(description="Semantic search across all SEO data in ChromaDB (GSC keywords, GA4 pages, reports, actions).")
def search_seo_data(query: str, collection: str = "gsc", n: int = 8) -> dict:
    """
    Search ChromaDB collections semantically.
    collection: gsc | ga | actions (default: gsc)
    """
    _ensure_vector_store()
    if collection == "ga":
        results = vector_store.search_ga_data(query, n=n)
    elif collection == "actions":
        col = vector_store._col(cfg.CHROMA_COLLECTION_ACTIONS)
        if col.count() == 0:
            return {"query": query, "results": []}
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
        results = vector_store.search_similar_keywords(query, n=n)

    return {
        "query":      query,
        "collection": collection,
        "count":      len(results),
        "results": [
            {
                "document": r.get("document", "")[:300],
                "distance": round(r.get("distance", 0), 4),
                "metadata": {
                    k: v for k, v in r.get("metadata", {}).items()
                    if k not in ("full_summary", "full_report", "implementation_data")
                },
            }
            for r in results
        ],
    }


@mcp.tool(description="Get GA4 engagement metrics for a specific page path.")
def get_page_analytics(page_path: str) -> dict:
    """Return GA4 metrics for a specific page from the vector store."""
    _ensure_vector_store()
    meta = vector_store.get_ga_page_metrics(page_path)
    if not meta:
        return {"error": f"No GA4 data found for page path: {page_path}"}
    return meta


@mcp.tool(description="Search trained Google Search Central / Rich Results reference docs semantically.")
def search_reference_docs(query: str, source: str = "", n: int = 8) -> dict:
    """Search ingested Google reference documents."""
    _ensure_vector_store()
    results = vector_store.search_reference_docs(query, n=n, source=source or None)
    return {
        "query": query,
        "source": source or None,
        "count": len(results),
        "results": [
            {
                "document": r.get("document", "")[:500],
                "distance": round(r.get("distance", 0), 4),
                "metadata": r.get("metadata", {}),
            }
            for r in results
        ],
    }


@mcp.tool(
    description=(
        "Crawl and train the agent on Google Search Central / Rich Results docs. "
        "Requires API secret."
    )
)
def train_reference_docs(api_secret: str, max_pages: int = 120, max_depth: int = 3) -> dict:
    """Train the vector store on Google documentation."""
    if not cfg.API_SECRET:
        return {"success": False, "error": "API secret is not configured"}
    if api_secret != cfg.API_SECRET:
        return {"success": False, "error": "Invalid API secret"}
    result = reference_docs_trainer.train_google_search_docs(
        max_pages=max_pages,
        max_depth=max_depth,
    )
    return {"success": True, "result": result}


@mcp.tool(
    description=(
        "Trigger the full SEO pipeline: fetch GSC + GA4 → LLM analysis → send approval email. "
        "Requires API secret. Only runs if agent is idle."
    )
)
def trigger_pipeline(api_secret: str) -> dict:
    """Trigger a full pipeline run in the background."""
    if not cfg.API_SECRET:
        return {"success": False, "error": "API secret is not configured"}
    if api_secret != cfg.API_SECRET:
        return {"success": False, "error": "Invalid API secret"}
    import scheduler as sched
    state = sched.get_state()
    if state.get("status") not in ("idle", "error"):
        return {
            "success": False,
            "error": f"Agent is busy: {state.get('status')}. Wait for it to return to idle.",
        }
    import threading
    t = threading.Thread(target=sched.step_fetch_and_analyze, daemon=True)
    t.start()
    return {
        "success": True,
        "message": "Pipeline started. Check get_agent_status for progress.",
    }


@mcp.tool(
    description=(
        "Trigger a supported local SEO-agent job from MCP. "
        "Allowed jobs: pipeline, implement, validate, extended, ga4_fetch, email_poll. "
        "Requires API secret."
    )
)
def trigger_agent_job(job: str, api_secret: str) -> dict:
    """Trigger a background agent job without needing direct shell access."""
    if not cfg.API_SECRET:
        return {"success": False, "error": "API secret is not configured"}
    if api_secret != cfg.API_SECRET:
        return {"success": False, "error": "Invalid API secret"}

    import scheduler as sched
    import threading

    if job == "pipeline":
        if sched.get_state().get("status") not in ("idle", "error"):
            return {"success": False, "error": f"Agent busy: {sched.get_state().get('status')}"}
        target = sched.step_fetch_and_analyze
        args = ()
    elif job == "implement":
        target = sched.step_implement_approved
        args = (sched.get_state().get("last_report_id", "manual"), "mcp_trigger")
    elif job == "validate":
        target = sched.step_validate_evening
        args = ()
    elif job == "extended":
        def _run_extended():
            from gsc_extended import gsc_extended
            from extended_analyzer import extended_analyzer
            _ensure_vector_store()
            snap_id = sched.get_state().get("last_snapshot_id", "manual")
            report = gsc_extended.fetch_full_extended_report(top_url_count=15)
            vector_store.store_analysis_report({
                **report,
                "type": "extended",
                "snapshot_id": snap_id,
                "summary": str(report.get("summary", {})),
                "fetch_date": report.get("generated_at", ""),
            })
            extended_analyzer.analyze_extended_report(report, snap_id)
        target = _run_extended
        args = ()
    elif job == "ga4_fetch":
        def _run_ga4_fetch():
            from ga_client import ga_client
            _ensure_vector_store()
            snapshot = ga_client.fetch_full_snapshot(days=cfg.GSC_DAYS_HISTORY)
            summary = ga_client.compute_summary_stats(snapshot)
            vector_store.store_ga_snapshot(snapshot, summary)
        target = _run_ga4_fetch
        args = ()
    elif job == "email_poll":
        def _run_email_poll():
            from mail_poller import mail_poller
            mail_poller.poll_all()
        target = _run_email_poll
        args = ()
    else:
        return {"success": False, "error": f"Unsupported job: {job}"}

    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return {"success": True, "job": job, "message": "Job started in background."}


@mcp.tool(
    description=(
        "Approve a pending SEO action plan. Starts implementation of approved actions. "
        "Requires API secret and report_id (from get_latest_report or get_agent_status)."
    )
)
def approve_report(report_id: str, api_secret: str) -> dict:
    """Approve a report and start implementation."""
    if not cfg.API_SECRET:
        return {"success": False, "error": "API secret is not configured"}
    if api_secret != cfg.API_SECRET:
        return {"success": False, "error": "Invalid API secret"}
    import scheduler as sched
    import threading
    sched.update_state(pending_approval_report_id=report_id)
    t = threading.Thread(
        target=sched.step_implement_approved,
        args=(report_id, "mcp_tool"),
        daemon=True,
    )
    t.start()
    return {
        "success": True,
        "message": f"Report {report_id} approved via MCP. Implementation starting in background.",
    }


@mcp.tool(description="Get last N lines from the agent log file.")
def get_agent_logs(lines: int = 50) -> dict:
    """Return recent log lines."""
    log_file = cfg.LOGS_DIR / "agent.log"
    if not log_file.exists():
        return {"lines": []}
    with open(log_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return {
        "lines": [l.rstrip() for l in all_lines[-lines:]],
        "total_lines": len(all_lines),
    }


@mcp.tool(description="List available report IDs and their dates from the analysis history.")
def list_reports(limit: int = 10) -> dict:
    """Return report history summaries."""
    _ensure_vector_store()
    history = vector_store.get_report_history(limit=limit)
    return {
        "count": len(history),
        "reports": [
            {
                "report_id":    r.get("report_id"),
                "created_at":   r.get("created_at"),
                "fetch_date":   r.get("fetch_date"),
                "action_count": r.get("action_count", 0),
                "type":         r.get("type", "standard"),
            }
            for r in history
        ],
    }


@mcp.tool(description="Return the synced MCP config so CLI agents/subagents can connect to this agent consistently.")
def get_mcp_config() -> dict:
    """Return the current stdio + HTTP MCP configuration and sync config files."""
    return sync_mcp_config_files()


# ══════════════════════════════════════════════════════════════════════════════
# RESOURCES
# ══════════════════════════════════════════════════════════════════════════════


@mcp.resource("seo://status")
def resource_status() -> str:
    """Current agent status as JSON string."""
    _ensure_vector_store()
    import scheduler as sched
    state = sched.get_state()
    stats = vector_store.stats()
    return json.dumps({"state": state, "chroma_stats": stats}, indent=2)


@mcp.resource("seo://latest-report")
def resource_latest_report() -> str:
    """Latest SEO analysis report as JSON string."""
    _ensure_vector_store()
    report = vector_store.get_latest_report()
    if not report:
        return json.dumps({"error": "No reports yet"})
    return json.dumps({
        "executive_summary": report.get("executive_summary", ""),
        "key_findings":      report.get("key_findings", []),
        "action_plan":       report.get("action_plan", []),
        "quick_wins":        report.get("quick_wins", []),
        "gsc_metrics":       report.get("gsc_metrics", {}),
        "ga4_metrics":       report.get("ga4_metrics", {}),
        "analyzed_at":       report.get("analyzed_at", ""),
    }, indent=2)


@mcp.resource("seo://pending-actions")
def resource_pending_actions() -> str:
    """Pending SEO action items as JSON string."""
    _ensure_vector_store()
    items = vector_store.get_pending_actions(limit=20)
    return json.dumps({
        "count":   len(items),
        "actions": [
            {k: v for k, v in item["metadata"].items()
             if k != "implementation_data"}
            for item in items
        ],
    }, indent=2)


@mcp.resource("seo://ga4-overview")
def resource_ga4_overview() -> str:
    """Latest GA4 traffic overview as JSON string."""
    _ensure_vector_store()
    summary = vector_store.get_latest_ga_summary()
    if not summary:
        return json.dumps({"error": "No GA4 data yet"})
    return json.dumps({
        "overview":     summary.get("overview", {}),
        "ecommerce":    summary.get("ecommerce", {}),
        "top_channels": summary.get("top_channels", [])[:5],
        "top_geos":     summary.get("top_geos", [])[:10],
        "devices":      summary.get("devices", []),
    }, indent=2)


@mcp.resource("seo://mcp-config")
def resource_mcp_config() -> str:
    """Current MCP connection configuration as JSON string."""
    return json.dumps(
        {
            "stdio": build_stdio_mcp_config(),
            "http": build_http_mcp_config(),
            "synced_paths": sync_mcp_config_files().get("written_paths", []),
        },
        indent=2,
    )


@mcp.resource("seo://reference-doc-sources")
def resource_reference_doc_sources() -> str:
    """List trained Google reference-document sources."""
    _ensure_vector_store()
    return json.dumps(
        {
            "reference_docs_count": vector_store.stats().get("reference_docs_count", 0),
            "sources": vector_store.list_reference_doc_sources(),
        },
        indent=2,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ══════════════════════════════════════════════════════════════════════════════


@mcp.prompt(description="Generate a prompt to analyze a specific page's SEO + GA4 performance.")
def analyze_page(page_url: str, primary_keyword: str = "") -> str:
    """Prompt template for per-page SEO analysis."""
    ga_meta = vector_store.get_ga_page_metrics(page_url.replace("https://indogenmed.org", ""))
    ga_text = ""
    if ga_meta:
        ga_text = (
            f"\n\nGA4 data for this page:\n"
            f"- Pageviews: {ga_meta.get('views', 'N/A')}\n"
            f"- Bounce rate: {ga_meta.get('bounce_rate', 'N/A')}%\n"
            f"- Avg session duration: {ga_meta.get('avg_duration', 'N/A')}s\n"
            f"- Engagement rate: {ga_meta.get('engagement', 'N/A')}%"
        )
    return (
        f"Analyze the SEO performance of this IndogenMed.org page:\n"
        f"URL: {page_url}\n"
        f"Primary keyword: {primary_keyword or 'not specified'}"
        f"{ga_text}\n\n"
        f"Use get_gsc_metrics and search_seo_data to find GSC data for this page. "
        f"Then recommend specific improvements (meta description, title, content, internal links)."
    )


@mcp.prompt(description="Generate a prompt to create an SEO + CRO action plan for IndogenMed.org.")
def generate_action_plan(focus: str = "quick wins") -> str:
    """Prompt template for generating a new action plan."""
    return (
        f"Generate an SEO + conversion rate optimisation action plan for IndogenMed.org.\n"
        f"Focus area: {focus}\n\n"
        f"Steps:\n"
        f"1. Call get_agent_status to check if data is fresh (run trigger_pipeline if last_run is stale)\n"
        f"2. Call get_latest_report for the current LLM analysis\n"
        f"3. Call get_ga4_metrics for user behaviour data\n"
        f"4. Call get_gsc_metrics for search visibility data\n"
        f"5. Cross-reference GSC impressions with GA4 bounce rates to find highest-impact pages\n"
        f"6. Return a prioritised action plan with specific implementation steps\n"
    )
