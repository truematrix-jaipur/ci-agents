"""
CI SEO Agent — Background Scheduler
Full autonomous pipeline:
  06:00 → Fetch GSC + Store in ChromaDB + Analyze + Email for approval
  07:30 → Execute approved actions (if approved by email)
  18:00 → Validation pass + evening report
  Continuously → Watch for approvals every 5 minutes
"""
import json
import logging
import time
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from agents.seo_agent.seo_config import cfg
from agents.seo_agent.gsc_client import GSCClient
from agents.seo_agent.ga_client import ga_client
from agents.seo_agent.vector_store import vector_store
from agents.seo_agent.analyzer import analyzer
from agents.seo_agent.implementer import implementer
from agents.seo_agent.validator import validator
from agents.seo_agent.notifier import notifier
from agents.seo_agent.mail_poller import mail_poller
from agents.seo_agent.gsc_extended import gsc_extended
from agents.seo_agent.extended_analyzer import extended_analyzer

logger = logging.getLogger("ci.scheduler")

# In-memory state (also persisted to ChromaDB)
_state: dict = {
    "last_run": None,
    "last_snapshot_id": None,
    "last_report_id": None,
    "pending_approval_report_id": None,
    "approval_received": False,   # True only when human explicitly approves via email or API
    "status": "idle",
    "last_error": None,
    "runs_today": 0,
}


def get_state() -> dict:
    return _state.copy()


def update_state(**kwargs):
    _state.update(kwargs)
    _state["updated_at"] = datetime.utcnow().isoformat()


# ── Job / Action Locking (cross-process) ───────────────────────────────────
# Use atomic file creation to implement simple advisory locks so multiple agent
# processes don't execute the same scheduled job or action twice.

def _lock_path(name: str) -> Path:
    p = Path(cfg.LOCK_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{name}.lock"


def acquire_lock(name: str) -> bool:
    """Attempt to acquire a lock named 'name'. Returns True if acquired."""
    path = _lock_path(name)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"pid": os.getpid(), "ts": datetime.utcnow().isoformat()}))
        logger.debug(f"Acquired lock: {path}")
        return True
    except FileExistsError:
        # Check for stale lock
        try:
            mtime = path.stat().st_mtime
            age = time.time() - mtime
            if age > cfg.LOCK_STALE_SECONDS:
                logger.warning(f"Lock {path} stale ({age}s) — removing and retrying")
                try:
                    path.unlink()
                except Exception as e:
                    logger.warning(f"Could not remove stale lock {path}: {e}")
                return acquire_lock(name)
        except Exception as e:
            logger.warning(f"Could not inspect lock file {path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to acquire lock {path}: {e}")
        return False


def release_lock(name: str) -> None:
    path = _lock_path(name)
    try:
        if path.exists():
            path.unlink()
            logger.debug(f"Released lock: {path}")
    except Exception as e:
        logger.warning(f"Failed to release lock {path}: {e}")


