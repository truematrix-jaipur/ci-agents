#!/usr/bin/env python3
import importlib
import json
import logging
import os
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.db_connectors.db_manager import db_manager
from core.agent_catalog import get_agent_specs


INCLUDE_DEPRECATED = os.getenv("HEALTHCHECK_INCLUDE_DEPRECATED_AGENTS", "false").lower() in ("1", "true", "yes")
AGENT_SPECS = [
    (spec.module_path, spec.class_name, spec.smoke_task)
    for spec in get_agent_specs(include_deprecated=INCLUDE_DEPRECATED)
]


def _check_core_deps():
    out = {
        "redis": {"ok": False, "detail": ""},
        "mysql": {"ok": False, "detail": ""},
        "erpnext_mysql": {"ok": False, "detail": ""},
        "cli_bins": {},
    }

    try:
        out["redis"]["ok"] = bool(db_manager.get_redis_client().ping())
        out["redis"]["detail"] = "ping ok"
    except Exception as e:
        out["redis"]["detail"] = str(e)

    try:
        conn = db_manager.get_mysql_connection()
        out["mysql"]["ok"] = conn is not None
        out["mysql"]["detail"] = "connected" if conn else "connection unavailable"
        if conn:
            conn.close()
    except Exception as e:
        out["mysql"]["detail"] = str(e)

    try:
        conn = db_manager.get_erpnext_mysql_connection()
        out["erpnext_mysql"]["ok"] = conn is not None
        out["erpnext_mysql"]["detail"] = "connected" if conn else "connection unavailable"
        if conn:
            conn.close()
    except Exception as e:
        out["erpnext_mysql"]["detail"] = str(e)

    for cmd in ("docker", "wp", "systemctl", "journalctl", "mysql"):
        out["cli_bins"][cmd] = bool(shutil.which(cmd))
    return out


def _check_env_capabilities():
    def _present(name: str) -> bool:
        return bool((os.getenv(name) or "").strip())

    per_agent_requirements = []
    specs = get_agent_specs(include_deprecated=INCLUDE_DEPRECATED)
    for spec in specs:
        missing_env = [name for name in spec.required_env if not _present(name)]
        missing_bins = [name for name in spec.required_binaries if not shutil.which(name)]
        per_agent_requirements.append(
            {
                "role": spec.role,
                "ok": not (missing_env or missing_bins),
                "missing_env": missing_env,
                "missing_binaries": missing_bins,
            }
        )

    return {
        "llm": {
            "openai_key_present": _present("OPENAI_API_KEY"),
            "anthropic_key_present": _present("ANTHROPIC_API_KEY"),
            "gemini_key_present": _present("GEMINI_API_KEY") or _present("GOOGLE_API_KEY"),
        },
        "seo": {
            "gsc_service_account_file": os.getenv("GSC_SERVICE_ACCOUNT_FILE", ""),
            "gsc_service_account_exists": Path(
                os.getenv("GSC_SERVICE_ACCOUNT_FILE", "/home/agents/agents/seo_agent/credentials/gsc_service_account.json")
            ).exists(),
        },
        "agent_requirements": per_agent_requirements,
    }


def _healthcheck_agent(module_path, class_name, smoke_task):
    result = {
        "module": module_path,
        "class": class_name,
        "import_ok": False,
        "init_ok": False,
        "task_smoke": "skipped",
        "task_result_status": None,
        "task_response": None,
        "error": None,
    }
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        result["import_ok"] = True
        agent = cls()
        result["init_ok"] = True

        if smoke_task is None:
            result["task_smoke"] = "skipped(write_or_cost_risk)"
            return result

        try:
            resp = agent.handle_task(smoke_task)
            result["task_smoke"] = "executed"
            if isinstance(resp, dict):
                result["task_result_status"] = resp.get("status")
                result["task_response"] = {
                    "message": str(resp.get("message", ""))[:300],
                    "keys": sorted(list(resp.keys()))[:20],
                }
            else:
                result["task_result_status"] = "non_dict_response"
                result["task_response"] = {"preview": str(resp)[:300]}
        except Exception as e:
            result["task_smoke"] = "error"
            result["error"] = f"task_error: {e}"
    except Exception as e:
        result["error"] = f"{e}\n{traceback.format_exc(limit=2)}"
    return result


def main():
    logging.disable(logging.INFO)
    logging.getLogger().setLevel(logging.ERROR)
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "core_dependencies": _check_core_deps(),
        "env_capabilities": _check_env_capabilities(),
        "agents": [],
    }
    for module_path, class_name, smoke_task in AGENT_SPECS:
        report["agents"].append(_healthcheck_agent(module_path, class_name, smoke_task))

    critical = [
        a for a in report["agents"]
        if not a["import_ok"] or not a["init_ok"]
    ]
    degraded = [
        a for a in report["agents"]
        if a["task_smoke"] == "error" or a.get("task_result_status") == "error"
    ]
    report["summary"] = {
        "total_agents": len(report["agents"]),
        "critical_failures": len(critical),
        "degraded_agents": len(degraded),
    }
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
