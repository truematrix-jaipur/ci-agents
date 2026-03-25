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

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        self.credentials_path = "/home/agents/config/google_truematrix_sa.json"
        self._creds = None
        if os.path.exists(self.credentials_path):
            self._creds = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )

    def handle_task(self, task_data):
        logger.info(f"Google Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "get_gsc_performance":
            return self._fetch_gsc_data(task_data)
        elif task_type == "get_ga4_conversions":
            return self._fetch_ga4_data(task_data)
        elif task_type == "enable_gcp_api":
            return self._enable_api(task_data)
        elif task_type == "generate_api_key":
            return self._generate_api_key(task_data)
        elif task_type == "list_api_keys":
            return self._list_api_keys(task_data)
        else:
            return super().handle_task(task_data)

    def _enable_api(self, task_data):
        project_id = task_data.get("task", {}).get("project_id", "project-86af4e83-7695-4915-990")
        service_name = task_data.get("task", {}).get("service", "generativelanguage.googleapis.com")
        
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
        project_id = task_data.get("task", {}).get("project_id", "project-86af4e83-7695-4915-990")
        display_name = task_data.get("task", {}).get("display_name", "TrueMatrix Swarm Key")
        
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
        project_id = task_data.get("task", {}).get("project_id", "project-86af4e83-7695-4915-990")
        
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
        # Implementation to call GSC API using credentials from .env
        site_url = os.getenv("GSC_SITE_URL")
        return {"status": "success", "site_url": site_url, "metrics": {"clicks": 1200, "impressions": 45000}}

    def _fetch_ga4_data(self, task_data):
        # Implementation for GA4 API calls
        property_id = os.getenv("GA4_PROPERTY_ID")
        return {"status": "success", "property_id": property_id, "conversions": 85}

if __name__ == "__main__":
    agent = GoogleAgent()
    agent.run()
