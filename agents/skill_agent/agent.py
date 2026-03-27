import sys
import os
import logging
import json
import hashlib
from typing import Any
from pathlib import Path

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from core.agent_catalog import AgentSpec, get_agent_spec, get_agent_specs

logger = logging.getLogger(__name__)

class SkillAgent(BaseAgent):
    AGENT_ROLE = "skill_agent"
    SYSTEM_PROMPT = """You are the Knowledge & Skill Acquisition Agent.
    Your mission is to continuously find the best documentations, processes, 
    and best practices from the internet or other AI models.
    
    You process this raw knowledge into structured data and send it to the 
    Training Agent to enhance the capabilities of other agents in the swarm."""

    def handle_task(self, task_data):
        logger.info(f"Skill Agent {self.agent_id} handling task: {task_data}")
        payload = self._extract_task_payload(task_data)
        task_type = payload.get("type")

        if task_type == "fetch_best_practices":
            return self._execute_with_goal_target(task_data, self._fetch_best_practices, "fetch_best_practices")
        elif task_type == "fetch_documentation":
            return self._execute_with_goal_target(task_data, self._fetch_documentation, "fetch_documentation")
        elif task_type == "create_agent_skill":
            return self._execute_with_goal_target(task_data, self._create_agent_skill, "create_agent_skill")
        elif task_type == "bootstrap_agent_skills":
            return self._execute_with_goal_target(task_data, self._bootstrap_agent_skills, "bootstrap_agent_skills")
        elif task_type == "train_mcp_autonomy":
            return self._train_mcp_autonomy(task_data)
        elif task_type == "bootstrap_mcp_autonomy":
            return self._bootstrap_mcp_autonomy(task_data)
        else:
            return super().handle_task(task_data)

    def _normalize_target_agent(self, target_agent: str | None) -> str:
        if not target_agent:
            return "integration_agent"
        return "integration_agent" if target_agent == "integrator_agent" else target_agent

    def _is_low_signal_training_content(self, content: str) -> bool:
        text = (content or "").strip().lower()
        if not text:
            return True
        refusal_markers = (
            "cannot fulfill this request",
            "unable to provide",
            "do not hallucinate data",
            "no context",
            "no source material",
            "please provide",
            "without violating my critical instruction",
        )
        hits = sum(1 for marker in refusal_markers if marker in text)
        return hits >= 2

    def _is_duplicate_training_dispatch(self, target_agent: str, source: str, content: str) -> bool:
        redis_client = getattr(self, "redis_client", None)
        if redis_client is None:
            return False
        digest = hashlib.sha256(
            f"{target_agent}|{source}|{(content or '').strip().lower()}".encode("utf-8")
        ).hexdigest()
        key = f"skill_dispatch_dedupe:{target_agent}:{digest}"
        ttl_seconds = max(60, int(os.getenv("SKILL_DISPATCH_DEDUPE_TTL_SECONDS", "21600")))
        try:
            created = redis_client.setnx(key, "1")
            if created:
                redis_client.expire(key, ttl_seconds)
                return False
            return True
        except Exception:
            return False

    def _is_rate_limited_training_dispatch(self, target_agent: str, source: str) -> bool:
        redis_client = getattr(self, "redis_client", None)
        if redis_client is None:
            return False
        digest = hashlib.sha256(f"{target_agent}|{source}".encode("utf-8")).hexdigest()
        key = f"skill_dispatch_ratelimit:{digest}"
        ttl_seconds = max(30, int(os.getenv("SKILL_DISPATCH_RATE_LIMIT_SECONDS", "900")))
        try:
            created = redis_client.setnx(key, "1")
            if created:
                redis_client.expire(key, ttl_seconds)
                return False
            return True
        except Exception:
            return False

    def _dispatch_training_payload_with_guards(self, task_data, target_agent: str, source: str, knowledge_content: str):
        if self._is_low_signal_training_content(knowledge_content):
            return {
                "status": "warning",
                "message": "Skipped low-signal training output from research/documentation fetch.",
                "target_agent": target_agent,
                "skipped": True,
                "reason": "low_signal_content",
            }
        if self._is_duplicate_training_dispatch(target_agent=target_agent, source=source, content=knowledge_content):
            return {
                "status": "warning",
                "message": "Skipped duplicate training dispatch.",
                "target_agent": target_agent,
                "skipped": True,
                "reason": "duplicate_dispatch",
            }
        if self._is_rate_limited_training_dispatch(target_agent=target_agent, source=source):
            return {
                "status": "warning",
                "message": "Skipped training dispatch due to rate limit guard.",
                "target_agent": target_agent,
                "skipped": True,
                "reason": "rate_limited",
            }

        training_payload = {
            "type": "train_agent",
            "target_agent": target_agent,
            "knowledge_content": knowledge_content,
            "source": source,
        }
        task_id = self.publish_task_to_agent("training_agent", training_payload)
        self.log_execution(
            task=task_data,
            thought_process=f"Validated training content for {target_agent} before dispatch.",
            action_taken=f"Dispatched training payload to training_agent ({task_id}).",
        )
        return {
            "status": "success",
            "message": f"Knowledge sent to Training Agent for {target_agent}.",
            "target_agent": target_agent,
            "dispatched_task_id": task_id,
        }

    def _build_fallback_best_practice_content(self, topic: str, target_agent: str) -> str:
        spec = get_agent_spec(target_agent)
        if spec:
            caps = ", ".join(spec.capabilities[:6]) if spec.capabilities else "general_operations"
            role_summary = f"{spec.role} capabilities: {caps}"
        else:
            role_summary = f"Role: {target_agent}"
        tool_lines = []
        if spec:
            declared_tools = list(spec.required_mcps or ()) + list(spec.required_binaries or ())
            tool_lines = [f"- {tool}" for tool in declared_tools[:10]]
        if not tool_lines:
            tool_lines = ["- Use configured MCP tools and CLI fallback utilities with explicit verification."]

        normalized_topic = (topic or "operational excellence").strip()
        return (
            f"Fallback training package for {target_agent}\n"
            f"Topic: {normalized_topic}\n\n"
            "Execution principles:\n"
            "- Never assume state; fetch and verify data before any action.\n"
            "- Prefer reversible changes and include post-change validation.\n"
            "- For permission/API failures, return explicit remediation with required access and exact scope.\n"
            "- For missing tables/endpoints, add preflight checks and degrade gracefully.\n"
            "- Record measurable outcomes (traffic, conversion, error-rate, latency, cost) for every change cycle.\n\n"
            "Role alignment:\n"
            f"- {role_summary}\n\n"
            "Primary tools in current environment:\n"
            + "\n".join(tool_lines)
            + "\n\nTraining objective:\n"
            "- Improve execution quality, efficiency, and accuracy using verified runtime data.\n"
        )

    def _fetch_best_practices(self, task_data):
        payload = self._extract_task_payload(task_data)
        topic = payload.get("topic")
        target_agent = self._normalize_target_agent(payload.get("target_agent"))
        
        # Use LLM to simulate web browsing and best practice extraction
        prompt = f"Research and provide a detailed list of current best practices for: {topic}. Output as structured technical guidelines."
        try:
            knowledge_content = self.execute_llm(prompt, provider="gemini", use_knowledge=False)
        except Exception as e:
            logger.warning(f"Best-practices LLM execution failed for {target_agent}: {e}")
            knowledge_content = self._build_fallback_best_practice_content(
                topic=str(topic or ""),
                target_agent=target_agent,
            )

        if self._is_low_signal_training_content(knowledge_content):
            logger.warning(
                f"Low-signal best-practices response for {target_agent}; using deterministic fallback package."
            )
            knowledge_content = self._build_fallback_best_practice_content(
                topic=str(topic or ""),
                target_agent=target_agent,
            )
        
        result = self._dispatch_training_payload_with_guards(
            task_data=task_data,
            target_agent=target_agent,
            source=f"LLM Research on {topic}",
            knowledge_content=knowledge_content,
        )
        if result.get("status") == "success":
            self.log_execution(
                task=task_data,
                thought_process=f"Researched best practices for {topic} using Gemini.",
                action_taken=f"Prepared and dispatched training data for {target_agent}.",
            )
        return result

    def _fetch_documentation(self, task_data):
        payload = self._extract_task_payload(task_data)
        tool_name = payload.get("tool")
        target_agent = self._normalize_target_agent(payload.get("target_agent"))
        
        prompt = f"Provide a comprehensive technical guide and command reference for: {tool_name}. Include common troubleshooting steps."
        knowledge_content = self.execute_llm(prompt, provider="anthropic", use_knowledge=False)
        
        return self._dispatch_training_payload_with_guards(
            task_data=task_data,
            target_agent=target_agent,
            source=f"Documentation fetch for {tool_name}",
            knowledge_content=knowledge_content,
        )

    def _create_agent_skill(self, task_data):
        task = self._extract_task_payload(task_data)
        target_agent = self._normalize_target_agent(task.get("target_agent"))
        sync_train = bool(task.get("sync_train", True))
        if not target_agent:
            return {"status": "error", "message": "target_agent is required"}

        spec = get_agent_spec(target_agent)
        if not spec:
            return {"status": "error", "message": f"Unknown target_agent: {target_agent}"}

        skill_pack = self._build_skill_pack(spec)
        training_payload = {
            "type": "train_agent",
            "target_agent": target_agent,
            "knowledge_content": skill_pack,
            "source": f"SkillAgent structured skill pack for {target_agent}",
        }

        if sync_train:
            from agents.training_agent.agent import TrainingAgent

            training_result = self.spawn_subagent(TrainingAgent, {"task": training_payload})
            ok = isinstance(training_result, dict) and training_result.get("status") == "success"
            self.log_execution(
                task=task_data,
                thought_process=f"Built structured skill pack for {target_agent}.",
                action_taken=f"Synchronous training {'succeeded' if ok else 'returned non-success'} for {target_agent}.",
                status="success" if ok else "warning",
            )
            return {
                "status": "success" if ok else "error",
                "target_agent": target_agent,
                "sync_train": True,
                "training_result": training_result,
                "skill_pack_preview": skill_pack[:500],
            }

        dispatched_task_id = self.publish_task_to_agent("training_agent", training_payload)
        self.log_execution(
            task=task_data,
            thought_process=f"Built structured skill pack for {target_agent}.",
            action_taken=f"Dispatched training payload to training_agent ({dispatched_task_id}).",
        )
        return {
            "status": "success",
            "target_agent": target_agent,
            "sync_train": False,
            "dispatched_task_id": dispatched_task_id,
            "skill_pack_preview": skill_pack[:500],
        }

    def _bootstrap_agent_skills(self, task_data):
        task = self._extract_task_payload(task_data)
        include_deprecated = bool(task.get("include_deprecated", False))
        sync_train = bool(task.get("sync_train", True))
        explicit_agents = task.get("agents") or []

        if explicit_agents and not isinstance(explicit_agents, list):
            return {"status": "error", "message": "agents must be a list when provided"}

        target_agents = explicit_agents or [s.role for s in get_agent_specs(include_deprecated=include_deprecated)]
        results: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0

        for role in target_agents:
            result = self._create_agent_skill(
                {"task": {"type": "create_agent_skill", "target_agent": role, "sync_train": sync_train}}
            )
            entry = {
                "target_agent": role,
                "status": result.get("status"),
                "message": result.get("message"),
            }
            if "training_result" in result:
                entry["training_result_status"] = (
                    result.get("training_result", {}).get("status")
                    if isinstance(result.get("training_result"), dict)
                    else "unknown"
                )
            if "dispatched_task_id" in result:
                entry["dispatched_task_id"] = result.get("dispatched_task_id")
            results.append(entry)
            if result.get("status") == "success":
                success_count += 1
            else:
                failure_count += 1

        self.log_execution(
            task=task_data,
            thought_process="Generated role-aligned skill packs and trained agents sequentially.",
            action_taken=f"Completed bootstrap for {len(target_agents)} agents: success={success_count}, failed={failure_count}.",
            status="success" if failure_count == 0 else "warning",
        )
        return {
            "status": "success" if failure_count == 0 else "warning",
            "sync_train": sync_train,
            "include_deprecated": include_deprecated,
            "summary": {
                "total_agents": len(target_agents),
                "success_count": success_count,
                "failure_count": failure_count,
            },
            "results": results,
        }

    def _train_mcp_autonomy(self, task_data):
        task = self._extract_task_payload(task_data)
        target_agent = self._normalize_target_agent(task.get("target_agent"))
        sync_train = bool(task.get("sync_train", True))

        spec = get_agent_spec(target_agent)
        if not spec:
            return {"status": "error", "message": f"Unknown target_agent: {target_agent}"}

        playbook_path = Path(__file__).resolve().parents[2] / "docs" / "training" / "mcp_autonomy_playbook.md"
        if not playbook_path.exists():
            return {"status": "error", "message": f"Missing MCP autonomy playbook: {playbook_path}"}

        mcp_content = playbook_path.read_text(encoding="utf-8")
        combined = (
            self._build_skill_pack(spec)
            + "\n\n"
            + mcp_content
        )

        training_payload = {
            "type": "train_agent",
            "target_agent": target_agent,
            "knowledge_content": combined,
            "source": f"MCP autonomy playbook for {target_agent}",
        }

        if sync_train:
            from agents.training_agent.agent import TrainingAgent

            training_result = self.spawn_subagent(TrainingAgent, {"task": training_payload})
            ok = isinstance(training_result, dict) and training_result.get("status") == "success"
            self.log_execution(
                task=task_data,
                thought_process=f"Built MCP autonomy training pack for {target_agent}.",
                action_taken=f"Synchronous MCP autonomy training {'succeeded' if ok else 'returned non-success'} for {target_agent}.",
                status="success" if ok else "warning",
            )
            return {
                "status": "success" if ok else "error",
                "target_agent": target_agent,
                "sync_train": True,
                "training_result": training_result,
            }

        dispatched_task_id = self.publish_task_to_agent("training_agent", training_payload)
        self.log_execution(
            task=task_data,
            thought_process=f"Built MCP autonomy training pack for {target_agent}.",
            action_taken=f"Dispatched MCP autonomy payload to training_agent ({dispatched_task_id}).",
        )
        return {
            "status": "success",
            "target_agent": target_agent,
            "sync_train": False,
            "dispatched_task_id": dispatched_task_id,
        }

    def _bootstrap_mcp_autonomy(self, task_data):
        task = self._extract_task_payload(task_data)
        include_deprecated = bool(task.get("include_deprecated", False))
        sync_train = bool(task.get("sync_train", True))
        explicit_agents = task.get("agents") or []

        if explicit_agents and not isinstance(explicit_agents, list):
            return {"status": "error", "message": "agents must be a list when provided"}

        target_agents = [self._normalize_target_agent(a) for a in explicit_agents] if explicit_agents else [
            s.role for s in get_agent_specs(include_deprecated=include_deprecated) if s.required_mcps
        ]

        results: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0

        for role in target_agents:
            result = self._train_mcp_autonomy(
                {"task": {"type": "train_mcp_autonomy", "target_agent": role, "sync_train": sync_train}}
            )
            results.append(
                {
                    "target_agent": role,
                    "status": result.get("status"),
                    "message": result.get("message"),
                    "training_result_status": (
                        result.get("training_result", {}).get("status")
                        if isinstance(result.get("training_result"), dict)
                        else None
                    ),
                }
            )
            if result.get("status") == "success":
                success_count += 1
            else:
                failure_count += 1

        self.log_execution(
            task=task_data,
            thought_process="Applied MCP autonomy playbook training to MCP-dependent agents.",
            action_taken=f"Completed MCP autonomy bootstrap for {len(target_agents)} agents: success={success_count}, failed={failure_count}.",
            status="success" if failure_count == 0 else "warning",
        )
        return {
            "status": "success" if failure_count == 0 else "warning",
            "sync_train": sync_train,
            "include_deprecated": include_deprecated,
            "summary": {
                "total_agents": len(target_agents),
                "success_count": success_count,
                "failure_count": failure_count,
            },
            "results": results,
        }

    def _build_skill_pack(self, spec: AgentSpec) -> str:
        capability_lines = "\n".join(f"- {c}" for c in spec.capabilities) or "- (none declared)"
        mcp_lines = "\n".join(f"- {m}" for m in spec.required_mcps) or "- (none required)"
        env_lines = "\n".join(f"- {e}" for e in spec.required_env) or "- (none required)"
        bin_lines = "\n".join(f"- {b}" for b in spec.required_binaries) or "- (none required)"
        permission_lines = "\n".join(f"- {p}" for p in spec.permission_profile) or "- (none declared)"

        return f"""# Skill Pack: {spec.role}

## Role Objective
Operate as `{spec.role}` with strict anti-hallucination behavior, only using verified data and declared tools.

## Core Capabilities
{capability_lines}

## Required MCP Tools
{mcp_lines}

## Required Environment Variables
{env_lines}

## Required Binaries
{bin_lines}

## Permission Profile
{permission_lines}

## Tool Responsibility Checklist
1. Validate required MCP connectivity before execution.
2. Validate required env variables and binaries before execution.
3. Prefer canonical owner agents for cross-domain operations.
4. Log every delegation and external side effect with task IDs.
5. Return explicit error details when a required tool/permission is unavailable.

## Implementation Guardrails
- Never fabricate external results.
- Use parameterized DB/API operations where applicable.
- Keep operations reversible and observable through logs.
"""

if __name__ == "__main__":
    agent = SkillAgent()
    agent.run()
