from agents.seo_agent import analyzer as analyzer_mod
import json


def test_analyzer_falls_back_to_gateway_cli_when_providers_fail(monkeypatch):
    monkeypatch.setattr(analyzer_mod.vector_store, "list_provider_statuses", lambda: [], raising=False)

    a = analyzer_mod.Analyzer()

    class FailingAnthropic:
        class messages:
            @staticmethod
            def create(*args, **kwargs):
                raise RuntimeError("invalid x-api-key")

    class FailingOpenAI:
        class chat:
            class completions:
                @staticmethod
                def create(*args, **kwargs):
                    raise RuntimeError("invalid api key")

    monkeypatch.setattr(a, "_get_provider_client", lambda p: FailingAnthropic() if p == "anthropic" else FailingOpenAI(), raising=True)
    monkeypatch.setattr(analyzer_mod.llm_gateway, "execute", lambda *args, **kwargs: '{"action_plan":[]}', raising=True)
    monkeypatch.setattr(analyzer_mod.vector_store, "record_llm_event", lambda *args, **kwargs: None, raising=False)

    out = a._call_llm("prompt", use_case="analysis")
    assert out == '{"action_plan":[]}'
    assert a._last_llm_provider_used == "cli_fallback"


def test_analyzer_returns_degraded_json_when_all_llm_paths_fail(monkeypatch):
    monkeypatch.setattr(analyzer_mod.vector_store, "list_provider_statuses", lambda: [], raising=False)
    monkeypatch.setattr(analyzer_mod.vector_store, "record_llm_event", lambda *args, **kwargs: None, raising=False)

    a = analyzer_mod.Analyzer()
    monkeypatch.setattr(a, "_get_provider_client", lambda p: object(), raising=True)

    class AlwaysFailAnthropic:
        class messages:
            @staticmethod
            def create(*args, **kwargs):
                raise RuntimeError("anthropic unavailable")

    class AlwaysFailOpenAI:
        class chat:
            class completions:
                @staticmethod
                def create(*args, **kwargs):
                    raise RuntimeError("openai unavailable")

    monkeypatch.setattr(a, "_get_provider_client", lambda p: AlwaysFailAnthropic() if p == "anthropic" else AlwaysFailOpenAI(), raising=True)
    monkeypatch.setattr(analyzer_mod.llm_gateway, "execute", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cli unavailable")), raising=True)

    out = a._call_llm("prompt", use_case="analysis")
    parsed = json.loads(out)
    assert parsed.get("degraded_mode") is True
    assert isinstance(parsed.get("monitoring_alerts"), list)
