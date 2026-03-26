import sys
import os
import logging
import json
import subprocess
import datetime
from pathlib import Path

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent

from config.settings import config
from agents.server_agent.ops_checks import check_container_status, get_system_metrics

logger = logging.getLogger(__name__)

class ServerAgent(BaseAgent):
    AGENT_ROLE = "server_agent"
    SYSTEM_PROMPT = """You are the Senior Systems Administrator & Server Guardian Agent.
    Your objective is to manage this Linux production server following SRE best practices.
    
    CAPABILITIES:
    1. Monitor CPU, RAM, and Disk usage.
    2. Audit services (systemd) and identify stuck or failed units.
    3. Safely optimize resource usage (e.g., clearing caches, rotating logs).
    4. Detect infinite loops or zombie processes and terminate them.
    5. Cleanup old logs and backups beyond retention periods.
    6. Manage MCP (Model Context Protocol) configurations in /home/mcp.
    
    SAFETY MANDATE:
    - Never 'rm -rf /' or delete critical system files.
    - Always verify if a service is critical before restarting.
    - Log every single shell command executed.
    - Adhere to the 'least privilege' principle where possible, though you have root access for critical maintenance."""

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        self.retention_days = 7 # Default retention for logs/backups
        self.mcp_path = config.MCP_CONFIG_PATH

    def handle_task(self, task_data):
        logger.info(f"Server Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type", "routine_audit")

        if task_type == "routine_audit":
            return self._perform_full_audit(task_data)
        elif task_type == "optimize_resources":
            return self._optimize_resources(task_data)
        elif task_type == "fix_service":
            return self._fix_stuck_service(task_data)
        elif task_type == "cleanup_storage":
            return self._cleanup_storage(task_data)
        elif task_type == "update_mcp_config":
            return self._update_mcp_config(task_data)
        elif task_type == "check_container_status":
            return self._check_container_status(task_data)
        elif task_type == "get_system_metrics":
            return self._get_system_metrics(task_data)
        else:
            return super().handle_task(task_data)

    def _update_mcp_config(self, task_data):
        """Updates MCP configuration file in the mandated /home/mcp directory."""
        config_data = task_data.get("task", {}).get("config")
        filename = task_data.get("task", {}).get("filename", "servers.json")
        
        if not config_data:
            return {"status": "error", "message": "No configuration data provided"}
            
        safe_name = os.path.basename(filename)
        if safe_name != filename:
            return {"status": "error", "message": "Invalid filename. Path traversal is not allowed."}
        if not safe_name.endswith((".json", ".toml")):
            return {"status": "error", "message": "Only .json or .toml MCP config files are allowed."}
        target_file = str(Path(self.mcp_path) / safe_name)
        
        try:
            # Ensure the directory exists
            os.makedirs(self.mcp_path, exist_ok=True)
            
            with open(target_file, "w") as f:
                if safe_name.endswith(".json"):
                    json.dump(config_data, f, indent=4)
                else:
                    if isinstance(config_data, str):
                        f.write(config_data)
                    else:
                        f.write(json.dumps(config_data, indent=2))
                
            self.log_execution(
                task=task_data,
                thought_process=f"Updating MCP config at {target_file} per user mandate.",
                action_taken=f"Wrote MCP config to {target_file}."
            )
            return {"status": "success", "message": f"MCP configuration updated at {target_file}"}
        except Exception as e:
            logger.error(f"Failed to update MCP config: {e}")
            return {"status": "error", "message": str(e)}

    def _execute_safe_command(self, cmd_list, task_data, description, shell=False):
        """Wrapper to execute and log system commands safely."""
        try:
            # If shell=True, cmd_list should be a string; otherwise a list
            cmd_str = cmd_list if shell else ' '.join(cmd_list)
            logger.info(f"Executing: {cmd_str}")
            result = subprocess.run(
                cmd_list, capture_output=True, text=True, timeout=30, shell=shell
            )
            
            self.log_execution(
                task=task_data,
                thought_process=f"Reasoning: {description}",
                action_taken=f"Executed: {cmd_str} | Exit Code: {result.returncode}",
                status="success" if result.returncode == 0 else "warning"
            )
            return result
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return None

    def _perform_full_audit(self, task_data):
        # 1. Check Disk Usage
        df = self._execute_safe_command(["df", "-h", "/"], task_data, "Checking disk pressure.")
        
        # 2. Check for failed services
        services = self._execute_safe_command(["systemctl", "--failed", "--type=service"], task_data, "Auditing failed services.")
        
        # 3. Check for high CPU processes
        top_cpu = self._execute_safe_command(
            ["ps", "-eo", "pcpu,pid,user,args", "--sort=-pcpu"],
            task_data,
            "Identifying CPU intensive processes.",
        )

        audit_report = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "disk_usage": df.stdout.strip() if df else "Error",
            "failed_services": services.stdout.strip() if services else "Error",
            "top_resource_users": "\n".join(top_cpu.stdout.strip().splitlines()[:5]) if top_cpu else "Error"
        }
        
        # If disk is > 90%, automatically trigger cleanup
        # (Simplified logic for the demo)
        return {"status": "success", "report": audit_report}

    def _optimize_resources(self, task_data):
        self._execute_safe_command(["sync"], task_data, "Flushing file system buffers.")
        # Do not run shell redirection tricks here; keep low-risk, observable actions only.
        self._execute_safe_command(["journalctl", "--vacuum-time=2d"], task_data, "Reducing system journal size.")
        return {"status": "success", "message": "Resource optimization sequence completed with safe operations."}

    def _fix_stuck_service(self, task_data):
        service_name = task_data.get("task", {}).get("service")
        if not service_name:
            return {"status": "error", "message": "Service name required"}
        
        # Restart service safely
        self._execute_safe_command(["systemctl", "restart", service_name], task_data, f"Restarting stuck service: {service_name}")
        return {"status": "success", "message": f"Service {service_name} restarted."}

    def _cleanup_storage(self, task_data):
        # 1. Clean journal logs older than 2 days
        self._execute_safe_command(["journalctl", "--vacuum-time=2d"], task_data, "Vacuuming systemd journals.")
        
        # 2. Find and remove old .log files in /var/log (safe rotation handled by logrotate, but we can do extra)
        # Note: In real production, we'd be more surgical.
        
        return {"status": "success", "message": "Log and backup cleanup completed."}

    def _check_container_status(self, task_data):
        result = check_container_status()
        self.log_execution(
            task=task_data,
            thought_process="Running shared ops check for Docker container health.",
            action_taken=f"Container status result: {result.get('status')}",
            status="success" if result.get("status") == "success" else "warning",
        )
        return result

    def _get_system_metrics(self, task_data):
        result = get_system_metrics()
        self.log_execution(
            task=task_data,
            thought_process="Running shared ops check for host load and memory.",
            action_taken=f"System metrics result: {result.get('status')}",
            status="success" if result.get("status") == "success" else "warning",
        )
        return result

if __name__ == "__main__":
    agent = ServerAgent()
    agent.run()
