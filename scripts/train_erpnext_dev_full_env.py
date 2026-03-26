#!/usr/bin/env python3
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.training_agent.agent import TrainingAgent
from core.agent_catalog import get_agent_spec


def main() -> int:
    target_agent = "erpnext_dev_agent"
    spec = get_agent_spec(target_agent)
    if not spec:
        print(json.dumps({"status": "error", "message": f"Unknown target agent: {target_agent}"}))
        return 1

    playbook_path = PROJECT_ROOT / "docs" / "training" / "erpnext_dev_full_env_playbook.md"
    overview_path = PROJECT_ROOT / "docs" / "training" / "erpnext_current_state_overview.md"
    if not playbook_path.exists():
        print(json.dumps({"status": "error", "message": f"Missing playbook: {playbook_path}"}))
        return 1

    capability_lines = "\n".join(f"- {c}" for c in spec.capabilities) or "- (none declared)"
    mcp_lines = "\n".join(f"- {m}" for m in spec.required_mcps) or "- (none required)"
    env_lines = "\n".join(f"- {e}" for e in spec.required_env) or "- (none required)"
    bin_lines = "\n".join(f"- {b}" for b in spec.required_binaries) or "- (none required)"

    skill_pack = f"""# Skill Pack: {spec.role}

## Core Capabilities
{capability_lines}

## Required MCP Tools
{mcp_lines}

## Required Environment Variables
{env_lines}

## Required Binaries
{bin_lines}
"""

    knowledge = skill_pack + "\n\n" + playbook_path.read_text(encoding="utf-8")
    if overview_path.exists():
        knowledge += "\n\n" + overview_path.read_text(encoding="utf-8")

    agent = TrainingAgent()
    result = agent.handle_task(
        {
            "task": {
                "type": "train_agent",
                "target_agent": target_agent,
                "knowledge_content": knowledge,
                "source": "ERPNext full environment lifecycle playbook",
            }
        }
    )
    out_path = PROJECT_ROOT / "logs" / "erpnext_dev_full_env_training.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    print(f"report_written={out_path}")
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
