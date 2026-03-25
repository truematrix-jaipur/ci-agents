"""
GA4 Conversion Auditor — CI SEO Agent
Audits GA4 event tracking completeness, conversion funnel integrity,
and cross-references attribution data for SourceMedium compatibility.
"""
from __future__ import annotations
import logging
from datetime import date, datetime, timedelta
from typing import Any
import mysql.connector

logger = logging.getLogger(__name__)

# Expected GA4 enhanced ecommerce events for a WooCommerce store
EXPECTED_EVENTS = [
    "view_item_list",
    "view_item",
    "add_to_cart",
    "remove_from_cart",
    "begin_checkout",
    "add_shipping_info",
    "add_payment_info",
    "purchase",
]

# Events that are conversion events (should be marked in GA4)
CONVERSION_EVENTS = ["purchase", "add_to_cart", "begin_checkout"]


class GA4ConversionAuditor:
    """Audits GA4 conversion tracking completeness and attribution quality."""

    def __init__(self):
        self._ga_client = None  # initialized lazily
        self._db_config = self._get_db_config()

    def _get_db_config(self) -> dict:
        """
        Read DB credentials from the WordPress .env file.

        wp-config.php wraps all DB constants in ci_env() calls which defeats
        simple regex extraction. The actual values live in the adjacent .env file
        which is a standard KEY=VALUE format, directly readable.
        """
        wp_env_path = "/var/www/html/indogenmed.org/html/.env"
        try:
            config: dict = {}
            with open(wp_env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    config[key] = value

            return {
                "host": config.get("DB_HOST") or "127.0.0.1",
                "database": config.get("DB_NAME", ""),
                "user": config.get("DB_USER", ""),
                "password": config.get("DB_PASSWORD", ""),
            }
        except Exception as e:
            logger.error(f"Failed to read WordPress .env for DB credentials: {e}")
            return {}

    def _get_db_connection(self):
        """Get MySQL connection using WP credentials."""
        return mysql.connector.connect(**self._db_config)

    def _get_ga_client(self):
        """Lazy-init GA4 client using the same pattern as ga_client.py."""
        if self._ga_client is None:
            from ga_client import GA4Client
            self._ga_client = GA4Client()
        return self._ga_client

    # ── Audit Methods ──────────────────────────────────────────────────────

    def audit_event_completeness(self, days: int = 28) -> dict:
        """
        Check which expected GA4 events have actually fired in the last N days.
        Returns dict with found/missing events and event counts.
        Uses the existing GA4Client._run_report() helper.
        """
        try:
            client = self._get_ga_client()

            from google.analytics.data_v1beta.types import DateRange

            end = date.today() - timedelta(days=2)  # GA4 ~2-day delay
            start = end - timedelta(days=days - 1)
            dr = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

            rows = client._run_report(
                dimensions=["eventName"],
                metrics=["eventCount", "conversions"],
                date_ranges=dr,
                limit=200,
            )

            found_events: dict = {}
            for row in rows:
                event_name = row.get("eventName", "")
                event_count = int(row.get("eventCount", 0))
                conversions = int(row.get("conversions", 0))
                found_events[event_name] = {
                    "count": event_count,
                    "conversions": conversions,
                    "is_conversion": conversions > 0,
                }

            missing_events = [e for e in EXPECTED_EVENTS if e not in found_events]
            found_expected = {e: found_events[e] for e in EXPECTED_EVENTS if e in found_events}

            return {
                "status": "ok",
                "period_days": days,
                "found_events": found_expected,
                "missing_events": missing_events,
                "all_events_count": len(found_events),
                "expected_events_found": len(found_expected),
                "expected_events_total": len(EXPECTED_EVENTS),
                "completeness_pct": round(len(found_expected) / len(EXPECTED_EVENTS) * 100, 1),
            }

        except Exception as e:
            logger.error(f"Event completeness audit failed: {e}")
            return {"status": "error", "error": str(e), "missing_events": EXPECTED_EVENTS}

    def audit_funnel_conversion(self, days: int = 28) -> dict:
        """
        Analyze conversion funnel: view_item → add_to_cart → checkout → purchase rates.
        """
        try:
            client = self._get_ga_client()

            from google.analytics.data_v1beta.types import DateRange

            end = date.today() - timedelta(days=2)
            start = end - timedelta(days=days - 1)
            dr = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

            rows = client._run_report(
                dimensions=["eventName"],
                metrics=["eventCount", "totalUsers"],
                date_ranges=dr,
                limit=200,
            )

            event_counts: dict = {}
            for row in rows:
                event_name = row.get("eventName", "")
                event_counts[event_name] = {
                    "events": int(row.get("eventCount", 0)),
                    "users": int(row.get("totalUsers", 0)),
                }

            funnel_steps = [
                "view_item",
                "add_to_cart",
                "begin_checkout",
                "add_payment_info",
                "purchase",
            ]
            funnel = {
                step: event_counts.get(step, {"events": 0, "users": 0})
                for step in funnel_steps
            }

            # Compute step-to-step conversion rates
            steps = list(funnel.items())
            rates: dict = {}
            for i in range(1, len(steps)):
                prev_name, prev_data = steps[i - 1]
                curr_name, curr_data = steps[i]
                prev_count = prev_data["events"]
                curr_count = curr_data["events"]
                rate = round(curr_count / prev_count * 100, 2) if prev_count > 0 else 0
                rates[f"{prev_name}_to_{curr_name}"] = {
                    "from": prev_count,
                    "to": curr_count,
                    "rate_pct": rate,
                    "drop_off_pct": round(100 - rate, 2),
                }

            overall_view_to_purchase = 0
            view_count = funnel.get("view_item", {}).get("events", 0)
            purchase_count = funnel.get("purchase", {}).get("events", 0)
            if view_count > 0:
                overall_view_to_purchase = round(purchase_count / view_count * 100, 3)

            return {
                "status": "ok",
                "period_days": days,
                "funnel_events": funnel,
                "conversion_rates": rates,
                "overall_view_to_purchase_rate": overall_view_to_purchase,
                "overall_purchase_rate": rates.get(
                    "view_item_to_add_to_cart", {}
                ).get("rate_pct", 0),
            }

        except Exception as e:
            logger.error(f"Funnel audit failed: {e}")
            return {"status": "error", "error": str(e)}

    def get_attribution_summary(self, days: int = 28) -> dict:
        """
        Read attribution DB tables and return conversion summary by source/medium/campaign.
        Returns gracefully if tables do not yet exist.
        """
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)

            since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

            # Revenue by source/medium
            cursor.execute(
                """
                SELECT
                    COALESCE(utm_source, 'direct') AS utm_source,
                    COALESCE(utm_medium, 'none')   AS utm_medium,
                    COALESCE(utm_campaign, '')      AS utm_campaign,
                    COUNT(*)                        AS order_count,
                    SUM(order_total)                AS revenue,
                    AVG(order_total)                AS avg_order_value
                FROM CI_ci_order_attribution
                WHERE created_at >= %s
                GROUP BY utm_source, utm_medium, utm_campaign
                ORDER BY revenue DESC
                LIMIT 20
                """,
                (since,),
            )
            revenue_by_source = cursor.fetchall()

            # Top converting search terms
            cursor.execute(
                """
                SELECT
                    utm_term,
                    COUNT(*)       AS conversions,
                    SUM(order_total) AS revenue
                FROM CI_ci_order_attribution
                WHERE utm_term IS NOT NULL AND utm_term != ''
                  AND created_at >= %s
                GROUP BY utm_term
                ORDER BY conversions DESC
                LIMIT 20
                """,
                (since,),
            )
            top_converting_terms = cursor.fetchall()

            # GCLID (paid search) stats
            cursor.execute(
                """
                SELECT
                    COUNT(*)       AS paid_conversions,
                    SUM(order_total) AS paid_revenue
                FROM CI_ci_order_attribution
                WHERE gclid IS NOT NULL AND gclid != ''
                  AND created_at >= %s
                """,
                (since,),
            )
            paid_stats = cursor.fetchone()

            # Totals
            cursor.execute(
                """
                SELECT
                    COUNT(*)       AS total_orders,
                    SUM(order_total) AS total_revenue
                FROM CI_ci_order_attribution
                WHERE created_at >= %s
                """,
                (since,),
            )
            totals = cursor.fetchone()

            cursor.close()
            conn.close()

            return {
                "status": "ok",
                "period_days": days,
                "totals": totals,
                "paid_stats": paid_stats,
                "revenue_by_source": revenue_by_source,
                "top_converting_terms": top_converting_terms,
            }

        except Exception as e:
            logger.warning(
                f"Attribution summary unavailable (tables may not exist yet): {e}"
            )
            return {
                "status": "unavailable",
                "error": str(e),
                "note": "Attribution tables not yet created — deploy TASK-4 MU plugin first",
            }

    def get_searchterm_conversion_map(
        self, gsc_keywords: list[dict], days: int = 28
    ) -> list[dict]:
        """
        Cross-reference GSC search terms with attribution DB conversions.
        Maps each GSC keyword to its conversion count if available.
        Used to prioritize SEO content work by revenue impact.
        """
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(dictionary=True)

            since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

            cursor.execute(
                """
                SELECT
                    utm_term,
                    COUNT(*)         AS conversions,
                    SUM(order_total) AS revenue
                FROM CI_ci_order_attribution
                WHERE utm_term IS NOT NULL AND utm_term != ''
                  AND created_at >= %s
                GROUP BY utm_term
                """,
                (since,),
            )
            converting_terms = {
                row["utm_term"].lower(): row for row in cursor.fetchall()
            }
            cursor.close()
            conn.close()

            # Enrich GSC keywords with conversion data
            enriched = []
            for kw in gsc_keywords[:100]:
                query = kw.get("query", "").lower()
                conv_data = converting_terms.get(query, {})
                enriched.append(
                    {
                        **kw,
                        "conversions": conv_data.get("conversions", 0),
                        "conversion_revenue": float(conv_data.get("revenue") or 0),
                        "has_conversion_data": bool(conv_data),
                    }
                )

            # Sort by revenue descending
            enriched.sort(key=lambda x: x["conversion_revenue"], reverse=True)
            return enriched

        except Exception as e:
            logger.warning(f"Search term conversion map failed (non-fatal): {e}")
            return gsc_keywords  # Return original if DB fails

    def run_full_audit(
        self,
        gsc_keywords: list[dict] | None = None,
        days: int = 28,
    ) -> dict:
        """
        Run complete conversion tracking audit.
        Returns structured report suitable for LLM analysis and email notification.
        """
        logger.info("Starting GA4 conversion audit...")

        audit: dict = {
            "timestamp": datetime.utcnow().isoformat(),
            "period_days": days,
            "event_completeness": self.audit_event_completeness(days),
            "funnel_analysis": self.audit_funnel_conversion(days),
            "attribution_summary": self.get_attribution_summary(days),
            "searchterm_conversions": [],
        }

        if gsc_keywords:
            audit["searchterm_conversions"] = self.get_searchterm_conversion_map(
                gsc_keywords, days
            )

        # Compute overall health score (0–100)
        completeness = audit["event_completeness"].get("completeness_pct", 0)
        has_attribution = audit["attribution_summary"].get("status") == "ok"
        purchase_rate = audit["funnel_analysis"].get("overall_view_to_purchase_rate", 0)

        health_issues: list[str] = []
        if completeness < 100:
            missing = audit["event_completeness"].get("missing_events", [])
            health_issues.append(f"Missing events: {', '.join(missing)}")
        if not has_attribution:
            health_issues.append(
                "Attribution DB not accessible — deploy TASK-4 MU plugin"
            )
        if purchase_rate == 0:
            health_issues.append(
                "Zero view→purchase rate detected — tracking may be broken"
            )

        audit["health_score"] = round(
            completeness * 0.6
            + (20 if has_attribution else 0)
            + (20 if purchase_rate > 0 else 0),
            1,
        )
        audit["health_issues"] = health_issues

        logger.info(
            f"Conversion audit complete. Health score: {audit['health_score']}%"
        )
        return audit


# Module-level singleton for use by scheduler / notifier
ga4_conversion_auditor = GA4ConversionAuditor()
