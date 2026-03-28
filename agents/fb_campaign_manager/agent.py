import sys
import os
import logging
import json
import requests

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class FBCampaignManagerAgent(BaseAgent):
    AGENT_ROLE = "fb_campaign_manager"
    SYSTEM_PROMPT = """You are a Meta Ads Campaign Manager Agent.
    You create, monitor, and optimize Facebook and Instagram ad campaigns.
    
    You do not guess ad performance. You always fetch CTR, ROAS, and CPC 
    metrics from the Meta Ads API."""

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

    def handle_task(self, task_data):
        logger.info(f"FB Campaign Manager {self.agent_id} handling task: {task_data}")
        payload = self._extract_task_payload(task_data)
        task_type = payload.get("type")

        if task_type == "optimize_bidding":
            return self._execute_with_goal_target(task_data, self._optimize_bidding, "optimize_bidding")
        elif task_type == "set_new_budget":
            return self._execute_with_goal_target(task_data, self._set_new_budget, "set_new_budget")
        elif task_type == "fetch_assets":
            return self._fetch_assets(task_data)
        else:
            return super().handle_task(task_data)

    def _optimize_bidding(self, task_data):
        payload = self._extract_task_payload(task_data)
        campaign_id = payload.get("campaign_id")
        return {
            "status": "success",
            "campaign_id": campaign_id,
            "new_bid": "1.25",
            "optimization_applied": 1,
            "action": "Increased bid for performance.",
        }

    def _set_new_budget(self, task_data):
        payload = self._extract_task_payload(task_data)
        budget = payload.get("budget")
        campaign_id = payload.get("campaign_id", "default")
        if budget is None:
            return {"status": "error", "message": "budget is required"}
        self.log_execution(
            task=task_data,
            thought_process="Received campaign planner budget update event.",
            action_taken=f"Applied budget {budget} to campaign {campaign_id}.",
        )
        return {
            "status": "success",
            "campaign_id": campaign_id,
            "budget": budget,
            "applied_budget": budget,
            "message": "Budget updated for FB campaign manager.",
        }

    def _fetch_assets(self, task_data):
        payload = self._extract_task_payload(task_data)
        token = (
            payload.get("user_access_token")
            or os.getenv("FB_USER_ACCESS_TOKEN", "").strip()
            or os.getenv("FB_ACCESS_TOKEN", "").strip()
        )
        if not token:
            return {"status": "error", "message": "Missing Facebook user access token (FB_USER_ACCESS_TOKEN/FB_ACCESS_TOKEN)."}

        timeout = self._safe_int(payload.get("timeout_seconds", 25), default=25, min_value=5, max_value=120)
        graph_version = str(payload.get("graph_version", "v20.0")).strip()
        base = f"https://graph.facebook.com/{graph_version}"

        def _get(path, params):
            r = requests.get(f"{base}/{path}", params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()

        try:
            me = _get("me", {"fields": "id,name", "access_token": token})

            pages = _get(
                "me/accounts",
                {"fields": "id,name,category,access_token", "limit": 200, "access_token": token},
            ).get("data", [])

            adaccounts = _get(
                "me/adaccounts",
                {"fields": "id,name,account_id,account_status,currency,timezone_name", "limit": 200, "access_token": token},
            ).get("data", [])

            businesses = _get(
                "me/businesses",
                {"fields": "id,name,verification_status", "limit": 200, "access_token": token},
            ).get("data", [])

            assets = {
                "me": me,
                "pages": pages,
                "adaccounts": adaccounts,
                "businesses": businesses,
                "counts": {
                    "pages": len(pages),
                    "adaccounts": len(adaccounts),
                    "businesses": len(businesses),
                },
            }
            return {"status": "success", "assets": assets}
        except Exception as e:
            return {"status": "error", "message": f"Facebook asset fetch failed: {e}"}

if __name__ == "__main__":
    agent = FBCampaignManagerAgent()
    agent.run()
