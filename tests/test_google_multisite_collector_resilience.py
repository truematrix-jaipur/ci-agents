from agents.google_agent.google_multisite_collector import GoogleMultisiteCollector


def test_fetch_all_sites_marks_unaccessible_gsc_property_without_calling_gsc(monkeypatch):
    collector = GoogleMultisiteCollector(credentials_path="/tmp/missing.json", google_api_key="")

    monkeypatch.setattr(
        collector,
        "list_accessible_gsc_sites",
        lambda: [{"siteUrl": "sc-domain:indogenmed.org"}],
        raising=True,
    )

    called = {"gsc": False}

    def _never_call(*args, **kwargs):
        called["gsc"] = True
        raise AssertionError("fetch_gsc_bundle should not be called for inaccessible property")

    monkeypatch.setattr(collector, "fetch_gsc_bundle", _never_call, raising=True)
    monkeypatch.setattr(collector, "fetch_woocommerce_products", lambda **kwargs: {"status": "skipped"}, raising=True)

    out = collector.fetch_all_sites(
        [
            {
                "site_id": "bad-site",
                "domain": "example.org",
                "gsc_site_url": "https://example.org/",
            }
        ],
        days=7,
    )

    assert called["gsc"] is False
    assert out["sites"][0]["status"] == "partial"
    errors = out["sites"][0].get("errors", [])
    assert any(e.get("error_code") == "site_not_accessible" for e in errors)


def test_classify_scope_error_includes_remediation_for_permission_issues():
    profile = {"site_id": "indogenmed", "domain": "indogenmed.org", "gsc_site_url": "sc-domain:indogenmed.org"}
    err = Exception("403 Forbidden: insufficient permissions")
    out = GoogleMultisiteCollector._classify_scope_error("ga4", err, profile)

    assert out["error_code"] == "forbidden"
    assert isinstance(out.get("remediation"), list)
    assert out.get("profile_context", {}).get("site_id") == "indogenmed"

