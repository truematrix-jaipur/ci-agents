import subprocess
from typing import Any


def check_container_status() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}: {{.Status}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        containers = [line for line in result.stdout.strip().split("\n") if line.strip()]
        return {"status": "success", "containers": containers}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_system_metrics() -> dict[str, Any]:
    try:
        load = subprocess.run(["uptime"], capture_output=True, text=True, timeout=10).stdout.strip()
        mem = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=10).stdout.strip()
        return {"status": "success", "load": load, "memory": mem}
    except Exception as e:
        return {"status": "error", "message": str(e)}
