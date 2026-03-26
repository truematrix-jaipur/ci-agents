import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.analytics.efficiency_matrix import build_agent_efficiency_matrix


class FakeRedis:
    def __init__(self, rows):
        self._rows = rows

    def lrange(self, key, start, end):
        assert key == "global_execution_log"
        return self._rows[start : end + 1]


def _row(ts, role, status, task_type):
    return json.dumps(
        {
            "timestamp": ts,
            "agent_role": role,
            "status": status,
            "task": {"task": {"type": task_type}},
            "action_taken": "ok",
        }
    )


def test_build_agent_efficiency_matrix_basic():
    redis_rows = [
        _row("2026-03-26T00:00:00+00:00", "seo_agent", "success", "status"),
        _row("2026-03-26T00:10:00+00:00", "seo_agent", "error", "run_pipeline"),
        _row("2026-03-26T00:15:00+00:00", "server_agent", "success", "routine_audit"),
    ]
    report = build_agent_efficiency_matrix(FakeRedis(redis_rows), limit=100)
    assert "global" in report
    assert report["global"]["tracked_agents"] == 2
    assert report["global"]["total_executions"] == 3
    seo = next(r for r in report["matrix"] if r["agent_role"] == "seo_agent")
    assert seo["total_executions"] == 2
    assert seo["error_count"] == 1
    assert "status" in seo["unique_task_types"]


def test_build_agent_efficiency_matrix_handles_bad_rows():
    redis_rows = ["not-json", json.dumps({"agent_role": "seo_agent"})]
    report = build_agent_efficiency_matrix(FakeRedis(redis_rows), limit=10)
    assert report["global"]["tracked_agents"] >= 1
