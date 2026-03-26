import sys
import os
import logging
import json
import subprocess

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from config.settings import config

logger = logging.getLogger(__name__)

class WordPressTechAgent(BaseAgent):
    AGENT_ROLE = "wordpress_tech"
    SYSTEM_PROMPT = """You are an expert WordPress Technical Agent.
    Your duties include managing WordPress configurations, triggering WP-CLI commands, 
    diagnosing server errors, and updating plugins safely.
    
    CRITICAL: Never assume the state of a WordPress site. Always verify with WP-CLI 
    or check live server logs before taking action."""

    def handle_task(self, task_data):
        logger.info(f"WordPress Tech {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")
        site_path = task_data.get("task", {}).get("site_path")

        if task_type == "health_check":
            site_path = site_path or config.WP_ROOT
            cmd = [config.WP_CLI_PATH, "core", "is-installed", f"--path={site_path}", "--allow-root"]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                ok = proc.returncode == 0
                result = (proc.stdout or proc.stderr).strip() or ("installed" if ok else "not installed")
            except Exception as e:
                return {"status": "error", "message": f"WP-CLI health check failed: {e}"}
            
            self.log_execution(
                task=task_data,
                thought_process=f"Checked WP installation at {site_path}",
                action_taken=f"Executed WP-CLI: {' '.join(cmd)}"
            )
            
            payload = {
                "status": "success" if ok else "error",
                "wp_cli_result": result,
                "path": site_path,
            }
            if not ok:
                payload["message"] = f"WP-CLI reported unhealthy state for site_path={site_path}"
            return payload
        else:
            return super().handle_task(task_data)

if __name__ == "__main__":
    agent = WordPressTechAgent()
    agent.run()