def with_lock(name: str):
    """Decorator to run a job under a named lock. Skips the job if lock not acquired."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            if not acquire_lock(name):
                logger.info(f"Skipping {name} because another process holds the lock")
                return None
            try:
                return fn(*args, **kwargs)
            finally:
                release_lock(name)
        return wrapper
    return decorator


# ── Pipeline Steps ─────────────────────────────────────────────────────────


def step_fetch_and_analyze() -> Optional[str]:
    """
    Step 1: Fetch GSC data, store in ChromaDB, analyze, store report.
    Returns report_id or None on failure.
    """
    update_state(status="fetching_gsc", last_error=None)
    logger.info("=" * 60)
    logger.info("PIPELINE STEP 1: GSC Fetch + Analysis")
    logger.info("=" * 60)

    try:
        gsc = GSCClient()
        snapshot = gsc.fetch_full_snapshot()
        summary = gsc.compute_summary_stats(snapshot)
        logger.info(
            f"GSC fetch complete: "
            f"{summary['query_summary']['total_clicks']:,} clicks, "
            f"{summary['query_summary']['total_impressions']:,} impressions"
        )

        # Store GSC in ChromaDB
        update_state(status="storing_vectors")
        snapshot_id = vector_store.store_gsc_snapshot(snapshot, summary)
        update_state(last_snapshot_id=snapshot_id)
        logger.info(f"Stored GSC snapshot: {snapshot_id}")

        # Fetch + store GA4 data
        ga_snapshot = {}
        ga_summary_data = {}
        try:
            update_state(status="fetching_ga4")
            logger.info("Fetching GA4 data...")
            ga_snapshot = ga_client.fetch_full_snapshot(days=cfg.GSC_DAYS_HISTORY)
            ga_summary_data = ga_client.compute_summary_stats(ga_snapshot)
            ga_snap_id = vector_store.store_ga_snapshot(ga_snapshot, ga_summary_data)
            logger.info(
                f"GA4 fetch complete: "
                f"{ga_summary_data.get('overview', {}).get('total_sessions', 0):,} sessions, "
                f"{ga_summary_data.get('overview', {}).get('total_pageviews', 0):,} pageviews — "
                f"stored as {ga_snap_id}"
            )
        except Exception as e:
            logger.warning(f"GA4 fetch failed (non-fatal, proceeding with GSC only): {e}", exc_info=True)

        # Run GA4 conversion audit (non-fatal)
        conversion_audit: dict = {}
        try:
            update_state(status="conversion_audit")
            logger.info("Running GA4 conversion audit...")
            from agents.seo_agent.ga4_conversion_auditor import GA4ConversionAuditor
            _auditor = GA4ConversionAuditor()
            # Pass top GSC keywords for cross-referencing — snapshot["data"]["query"]
            # rows have structure: {"keys": ["keyword"], "clicks": N, "impressions": N, ...}
            gsc_keywords = [
                {"query": row["keys"][0], "clicks": row.get("clicks", 0)}
                for row in snapshot.get("data", {}).get("query", [])[:100]
                if row.get("keys")
            ]
            conversion_audit = _auditor.run_full_audit(
                gsc_keywords=gsc_keywords or None,
                days=cfg.GSC_DAYS_HISTORY,
            )
            logger.info(
                f"Conversion audit complete — health score: "
                f"{conversion_audit.get('health_score', 0)}%"
            )
        except Exception as e:
            logger.warning(f"Conversion audit failed (non-fatal): {e}", exc_info=True)

        # Analyze (GSC + GA4 combined — pass conversion_audit for revenue-aware sorting)
        update_state(status="analyzing")
        report = analyzer.analyze(snapshot, summary, snapshot_id, ga_snapshot, ga_summary_data,
                                  conversion_audit=conversion_audit)

        # Store report
        report_id = vector_store.store_analysis_report(report)
        update_state(last_report_id=report_id, status="awaiting_approval", last_error=None)
        logger.info(f"Analysis complete: report_id={report_id}")
        # Telemetry: record which provider produced the analysis
        try:
            provider_used = getattr(analyzer, "_last_llm_provider_used", None) or "unknown"
            vector_store.record_llm_event(provider=provider_used, event="analysis_saved", message=f"report_id={report_id}", details={"report_id": report_id, "snapshot_id": snapshot_id})
        except Exception as e:
            logger.warning(f"Failed to record analysis_saved metric: {e}")

        # Store action items (with baseline data_signals for future impact measurement)
        action_plan = report.get("action_plan", [])
        for action in action_plan:
            impl_data = action.get("implementation_data", {})
            signals = action.get("data_signals", {})
            # Carry conversion_revenue from LLM enrichment into data_signals
            if action.get("conversion_revenue"):
                signals["conversion_revenue"] = action["conversion_revenue"]
            # Include which LLM provider produced this analysis (if available)
            try:
                signals["llm_provider"] = provider_used
            except Exception:
                signals["llm_provider"] = getattr(analyzer, "_last_llm_provider_used", None) or "unknown"
            vector_store.create_action_item(
                action_type=action.get("action_type", "FLAG_FOR_REVIEW"),
                priority=action.get("priority", "low"),
                title=action.get("title", ""),
                description=action.get("description", ""),
                target_url=action.get("target_url", ""),
                target_keyword=action.get("target_keyword", ""),
                implementation_data=impl_data,
                snapshot_id=snapshot_id,
                data_signals=signals,
            )
        logger.info(f"Stored {len(action_plan)} action items in ChromaDB")

        # Run extended GSC report (indexing, schema, CWV, sitemaps, links)
        extended_report = {}
        extended_analysis = {}
        try:
            update_state(status="extended_analysis")
            logger.info("Running extended GSC report (indexing + schema + CWV)...")
            extended_report = gsc_extended.fetch_full_extended_report(top_url_count=15)
            vector_store.store_analysis_report({
                **extended_report,
                "type": "extended",
                "snapshot_id": snapshot_id,
                "summary": f"Extended technical report: {extended_report.get('summary', {})}",
                "fetch_date": snapshot["fetched_at"],
            })
            extended_analysis = extended_analyzer.analyze_extended_report(extended_report, snapshot_id)
            logger.info("Extended report complete")
        except Exception as e:
            logger.warning(f"Extended report failed (non-fatal): {e}", exc_info=True)

        # Build extended email section
        extended_html = ""
        try:
            if extended_report:
                extended_html = extended_analyzer.generate_extended_email_section(
                    extended_analysis, extended_report
                )
        except Exception as e:
            logger.warning(f"Extended email section failed: {e}")

        # Build conversion audit email section
        conversion_html = ""
        try:
            if conversion_audit:
                conversion_html = notifier.build_conversion_audit_html(conversion_audit)
        except Exception as e:
            logger.warning(f"Conversion audit email section failed: {e}")

        # Send approval email — reset approval_received so 07:30 job waits for explicit approval
        update_state(status="awaiting_approval", pending_approval_report_id=report_id, approval_received=False)
        email_ok = notifier.send_approval_request(
            report, report_id,
            extended_html=extended_html,
            conversion_html=conversion_html,
        )
        if email_ok:
            logger.info("Approval email sent to surya@truematrix.io")
        else:
            logger.warning("Approval email failed — actions will still be available via API")

        update_state(
            last_run=datetime.utcnow().isoformat(),
            runs_today=_state.get("runs_today", 0) + 1,
        )
        return report_id

    except Exception as e:
        logger.error(f"Pipeline step 1 failed: {e}", exc_info=True)
        update_state(status="error", last_error=str(e))
        notifier.send_error_alert("GSC Fetch/Analysis Failed", str(e))
        return None


def step_implement_approved(report_id: str, approved_by: str = "api") -> list[dict]:
    """
    Step 2: Execute approved action items with guardrails + validation.
    Returns list of results.
    Uses a per-report file lock to avoid concurrent implementation runs.
    """
    lock_name = f"implement_run_{report_id}"
    if not acquire_lock(lock_name):
        logger.info(f"Skipping implementation for report {report_id} because another run holds the lock")
        return []

    try:
        update_state(status="implementing", last_error=None)
        logger.info("=" * 60)
        logger.info(f"PIPELINE STEP 2: Implementation (approved by {approved_by})")
        logger.info("=" * 60)

        start_time = time.time()
        results = []

        try:
            # Get all pending actions
            pending = vector_store.get_pending_actions(limit=20)
            logger.info(f"Found {len(pending)} pending actions")

            executed = 0
            max_per_run = 10  # Hard safety limit per run

            for action in pending[:max_per_run]:
                meta = action.get("metadata", {})
                action_id = meta.get("action_id", "unknown")
                action_type = meta.get("action_type", "")
                target_url = meta.get("target_url", "")

                logger.info(f"Processing action {action_id}: {action_type} → {target_url}")

                # 1. Guardrail check
                is_safe, reason = validator.validate_action(action)
                if not is_safe:
                    logger.warning(f"Guardrail blocked {action_id}: {reason}")
                    vector_store.update_action_status(
                        action_id, "blocked", error=f"Guardrail: {reason}"
                    )
                    results.append({
                        "action_id": action_id,
                        "action_type": action_type,
                        "target_url": target_url,
                        "success": False,
                        "message": f"Blocked by guardrail: {reason}",
                    })
                    continue

                # 2. Pre-change backup (for WordPress meta changes)
                backup = {}
                post_id = None
                if target_url and action_type in {"UPDATE_META_DESCRIPTION", "UPDATE_PAGE_TITLE", "FIX_CANONICAL", "OPTIMIZE_HEADING"}:
                    post = implementer.wp.get_post_by_url(target_url)
                    if post:
                        post_id = post.get("ID") or post.get("id")
                        if post_id:
                            backup = validator.backup_post_meta(int(post_id))
                            logger.debug(f"Backup taken for post {post_id}")

                # 3. Mark as in-progress; persist backup so evening validation can auto-rollback
                stored_backup = {"post_id": post_id, "fields": backup} if backup else None
                vector_store.update_action_status(action_id, "in_progress", backup=stored_backup)

                # 4. Execute
                success, message = implementer.execute_action(action)

                # 5. Post-execution verification
                if success and target_url:
                    impl_data = meta.get("implementation_data", {})
                    if isinstance(impl_data, str):
                        try:
                            impl_data = json.loads(impl_data)
                        except Exception:
                            impl_data = {}

                    new_val = impl_data.get("new_value", "")
                    if action_type == "UPDATE_META_DESCRIPTION" and new_val:
                        verified, v_msg = validator.verify_meta_description(target_url, new_val)
                        if not verified:
                            logger.warning(f"Verification failed for {action_id}: {v_msg}")
                            # Attempt rollback using backup fields
                            if backup and post_id:
                                validator.rollback_post_meta(int(post_id), backup)
                                logger.info(f"Rolled back post {post_id} (meta description verification failed)")
                            message += f"\n⚠️ Verification failed (rolled back): {v_msg}"
                            success = False

                    elif action_type == "UPDATE_PAGE_TITLE" and new_val:
                        verified, v_msg = validator.verify_page_title(target_url, new_val)
                        if not verified:
                            logger.warning(f"Title verification failed for {action_id}: {v_msg}")
                            if backup and post_id:
                                validator.rollback_post_meta(int(post_id), backup)
                                logger.info(f"Rolled back post {post_id} (title verification failed)")
                            message += f"\n⚠️ Title verification failed (rolled back): {v_msg}"
                            success = False

                # 6. Update status
                status = "done" if success else "failed"
                vector_store.update_action_status(
                    action_id,
                    status,
                    result=message[:500] if success else "",
                    error=message[:500] if not success else "",
                )

                result = {
                    "action_id": action_id,
                    "action_type": action_type,
                    "target_url": target_url,
                    "success": success,
                    "message": message,
                }
                results.append(result)
                executed += 1

                logger.info(
                    f"Action {action_id} {'✓' if success else '✗'}: "
                    f"{message[:100]}"
                )

            duration = time.time() - start_time
            logger.info(
                f"Implementation complete: {sum(r['success'] for r in results)}/{len(results)} "
                f"succeeded in {duration:.1f}s"
            )

        except Exception as e:
            logger.error(f"Implementation step failed: {e}", exc_info=True)
            update_state(status="error", last_error=str(e))
            notifier.send_error_alert("Implementation Failed", str(e))

        # Send results email
        try:
            notifier.send_implementation_report(report_id, results, max(0, time.time() - start_time))
        except Exception:
            logger.warning("Failed to send implementation report email")
        update_state(status="idle", pending_approval_report_id=None, last_error=None)
        return results

    finally:
        release_lock(lock_name)


def step_validate_evening():
    """
    Step 3: Evening validation — check that morning's changes are still live.
    """
    update_state(status="validating", last_error=None)
    logger.info("PIPELINE STEP 3: Evening Validation Pass")

    try:
        # Get actions completed today
        done_actions = vector_store.get_all_actions(status="done")
        today = datetime.utcnow().date().isoformat()
        today_done = [
            a for a in done_actions
            if a["metadata"].get("updated_at", "")[:10] == today
        ]

        logger.info(f"Validating {len(today_done)} actions completed today")
        issues = []

        for action in today_done[:20]:
            meta = action.get("metadata", {})
            target_url = meta.get("target_url", "")
            action_type = meta.get("action_type", "")
            action_id = meta.get("action_id", "")

            if not target_url:
                continue

            accessible, status_code = validator.verify_url_accessible(target_url)
            if not accessible:
                issues.append(
                    f"{action_type} on {target_url} — page now returns HTTP {status_code}"
                )
                logger.warning(f"Page inaccessible after change: {target_url} (HTTP {status_code})")

                # Auto-rollback on server errors (5xx) — page is broken, revert immediately
                if status_code >= 500 and action_id:
                    backup_data = vector_store.get_action_backup(action_id)
                    if backup_data and backup_data.get("post_id") and backup_data.get("fields"):
                        rolled_back = validator.rollback_post_meta(
                            int(backup_data["post_id"]), backup_data["fields"]
                        )
                        if rolled_back:
                            logger.info(
                                f"Auto-rolled back {action_id} (HTTP {status_code} on {target_url})"
                            )
                            vector_store.update_action_status(
                                action_id, "rolled_back",
                                error=f"Auto-rolled back: page returned HTTP {status_code} in evening validation",
                            )
                            issues[-1] += " — ✅ AUTO-ROLLED BACK"
                        else:
                            logger.error(f"Rollback FAILED for {action_id} — manual intervention needed")
                            issues[-1] += " — ❌ ROLLBACK FAILED (manual fix needed)"

        if issues:
            notifier.send_error_alert(
                "Evening Validation Issues",
                "\n".join(issues),
            )
        else:
            logger.info("Evening validation: all changed pages accessible ✓")

        update_state(status="idle", last_error=None)

    except Exception as e:
        logger.error(f"Evening validation failed: {e}", exc_info=True)
        update_state(status="error", last_error=str(e))


# ── Impact Measurement ─────────────────────────────────────────────────────


def step_measure_impact():
    """
    Daily pass: re-fetch GSC data for all completed actions that are 7+ days old
    and haven't been impact-measured yet. Compare current position/CTR/clicks
    against the baseline data_signals stored at action creation time.
    Emails a delta impact report.
    Uses a file lock to avoid concurrent runs.
    """
    lock_name = "measure_impact"
    if not acquire_lock(lock_name):
        logger.info("Skipping impact measurement because another process holds the lock")
        return

    try:
        update_state(status="measuring_impact", last_error=None)
        logger.info("=" * 60)
        logger.info("PIPELINE STEP 4: Post-Implementation Impact Measurement")
        logger.info("=" * 60)

        try:
            from datetime import date, timedelta
            actions = vector_store.get_actions_for_impact_check(min_days_old=7, limit=30)
            if not actions:
                logger.info("[IMPACT] No actions eligible for impact measurement this run")
                update_state(status="idle")
                return

            logger.info(f"[IMPACT] Measuring {len(actions)} completed actions")

            # Collect unique target URLs
            urls_to_check = list({
                a["metadata"]["target_url"]
                for a in actions
                if a["metadata"].get("target_url")
            })
            logger.info(f"[IMPACT] Fetching GSC data for {len(urls_to_check)} unique URLs")

            # Fetch current 7-day GSC page-level metrics
            gsc = GSCClient()
            site_url = gsc.find_working_site_url()
            if not site_url:
                logger.warning("[IMPACT] No GSC site found — skipping impact measurement")
                update_state(status="idle")
                return

            end_date = date.today() - timedelta(days=3)   # GSC 3-day lag
            start_date = end_date - timedelta(days=7)

            current_page_rows = gsc.fetch_query_performance(
                site_url, start_date, end_date, dimensions=["page"]
            )
            # Build URL → current metrics lookup (try with/without trailing slash)
            current_metrics: dict = {}
            for row in current_page_rows:
                url = row["keys"][0]
                m = {
                    "clicks":      int(row.get("clicks", 0)),
                    "impressions": int(row.get("impressions", 0)),
                    "ctr":         round(row.get("ctr", 0) * 100, 2),
                    "position":    round(row.get("position", 0), 1),
                }
                current_metrics[url] = m
                current_metrics[url.rstrip("/") + "/"] = m
                current_metrics[url.rstrip("/")] = m

            impact_results = []
            for action in actions:
                meta = action["metadata"]
                action_id  = meta.get("action_id", "")
                action_type = meta.get("action_type", "")
                target_url  = meta.get("target_url", "")
                keyword     = meta.get("target_keyword", "")

                baseline = {
                    "clicks":    float(meta.get("baseline_clicks", 0)),
                    "ctr":       float(meta.get("baseline_ctr", 0)),
                    "position":  float(meta.get("baseline_position", 0)),
                }

                current = (
                    current_metrics.get(target_url)
                    or current_metrics.get(target_url.rstrip("/") + "/")
                    or current_metrics.get(target_url.rstrip("/"))
                    or {}
                )

                implemented_at = meta.get("updated_at", "")
                try:
                    days_since = (
                        datetime.utcnow() - datetime.fromisoformat(implemented_at)
                    ).days
                except Exception:
                    days_since = 7

                delta = {
                    "clicks":   int(current.get("clicks", 0)) - int(baseline["clicks"]),
                    "ctr":      round(float(current.get("ctr", 0)) - baseline["ctr"], 2),
                    "position": round(float(current.get("position", 0)) - baseline["position"], 1)
                                if current.get("position") else 0,
                }

                impact = {
                    "action_id":    action_id,
                    "action_type":  action_type,
                    "target_url":   target_url,
                    "keyword":      keyword,
                    "days_since":   days_since,
                    "implemented_at": implemented_at,
                    "measured_at":  datetime.utcnow().isoformat(),
                    "baseline":     baseline,
                    "current":      current,
                    "delta":        delta,
                    "improved":     delta["clicks"] > 0 or delta["ctr"] > 0 or delta["position"] < 0,
                }

                vector_store.update_action_impact(action_id, impact)
                impact_results.append(impact)

                sign = "↑" if impact["improved"] else ("→" if not any(delta.values()) else "↓")
                logger.info(
                    f"[IMPACT] {sign} {action_type} | {target_url[:50]} | "
                    f"clicks {delta['clicks']:+d} | pos {delta['position']:+.1f} | ctr {delta['ctr']:+.2f}%"
                )

            notifier.send_impact_report(impact_results)
            logger.info(
                f"[IMPACT] Measurement complete: {len(impact_results)} actions | "
                f"{sum(1 for r in impact_results if r['improved'])} improved"
            )
            update_state(status="idle")

        except Exception as e:
            logger.error(f"Impact measurement failed: {e}", exc_info=True)
            update_state(status="error", last_error=str(e))
            notifier.send_error_alert("Impact Measurement Failed", str(e))

    finally:
        release_lock(lock_name)


# ── Scheduled Jobs ─────────────────────────────────────────────────────────


@with_lock("daily_fetch")
def job_daily_fetch():
    """06:00 — Daily GSC fetch, analysis, approval email."""
    logger.info(f"[CRON] Daily fetch job started at {datetime.utcnow().isoformat()}")
    step_fetch_and_analyze()


@with_lock("implement")
def job_implement():
    """07:30 — Implement only if human has explicitly approved via email or API."""
    logger.info(f"[CRON] Implementation job started at {datetime.utcnow().isoformat()}")
    report_id = _state.get("pending_approval_report_id")
    if not report_id:
        logger.info("[CRON] No pending report — skipping implementation")
        return
    if not _state.get("approval_received"):
        logger.info(
            f"[CRON] Report {report_id} awaiting human approval — skipping auto-implement. "
            "Click Approve in the email or use POST /approve/{report_id}."
        )
        return
    logger.info(f"[CRON] Implementing report {report_id} (human-approved)")
    step_implement_approved(report_id, approved_by="scheduled_post_approval")


@with_lock("evening_validation")
def job_evening_validation():
    """18:00 — Evening validation pass."""
    logger.info(f"[CRON] Evening validation started at {datetime.utcnow().isoformat()}")
    step_validate_evening()


@with_lock("daily_impact")
def job_measure_impact():
    """Daily job that measures impact for previously implemented actions."""
    logger.info(f"[CRON] Impact measurement job started at {datetime.utcnow().isoformat()}")
    step_measure_impact()


@with_lock("email_poller")
def job_poll_email():
    """Every 10 min — Poll Gmail + local mailbox for approval replies."""
    replies = mail_poller.poll_all()
    if not replies:
        return

    for reply in replies:
        decision = reply.get("decision")
        report_id = reply.get("report_id")
        from_addr = reply.get("from", "")
        source = reply.get("source", "unknown")

        logger.info(
            f"[EMAIL REPLY] decision={decision} report={report_id} "
            f"from={from_addr} via={source}"
        )

        if not report_id:
            report_id = _state.get("pending_approval_report_id", "latest")

        if decision == "approve":
            logger.info(f"Approving report {report_id} via email reply")
            update_state(pending_approval_report_id=report_id, approval_received=True)
            import threading
            t = threading.Thread(
                target=step_implement_approved,
                args=(report_id, f"email_reply_{source}"),
                daemon=True,
            )
            t.start()
        elif decision == "reject":
            logger.info(f"Rejecting report {report_id} via email reply")
            update_state(pending_approval_report_id=None, status="idle")
            pending = vector_store.get_pending_actions(limit=50)
            for action in pending:
                meta = action.get("metadata", {})
                vector_store.update_action_status(
                    meta.get("action_id", ""), "rejected",
                    error=f"Rejected by {from_addr} via email reply"
                )


# ── Scheduler Setup ────────────────────────────────────────────────────────


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    # Daily fetch: 06:00 UTC (can be disabled via PAUSE_SCHEDULED_FETCH)
    if not getattr(cfg, 'PAUSE_SCHEDULED_FETCH', False):
        scheduler.add_job(
            job_daily_fetch,
            "cron",
            hour=cfg.SCHEDULE_FETCH_HOUR,
            minute=cfg.SCHEDULE_FETCH_MINUTE,
            id="daily_fetch",
            replace_existing=True,
            misfire_grace_time=3600,
        )
    else:
        logger.info("Daily fetch job disabled via PAUSE_SCHEDULED_FETCH config")

    # Implementation: 07:30 UTC
    scheduler.add_job(
        job_implement,
        "cron",
        hour=cfg.SCHEDULE_IMPLEMENT_HOUR,
        minute=cfg.SCHEDULE_IMPLEMENT_MINUTE,
        id="implement",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Evening validation: 18:00 UTC
    scheduler.add_job(
        job_evening_validation,
        "cron",
        hour=cfg.SCHEDULE_VALIDATE_HOUR,
        minute=cfg.SCHEDULE_VALIDATE_MINUTE,
        id="evening_validation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Email reply poller: every 10 minutes
    scheduler.add_job(
        job_poll_email,
        "interval",
        minutes=10,
        id="email_poller",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Daily impact measurement: configurable hour (default 09:00 UTC)
    scheduler.add_job(
        job_measure_impact,
        "cron",
        hour=cfg.SCHEDULE_IMPACT_HOUR,
        minute=cfg.SCHEDULE_IMPACT_MINUTE,
        id="daily_impact",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    def on_error(event):
        logger.error(f"Scheduler job failed: {event.job_id} — {event.exception}")

    def on_success(event):
        logger.info(f"Scheduler job completed: {event.job_id}")

    scheduler.add_listener(on_error, EVENT_JOB_ERROR)
    scheduler.add_listener(on_success, EVENT_JOB_EXECUTED)

    return scheduler
