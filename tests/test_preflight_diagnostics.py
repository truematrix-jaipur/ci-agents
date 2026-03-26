import json
from pathlib import Path
from unittest.mock import patch

from core.diagnostics.preflight import (
    check_gsc_service_account,
    check_indogenmed_woocommerce,
    run_preflight_diagnostics,
)


def test_check_gsc_service_account_missing(tmp_path):
    with patch("core.diagnostics.preflight.config.GSC_SERVICE_ACCOUNT_FILE", str(tmp_path / "missing.json")):
        result = check_gsc_service_account()
    assert result["ok"] is False
    assert result["severity"] == "critical"


def test_check_gsc_service_account_valid(tmp_path):
    sa = tmp_path / "sa.json"
    sa.write_text(
        json.dumps({"client_email": "svc@example.iam.gserviceaccount.com", "private_key": "abc"}),
        encoding="utf-8",
    )
    with patch("core.diagnostics.preflight.config.GSC_SERVICE_ACCOUNT_FILE", str(sa)):
        result = check_gsc_service_account()
    assert result["ok"] is True
    assert result["severity"] == "info"


def test_check_indogenmed_woocommerce_missing_credentials():
    with patch.dict("os.environ", {"WC_URL": "", "WC_INDOGENMED_CK": "", "WC_INDOGENMED_CS": ""}, clear=False):
        result = check_indogenmed_woocommerce()
    assert result["ok"] is False
    assert result["severity"] == "warning"


def test_run_preflight_diagnostics_status_degraded():
    with patch("core.diagnostics.preflight.check_gsc_service_account", return_value={"ok": True, "severity": "info", "detail": "", "remediation": ""}), \
        patch("core.diagnostics.preflight.check_erpnext_mysql", return_value={"ok": False, "severity": "critical", "detail": "", "remediation": ""}), \
        patch("core.diagnostics.preflight.check_indogenmed_woocommerce", return_value={"ok": False, "severity": "warning", "detail": "", "remediation": ""}):
        report = run_preflight_diagnostics()
    assert report["status"] == "degraded"
    assert report["summary"]["critical_count"] == 1
    assert report["summary"]["warning_count"] == 1
