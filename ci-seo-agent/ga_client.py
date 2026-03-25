"""
CI SEO Agent — Google Analytics 4 Client
Fetches GA4 metrics using the same service account as GSC.
Property: 250072994 (IndogenMed.org — G-LRP6DLLB0Q)
"""
import logging
from datetime import date, timedelta
from typing import Optional

from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    OrderBy,
    RunRealtimeReportRequest,
)

from config import cfg

logger = logging.getLogger("ci.ga_client")

GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


class GA4Client:
    def __init__(self):
        self._client: Optional[BetaAnalyticsDataClient] = None

    def _get_client(self) -> BetaAnalyticsDataClient:
        if self._client:
            return self._client
        try:
            creds = service_account.Credentials.from_service_account_file(
                cfg.GSC_SERVICE_ACCOUNT_FILE,
                scopes=GA4_SCOPES,
            )
            self._client = BetaAnalyticsDataClient(credentials=creds)
            logger.info(f"GA4 client initialised — property {cfg.GA4_PROPERTY_ID}")
            return self._client
        except Exception as e:
            logger.error(f"GA4 client init failed: {e}")
            # Create a FLAG_FOR_REVIEW action so a human can add the service account to GA4 property
            try:
                from vector_store import vector_store
                vector_store.create_action_item(
                    action_type="FLAG_FOR_REVIEW",
                    priority="critical",
                    title="GA4 Data API access failure",
                    description=(
                        f"GA4 client failed to initialise: {str(e)[:300]}. "
                        "Ensure the service account in config has Viewer access to the GA4 property."
                    ),
                    target_url="https://analytics.google.com/analytics/web/",
                    data_signals={"error": str(e)[:500]},
                )
                logger.info("Created FLAG_FOR_REVIEW action for GA4 access failure")
            except Exception as e2:
                logger.warning(f"Could not create FLAG_FOR_REVIEW action for GA4 failure: {e2}")
            raise

    # ── Core runner ────────────────────────────────────────────────────────

    def _run_report(
        self,
        dimensions: list[str],
        metrics: list[str],
        date_ranges: list,
        order_bys: list = None,
        limit: int = 100,
        dimension_filter=None,
    ) -> list[dict]:
        """Run a GA4 report and return rows as list of dicts."""
        client = self._get_client()
        req_kwargs = dict(
            property=f"properties/{cfg.GA4_PROPERTY_ID}",
            dimensions=[Dimension(name=d) for d in dimensions],
            metrics=[Metric(name=m) for m in metrics],
            date_ranges=date_ranges,
            order_bys=order_bys or [],
            limit=limit,
        )
        if dimension_filter:
            req_kwargs["dimension_filter"] = dimension_filter

        request = RunReportRequest(**req_kwargs)
        response = client.run_report(request)

        dim_headers = [h.name for h in response.dimension_headers]
        met_headers = [h.name for h in response.metric_headers]

        rows = []
        for row in response.rows:
            row_dict = {}
            for i, dv in enumerate(row.dimension_values):
                row_dict[dim_headers[i]] = dv.value
            for i, mv in enumerate(row.metric_values):
                try:
                    row_dict[met_headers[i]] = float(mv.value)
                except ValueError:
                    row_dict[met_headers[i]] = mv.value
            rows.append(row_dict)
        return rows

    # ── Individual fetchers ────────────────────────────────────────────────

    def fetch_traffic_overview(self, days: int = 28) -> dict:
        """Overall traffic metrics + daily trend."""
        end = date.today() - timedelta(days=2)  # GA4 ~2 day delay
        start = end - timedelta(days=days - 1)
        dr = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        daily = self._run_report(
            dimensions=["date"],
            metrics=[
                "sessions", "activeUsers", "screenPageViews",
                "bounceRate", "averageSessionDuration", "newUsers",
            ],
            date_ranges=dr,
            order_bys=[OrderBy(
                dimension=OrderBy.DimensionOrderBy(dimension_name="date"),
            )],
            limit=days + 5,
        )

        total_sessions   = sum(r.get("sessions", 0)               for r in daily)
        total_users      = sum(r.get("activeUsers", 0)             for r in daily)
        total_pageviews  = sum(r.get("screenPageViews", 0)         for r in daily)
        new_users        = sum(r.get("newUsers", 0)                for r in daily)
        avg_bounce       = sum(r.get("bounceRate", 0)              for r in daily) / len(daily) if daily else 0
        avg_duration     = sum(r.get("averageSessionDuration", 0)  for r in daily) / len(daily) if daily else 0

        return {
            "total_sessions":             int(total_sessions),
            "total_users":                int(total_users),
            "total_pageviews":            int(total_pageviews),
            "new_users":                  int(new_users),
            "returning_users":            int(total_users - new_users),
            "avg_bounce_rate_pct":        round(avg_bounce * 100, 2),
            "avg_session_duration_sec":   round(avg_duration, 1),
            "daily_trend":                daily,
            "days":                       days,
            "date_range": {
                "start": start.isoformat(),
                "end":   end.isoformat(),
            },
        }

    def fetch_page_performance(self, days: int = 28, limit: int = 100) -> list[dict]:
        """Per-page engagement metrics."""
        end   = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        dr    = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        return self._run_report(
            dimensions=["pagePath", "pageTitle"],
            metrics=[
                "screenPageViews", "activeUsers", "bounceRate",
                "averageSessionDuration", "engagementRate",
                "scrolledUsers",
            ],
            date_ranges=dr,
            order_bys=[OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
                desc=True,
            )],
            limit=limit,
        )

    def fetch_source_channels(self, days: int = 28) -> list[dict]:
        """Traffic by default channel grouping."""
        end   = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        dr    = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        return self._run_report(
            dimensions=["sessionDefaultChannelGroup", "sessionSource", "sessionMedium"],
            metrics=["sessions", "activeUsers", "newUsers", "bounceRate"],
            date_ranges=dr,
            order_bys=[OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                desc=True,
            )],
            limit=30,
        )

    def fetch_landing_pages(self, days: int = 28, limit: int = 50) -> list[dict]:
        """Top landing pages (entry pages)."""
        end   = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        dr    = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        return self._run_report(
            dimensions=["landingPagePlusQueryString"],
            metrics=[
                "sessions", "activeUsers", "bounceRate",
                "averageSessionDuration", "engagementRate",
            ],
            date_ranges=dr,
            order_bys=[OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                desc=True,
            )],
            limit=limit,
        )

    def fetch_ecommerce_metrics(self, days: int = 28) -> dict:
        """WooCommerce conversion + revenue metrics."""
        end   = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        dr    = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        try:
            rows = self._run_report(
                dimensions=["date"],
                metrics=[
                    "transactions", "totalRevenue",
                    "ecommercePurchases", "addToCarts",
                    "checkouts", "cartToViewRate",
                ],
                date_ranges=dr,
                order_bys=[OrderBy(
                    dimension=OrderBy.DimensionOrderBy(dimension_name="date"),
                )],
                limit=days + 5,
            )
            total_revenue      = sum(r.get("totalRevenue", 0)      for r in rows)
            total_transactions = sum(r.get("transactions", 0)      for r in rows)
            total_add_to_cart  = sum(r.get("addToCarts", 0)        for r in rows)
            total_checkouts    = sum(r.get("checkouts", 0)         for r in rows)

            return {
                "total_transactions":  int(total_transactions),
                "total_revenue_usd":   round(total_revenue, 2),
                "avg_order_value_usd": round(
                    total_revenue / total_transactions, 2
                ) if total_transactions else 0,
                "total_add_to_cart":   int(total_add_to_cart),
                "total_checkouts":     int(total_checkouts),
                "checkout_conversion_pct": round(
                    (total_transactions / total_checkouts * 100), 2
                ) if total_checkouts else 0,
                "daily_trend": rows,
            }
        except Exception as e:
            logger.warning(f"Ecommerce metrics fetch failed (may need GA4 e-commerce setup): {e}")
            return {
                "total_transactions": 0, "total_revenue_usd": 0,
                "avg_order_value_usd": 0, "total_add_to_cart": 0,
                "total_checkouts": 0, "checkout_conversion_pct": 0,
            }

    def fetch_geo_performance(self, days: int = 28) -> list[dict]:
        """Traffic by country — important for IndogenMed's 15-country coverage."""
        end   = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        dr    = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        return self._run_report(
            dimensions=["country"],
            metrics=["sessions", "activeUsers", "bounceRate", "averageSessionDuration"],
            date_ranges=dr,
            order_bys=[OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                desc=True,
            )],
            limit=25,
        )

    def fetch_device_breakdown(self, days: int = 28) -> list[dict]:
        """Traffic by device category."""
        end   = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        dr    = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        return self._run_report(
            dimensions=["deviceCategory"],
            metrics=[
                "sessions", "activeUsers", "bounceRate",
                "averageSessionDuration", "engagementRate",
            ],
            date_ranges=dr,
            order_bys=[OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                desc=True,
            )],
            limit=5,
        )

    def fetch_search_queries(self, days: int = 28, limit: int = 50) -> list[dict]:
        """Organic search queries driving traffic (GA4 organic channel)."""
        end   = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        dr    = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        try:
            # sessionSource = google AND sessionMedium = organic
            from google.analytics.data_v1beta.types import (
                FilterExpression, FilterExpressionList, Filter
            )
            dim_filter = FilterExpression(
                and_group=FilterExpressionList(
                    expressions=[
                        FilterExpression(filter=Filter(
                            field_name="sessionMedium",
                            string_filter=Filter.StringFilter(value="organic"),
                        )),
                    ]
                )
            )
            return self._run_report(
                dimensions=["sessionSource", "firstUserDefaultChannelGroup"],
                metrics=["sessions", "activeUsers", "bounceRate"],
                date_ranges=dr,
                dimension_filter=dim_filter,
                order_bys=[OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                    desc=True,
                )],
                limit=limit,
            )
        except Exception as e:
            logger.warning(f"GA4 organic search queries failed: {e}")
            return []

    def fetch_user_retention(self, days: int = 28) -> dict:
        """New vs returning user breakdown + engagement rate."""
        end   = date.today() - timedelta(days=2)
        start = end - timedelta(days=days - 1)
        dr    = [DateRange(start_date=start.isoformat(), end_date=end.isoformat())]

        try:
            rows = self._run_report(
                dimensions=["newVsReturning"],
                metrics=["sessions", "activeUsers", "engagementRate", "averageSessionDuration"],
                date_ranges=dr,
                order_bys=[OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                    desc=True,
                )],
                limit=5,
            )
            result = {}
            for row in rows:
                key = row.get("newVsReturning", "unknown").lower().replace(" ", "_")
                result[key] = {
                    "sessions":                  int(row.get("sessions", 0)),
                    "users":                     int(row.get("activeUsers", 0)),
                    "engagement_rate_pct":        round(row.get("engagementRate", 0) * 100, 2),
                    "avg_session_duration_sec":  round(row.get("averageSessionDuration", 0), 1),
                }
            return result
        except Exception as e:
            logger.warning(f"User retention fetch failed: {e}")
            return {}

    # ── Full snapshot ──────────────────────────────────────────────────────

    def fetch_full_snapshot(self, days: int = 28) -> dict:
        """
        Fetch complete GA4 snapshot for analysis and vector storage.
        Tolerates individual section failures — always returns a partial snapshot.
        """
        logger.info(f"Fetching GA4 full snapshot — last {days} days...")
        snapshot: dict = {
            "fetched_at":    (date.today() - timedelta(days=2)).isoformat(),
            "property_id":   cfg.GA4_PROPERTY_ID,
            "measurement_id": "G-LRP6DLLB0Q",
            "days":          days,
        }

        sections = [
            ("traffic_overview",  lambda: self.fetch_traffic_overview(days)),
            ("page_performance",  lambda: self.fetch_page_performance(days)),
            ("source_channels",   lambda: self.fetch_source_channels(days)),
            ("landing_pages",     lambda: self.fetch_landing_pages(days)),
            ("ecommerce",         lambda: self.fetch_ecommerce_metrics(days)),
            ("geo",               lambda: self.fetch_geo_performance(days)),
            ("devices",           lambda: self.fetch_device_breakdown(days)),
            ("user_retention",    lambda: self.fetch_user_retention(days)),
        ]

        for key, fn in sections:
            try:
                snapshot[key] = fn()
                if isinstance(snapshot[key], dict):
                    total = snapshot[key].get("total_sessions", snapshot[key].get("total_transactions", ""))
                    logger.info(f"GA4 {key}: ok{' — ' + str(total) if total != '' else ''}")
                else:
                    logger.info(f"GA4 {key}: {len(snapshot[key])} rows")
            except Exception as e:
                logger.error(f"GA4 {key} failed: {e}")
                snapshot[key] = {} if key in ("traffic_overview", "ecommerce", "user_retention") else []

        logger.info("GA4 full snapshot complete")
        return snapshot

    def compute_summary_stats(self, snapshot: dict) -> dict:
        """Compute summary statistics from a GA4 snapshot."""
        ov = snapshot.get("traffic_overview", {})
        ec = snapshot.get("ecommerce", {})

        # Top pages by views
        pages = snapshot.get("page_performance", [])
        top_pages = sorted(pages, key=lambda r: r.get("screenPageViews", 0), reverse=True)[:10]

        # High bounce pages (views > 100, bounce > 70%)
        high_bounce = [
            p for p in pages
            if p.get("bounceRate", 0) > 0.70 and p.get("screenPageViews", 0) > 100
        ][:10]

        # Top channels
        channels = snapshot.get("source_channels", [])
        organic_sessions = sum(
            r.get("sessions", 0) for r in channels
            if "organic" in r.get("sessionMedium", "").lower()
            or "organic" in r.get("sessionDefaultChannelGroup", "").lower()
        )

        return {
            "overview": {
                "total_sessions":           ov.get("total_sessions", 0),
                "total_users":              ov.get("total_users", 0),
                "total_pageviews":          ov.get("total_pageviews", 0),
                "new_users":                ov.get("new_users", 0),
                "avg_bounce_rate_pct":      ov.get("avg_bounce_rate_pct", 0),
                "avg_session_duration_sec": ov.get("avg_session_duration_sec", 0),
                "organic_sessions":         int(organic_sessions),
                "organic_pct":              round(
                    organic_sessions / ov.get("total_sessions", 1) * 100, 1
                ) if ov.get("total_sessions") else 0,
            },
            "ecommerce": ec,
            "top_pages_by_views":    top_pages,
            "high_bounce_pages":     high_bounce,
            "top_channels":          channels[:8],
            "top_geos":              snapshot.get("geo", [])[:10],
            "devices":               snapshot.get("devices", []),
            "user_retention":        snapshot.get("user_retention", {}),
        }


ga_client = GA4Client()
