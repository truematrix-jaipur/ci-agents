"""
CI SEO Agent — LLM Analyzer
Uses Claude (Anthropic) as primary, GPT-4o as fallback.
Analyzes GSC data, detects patterns, generates structured action plans.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import anthropic
from openai import OpenAI

from agents.seo_agent.seo_config import cfg
from agents.seo_agent.vector_store import vector_store
from core.llm_gateway.gateway import llm_gateway

logger = logging.getLogger("ci.analyzer")


SYSTEM_PROMPT = """You are an expert SEO and digital analytics analyst for IndogenMed.org, a medical e-commerce website selling prescription medications online across multiple countries (UK, US, Philippines, India, etc.).

The site runs on WordPress + WooCommerce with Woodmart theme, WPML for multilingual support (EN, ES, FR, DE, etc.), and Rank Math Pro for SEO.

Your job is to:
1. Analyze Google Search Console (GSC) data alongside Google Analytics 4 (GA4) data
2. Cross-reference search visibility (impressions, CTR, position) with user behaviour (bounce rate, session duration, engagement rate, conversions)
3. Prioritize actionable improvements by combined SEO + UX impact
4. Generate specific, implementable action items that can be executed programmatically

When generating action items, be SPECIFIC:
- For meta description updates: provide the EXACT new meta description text
- For title updates: provide the EXACT new title
- For content additions: provide specific content recommendations
- For technical fixes: provide exact steps

Action types available for automation:
- UPDATE_META_DESCRIPTION: Update page meta description via WordPress REST API
- UPDATE_PAGE_TITLE: Update page title/SEO title via Rank Math
- ADD_INTERNAL_LINK: Add internal link from one page to another
- CREATE_CONTENT_BRIEF: Generate content brief for new/updated page
- FIX_CANONICAL: Fix canonical URL issues
- UPDATE_SCHEMA: Add/update structured data
- OPTIMIZE_HEADING: Update H1/H2 headings
- FLAG_FOR_REVIEW: Flag page for manual review (complex issues)

Always respond with valid JSON only."""


ANALYSIS_PROMPT_TEMPLATE = """Analyze this combined Google Search Console + Google Analytics 4 data for indogenmed.org and generate a comprehensive SEO + UX action plan.

## GSC Data Summary (Last {days} days)

### Overall Search Metrics
- Total Clicks: {total_clicks:,}
- Total Impressions: {total_impressions:,}
- Average CTR: {avg_ctr}%
- Average Position: {avg_position}

### Top Keywords by Clicks
{top_keywords}

### Low CTR Keywords (High Impressions, Low CTR — Quick Win Opportunities)
{low_ctr_keywords}

### Top Pages by Clicks (GSC)
{top_pages}

### Underperforming Pages (Position 4-20, Low CTR)
{underperforming_pages}

### GSC vs Previous Period (7 days ago)
{comparison}

## GA4 Data Summary (Last {days} days)

### Traffic Overview
{ga_overview}

### Top Pages by Pageviews (with GA4 engagement data)
{ga_top_pages}

### High Bounce Rate Pages (views > 100, bounce > 70%)
{ga_high_bounce}

### Traffic Channels
{ga_channels}

### Geographic Breakdown (top countries)
{ga_geo}

### Device Breakdown
{ga_devices}

### E-commerce Metrics
{ga_ecommerce}

### User Retention (new vs returning)
{ga_retention}

## Instructions

Cross-reference the GSC data (search visibility) with GA4 data (user behaviour) to find:
1. Pages with high GSC impressions + high GA4 bounce rate → poor landing experience
2. Pages with good GSC CTR + low GA4 engagement → content/intent mismatch
3. High-traffic GA4 pages with low GSC rankings → SEO opportunity for organic growth
4. E-commerce conversion bottlenecks visible in both datasets

Return a JSON response with this EXACT structure:

