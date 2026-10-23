import json

import pytest

from scripts.audit_packaged_update_config import audit_config
from scripts.build_app_update_config import build_update_pin_verifier


def test_packaged_pin_audit_only_reports_metadata_and_match_result(tmp_path):
    config = tmp_path / "_internal/app_update_config.json"
    config.parent.mkdir()
    pin = "58274931"
    verifier = build_update_pin_verifier(pin, "ClassInEDBMVP")
    config.write_text(json.dumps({"version": "0.2.8", "appId": "ClassInEDBMVP", "updatePinHash": verifier,
        "updateFeedUrl": "https://user:pass@example.test/update.json?token=secret#hidden"}))
    result = audit_config(tmp_path, build_pin=pin)
    assert result["pinConfigured"] and result["pinVerifierValid"]
    assert result["matchesCurrentBuildSecret"] is True
    assert result["updateFeedUrlWithoutCredentials"] == "https://example.test/update.json"
    text = json.dumps(result)
    assert all(secret not in text for secret in [pin, verifier, "user:pass", "token=secret", "hidden"])
    assert audit_config(tmp_path, build_pin="9999")["matchesCurrentBuildSecret"] is False


def test_empty_pin_and_feed_do_not_invent_a_default(tmp_path):
    (tmp_path / "app_update_config.json").write_text('{"version":"0.2.8"}')
    result = audit_config(tmp_path)
    assert result["pinConfigured"] is False
    assert result["updateFeedConfigured"] is False
    assert result["defaultPinExists"] is False
    assert result["matchesCurrentBuildSecret"] is None


def test_ambiguous_packaged_configs_are_rejected(tmp_path):
    (tmp_path / "app_update_config.json").write_text("{}")
    other = tmp_path / "_internal/app_update_config.json"
    other.parent.mkdir()
    other.write_text("{}")
    with pytest.raises(ValueError, match="unambiguous"):
        audit_config(tmp_path)
