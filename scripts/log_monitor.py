#!/usr/bin/env python3
"""
Real-time agent log monitor + safe self-healing actions.

What it does:
- Watches /home/agents/logs/*.log continuously.
- Detects recurring runtime failures (LLM auth/quota, MCP redis startup timeout, task handler errors).
- Applies bounded remediations (provider disable in .env, MCP redis re-verify/sync, agent restart).
- Writes every action to logs/monitor_actions.log.
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path("/home/agents")
LOG_DIR = BASE_DIR / "logs"
ENV_FILE = BASE_DIR / ".env"
MONITOR_LOG = LOG_DIR / "monitor_actions.log"
SCAN_GLOB = "*.log"
POLL_SECONDS = float(os.getenv("LOG_MONITOR_POLL_SECONDS", "1.0"))
ACTION_COOLDOWN_SECONDS = int(os.getenv("LOG_MONITOR_ACTION_COOLDOWN_SECONDS", "180"))
PROCESS_HEALTHCHECK_INTERVAL_SECONDS = int(os.getenv("LOG_MONITOR_PROCESS_HEALTHCHECK_INTERVAL_SECONDS", "15"))
QUEUE_RECOVERY_INTERVAL_SECONDS = int(os.getenv("LOG_MONITOR_QUEUE_RECOVERY_INTERVAL_SECONDS", "30"))
QUEUE_STALE_AFTER_SECONDS = int(os.getenv("TASK_STALE_AFTER_SECONDS", "900"))
RESOURCE_CHECK_INTERVAL_SECONDS = int(os.getenv("LOG_MONITOR_RESOURCE_CHECK_INTERVAL_SECONDS", "20"))
MAX_AGENT_CPU_PERCENT = float(os.getenv("LOG_MONITOR_MAX_AGENT_CPU_PERCENT", "250"))
MAX_AGENT_RSS_MB = float(os.getenv("LOG_MONITOR_MAX_AGENT_RSS_MB", "2048"))
MAX_TOTAL_AGENT_RSS_MB = float(os.getenv("LOG_MONITOR_MAX_TOTAL_AGENT_RSS_MB", "16384"))
PROCESS_HEALTHCHECK_STARTUP_GRACE_SECONDS = int(os.getenv("LOG_MONITOR_PROCESS_HEALTHCHECK_STARTUP_GRACE_SECONDS", "30"))
AUTO_DISABLE_LLM_PROVIDERS = str(
    os.getenv("LOG_MONITOR_AUTO_DISABLE_LLM_PROVIDERS", "false")
).lower() in ("1", "true", "yes", "on")
RESTART_ON_ALL_LLM_FAILED = str(
    os.getenv("LOG_MONITOR_RESTART_ON_ALL_LLM_FAILED", "false")
).lower() in ("1", "true", "yes", "on")

ANTHROPIC_INVALID_PAT = re.compile(r"invalid x-api-key|authentication_error", re.I)
OPENAI_INVALID_PAT = re.compile(r"invalid_api_key|Incorrect API key provided|incorrect api key", re.I)
OPENAI_QUOTA_PAT = re.compile(r"insufficient_quota|You exceeded your current quota", re.I)
GEMINI_QUOTA_PAT = re.compile(r"RESOURCE_EXHAUSTED|exceeded your current quota|rate-limits", re.I)
MCP_REDIS_TIMEOUT_PAT = re.compile(r"mcp:\s+redis-(local|erpnext)\s+failed:.*timed out", re.I)
ALL_LLM_FAILED_PAT = re.compile(r"ALL LLM providers failed", re.I)
TASK_NOT_IMPLEMENTED_PAT = re.compile(r"does not implement task type:\s*([a-zA-Z0-9_]+)", re.I)
ERROR_LINE_PAT = re.compile(r"\b(ERROR|Traceback|Exception)\b", re.I)
FATAL_RESTART_PAT = re.compile(
    r"(Traceback|Critical loop error|Unhandled exception|Segmentation fault|Fatal Python error)",
    re.I,
)


def log(message: str) -> None:
    ts = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    line = f"{ts} {message}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with MONITOR_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def backup_env() -> Path | None:
    if not ENV_FILE.exists():
        return None
    bak = ENV_FILE.with_name(f".env.bak.{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(ENV_FILE, bak)
    log(f"Backed up .env to {bak}")
    return bak


def set_disabled_provider(provider: str) -> bool:
    provider = provider.lower().strip()
    if not provider:
        return False
    if not ENV_FILE.exists():
        log(".env not found; cannot update LLM_DISABLED_PROVIDERS")
        return False
    backup_env()
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    updated = []
    found = False
    for line in lines:
        if line.strip().startswith("LLM_DISABLED_PROVIDERS="):
            found = True
            current = line.split("=", 1)[1]
            providers = {p.strip().lower() for p in current.split(",") if p.strip()}
            providers.add(provider)
            updated.append(f"LLM_DISABLED_PROVIDERS={','.join(sorted(providers))}")
        else:
            updated.append(line)
    if not found:
        updated.append(f"LLM_DISABLED_PROVIDERS={provider}")
    ENV_FILE.write_text("\n".join(updated) + "\n", encoding="utf-8")
    log(f"Set LLM_DISABLED_PROVIDERS to include '{provider}'")
    return True


def run_cmd(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(BASE_DIR))
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def remediate_mcp_redis() -> bool:
    log("Running MCP redis remediation: verify_env_mcp.py + sync_mcp.py")
    rc1, out1, err1 = run_cmd(["python3", "/home/mcp/verify_env_mcp.py"], timeout=180)
    if rc1 != 0:
        log(f"verify_env_mcp.py failed (rc={rc1}) stdout={out1[:200]} stderr={err1[:200]}")
        return False
    rc2, out2, err2 = run_cmd(["python3", "/home/mcp/sync_mcp.py"], timeout=180)
    if rc2 != 0:
        log(f"sync_mcp.py failed (rc={rc2}) stdout={out2[:200]} stderr={err2[:200]}")
        return False
    log("MCP redis remediation completed successfully")
    return True


def load_agent_scripts() -> dict[str, Path]:
    sys.path.insert(0, str(BASE_DIR))
    from core.agent_catalog import get_agent_specs  # pylint: disable=import-outside-toplevel

    mapping: dict[str, Path] = {}
    for spec in get_agent_specs(include_deprecated=False):
        rel = spec.module_path.replace(".", "/") + ".py"
        mapping[spec.role] = BASE_DIR / rel
    return mapping


def list_role_pids(role_script: Path) -> list[int]:
    p = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=False)
    if p.returncode != 0:
        return []
    pids: list[int] = []
    needle = str(role_script)
    for line in (p.stdout or "").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_s, args = parts
        if needle in args and "python" in args and "log_monitor.py" not in args:
            try:
                pids.append(int(pid_s))
            except Exception:
                continue
    return pids


def list_role_process_stats(role_script: Path) -> list[dict]:
    p = subprocess.run(
        ["ps", "-eo", "pid=,%cpu=,rss=,args="],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        return []

    stats: list[dict] = []
    needle = str(role_script)
    for line in (p.stdout or "").splitlines():
        parts = line.strip().split(maxsplit=3)
        if len(parts) != 4:
            continue
        pid_s, cpu_s, rss_kb_s, args = parts
        if needle not in args or "python" not in args or "log_monitor.py" in args:
            continue
        try:
            stats.append(
                {
                    "pid": int(pid_s),
                    "cpu_percent": float(cpu_s),
                    "rss_mb": float(rss_kb_s) / 1024.0,
                    "args": args,
                }
            )
        except Exception:
            continue
    return stats


def ensure_role_running(role: str, scripts: dict[str, Path], cooldown: dict[str, float]) -> bool:
    if role not in scripts:
        return False
    pids = list_role_pids(scripts[role])
    if len(pids) > 1:
        if should_run(f"dedupe:{role}:multi_process", cooldown):
            # Keep the oldest pid, stop the rest.
            for pid in sorted(pids)[1:]:
                try:
                    os.kill(pid, signal.SIGTERM)
                    log(f"Deduped role={role}; sent SIGTERM to extra pid={pid}")
                except Exception as e:
                    log(f"Failed dedupe stop role={role} pid={pid}: {e}")
        return False
    if pids:
        return False
    if not should_run(f"restart:{role}:missing_process", cooldown):
        return False
    log(f"Detected missing process for role={role}; auto-restarting")
    return restart_agent(role, scripts)


def guard_resource_usage(scripts: dict[str, Path], cooldown: dict[str, float]) -> None:
    role_totals: list[tuple[str, float]] = []
    total_agent_rss_mb = 0.0

    for role, script in sorted(scripts.items()):
        stats = list_role_process_stats(script)
        if not stats:
            continue

        role_rss_mb = sum(s["rss_mb"] for s in stats)
        total_agent_rss_mb += role_rss_mb
        role_totals.append((role, role_rss_mb))

        peak_cpu = max(s["cpu_percent"] for s in stats)
        peak_rss = max(s["rss_mb"] for s in stats)

        if peak_cpu > MAX_AGENT_CPU_PERCENT:
            key = f"resource:cpu:{role}"
            if should_run(key, cooldown):
                log(
                    f"Resource guard CPU breach role={role} peak_cpu={peak_cpu:.1f}% "
                    f"threshold={MAX_AGENT_CPU_PERCENT:.1f}% -> restarting"
                )
                restart_agent(role, scripts)
            continue

        if peak_rss > MAX_AGENT_RSS_MB:
            key = f"resource:rss:{role}"
            if should_run(key, cooldown):
                log(
                    f"Resource guard RSS breach role={role} peak_rss_mb={peak_rss:.1f} "
                    f"threshold_mb={MAX_AGENT_RSS_MB:.1f} -> restarting"
                )
                restart_agent(role, scripts)

    if total_agent_rss_mb > MAX_TOTAL_AGENT_RSS_MB and role_totals:
        worst_role, worst_rss = sorted(role_totals, key=lambda x: x[1], reverse=True)[0]
        key = "resource:total_rss"
        if should_run(key, cooldown):
            log(
                f"Resource guard total RSS breach total_mb={total_agent_rss_mb:.1f} "
                f"threshold_mb={MAX_TOTAL_AGENT_RSS_MB:.1f}; restarting_heaviest role={worst_role} rss_mb={worst_rss:.1f}"
            )
            restart_agent(worst_role, scripts)


def recover_stale_queues(scripts: dict[str, Path], cooldown: dict[str, float]) -> None:
    if not should_run("queue:recover_scan", cooldown):
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from core.db_connectors.db_manager import db_manager  # pylint: disable=import-outside-toplevel
        from core.task_queue import recover_stale_processing  # pylint: disable=import-outside-toplevel

        redis = db_manager.get_redis_client()
        total = 0
        for role in sorted(scripts.keys()):
            recovered = recover_stale_processing(
                redis_client=redis,
                role=role,
                stale_after_seconds=max(30, QUEUE_STALE_AFTER_SECONDS),
            )
            if recovered:
                total += recovered
                log(f"Recovered stale queue tasks role={role} count={recovered}")
        if total:
            log(f"Recovered stale queue tasks total={total}")
    except Exception as e:
        log(f"Queue stale-recovery scan failed: {e}")


def restart_agent(role: str, scripts: dict[str, Path]) -> bool:
    script = scripts.get(role)
    if not script or not script.exists():
        log(f"Cannot restart unknown role '{role}'")
        return False
    pids = list_role_pids(script)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            log(f"Sent SIGTERM to {role} pid={pid}")
        except Exception as e:
            log(f"Failed to stop {role} pid={pid}: {e}")
    time.sleep(1.0)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_file = LOG_DIR / f"{role}.log"
    with out_file.open("a", encoding="utf-8") as fh:
        subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(BASE_DIR),
            stdout=fh,
            stderr=fh,
            start_new_session=True,
        )
    log(f"Restarted agent role={role} script={script}")
    return True


def infer_role_from_path(path: Path) -> str | None:
    name = path.name
    if not name.endswith(".log"):
        return None
    role = name[:-4]
    if role == "api_server":
        return None
    return role


def should_run(action_key: str, cooldown: dict[str, float]) -> bool:
    now = time.time()
    last = cooldown.get(action_key, 0.0)
    if now - last < ACTION_COOLDOWN_SECONDS:
        return False
    cooldown[action_key] = now
    return True


def process_line(path: Path, line: str, scripts: dict[str, Path], cooldown: dict[str, float]) -> None:
    role = infer_role_from_path(path)

    if MCP_REDIS_TIMEOUT_PAT.search(line):
        key = "fix:mcp_redis"
        if should_run(key, cooldown):
            remediate_mcp_redis()
        return

    if ANTHROPIC_INVALID_PAT.search(line):
        if AUTO_DISABLE_LLM_PROVIDERS:
            key = "fix:disable_anthropic"
            if should_run(key, cooldown):
                set_disabled_provider("anthropic")
        else:
            log("Observed anthropic auth error; auto-disable is disabled (LOG_MONITOR_AUTO_DISABLE_LLM_PROVIDERS=false)")
        return

    if OPENAI_INVALID_PAT.search(line) or OPENAI_QUOTA_PAT.search(line):
        if AUTO_DISABLE_LLM_PROVIDERS:
            key = "fix:disable_openai"
            if should_run(key, cooldown):
                set_disabled_provider("openai")
        else:
            log("Observed openai auth/quota error; auto-disable is disabled (LOG_MONITOR_AUTO_DISABLE_LLM_PROVIDERS=false)")
        return

    if GEMINI_QUOTA_PAT.search(line):
        if AUTO_DISABLE_LLM_PROVIDERS:
            key = "fix:disable_gemini"
            if should_run(key, cooldown):
                set_disabled_provider("gemini")
        else:
            log("Observed gemini quota error; auto-disable is disabled (LOG_MONITOR_AUTO_DISABLE_LLM_PROVIDERS=false)")
        return

    m = TASK_NOT_IMPLEMENTED_PAT.search(line)
    if m:
        task_type = m.group(1)
        log(f"Observed unsupported task type '{task_type}' in {path.name}")
        if role and should_run(f"restart:{role}:unsupported_task", cooldown):
            restart_agent(role, scripts)
        return

    if ALL_LLM_FAILED_PAT.search(line):
        if RESTART_ON_ALL_LLM_FAILED and role and should_run(f"restart:{role}:llm_failed", cooldown):
            restart_agent(role, scripts)
        elif role:
            log(
                f"Observed ALL_LLM providers failed for role={role}; "
                "restart suppressed (LOG_MONITOR_RESTART_ON_ALL_LLM_FAILED=false)"
            )
        return

    # Avoid restart storms on recoverable runtime errors (LLM quota/auth/provider fallbacks,
    # unsupported task types, and task-level validation failures). Restart only on fatal loop errors.
    if FATAL_RESTART_PAT.search(line) and "apscheduler" not in line.lower():
        if role and should_run(f"restart:{role}:generic_error", cooldown):
            restart_agent(role, scripts)


def monitor_loop() -> None:
    scripts = load_agent_scripts()
    offsets: dict[Path, int] = {}
    cooldown: dict[str, float] = {}
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log("Log monitor started")
    log(f"Known agent roles for restart: {', '.join(sorted(scripts.keys()))}")
    process_check_last = 0.0
    queue_recovery_last = 0.0
    resource_check_last = 0.0
    monitor_started_at = time.time()

    # Start from EOF for existing files so we only react to fresh errors.
    for path in sorted(LOG_DIR.glob(SCAN_GLOB)):
        if path.name in {"monitor_actions.log", "log_monitor.log"} or not path.is_file():
            continue
        try:
            offsets[path] = path.stat().st_size
        except Exception:
            offsets[path] = 0

    while True:
        now = time.time()
        if (
            now - monitor_started_at >= max(0, PROCESS_HEALTHCHECK_STARTUP_GRACE_SECONDS)
            and now - process_check_last >= max(5, PROCESS_HEALTHCHECK_INTERVAL_SECONDS)
        ):
            for role in sorted(scripts.keys()):
                ensure_role_running(role, scripts, cooldown)
            process_check_last = now

        if now - queue_recovery_last >= max(10, QUEUE_RECOVERY_INTERVAL_SECONDS):
            recover_stale_queues(scripts, cooldown)
            queue_recovery_last = now

        if now - resource_check_last >= max(10, RESOURCE_CHECK_INTERVAL_SECONDS):
            guard_resource_usage(scripts, cooldown)
            resource_check_last = now

        for path in sorted(LOG_DIR.glob(SCAN_GLOB)):
            if path.name in {"monitor_actions.log", "log_monitor.log"}:
                continue
            if not path.is_file():
                continue

            previous = offsets.get(path, 0)
            try:
                size = path.stat().st_size
            except Exception:
                continue
            if size < previous:
                previous = 0
            if size == previous:
                offsets[path] = previous
                continue

            try:
                with path.open("r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(previous)
                    for raw in fh:
                        process_line(path, raw.rstrip("\n"), scripts, cooldown)
                    offsets[path] = fh.tell()
            except Exception as e:
                log(f"Failed reading {path}: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        monitor_loop()
    except KeyboardInterrupt:
        log("Log monitor stopped by keyboard interrupt")