{{
  "executive_summary": "2-3 sentence summary covering both search visibility and user behaviour",
  "key_findings": [
    {{
      "finding": "Specific finding (reference both GSC and GA4 data where applicable)",
      "impact": "high|medium|low",
      "evidence": "Data point(s) supporting this finding",
      "data_source": "gsc|ga4|both"
    }}
  ],
  "action_plan": [
    {{
      "action_type": "UPDATE_META_DESCRIPTION|UPDATE_PAGE_TITLE|ADD_INTERNAL_LINK|CREATE_CONTENT_BRIEF|FIX_CANONICAL|UPDATE_SCHEMA|OPTIMIZE_HEADING|FLAG_FOR_REVIEW",
      "priority": "critical|high|medium|low",
      "title": "Short action title",
      "description": "Detailed description referencing both GSC and GA4 signals",
      "target_url": "https://indogenmed.org/the-specific-page/",
      "target_keyword": "the primary keyword",
      "expected_impact": "What improvement to expect (clicks/CTR/bounce/conversions)",
      "data_signals": {{
        "gsc_impressions": 0,
        "gsc_ctr_pct": 0.0,
        "gsc_position": 0.0,
        "ga4_pageviews": 0,
        "ga4_bounce_pct": 0.0,
        "ga4_avg_duration_sec": 0.0
      }},
      "implementation_data": {{
        "current_value": "current meta description or title",
        "new_value": "EXACT new meta description or title text to use",
        "notes": "any additional implementation notes"
      }}
    }}
  ],
  "quick_wins": ["list of 3-5 quick wins referencing both data sources"],
  "monitoring_alerts": ["list of concerning trends from GSC or GA4"],
  "ga4_insights": {{
    "top_organic_pages": ["pages getting most organic traffic"],
    "conversion_issues": ["pages with good traffic but poor engagement/conversion"],
    "geo_opportunities": ["countries with growth potential based on traffic data"]
  }}
}}

Focus on:
1. Pages where GSC shows high impressions but GA4 shows high bounce → fix meta + landing content
2. Pages ranking 4-10 in GSC with good GA4 engagement → push to page 1
3. Medical/pharma keywords with e-commerce intent
4. Countries in GA4 geo data underrepresented in GSC rankings
5. Mobile vs desktop traffic from GA4 device data
6. E-commerce funnel: add-to-cart → checkout → purchase conversion rates

