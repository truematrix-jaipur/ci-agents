from agents.seo_agent.ga4_conversion_auditor import GA4ConversionAuditor


class _CursorInfoSchema:
    def __init__(self, exists: bool):
        self.exists = exists
        self.closed = False

    def execute(self, query, params):
        return None

    def fetchone(self):
        return (1,) if self.exists else None

    def close(self):
        self.closed = True


class _ConnInfoSchemaOnly:
    def __init__(self, exists: bool):
        self.exists = exists
        self.closed = False

    def cursor(self, dictionary=False):
        return _CursorInfoSchema(self.exists)

    def close(self):
        self.closed = True


def test_get_attribution_summary_returns_unavailable_when_table_missing(monkeypatch):
    auditor = GA4ConversionAuditor()
    auditor._db_config = {
        "host": "127.0.0.1",
        "database": "igm_db",
        "user": "x",
        "password": "y",
    }
    monkeypatch.setattr(auditor, "_get_db_connection", lambda: _ConnInfoSchemaOnly(False), raising=True)

    out = auditor.get_attribution_summary(days=28)
    assert out["status"] == "unavailable"
    assert "does not exist" in out["error"]


def test_searchterm_conversion_map_falls_back_to_input_when_table_missing(monkeypatch):
    auditor = GA4ConversionAuditor()
    auditor._db_config = {
        "host": "127.0.0.1",
        "database": "igm_db",
        "user": "x",
        "password": "y",
    }
    monkeypatch.setattr(auditor, "_get_db_connection", lambda: _ConnInfoSchemaOnly(False), raising=True)

    gsc_keywords = [{"query": "cenforce 200", "clicks": 10}]
    out = auditor.get_searchterm_conversion_map(gsc_keywords, days=28)
    assert out == gsc_keywords

