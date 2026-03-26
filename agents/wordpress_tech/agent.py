import sys
import os
import logging
import json
import subprocess
import datetime
import shutil
from pathlib import Path

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

        if task_type == "health_check":
            return self._health_check(task_data)
        elif task_type == "implement_fix":
            return self._implement_fix(task_data)
        elif task_type == "update_plugin_code":
            return self._update_plugin_code(task_data)
        elif task_type == "update_theme_code":
            return self._update_theme_code(task_data)
        elif task_type == "woocommerce_rule_change":
            return self._woocommerce_rule_change(task_data)
        else:
            return super().handle_task(task_data)

    def _health_check(self, task_data):
        site_path = self._resolve_site_path(task_data)
        cli_result = self._run_wp_cli(site_path, ["core", "is-installed"], timeout=20)
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

    def _run_wp_cli(self, site_path, cli_args, timeout=60):
        cmd = [config.WP_CLI_PATH, *cli_args, f"--path={site_path}", "--allow-root"]
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