Return ONLY the JSON, no markdown, no explanation."""


class Analyzer:
    def __init__(self):
        # Per-provider client cache
        self._provider_clients: dict = {}
        # Per-provider disable flags (in-memory)
        self._provider_disabled: dict = {}
        # Backwards-compat: keep named attributes for older code
        self._anthropic: Optional[anthropic.Anthropic] = None
        self._openai: Optional[OpenAI] = None
        # Legacy flag (kept for compatibility)
        self._anthropic_disabled: bool = False
        # Record which provider produced the last successful response
        self._last_llm_provider_used: Optional[str] = None
        # Load persisted provider disable statuses from vector store (if any)
        try:
            statuses = vector_store.list_provider_statuses() or []
            for s in statuses:
                p = (s.get("provider") or "").lower()
                if p:
                    self._provider_disabled[p] = bool(s.get("disabled", False))
        except Exception as e:
            logger.warning(f"Could not load persisted provider statuses: {e}")

    def _get_provider_client(self, name: str):
        """Return client instance for provider or None if unavailable/disabled."""
        name = (name or "").lower()
        if self._provider_disabled.get(name):
            return None
        if name in self._provider_clients and self._provider_clients[name]:
            return self._provider_clients[name]
        try:
            if name == "anthropic":
                if not cfg.ANTHROPIC_API_KEY:
                    return None
                client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
                self._provider_clients["anthropic"] = client
                self._anthropic = client
                return client
            if name == "openai":
                if not cfg.OPENAI_API_KEY:
                    return None
                client = OpenAI(api_key=cfg.OPENAI_API_KEY)
                self._provider_clients["openai"] = client
                self._openai = client
                return client
            # Unknown provider
            return None
        except Exception as e:
            logger.warning(f"Failed to initialize LLM provider {name}: {e}")
            self._provider_disabled[name] = True
            return None

    def _get_anthropic(self) -> Optional[anthropic.Anthropic]:
        return self._get_provider_client("anthropic")

    def _get_openai(self) -> OpenAI:
        return self._get_provider_client("openai")

    def _should_disable_for_error(self, provider: str, err_text: str) -> bool:
        t = (err_text or "").lower()
        if not t:
            return False
        if provider == "anthropic":
            return any(k in t for k in ("credit", "billing", "insufficient", "not enough credits"))
        if provider == "openai":
            return any(k in t for k in ("insufficient_quota", "exceeded your current quota", "insufficient", "billing", "quota", "rate limit"))
        return False

    def _call_llm(self, prompt: str, max_tokens: int = 4000, use_case: str = "analysis") -> str:
        """Call LLM providers in configured priority order per use_case, trying each fallback in turn."""
        # Determine provider order (per-use-case override or global order)
        provider_order = []
        try:
            usecase_map = getattr(cfg, "LLM_USECASE_PRIORITIES", {}) or {}
            if isinstance(usecase_map, dict) and use_case in usecase_map and usecase_map.get(use_case):
                val = usecase_map.get(use_case)
                if isinstance(val, str):
                    provider_order = [p.strip().lower() for p in val.split(",") if p.strip()]
                elif isinstance(val, (list, tuple)):
                    provider_order = [p.strip().lower() for p in val if p]
        except Exception:
            provider_order = []

        if not provider_order:
            provider_order = [p.strip().lower() for p in getattr(cfg, "LLM_PROVIDER_ORDER", ["anthropic", "openai"])]

        last_exc = None
        for provider in provider_order:
            provider = provider.lower()
            client = self._get_provider_client(provider)
            if not client:
                logger.info(f"LLM provider {provider} not available/disabled — skipping")
                continue
            try:
                logger.info(f"Calling {provider} for use_case={use_case}...")
                if provider == "anthropic":
                    msg = client.messages.create(
                        model=cfg.ANTHROPIC_MODEL,
                        max_tokens=max_tokens,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = msg.content[0].text
                    # Telemetry: record successful provider response
                    self._last_llm_provider_used = "anthropic"
                    try:
                        vector_store.record_llm_event(
                            provider="anthropic",
                            event="success",
                            message=(text[:1000] if isinstance(text, str) else str(text)),
                            details={"use_case": use_case, "model": cfg.ANTHROPIC_MODEL},
                        )
                    except Exception as e:
                        logger.warning(f"Failed to record llm metric (anthropic success): {e}")
                    return text

                elif provider == "openai":
                    resp = client.chat.completions.create(
                        model=cfg.OPENAI_MODEL,
                        max_tokens=max_tokens,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                    )
                    try:
                        content = resp.choices[0].message.content
                    except Exception:
                        content = getattr(resp, "choices", [])[0].message.content if hasattr(resp, "choices") else str(resp)
                    self._last_llm_provider_used = "openai"
                    # Record usage/telemetry if available
                    usage = None
                    try:
                        usage = getattr(resp, "usage", None) or (resp.get("usage") if isinstance(resp, dict) else None)
                    except Exception:
                        usage = None
                    try:
                        vector_store.record_llm_event(
                            provider="openai",
                            event="success",
                            message=(str(content)[:1000]),
                            details={"use_case": use_case, "model": cfg.OPENAI_MODEL, "usage": usage},
                        )
                    except Exception as e:
                        logger.warning(f"Failed to record llm metric (openai success): {e}")
                    return content

                else:
                    logger.warning(f"Unknown LLM provider configured: {provider}")
                    continue

            except Exception as e:
                err_text = str(e)
                logger.warning(f"{provider} call failed: {err_text}")
                last_exc = e
                # Record failure telemetry
                try:
                    vector_store.record_llm_event(
                        provider=provider,
                        event="failure",
                        message=err_text,
                        details={"use_case": use_case},
                    )
                except Exception as ee:
                    logger.warning(f"Failed to record llm failure metric for {provider}: {ee}")
                try:
                    if self._should_disable_for_error(provider, err_text):
                        self._provider_disabled[provider] = True
                        # Persist disable with TTL
                        try:
                            disabled_until = datetime.utcnow() + timedelta(seconds=getattr(cfg, "PROVIDER_DISABLE_TTL_SECONDS", 21600))
                            vector_store.set_provider_status(provider, True, reason=err_text, disabled_until=disabled_until)
                        except Exception as _e:
                            logger.warning(f"Failed to persist provider disabled state for {provider}: {_e}")
                        logger.error(f"{provider} appears unavailable — disabling for future calls: {err_text}")
                        try:
                            vector_store.record_llm_event(
                                provider=provider,
                                event="disabled",
                                message=err_text,
                                details={"use_case": use_case, "disabled_until": getattr(cfg, "PROVIDER_DISABLE_TTL_SECONDS", 21600)},
                            )
                        except Exception:
                            logger.warning("Failed to record provider disabled metric")
                except Exception:
                    pass
                continue

        # All providers failed
        logger.error("All configured LLM providers failed")
        # Global fallback: use CLI-based LLM tools configured in llm_gateway (codex/claude/copilot/gemini).
        try:
            cli_response = llm_gateway.execute(
                prompt=prompt,
                provider="anthropic",
                system_prompt=SYSTEM_PROMPT,
                retries=1,
                temperature=0.2,
            )
            if cli_response:
                self._last_llm_provider_used = "cli_fallback"
                try:
                    vector_store.record_llm_event(
                        provider="cli_fallback",
                        event="success",
                        message=(str(cli_response)[:1000]),
                        details={"use_case": use_case},
                    )
                except Exception as e:
                    logger.warning(f"Failed to record llm metric (cli fallback success): {e}")
                return cli_response
        except Exception as e:
            logger.warning(f"CLI fallback via llm_gateway failed: {e}")
        # Final safety net: return structured fallback JSON so autonomous pipeline
        # can complete with explicit warning instead of hard-failing.
        fallback_payload = {
            "executive_summary": (
                "Automated analysis degraded: all LLM providers and CLI fallbacks were unavailable "
                "for this run. Data collection completed; action generation is deferred."
            ),
            "key_findings": [],
            "action_plan": [],
            "quick_wins": [],
            "monitoring_alerts": [
                "LLM unavailable during analysis run; retry after provider/CLI recovery."
            ],
            "ga4_insights": {},
            "degraded_mode": True,
        }
        try:
            vector_store.record_llm_event(
                provider="analyzer",
                event="degraded_fallback",
                message=(str(last_exc)[:1000] if last_exc else "No LLM provider available"),
                details={"use_case": use_case},
            )
        except Exception:
            pass
        return json.dumps(fallback_payload)

    def format_keyword_table(self, keywords: list[dict], key_field: str = "keyword") -> str:
        if not keywords:
            return "No data available"
        lines = []
        for kw in keywords[:15]:  # Limit for token efficiency
            field = kw.get(key_field, kw.get("page", ""))
            lines.append(
                f"  - {field}: {kw.get('clicks', 0)} clicks, "
                f"{kw.get('impressions', 0)} impressions, "
                f"CTR {kw.get('ctr', 0)}%, "
                f"Position {kw.get('position', 0)}"
            )
        return "\n".join(lines)

    def _format_ga_overview(self, ga_summary: dict) -> str:
        ov = ga_summary.get("overview", {}) if ga_summary else {}
        if not ov:
            return "GA4 data not available"
        return (
            f"  Sessions: {ov.get('total_sessions',0):,} | "
            f"Users: {ov.get('total_users',0):,} | "
            f"Pageviews: {ov.get('total_pageviews',0):,}\n"
            f"  New Users: {ov.get('new_users',0):,} | "
            f"Organic Sessions: {ov.get('organic_sessions',0):,} ({ov.get('organic_pct',0)}%)\n"
            f"  Avg Bounce Rate: {ov.get('avg_bounce_rate_pct',0)}% | "
            f"Avg Session Duration: {ov.get('avg_session_duration_sec',0)}s"
        )

    def _format_ga_pages(self, pages: list) -> str:
        if not pages:
            return "No page data available"
        lines = []
        for p in pages[:12]:
            lines.append(
                f"  - {p.get('pagePath','')[:60]} | "
                f"views: {int(p.get('screenPageViews',0)):,} | "
                f"bounce: {round(float(p.get('bounceRate',0))*100,1)}% | "
                f"duration: {round(float(p.get('averageSessionDuration',0)),0)}s | "
                f"engagement: {round(float(p.get('engagementRate',0))*100,1)}%"
            )
        return "\n".join(lines)

    def _format_ga_channels(self, channels: list) -> str:
        if not channels:
            return "No channel data available"
        lines = []
        for c in channels[:8]:
            lines.append(
                f"  - {c.get('sessionDefaultChannelGroup','?')} / "
                f"{c.get('sessionSource','?')}: "
                f"{int(c.get('sessions',0)):,} sessions | "
                f"bounce: {round(float(c.get('bounceRate',0))*100,1)}%"
            )
        return "\n".join(lines)

    def _format_ga_geo(self, geo: list) -> str:
        if not geo:
            return "No geo data available"
        lines = []
        for g in geo[:10]:
            lines.append(
                f"  - {g.get('country','?')}: "
                f"{int(g.get('sessions',0)):,} sessions | "
                f"users: {int(g.get('activeUsers',0)):,}"
            )
        return "\n".join(lines)

    def _format_ga_devices(self, devices: list) -> str:
        if not devices:
            return "No device data available"
        lines = []
        for d in devices:
            lines.append(
                f"  - {d.get('deviceCategory','?')}: "
                f"{int(d.get('sessions',0)):,} sessions | "
                f"bounce: {round(float(d.get('bounceRate',0))*100,1)}%"
            )
        return "\n".join(lines)

    def _format_ga_ecommerce(self, ec: dict) -> str:
        if not ec or not ec.get("total_transactions"):
            return "E-commerce data not available (may need GA4 e-commerce events configured)"
        return (
            f"  Transactions: {ec.get('total_transactions',0):,} | "
            f"Revenue: ${ec.get('total_revenue_usd',0):,.2f} | "
            f"AOV: ${ec.get('avg_order_value_usd',0):.2f}\n"
            f"  Add to Cart: {ec.get('total_add_to_cart',0):,} | "
            f"Checkouts: {ec.get('total_checkouts',0):,} | "
            f"Checkout→Purchase: {ec.get('checkout_conversion_pct',0)}%"
        )

    def _format_ga_retention(self, retention: dict) -> str:
        if not retention:
            return "No retention data available"
        lines = []
        for key, val in retention.items():
            lines.append(
                f"  - {key}: {val.get('sessions',0):,} sessions | "
                f"engagement: {val.get('engagement_rate_pct',0)}% | "
                f"avg duration: {val.get('avg_session_duration_sec',0)}s"
            )
        return "\n".join(lines)

    def analyze(
        self,
        snapshot: dict,
        summary: dict,
        snapshot_id: str,
        ga_snapshot: dict = None,
        ga_summary: dict = None,
        conversion_audit: dict = None,
    ) -> dict:
        """
        Run full combined GSC + GA4 analysis.
        Returns structured report dict with action plan.
        ga_snapshot/ga_summary are optional — falls back to GSC-only if missing.
        """
        logger.info(f"Starting combined GSC+GA4 analysis for snapshot {snapshot_id}")

        # ── GSC comparison ─────────────────────────────────────────────────
        prev_summary = vector_store.get_previous_snapshot_summary(days_ago=7)
        comparison_text = "No previous data available for comparison"
        if prev_summary:
            prev_qs = prev_summary.get("query_summary", {})
            curr_qs = summary.get("query_summary", {})
            click_delta = curr_qs.get("total_clicks", 0) - prev_qs.get("total_clicks", 0)
            pos_delta = curr_qs.get("avg_position", 0) - prev_qs.get("avg_position", 0)
            comparison_text = (
                f"vs 7 days ago: "
                f"Clicks: {click_delta:+,} | "
                f"Avg Position: {pos_delta:+.1f} | "
                f"Avg CTR: {curr_qs.get('avg_ctr', 0) - prev_qs.get('avg_ctr', 0):+.2f}%"
            )

        # ── GA4 data ───────────────────────────────────────────────────────
        # Fall back to vector store if not passed in
        if ga_summary is None:
            ga_summary = vector_store.get_latest_ga_summary() or {}

        ga_ov      = ga_summary.get("overview", {}) if ga_summary else {}
        ga_pages   = ga_summary.get("top_pages_by_views", []) if ga_summary else []
        ga_bounce  = ga_summary.get("high_bounce_pages", []) if ga_summary else []
        ga_channels= ga_summary.get("top_channels", []) if ga_summary else []
        ga_geo     = ga_summary.get("top_geos", []) if ga_summary else []
        ga_devices = ga_summary.get("devices", []) if ga_summary else []
        ga_ec      = ga_summary.get("ecommerce", {}) if ga_summary else {}
        ga_ret     = ga_summary.get("user_retention", {}) if ga_summary else {}

        # ── Build prompt ───────────────────────────────────────────────────
        qs = summary.get("query_summary", {})
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            days=cfg.GSC_DAYS_HISTORY,
            total_clicks=qs.get("total_clicks", 0),
            total_impressions=qs.get("total_impressions", 0),
            avg_ctr=qs.get("avg_ctr", 0),
            avg_position=qs.get("avg_position", 0),
            top_keywords=self.format_keyword_table(summary.get("top_keywords", [])),
            low_ctr_keywords=self.format_keyword_table(summary.get("low_ctr_keywords", [])),
            top_pages=self.format_keyword_table(
                summary.get("top_pages", []), key_field="page"
            ),
            underperforming_pages=self.format_keyword_table(
                summary.get("underperforming_pages", []), key_field="page"
            ),
            comparison=comparison_text,
            ga_overview=self._format_ga_overview(ga_summary),
            ga_top_pages=self._format_ga_pages(ga_pages),
            ga_high_bounce=self._format_ga_pages(ga_bounce),
            ga_channels=self._format_ga_channels(ga_channels),
            ga_geo=self._format_ga_geo(ga_geo),
            ga_devices=self._format_ga_devices(ga_devices),
            ga_ecommerce=self._format_ga_ecommerce(ga_ec),
            ga_retention=self._format_ga_retention(ga_ret),
        )

        # ── Call LLM ───────────────────────────────────────────────────────
        raw_response = self._call_llm(prompt, use_case='analysis')

        # ── Parse JSON ─────────────────────────────────────────────────────
        import re
        try:
            analysis = json.loads(raw_response)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw_response, re.DOTALL)
            if match:
                try:
                    analysis = json.loads(match.group())
                except Exception:
                    analysis = {}
            else:
                analysis = {}

        if not analysis.get("action_plan"):
            logger.error(f"Could not parse LLM response as JSON: {raw_response[:500]}")
            analysis = {
                "executive_summary": "Analysis failed — could not parse LLM response",
                "key_findings": [],
                "action_plan": [],
                "quick_wins": [],
                "monitoring_alerts": ["LLM response parsing failed"],
                "ga4_insights": {},
            }

        # ── Enrich action_plan with conversion revenue + sort ──────────────
        action_plan = analysis.get("action_plan", [])
        if conversion_audit and action_plan:
            # Build keyword → revenue lookup from the conversion audit
            kw_revenue: dict[str, float] = {}
            for row in conversion_audit.get("searchterm_conversions", []):
                kw = (row.get("query") or "").strip().lower()
                if kw:
                    kw_revenue[kw] = float(row.get("conversion_revenue", 0))

            for action in action_plan:
                kw = (action.get("target_keyword") or "").strip().lower()
                action["conversion_revenue"] = kw_revenue.get(kw, 0.0)

            # Sort: priority tier first, then highest conversion revenue within each tier
            priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            action_plan.sort(key=lambda a: (
                priority_order.get(a.get("priority", "low"), 4),
                -float(a.get("conversion_revenue", 0)),
            ))
            analysis["action_plan"] = action_plan
            logger.info(
                f"Action plan sorted by conversion revenue — "
                f"top revenue keyword: £{action_plan[0].get('conversion_revenue', 0):.2f} "
                f"({action_plan[0].get('target_keyword', '')!r})"
                if action_plan else "Action plan empty after sorting"
            )

        # ── Attach metadata ────────────────────────────────────────────────
        analysis["snapshot_id"]  = snapshot_id
        analysis["fetch_date"]   = snapshot.get("fetched_at", "")
        analysis["analyzed_at"]  = datetime.utcnow().isoformat()
        analysis["action_count"] = len(analysis.get("action_plan", []))
        analysis["summary"]      = analysis.get("executive_summary", "")
        analysis["gsc_metrics"]  = {
            "total_clicks":      qs.get("total_clicks", 0),
            "total_impressions": qs.get("total_impressions", 0),
            "avg_ctr":           qs.get("avg_ctr", 0),
            "avg_position":      qs.get("avg_position", 0),
        }
        analysis["ga4_metrics"] = {
            "total_sessions":           ga_ov.get("total_sessions", 0),
            "total_users":              ga_ov.get("total_users", 0),
            "total_pageviews":          ga_ov.get("total_pageviews", 0),
            "organic_sessions":         ga_ov.get("organic_sessions", 0),
            "avg_bounce_rate_pct":      ga_ov.get("avg_bounce_rate_pct", 0),
            "avg_session_duration_sec": ga_ov.get("avg_session_duration_sec", 0),
            "transactions":             ga_ec.get("total_transactions", 0),
            "revenue_usd":              ga_ec.get("total_revenue_usd", 0),
        }

        analysis["llm_provider"] = getattr(self, "_last_llm_provider_used", None) or "unknown"
        logger.info(
            f"Analysis complete: {analysis['action_count']} action items generated "
            f"(GSC + GA4 combined)"
        )
        return analysis

    def generate_meta_description(
        self, page_url: str, keyword: str, current_meta: str = ""
    ) -> str:
        """Generate an optimized meta description for a specific page."""
        prompt = f"""Generate an optimized meta description for this medical e-commerce page.

