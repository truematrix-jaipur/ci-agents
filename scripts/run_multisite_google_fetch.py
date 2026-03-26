#!/usr/bin/env python3
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.google_agent.google_multisite_collector import GoogleMultisiteCollector, DEFAULT_REQUIRED_APIS


def _parse_profiles() -> list[dict]:
    raw = os.getenv("GOOGLE_SITE_PROFILES_JSON", "")
    parsed = GoogleMultisiteCollector.parse_site_profiles(raw)
    if parsed:
        return parsed
    return [{
        "site_id": "indogenmed",
        "domain": "indogenmed.org",
        "canonical_url": "https://indogenmed.org/",
        "gsc_site_url": os.getenv("GSC_SITE_URL", "https://indogenmed.org/"),
        "ga4_property_id": os.getenv("GA4_PROPERTY_ID", ""),
        "woocommerce_url": os.getenv("WC_URL", "https://indogenmed.org"),
        "wc_ck": os.getenv("WC_INDOGENMED_CK", ""),
        "wc_cs": os.getenv("WC_INDOGENMED_CS", ""),
    }]


def main() -> int:
    creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH") or os.getenv("GSC_SERVICE_ACCOUNT_FILE", "")
    if not creds or not Path(creds).exists():
        print(json.dumps({"status": "error", "message": f"Service account JSON not found: {creds}"}, indent=2))
        return 1

    collector = GoogleMultisiteCollector(credentials_path=creds, google_api_key=os.getenv("GOOGLE_API_KEY", ""))

    project_id = os.getenv("GOOGLE_PROJECT_ID", "")
    required_apis = [s.strip() for s in os.getenv("GOOGLE_REQUIRED_APIS", ",".join(DEFAULT_REQUIRED_APIS)).split(",") if s.strip()]
    enablement = collector.enable_required_apis(project_id=project_id, required_apis=required_apis) if project_id else {
        "status": "skipped", "message": "GOOGLE_PROJECT_ID is not set", "enabled": [], "failed": []
    }

    days = int(os.getenv("GSC_DAYS_HISTORY", "28"))
    profiles = _parse_profiles()
    data = collector.fetch_all_sites(site_profiles=profiles, days=days)

    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "api_enablement": enablement,
        "data": data,
    }

    out_dir = Path("/home/agents/data/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"multisite_google_fetch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    summary = {
        "status": "success",
        "report": str(out_path),
        "site_count": len(data.get("sites", [])),
        "global_errors": len(data.get("errors", [])),
        "api_enablement_status": enablement.get("status"),
        "api_enablement_failed": len(enablement.get("failed", [])) if isinstance(enablement, dict) else None,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
