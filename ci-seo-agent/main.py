"""
CI SEO Agent — FastAPI Application
Endpoints for status, approval, manual triggers, history, and metrics.
"""
import json
import hmac
import hashlib
import base64
import logging
import logging.handlers
import os
import sys
import time
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Query, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import cfg
from mcp_config import build_stdio_mcp_config, build_http_mcp_config, sync_mcp_config_files
from reference_docs import reference_docs_trainer
from vector_store import vector_store
from scheduler import (
    create_scheduler,
    get_state,
    step_fetch_and_analyze,
    step_implement_approved,
    step_validate_evening,
    update_state,
)

# ── Logging Setup ──────────────────────────────────────────────────────────

cfg.LOGS_DIR.mkdir(parents=True, exist_ok=True)

log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Guard against duplicate handler registration on module reload / multiple workers
if not root_logger.handlers:
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    # Rotating file handler (10 MB × 10 files)
    file_handler = logging.handlers.RotatingFileHandler(
        cfg.LOGS_DIR / "agent.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)

# Separate action log — guard child logger handlers too
_implementer_logger = logging.getLogger("ci.implementer")
_scheduler_logger = logging.getLogger("ci.scheduler")
if not _implementer_logger.handlers:
    action_handler = logging.handlers.RotatingFileHandler(
        cfg.LOGS_DIR / "actions.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    action_handler.setFormatter(log_formatter)
    _implementer_logger.addHandler(action_handler)
    _scheduler_logger.addHandler(action_handler)

logger = logging.getLogger("ci.api")

# ── Lifespan ───────────────────────────────────────────────────────────────

_scheduler = None
_mcp_http_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    logger.info("=" * 70)
    logger.info("CI SEO AGENT STARTING")
    logger.info(f"API: http://{cfg.API_HOST}:{cfg.API_PORT}")
    logger.info(f"ChromaDB: {cfg.CHROMA_DIR}")
    logger.info(f"Logs: {cfg.LOGS_DIR}")
    logger.info("=" * 70)

    # Init ChromaDB
    vector_store.init()
    logger.info("ChromaDB initialized")

    # Sync shared MCP config so CLI agents and subagents can discover this server.
    sync_result = sync_mcp_config_files()
    logger.info(f"MCP config synced: {', '.join(sync_result['written_paths'])}")

    # Start scheduler
    _scheduler = create_scheduler()
    _scheduler.start()
    logger.info(
        f"Scheduler started — "
        f"fetch@{cfg.SCHEDULE_FETCH_HOUR:02d}:{cfg.SCHEDULE_FETCH_MINUTE:02d}UTC "
        f"implement@{cfg.SCHEDULE_IMPLEMENT_HOUR:02d}:{cfg.SCHEDULE_IMPLEMENT_MINUTE:02d}UTC "
        f"validate@{cfg.SCHEDULE_VALIDATE_HOUR:02d}:{cfg.SCHEDULE_VALIDATE_MINUTE:02d}UTC"
    )

    mcp_lifespan = None
    if _mcp_http_app is not None:
        mcp_lifespan = _mcp_http_app.router.lifespan_context(_mcp_http_app)
        await mcp_lifespan.__aenter__()
        logger.info("MCP HTTP lifespan started")

    try:
        yield
    finally:
        if mcp_lifespan is not None:
            await mcp_lifespan.__aexit__(None, None, None)
        if _scheduler and _scheduler.running:
            _scheduler.shutdown(wait=False)
        logger.info("CI SEO AGENT STOPPED")


# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CI SEO Agent",
    description=(
        "Autonomous SEO monitoring and implementation agent for IndogenMed.org. "
        "Fetches Google Search Console + Google Analytics 4 data daily, analyzes with Claude AI, "
        "sends action plan for approval, then implements on production WordPress. "
        "MCP server available at /mcp/ for AI agent connections."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# CORS so Claude Desktop / remote agents can reach the MCP endpoint
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CI_API_ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1").split(",") if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Secret"],
)

# ── Mount MCP server ────────────────────────────────────────────────────────
try:
    from mcp_server import mcp as _mcp_instance
    # FastMCP 1.26+ uses streamable_http_app() for HTTP transport
    _mcp_instance.settings.streamable_http_path = "/"
    _mcp_http_app = _mcp_instance.streamable_http_app()
    app.mount("/mcp", _mcp_http_app)
    logger.info("MCP server mounted at /mcp/ (Streamable HTTP transport)")
except Exception as _mcp_err:
    logger.warning(f"MCP server mount failed (non-fatal): {_mcp_err}")


def require_secret(x_api_secret: Optional[str] = Header(None)):
    if not cfg.API_SECRET:
        raise HTTPException(status_code=503, detail="API secret is not configured")
    if x_api_secret != cfg.API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API secret")


def _sign_report_action(report_id: str, action: str, expires_ts: int) -> str:
    payload = f"{report_id}:{action}:{expires_ts}"
    mac = hmac.new(cfg.API_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("utf-8").rstrip("=")


def generate_approval_token(report_id: str, action: str, ttl_seconds: int = 86400) -> str:
    expires_ts = int(time.time()) + ttl_seconds
    signature = _sign_report_action(report_id, action, expires_ts)
    return f"{expires_ts}.{signature}"


def verify_approval_token(report_id: str, action: str, token: str) -> bool:
    if not cfg.API_SECRET:
        return False
    try:
        expires_raw, signature = token.split(".", 1)
        expires_ts = int(expires_raw)
    except (ValueError, AttributeError):
        return False
    if expires_ts < int(time.time()):
        return False
    expected = _sign_report_action(report_id, action, expires_ts)
    return hmac.compare_digest(signature, expected)


# ── Models ─────────────────────────────────────────────────────────────────


class TriggerResponse(BaseModel):
    success: bool
    message: str
    job_id: Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def root():
    state = get_state()
    stats = vector_store.stats()
    jobs = []
    if _scheduler:
        for job in _scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append(
                f"<tr><td>{job.id}</td><td>{next_run.strftime('%Y-%m-%d %H:%M UTC') if next_run else 'N/A'}</td></tr>"
            )

    return f"""<!DOCTYPE html>
<html><head><title>CI SEO Agent</title>
<style>
  body{{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:20px;color:#333}}
  h1{{color:#2c3e50}} h2{{color:#2980b9;margin-top:30px}}
  .status{{display:inline-block;padding:5px 15px;border-radius:20px;font-weight:bold;font-size:13px}}
  .status-idle{{background:#27ae60;color:white}}
  .status-fetching,.status-analyzing,.status-implementing{{background:#f39c12;color:white}}
  .status-error{{background:#e74c3c;color:white}}
  .status-awaiting_approval{{background:#3498db;color:white}}
  table{{width:100%;border-collapse:collapse;margin:10px 0}}
  th{{background:#2c3e50;color:white;padding:8px;text-align:left;font-size:12px}}
  td{{padding:8px;border-bottom:1px solid #ecf0f1;font-size:13px}}
  .btn{{display:inline-block;padding:10px 20px;border-radius:5px;color:white;text-decoration:none;font-weight:bold;margin:5px}}
  .btn-blue{{background:#3498db}} .btn-green{{background:#27ae60}} .btn-orange{{background:#e67e22}}
  code{{background:#ecf0f1;padding:2px 6px;border-radius:3px;font-family:monospace;font-size:12px}}
</style>
</head>
<body>
<h1>🔍 CI SEO Agent</h1>
<p>Autonomous SEO monitoring for <strong>indogenmed.org</strong></p>

<p>Status: <span class="status status-{state.get('status','idle')}">{state.get('status','idle').upper()}</span>
   &nbsp;Last run: {state.get('last_run','Never')}</p>

<h2>Quick Actions</h2>
<a href="/docs" class="btn btn-blue">📖 API Docs</a>
<a href="/status" class="btn btn-blue">📊 Status JSON</a>
<a href="/report/latest" class="btn btn-blue">📋 Latest Report</a>
<a href="/actions" class="btn btn-blue">⚡ Action Items</a>

<h2>ChromaDB Stats</h2>
<table>
  <tr><th>Collection</th><th>Documents</th></tr>
  <tr><td>GSC Data</td><td>{stats.get('gsc_data_count',0):,}</td></tr>
  <tr><td>GA4 Data</td><td>{stats.get('ga_data_count',0):,}</td></tr>
  <tr><td>Action Items</td><td>{stats.get('action_items_count',0)}</td></tr>
  <tr><td>Analysis Reports</td><td>{stats.get('reports_count',0)}</td></tr>
  <tr><td>Page Cache</td><td>{stats.get('pages_count',0)}</td></tr>
</table>
<p>GSC snapshots: {', '.join(stats.get('snapshot_dates',[]) or ['None yet'])}</p>
<p>GA4 snapshots: {', '.join(stats.get('ga_snapshot_dates',[]) or ['None yet'])}</p>

<h2>MCP Server</h2>
<p>AI agents can connect to this agent via MCP:</p>
<code>http://localhost:{cfg.API_PORT}/mcp/</code>
&nbsp;&nbsp;<a href="/mcp-info" class="btn btn-orange">📡 MCP Info</a>

<h2>Scheduled Jobs</h2>
<table>
  <tr><th>Job</th><th>Next Run</th></tr>
  {''.join(jobs) or '<tr><td colspan=2>No jobs scheduled</td></tr>'}
</table>

<h2>Trigger Manual Run (requires API secret header)</h2>
<p>Use the <a href="/docs">API docs</a> to trigger runs manually.</p>
<code>curl -X POST http://localhost:{cfg.API_PORT}/run-now -H "X-API-Secret: &lt;your-secret&gt;"</code>
</body></html>"""


@app.get("/status")
def status(_=Depends(require_secret)):
    """Current agent status, state, and scheduler info."""
    state = get_state()
    stats = vector_store.stats()
    jobs = []
    if _scheduler:
        for job in _scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id": job.id,
                "next_run": next_run.isoformat() if next_run else None,
            })
    return {
        "agent": "CI SEO Agent v1.0",
        "site": "indogenmed.org",
        "state": state,
        "chroma_stats": stats,
        "scheduled_jobs": jobs,
        "api_port": cfg.API_PORT,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/report/latest")
def report_latest(_=Depends(require_secret)):
    """Return the most recent analysis report."""
    report = vector_store.get_latest_report()
    if not report:
        raise HTTPException(status_code=404, detail="No reports yet. Trigger /run-now first.")
    return report


@app.get("/report/history")
def report_history(limit: int = Query(30, ge=1, le=100), _=Depends(require_secret)):
    """Return report history (summaries, not full reports)."""
    return {"reports": vector_store.get_report_history(limit=limit)}


@app.get("/actions")
def list_actions(status: Optional[str] = Query(None), _=Depends(require_secret)):
    """List action items. Filter by status: pending, in_progress, done, failed, blocked."""
    items = vector_store.get_all_actions(status=status)
    return {
        "count": len(items),
        "status_filter": status,
        "actions": [a["metadata"] for a in items],
    }


@app.get("/metrics")
def metrics(_=Depends(require_secret)):
    """ChromaDB collection stats and agent metrics."""
    logger.info("/metrics called — collecting llm metrics and provider statuses")
    try:
        llm_summary = vector_store.get_llm_metrics_summary()
    except Exception as e:
        logger.warning(f"Failed to compute llm summary: {e}")
        llm_summary = {}
    try:
        provider_statuses = vector_store.list_provider_statuses()
    except Exception as e:
        logger.warning(f"Failed to list provider statuses: {e}")
        provider_statuses = []
    resp = {"chroma": vector_store.stats(), "state": get_state(), "llm_metrics": llm_summary, "provider_statuses": provider_statuses}
    logger.info(f"/metrics response prepared — llm_providers={list(llm_summary.keys())}")
    return resp


@app.get("/prometheus")
def prometheus(_=Depends(require_secret)):
    """Prometheus exposition of LLM and provider metrics."""
    try:
        llm_summary = vector_store.get_llm_metrics_summary()
    except Exception as e:
        logger.warning(f"Failed to compute llm summary for prometheus: {e}")
        llm_summary = {}
    try:
        provider_statuses = vector_store.list_provider_statuses()
    except Exception as e:
        logger.warning(f"Failed to list provider statuses for prometheus: {e}")
        provider_statuses = []
    lines = []
    for provider, stats in (llm_summary or {}).items():
        for event, count in stats.items():
            lines.append(f'ci_llm_events_total{{provider="{provider}",event="{event}"}} {count}')
    for p in provider_statuses:
        disabled = 1 if p.get("disabled") else 0
        lines.append(f'ci_provider_disabled{{provider="{p.get("provider")}"}} {disabled}')
    stats = vector_store.stats()
    lines.append(f'ci_chroma_reports_count {stats.get("reports_count", 0)}')
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/logs")
def get_logs(lines: int = Query(100, ge=10, le=500), _=Depends(require_secret)):
    """Return last N lines from agent.log."""
    log_file = cfg.LOGS_DIR / "agent.log"
    if not log_file.exists():
        return {"lines": []}
    with open(log_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return {"lines": all_lines[-lines:]}


# ── Approval Endpoints ─────────────────────────────────────────────────────


@app.get("/approve/{report_id}", response_class=HTMLResponse)
def approve_via_link(report_id: str, token: str = Query("")):
    """Approve implementation via email link (GET for easy clicking)."""
    if not verify_approval_token(report_id, "approve", token):
        return HTMLResponse(
            "<html><body><h2 style='color:red'>❌ Invalid or expired approval token.</h2></body></html>",
            status_code=401,
        )
    return _do_approve(report_id, approved_by="email_link", html_response=True)


@app.post("/approve/{report_id}")
def approve_via_api(
    report_id: str,
    background_tasks: BackgroundTasks,
    _=Depends(require_secret),
):
    """Approve implementation via API call."""
    background_tasks.add_task(step_implement_approved, report_id, "api_post")
    update_state(pending_approval_report_id=report_id, approval_received=True)
    return {"success": True, "message": f"Report {report_id} approved. Implementation starting in background."}


@app.get("/reject/{report_id}", response_class=HTMLResponse)
def reject_via_link(report_id: str, token: str = Query("")):
    """Reject implementation via email link."""
    if not verify_approval_token(report_id, "reject", token):
        return HTMLResponse("<html><body><h2 style='color:red'>❌ Invalid or expired rejection token.</h2></body></html>", status_code=401)
    update_state(pending_approval_report_id=None, status="idle")
    # Mark all pending actions from this report as rejected
    pending = vector_store.get_pending_actions(limit=50)
    for action in pending:
        meta = action.get("metadata", {})
        if meta.get("snapshot_id", "") in report_id:
            vector_store.update_action_status(
                meta.get("action_id", ""), "rejected", error="Rejected by user via email"
            )
    return HTMLResponse("""
<html><body style="font-family:Arial;text-align:center;padding:50px">
<h2 style="color:#e74c3c">❌ Action Plan Rejected</h2>
<p>No changes have been made to the website.</p>
<p>A new action plan will be generated tomorrow during the daily run.</p>
</body></html>""")


def _do_approve(report_id: str, approved_by: str, html_response: bool = False):
    """Internal approval handler — starts implementation in background thread."""
    update_state(pending_approval_report_id=report_id, approval_received=True)
    t = threading.Thread(
        target=step_implement_approved,
        args=(report_id, approved_by),
        daemon=True,
    )
    t.start()

    if html_response:
        return HTMLResponse(f"""
<html><body style="font-family:Arial;text-align:center;padding:50px">
<h2 style="color:#27ae60">✅ Implementation Approved!</h2>
<p>Report <code>{report_id}</code> has been approved.</p>
<p>Implementation is running in the background. You will receive an email with results shortly.</p>
<p><a href="http://localhost:{cfg.API_PORT}/actions">View Action Items Status</a></p>
</body></html>""")
    return {"success": True, "message": "Approved. Implementation running."}


# ── Manual Trigger Endpoints ───────────────────────────────────────────────


@app.post("/run-now")
def run_now(
    background_tasks: BackgroundTasks,
    _=Depends(require_secret),
):
    """Trigger the full pipeline immediately (fetch → analyze → email for approval)."""
    if get_state().get("status") not in ("idle", "error"):
        return {"success": False, "message": f"Agent is busy: {get_state()['status']}"}
    background_tasks.add_task(step_fetch_and_analyze)
    return {"success": True, "message": "Full pipeline triggered. Check /status for progress."}


@app.post("/run-implement")
def run_implement(
    background_tasks: BackgroundTasks,
    _=Depends(require_secret),
):
    """Trigger implementation of approved actions immediately."""
    report_id = get_state().get("last_report_id", "manual")
    background_tasks.add_task(step_implement_approved, report_id, "manual_trigger")
    return {"success": True, "message": f"Implementation triggered for report {report_id}"}


@app.post("/run-validate")
def run_validate(
    background_tasks: BackgroundTasks,
    _=Depends(require_secret),
):
    """Trigger evening validation pass immediately."""
    background_tasks.add_task(step_validate_evening)
    return {"success": True, "message": "Validation pass triggered"}


@app.post("/run-fetch-only")
def run_fetch_only(
    background_tasks: BackgroundTasks,
    _=Depends(require_secret),
):
    """Fetch GSC data only (no analysis, no email)."""
    from gsc_client import GSCClient

    def _fetch():
        try:
            gsc = GSCClient()
            snapshot = gsc.fetch_full_snapshot()
            summary = gsc.compute_summary_stats(snapshot)
            snap_id = vector_store.store_gsc_snapshot(snapshot, summary)
            logger.info(f"Fetch-only complete: {snap_id}")
        except Exception as e:
            logger.error(f"Fetch-only failed: {e}", exc_info=True)

    background_tasks.add_task(_fetch)
    return {"success": True, "message": "GSC fetch triggered"}


@app.get("/extended-report/latest")
def extended_report_latest(_=Depends(require_secret)):
    """Return latest extended technical SEO report (indexing, schema, CWV, sitemaps, links)."""
    reports = vector_store.get_report_history(limit=10)
    extended = [r for r in reports if r.get("type") == "extended" or "extended" in r.get("report_id","")]
    if not extended:
        raise HTTPException(status_code=404, detail="No extended report yet. Trigger /run-now first.")
    latest_id = extended[0].get("report_id")
    col = vector_store._col(cfg.CHROMA_COLLECTION_REPORTS)
    res = col.get(ids=[latest_id], include=["metadatas"])
    if res["ids"]:
        import json as _json
        full = _json.loads(res["metadatas"][0].get("full_report", "{}"))
        return full
    return extended[0]


@app.post("/run-extended")
def run_extended(
    background_tasks: BackgroundTasks,
    _=Depends(require_secret),
):
    """Trigger extended GSC report only (indexing, schema, CWV, sitemaps, links)."""

    def _run():
        try:
            from gsc_extended import gsc_extended
            from extended_analyzer import extended_analyzer
            snap_id = get_state().get("last_snapshot_id", "manual")
            report = gsc_extended.fetch_full_extended_report(top_url_count=15)
            vector_store.store_analysis_report({
                **report, "type": "extended",
                "snapshot_id": snap_id,
                "summary": str(report.get("summary", {})),
                "fetch_date": report.get("generated_at", ""),
            })
            analysis = extended_analyzer.analyze_extended_report(report, snap_id)
            logger.info(f"Extended report done: {report.get('summary',{})}")
        except Exception as e:
            logger.error(f"Extended report failed: {e}", exc_info=True)

    background_tasks.add_task(_run)
    return {"success": True, "message": "Extended report triggered (indexing + schema + CWV + sitemaps + links)"}


@app.get("/email-poll")
def trigger_email_poll(_=Depends(require_secret)):
    """Manually trigger email reply polling."""
    from mail_poller import mail_poller
    replies = mail_poller.poll_all()
    return {"replies_found": len(replies), "replies": replies}


@app.get("/search")
def search_similar(
    q: str = Query(..., description="Query to search similar keywords/pages in ChromaDB"),
    n: int = Query(5, ge=1, le=20),
    collection: str = Query("gsc", description="Collection to search: gsc | ga | actions"),
    _=Depends(require_secret),
):
    """Semantic search across ChromaDB collections (gsc, ga, actions)."""
    if collection == "ga":
        results = vector_store.search_ga_data(q, n=n)
    elif collection == "actions":
        col = vector_store._col(cfg.CHROMA_COLLECTION_ACTIONS)
        if col.count() == 0:
            return {"query": q, "results": []}
        raw = col.query(
            query_texts=[q], n_results=min(n, col.count()),
            include=["documents", "metadatas", "distances"],
        )
        results = [
            {"document": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                raw["documents"][0], raw["metadatas"][0], raw["distances"][0]
            )
        ]
    else:
        results = vector_store.search_similar_keywords(q, n=n)
    return {"query": q, "collection": collection, "results": results}


@app.post("/docs/train")
def train_reference_docs(
    background_tasks: BackgroundTasks,
    _=Depends(require_secret),
    max_pages: int = Query(120, ge=10, le=500),
    max_depth: int = Query(3, ge=1, le=6),
):
    """Train the agent on Google Search Central / Rich Results docs."""

    background_tasks.add_task(
        reference_docs_trainer.train_google_search_docs,
        max_pages=max_pages,
        max_depth=max_depth,
    )
    return {
        "success": True,
        "message": "Reference-doc training started in background.",
        "max_pages": max_pages,
        "max_depth": max_depth,
    }


@app.get("/docs/search")
def search_reference_docs(
    q: Optional[str] = Query(None, description="Semantic query for Google Search guidance"),
    query: Optional[str] = Query(None, description="Alias for q"),
    n: int = Query(8, ge=1, le=20),
    source: Optional[str] = Query(None, description="Optional source filter"),
    _=Depends(require_secret),
):
    """Search trained Google Search / Rich Results documentation."""
    search_term = (q or query or "").strip()
    if not search_term:
        raise HTTPException(status_code=422, detail="Either q or query is required")
    return {
        "query": search_term,
        "source": source,
        "results": vector_store.search_reference_docs(search_term, n=n, source=source),
    }


@app.get("/docs/sources")
def reference_doc_sources(_=Depends(require_secret)):
    """List trained documentation sources and counts."""
    stats = vector_store.stats()
    return {
        "reference_docs_count": stats.get("reference_docs_count", 0),
        "sources": vector_store.list_reference_doc_sources(),
    }


# ── GA4 Endpoints ──────────────────────────────────────────────────────────


@app.get("/ga4/summary")
def ga4_summary(_=Depends(require_secret)):
    """Return latest GA4 summary from ChromaDB vector store."""
    summary = vector_store.get_latest_ga_summary()
    if not summary:
        raise HTTPException(
            status_code=404,
            detail="No GA4 data yet. Trigger /run-now or /ga4/fetch to fetch.",
        )
    return summary


@app.get("/ga4/page")
def ga4_page_metrics(
    url: Optional[str] = Query(None, description="Page path, e.g. /product/cenforce-200mg-tablets/"),
    page_path: Optional[str] = Query(None, description="Alias for page path."),
    _=Depends(require_secret),
):
    """Return GA4 metrics for a specific page path from the vector store."""
    target_url = (url or page_path or "").strip()
    if not target_url:
        raise HTTPException(status_code=422, detail="Either 'url' or 'page_path' is required")
    meta = vector_store.get_ga_page_metrics(target_url)
    if not meta:
        raise HTTPException(status_code=404, detail=f"No GA4 data for: {target_url}")
    return meta


@app.post("/ga4/fetch")
def ga4_fetch(
    background_tasks: BackgroundTasks,
    _=Depends(require_secret),
):
    """Fetch GA4 data only (no GSC, no analysis)."""

    def _fetch():
        try:
            from ga_client import ga_client
            snapshot = ga_client.fetch_full_snapshot(days=cfg.GSC_DAYS_HISTORY)
            summary = ga_client.compute_summary_stats(snapshot)
            snap_id = vector_store.store_ga_snapshot(snapshot, summary)
            logger.info(f"GA4 fetch-only complete: {snap_id}")
        except Exception as e:
            logger.error(f"GA4 fetch failed: {e}", exc_info=True)

    background_tasks.add_task(_fetch)
    return {"success": True, "message": "GA4 fetch triggered. Check /ga4/summary once complete."}


@app.get("/ga4/snapshots")
def ga4_snapshots(_=Depends(require_secret)):
    """List GA4 snapshot dates stored in ChromaDB."""
    return {"snapshot_dates": vector_store.list_ga_snapshot_dates()}


# ── MCP Info endpoint ──────────────────────────────────────────────────────


@app.get("/mcp-info")
def mcp_info(_=Depends(require_secret)):
    """Information on how to connect to the CI SEO Agent MCP server."""
    sync_result = sync_mcp_config_files()
    return {
        "mcp_url":    f"http://localhost:{cfg.API_PORT}/mcp/",
        "transport":  "HTTP/SSE (Streamable HTTP)",
        "tools": [
            "get_agent_status", "get_latest_report", "get_pending_actions",
            "get_ga4_metrics", "get_gsc_metrics", "search_seo_data",
            "get_page_analytics", "trigger_pipeline", "trigger_agent_job",
            "approve_report", "get_agent_logs", "list_reports", "get_mcp_config",
            "search_reference_docs", "train_reference_docs",
        ],
        "resources": [
            "seo://status", "seo://latest-report",
            "seo://pending-actions", "seo://ga4-overview", "seo://mcp-config",
            "seo://reference-doc-sources",
        ],
        "prompts": ["analyze_page", "generate_action_plan"],
        "mcp_json_path": str(cfg.PROJECT_MCP_JSON_PATH),
        "agent_mcp_json_path": str(cfg.AGENT_MCP_JSON_PATH),
        "claude_code_config": build_stdio_mcp_config(),
        "http_mcp_config": build_http_mcp_config(),
        "synced_paths": sync_result["written_paths"],
        "note": "MCP config is synced to both the project root and the SEO agent root so CLI agents and subagents can auto-load the same servers.",
    }


@app.get("/mcp-config")
def mcp_config(_=Depends(require_secret)):
    """Return and sync the MCP config used by project agents and subagents."""
    return sync_mcp_config_files()


# ── GA4 Conversion Audit Endpoints ─────────────────────────────────────────


@app.get("/ga4/conversion-audit")
async def ga4_conversion_audit(
    days: int = 28,
    _=Depends(require_secret),
):
    """Run live GA4 conversion tracking audit."""

    try:
        from ga4_conversion_auditor import GA4ConversionAuditor
        auditor = GA4ConversionAuditor()
        report = auditor.run_full_audit(days=days)
        return {"status": "ok", "report": report}
    except Exception as e:
        logger.error(f"Conversion audit error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ga4/attribution-data")
async def ga4_attribution_data(
    days: int = 28,
    _=Depends(require_secret),
):
    """Get conversion attribution summary from DB."""

    try:
        from ga4_conversion_auditor import GA4ConversionAuditor
        auditor = GA4ConversionAuditor()
        summary = auditor.get_attribution_summary(days=days)
        return {"status": "ok", "data": summary}
    except Exception as e:
        logger.error(f"Attribution data error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ga4/funnel-report")
async def ga4_funnel_report(
    days: int = 28,
    _=Depends(require_secret),
):
    """Get ecommerce funnel conversion rates."""

    try:
        from ga4_conversion_auditor import GA4ConversionAuditor
        auditor = GA4ConversionAuditor()
        report = auditor.audit_funnel_conversion(days=days)
        return {"status": "ok", "report": report}
    except Exception as e:
        logger.error(f"Funnel report error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=cfg.API_HOST,
        port=cfg.API_PORT,
        reload=False,
        log_level="info",
    )
