from agents.skill_agent.agent import SkillAgent


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.expiry = {}

    def setnx(self, key, value):
        if key in self.kv:
            return 0
        self.kv[key] = value
        return 1

    def expire(self, key, ttl):
        self.expiry[key] = ttl


def _skill_agent_stub() -> SkillAgent:
    agent = SkillAgent.__new__(SkillAgent)
    agent.AGENT_ROLE = "skill_agent"
    agent.agent_id = "test-agent"
    agent.redis_client = FakeRedis()
    agent.log_execution = lambda *a, **k: None
    agent.publish_task_to_agent = lambda *a, **k: "task-id"
    return agent


def test_low_signal_content_is_not_dispatched():
    agent = _skill_agent_stub()
    result = agent._dispatch_training_payload_with_guards(
        task_data={"task": {"type": "fetch_best_practices"}},
        target_agent="seo_agent",
        source="LLM Research on topic",
        knowledge_content="I cannot fulfill this request. Do not hallucinate data. Please provide source material.",
    )
    assert result["status"] == "warning"
    assert result["reason"] == "low_signal_content"


def test_duplicate_dispatch_is_suppressed():
    agent = _skill_agent_stub()
    payload = dict(
        task_data={"task": {"type": "fetch_best_practices"}},
        target_agent="seo_agent",
        source="LLM Research on topic",
        knowledge_content="Use canonical tags. Fix internal linking. Validate index coverage.",
    )
    first = agent._dispatch_training_payload_with_guards(**payload)
    second = agent._dispatch_training_payload_with_guards(**payload)
    assert first["status"] == "success"
    assert second["status"] == "warning"
    assert second["reason"] == "duplicate_dispatch"


def test_rate_limit_blocks_burst_for_same_source_target():
    agent = _skill_agent_stub()
    first = agent._dispatch_training_payload_with_guards(
        task_data={"task": {"type": "fetch_best_practices"}},
        target_agent="seo_agent",
        source="LLM Research on topic",
        knowledge_content="Use canonical tags and improve crawl depth.",
    )
    second = agent._dispatch_training_payload_with_guards(
        task_data={"task": {"type": "fetch_best_practices"}},
        target_agent="seo_agent",
        source="LLM Research on topic",
        knowledge_content="Prioritize internal links for money pages and monitor coverage weekly.",
    )
    assert first["status"] == "success"
    assert second["status"] == "warning"
    assert second["reason"] == "rate_limited"


def test_fetch_best_practices_uses_fallback_when_llm_returns_low_signal(monkeypatch):
    agent = _skill_agent_stub()
    captured = {}

    def _fake_dispatch(task_data, target_agent, source, knowledge_content):
        captured["target_agent"] = target_agent
        captured["content"] = knowledge_content
        return {"status": "success", "target_agent": target_agent}

    agent.execute_llm = lambda *a, **k: (
        "I cannot fulfill this request. Do not hallucinate data. Please provide source material."
    )
    agent._dispatch_training_payload_with_guards = _fake_dispatch

    result = agent._fetch_best_practices(
        {"task": {"type": "fetch_best_practices", "topic": "seo for ecommerce", "target_agent": "seo_agent"}}
    )
    assert result["status"] == "success"
    assert captured["target_agent"] == "seo_agent"
    assert "Fallback training package for seo_agent" in captured["content"]
