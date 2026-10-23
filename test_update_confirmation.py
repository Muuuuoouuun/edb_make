from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import app_server as app


def available_update():
    return {
        "platform": "windows", "arch": "x64", "currentVersion": "0.2.8",
        "updateAvailable": True,
        "latest": {"version": "0.2.9", "downloadUrl": "https://example.test/Setup.exe",
                   "fileName": "Setup.exe", "artifactType": "setup-exe",
                   "sha256": "a" * 64, "sizeBytes": 100},
    }


def confirmation(status):
    latest = status["latest"]
    return {"version": latest["version"], "downloadUrl": latest["downloadUrl"],
            "sha256": latest["sha256"], "sizeBytes": latest["sizeBytes"],
            "arch": status["arch"], "artifactType": latest["artifactType"]}


@pytest.mark.parametrize("field,value", [
    ("version", "0.2.10"), ("downloadUrl", "https://example.test/Other.exe"),
    ("sha256", "b" * 64), ("sizeBytes", 101), ("arch", "arm64"),
    ("artifactType", "zip"), ("sizeBytes", "100"), ("sizeBytes", True),
])
def test_changed_confirmed_artifact_never_downloads_or_launches(field, value):
    status = available_update()
    expected = confirmation(status)
    expected[field] = value
    verifier = app.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
    with (patch.object(app, "build_app_update_status", return_value=status),
          patch.object(app, "load_app_update_config", return_value={"updatePinHash": verifier}),
          patch.object(app, "_download_update_artifact") as download,
          patch.object(app, "_launch_update_helper") as launch):
        with pytest.raises(app.AppUpdateError) as caught:
            app.prepare_app_update("4826", expected_update=expected)
    assert caught.value.code == "update_target_changed"
    assert caught.value.status == app.HTTPStatus.CONFLICT
    download.assert_not_called()
    launch.assert_not_called()


def test_confirmed_target_with_normalized_arch_installs_exact_artifact():
    status = available_update()
    expected = confirmation(status)
    expected.update(arch="AMD64")
    verifier = app.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
    artifact = Path("Setup.exe")
    with (patch.object(app, "build_app_update_status", return_value=status),
          patch.object(app, "load_app_update_config", return_value={"updatePinHash": verifier}),
          patch.object(app, "_download_update_artifact", return_value=artifact) as download,
          patch.object(app, "_launch_update_helper", return_value={"restartScheduled": True}) as launch):
        result = app.prepare_app_update("4826", expected_update=expected)
    assert result["targetVersion"] == "0.2.9"
    download.assert_called_once_with(status)
    launch.assert_called_once_with(artifact, status, "setup-exe")


def test_install_http_requires_confirmation_before_starting_update():
    handler = SimpleNamespace(
        headers={"Host": "127.0.0.1:8765"},
        _read_json_body=lambda: {"pin": "4826"}, _send_json=Mock(),
    )
    with patch.object(app, "begin_app_update") as begin:
        app.AppRequestHandler._handle_app_update_install(handler)
    begin.assert_not_called()
    payload = handler._send_json.call_args.args[0]
    assert payload["code"] == "update_confirmation_required"
    assert handler._send_json.call_args.kwargs["status"] == app.HTTPStatus.CONFLICT


def test_explicit_http_check_refreshes_backend_cache_but_regular_poll_reuses_it():
    app.clear_app_update_status_cache()
    config = {"appId": "ClassInEDBMVP", "appName": "ClassInEDBMVP", "version": "0.2.8",
              "platform": "windows", "updateFeedUrl": "https://example.test/update.json"}
    first = {"version": "0.2.9", "platforms": {"windows": available_update()["latest"]}}
    second = deepcopy(first)
    second["platforms"]["windows"]["version"] = "0.2.10"
    replies = []
    handler = SimpleNamespace(path="/api/app/update", _rewrite_legacy_ui_asset_path=lambda _p: False,
                              _send_json=replies.append)
    with (patch.object(app, "load_app_update_config", return_value=config),
          patch.object(app, "_fetch_update_feed", side_effect=[first, second]) as fetch,
          patch.object(app.platform, "machine", return_value="AMD64")):
        app.AppRequestHandler.do_GET(handler)
        app.AppRequestHandler.do_GET(handler)
        handler.path += "?refresh=1"
        app.AppRequestHandler.do_GET(handler)
    assert [reply["latest"]["version"] for reply in replies] == ["0.2.9", "0.2.9", "0.2.10"]
    assert fetch.call_count == 2
    app.clear_app_update_status_cache()


@pytest.mark.parametrize("platform,suffix,kind", [
    ("windows", ".exe", "setup-exe"), ("macos", ".dmg", "dmg"), ("macos", ".zip", "zip"),
])
def test_legacy_feed_without_optional_artifact_type_accepts_browser_snapshot(platform, suffix, kind):
    status = available_update()
    status["platform"] = platform
    status["latest"].update(downloadUrl="https://example.test/installer" + suffix, fileName="installer" + suffix)
    expected = confirmation(status)
    status["latest"].pop("artifactType")
    expected["artifactType"] = ""
    app._validate_confirmed_update(status, expected)
    assert app._update_artifact_details(status)[2] == kind
    expected["sha256"] = "b" * 64
    with pytest.raises(app.AppUpdateError, match="변경"):
        app._validate_confirmed_update(status, expected)
