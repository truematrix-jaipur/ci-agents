import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(v)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    except Exception:
        return None


def _safe_json_loads(value: str) -> dict[str, Any] | None:
    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _efficiency_score(success_rate: float, error_rate: float, throughput_per_hour: float) -> float:
    # Weighted score: reliability first, then activity.
    activity_component = min(throughput_per_hour / 20.0, 1.0) * 100.0
    score = (success_rate * 0.6) + ((1.0 - error_rate) * 0.3) + ((activity_component / 100.0) * 0.1)
    return round(max(0.0, min(1.0, score)) * 100.0, 2)


def build_agent_efficiency_matrix(
    redis_client,
    limit: int = 1000,
    hours: int | None = None,
) -> dict[str, Any]:
    raw = redis_client.lrange("global_execution_log", 0, max(0, limit - 1))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours) if hours else None

    entries: list[dict[str, Any]] = []
    for line in raw:
        parsed = _safe_json_loads(line)
        if not parsed:
            continue
        ts = _parse_ts(parsed.get("timestamp"))
        if cutoff and ts and ts < cutoff:
            continue
        parsed["_ts"] = ts
        entries.append(parsed)

    by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        role = e.get("agent_role") or "unknown"
        by_agent[role].append(e)

    matrix: list[dict[str, Any]] = []
    for role, rows in by_agent.items():
        statuses = [str(r.get("status", "")).lower() for r in rows]
        total = len(rows)
        success = sum(1 for s in statuses if s == "success")
        errors = sum(1 for s in statuses if s == "error")
        warnings = sum(1 for s in statuses if s == "warning")
        infos = sum(1 for s in statuses if s == "info")

        success_rate = (success / total) if total else 0.0
        error_rate = (errors / total) if total else 0.0

        task_types = set()
        delegation_count = 0
        timestamps: list[datetime] = []
        for r in rows:
            task = r.get("task", {})
            if isinstance(task, dict):
                nested_task = task.get("task", {})
                if isinstance(nested_task, dict):
                    t = nested_task.get("type")
                    if t:
                        task_types.add(str(t))
            action_taken = str(r.get("action_taken", ""))
            if "Published task to" in action_taken:
                delegation_count += 1
            if r.get("_ts"):
                timestamps.append(r["_ts"])

        timestamps.sort()
        span_hours = 0.0
        avg_inter_event_seconds = None
        throughput_per_hour = 0.0
        if len(timestamps) >= 2:
            span = timestamps[-1] - timestamps[0]
            span_hours = max(span.total_seconds() / 3600.0, 1e-6)
            deltas = [
                (timestamps[i] - timestamps[i - 1]).total_seconds()
                for i in range(1, len(timestamps))
            ]
            avg_inter_event_seconds = round(mean(deltas), 2) if deltas else None
            throughput_per_hour = round(total / span_hours, 2)
        elif len(timestamps) == 1:
            throughput_per_hour = float(total)

        efficiency_score = _efficiency_score(success_rate, error_rate, throughput_per_hour)

        health = "healthy"
        if error_rate >= 0.2:
            health = "critical"
        elif error_rate >= 0.1 or warnings > success:
            health = "warning"

        matrix.append(
            {
                "agent_role": role,
                "total_executions": total,
                "success_count": success,
                "error_count": errors,
                "warning_count": warnings,
                "info_count": infos,
                "success_rate": round(success_rate, 4),
                "error_rate": round(error_rate, 4),
                "throughput_per_hour": throughput_per_hour,
                "avg_inter_event_seconds": avg_inter_event_seconds,
                "delegation_count": delegation_count,
                "unique_task_types": sorted(task_types),
                "efficiency_score": efficiency_score,
                "health": health,
            }
        )

    matrix.sort(key=lambda x: x["efficiency_score"], reverse=True)

    global_total = sum(a["total_executions"] for a in matrix)
    global_errors = sum(a["error_count"] for a in matrix)
    global_success = sum(a["success_count"] for a in matrix)
    global_success_rate = (global_success / global_total) if global_total else 0.0
    global_error_rate = (global_errors / global_total) if global_total else 0.0

    recommendations: list[str] = []
    for row in matrix:
        if row["health"] == "critical":
            recommendations.append(
                f"{row['agent_role']}: high error rate ({row['error_rate']:.0%}) — investigate dependencies and retry policy."
            )
        elif row["health"] == "warning":
            recommendations.append(
                f"{row['agent_role']}: warning health — improve task validation and execution guardrails."
            )

    return {
        "generated_at_utc": now.isoformat(),
        "window_hours": hours,
        "limit": limit,
        "global": {
            "total_executions": global_total,
            "global_success_rate": round(global_success_rate, 4),
            "global_error_rate": round(global_error_rate, 4),
            "tracked_agents": len(matrix),
        },
        "matrix": matrix,
        "recommendations": recommendations[:20],
    }
