"""
CI SEO Agent — Extended Analysis
Analyzes the full extended GSC report (indexing, schema, CWV, links, sitemaps)
and generates additional targeted action items.
"""
import json
import logging
from datetime import datetime
from typing import Optional

from agents.seo_agent.seo_config import cfg
from agents.seo_agent.vector_store import vector_store

logger = logging.getLogger("ci.extended_analyzer")


EXTENDED_ANALYSIS_PROMPT = """You are an expert technical SEO analyst for IndogenMed.org, a medical e-commerce site.

Analyze this comprehensive technical SEO data and generate a structured action plan.
Focus on CRITICAL issues first (indexing blocks, schema errors, canonical problems).

## Extended GSC Technical Report

### Sitemaps Status
{sitemaps}

### Index Coverage
{index_coverage}

### Links Analysis
{links}

### Core Web Vitals (Mobile vs Desktop)
{cwv}

### Rich Results / Schema
{rich_results}

### PageSpeed Scores
{psi_scores}

## Instructions

Return ONLY valid JSON with this structure:
{{
  "technical_summary": "2-3 sentence overview of technical SEO health",
  "critical_issues": [
    {{
      "issue": "Description of the issue",
      "impact": "Why this matters for SEO",
      "affected_urls": ["url1", "url2"],
      "fix": "Specific fix recommendation"
    }}
  ],
  "schema_issues": [
    {{
      "url": "page url",
      "schema_type": "Product|FAQ|BreadcrumbList|etc",
      "issue": "Missing required field or error",
      "fix": "Exact fix needed"
    }}
  ],
  "indexing_actions": [
    {{
      "action_type": "UPDATE_META_DESCRIPTION|FIX_CANONICAL|FLAG_FOR_REVIEW",
      "priority": "critical|high|medium|low",
      "title": "Action title",
      "description": "What to do",
      "target_url": "https://indogenmed.org/...",
      "target_keyword": "",
      "expected_impact": "Expected result",
      "implementation_data": {{
        "current_value": "",
        "new_value": "",
        "notes": ""
      }}
    }}
  ],
  "cwv_recommendations": ["list of CWV improvement recommendations"],
  "sitemap_actions": ["list of sitemap fixes needed"],
  "link_building_opportunities": ["list of internal link opportunities from orphan page analysis"]
}}"""


