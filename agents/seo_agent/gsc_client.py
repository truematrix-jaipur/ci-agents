"""
CI SEO Agent — Google Search Console Client
Supports: service account JSON key OR OAuth2 user credentials (fallback)
"""
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from agents.seo_agent.seo_config import cfg

logger = logging.getLogger("ci.gsc")

GSC_SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/webmasters",
]


class GSCClient:
    def __init__(self):
        self.service = None
        self._verified_site: Optional[str] = None

    # ── Authentication ─────────────────────────────────────────────────────

    def _build_service(self):
        """Build authenticated GSC service. Tries service account first, then OAuth.

        Adds an early verification call to detect restricted_client / unregistered scope
        errors and creates a FLAG_FOR_REVIEW action + alert email to the operator with
        remediation instructions. Falls back to OAuth authorized_user if available.
        """
        creds = None

        sa_path = Path(cfg.GSC_SERVICE_ACCOUNT_FILE)
        oauth_path = Path(cfg.GSC_OAUTH_FILE)
        tried_sa = False
        tried_oauth = False

        # If the operator has configured the agent to require a service account (GSC_FORCE_SERVICE_ACCOUNT),
        # fail fast with a FLAG_FOR_REVIEW rather than attempting OAuth fallback. This prevents
        # intermittent 'restricted_client' errors caused by developer OAuth clients with unregistered scopes.
        if not sa_path.exists() and getattr(cfg, 'GSC_FORCE_SERVICE_ACCOUNT', True):
            logger.error(f"GSC service account not found at {sa_path} and GSC_FORCE_SERVICE_ACCOUNT is enabled")
            try:
                from vector_store import vector_store

                vector_store.create_action_item(
                    action_type="FLAG_FOR_REVIEW",
                    priority="critical",
                    title="GSC Access: missing service account JSON",
                    description=(
                        f"Required service account JSON not found at: {sa_path}.\n\n"
                        "Please upload the service account JSON and add the service account email as an owner in Search Console."
                    ),
                )
            except Exception as e:
                logger.warning(f"Could not create FLAG_FOR_REVIEW action: {e}")
            try:
                from notifier import notifier

                notifier.send_error_alert(
                    "GSC Missing Service Account",
                    f"Service account JSON not found at {sa_path} and agent is configured to require service-account authentication.\n\nPlease upload the JSON and add the service account as owner in Search Console."
                )
            except Exception:
                logger.warning("Failed to send notifier for missing service account")
            raise RuntimeError(f"Required GSC service account missing: {sa_path}")

        # 1. Try service account JSON (preferred for background agents)
        if sa_path.exists():
            tried_sa = True
            logger.info(f"Authenticating via service account: {sa_path}")
            try:
                creds = service_account.Credentials.from_service_account_file(
                    str(sa_path), scopes=GSC_SCOPES
                )
            except Exception as e:
                logger.warning(f"Service account credentials load failed: {e}")
                creds = None

        # 2. OAuth2 user credentials (fallback)
        if creds is None and oauth_path.exists():
            tried_oauth = True
            logger.info(f"Authenticating via OAuth user credentials: {oauth_path}")
            try:
                with open(oauth_path) as f:
                    oauth_data = json.load(f)
                if oauth_data.get("type") == "authorized_user":
                    creds = Credentials(
                        token=None,
                        refresh_token=oauth_data.get("refresh_token"),
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=oauth_data.get("client_id"),
                        client_secret=oauth_data.get("client_secret"),
                        scopes=GSC_SCOPES,
                    )
                    # Refresh to get a valid token
                    creds.refresh(Request())
                    logger.info("OAuth token refreshed successfully")
            except Exception as e:
                logger.warning(f"OAuth credentials load/refresh failed: {e}")
                creds = None

        if creds is None:
            # Clear actionable guidance in the raised error so the operator can fix it
            raise RuntimeError(
                f"No valid GSC credentials found. Service account present: {sa_path.exists()}, OAuth present: {oauth_path.exists()}"
            )

        # Build the service and run a quick verification call to detect restricted_client errors
        self.service = build("searchconsole", "v1", credentials=creds)
        try:
            # Quick test call - if this fails with HttpError containing 'restricted_client'
            # or 'Unregistered scope' we capture it and generate an operator action.
            self.service.sites().list().execute()
            logger.info("GSC service built and verified successfully")
            return self.service
        except HttpError as e:
            err_text = str(e)
            logger.error(f"GSC API build/test call failed: {err_text}")

            # If we haven't already tried OAuth and an OAuth file exists, attempt fallback
            if not tried_oauth and oauth_path.exists():
                try:
                    with open(oauth_path) as f:
                        oauth_data = json.load(f)
                    if oauth_data.get("type") == "authorized_user":
                        creds2 = Credentials(
                            token=None,
                            refresh_token=oauth_data.get("refresh_token"),
                            token_uri="https://oauth2.googleapis.com/token",
                            client_id=oauth_data.get("client_id"),
                            client_secret=oauth_data.get("client_secret"),
                            scopes=GSC_SCOPES,
                        )
                        creds2.refresh(Request())
                        self.service = build("searchconsole", "v1", credentials=creds2)
                        self.service.sites().list().execute()
                        logger.info("GSC service built via OAuth fallback")
                        return self.service
                except Exception as e2:
                    logger.warning(f"OAuth fallback also failed: {e2}")
                    err_text += f"\nOAuth fallback failed: {e2}"

            # Create a FLAG_FOR_REVIEW action and notify human operators with remediation steps
            try:
                from vector_store import vector_store

                sa_email = None
                if sa_path.exists():
                    try:
                        with open(sa_path) as fh:
                            j = json.load(fh)
                            sa_email = j.get("client_email")
                    except Exception:
                        sa_email = None

                desc = (
                    f"GSC API access failed during verification.\n\nError: {err_text}\n\n"
                    "If using a service account, add the service account email as an Owner for the"
                    " Search Console property (Search Console → Settings → Users and permissions → Add user)."
                )
                if sa_email:
                    desc += f"\nService account email: {sa_email}"

                vector_store.create_action_item(
                    action_type="FLAG_FOR_REVIEW",
                    priority="critical",
                    title="GSC Access: service account/OAuth failure",
                    description=desc,
                )
                logger.info("Created FLAG_FOR_REVIEW action for GSC credential issue")
            except Exception as e3:
                logger.warning(f"Could not create FLAG_FOR_REVIEW action: {e3}")

            try:
                from notifier import notifier

                notifier.send_error_alert(
                    "GSC Credential Error",
                    f"GSC API verification failed: {err_text}\n\nPlease add the service account (or provide OAuth authorized_user JSON) and re-run."
                )
                logger.info("Sent GSC credential error email to operator")
            except Exception:
                logger.warning("Failed to send notifier for GSC credential error")

            # Re-raise so callers know building the service didn't succeed
            raise

    def get_service(self):
        if self.service is None:
            self._build_service()
        return self.service

    # ── Site Discovery ─────────────────────────────────────────────────────

    def list_sites(self) -> list[dict]:
        """Return all verified sites accessible to this account."""
        svc = self.get_service()
        resp = svc.sites().list().execute()
        return resp.get("siteEntry", [])

    def find_working_site_url(self) -> Optional[str]:
        """Find the first accessible URL from cfg.GSC_SITE_URLS."""
        if self._verified_site:
            return self._verified_site

        accessible = {s["siteUrl"] for s in self.list_sites()}
        logger.info(f"Accessible GSC sites: {accessible}")

        for candidate in cfg.GSC_SITE_URLS:
            if candidate in accessible:
                self._verified_site = candidate
                logger.info(f"Using GSC site: {candidate}")
                return candidate

        logger.warning(
            f"None of {cfg.GSC_SITE_URLS} found in accessible sites. "
            f"Accessible: {accessible}. Trying first accessible site."
        )
        if accessible:
            self._verified_site = next(iter(accessible))
            return self._verified_site
        return None

    # ── Data Fetching ──────────────────────────────────────────────────────

    def fetch_query_performance(
        self,
        site_url: str,
        start_date: date,
        end_date: date,
        dimensions: list[str] = None,
        row_limit: int = None,
    ) -> list[dict]:
        """
        Fetch performance data from GSC.
        Returns list of rows with keys: keys (list), clicks, impressions, ctr, position
        """
        if dimensions is None:
            dimensions = ["query", "page"]
        if row_limit is None:
            row_limit = cfg.GSC_ROW_LIMIT

        svc = self.get_service()
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": 0,
        }

        all_rows = []
        start_row = 0
        while True:
            body["startRow"] = start_row
            try:
                resp = (
                    svc.searchanalytics()
                    .query(siteUrl=site_url, body=body)
                    .execute()
                )
            except HttpError as e:
                logger.error(f"GSC API error: {e}")
                raise

            rows = resp.get("rows", [])
            all_rows.extend(rows)
            logger.debug(f"Fetched {len(rows)} rows (total {len(all_rows)})")

            if len(rows) < row_limit:
                break  # last page
            start_row += row_limit

        return all_rows

    def fetch_full_snapshot(self, days: int = None) -> dict:
        """
        Fetch a complete GSC snapshot for the configured site.
        Returns structured dict with metadata + rows for multiple dimension combos.
        """
        if days is None:
            days = cfg.GSC_DAYS_HISTORY

        site_url = self.find_working_site_url()
        if not site_url:
            raise RuntimeError("No accessible GSC site found")

        end_date = date.today() - timedelta(days=3)  # GSC has 3-day lag
        start_date = end_date - timedelta(days=days)

        logger.info(
            f"Fetching GSC snapshot: {site_url} | {start_date} → {end_date}"
        )

        snapshot = {
            "site_url": site_url,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "fetched_at": date.today().isoformat(),
            "data": {},
        }

        # Query + Page
        try:
            rows = self.fetch_query_performance(
                site_url, start_date, end_date, ["query", "page"]
            )
            snapshot["data"]["query_page"] = rows
            logger.info(f"query+page rows: {len(rows)}")
        except Exception as e:
            logger.error(f"Failed query+page fetch: {e}")
            snapshot["data"]["query_page"] = []

        # Query only (for aggregate keyword view)
        try:
            rows = self.fetch_query_performance(
                site_url, start_date, end_date, ["query"]
            )
            snapshot["data"]["query"] = rows
            logger.info(f"query-only rows: {len(rows)}")
        except Exception as e:
            logger.error(f"Failed query-only fetch: {e}")
            snapshot["data"]["query"] = []

        # Page only (for page-level metrics)
        try:
            rows = self.fetch_query_performance(
                site_url, start_date, end_date, ["page"]
            )
            snapshot["data"]["page"] = rows
            logger.info(f"page-only rows: {len(rows)}")
        except Exception as e:
            logger.error(f"Failed page-only fetch: {e}")
            snapshot["data"]["page"] = []

        # Country breakdown
        try:
            rows = self.fetch_query_performance(
                site_url, start_date, end_date, ["query", "country"], row_limit=2000
            )
            snapshot["data"]["query_country"] = rows
            logger.info(f"query+country rows: {len(rows)}")
        except Exception as e:
            logger.error(f"Failed query+country fetch: {e}")
            snapshot["data"]["query_country"] = []

        # Device breakdown
        try:
            rows = self.fetch_query_performance(
                site_url, start_date, end_date, ["page", "device"]
            )
            snapshot["data"]["page_device"] = rows
            logger.info(f"page+device rows: {len(rows)}")
        except Exception as e:
            logger.error(f"Failed page+device fetch: {e}")
            snapshot["data"]["page_device"] = []

        return snapshot

    def compute_summary_stats(self, snapshot: dict) -> dict:
        """Compute aggregate stats from a snapshot for quick analysis."""
        query_rows = snapshot["data"].get("query", [])
        page_rows = snapshot["data"].get("page", [])

        def agg(rows):
            total_clicks = sum(r.get("clicks", 0) for r in rows)
            total_impressions = sum(r.get("impressions", 0) for r in rows)
            avg_position = (
                sum(r.get("position", 0) * r.get("impressions", 1) for r in rows)
                / max(total_impressions, 1)
            )
            avg_ctr = total_clicks / max(total_impressions, 1)
            return {
                "total_clicks": total_clicks,
                "total_impressions": total_impressions,
                "avg_position": round(avg_position, 2),
                "avg_ctr": round(avg_ctr * 100, 2),
                "row_count": len(rows),
            }

        # Top keywords by clicks
        top_keywords = sorted(
            query_rows, key=lambda r: r.get("clicks", 0), reverse=True
        )[:20]
        top_keywords = [
            {
                "keyword": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"] * 100, 2),
                "position": round(r["position"], 1),
            }
            for r in top_keywords
        ]

        # Low CTR but high impression keywords
        low_ctr = [
            r
            for r in query_rows
            if r.get("impressions", 0) >= cfg.LOW_CTR_IMPRESSION_MIN
            and r.get("ctr", 1) <= cfg.LOW_CTR_RATE_MAX
        ]
        low_ctr = sorted(low_ctr, key=lambda r: r.get("impressions", 0), reverse=True)[
            :20
        ]
        low_ctr = [
            {
                "keyword": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"] * 100, 2),
                "position": round(r["position"], 1),
            }
            for r in low_ctr
        ]

        # Top pages by clicks
        top_pages = sorted(
            page_rows, key=lambda r: r.get("clicks", 0), reverse=True
        )[:20]
        top_pages = [
            {
                "page": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"] * 100, 2),
                "position": round(r["position"], 1),
            }
            for r in top_pages
        ]

        # Underperforming pages (position 4-20, low CTR)
        underperforming = [
            r
            for r in page_rows
            if 4 <= r.get("position", 0) <= 20
            and r.get("impressions", 0) >= 50
            and r.get("ctr", 1) <= 0.05
        ]
        underperforming = sorted(
            underperforming, key=lambda r: r.get("impressions", 0), reverse=True
        )[:20]
        underperforming = [
            {
                "page": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"] * 100, 2),
                "position": round(r["position"], 1),
            }
            for r in underperforming
        ]

        return {
            "query_summary": agg(query_rows),
            "page_summary": agg(page_rows),
            "top_keywords": top_keywords,
            "low_ctr_keywords": low_ctr,
            "top_pages": top_pages,
            "underperforming_pages": underperforming,
        }
