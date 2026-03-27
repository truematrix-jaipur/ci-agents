import json

from agents.seo_agent import extended_analyzer as ext_mod


def test_extended_analyzer_degraded_mode_when_all_llm_paths_fail(monkeypatch):
    ea = ext_mod.ExtendedAnalyzer()

    monkeypatch.setattr(ea, "_call_llm", lambda prompt: (_ for _ in ()).throw(RuntimeError("llm down")))
    monkeypatch.setattr(ext_mod.vector_store, "create_action_item", lambda *args, **kwargs: None, raising=False)

    out = ea.analyze_extended_report(
        report={
            "index_coverage": {},
            "links": {},
            "cwv": {},
            "rich_results": {},
            "sitemaps": [],
        },
        snapshot_id="snapshot_x",
    )

    assert out.get("degraded_mode") is True
    assert out.get("snapshot_id") == "snapshot_x"
    assert isinstance(out.get("indexing_actions"), list)

