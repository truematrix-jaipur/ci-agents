"""
CI SEO Agent — Email Notifier
Sends action plan approval requests and result summaries to surya@truematrix.io
Uses WordPress SMTP (already configured on the site) via WP-CLI wp eval.
Falls back to Python smtplib with system SMTP.
"""
import json
import logging
import subprocess
import smtplib
import os
import hmac
import hashlib
import base64
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import cfg

logger = logging.getLogger("ci.notifier")

APPROVAL_EMAIL = "surya@truematrix.io"
FROM_EMAIL = "seo-agent@indogenmed.org"
API_BASE = f"http://localhost:{cfg.API_PORT}"


def _sign_report_action(report_id: str, action: str, expires_ts: int) -> str:
    payload = f"{report_id}:{action}:{expires_ts}"
    mac = hmac.new(cfg.API_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("utf-8").rstrip("=")


def _generate_approval_token(report_id: str, action: str, ttl_seconds: int = 86400) -> str:
    expires_ts = int(time.time()) + ttl_seconds
    return f"{expires_ts}.{_sign_report_action(report_id, action, expires_ts)}"


class Notifier:

    def send_approval_request(self, report: dict, report_id: str, extended_html: str = "", conversion_html: str = "") -> bool:
        """
        Email the full action plan to surya@truematrix.io and request approval.
        Includes an approve link pointing to the FastAPI endpoint.
        """
        action_plan = report.get("action_plan", [])
        summary = report.get("executive_summary", "")
        metrics = report.get("gsc_metrics", {})
        fetch_date = report.get("fetch_date", "")

        approve_url = f"{API_BASE}/approve/{report_id}?token={_generate_approval_token(report_id, 'approve')}"
        reject_url = f"{API_BASE}/reject/{report_id}?token={_generate_approval_token(report_id, 'reject')}"

        subject = (
            f"[CI SEO Agent] Action Plan Approval Required — "
            f"{len(action_plan)} actions for {fetch_date}"
        )

        # Build HTML email body
        actions_html = self._build_actions_table(action_plan)
        findings_html = self._build_findings_list(report.get("key_findings", []))
        quick_wins_html = "".join(
            f"<li>{w}</li>" for w in report.get("quick_wins", [])
        )
        alerts_html = "".join(
            f"<li style='color:#c0392b'>{a}</li>"
            for a in report.get("monitoring_alerts", [])
        )

        html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; color: #333; max-width: 900px; margin: 0 auto; padding: 20px; }}
  h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
  h2 {{ color: #2980b9; margin-top: 30px; }}
  .metrics {{ display: flex; gap: 20px; margin: 20px 0; flex-wrap: wrap; }}
  .metric {{ background: #ecf0f1; padding: 15px 20px; border-radius: 8px; text-align: center; min-width: 120px; }}
  .metric-value {{ font-size: 24px; font-weight: bold; color: #2c3e50; }}
  .metric-label {{ font-size: 12px; color: #7f8c8d; margin-top: 5px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
  th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; font-size: 13px; }}
  td {{ padding: 10px; border-bottom: 1px solid #ecf0f1; font-size: 13px; vertical-align: top; }}
  tr:hover {{ background: #f8f9fa; }}
  .priority-critical {{ background: #e74c3c; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
  .priority-high {{ background: #e67e22; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
  .priority-medium {{ background: #f1c40f; color: #333; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
  .priority-low {{ background: #95a5a6; color: white; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
  .approve-btn {{ display: inline-block; background: #27ae60; color: white; padding: 15px 40px; border-radius: 8px; text-decoration: none; font-size: 18px; font-weight: bold; margin: 10px; }}
  .reject-btn {{ display: inline-block; background: #e74c3c; color: white; padding: 15px 40px; border-radius: 8px; text-decoration: none; font-size: 18px; font-weight: bold; margin: 10px; }}
  .action-box {{ text-align: center; margin: 30px 0; padding: 20px; background: #f8f9fa; border-radius: 8px; border: 2px dashed #3498db; }}
  .summary-box {{ background: #eaf4fb; border-left: 4px solid #3498db; padding: 15px; margin: 15px 0; border-radius: 4px; }}
  .warning {{ background: #fef9e7; border-left: 4px solid #f39c12; padding: 10px; margin: 10px 0; font-size: 13px; }}
  code {{ background: #ecf0f1; padding: 2px 6px; border-radius: 3px; font-family: monospace; font-size: 12px; }}
</style>
</head>
<body>
<h1>🔍 CI SEO Agent — Daily Action Plan</h1>
<p><strong>Report Date:</strong> {fetch_date} &nbsp;|&nbsp;
   <strong>Generated:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} &nbsp;|&nbsp;
   <strong>Report ID:</strong> <code>{report_id}</code></p>

<div class="summary-box">
  <strong>Executive Summary:</strong><br>{summary}
</div>

<h2>📊 GSC Performance Metrics (Last {cfg.GSC_DAYS_HISTORY} Days)</h2>
<div class="metrics">
  <div class="metric">
    <div class="metric-value">{metrics.get('total_clicks', 0):,}</div>
    <div class="metric-label">Total Clicks</div>
  </div>
  <div class="metric">
    <div class="metric-value">{metrics.get('total_impressions', 0):,}</div>
    <div class="metric-label">Impressions</div>
  </div>
  <div class="metric">
    <div class="metric-value">{metrics.get('avg_ctr', 0):.2f}%</div>
    <div class="metric-label">Avg CTR</div>
  </div>
  <div class="metric">
    <div class="metric-value">#{metrics.get('avg_position', 0):.1f}</div>
    <div class="metric-label">Avg Position</div>
  </div>
</div>

<h2>🔑 Key Findings</h2>
{findings_html}

<h2>⚡ Quick Wins</h2>
<ul>{quick_wins_html}</ul>

{'<h2>⚠️ Monitoring Alerts</h2><ul>' + alerts_html + '</ul>' if alerts_html else ''}

<h2>📋 Action Plan ({len(action_plan)} Actions)</h2>
<div class="warning">
  ⚠️ <strong>Production Safety:</strong> All actions have been validated through guardrails.
  Each action will be executed with a pre-change backup and post-change verification.
  Failed actions are automatically rolled back. Implementation is limited to 10 actions per run.
</div>
{actions_html}

{conversion_html}

{extended_html}

<div class="action-box">
  <h2 style="margin-top:0">✅ Approve Implementation?</h2>
  <p>Click <strong>APPROVE</strong> to implement all actions listed above on the live site.<br>
     Click <strong>REJECT</strong> to cancel this action plan (no changes will be made).</p>
  <a href="{approve_url}" class="approve-btn">✅ APPROVE ALL ACTIONS</a>
  <a href="{reject_url}" class="reject-btn">❌ REJECT / CANCEL</a>
  <div style="margin-top:20px; padding:15px; background:#f0f8ff; border-radius:8px; font-size:13px;">
    <strong>💬 Or simply reply to this email:</strong><br>
    Reply with <strong>"APPROVE"</strong> or <strong>"YES"</strong> to implement all actions.<br>
    Reply with <strong>"REJECT"</strong> or <strong>"NO"</strong> to cancel.<br>
    <em style="color:#7f8c8d; font-size:12px;">
      Note: Email reply detection requires one-time Google Workspace admin setup.<br>
      Until then, use the buttons above or the API endpoint.
    </em>
  </div>
  <p style="font-size:12px; color:#7f8c8d; margin-top:15px;">
    API: <code>POST {API_BASE}/approve/{report_id}</code> with header <code>X-API-Secret: &lt;configured secret&gt;</code>
  </p>
</div>

<hr>
<p style="font-size:11px; color:#999;">
  CI SEO Agent — running on indogenmed.org production server<br>
  This email was sent automatically. Do not reply. Contact surya@truematrix.io for support.
</p>
</body>
</html>"""

        return self._send_email(APPROVAL_EMAIL, subject, html_body)

    def send_implementation_report(
        self, report_id: str, results: list[dict], duration_seconds: float
    ) -> bool:
        """Email the implementation results after execution."""
        success_count = sum(1 for r in results if r.get("success"))
        fail_count = len(results) - success_count

        subject = (
            f"[CI SEO Agent] Implementation Complete — "
            f"{success_count} done, {fail_count} failed | {datetime.utcnow().strftime('%Y-%m-%d')}"
        )

        rows_html = ""
        for r in results:
            status_icon = "✅" if r.get("success") else "❌"
            rows_html += f"""
<tr>
  <td>{status_icon} {'Success' if r.get('success') else 'Failed'}</td>
  <td>{r.get('action_type','')}</td>
  <td style="font-size:11px;">{r.get('target_url','')[:60]}</td>
  <td style="font-size:11px;">{str(r.get('message',''))[:150]}</td>
</tr>"""

        html_body = f"""
<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;color:#333;max-width:900px;margin:0 auto;padding:20px">
<h1>✅ SEO Implementation Report</h1>
<p><strong>Report ID:</strong> {report_id} &nbsp;|&nbsp;
   <strong>Completed:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} &nbsp;|&nbsp;
   <strong>Duration:</strong> {duration_seconds:.1f}s</p>

<div style="display:flex;gap:20px;margin:20px 0">
  <div style="background:#27ae60;color:white;padding:15px 25px;border-radius:8px;text-align:center">
    <div style="font-size:28px;font-weight:bold">{success_count}</div>
    <div style="font-size:12px">Successful</div>
  </div>
  <div style="background:#e74c3c;color:white;padding:15px 25px;border-radius:8px;text-align:center">
    <div style="font-size:28px;font-weight:bold">{fail_count}</div>
    <div style="font-size:12px">Failed</div>
  </div>
  <div style="background:#3498db;color:white;padding:15px 25px;border-radius:8px;text-align:center">
    <div style="font-size:28px;font-weight:bold">{len(results)}</div>
    <div style="font-size:12px">Total Actions</div>
  </div>
</div>

<table style="width:100%;border-collapse:collapse">
  <tr style="background:#2c3e50;color:white">
    <th style="padding:10px">Status</th>
    <th style="padding:10px">Action Type</th>
    <th style="padding:10px">Target URL</th>
    <th style="padding:10px">Result</th>
  </tr>
  {rows_html}
</table>

<p>The LiteSpeed cache has been purged. Changes are live on indogenmed.org.</p>
<hr>
<p style="font-size:11px;color:#999">CI SEO Agent — automated implementation report</p>
</body></html>"""

        return self._send_email(APPROVAL_EMAIL, subject, html_body)

    def send_impact_report(self, impact_results: list[dict]) -> bool:
        """
        Weekly email showing before/after GSC metrics for every completed action
        that was measured this week.
        """
        if not impact_results:
            return True

        improved = [r for r in impact_results if r.get("improved")]
        neutral  = [r for r in impact_results if not r.get("improved") and r.get("current")]
        missing  = [r for r in impact_results if not r.get("current")]

        subject = (
            f"[CI SEO Agent] Weekly Impact Report — "
            f"{len(improved)} improved, {len(neutral)} neutral, {len(missing)} no data | "
            f"{datetime.utcnow().strftime('%Y-%m-%d')}"
        )

        def delta_cell(val, invert=False) -> str:
            """Colour a delta value. invert=True means lower is better (position)."""
            if val == 0:
                return f"<td style='text-align:center;color:#7f8c8d'>→ 0</td>"
            positive = val > 0 if not invert else val < 0
            color = "#27ae60" if positive else "#e74c3c"
            arrow = "↑" if val > 0 else "↓"
            return f"<td style='text-align:center;color:{color};font-weight:bold'>{arrow} {abs(val)}</td>"

        def build_rows(items: list) -> str:
            rows = ""
            for r in items:
                b = r.get("baseline", {})
                c = r.get("current", {})
                d = r.get("delta", {})
                rows += f"""
<tr>
  <td style='font-size:11px'>{r.get('action_type','')}</td>
  <td style='font-size:11px;max-width:180px;overflow:hidden'>{r.get('target_url','')[-50:]}</td>
  <td style='font-size:11px'>{r.get('keyword','')[:30]}</td>
  <td style='text-align:center'>{int(b.get('clicks',0))} → {int(c.get('clicks',0))}</td>
  {delta_cell(d.get('clicks',0))}
  <td style='text-align:center'>{b.get('ctr',0):.1f}% → {c.get('ctr',0):.1f}%</td>
  {delta_cell(d.get('ctr',0))}
  <td style='text-align:center'>#{b.get('position',0):.1f} → #{c.get('position',0):.1f}</td>
  {delta_cell(d.get('position',0), invert=True)}
  <td style='text-align:center;font-size:11px'>{r.get('days_since',0)}d</td>
</tr>"""
            return rows

        header_row = """
<tr style='background:#2c3e50;color:white'>
  <th style='padding:8px'>Action</th><th style='padding:8px'>URL</th>
  <th style='padding:8px'>Keyword</th>
  <th style='padding:8px'>Clicks (before→after)</th><th style='padding:8px'>Δ</th>
  <th style='padding:8px'>CTR</th><th style='padding:8px'>Δ</th>
  <th style='padding:8px'>Position</th><th style='padding:8px'>Δ</th>
  <th style='padding:8px'>Age</th>
</tr>"""

        improved_section = ""
        if improved:
            improved_section = f"""
<h2 style='color:#27ae60'>✅ Improved ({len(improved)} actions)</h2>
<table style='width:100%;border-collapse:collapse'>{header_row}{build_rows(improved)}</table>"""

        neutral_section = ""
        if neutral:
            neutral_section = f"""
<h2 style='color:#f39c12'>→ No Change ({len(neutral)} actions)</h2>
<table style='width:100%;border-collapse:collapse'>{header_row}{build_rows(neutral)}</table>"""

        missing_section = ""
        if missing:
            missing_html = "".join(
                f"<li style='font-size:12px'>{r.get('action_type','')} — "
                f"{r.get('target_url','')[:60]} — not yet in GSC (may need more time)</li>"
                for r in missing
            )
            missing_section = f"<h2 style='color:#7f8c8d'>⏳ No GSC Data Yet ({len(missing)})</h2><ul>{missing_html}</ul>"

        html_body = f"""
<!DOCTYPE html><html>
<head><meta charset='UTF-8'>
<style>
  body {{ font-family:Arial,sans-serif;color:#333;max-width:1000px;margin:0 auto;padding:20px }}
  h1 {{ color:#2c3e50;border-bottom:3px solid #3498db;padding-bottom:10px }}
  table {{ margin:10px 0 25px }}
  td,th {{ padding:8px;border-bottom:1px solid #ecf0f1 }}
</style></head>
<body>
<h1>📈 CI SEO Agent — Weekly Impact Report</h1>
<p><strong>Measured:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} &nbsp;|&nbsp;
   <strong>Actions analysed:</strong> {len(impact_results)} &nbsp;|&nbsp;
   <strong>Improved:</strong> {len(improved)} &nbsp;|&nbsp;
   <strong>Neutral:</strong> {len(neutral)} &nbsp;|&nbsp;
   <strong>No GSC data:</strong> {len(missing)}</p>
<p style='font-size:12px;color:#7f8c8d'>
  Δ position: negative = better ranking (moved up). CTR and clicks: positive = better.
  Baseline = GSC metrics when action was created. Current = last 7 days in GSC.
</p>
{improved_section}
{neutral_section}
{missing_section}
<hr>
<p style='font-size:11px;color:#999'>CI SEO Agent — weekly impact measurement (every Sunday 08:00 UTC)</p>
</body></html>"""

        return self._send_email(APPROVAL_EMAIL, subject, html_body)

    def send_error_alert(self, error_type: str, message: str) -> bool:
        """Send an error alert email."""
        subject = f"[CI SEO Agent] ⚠️ Error: {error_type}"
        html_body = f"""
<html><body style="font-family:Arial,sans-serif;padding:20px">
<h2 style="color:#e74c3c">⚠️ SEO Agent Error</h2>
<p><strong>Type:</strong> {error_type}</p>
<p><strong>Time:</strong> {datetime.utcnow().isoformat()} UTC</p>
<pre style="background:#f8f8f8;padding:15px;border-radius:5px;white-space:pre-wrap">{message}</pre>
</body></html>"""
        return self._send_email(APPROVAL_EMAIL, subject, html_body)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _build_actions_table(self, action_plan: list) -> str:
        if not action_plan:
            return "<p>No actions in this plan.</p>"

        rows = ""
        for i, action in enumerate(action_plan, 1):
            impl = action.get("implementation_data") or {}
            current = impl.get("current_value", "")[:100] if impl else ""
            new_val = impl.get("new_value", "")[:100] if impl else ""
            rows += f"""
<tr>
  <td style="font-weight:bold;color:#7f8c8d">{i}</td>
  <td><span class="priority-{action.get('priority','low')}">{action.get('priority','?').upper()}</span></td>
  <td><strong>{action.get('action_type','')}</strong></td>
  <td>{action.get('title','')}</td>
  <td style="font-size:11px">{action.get('target_url','') or action.get('target_keyword','')}</td>
  <td style="font-size:11px">{action.get('description','')[:150]}</td>
  <td style="font-size:11px;color:#27ae60">{action.get('expected_impact','')}</td>
  {'<td style="font-size:11px"><em>Current:</em> ' + current + '<br><em>New:</em> <strong>' + new_val + '</strong></td>' if current or new_val else '<td></td>'}
</tr>"""

        return f"""
<table>
  <tr>
    <th>#</th><th>Priority</th><th>Type</th><th>Title</th>
    <th>Target</th><th>Description</th><th>Expected Impact</th><th>Change</th>
  </tr>
  {rows}
</table>"""

    def _build_findings_list(self, findings: list) -> str:
        if not findings:
            return "<p>No key findings.</p>"
        icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        items = ""
        for f in findings:
            icon = icons.get(f.get("impact", "low"), "⚪")
            items += f"""
<li style="margin:8px 0">
  {icon} <strong>{f.get('finding','')}</strong>
  <span style="color:#7f8c8d;font-size:12px"> — {f.get('evidence','')}</span>
</li>"""
        return f"<ul>{items}</ul>"

    def build_conversion_audit_html(self, audit: dict) -> str:
        """Generate HTML section for the GA4 conversion audit in the approval email."""
        if not audit:
            return ""

        health_score = audit.get("health_score", 0)
        health_issues = audit.get("health_issues", [])
        completeness = audit.get("event_completeness", {})
        funnel = audit.get("funnel_analysis", {})
        attribution = audit.get("attribution_summary", {})

        # Health score colour
        if health_score >= 80:
            score_color = "#27ae60"
        elif health_score >= 50:
            score_color = "#f39c12"
        else:
            score_color = "#e74c3c"

        issues_html = ""
        if health_issues:
            issues_html = "<ul>" + "".join(f"<li style='color:#c0392b'>{i}</li>" for i in health_issues) + "</ul>"

        # Event completeness table
        missing_events = completeness.get("missing_events", [])
        found_events = completeness.get("found_events", {})
        event_rows = ""
        from ga4_conversion_auditor import EXPECTED_EVENTS
        for ev in EXPECTED_EVENTS:
            if ev in found_events:
                count = found_events[ev].get("count", 0)
                is_conv = found_events[ev].get("is_conversion", False)
                event_rows += (
                    f"<tr><td>&#10003; {ev}</td><td style='color:#27ae60'>{count:,}</td>"
                    f"<td>{'Yes' if is_conv else 'No'}</td></tr>"
                )
            else:
                event_rows += (
                    f"<tr style='background:#fef9e7'><td style='color:#e74c3c'>&#10007; {ev}</td>"
                    f"<td style='color:#e74c3c'>MISSING</td><td>—</td></tr>"
                )

        # Funnel table
        rates = funnel.get("conversion_rates", {})
        funnel_rows = ""
        for step_key, data in rates.items():
            rate = data.get("rate_pct", 0)
            rate_color = "#27ae60" if rate >= 30 else ("#f39c12" if rate >= 10 else "#e74c3c")
            funnel_rows += (
                f"<tr><td>{step_key.replace('_to_', ' → ')}</td>"
                f"<td>{data.get('from', 0):,}</td><td>{data.get('to', 0):,}</td>"
                f"<td style='color:{rate_color};font-weight:bold'>{rate}%</td></tr>"
            )

        # Attribution summary
        attr_html = ""
        if attribution.get("status") == "ok":
            totals = attribution.get("totals") or {}
            paid = attribution.get("paid_stats") or {}
            total_rev = totals.get("total_revenue") or 0
            total_orders = totals.get("total_orders") or 0
            paid_rev = paid.get("paid_revenue") or 0
            paid_conv = paid.get("paid_conversions") or 0
            source_rows = ""
            for row in (attribution.get("revenue_by_source") or [])[:8]:
                rev = float(row.get("revenue") or 0)
                source_rows += (
                    f"<tr><td>{row.get('utm_source','')}</td><td>{row.get('utm_medium','')}</td>"
                    f"<td>{row.get('order_count',0)}</td><td>£{rev:,.2f}</td></tr>"
                )
            top_terms = ""
            for row in (attribution.get("top_converting_terms") or [])[:5]:
                rev = float(row.get("revenue") or 0)
                top_terms += (
                    f"<li><strong>{row.get('utm_term','')}</strong> — "
                    f"{row.get('conversions',0)} orders, £{rev:,.2f}</li>"
                )
            attr_html = f"""
<h3 style="color:#2980b9;margin-top:15px">Attribution Summary ({audit.get('period_days',28)} days)</h3>
<p>Total orders: <strong>{int(total_orders):,}</strong> &nbsp;|&nbsp;
   Total revenue: <strong>£{float(total_rev):,.2f}</strong> &nbsp;|&nbsp;
   Paid conversions: <strong>{int(paid_conv):,}</strong> (£{float(paid_rev):,.2f})</p>
<table>
  <tr style="background:#2c3e50;color:white">
    <th style="padding:8px">Source</th><th style="padding:8px">Medium</th>
    <th style="padding:8px">Orders</th><th style="padding:8px">Revenue</th>
  </tr>
  {source_rows}
</table>
{'<p><strong>Top converting search terms:</strong></p><ul>' + top_terms + '</ul>' if top_terms else ''}"""
        elif attribution.get("status") == "unavailable":
            attr_html = f"<p style='color:#e67e22'>Attribution DB not yet set up: {attribution.get('note','')}</p>"

        return f"""
<h2 style="color:#2980b9;margin-top:30px">&#128200; GA4 Conversion Audit</h2>
<p>Health score: <strong style="color:{score_color};font-size:20px">{health_score}%</strong>
   &nbsp;|&nbsp; Event completeness: <strong>{completeness.get('completeness_pct', 0)}%</strong>
   ({completeness.get('expected_events_found', 0)}/{completeness.get('expected_events_total', 0)} events)</p>
{issues_html}

<h3 style="color:#2980b9">Event Tracking Status</h3>
<table>
  <tr style="background:#2c3e50;color:white">
    <th style="padding:8px">Event</th><th style="padding:8px">Count ({audit.get('period_days',28)}d)</th>
    <th style="padding:8px">Conversion Event</th>
  </tr>
  {event_rows}
</table>

<h3 style="color:#2980b9;margin-top:15px">Conversion Funnel</h3>
{f'''<table>
  <tr style="background:#2c3e50;color:white">
    <th style="padding:8px">Step</th><th style="padding:8px">From</th>
    <th style="padding:8px">To</th><th style="padding:8px">Rate</th>
  </tr>
  {funnel_rows}
</table>''' if funnel_rows else '<p style="color:#e67e22">No funnel data — ecommerce events not firing.</p>'}

{attr_html}
"""

    def _send_email(self, to: str, subject: str, html_body: str) -> bool:
        """Send email via WP-CLI (uses site's SMTP config) or system SMTP fallback."""
        # Primary: WP-CLI wp mail send
        try:
            ok = self._send_via_wpcli(to, subject, html_body)
            if ok:
                logger.info(f"Email sent via WP-CLI to {to}: {subject}")
                return True
        except Exception as e:
            logger.warning(f"WP-CLI email failed: {e}")

        # Fallback: system sendmail
        try:
            ok = self._send_via_sendmail(to, subject, html_body)
            if ok:
                logger.info(f"Email sent via sendmail to {to}: {subject}")
                return True
        except Exception as e:
            logger.warning(f"sendmail fallback failed: {e}")

        logger.error(f"All email methods failed for: {subject}")
        return False

    def _send_via_wpcli(self, to: str, subject: str, html_body: str) -> bool:
        """Use WP-CLI to send email through WordPress SMTP stack."""
        # Escape for PHP
        esc_to = to.replace("'", "\\'")
        esc_subject = subject.replace("'", "\\'")
        # Write HTML body to temp file to avoid shell escaping issues
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html_body)
            tmp_path = f.name

        php_code = (
            f"$html = file_get_contents('{tmp_path}');"
            f"$headers = ['Content-Type: text/html; charset=UTF-8', 'From: CI SEO Agent <{FROM_EMAIL}>'];"
            f"$result = wp_mail('{esc_to}', '{esc_subject}', $html, $headers);"
            f"echo $result ? 'sent' : 'failed';"
            f"unlink('{tmp_path}');"
        )
        cmd = [
            cfg.WP_CLI_PATH, "--allow-root", f"--path={cfg.WP_ROOT}",
            "eval", php_code,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0 and "sent" in result.stdout

    def _send_via_sendmail(self, to: str, subject: str, html_body: str) -> bool:
        """Fallback: use Python smtplib with localhost."""
        msg = MIMEMultipart("alternative")
        msg["From"] = f"CI SEO Agent <{FROM_EMAIL}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP("localhost", 25, timeout=10) as smtp:
            smtp.sendmail(FROM_EMAIL, [to], msg.as_string())
        return True


notifier = Notifier()
