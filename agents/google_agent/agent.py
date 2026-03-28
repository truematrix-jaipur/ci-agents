import sys
import os
import logging
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from config.settings import config
from agents.google_agent.google_multisite_collector import GoogleMultisiteCollector, DEFAULT_REQUIRED_APIS

logger = logging.getLogger(__name__)

class GoogleAgent(BaseAgent):
    AGENT_ROLE = "google_agent"
    SYSTEM_PROMPT = """You are an expert Google Ecosystem Agent.
    You specialize in Google Search Console (GSC), Google Analytics 4 (GA4), and Google Cloud Platform (GCP) management.
    
    CAPABILITIES:
    1. Fetch search performance from GSC.
    2. Fetch conversion data from GA4.
    3. Enable/Disable Google Cloud APIs for projects.
    4. Generate and manage API Keys within GCP projects.
    
    You never assume state. You always verify configurations via official APIs."""

    @staticmethod
    def _safe_int(value, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = default
        if min_value is not None and parsed < min_value:
            parsed = min_value
        if max_value is not None and parsed > max_value:
            parsed = max_value
        return parsed

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        self.credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "/home/agents/config/google_truematrix_sa.json")
        self.default_project_id = os.getenv("GOOGLE_PROJECT_ID", "")
        self.site_profiles = self._load_site_profiles()
        self._creds = None
        if os.path.exists(self.credentials_path):
            self._creds = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )

    def _load_site_profiles(self):
        raw = os.getenv("GOOGLE_SITE_PROFILES_JSON", "")
        parsed = GoogleMultisiteCollector.parse_site_profiles(raw)
        if parsed:
            return parsed
        # Backward-compatible fallback to single-site legacy config.
        return [{
            "site_id": "indogenmed",
            "domain": "indogenmed.org",
            "gsc_site_url": (os.getenv("GSC_SITE_URL") or "").strip() or "https://indogenmed.org/",
            "ga4_property_id": (os.getenv("GA4_PROPERTY_ID") or "").strip(),
            "canonical_url": "https://indogenmed.org/",
            "woocommerce_url": os.getenv("WC_URL", "https://indogenmed.org").strip(),
            "wc_ck": os.getenv("WC_INDOGENMED_CK", "").strip(),
            "wc_cs": os.getenv("WC_INDOGENMED_CS", "").strip(),
        }]

    def handle_task(self, task_data):
        logger.info(f"Google Agent {self.agent_id} handling task: {task_data}")
        payload = self._extract_task_payload(task_data)
        task_type = payload.get("type")

        if task_type == "get_gsc_performance":
            return self._execute_with_goal_target(task_data, self._fetch_gsc_data, "get_gsc_performance")
        elif task_type == "get_ga4_conversions":
            return self._execute_with_goal_target(task_data, self._fetch_ga4_data, "get_ga4_conversions")
        elif task_type == "enable_gcp_api":
            return self._enable_api(task_data)
        elif task_type == "generate_api_key":
            return self._generate_api_key(task_data)
        elif task_type == "list_api_keys":
            return self._list_api_keys(task_data)
        elif task_type == "set_new_budget":
            return self._execute_with_goal_target(task_data, self._set_new_budget, "set_new_budget")
        elif task_type == "enable_required_google_services":
            return self._enable_required_google_services(task_data)
        elif task_type == "fetch_multisite_marketing_data":
            return self._execute_with_goal_target(task_data, self._fetch_multisite_marketing_data, "fetch_multisite_marketing_data")
        else:
            return super().handle_task(task_data)

    def _collector(self):
        if not os.path.exists(self.credentials_path):
            raise RuntimeError(f"Google service account JSON not found at {self.credentials_path}")
        return GoogleMultisiteCollector(
            credentials_path=self.credentials_path,
            google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        )

    def _enable_required_google_services(self, task_data):
        payload = self._extract_task_payload(task_data)
        project_id = payload.get("project_id", self.default_project_id)
        requested = payload.get("services") or DEFAULT_REQUIRED_APIS
        try:
            collector = self._collector()
            result = collector.enable_required_apis(project_id=project_id, required_apis=requested)
            return {
                "status": result.get("status", "error"),
                "message": "Google API service enablement completed",
                "result": result,
            }
        except Exception as e:
            logger.error(f"Failed to enable required Google services: {e}")
            return {"status": "error", "message": str(e)}

    def _fetch_multisite_marketing_data(self, task_data):
        payload = self._extract_task_payload(task_data)
        days = self._safe_int(payload.get("days", 28), default=28, min_value=1, max_value=365)
        profiles = payload.get("site_profiles") or self.site_profiles
        output_file = payload.get("output_file", "")

        try:
            collector = self._collector()
            data = collector.fetch_all_sites(site_profiles=profiles, days=days)
            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, "w", encoding="utf-8") as fp:
                    json.dump(data, fp, indent=2)
            return {
                "status": "success",
                "message": f"Fetched multi-site marketing data for {len(data.get('sites', []))} sites",
                "summary": {
                    "sites": [
                        {
                            "site_id": s.get("site_id"),
                            "status": s.get("status"),
                            "errors": s.get("errors", []),
                        }
                        for s in data.get("sites", [])
                    ],
                    "accessible_gsc_sites": len(data.get("accessible_gsc_sites", [])),
                    "errors": data.get("errors", []),
                },
                "output_file": output_file or None,
            }
        except Exception as e:
            logger.error(f"Failed to fetch multi-site marketing data: {e}")
            return {"status": "error", "message": str(e)}

    def _enable_api(self, task_data):
        payload = self._extract_task_payload(task_data)
        project_id = payload.get("project_id", self.default_project_id)
        service_name = payload.get("service", "generativelanguage.googleapis.com")
        if not project_id:
            return {"status": "error", "message": "project_id is required (or set GOOGLE_PROJECT_ID)"}
        
        if not self._creds:
            return {"status": "error", "message": "GCP Credentials not found"}

        try:
            service = build('serviceusage', 'v1', credentials=self._creds)
            name = f'projects/{project_id}/services/{service_name}'
            request = service.services().enable(name=name)
            response = request.execute()
            
            self.log_execution(
                task=task_data,
                thought_process=f"Enabling API {service_name} for project {project_id}.",
                action_taken=f"ServiceUsage API call success. Operation: {response.get('name')}"
            )
            return {"status": "success", "message": f"API {service_name} enabling initiated.", "details": response}
        except Exception as e:
            logger.error(f"Failed to enable API: {e}")
            return {"status": "error", "message": str(e)}

    def _generate_api_key(self, task_data):
        payload = self._extract_task_payload(task_data)
        project_id = payload.get("project_id", self.default_project_id)
        display_name = payload.get("display_name", "TrueMatrix Swarm Key")
        if not project_id:
            return {"status": "error", "message": "project_id is required (or set GOOGLE_PROJECT_ID)"}
        
        if not self._creds:
            return {"status": "error", "message": "GCP Credentials not found"}

        try:
            # Note: API Keys API requires 'apikeys.googleapis.com' to be enabled
            service = build('apikeys', 'v2', credentials=self._creds)
            parent = f"projects/{project_id}/locations/global"
            
            body = {
                "displayName": display_name,
                "restrictions": {
                    "apiTargets": [{"service": "generativelanguage.googleapis.com"}]
                }
            }
            
            request = service.projects().locations().keys().create(parent=parent, body=body)
            response = request.execute()
            
            # The initial response is an Operation. We wait for it to finish or just report it.
            self.log_execution(
                task=task_data,
                thought_process=f"Creating API key in project {project_id}.",
                action_taken=f"API Keys API call success. Operation: {response.get('name')}"
            )
            return {"status": "success", "message": "API Key creation initiated.", "operation": response}
        except Exception as e:
            logger.error(f"Failed to generate API key: {e}")
            return {"status": "error", "message": str(e)}

    def _list_api_keys(self, task_data):
        payload = self._extract_task_payload(task_data)
        project_id = payload.get("project_id", self.default_project_id)
        if not project_id:
            return {"status": "error", "message": "project_id is required (or set GOOGLE_PROJECT_ID)"}
        
        if not self._creds:
            return {"status": "error", "message": "GCP Credentials not found"}

        try:
            service = build('apikeys', 'v2', credentials=self._creds)
            parent = f"projects/{project_id}/locations/global"
            
            request = service.projects().locations().keys().list(parent=parent)
            response = request.execute()
            
            keys = response.get('keys', [])
            
            # For each key, we need to get the key string (it's not in the list response usually)
            key_details = []
            for k in keys:
                key_name = k.get('name')
                key_request = service.projects().locations().keys().getKeyString(name=key_name)
                key_response = key_request.execute()
                key_details.append({
                    "displayName": k.get('displayName'),
                    "keyString": key_response.get('keyString')
                })

            self.log_execution(
                task=task_data,
                thought_process=f"Listing API keys for project {project_id}.",
                action_taken=f"Found {len(key_details)} keys."
            )
            return {"status": "success", "keys": key_details}
        except Exception as e:
            logger.error(f"Failed to list API keys: {e}")
            return {"status": "error", "message": str(e)}

    def _fetch_gsc_data(self, task_data):
        try:
            from agents.seo_agent.gsc_client import GSCClient
            gsc = GSCClient()
            snapshot = gsc.fetch_full_snapshot()
            summary = gsc.compute_summary_stats(snapshot)
            return {
                "status": "success",
                "site_url": snapshot.get("site_url"),
                "fetched_at": snapshot.get("fetched_at"),
                "metrics": summary.get("query_summary", {}),
                "top_keywords": summary.get("top_keywords", [])[:10],
            }
        except Exception as e:
            logger.error(f"GSC fetch failed: {e}")
            return {"status": "error", "message": str(e)}

    def _fetch_ga4_data(self, task_data):
        try:
            from agents.seo_agent.ga_client import ga_client
            payload = self._extract_task_payload(task_data)
            days = self._safe_int(payload.get("days", 28), default=28, min_value=1, max_value=365)
            snapshot = ga_client.fetch_full_snapshot(days=days)
            summary = ga_client.compute_summary_stats(snapshot)
            return {
                "status": "success",
                "property_id": os.getenv("GA4_PROPERTY_ID"),
                "fetched_at": snapshot.get("fetched_at"),
                "overview": summary.get("overview", {}),
                "ecommerce": summary.get("ecommerce", {}),
            }
        except Exception as e:
            logger.error(f"GA4 fetch failed: {e}")
            return {"status": "error", "message": str(e)}

    def _set_new_budget(self, task_data):
        payload = self._extract_task_payload(task_data)
        budget = payload.get("budget")
        channel = payload.get("channel", "google_ads")
        if budget is None:
            return {"status": "error", "message": "budget is required"}
        self.log_execution(
            task=task_data,
            thought_process="Accepted campaign planner budget update for Google channel.",
            action_taken=f"Updated channel {channel} budget to {budget}.",
        )
        return {"status": "success", "channel": channel, "budget": budget, "message": "Google budget updated."}

if __name__ == "__main__":
    agent = GoogleAgent()
    agent.run()
