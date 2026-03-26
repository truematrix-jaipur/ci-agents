from __future__ import annotations

import datetime
import json
import os
import shutil
from pathlib import Path
from typing import Any

import mysql.connector
import requests
from requests.auth import HTTPBasicAuth

from config.settings import config
from core.agent_catalog import get_agent_specs


def _ok(detail: str, remediation: str = "") -> dict[str, Any]:
    return {"ok": True, "severity": "info", "detail": detail, "remediation": remediation}


def _warn(detail: str, remediation: str = "") -> dict[str, Any]:
    return {"ok": False, "severity": "warning", "detail": detail, "remediation": remediation}


def _critical(detail: str, remediation: str = "") -> dict[str, Any]:
    return {"ok": False, "severity": "critical", "detail": detail, "remediation": remediation}


def check_gsc_service_account() -> dict[str, Any]:
    sa_path = Path(config.GSC_SERVICE_ACCOUNT_FILE)
    if not sa_path.exists():
        return _critical(
            f"Missing service account file: {sa_path}",
            "Provide valid JSON credentials at GSC_SERVICE_ACCOUNT_FILE and share Search Console + GA4 access.",
        )
    try:
        payload = json.loads(sa_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _critical(
            f"Invalid JSON in service account file: {e}",
            "Replace with a valid Google service account JSON file.",
        )
    client_email = payload.get("client_email")
    private_key = payload.get("private_key")
    if not client_email or not private_key:
        return _critical(
            "Service account JSON is missing required keys (client_email/private_key).",
            "Regenerate the service account key and update the credentials file.",
        )
    return _ok(f"Service account file is present and valid for {client_email}.")


def check_erpnext_mysql() -> dict[str, Any]:
    conn = None
    try:
        conn = mysql.connector.connect(
            host=config.ERPNEXT_MYSQL_HOST,
            port=config.ERPNEXT_MYSQL_PORT,
            user=config.ERPNEXT_MYSQL_USER,
            password=config.ERPNEXT_MYSQL_PASSWORD,
            database=config.ERPNEXT_MYSQL_DATABASE,
            connection_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        return _ok("ERPNext MySQL connection succeeded.")
    except Exception as e:
        return _critical(
            f"ERPNext MySQL connection failed: {e}",
            "Verify ERPNEXT_MYSQL_* env vars and DB user privileges.",
        )
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def check_indogenmed_woocommerce() -> dict[str, Any]:
    wc_url = os.getenv("WC_URL", "").rstrip("/")
    wc_ck = os.getenv("WC_INDOGENMED_CK", "")
    wc_cs = os.getenv("WC_INDOGENMED_CS", "")
    if not wc_url or not wc_ck or not wc_cs:
        return _warn(
            "WooCommerce (IndogenMed) credentials are not fully configured.",
            "Set WC_URL, WC_INDOGENMED_CK, and WC_INDOGENMED_CS.",
        )
    try:
        resp = requests.get(
            f"{wc_url}/wp-json/wc/v3/products",
            auth=HTTPBasicAuth(wc_ck, wc_cs),
            params={"per_page": 1},
            timeout=8,
        )
        if resp.status_code == 401:
            return _critical(
                "WooCommerce API returned 401 Unauthorized.",
                "Rotate WooCommerce API keys and ensure read permissions.",
            )
        resp.raise_for_status()
        return _ok("WooCommerce API authentication succeeded.")
    except Exception as e:
        return _critical(
            f"WooCommerce API check failed: {e}",
            "Verify WC_URL reachability and WooCommerce REST API credentials.",
        )


def run_preflight_diagnostics() -> dict[str, Any]:
    checks = {
        "gsc_service_account": check_gsc_service_account(),
        "erpnext_mysql": check_erpnext_mysql(),
        "woocommerce_indogenmed": check_indogenmed_woocommerce(),
    }
    critical_count = sum(1 for c in checks.values() if c["severity"] == "critical")
    warning_count = sum(1 for c in checks.values() if c["severity"] == "warning")
    ok_count = sum(1 for c in checks.values() if c["ok"])
    status = "ok" if critical_count == 0 and warning_count == 0 else "degraded"
    return {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": status,
        "summary": {
            "ok_count": ok_count,
            "warning_count": warning_count,
            "critical_count": critical_count,
            "total_checks": len(checks),
        },
        "checks": checks,
        "agent_runtime_readiness": check_agent_runtime_requirements(),
    }


def check_agent_runtime_requirements() -> dict[str, Any]:
    """
    Audits static runtime requirements (env vars + binaries) per canonical agent.
    This supplements external connectivity checks without affecting preflight summary counts.
    """
    per_agent = []
    mcp_servers = _load_configured_mcp_servers()
    missing_total = 0
    for spec in get_agent_specs(include_deprecated=False):
        missing_env = [name for name in spec.required_env if not (os.getenv(name) or "").strip()]
        missing_bins = [name for name in spec.required_binaries if shutil.which(name) is None]
        missing_mcps = [name for name in spec.required_mcps if name not in mcp_servers]
        missing_total += len(missing_env) + len(missing_bins) + len(missing_mcps)
        per_agent.append(
            {
                "role": spec.role,
                "ok": not (missing_env or missing_bins or missing_mcps),
                "missing_env": missing_env,
                "missing_binaries": missing_bins,
                "missing_mcps": missing_mcps,
                "permission_profile": list(spec.permission_profile),
            }
        )
    return {
        "ok": missing_total == 0,
        "total_agents": len(per_agent),
        "agents_with_missing_requirements": sum(1 for a in per_agent if not a["ok"]),
        "configured_mcps": sorted(mcp_servers),
        "agents": per_agent,
    }


def _load_configured_mcp_servers() -> set[str]:
    """
    Reads local MCP server declarations from unified MCP JSON config.
    """
    config_path = os.getenv("MCP_UNIFIED_CONFIG_PATH", "/home/mcp/unified_mcp_config.json")
    p = Path(config_path)
    if not p.exists():
        return set()
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return set()
    servers = payload.get("mcpServers", {})
    if isinstance(servers, dict):
        return set(servers.keys())
    return set()