class ExtendedAnalyzer:
    def __init__(self):
        self._llm_client = None

    def _call_llm(self, prompt: str) -> str:
        """Call GPT-4o or Claude for analysis."""
        try:
            from openai import OpenAI
            oai = OpenAI(api_key=cfg.OPENAI_API_KEY)
            resp = oai.chat.completions.create(
                model=cfg.OPENAI_MODEL,
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

    def _fmt(self, data, max_items: int = 10) -> str:
        """Format a dict/list for prompt inclusion."""
        if not data:
            return "No data available"
        if isinstance(data, list):
            data = data[:max_items]
        return json.dumps(data, indent=2)[:3000]

    def analyze_extended_report(self, report: dict, snapshot_id: str) -> dict:
        """
        Analyze the extended GSC report and generate technical action items.
        Returns structured analysis with action plan.
        """
        logger.info("Running extended technical SEO analysis...")

        ic = report.get("index_coverage", {})
        links = report.get("links", {})
        cwv = report.get("cwv", {})
        rich = report.get("rich_results", {})
        sitemaps = report.get("sitemaps", [])
        psi = cwv.get("pagespeed_scores", [])

        prompt = EXTENDED_ANALYSIS_PROMPT.format(
            sitemaps=self._fmt([{
                "path": s["path"],
                "errors": s.get("errors", 0),
                "warnings": s.get("warnings", 0),
                "contents": s.get("contents", []),
                "last_submitted": s.get("last_submitted", ""),
            } for s in sitemaps]),
            index_coverage=self._fmt({
                "verdict_summary": ic.get("verdict_summary", {}),
                "not_indexed_pages": ic.get("not_indexed_pages", [])[:10],
                "canonical_mismatches": ic.get("canonical_mismatches", [])[:5],
                "mobile_issues": ic.get("mobile_issues", [])[:5],
                "schema_issues": ic.get("schema_issues", [])[:5],
            }),
            links=self._fmt({
                "orphan_candidates": links.get("orphan_candidates", [])[:10],
                "buried_pages": links.get("buried_pages", [])[:10],
                "total_pages": links.get("total_pages_in_gsc", 0),
            }),
            cwv=self._fmt({
                "mobile_degraded": cwv.get("mobile_degraded_pages", [])[:10],
            }),
            rich_results=self._fmt(rich),
            psi_scores=self._fmt(psi),
        )

        raw = self._call_llm(prompt)
        try:
            analysis = json.loads(raw)
        except Exception:
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            analysis = json.loads(m.group()) if m else {}

        analysis["snapshot_id"] = snapshot_id
        analysis["analyzed_at"] = datetime.utcnow().isoformat()
        analysis["source"] = "extended_gsc_report"

        # Store indexing action items in ChromaDB
        actions_added = 0
        for action in analysis.get("indexing_actions", []):
            vector_store.create_action_item(
                action_type=action.get("action_type", "FLAG_FOR_REVIEW"),
                priority=action.get("priority", "medium"),
                title=action.get("title", ""),
                description=action.get("description", ""),
                target_url=action.get("target_url", ""),
                target_keyword=action.get("target_keyword", ""),
                implementation_data=action.get("implementation_data", {}),
                snapshot_id=snapshot_id,
            )
            actions_added += 1

        # Flag schema issues as action items
        for issue in analysis.get("schema_issues", [])[:5]:
            vector_store.create_action_item(
                action_type="UPDATE_SCHEMA",
                priority="high",
                title=f"Fix {issue.get('schema_type', 'Schema')} issue",
                description=issue.get("issue", ""),
                target_url=issue.get("url", ""),
                implementation_data={"notes": issue.get("fix", ""), "new_value": ""},
                snapshot_id=snapshot_id,
            )
            actions_added += 1

        logger.info(f"Extended analysis complete: {actions_added} additional action items")
        return analysis

    def generate_extended_email_section(self, analysis: dict, extended_report: dict) -> str:
        """Generate HTML section for extended report to include in approval email."""
        summary = extended_report.get("summary", {})
        critical = analysis.get("critical_issues", [])
        cwv_recs = analysis.get("cwv_recommendations", [])

        psi_rows = ""
        for p in extended_report.get("cwv", {}).get("pagespeed_scores", [])[:6]:
            score_color = "#27ae60" if p.get("performance_score", 0) >= 70 else "#e74c3c"
            psi_rows += f"""
<tr>
  <td style="font-size:11px">{p.get('strategy','').title()}</td>
  <td style="font-size:11px;max-width:200px;overflow:hidden">{p.get('url','')[-50:]}</td>
  <td style="text-align:center"><span style="background:{score_color};color:white;padding:2px 8px;border-radius:10px;font-size:12px">{p.get('performance_score',0)}</span></td>
  <td style="text-align:center">{p.get('seo_score',0)}</td>
  <td style="font-size:11px">{p.get('lcp','')}</td>
  <td style="font-size:11px">{p.get('cls','')}</td>
</tr>"""

        critical_html = "".join(
            f"<li><strong>{c.get('issue','')}</strong><br>"
            f"<span style='color:#7f8c8d;font-size:12px'>{c.get('fix','')}</span></li>"
            for c in critical[:5]
        )
        cwv_html = "".join(f"<li>{r}</li>" for r in cwv_recs[:5])

        return f"""
<h2>🔧 Technical SEO Health</h2>
<p>{analysis.get('technical_summary','')}</p>

<div style="display:flex;gap:15px;flex-wrap:wrap;margin:15px 0">
  <div style="background:#{'e74c3c' if summary.get('not_indexed_count',0)>0 else '27ae60'};color:white;padding:12px 20px;border-radius:8px;text-align:center;min-width:100px">
    <div style="font-size:22px;font-weight:bold">{summary.get('not_indexed_count',0)}</div>
    <div style="font-size:11px">Not Indexed</div>
  </div>
  <div style="background:#{'e74c3c' if summary.get('schema_issues_count',0)>0 else '27ae60'};color:white;padding:12px 20px;border-radius:8px;text-align:center;min-width:100px">
    <div style="font-size:22px;font-weight:bold">{summary.get('schema_issues_count',0)}</div>
    <div style="font-size:11px">Schema Issues</div>
  </div>
  <div style="background:#{'e67e22' if summary.get('canonical_mismatches',0)>0 else '27ae60'};color:white;padding:12px 20px;border-radius:8px;text-align:center;min-width:100px">
    <div style="font-size:22px;font-weight:bold">{summary.get('canonical_mismatches',0)}</div>
    <div style="font-size:11px">Canonical Issues</div>
  </div>
  <div style="background:#3498db;color:white;padding:12px 20px;border-radius:8px;text-align:center;min-width:100px">
    <div style="font-size:22px;font-weight:bold">{summary.get('orphan_candidates',0)}</div>
    <div style="font-size:11px">Orphan Pages</div>
  </div>
  <div style="background:#{'e74c3c' if summary.get('mobile_degraded_pages',0)>3 else 'f39c12'};color:white;padding:12px 20px;border-radius:8px;text-align:center;min-width:100px">
    <div style="font-size:22px;font-weight:bold">{summary.get('mobile_degraded_pages',0)}</div>
    <div style="font-size:11px">Mobile Degraded</div>
  </div>
</div>

{'<h3>🚨 Critical Issues</h3><ul>' + critical_html + '</ul>' if critical_html else ''}

<h3>⚡ PageSpeed Scores</h3>
<table style="width:100%;border-collapse:collapse">
  <tr style="background:#2c3e50;color:white">
    <th style="padding:8px">Device</th><th style="padding:8px">URL</th>
    <th style="padding:8px">Perf</th><th style="padding:8px">SEO</th>
    <th style="padding:8px">LCP</th><th style="padding:8px">CLS</th>
  </tr>
  {psi_rows or '<tr><td colspan=6 style="text-align:center;padding:10px">No PSI data</td></tr>'}
</table>

{'<h3>🚀 CWV Recommendations</h3><ul>' + cwv_html + '</ul>' if cwv_html else ''}
"""


extended_analyzer = ExtendedAnalyzer()
