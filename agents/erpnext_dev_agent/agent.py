import sys
import os
import logging
import json
import requests
import subprocess
import shlex
from datetime import datetime, timezone
from pathlib import Path

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from config.settings import config

logger = logging.getLogger(__name__)

class ERPNextDevAgent(BaseAgent):
    AGENT_ROLE = "erpnext_dev_agent"
    SYSTEM_PROMPT = """You are an expert Frappe/ERPNext Developer Agent.
    You own full bench lifecycle operations for Frappe/ERPNext: app install,
    migrate, patch orchestration, asset build, rollback planning, and
    multi-site release execution within the live environment.
    
    You follow the bench command standards and write clean, maintainable 
    Python and JS code."""

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        # Developers often need direct access to the app paths
        self.bench_path = "/home/erpnext/frappe_docker"
        self.logs_dir = Path("/home/agents/logs/erpnext_dev")
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def handle_task(self, task_data):
        logger.info(f"ERPNext Dev Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "create_doctype":
            return self._create_doctype(task_data)
        elif task_type == "apply_fix":
            return self._apply_fix(task_data)
        elif task_type == "plan_release":
            return self._plan_release(task_data)
        elif task_type == "execute_release":
            return self._execute_release(task_data)
        elif task_type == "rollback_release":
            return self._rollback_release(task_data)
        else:
            return super().handle_task(task_data)

    def _create_doctype(self, task_data):
        doctype_name = task_data.get("task", {}).get("name")
        module = task_data.get("task", {}).get("module", "Custom")
        if not doctype_name:
            return {"status": "error", "message": "name is required"}
        if not (config.ERP_URL and config.ERP_API_KEY and config.ERP_API_SECRET):
            return {"status": "error", "message": "ERP REST credentials are not configured"}
        url = f"{config.ERP_URL.rstrip('/')}/api/resource/DocType"
        headers = {
            "Authorization": f"token {config.ERP_API_KEY}:{config.ERP_API_SECRET}",
            "Content-Type": "application/json",
        }
        payload = {
            "name": doctype_name,
            "module": module,
            "custom": 1,
            "istable": 0,
            "track_changes": 1,
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code >= 400:
                return {"status": "error", "message": f"DocType create failed: HTTP {r.status_code} {r.text[:300]}"}
            data = r.json().get("data", {})
            return {"status": "success", "message": f"DocType {doctype_name} created.", "doctype": data.get("name")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _apply_fix(self, task_data):
        service = task_data.get("task", {}).get("service")
        if not service:
            return {"status": "error", "message": "service is required"}
        # Delegate restart/fix action to server_agent so operations stay centralized.
        delegated_id = self.publish_task_to_agent("server_agent", {"type": "fix_service", "service": service})
        return {"status": "success", "message": f"Fix delegated for service {service}.", "delegated_task_id": delegated_id}

    # ---------------------------
    # Full bench lifecycle tasks
    # ---------------------------
    def _plan_release(self, task_data):
        task = task_data.get("task", {})
        release_plan = self._build_release_plan(task)
        if release_plan.get("status") != "success":
            return release_plan
        return {"status": "success", "release_plan": release_plan["release_plan"]}

    def _execute_release(self, task_data):
        task = task_data.get("task", {})
        dry_run = bool(task.get("dry_run", True))
        plan_resp = self._build_release_plan(task)
        if plan_resp.get("status") != "success":
            return plan_resp
        plan = plan_resp["release_plan"]

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_log = {
            "run_id": run_id,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "plan": plan,
            "steps": [],
        }

        # Gate: baseline check before mutating release operations.
        baseline_cmd = "/home/erpnext/frappe_docker/scripts/baseline-check.sh"
        if not dry_run:
            baseline_res = self._run_host_cmd(baseline_cmd, timeout=180)
            run_log["steps"].append({"name": "baseline_check", **baseline_res})
            if baseline_res.get("returncode", 1) != 0:
                self._persist_release_run(run_id, run_log)
                return {
                    "status": "error",
                    "message": "Baseline check failed; release aborted.",
                    "run_id": run_id,
                    "run_log_path": str(self.logs_dir / f"release_run_{run_id}.json"),
                }
        else:
            run_log["steps"].append({"name": "baseline_check", "skipped": True, "reason": "dry_run"})

        for step in plan.get("steps", []):
            step_name = step.get("name")
            commands = step.get("commands", [])
            step_result = {"name": step_name, "commands": []}
            if dry_run:
                step_result["dry_run"] = True
                step_result["commands"] = [{"command": c, "skipped": True} for c in commands]
                run_log["steps"].append(step_result)
                continue

            for cmd in commands:
                res = self._run_host_cmd(cmd, timeout=step.get("timeout", 180))
                step_result["commands"].append(res)
                if res.get("returncode", 1) != 0:
                    step_result["status"] = "error"
                    step_result["failed_command"] = cmd
                    run_log["steps"].append(step_result)
                    self._persist_release_run(run_id, run_log)
                    return {
                        "status": "error",
                        "message": f"Release step failed: {step_name}",
                        "run_id": run_id,
                        "failed_step": step_name,
                        "failed_command": cmd,
                        "run_log_path": str(self.logs_dir / f"release_run_{run_id}.json"),
                    }
            step_result["status"] = "success"
            run_log["steps"].append(step_result)

        run_log["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        self._persist_release_run(run_id, run_log)
        return {
            "status": "success",
            "message": "Release executed successfully." if not dry_run else "Dry-run release plan generated.",
            "run_id": run_id,
            "dry_run": dry_run,
            "run_log_path": str(self.logs_dir / f"release_run_{run_id}.json"),
            "plan_summary": {
                "sites": plan.get("sites", []),
                "apps": plan.get("apps", []),
                "patches": plan.get("patches", []),
            },
        }

    def _rollback_release(self, task_data):
        """
        Rollback is intentionally explicit and safe-by-default:
        - default: dry-run plan from release log
        - execute only when execute=true
        """
        task = task_data.get("task", {})
        run_id = task.get("run_id")
        if not run_id:
            return {"status": "error", "message": "run_id is required for rollback"}

        release_log_path = self.logs_dir / f"release_run_{run_id}.json"
        if not release_log_path.exists():
            return {"status": "error", "message": f"Release run log not found: {release_log_path}"}

        execute = bool(task.get("execute", False))
        rollback_steps = task.get("rollback_steps", [])
        if rollback_steps and not isinstance(rollback_steps, list):
            return {"status": "error", "message": "rollback_steps must be a list"}

        recorded = json.loads(release_log_path.read_text(encoding="utf-8"))
        plan = recorded.get("plan", {})
        sites = plan.get("sites", [])
        apps = plan.get("apps", [])

        generated_steps = rollback_steps or self._default_rollback_steps(sites=sites, apps=apps)
        if not execute:
            return {
                "status": "success",
                "message": "Rollback plan generated (dry-run). Set execute=true to run.",
                "run_id": run_id,
                "rollback_plan": generated_steps,
            }

        rollback_log = {
            "rollback_of_run_id": run_id,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "steps": [],
        }
        for cmd in generated_steps:
            res = self._run_host_cmd(cmd, timeout=240)
            rollback_log["steps"].append(res)
            if res.get("returncode", 1) != 0:
                rollback_log["status"] = "error"
                self._persist_rollback_run(run_id, rollback_log)
                return {
                    "status": "error",
                    "message": "Rollback failed",
                    "failed_command": cmd,
                    "rollback_log_path": str(self.logs_dir / f"rollback_run_{run_id}.json"),
                }

        rollback_log["status"] = "success"
        rollback_log["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        self._persist_rollback_run(run_id, rollback_log)
        return {
            "status": "success",
            "message": "Rollback completed successfully.",
            "rollback_log_path": str(self.logs_dir / f"rollback_run_{run_id}.json"),
        }

    def _build_release_plan(self, task: dict):
        sites = task.get("sites") or ["erp.igmhealth.com"]
        apps = task.get("apps") or []
        patches = task.get("patches") or []
        restart_services = task.get(
            "restart_services", ["backend", "queue-short", "queue-long", "scheduler"]
        )

        if not isinstance(sites, list) or not all(isinstance(s, str) and s.strip() for s in sites):
            return {"status": "error", "message": "sites must be a non-empty list of site names"}
        if not isinstance(apps, list) or not all(isinstance(a, str) and a.strip() for a in apps):
            return {"status": "error", "message": "apps must be a list of app names"}
        if not isinstance(patches, list):
            return {"status": "error", "message": "patches must be a list"}
        if not isinstance(restart_services, list):
            return {"status": "error", "message": "restart_services must be a list"}

        compose_dir = shlex.quote(self.bench_path)

        steps = []
        if apps:
            app_cmds = []
            for app in apps:
                for site in sites:
                    app_cmds.append(
                        f"cd {compose_dir} && docker exec -i frappe_docker-backend-1 bash -lc "
                        f"\"cd /home/frappe/frappe-bench && bench --site {shlex.quote(site)} install-app {shlex.quote(app)}\""
                    )
            steps.append({"name": "install_apps", "commands": app_cmds, "timeout": 300})

        migrate_cmds = []
        for site in sites:
            migrate_cmds.append(
                f"cd {compose_dir} && docker exec -i frappe_docker-backend-1 bash -lc "
                f"\"cd /home/frappe/frappe-bench && bench --site {shlex.quote(site)} migrate\""
            )
        steps.append({"name": "migrate_sites", "commands": migrate_cmds, "timeout": 600})

        if patches:
            patch_cmds = []
            for site in sites:
                for patch in patches:
                    patch_cmds.append(
                        f"cd {compose_dir} && docker exec -i frappe_docker-backend-1 bash -lc "
                        f"\"cd /home/frappe/frappe-bench && bench --site {shlex.quote(site)} execute {shlex.quote(patch)}\""
                    )
            steps.append({"name": "run_patches", "commands": patch_cmds, "timeout": 300})

        build_cmds = []
        for site in sites:
            build_cmds.append(f"/home/erpnext/frappe_docker/scripts/build-assets.sh {shlex.quote(site)}")
        steps.append({"name": "build_assets", "commands": build_cmds, "timeout": 600})

        if restart_services:
            restart_list = " ".join(shlex.quote(s) for s in restart_services)
            steps.append(
                {
                    "name": "restart_services",
                    "commands": [f"cd {compose_dir} && docker compose restart {restart_list}"],
                    "timeout": 180,
                }
            )

        return {
            "status": "success",
            "release_plan": {
                "sites": sites,
                "apps": apps,
                "patches": patches,
                "restart_services": restart_services,
                "steps": steps,
            },
        }

    def _default_rollback_steps(self, sites: list[str], apps: list[str]):
        compose_dir = shlex.quote(self.bench_path)
        steps = []

        # Conservative rollback: re-run migrate and clear caches, then restart.
        for site in sites:
            steps.append(
                f"cd {compose_dir} && docker exec -i frappe_docker-backend-1 bash -lc "
                f"\"cd /home/frappe/frappe-bench && bench --site {shlex.quote(site)} migrate && "
                f"bench --site {shlex.quote(site)} clear-cache && bench --site {shlex.quote(site)} clear-website-cache\""
            )
        steps.append(
            f"cd {compose_dir} && docker compose restart backend queue-short queue-long scheduler"
        )
        return steps

    def _run_host_cmd(self, cmd: str, timeout: int = 180):
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                executable="/bin/bash",
            )
            return {
                "command": cmd,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:],
                "stderr": proc.stderr[-4000:],
            }
        except subprocess.TimeoutExpired:
            return {"command": cmd, "returncode": 124, "stdout": "", "stderr": f"timeout after {timeout}s"}
        except Exception as e:
            return {"command": cmd, "returncode": 1, "stdout": "", "stderr": str(e)}

    def _persist_release_run(self, run_id: str, payload: dict):
        out = self.logs_dir / f"release_run_{run_id}.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _persist_rollback_run(self, run_id: str, payload: dict):
        out = self.logs_dir / f"rollback_run_{run_id}.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

if __name__ == "__main__":
    agent = ERPNextDevAgent()
    agent.run()
