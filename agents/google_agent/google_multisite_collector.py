import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import requests
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest, OrderBy
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
GSC_SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/webmasters",
]
CLOUD_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

DEFAULT_REQUIRED_APIS = [
    "analyticsdata.googleapis.com",
    "searchconsole.googleapis.com",
    "webmasters.googleapis.com",
    "serviceusage.googleapis.com",
    "pagespeedonline.googleapis.com",
]


class GoogleMultisiteCollector:
    def __init__(self, credentials_path: str, google_api_key: str = ""):
        self.credentials_path = credentials_path
        self.google_api_key = google_api_key or os.getenv("GOOGLE_API_KEY", "")
        self._ga_client: Optional[BetaAnalyticsDataClient] = None
        self._gsc_service = None
        self._serviceusage = None

    def _ensure_creds(self, scopes: list[str]):
        return service_account.Credentials.from_service_account_file(self.credentials_path, scopes=scopes)

    def _ga(self) -> BetaAnalyticsDataClient:
        if self._ga_client:
            return self._ga_client
        creds = self._ensure_creds(GA4_SCOPES)
        self._ga_client = BetaAnalyticsDataClient(credentials=creds)
        return self._ga_client

    def _gsc(self):
        if self._gsc_service:
            return self._gsc_service
        creds = self._ensure_creds(GSC_SCOPES)
        self._gsc_service = build("searchconsole", "v1", credentials=creds)
        return self._gsc_service

    def _serviceusage_client(self):
        if self._serviceusage:
            return self._serviceusage
        creds = self._ensure_creds(CLOUD_SCOPES)
        self._serviceusage = build("serviceusage", "v1", credentials=creds)
        return self._serviceusage

    @staticmethod
    def parse_site_profiles(raw: str) -> List[Dict[str, Any]]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception:
            logger.warning("GOOGLE_SITE_PROFILES_JSON is invalid JSON")
        return []

    def enable_required_apis(self, project_id: str, required_apis: Optional[List[str]] = None) -> Dict[str, Any]:
        if not project_id:
            return {"status": "error", "message": "project_id missing"}

        apis = required_apis or DEFAULT_REQUIRED_APIS
        svc = self._serviceusage_client()
        out = {"project_id": project_id, "enabled": [], "failed": []}

        for api in apis:
            name = f"projects/{project_id}/services/{api}"
            try:
                op = svc.services().enable(name=name).execute()
                out["enabled"].append({"service": api, "operation": op.get("name")})
            except Exception as e:
                out["failed"].append({"service": api, "error": str(e)})

        out["status"] = "success" if not out["failed"] else "partial"
        return out

    def list_accessible_gsc_sites(self) -> List[Dict[str, Any]]:
        svc = self._gsc()
        resp = svc.sites().list().execute()
        return resp.get("siteEntry", [])

    def _ga_report(
        self,
        property_id: str,
        dimensions: List[str],
        metrics: List[str],
        days: int,
        limit: int = 100,
        order_metric: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        end = date.today() - timedelta(days=2)
        start = end - timedelta(days=max(1, days) - 1)
        order_bys = []
        if order_metric:
            order_bys = [OrderBy(metric=OrderBy.MetricOrderBy(metric_name=order_metric), desc=True)]
        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name=d) for d in dimensions],
            metrics=[Metric(name=m) for m in metrics],
            date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
            order_bys=order_bys,
            limit=limit,
        )
        response = self._ga().run_report(request)
        dim_headers = [h.name for h in response.dimension_headers]
        met_headers = [h.name for h in response.metric_headers]
        rows: List[Dict[str, Any]] = []
        for r in response.rows:
            item: Dict[str, Any] = {}
            for i, dv in enumerate(r.dimension_values):
                item[dim_headers[i]] = dv.value
            for i, mv in enumerate(r.metric_values):
                try:
                    item[met_headers[i]] = float(mv.value)
                except Exception:
                    item[met_headers[i]] = mv.value
            rows.append(item)
        return rows

    def fetch_ga4_bundle(self, property_id: str, days: int = 28) -> Dict[str, Any]:
        traffic = self._ga_report(
            property_id,
            dimensions=["date"],
            metrics=["sessions", "activeUsers", "screenPageViews", "newUsers", "conversions", "totalRevenue"],
            days=days,
            limit=days + 5,
        )
        pages = self._ga_report(
            property_id,
            dimensions=["pagePath", "pageTitle"],
            metrics=["screenPageViews", "activeUsers", "conversions", "totalRevenue"],
            days=days,
            limit=200,
            order_metric="screenPageViews",
        )
        countries = self._ga_report(
            property_id,
            dimensions=["country"],
            metrics=["sessions", "activeUsers", "conversions", "totalRevenue"],
            days=days,
            limit=80,
            order_metric="sessions",
        )
        source_medium = self._ga_report(
            property_id,
            dimensions=["sessionSource", "sessionMedium", "sessionDefaultChannelGroup"],
            metrics=["sessions", "activeUsers", "conversions", "totalRevenue"],
            days=days,
            limit=120,
            order_metric="sessions",
        )
        conversions = self._ga_report(
            property_id,
            dimensions=["eventName"],
            metrics=["eventCount", "conversions", "totalRevenue"],
            days=days,
            limit=120,
            order_metric="eventCount",
        )

        return {
            "property_id": property_id,
            "days": days,
            "traffic": traffic,
            "pages": pages,
            "countries": countries,
            "source_medium": source_medium,
            "conversion_events": conversions,
        }

    def _gsc_query(self, site_url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        svc = self._gsc()
        return svc.searchanalytics().query(siteUrl=site_url, body=body).execute()

    def fetch_gsc_bundle(self, site_url: str, days: int = 28) -> Dict[str, Any]:
        end = date.today() - timedelta(days=3)
        start = end - timedelta(days=max(1, days) - 1)
        base = {"startDate": start.isoformat(), "endDate": end.isoformat(), "rowLimit": 250}

        page_rows = self._gsc_query(site_url, {**base, "dimensions": ["page"]}).get("rows", [])
        country_rows = self._gsc_query(site_url, {**base, "dimensions": ["country"]}).get("rows", [])
        query_rows = self._gsc_query(site_url, {**base, "dimensions": ["query"]}).get("rows", [])
        appearance_rows = self._gsc_query(site_url, {**base, "dimensions": ["searchAppearance"]}).get("rows", [])

        indexed_pages_estimate = len({(r.get("keys") or [None])[0] for r in page_rows if r.get("keys")})

        schema_flags = {
            "product_results": False,
            "merchant_listings": False,
            "breadcrumb": False,
            "review_snippet": False,
            "faq": False,
        }
        for row in appearance_rows:
            val = " ".join(row.get("keys", [])).lower()
            if "product" in val:
                schema_flags["product_results"] = True
            if "merchant" in val or "shopping" in val:
                schema_flags["merchant_listings"] = True
            if "breadcrumb" in val:
                schema_flags["breadcrumb"] = True
            if "review" in val:
                schema_flags["review_snippet"] = True
            if "faq" in val:
                schema_flags["faq"] = True

        return {
            "site_url": site_url,
            "days": days,
            "pages": page_rows,
            "countries": country_rows,
            "queries": query_rows,
            "search_appearance": appearance_rows,
            "indexed_pages_estimate": indexed_pages_estimate,
            "schema_signals": schema_flags,
        }

    def fetch_cwv_snapshot(self, url: str, strategy: str = "mobile") -> Dict[str, Any]:
        if not self.google_api_key:
            return {"status": "skipped", "reason": "GOOGLE_API_KEY missing"}
        endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        params = {"url": url, "strategy": strategy, "category": "performance", "key": self.google_api_key}
        resp = requests.get(endpoint, params=params, timeout=45)
        resp.raise_for_status()
        data = resp.json()
        lr = (data.get("lighthouseResult") or {}).get("audits") or {}
        cwv = {
            "lcp_ms": ((lr.get("largest-contentful-paint") or {}).get("numericValue")),
            "cls": ((lr.get("cumulative-layout-shift") or {}).get("numericValue")),
            "inp_ms": ((lr.get("interaction-to-next-paint") or {}).get("numericValue")),
            "fcp_ms": ((lr.get("first-contentful-paint") or {}).get("numericValue")),
            "tbt_ms": ((lr.get("total-blocking-time") or {}).get("numericValue")),
        }
        return {"status": "success", "url": url, "strategy": strategy, "core_web_vitals": cwv}

    def fetch_woocommerce_products(self, base_url: str, ck: str, cs: str, per_page: int = 100) -> Dict[str, Any]:
        if not (base_url and ck and cs):
            return {"status": "skipped", "reason": "Woo credentials missing"}
        api = f"{base_url.rstrip('/')}/wp-json/wc/v3/products"
        resp = requests.get(api, params={"per_page": per_page, "consumer_key": ck, "consumer_secret": cs}, timeout=45)
        resp.raise_for_status()
        products = resp.json()
        return {
            "status": "success",
            "product_count": len(products),
            "products": [
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "sku": p.get("sku"),
                    "price": p.get("price"),
                    "stock_status": p.get("stock_status"),
                    "permalink": p.get("permalink"),
                }
                for p in products
            ],
        }

    def fetch_all_sites(self, site_profiles: List[Dict[str, Any]], days: int = 28) -> Dict[str, Any]:
        result = {
            "fetched_at": date.today().isoformat(),
            "credentials_path": self.credentials_path,
            "sites": [],
            "errors": [],
            "accessible_gsc_sites": [],
        }
        try:
            result["accessible_gsc_sites"] = self.list_accessible_gsc_sites()
        except Exception as e:
            result["errors"].append({"scope": "gsc_list_sites", "error": str(e)})

        for profile in site_profiles:
            site_id = profile.get("site_id") or profile.get("domain") or "unknown"
            site_out: Dict[str, Any] = {"site_id": site_id, "profile": profile, "status": "success"}

            ga4_property_id = str(profile.get("ga4_property_id", "")).strip()
            gsc_site_url = str(profile.get("gsc_site_url", "")).strip()
            canonical_url = str(profile.get("canonical_url", "")).strip()

            try:
                if ga4_property_id:
                    site_out["ga4"] = self.fetch_ga4_bundle(ga4_property_id, days=days)
            except Exception as e:
                site_out.setdefault("errors", []).append({"scope": "ga4", "error": str(e)})

            try:
                if gsc_site_url:
                    site_out["gsc"] = self.fetch_gsc_bundle(gsc_site_url, days=days)
            except Exception as e:
                site_out.setdefault("errors", []).append({"scope": "gsc", "error": str(e)})

            try:
                cwv_url = canonical_url or profile.get("base_url") or ""
                if cwv_url:
                    site_out["core_web_vitals"] = self.fetch_cwv_snapshot(cwv_url, strategy="mobile")
            except Exception as e:
                site_out.setdefault("errors", []).append({"scope": "core_web_vitals", "error": str(e)})

            try:
                site_out["woocommerce_products"] = self.fetch_woocommerce_products(
                    base_url=str(profile.get("woocommerce_url", "")).strip(),
                    ck=str(profile.get("wc_ck", "")).strip(),
                    cs=str(profile.get("wc_cs", "")).strip(),
                    per_page=int(profile.get("wc_per_page", 100)),
                )
            except Exception as e:
                site_out.setdefault("errors", []).append({"scope": "woocommerce_products", "error": str(e)})

            if site_out.get("errors"):
                site_out["status"] = "partial"
            result["sites"].append(site_out)

        return result
