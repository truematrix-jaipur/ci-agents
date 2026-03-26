from __future__ import annotations

from agents.server_agent.ops_checks import check_container_status, get_system_metrics


class RuntimeOpsSubagent:
    """Single-purpose runtime operations subagent shared by server/devops flows."""

    def handle_task(self, task_payload: dict) -> dict:
        task_type = (task_payload or {}).get("type")
        if task_type == "check_container_status":
            return check_container_status()
        if task_type == "get_system_metrics":
            return get_system_metrics()
        return {"status": "error", "message": f"Unsupported runtime ops task: {task_type}"}
