#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.skill_agent.agent import SkillAgent


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and train agent skill packs one-by-one via SkillAgent.")
    parser.add_argument("--agent", action="append", default=[], help="Target agent role (can be repeated).")
    parser.add_argument("--include-deprecated", action="store_true", help="Include deprecated roles from catalog.")
    parser.add_argument("--async-dispatch", action="store_true", help="Dispatch to training_agent queue instead of sync training.")
    parser.add_argument("--output", default="/home/agents/logs/agent_skill_bootstrap_report.json", help="Output report path.")
    args = parser.parse_args()

    skill_agent = SkillAgent()
    task = {
        "task": {
            "type": "bootstrap_agent_skills",
            "agents": args.agent,
            "include_deprecated": args.include_deprecated,
            "sync_train": not args.async_dispatch,
        }
    }
    result = skill_agent.handle_task(task)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result.get("summary", {}), indent=2))
    print(f"report_written={out_path}")
    return 0 if result.get("status") in {"success", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
