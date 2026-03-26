import importlib.util
from pathlib import Path
import sys
import builtins

import pytest


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.closed = False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, rows=None):
        self.cursor_obj = FakeCursor(rows=rows)

    def cursor(self, dictionary=True):
        return self.cursor_obj


@pytest.fixture
def base_stubs(monkeypatch):
    from core.base_agent import BaseAgent

    monkeypatch.setattr(BaseAgent, "log_execution", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(BaseAgent, "publish_task_to_agent", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(BaseAgent, "execute_llm", lambda *a, **k: "ok", raising=False)
    monkeypatch.setattr(BaseAgent, "speak", lambda *a, **k: None, raising=False)


@pytest.fixture
def patch_subprocess(monkeypatch):
    import subprocess

    class _R:
        def __init__(self, out="ok"):
            self.stdout = out
            self.returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R("dummy\nline2\nline3\nline4\nline5\nline6"), raising=True)


def _mk_agent(cls, **attrs):
    obj = object.__new__(cls)
    obj.agent_id = "t-1"
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


def _load_module_from_path(name: str, path: Path):
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_data_analyser_blocks_unsafe_sql(base_stubs):
    from agents.data_analyser.agent import DataAnalyserAgent

    agent = _mk_agent(DataAnalyserAgent, mysql_conn=FakeConn(), erpnext_conn=FakeConn())

    r1 = agent._execute_query({"task": {"type": "query_db", "query": "UPDATE t SET a=1"}})
    assert r1["status"] == "error"
    assert "SELECT" in r1["message"]

    r2 = agent._execute_query({"task": {"type": "query_db", "query": "SELECT * FROM t; DROP TABLE t"}})
    assert r2["status"] == "error"
    assert "Multi-statement" in r2["message"]


def test_data_analyser_parameterized_query(base_stubs):
    from agents.data_analyser.agent import DataAnalyserAgent

    conn = FakeConn(rows=[{"page_views": 10}])
    agent = _mk_agent(DataAnalyserAgent, mysql_conn=conn, erpnext_conn=FakeConn())

    res = agent._execute_query(
        {"task": {"type": "query_db", "query": "SELECT page_views FROM traffic_stats WHERE url = %s", "params": ["https://x"]}}
    )
    assert res["status"] == "success"
    assert conn.cursor_obj.executed[0][1] == ("https://x",)


def test_data_analyser_manual_sales_trend_command(base_stubs):
    from agents.data_analyser.agent import DataAnalyserAgent

    rows = [
        {"day": "2026-03-20", "orders": 3, "revenue": 100.0},
        {"day": "2026-03-21", "orders": 2, "revenue": 80.0},
    ]
    conn = FakeConn(rows=rows)
    agent = _mk_agent(DataAnalyserAgent, mysql_conn=FakeConn(), erpnext_conn=conn)

    res = agent.handle_task({"task": {"type": "manual_command", "command": "summarize last 7 days sales trend"}})
    assert res["status"] == "success"
    assert res["window_days"] == 7
    assert "timeline" in res
    assert "Sales trend (7d)" in res["summary"]


def test_data_analyser_autonomous_sales_monitor_delegates_on_drop(base_stubs, monkeypatch):
    from agents.data_analyser.agent import DataAnalyserAgent

    # First half stronger than second half -> negative trend.
    rows = [
        {"day": "2026-03-20", "orders": 3, "revenue": 120.0},
        {"day": "2026-03-21", "orders": 3, "revenue": 100.0},
        {"day": "2026-03-22", "orders": 2, "revenue": 80.0},
        {"day": "2026-03-23", "orders": 1, "revenue": 20.0},
    ]
    agent = _mk_agent(DataAnalyserAgent, mysql_conn=FakeConn(), erpnext_conn=FakeConn(rows=rows))
    delegated = {}

    def _capture(self, role, payload):
        delegated["role"] = role
        delegated["payload"] = payload
        return "task-123"

    monkeypatch.setattr(DataAnalyserAgent, "publish_task_to_agent", _capture, raising=False)
    res = agent.handle_task(
        {
            "task": {
                "type": "autonomous_sales_monitor",
                "days": 4,
                "drop_alert_pct": -5.0,
                "auto_delegate": True,
            }
        }
    )
    assert res["status"] == "success"
    assert res["alert_triggered"] is True
    assert res["delegated_task_id"] == "task-123"
    assert delegated["role"] == "growth_agent"


def test_growth_agent_closed_loop_sync(base_stubs, monkeypatch):
    from agents.growth_agent.agent import GrowthAgent

    agent = _mk_agent(GrowthAgent)

    def _spawn(self, cls, payload):
        role = getattr(cls, "AGENT_ROLE", "")
        if role == "data_analyser":
            return {
                "status": "success",
                "trend": {"percent_change": -12.0, "direction": "down"},
                "totals": {"revenue": 1000, "orders": 20},
            }
        if role == "seo_agent" and payload.get("task", {}).get("type") == "get_ga4_summary":
            return {"status": "success", "ga4": {"sessions": 2000, "conversions": 20}}
        if role == "google_agent":
            return {"status": "success", "clicks": 1200, "impressions": 25000, "top_keywords": [{"query": "vitamin c"}]}
        if role == "campaign_planner_agent":
            return {"status": "success", "message": "Campaign plan dispatched."}
        if role == "seo_agent" and payload.get("task", {}).get("type") == "run_autonomous_pipeline":
            return {"status": "success", "message": "Autonomous SEO pipeline triggered."}
        if role == "seo_agent" and payload.get("task", {}).get("type") == "run_extended":
            return {"status": "success", "details": "extended"}
        if role == "fb_campaign_manager":
            return {"status": "success", "action": "Increased bid for performance."}
        if role == "skill_agent":
            return {"status": "success", "message": "knowledge dispatched"}
        return {"status": "error", "message": "unexpected"}

    monkeypatch.setattr(GrowthAgent, "spawn_subagent", _spawn, raising=False)
    res = agent.handle_task(
        {"task": {"type": "plan_quarterly_growth", "execution_mode": "sync", "window_days": 30, "total_budget": 9000}}
    )
    assert res["status"] in {"success", "warning"}
    assert res["mode"] == "sync"
    assert "plan" in res
    assert "execution" in res
    assert res["plan"]["budget"]["total_budget"] == 9000.0


def test_growth_agent_closed_loop_async_dispatch(base_stubs, monkeypatch):
    from agents.growth_agent.agent import GrowthAgent

    agent = _mk_agent(GrowthAgent)
    dispatched = []

    def _publish(self, role, payload):
        dispatched.append((role, payload))
        return f"tid-{len(dispatched)}"

    monkeypatch.setattr(GrowthAgent, "publish_task_to_agent", _publish, raising=False)
    res = agent.handle_task(
        {"task": {"type": "plan_quarterly_growth", "execution_mode": "async", "window_days": 60, "total_budget": 12000}}
    )
    assert res["status"] == "success"
    assert res["mode"] == "async"
    assert len(dispatched) == 5
    assert "campaign_plan_task_id" in res["dispatched"]


def test_growth_agent_custom_csv_report_ingestion(base_stubs, tmp_path, monkeypatch):
    from agents.growth_agent.agent import GrowthAgent

    csv_file = tmp_path / "semrush_export.csv"
    csv_file.write_text("keyword,traffic,position\nvitamin c,1200,4\nomega 3,800,7\n", encoding="utf-8")
    agent = _mk_agent(GrowthAgent)

    def _spawn(self, cls, payload):
        role = getattr(cls, "AGENT_ROLE", "")
        if role == "data_analyser":
            return {"status": "success", "trend": {"percent_change": 3.5, "direction": "up"}, "totals": {"revenue": 2000}}
        if role == "seo_agent" and payload.get("task", {}).get("type") == "get_ga4_summary":
            return {"status": "success", "ga4": {"sessions": 1500, "conversions": 35}}
        if role == "google_agent":
            return {"status": "success", "clicks": 900, "impressions": 20000, "top_keywords": [{"query": "zinc"}]}
        if role == "campaign_planner_agent":
            return {"status": "success"}
        if role == "seo_agent":
            return {"status": "success"}
        if role == "fb_campaign_manager":
            return {"status": "success"}
        if role == "skill_agent":
            return {"status": "success"}
        return {"status": "success"}

    monkeypatch.setattr(GrowthAgent, "spawn_subagent", _spawn, raising=False)
    res = agent.handle_task(
        {
            "task": {
                "type": "plan_quarterly_growth",
                "execution_mode": "sync",
                "custom_reports": [str(csv_file)],
            }
        }
    )
    assert res["status"] in {"success", "warning"}
    assert res["inputs"]["external"]["reports"]
    assert res["diagnosis"]["keyword_signals"]["top_keywords_count"] >= 1


def test_erpnext_customer_lookup_parameterized(base_stubs):
    from agents.erpnext_agent.agent import ERPNextAgent

    conn = FakeConn(rows=[{"name": "CUST-0001"}])
    agent = _mk_agent(ERPNextAgent, conn=conn)

    res = agent._find_customer({"task": {"email": "a@b.com"}})
    assert res["status"] == "success"
    q, params = conn.cursor_obj.executed[0]
    assert "%s" in q
    assert params == ("a@b.com",)


def test_notifier_uses_signed_token_links(monkeypatch):
    from config.settings import config as swarm_cfg

    notifier_mod = _load_module_from_path(
        "seo_notifier",
        Path("/home/agents/agents/seo_agent/notifier.py"),
    )

    sent = {}
    monkeypatch.setattr(swarm_cfg, "SEO_API_SECRET", "test-secret", raising=False)

    def _capture(self, to, subject, html_body):
        sent["to"] = to
        sent["subject"] = subject
        sent["body"] = html_body
        return True

    monkeypatch.setattr(notifier_mod.Notifier, "_send_email", _capture, raising=True)
    ok = notifier_mod.Notifier().send_approval_request(
        report={"action_plan": [], "gsc_metrics": {}, "fetch_date": "2026-03-25"},
        report_id="r1",
    )
    assert ok is True
    assert "?token=" in sent["body"]
    assert "?secret=" not in sent["body"]


@pytest.mark.parametrize(
    "module_path,class_name,task",
    [
        ("agents.agent_builder.agent", "AgentBuilder", {"task": {"type": "build_new_agent", "name": "demo", "description": "x"}}),
        ("agents.campaign_planner_agent.agent", "CampaignPlannerAgent", {"task": {"type": "plan_campaign"}}),
        ("agents.design_agent.agent", "DesignAgent", {"task": {"type": "generate_image_prompt", "topic": "landing page"}}),
        ("agents.devops_agent.agent", "DevOpsAgent", {"task": {"type": "get_system_metrics"}}),
        ("agents.email_marketing_agent.agent", "EmailMarketingAgent", {"task": {"type": "send_newsletter"}}),
        ("agents.erpnext_dev_agent.agent", "ERPNextDevAgent", {"task": {"type": "create_doctype", "name": "X"}}),
        ("agents.fb_campaign_manager.agent", "FBCampaignManagerAgent", {"task": {"type": "optimize_bidding", "campaign_id": "1"}}),
        ("agents.google_agent.agent", "GoogleAgent", {"task": {"type": "get_ga4_conversions"}}),
        ("agents.growth_agent.agent", "GrowthAgent", {"task": {"type": "plan_quarterly_growth"}}),
        ("agents.integration_agent.agent", "IntegrationAgent", {"task": {"type": "check_stock_levels"}}),
        ("agents.seo_agent.agent", "SEOAgent", {"task": {"type": "full_audit", "url": "https://example.com"}}),
        ("agents.server_agent.agent", "ServerAgent", {"task": {"type": "optimize_resources"}}),
        ("agents.skill_agent.agent", "SkillAgent", {"task": {"type": "fetch_documentation", "tool": "docker", "target_agent": "devops_agent"}}),
        ("agents.smo_agent.agent", "SMOResponsiveAgent", {"task": {"type": "post_update", "platform": "x", "content": "hello"}}),
        ("agents.training_agent.agent", "TrainingAgent", {"task": {"type": "train_agent", "target_agent": "seo_agent", "knowledge_content": "abc"}}),
        ("agents.wordpress_tech.agent", "WordPressTechAgent", {"task": {"type": "health_check", "site_path": "/tmp"}}),
    ],
)
def test_agent_handle_task_smoke(base_stubs, patch_subprocess, monkeypatch, module_path, class_name, task):
    module = __import__(module_path, fromlist=[class_name])
    cls = getattr(module, class_name)

    agent = _mk_agent(cls)

    # Agent-specific stubs for methods used by handlers.
    if class_name == "AgentBuilder":
        real_open = builtins.open
        monkeypatch.setattr(agent, "execute_llm", lambda *a, **k: "class X: pass", raising=False)
        monkeypatch.setattr(module.os.path, "exists", lambda p: False, raising=False)
        monkeypatch.setattr(module.os, "makedirs", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(builtins, "open", lambda *a, **k: real_open("/tmp/agent_builder_test.txt", "w"), raising=True)
    if class_name == "IntegrationAgent":
        agent.wc_url = "https://example.com"
        agent.wc_ck = "a"
        agent.wc_cs = "b"
    if class_name == "SEOAgent":
        monkeypatch.setattr(agent, "spawn_subagent", lambda *a, **k: {"metrics": {}, "recommendations": []}, raising=False)
    if class_name == "TrainingAgent":
        fake_collection = type("C", (), {"add": lambda *a, **k: None})()
        fake_chroma = type("CC", (), {"get_or_create_collection": lambda *a, **k: fake_collection})()
        monkeypatch.setattr(module.db_manager, "get_chroma_client", lambda: fake_chroma, raising=True)
        agent.redis_client = type("R", (), {"publish": lambda *a, **k: None})()

    result = agent.handle_task(task)
    assert isinstance(result, dict)
    assert result.get("status") in {"success", "warning", "error"}


def test_wordpress_implement_fix_dry_run_and_apply(base_stubs, tmp_path):
    from agents.wordpress_tech.agent import WordPressTechAgent

    wp_root = tmp_path / "wp"
    target = wp_root / "wp-content" / "themes" / "mytheme" / "functions.php"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<?php\n// old_value\n", encoding="utf-8")

    agent = _mk_agent(WordPressTechAgent)

    dry_run_task = {
        "task": {
            "type": "implement_fix",
            "site_path": str(wp_root),
            "target_path": "wp-content/themes/mytheme/functions.php",
            "replacements": [{"find_text": "old_value", "replace_text": "new_value"}],
            "dry_run": True,
        }
    }
    dry = agent.handle_task(dry_run_task)
    assert dry["status"] == "success"
    assert dry["dry_run"] is True
    assert "old_value" in target.read_text(encoding="utf-8")

    apply_task = {
        "task": {
            "type": "implement_fix",
            "site_path": str(wp_root),
            "target_path": "wp-content/themes/mytheme/functions.php",
            "replacements": [{"find_text": "old_value", "replace_text": "new_value"}],
            "dry_run": False,
        }
    }
    applied = agent.handle_task(apply_task)
    assert applied["status"] == "success"
    assert applied["backup_file"]
    assert "new_value" in target.read_text(encoding="utf-8")


def test_wordpress_implement_fix_blocks_path_traversal(base_stubs, tmp_path):
    from agents.wordpress_tech.agent import WordPressTechAgent

    wp_root = tmp_path / "wp"
    wp_root.mkdir(parents=True, exist_ok=True)
    agent = _mk_agent(WordPressTechAgent)

    task = {
        "task": {
            "type": "implement_fix",
            "site_path": str(wp_root),
            "target_path": "../etc/passwd",
            "replacements": [{"find_text": "x", "replace_text": "y"}],
        }
    }
    result = agent.handle_task(task)
    assert result["status"] == "error"
    assert "Path traversal" in result["message"]


def test_wordpress_woocommerce_rule_change_set_option(base_stubs, monkeypatch):
    from agents.wordpress_tech.agent import WordPressTechAgent

    agent = _mk_agent(WordPressTechAgent)

    monkeypatch.setattr(
        agent,
        "_run_wp_cli",
        lambda *a, **k: {"ok": True, "output": "updated", "command": "wp option update", "returncode": 0},
        raising=True,
    )

    task = {
        "task": {
            "type": "woocommerce_rule_change",
            "site_path": "/tmp",
            "action": "set_option",
            "option_name": "woocommerce_currency",
            "option_value": "USD",
        }
    }
    result = agent.handle_task(task)
    assert result["status"] == "success"
    assert result["action"] == "set_option"