URL: {page_url}
Primary Keyword: {keyword}
Current Meta: {current_meta or 'None'}

Requirements:
- Length: 150-160 characters
- Include the primary keyword naturally
- Include a clear call-to-action
- Medical/pharmaceutical tone
- Trustworthy and professional
- Do NOT make false medical claims

Return ONLY the meta description text, no quotes, no explanation."""

        try:
            result = self._call_llm(prompt, max_tokens=200, use_case='meta')
            return result.strip().strip('"').strip("'")[:160]
        except Exception as e:
            logger.error(f"Meta description generation failed: {e}")
            return ""

    def generate_page_title(
        self, page_url: str, keyword: str, current_title: str = ""
    ) -> str:
        """Generate an optimized SEO title for a page."""
        prompt = f"""Generate an optimized SEO page title for this medical e-commerce page.

URL: {page_url}
Primary Keyword: {keyword}
Current Title: {current_title or 'None'}

Requirements:
- Length: 50-60 characters
- Include the primary keyword at or near the start
- Include brand name "Indogenmed" or "IndogenMed" at the end (separated by |)
- Medical/pharmaceutical context
- Compelling and click-worthy

Return ONLY the title text, no quotes, no explanation."""

        try:
            result = self._call_llm(prompt, max_tokens=100, use_case='title')
            return result.strip().strip('"').strip("'")[:60]
        except Exception as e:
            logger.error(f"Page title generation failed: {e}")
            return ""


analyzer = Analyzer()
