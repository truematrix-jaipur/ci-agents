import sys
import os
import logging
import json
import subprocess
import datetime
import shutil
import re
import time
from pathlib import Path
from urllib.parse import urlparse

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
    METRICS_REDIS_KEY = "agent_runtime_metrics:wordpress_tech"
    METRICS_EVENTS_REDIS_KEY = "agent_runtime_metrics_events:wordpress_tech"

    def __init__(self, agent_id=None):
        super().__init__(agent_id)
        self._metrics = {"tasks_total": 0, "success": 0, "warning": 0, "error": 0}

    def handle_task(self, task_data):
        logger.info(f"WordPress Tech {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")
        started_at = time.perf_counter()
        route = task_type or "unknown"
        if task_type == "manual_command":
            result = self._execute_with_goal_target(task_data, self._handle_manual_command, "manual_command")
            route = "manual_command"
        elif task_type == "health_check":
            result = self._execute_with_goal_target(task_data, self._health_check, "health_check")
            route = "health_check"
        elif task_type == "implement_fix":
            result = self._execute_with_goal_target(task_data, self._implement_fix, "implement_fix")
            route = "implement_fix"
        elif task_type == "update_plugin_code":
            result = self._execute_with_goal_target(task_data, self._update_plugin_code, "update_plugin_code")
            route = "update_plugin_code"
        elif task_type == "update_theme_code":
            result = self._execute_with_goal_target(task_data, self._update_theme_code, "update_theme_code")
            route = "update_theme_code"
        elif task_type == "woocommerce_rule_change":
            result = self._execute_with_goal_target(task_data, self._woocommerce_rule_change, "woocommerce_rule_change")
            route = "woocommerce_rule_change"
        else:
            result = super().handle_task(task_data)
            route = "fallback"
        return self._finalize_result(result=result, started_at=started_at, route=route)

    def _finalize_result(self, result, started_at: float, route: str):
        out = result if isinstance(result, dict) else {"status": "success", "result": result}
        status = str(out.get("status", "success")).lower()
        if status not in {"success", "warning", "error"}:
            status = "success"
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        if not hasattr(self, "_metrics") or not isinstance(self._metrics, dict):
            self._metrics = {"tasks_total": 0, "success": 0, "warning": 0, "error": 0}
        self._metrics["tasks_total"] += 1
        self._metrics[status] += 1
        out.setdefault("execution", {})
        out["execution"].update(
            {
                "route": route,
                "duration_ms": elapsed_ms,
                "metrics_snapshot": dict(self._metrics),
            }
        )
        self._persist_metrics(route=route, status=status, duration_ms=elapsed_ms)
        return out

    def _persist_metrics(self, route: str, status: str, duration_ms: int):
        redis_client = getattr(self, "redis_client", None)
        if redis_client is None:
            return
        now = datetime.datetime.now(datetime.UTC).isoformat()
        try:
            mapping = {
                "tasks_total": str(self._metrics.get("tasks_total", 0)),
                "success": str(self._metrics.get("success", 0)),
                "warning": str(self._metrics.get("warning", 0)),
                "error": str(self._metrics.get("error", 0)),
                "last_route": route,
                "last_status": status,
                "last_duration_ms": str(int(duration_ms)),
                "updated_at_utc": now,
            }
            redis_client.hset(self.METRICS_REDIS_KEY, mapping=mapping)
            redis_client.expire(self.METRICS_REDIS_KEY, 3600 * 24 * 30)
            event = {
                "timestamp_utc": now,
                "route": route,
                "status": status,
                "duration_ms": int(duration_ms),
                "tasks_total": int(self._metrics.get("tasks_total", 0)),
            }
            redis_client.rpush(self.METRICS_EVENTS_REDIS_KEY, json.dumps(event))
            redis_client.ltrim(self.METRICS_EVENTS_REDIS_KEY, -5000, -1)
            redis_client.expire(self.METRICS_EVENTS_REDIS_KEY, 3600 * 24 * 30)
        except Exception:
            return

    def _health_check(self, task_data):
        site_path = self._resolve_site_path(task_data)
        cli_result = self._run_wp_cli(
            site_path,
            ["core", "is-installed"],
            timeout=20,
            extra_flags=["--skip-plugins", "--skip-themes"],
        )
        self.log_execution(
            task=task_data,
            thought_process=f"Checked WP installation at {site_path}",
            action_taken=f"Executed WP-CLI: {cli_result['command']}",
            status="success" if cli_result["ok"] else "warning",
        )

        payload = {
            "status": "success" if cli_result["ok"] else "error",
            "wp_cli_result": cli_result["output"] or ("installed" if cli_result["ok"] else "not installed"),
            "path": site_path,
            "command": cli_result["command"],
        }
        if not cli_result["ok"]:
            payload["message"] = f"WP-CLI reported unhealthy state for site_path={site_path}"
        return payload

    def _handle_manual_command(self, task_data):
        task = task_data.get("task", {})
        raw_command = (task.get("command") or "").strip()
        if not raw_command:
            return {"status": "error", "message": "command is required for manual_command"}

        command = raw_command.lower()
        if "health" in command:
            result = self._health_check(task_data)
            result["manual_command_routed"] = "health_check"
            return result

        if "transient" in command and ("flush" in command or "delete" in command):
            wc_task = {"task": {"site_path": self._resolve_site_path(task_data), "action": "flush_transients"}}
            result = self._woocommerce_rule_change(wc_task)
            result["manual_command_routed"] = "woocommerce_rule_change.flush_transients"
            return result

        if ("audit" in command and "url" in command) or ("search-replace" in command and "url" in command):
            return self._manual_url_audit(task_data)

        return {
            "status": "success",
            "message": (
                "Manual command not executed to avoid non-deterministic LLM fallback. "
                "Use structured task types: health_check, implement_fix, update_plugin_code, "
                "update_theme_code, woocommerce_rule_change."
            ),
            "skipped_llm": True,
            "manual_command": raw_command,
        }

    def _manual_url_audit(self, task_data):
        task = task_data.get("task", {})
        command = (task.get("command") or "").strip()
        site_path = self._resolve_site_path(task_data)

        old_url = (task.get("old_url") or "").strip()
        new_url = (task.get("new_url") or "").strip()
        if not old_url or not new_url:
            urls = re.findall(r"https?://[^\s\"']+", command)
            if len(urls) >= 2:
                old_url, new_url = urls[0], urls[1]

        if not old_url or not new_url:
            site_url = None
            site_url_resp = self._run_wp_cli(
                site_path,
                ["option", "get", "siteurl"],
                timeout=20,
                extra_flags=["--skip-plugins", "--skip-themes"],
            )
            if site_url_resp["ok"]:
                site_url = (site_url_resp.get("output") or "").strip()
            return {
                "status": "success",
                "message": (
                    "URL audit requires explicit old_url and new_url. "
                    "Dry-run was not executed."
                ),
                "skipped_llm": True,
                "site_url": site_url,
                "example_task": {
                    "task": {
                        "type": "manual_command",
                        "command": "audit and replace URLs",
                        "old_url": "https://old.example.com",
                        "new_url": "https://new.example.org",
                        "apply": False,
                    }
                },
            }

        old_host = urlparse(old_url).hostname or ""
        new_host = urlparse(new_url).hostname or ""
        if not old_host or not new_host:
            return {"status": "error", "message": "old_url and new_url must be absolute URLs with hostnames."}
        if old_host == new_host:
            return {"status": "error", "message": "old_url and new_url hosts are identical; no replacement needed."}

        apply = bool(task.get("apply", False))
        args = [
            "search-replace",
            old_url,
            new_url,
            "--all-tables-with-prefix",
            "--precise",
            "--report-changed-only",
        ]
        if not apply:
            args.append("--dry-run")

        cli_result = self._run_wp_cli(site_path, args, timeout=120)
        status = "success" if cli_result["ok"] else "error"
        message = "URL replacement applied." if apply else "URL replacement audit completed (dry-run)."
        if not cli_result["ok"]:
            message = "URL audit/replacement failed."

        self.log_execution(
            task=task_data,
            thought_process="Handled manual URL audit/replacement command via deterministic WP-CLI flow.",
            action_taken=f"Executed WP-CLI: {cli_result['command']}",
            status="success" if cli_result["ok"] else "warning",
        )
        return {
            "status": status,
            "message": message,
            "manual_command_routed": "url_audit",
            "skipped_llm": True,
            "dry_run": not apply,
            "old_url": old_url,
            "new_url": new_url,
            "command": cli_result["command"],
            "result": cli_result["output"],
            "returncode": cli_result["returncode"],
        }

    def _implement_fix(self, task_data):
        task = task_data.get("task", {})
        mode = task.get("mode", "text_replace")
        if mode != "text_replace":
            return {"status": "error", "message": f"Unsupported implement_fix mode: {mode}"}

        site_path = self._resolve_site_path(task_data)
        target_path = task.get("target_path")
        if not target_path:
            return {"status": "error", "message": "target_path is required"}

        replacements = task.get("replacements")
        if not replacements and task.get("find_text") is not None:
            replacements = [{"find_text": task.get("find_text"), "replace_text": task.get("replace_text", "")}]
        if not replacements or not isinstance(replacements, list):
            return {"status": "error", "message": "replacements list is required"}

        try:
            safe_path = self._safe_target_path(site_path, target_path)
        except Exception as e:
            return {"status": "error", "message": str(e)}
        if not safe_path.exists() or not safe_path.is_file():
            return {"status": "error", "message": f"Target file does not exist: {safe_path}"}

        raw = safe_path.read_text(encoding="utf-8")
        updated = raw
        replacements_applied = 0
        for row in replacements:
            find_text = row.get("find_text")
            replace_text = row.get("replace_text", "")
            if not isinstance(find_text, str) or find_text == "":
                return {"status": "error", "message": "Every replacement must include non-empty find_text"}
            if not isinstance(replace_text, str):
                return {"status": "error", "message": "replace_text must be a string"}
            occurrences = updated.count(find_text)
            if occurrences > 0:
                replacements_applied += occurrences
                updated = updated.replace(find_text, replace_text)

        dry_run = bool(task.get("dry_run", False))
        if updated == raw:
            return {
                "status": "warning",
                "message": "No matching text found; no changes applied.",
                "target_file": str(safe_path),
                "dry_run": dry_run,
            }

        backup_path = None
        if not dry_run:
            backup_path = self._write_file_with_backup(safe_path, updated)

        self.log_execution(
            task=task_data,
            thought_process=f"Applied text replacements to {safe_path} with safety backup={not dry_run}.",
            action_taken=f"Replacements applied: {replacements_applied}. Dry-run: {dry_run}.",
            status="success",
        )
        return {
            "status": "success",
            "message": "Code change prepared." if dry_run else "Code change applied.",
            "target_file": str(safe_path),
            "backup_file": str(backup_path) if backup_path else None,
            "replacements_applied": replacements_applied,
            "dry_run": dry_run,
        }

    def _update_plugin_code(self, task_data):
        task = dict(task_data.get("task", {}))
        plugin_slug = task.get("plugin_slug") or task.get("plugin")
        relative_path = task.get("relative_path")
        if not plugin_slug or not relative_path:
            return {"status": "error", "message": "plugin_slug/plugin and relative_path are required"}

        task["target_path"] = str(Path("wp-content") / "plugins" / plugin_slug / relative_path)
        task["type"] = "implement_fix"
        return self._implement_fix({"task": task})

    def _update_theme_code(self, task_data):
        task = dict(task_data.get("task", {}))
        theme_slug = task.get("theme_slug") or task.get("theme")
        relative_path = task.get("relative_path")
        if not theme_slug or not relative_path:
            return {"status": "error", "message": "theme_slug/theme and relative_path are required"}

        task["target_path"] = str(Path("wp-content") / "themes" / theme_slug / relative_path)
        task["type"] = "implement_fix"
        return self._implement_fix({"task": task})

    def _woocommerce_rule_change(self, task_data):
        task = task_data.get("task", {})
        action = task.get("action") or task.get("operation")
        site_path = self._resolve_site_path(task_data)
        if not action:
            return {"status": "error", "message": "action/operation is required"}

        if action == "set_option":
            option_name = task.get("option_name")
            option_value = task.get("option_value")
            if not option_name:
                return {"status": "error", "message": "option_name is required for set_option"}
            cli_result = self._run_wp_cli(site_path, ["option", "update", option_name, str(option_value)])
        elif action == "update_product_meta":
            product_id = task.get("product_id")
            meta_key = task.get("meta_key")
            meta_value = task.get("meta_value")
            if not product_id or not meta_key:
                return {"status": "error", "message": "product_id and meta_key are required for update_product_meta"}
            cli_result = self._run_wp_cli(site_path, ["post", "meta", "update", str(product_id), str(meta_key), str(meta_value)])
        elif action == "flush_transients":
            cli_result = self._run_wp_cli(site_path, ["transient", "delete", "--all"])
        else:
            return {"status": "error", "message": f"Unsupported woocommerce_rule_change action: {action}"}

        self.log_execution(
            task=task_data,
            thought_process=f"Applied WooCommerce rule change via action={action}.",
            action_taken=f"Executed WP-CLI: {cli_result['command']}",
            status="success" if cli_result["ok"] else "warning",
        )
        return {
            "status": "success" if cli_result["ok"] else "error",
            "action": action,
            "command": cli_result["command"],
            "result": cli_result["output"],
            "returncode": cli_result["returncode"],
        }

    def _resolve_site_path(self, task_data):
        return task_data.get("task", {}).get("site_path") or config.WP_ROOT

    def _safe_target_path(self, site_path, target_path):
        base = Path(site_path).resolve()
        candidate = Path(target_path)
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve()
        if not str(resolved).startswith(str(base) + os.sep):
            raise ValueError("Invalid target_path. Path traversal is not allowed.")
        return resolved

    def _write_file_with_backup(self, path: Path, content: str):
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        backup_path = Path(f"{path}.bak.{stamp}")
        shutil.copy2(path, backup_path)
        path.write_text(content, encoding="utf-8")
        return backup_path

    def _run_wp_cli(self, site_path, cli_args, timeout=60, extra_flags=None):
        cmd = [config.WP_CLI_PATH, *cli_args, f"--path={site_path}", "--allow-root"]
        if extra_flags:
            cmd.extend(extra_flags)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            output = (proc.stdout or proc.stderr).strip()
            return {
                "ok": proc.returncode == 0,
                "output": output,
                "command": " ".join(cmd),
                "returncode": proc.returncode,
            }
        except Exception as e:
            return {
                "ok": False,
                "output": str(e),
                "command": " ".join(cmd),
                "returncode": -1,
            }

if __name__ == "__main__":
    agent = WordPressTechAgent()
    agent.run()
