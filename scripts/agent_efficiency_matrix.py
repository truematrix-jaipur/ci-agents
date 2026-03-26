#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.db_connectors.db_manager import db_manager
from core.analytics.efficiency_matrix import build_agent_efficiency_matrix


def main():
    parser = argparse.ArgumentParser(description="Generate agent efficiency & execution tracking matrix.")
    parser.add_argument("--limit", type=int, default=1000, help="Number of recent log rows to inspect.")
    parser.add_argument("--hours", type=int, default=None, help="Optional rolling window in hours.")
    parser.add_argument("--output", type=str, default="", help="Optional output JSON path.")
    args = parser.parse_args()

    matrix = build_agent_efficiency_matrix(
        redis_client=db_manager.get_redis_client(),
        limit=args.limit,
        hours=args.hours,
    )
    rendered = json.dumps(matrix, indent=2, ensure_ascii=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote matrix report to {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
