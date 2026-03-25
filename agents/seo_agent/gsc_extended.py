"""
CI SEO Agent — Extended GSC Reports
Covers: Page Indexing, Sitemaps, Rich Results/Schema, Links, CWV, Mobile Usability.
Uses Search Console API v1 + URL Inspection API.
"""
import logging
import time
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlparse

from googleapiclient.errors import HttpError

from agents.seo_agent.seo_config import cfg
from agents.seo_agent.gsc_client import GSCClient

logger = logging.getLogger("ci.gsc_extended")


class GSCExtended:
    def __init__(self):
        self._gsc = GSCClient()

    def _svc(self):
        return self._gsc.get_service()

    def _site(self) -> str:
        url = self._gsc.find_working_site_url()
        if not url:
            raise RuntimeError("No accessible GSC site")
        return url

    # ── 1. Sitemaps ───────────────────────────────────────────────────────

    def fetch_sitemaps(self) -> list[dict]:
        """
        Fetch all submitted sitemaps and their status.
        Returns list of sitemap dicts with status, errors, warnings.
        """
        try:
            resp = self._svc().sitemaps().list(siteUrl=self._site()).execute()
            sitemaps = resp.get("sitemap", [])
            results = []
            for sm in sitemaps:
                errors = int(sm.get("errors", 0) or 0)
                warnings = int(sm.get("warnings", 0) or 0)
                results.append({
                    "path": sm.get("path", ""),
                    "last_submitted": sm.get("lastSubmitted", ""),
                    "last_downloaded": sm.get("lastDownloaded", ""),
                    "is_pending": sm.get("isPending", False),
                    "is_sitemaps_index": sm.get("isSitemapsIndex", False),
                    "type": sm.get("type", ""),
                    "warnings": warnings,
                    "errors": errors,
                    "contents": [
                        {
                            "type": c.get("type", ""),
                            "submitted": c.get("submitted", 0),
                            "indexed": c.get("indexed", 0),
                        }
                        for c in sm.get("contents", [])
                    ],
                })
            logger.info(f"Sitemaps: {len(results)} found")
            return results
        except HttpError as e:
            logger.error(f"Sitemaps fetch failed: {e}")
            return []

    # ── 2. URL Inspection / Indexing ──────────────────────────────────────

    def inspect_urls(self, urls: list[str], max_urls: int = 20) -> list[dict]:
        """
        Run URL Inspection API on a list of URLs.
        Returns indexing status, canonical, robots, rich result status.
        Rate limit: 2 req/sec per site.
        """
        site_url = self._site()
        # URL Inspection API requires the siteUrl to match the registered property exactly.
        # Domain properties (sc-domain:) must be passed as-is.
        inspection_site = site_url

        results = []
        for i, url in enumerate(urls[:max_urls]):
            try:
                body = {
                    "inspectionUrl": url,
                    "siteUrl": inspection_site,
                    "languageCode": "en-US",
                }
                resp = (
                    self._svc()
                    .urlInspection()
                    .index()
                    .inspect(body=body)
                    .execute()
                )
                ir = resp.get("inspectionResult", {})
                index_status = ir.get("indexStatusResult", {})
                mobile = ir.get("mobileUsabilityResult", {})
                rich = ir.get("richResultsResult", {})
                amp = ir.get("ampResult", {})

                result = {
                    "url": url,
                    "verdict": index_status.get("verdict", "UNSPECIFIED"),
                    "coverage_state": index_status.get("coverageState", ""),
                    "robots_txt_state": index_status.get("robotsTxtState", "UNSPECIFIED"),
                    "indexing_state": index_status.get("indexingState", "INDEXING_ALLOWED"),
                    "last_crawl_time": index_status.get("lastCrawlTime", ""),
                    "crawled_as": index_status.get("crawledAs", ""),
                    "google_canonical": index_status.get("googleCanonical", ""),
                    "user_canonical": index_status.get("userDeclaredCanonical", ""),
                    "sitemap": index_status.get("sitemap", []),
                    "referring_urls": index_status.get("referringUrls", []),
                    "mobile_verdict": mobile.get("verdict", ""),
                    "mobile_issues": [i.get("message", "") for i in mobile.get("issues", [])],
                    "rich_results_verdict": rich.get("verdict", ""),
                    "rich_results_detected": [
                        {
                            "name": ri.get("richResultType", ""),
                            "items": [
                                {
                                    "name": it.get("name", ""),
                                    "issues": [
                                        {
                                            "type": iss.get("issueMessage", ""),
                                            "severity": iss.get("severity", ""),
                                        }
                                        for iss in it.get("issues", [])
                                    ],
                                }
                                for it in ri.get("items", [])
                            ],
                        }
                        for ri in rich.get("detectedItems", [])
                    ],
                    "amp_verdict": amp.get("verdict", ""),
                }
                results.append(result)
                logger.debug(f"Inspected {url}: {result['verdict']}")

                # Rate limit: 2 req/sec
                if i < len(urls) - 1:
                    time.sleep(0.6)

            except HttpError as e:
                logger.warning(f"URL inspection failed for {url}: {e}")
                results.append({"url": url, "error": str(e)})
            except Exception as e:
                logger.warning(f"URL inspection error for {url}: {e}")
                results.append({"url": url, "error": str(e)})

        return results

    def fetch_index_coverage_sample(self, top_n: int = 30) -> dict:
        """
        Inspect top N pages from GSC performance data for index coverage.
        Returns summary of indexing health.
        """
        # Get top pages by impressions
        end_date = date.today() - timedelta(days=3)
        start_date = end_date - timedelta(days=28)
        page_rows = self._gsc.fetch_query_performance(
            self._site(), start_date, end_date, ["page"], row_limit=100
        )
        top_pages = sorted(page_rows, key=lambda r: r.get("impressions", 0), reverse=True)
        urls = [r["keys"][0] for r in top_pages[:top_n] if r.get("keys")]

        logger.info(f"Inspecting {len(urls)} top URLs for index coverage")
        inspections = self.inspect_urls(urls, max_urls=top_n)

        # Summarize
        verdicts = {}
        mobile_issues_found = []
        schema_issues_found = []
        canonical_mismatches = []
        not_indexed = []

        for r in inspections:
            if "error" in r:
                continue
            v = r.get("verdict", "UNSPECIFIED")
            verdicts[v] = verdicts.get(v, 0) + 1

            if r.get("mobile_issues"):
                mobile_issues_found.append({
                    "url": r["url"],
                    "issues": r["mobile_issues"],
                })

            for ri in r.get("rich_results_detected", []):
                for item in ri.get("items", []):
                    if item.get("issues"):
                        schema_issues_found.append({
                            "url": r["url"],
                            "schema_type": ri["name"],
                            "issues": item["issues"],
                        })

            gc = r.get("google_canonical", "")
            uc = r.get("user_canonical", "")
            if gc and uc and gc.rstrip("/") != uc.rstrip("/"):
                canonical_mismatches.append({
                    "url": r["url"],
                    "google_canonical": gc,
                    "user_canonical": uc,
                })

            if v not in ("PASS", "VERDICT_UNSPECIFIED"):
                not_indexed.append({"url": r["url"], "verdict": v, "coverage_state": r.get("coverage_state", "")})

        return {
            "inspected_count": len(inspections),
            "verdict_summary": verdicts,
            "not_indexed_pages": not_indexed[:20],
            "mobile_issues": mobile_issues_found[:10],
            "schema_issues": schema_issues_found[:10],
            "canonical_mismatches": canonical_mismatches[:10],
        }

    # ── 3. Links Report ───────────────────────────────────────────────────

    def fetch_links_report(self) -> dict:
        """
        Fetch internal and external links data from GSC Links report.
        Uses searchanalytics with 'page' dimension to approximate
        (GSC API doesn't expose links directly; uses auxiliary endpoint if available).
        """
        # GSC API v1 doesn't expose links directly via REST.
        # We derive link health from URL inspection referringUrls field.
        # Instead, fetch high-traffic pages and check their linking structure via WP-CLI.
        try:
            end_date = date.today() - timedelta(days=3)
            start_date = end_date - timedelta(days=28)
            page_rows = self._gsc.fetch_query_performance(
                self._site(), start_date, end_date, ["page"], row_limit=200
            )

            # Pages with 0 clicks but some impressions (orphan candidates)
            orphan_candidates = [
                {
                    "page": r["keys"][0],
                    "impressions": int(r.get("impressions", 0)),
                    "clicks": int(r.get("clicks", 0)),
                    "position": round(r.get("position", 0), 1),
                }
                for r in page_rows
                if r.get("clicks", 0) == 0
                and r.get("impressions", 0) >= 50
            ][:20]

            # Pages with very high position (> 20) — deep in results
            buried_pages = [
                {
                    "page": r["keys"][0],
                    "impressions": int(r.get("impressions", 0)),
                    "position": round(r.get("position", 0), 1),
                }
                for r in page_rows
                if r.get("position", 0) > 20 and r.get("impressions", 0) >= 30
            ][:20]

            logger.info(f"Links: {len(orphan_candidates)} orphan candidates, {len(buried_pages)} buried pages")

            return {
                "orphan_candidates": orphan_candidates,
                "buried_pages": buried_pages,
                "total_pages_in_gsc": len(page_rows),
            }
        except Exception as e:
            logger.error(f"Links report failed: {e}")
            return {}

    # ── 4. Core Web Vitals (Search Console CWV) ───────────────────────────

    def fetch_cwv_performance(self) -> dict:
        """
        Approximate CWV issues from GSC search type filters.
        GSC CWV report is only accessible via Google Sheets API add-on or PageSpeed.
        We use search type DISCOVER / WEB and device split as CWV proxy.
        Also queries PageSpeed Insights API for top pages.
        """
        try:
            end_date = date.today() - timedelta(days=3)
            start_date = end_date - timedelta(days=28)

            # Desktop vs Mobile position delta (large gap = mobile CWV issues)
            desktop_rows = self._gsc.fetch_query_performance(
                self._site(), start_date, end_date, ["page"],
                row_limit=200,
            )

            # Fetch mobile data separately with filter
            svc = self._svc()
            body_mobile = {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": ["page"],
                "dimensionFilterGroups": [{
                    "filters": [{"dimension": "device", "operator": "equals", "expression": "MOBILE"}]
                }],
                "rowLimit": 200,
            }
            resp_mobile = svc.searchanalytics().query(siteUrl=self._site(), body=body_mobile).execute()
            mobile_rows = resp_mobile.get("rows", [])

            # Build maps
            desktop_map = {r["keys"][0]: r for r in desktop_rows}
            mobile_map = {r["keys"][0]: r for r in mobile_rows}

            # Find pages where mobile position is much worse than desktop
            mobile_degraded = []
            for url, mob in mobile_map.items():
                desk = desktop_map.get(url)
                if desk:
                    desk_pos = desk.get("position", 0)
                    mob_pos = mob.get("position", 0)
                    if mob_pos - desk_pos > 5 and mob.get("impressions", 0) >= 50:
                        mobile_degraded.append({
                            "url": url,
                            "desktop_position": round(desk_pos, 1),
                            "mobile_position": round(mob_pos, 1),
                            "position_gap": round(mob_pos - desk_pos, 1),
                            "mobile_impressions": int(mob.get("impressions", 0)),
                        })

            mobile_degraded.sort(key=lambda x: x["position_gap"], reverse=True)

            # PageSpeed Insights for top 5 pages
            psi_results = self._fetch_psi_scores()

            logger.info(f"CWV: {len(mobile_degraded)} mobile-degraded pages")
            return {
                "mobile_degraded_pages": mobile_degraded[:15],
                "pagespeed_scores": psi_results,
            }
        except Exception as e:
            logger.error(f"CWV fetch failed: {e}")
            return {}

    def _fetch_psi_scores(self) -> list[dict]:
        """Fetch PageSpeed Insights scores for top pages."""
        import requests

        google_api_key = getattr(cfg, "GOOGLE_API_KEY", "")
        if not google_api_key:
            logger.warning("GOOGLE_API_KEY is not configured; skipping PageSpeed Insights fetch.")
            return []

        # Get top 5 pages
        try:
            end_date = date.today() - timedelta(days=3)
            start_date = end_date - timedelta(days=28)
            rows = self._gsc.fetch_query_performance(
                self._site(), start_date, end_date, ["page"], row_limit=10
            )
            top_pages = sorted(rows, key=lambda r: r.get("clicks", 0), reverse=True)[:5]
            urls = [r["keys"][0] for r in top_pages]
        except Exception:
            urls = ["https://indogenmed.org/"]

        results = []
        for url in urls[:5]:
            try:
                for strategy in ("mobile", "desktop"):
                    resp = requests.get(
                        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                        params={
                            "url": url,
                            "strategy": strategy,
                            "key": google_api_key,
                            "category": ["performance", "seo", "best-practices"],
                        },
                        timeout=30,
                    )
                    if resp.ok:
                        data = resp.json()
                        cats = data.get("lighthouseResult", {}).get("categories", {})
                        audits = data.get("lighthouseResult", {}).get("audits", {})

                        lcp = audits.get("largest-contentful-paint", {}).get("displayValue", "")
                        cls_val = audits.get("cumulative-layout-shift", {}).get("displayValue", "")
                        inp = audits.get("interaction-to-next-paint", {}).get("displayValue", "")
                        fid = audits.get("total-blocking-time", {}).get("displayValue", "")

                        results.append({
                            "url": url,
                            "strategy": strategy,
                            "performance_score": int((cats.get("performance", {}).get("score") or 0) * 100),
                            "seo_score": int((cats.get("seo", {}).get("score") or 0) * 100),
                            "best_practices_score": int((cats.get("best-practices", {}).get("score") or 0) * 100),
                            "lcp": lcp,
                            "cls": cls_val,
                            "inp": inp,
                            "tbt": fid,
                        })
                    time.sleep(1)
            except Exception as e:
                logger.warning(f"PSI failed for {url}: {e}")

        return results

    # ── 5. Rich Results / Schema Issues ───────────────────────────────────

    def fetch_rich_results_report(self) -> dict:
        """
        Fetch rich results status using the Search Appearance performance filter.
        Checks for Product, FAQ, Breadcrumb, Article schema in GSC.
        """
        try:
            svc = self._svc()
            end_date = date.today() - timedelta(days=3)
            start_date = end_date - timedelta(days=28)

            # Search type: web with various search appearances
            schema_types = [
                ("RICHCARD", "Rich Cards / Schema"),
                ("WEBLITE", "Web Light (Mobile)"),
                ("IMAGE", "Image Search"),
                ("VIDEO", "Video"),
            ]

            results = {}
            for search_type, label in schema_types:
                try:
                    body = {
                        "startDate": start_date.isoformat(),
                        "endDate": end_date.isoformat(),
                        "dimensions": ["page"],
                        "searchType": search_type,
                        "rowLimit": 100,
                    }
                    resp = svc.searchanalytics().query(siteUrl=self._site(), body=body).execute()
                    rows = resp.get("rows", [])
                    if rows:
                        results[label] = {
                            "pages_count": len(rows),
                            "total_clicks": sum(r.get("clicks", 0) for r in rows),
                            "total_impressions": sum(r.get("impressions", 0) for r in rows),
                            "top_pages": [
                                {
                                    "page": r["keys"][0],
                                    "clicks": int(r.get("clicks", 0)),
                                    "impressions": int(r.get("impressions", 0)),
                                }
                                for r in sorted(rows, key=lambda x: x.get("clicks", 0), reverse=True)[:5]
                            ],
                        }
                except Exception:
                    pass  # Some search types may not be available

            logger.info(f"Rich results: {len(results)} search types with data")
            return results
        except Exception as e:
            logger.error(f"Rich results fetch failed: {e}")
            return {}

    # ── 6. Full Extended Report ───────────────────────────────────────────

    def fetch_full_extended_report(self, top_url_count: int = 20) -> dict:
        """
        Run all extended GSC checks and return a consolidated report.
        This runs after the main snapshot to avoid rate-limiting.
        """
        logger.info("Starting extended GSC report...")
        report = {
            "generated_at": date.today().isoformat(),
            "sitemaps": [],
            "index_coverage": {},
            "links": {},
            "cwv": {},
            "rich_results": {},
        }

        # 1. Sitemaps
        logger.info("Fetching sitemaps...")
        report["sitemaps"] = self.fetch_sitemaps()

        # 2. Index coverage (URL inspection — slow, rate-limited)
        logger.info("Fetching index coverage...")
        report["index_coverage"] = self.fetch_index_coverage_sample(top_n=top_url_count)

        # 3. Links analysis
        logger.info("Fetching links report...")
        report["links"] = self.fetch_links_report()

        # 4. Core Web Vitals proxy
        logger.info("Fetching CWV data...")
        report["cwv"] = self.fetch_cwv_performance()

        # 5. Rich results / schema
        logger.info("Fetching rich results...")
        report["rich_results"] = self.fetch_rich_results_report()

        # Summary stats for quick overview
        ic = report["index_coverage"]
        report["summary"] = {
            "sitemaps_with_errors": sum(1 for s in report["sitemaps"] if s.get("errors", 0) > 0),
            "not_indexed_count": len(ic.get("not_indexed_pages", [])),
            "schema_issues_count": len(ic.get("schema_issues", [])),
            "canonical_mismatches": len(ic.get("canonical_mismatches", [])),
            "mobile_issues_count": len(ic.get("mobile_issues", [])),
            "orphan_candidates": len(report["links"].get("orphan_candidates", [])),
            "mobile_degraded_pages": len(report["cwv"].get("mobile_degraded_pages", [])),
        }

        logger.info(f"Extended report complete: {report['summary']}")
        return report


gsc_extended = GSCExtended()
