from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from core.agent_catalog import get_agent_specs

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"


def _is_python_agent_process_running(script_rel_path: str) -> bool:
    target = script_rel_path.strip()
    if not target:
        return False
    try:
        proc = subprocess.run(
            ["ps", "-eo", "args="],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return False
        for line in (proc.stdout or "").splitlines():
            if target in line and "python" in line and "start_swarm.sh" not in line:
                return True
    except Exception as e:
        logger.warning(f"Unable to inspect process list for {target}: {e}")
    return False


def _start_agent_process(script_rel_path: str, log_name: str) -> bool:
    script_path = BASE_DIR / script_rel_path
    if not script_path.exists():
        logger.error(f"Agent script missing, skip autostart: {script_path}")
        return False

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / log_name

    with open(log_path, "a", encoding="utf-8") as log_fp:
        subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(BASE_DIR),
            stdout=log_fp,
            stderr=log_fp,
            start_new_session=True,
        )
    logger.info(f"Autostart launched: {script_rel_path}")
    return True


def ensure_agents_running() -> list[str]:
    """
    Ensure one runtime process exists for each canonical (non-deprecated) agent.
    Returns the list of role ids started by this call.
    """
    started: list[str] = []

    for spec in get_agent_specs(include_deprecated=False):
        module_fs = spec.module_path.replace(".", "/")
        script_rel = f"{module_fs}.py"
        if _is_python_agent_process_running(script_rel):
            continue
        started_ok = _start_agent_process(script_rel, f"{spec.role}.log")
        if started_ok:
            started.append(spec.role)
    return started
