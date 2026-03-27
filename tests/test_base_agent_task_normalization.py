from core.base_agent import BaseAgent


def _agent_stub() -> BaseAgent:
    agent = BaseAgent.__new__(BaseAgent)
    agent.AGENT_ROLE = "unit_test_agent"
    agent.agent_id = "unit-agent-id"
    agent.role_channel = "task_queue_unit_test_agent"
    agent.specific_channel = "agent_unit_test_agent_unit-agent-id"
    agent.speak = lambda *a, **k: None
    agent.execute_llm = lambda *a, **k: "ok"
    agent.autonomous_idle_enabled = True
    agent.autonomous_idle_interval_seconds = 60
    agent._last_idle_dispatch_ts = 0.0
    agent.task_summary_min_interval_seconds = 0
    agent.task_summary_max_per_hour = 100
    agent.task_summary_dedupe_seconds = 60
    return agent


def test_handle_task_malformed_task_payload_does_not_raise():
    agent = _agent_stub()
    res = agent.handle_task({"task": "MALFORMED_TASK_PAYLOAD"})
    assert res["status"] == "warning"
    assert res["task_type"] == "malformed_task_payload"


def test_handle_manual_command_malformed_payload_returns_error():
    agent = _agent_stub()
    res = agent._handle_manual_command({"task": "MALFORMED_TASK_PAYLOAD"})
    assert res["status"] == "error"
    assert "No command provided" in res["message"]


def test_normalize_task_envelope_wraps_non_dict_task_payload():
    agent = _agent_stub()
    out = agent._normalize_task_envelope({"task_id": "t1", "task": "MALFORMED_TASK_PAYLOAD"})
    assert out["task"]["type"] == "malformed_task_payload"
    assert out["task"]["raw_task"] == "MALFORMED_TASK_PAYLOAD"


def test_handle_task_training_update_received_acknowledged():
    agent = _agent_stub()
    res = agent.handle_task({"task": {"type": "training_update_received", "source": "unit-test"}})
    assert res["status"] == "success"
    assert res["ignored"] is True
    assert res["task_type"] == "training_update_received"


def test_handle_task_autonomous_self_check():
    agent = _agent_stub()
    res = agent.handle_task({"task": {"type": "autonomous_self_check"}})
    assert res["status"] == "success"
    assert res["verification"]["role"] == "unit_test_agent"


def test_build_idle_autonomous_task_for_growth_agent():
    agent = _agent_stub()
    agent.AGENT_ROLE = "growth_agent"
    task = agent._build_idle_autonomous_task()
    assert task["type"] == "plan_quarterly_growth"
    assert task["execution_mode"] == "async"
    assert task["require_verification"] is True


def test_maybe_dispatch_idle_autonomous_task_respects_interval(monkeypatch):
    agent = _agent_stub()
    dispatched = []

    def _capture_publish(role, payload):
        dispatched.append((role, payload))
        return "task-1"

    monkeypatch.setattr(agent, "publish_task_to_agent", _capture_publish, raising=False)
    monkeypatch.setattr(
        agent,
        "_build_idle_autonomous_task",
        lambda: {"type": "autonomous_self_check", "require_verification": True},
        raising=False,
    )

    agent._maybe_dispatch_idle_autonomous_task(now=100.0)
    agent._maybe_dispatch_idle_autonomous_task(now=120.0)
    agent._maybe_dispatch_idle_autonomous_task(now=170.0)

    assert len(dispatched) == 2
    assert dispatched[0][0] == "unit_test_agent"


def test_maybe_dispatch_idle_autonomous_task_uses_redis_cooldown(monkeypatch):
    class _Redis:
        def __init__(self):
            self.store = {}

        def get(self, key):
            return self.store.get(key)

        def set(self, key, value, ex=None):
            self.store[key] = value
            return True

    agent = _agent_stub()
    agent.redis_client = _Redis()
    dispatched = []

    def _capture_publish(role, payload):
        dispatched.append((role, payload))
        return "task-1"

    monkeypatch.setattr(agent, "publish_task_to_agent", _capture_publish, raising=False)
    monkeypatch.setattr(
        agent,
        "_build_idle_autonomous_task",
        lambda: {"type": "autonomous_self_check", "require_verification": True},
        raising=False,
    )

    agent._maybe_dispatch_idle_autonomous_task(now=100.0)
    agent._maybe_dispatch_idle_autonomous_task(now=110.0)
    agent._maybe_dispatch_idle_autonomous_task(now=200.0)

    assert len(dispatched) == 2


def test_build_idle_autonomous_task_wordpress_requires_verified_root(monkeypatch):
    agent = _agent_stub()
    agent.AGENT_ROLE = "wordpress_tech"
    monkeypatch.setenv("WP_ROOT", "/nonexistent/wp")
    task = agent._build_idle_autonomous_task()
    assert task["type"] == "autonomous_self_check"


def test_maybe_email_task_summary_dispatches_to_email_agent(monkeypatch):
    agent = _agent_stub()
    agent.task_summary_email_enabled = True
    agent.task_summary_email_exclude_roles = {"email_marketing_agent"}
    published = {}

    def _capture_publish(role, payload):
        published["role"] = role
        published["payload"] = payload
        return "mail-task-1"

    monkeypatch.setattr(agent, "publish_task_to_agent", _capture_publish, raising=False)
    ctx = {"task_id": "t-123", "source_agent": "api_gateway", "task": {"type": "status"}}
    agent._maybe_email_task_summary(
        task_context=ctx,
        task_type="status",
        status="completed",
        result={"status": "success", "message": "ok"},
        error_text=None,
    )
    assert published["role"] == "email_marketing_agent"
    assert published["payload"]["type"] == "send_autonomous_summary"
    assert "Task Summary" in published["payload"]["subject"]
    body = published["payload"]["body"]
    assert "Why:" in body
    assert "Where:" in body
    assert "Whom:" in body
    assert "Expected Outcome:" in body
    assert "Current Status:" in body


def test_maybe_email_task_summary_throttle_prevents_flood(monkeypatch):
    class _Redis:
        def __init__(self):
            self.kv = {}
            self.exp = {}
            self.counters = {}

        def setnx(self, key, value):
            if key in self.kv:
                return 0
            self.kv[key] = value
            return 1

        def expire(self, key, ttl):
            self.exp[key] = ttl
            return True

        def get(self, key):
            return self.kv.get(key)

        def set(self, key, value, ex=None):
            self.kv[key] = value
            if ex is not None:
                self.exp[key] = ex
            return True

        def incr(self, key):
            self.counters[key] = int(self.counters.get(key, 0)) + 1
            return self.counters[key]

    agent = _agent_stub()
    agent.redis_client = _Redis()
    agent.task_summary_email_enabled = True
    agent.task_summary_email_exclude_roles = {"email_marketing_agent"}
    agent.task_summary_min_interval_seconds = 9999
    published = []

    def _capture_publish(role, payload):
        published.append((role, payload))
        return "mail-task"

    monkeypatch.setattr(agent, "publish_task_to_agent", _capture_publish, raising=False)
    ctx = {"task_id": "t-1", "source_agent": "api_gateway", "task": {"type": "status"}}
    agent._maybe_email_task_summary(
        task_context=ctx,
        task_type="status",
        status="completed",
        result={"status": "success", "message": "ok"},
        error_text=None,
    )
    agent._maybe_email_task_summary(
        task_context=ctx,
        task_type="status",
        status="completed",
        result={"status": "success", "message": "ok"},
        error_text=None,
    )
    assert len(published) == 1
