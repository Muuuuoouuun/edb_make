"""Updater stabilization regressions: preflight, failure report, Rosetta, storage, log size."""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app_server as app

TRANSLOCATED = Path("/private/var/folders/x/AppTranslocation/ABC/d/ClassInEDBMVP.app")


def mac_update_status():
    return {
        "platform": "macos", "arch": "arm64", "currentVersion": "0.2.8", "updateAvailable": True,
        "latest": {"version": "0.2.9", "downloadUrl": "https://example.test/ClassInEDBMVP.dmg",
                   "fileName": "ClassInEDBMVP.dmg", "artifactType": "dmg", "sha256": "a" * 64, "sizeBytes": 100},
    }


def test_translocated_mac_app_is_refused_before_any_download_and_status_says_why():
    verifier = app.build_update_pin_verifier("4826", salt=b"0123456789abcdef")
    app.clear_update_pin_attempts()
    with (patch.object(app, "build_app_update_status", return_value=mac_update_status()),
          patch.object(app, "load_app_update_config", return_value={"updatePinHash": verifier}),
          patch.object(app, "is_frozen_app", return_value=True),
          patch.object(app, "_current_macos_bundle_path", return_value=TRANSLOCATED),
          patch.object(app, "_download_update_artifact") as download):
        with pytest.raises(app.AppUpdateError) as caught:
            app.prepare_app_update("4826")
    assert caught.value.code == "update_translocated_app"
    assert "응용 프로그램" in caught.value.recovery_steps[0]
    download.assert_not_called()

    app.clear_app_update_status_cache()
    config = {"appId": "ClassInEDBMVP", "appName": "ClassInEDBMVP", "platform": "macos", "version": "0.2.8"}
    with (patch.object(app, "load_app_update_config", return_value=config),
          patch.object(app, "is_frozen_app", return_value=True),
          patch.object(app, "_current_macos_bundle_path", return_value=TRANSLOCATED)):
        status = app.build_app_update_status()
    app.clear_app_update_status_cache()
    assert status["automaticUpdateBlockedReason"] == str(caught.value)


def test_failed_update_marker_is_reported_once(tmp_path):
    marker = tmp_path / "pending-abc" / "update-failed"
    marker.parent.mkdir()
    marker.write_text("2026-11-18T09:00:00")
    replies = []
    handler = SimpleNamespace(path="/api/app/update", _rewrite_legacy_ui_asset_path=lambda _p: False,
                              _send_json=replies.append)
    with (patch.object(app, "_update_root", return_value=tmp_path),
          patch.object(app, "build_app_update_status", side_effect=lambda: {"ok": True})):
        app.AppRequestHandler.do_GET(handler)
        app.AppRequestHandler.do_GET(handler)
    assert [reply.get("lastUpdateFailed") for reply in replies] == [True, None]
    assert not marker.exists()


@pytest.mark.parametrize("translated,expected", [("1\n", "arm64"), ("0\n", "x64"), (OSError("no sysctl"), "x64")])
def test_intel_build_under_rosetta_asks_for_the_native_build(translated, expected):
    app._macos_rosetta_translated.cache_clear()
    run = (patch.object(app.subprocess, "run", side_effect=translated) if isinstance(translated, Exception)
           else patch.object(app.subprocess, "run", return_value=SimpleNamespace(stdout=translated)))
    try:
        with patch.object(app.sys, "platform", "darwin"), patch.object(app.platform, "machine", return_value="x86_64"), run:
            assert app._app_arch_key() == expected
    finally:
        app._macos_rosetta_translated.cache_clear()


def test_windows_updates_stay_out_of_synced_documents_and_old_attempts_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("EDB_APP_HOME", raising=False)
    with patch.object(app.sys, "platform", "win32"):
        assert app._update_root() == tmp_path / "local" / "ClassInEDBMVP" / "updates"

    old = tmp_path / "updates" / "pending-old"
    old.mkdir(parents=True)
    week_ago = time.time() - 8 * 24 * 3600
    os.utime(old, (week_ago, week_ago))
    recent = tmp_path / "updates" / "pending-recent"
    recent.mkdir()
    with patch.object(app, "urlopen", side_effect=OSError("offline")), pytest.raises(app.AppUpdateError):
        app._download_update_artifact(mac_update_status(), runtime_dir=tmp_path)
    assert not old.exists()
    assert recent.exists()


def test_large_app_log_is_rotated_at_launch(tmp_path, monkeypatch):
    # In place, never renamed: on macOS a Dock relaunch runs this while the serving copy still writes app.log.
    monkeypatch.setattr(app, "APP_LOG_MAX_BYTES", 4096)
    monkeypatch.setattr(app, "_app_log_stream", None)
    log = tmp_path / "app.log"
    log.write_bytes(b"x" * 4097)
    serving = log.open("a", encoding="utf-8", buffering=1)
    original = (app.sys.stdout, app.sys.stderr)
    try:
        app.configure_app_logging(log)
        stream = app.sys.stdout
    finally:
        app.sys.stdout, app.sys.stderr = original
    stream.close()
    serving.write("server after relaunch\n")
    serving.close()
    assert (tmp_path / "app.log.1").stat().st_size == 4097
    assert log.stat().st_size < 1024
    assert log.read_text(encoding="utf-8").endswith("server after relaunch\n")
