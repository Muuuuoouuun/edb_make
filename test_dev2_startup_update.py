"""Round-2 startup and update fixes: Mac CA roots, folder guards, a stale Mac server, shared PCs,
the loopback proxy, renamed Mac installs, and the API key and update downloads out of synced Documents."""
from __future__ import annotations

import errno
import json
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import app_server as app
import user_settings
from scripts.smoke_packaged_app import _validate_update_metadata

ROOT = Path(__file__).resolve().parent


class _FakeHealth:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


def _serve_health(payload: dict) -> ThreadingHTTPServer:
    body = json.dumps(payload).encode("utf-8")

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# R6: the frozen Mac app has no CA roots -------------------------------------------------------

@pytest.mark.parametrize("platform, cafile, expected", [
    ("darwin", None, "/etc/ssl/cert.pem"),  # a teacher's Mac: python.org's bundle folder is missing
    ("darwin", "/opt/homebrew/etc/openssl@3/cert.pem", "/missing.pem"),  # a working bundle is left alone
    ("win32", None, "/missing.pem"),  # Windows reads its own certificate store
])
def test_mac_without_ca_roots_uses_the_system_bundle(platform, cafile, expected):
    with mock.patch.dict(os.environ, {"SSL_CERT_FILE": "/missing.pem"}), \
            mock.patch.object(app.sys, "platform", platform), \
            mock.patch.object(app.ssl, "get_default_verify_paths", return_value=SimpleNamespace(cafile=cafile)):
        app._use_macos_system_roots()
        assert os.environ["SSL_CERT_FILE"] == expected


def test_serving_process_fixes_ca_roots_before_any_startup_work():
    order = mock.Mock()
    with mock.patch.object(app, "is_frozen_app", return_value=False), \
            mock.patch.object(app.sys, "argv", ["app_server.py", "--no-open-browser"]), \
            mock.patch.object(app, "_use_macos_system_roots", order.roots), \
            mock.patch.object(app, "ensure_runtime_dirs", order.dirs), \
            mock.patch.object(app, "enable_fatal_error_logging"), \
            mock.patch.object(app, "run_server", order.serve):
        assert app.main() == 0
    assert [name for name, *_ in order.mock_calls] == ["roots", "dirs", "serve"]


def test_app_smoke_fails_only_when_the_app_cannot_verify_https():
    update = {
        "ok": True, "appId": "ClassInEDBMVP", "currentVersion": "1.2.3", "platform": "macos", "arch": "arm64",
        "channelStatus": "error", "configured": True, "updateAvailable": False, "automaticUpdateEnabled": True,
        "automaticUpdateSupported": True, "automaticUpdateReady": False,
    }
    _validate_update_metadata({**update, "detail": "URLError: <urlopen error timed out>"})  # offline runner passes
    with pytest.raises(RuntimeError, match="cannot verify HTTPS certificates"):
        _validate_update_metadata({
            **update,
            "detail": "URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>",
        })
    workflow = (ROOT / ".github" / "workflows" / "build-installers.yml").read_text(encoding="utf-8")
    assert "SSL_CERT_FILE=/nonexistent/cert.pem .venv/bin/python scripts/smoke_packaged_app.py" in workflow


# R7: a folder guard makes the write probe spin -----------------------------------------------

def test_existing_data_folder_that_refuses_new_files_falls_back_at_once(tmp_path):
    runtime = tmp_path / "home" / ".app_runtime"
    runtime.mkdir(parents=True)
    real_open, attempts = os.open, []

    def denied(path, *args, **kwargs):
        if Path(path).parent == runtime:
            attempts.append(path)
            raise PermissionError(13, "denied", str(path))
        return real_open(path, *args, **kwargs)

    # TMP_MAX=50 makes the old tempfile probe give up after 50 tries instead of hanging the run.
    with mock.patch.object(app.os, "open", denied), mock.patch.object(app.tempfile, "TMP_MAX", 50):
        assert app._app_home_is_writable(runtime.parent) is False
    assert len(attempts) == 1
    assert app._app_home_is_writable(tmp_path / "ok") is True
    assert list((tmp_path / "ok" / ".app_runtime").iterdir()) == []
    # Every other tempfile caller (saves under the session lock) fails fast too; POSIX never loops.
    assert app.tempfile.TMP_MAX <= 1000


# R29: after a hand-installed Mac update the old server keeps running ---------------------------

@pytest.mark.parametrize("payload, warned", [
    ({"ok": True, "version": "0.0.1"}, True),
    ({"ok": True}, True),  # a server from before /api/health named its version
    ({"ok": True, "version": "0.2.15"}, False),
])
def test_macos_plain_open_says_when_another_version_is_still_running(payload, warned):
    order = mock.Mock()
    with mock.patch.object(app, "is_frozen_app", return_value=True), \
            mock.patch.object(app.sys, "platform", "darwin"), \
            mock.patch.object(app.sys, "argv", ["ClassInEDBMVP"]), \
            mock.patch.object(app, "load_app_update_config", return_value={"version": "0.2.15"}), \
            mock.patch.object(app, "_local_server_is_healthy", return_value=True), \
            mock.patch.object(app, "_local_server_health", return_value={**payload, "app": app.APP_NAME}), \
            mock.patch.object(app, "_show_macos_alert", order.alert), \
            mock.patch.object(app.webbrowser, "open", order.open), \
            mock.patch.object(app.subprocess, "Popen") as popen:
        assert app.main() == 0
    popen.assert_not_called()
    assert [name for name, *_ in order.mock_calls] == (["alert", "open"] if warned else ["open"])
    order.open.assert_called_once_with("http://127.0.0.1:8765/")
    if warned:
        message = order.alert.call_args.args[0]
        assert "‘종료’" in message and "다시 열어 주세요" in message


def test_second_copy_on_a_moved_port_also_says_when_another_version_runs():
    with mock.patch.object(app, "is_frozen_app", return_value=True), \
            mock.patch.object(app.sys, "platform", "darwin"), \
            mock.patch.object(app, "ensure_runtime_dirs"), \
            mock.patch.object(app, "hydrate_user_settings_env"), \
            mock.patch.object(app, "write_placeholder_generated_session"), \
            mock.patch.object(app, "load_app_update_config", return_value={"version": "0.2.15"}), \
            mock.patch.object(app, "_local_server_is_healthy", side_effect=lambda _h, p, **_kw: p == 8766), \
            mock.patch.object(app, "_local_server_health", return_value={"ok": True, "app": app.APP_NAME}), \
            mock.patch.object(app, "_show_macos_alert") as alert, \
            mock.patch.object(app.webbrowser, "open") as browser, \
            mock.patch.object(app, "AppHTTPServer", side_effect=OSError(48, "in use")) as server_class:
        app.run_server(host="127.0.0.1", port=8765, open_browser=True)
    alert.assert_called_once()
    browser.assert_called_once_with("http://127.0.0.1:8766/")
    assert server_class.call_count == 1  # 8765 tried once, the copy on 8766 reused


def test_windows_reuse_never_probes_or_alerts():
    with mock.patch.object(app, "is_frozen_app", return_value=True), \
            mock.patch.object(app.sys, "platform", "win32"), \
            mock.patch.object(app, "_local_server_health") as health, \
            mock.patch.object(app, "_show_macos_alert") as alert, \
            mock.patch.object(app.webbrowser, "open") as browser:
        app._open_running_app("127.0.0.1", 8765)
    health.assert_not_called()
    alert.assert_not_called()
    browser.assert_called_once_with("http://127.0.0.1:8765/")


def test_health_names_the_version_read_at_start_and_the_data_folder():
    server = app.AppHTTPServer(("127.0.0.1", 0), app.AppRequestHandler)
    try:
        started = app.load_app_update_config()["version"]
        with mock.patch.object(app, "load_app_update_config", return_value={"version": "9.9.9"}):
            payload = server.health_payload()
    finally:
        server.server_close()
    assert payload["version"] == started
    assert payload["home"] == app._DATA_FOLDER_TAG


# R26: a second OS account reuses the first teacher's server ------------------------------------

def test_health_check_skips_another_accounts_copy():
    cases = (({"home": "0123456789abcdef"}, False), ({"home": app._DATA_FOLDER_TAG}, True), ({}, True))
    for extra, ours in cases:  # no "home": a copy from before the tag, still ours
        payload = {"ok": True, "app": app.APP_NAME, **extra}
        with mock.patch.object(app, "build_opener") as opener:
            opener.return_value.open.return_value = _FakeHealth(payload)
            assert app._local_server_is_healthy("127.0.0.1", 8765) is ours
            assert app._local_server_health("127.0.0.1", 8765) == (payload if ours else None)


def test_another_accounts_copy_on_the_port_makes_this_one_take_the_next_port():
    foreign = _serve_health({"ok": True, "app": app.APP_NAME, "home": "someone-else"})
    port = foreign.server_address[1]
    ours = mock.MagicMock()
    ours.shutdown_reason.return_value = "test"

    def bind(address, _handler):
        if address[1] == port:
            raise OSError(errno.EADDRINUSE, "in use")
        return ours

    try:
        with mock.patch.object(app, "ensure_runtime_dirs"), \
                mock.patch.object(app, "hydrate_user_settings_env"), \
                mock.patch.object(app, "write_placeholder_generated_session"), \
                mock.patch.object(app, "_prune_stale_update_downloads"), \
                mock.patch.object(app, "_run_startup_maintenance"), \
                mock.patch.object(app, "AppHTTPServer", side_effect=bind) as server_class, \
                mock.patch.object(app.webbrowser, "open") as browser:
            app.run_server(host="127.0.0.1", port=port, open_browser=True)
    finally:
        foreign.shutdown()
        foreign.server_close()
    assert server_class.call_args.args[0] == ("127.0.0.1", port + 1)
    browser.assert_called_once_with(f"http://127.0.0.1:{port + 1}/")
    ours.serve_forever.assert_called_once()


# R42: the loopback probe went through the system proxy -----------------------------------------

def test_local_health_check_ignores_system_proxy():
    server = _serve_health({"ok": True, "app": app.APP_NAME})
    try:
        # Resetting _opener matters: a global opener cached earlier without a proxy would hide the bug.
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""}), \
                mock.patch.object(urllib.request, "_opener", None):
            assert app._local_server_is_healthy("127.0.0.1", server.server_address[1], timeout=2.0)
    finally:
        server.shutdown()
        server.server_close()


# R41: a Mac install not named ClassInEDBMVP.app never updates ----------------------------------

def test_mac_update_helper_is_told_the_bundle_name_inside_the_download(tmp_path):
    artifact = tmp_path / "pending-x" / "ClassInEDBMVP-macOS.dmg"
    artifact.parent.mkdir()
    artifact.write_bytes(b"x")
    with mock.patch.object(app, "_check_update_target", return_value=Path("/Applications/ClassInEDBMVP 2.app")), \
            mock.patch.object(app.subprocess, "Popen") as popen:
        app._launch_macos_update_helper(artifact, "dmg")
    assert popen.call_args.args[0][-1] == "ClassInEDBMVP.app"
    helper = app.MACOS_UPDATE_HELPER
    # ${6:-...}, never a bare "$6": under set -u that would stop before the reopen trap is set.
    assert 'source_name="${6:-$bundle_name}"' in helper
    assert '"$mount_dir/$source_name"' in helper and '"$extract_dir/$source_name"' in helper
    assert "$mount_dir/$bundle_name" not in helper


# R47: the Gemini key sits in the synced Documents folder ---------------------------------------

def test_move_settings_leaves_the_key_only_in_the_local_folder(tmp_path):
    documents, local = tmp_path / "documents", tmp_path / "local"
    user_settings.save_user_settings(documents, {"gemini_api_key": "moved-test-key", "ai_enabled": False})

    user_settings.move_settings(documents, local)

    assert not user_settings.settings_path(documents).exists()
    assert user_settings.load_user_settings(local) == {"gemini_api_key": "moved-test-key", "ai_enabled": False}
    # Moved once: a copy an older version writes later (another PC on the same OneDrive) never replaces it.
    user_settings.save_user_settings(documents, {"gemini_api_key": "other-pc-key"})
    user_settings.move_settings(documents, local)
    assert user_settings.load_user_settings(local)["gemini_api_key"] == "moved-test-key"


def test_locked_settings_file_is_not_moved(tmp_path, monkeypatch):
    documents, local = tmp_path / "documents", tmp_path / "local"
    user_settings.save_user_settings(documents, {"gemini_api_key": "kept-test-key"})
    real_read = Path.read_text

    def locked(self, *args, **kwargs):
        if self == user_settings.settings_path(documents):
            error = OSError(13, "sharing violation")
            error.winerror = 32
            raise error
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", locked)
    monkeypatch.setattr(user_settings.time, "sleep", lambda _seconds: None)
    with pytest.raises(OSError):
        user_settings.move_settings(documents, local)
    monkeypatch.undo()

    assert user_settings.load_user_settings(documents) == {"gemini_api_key": "kept-test-key"}
    assert not user_settings.settings_path(local).exists()


def test_packaged_app_keeps_the_api_key_out_of_documents(tmp_path, monkeypatch):
    documents = tmp_path / "Documents" / "ClassInEDBMVP" / ".app_runtime"
    local = tmp_path / "local"
    user_settings.save_user_settings(documents, {"gemini_api_key": "moved-test-key", "ai_enabled": False})
    monkeypatch.setattr(app, "RUNTIME_DIR", documents)
    monkeypatch.setattr(app, "is_frozen_app", lambda: True)
    monkeypatch.setattr(app, "_local_app_home", lambda: local)
    replies = []
    handler = object.__new__(app.AppRequestHandler)
    handler._send_json = lambda payload, **_kwargs: replies.append(payload)
    with mock.patch.dict(os.environ):
        os.environ.pop("EDB_APP_HOME", None)
        os.environ.pop("GEMINI_API_KEY", None)

        app.hydrate_user_settings_env()

        assert os.environ["GEMINI_API_KEY"] == "moved-test-key"
        assert not user_settings.settings_path(documents).exists()
        assert app._global_ai_enabled() is False
        handler._handle_user_settings_get()
        assert replies[-1]["settings"]["hasStoredGeminiApiKey"] is True
        handler._read_json_body = lambda: {"aiEnabled": True}
        handler._handle_user_settings_post()
        assert user_settings.load_user_settings(local)["ai_enabled"] is True
        assert not user_settings.settings_path(documents).exists()
        # A portable or smoke-test home (EDB_APP_HOME) keeps everything together.
        os.environ["EDB_APP_HOME"] = str(tmp_path)
        assert app._settings_dir() == documents


def test_source_runs_keep_settings_in_the_runtime_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "RUNTIME_DIR", tmp_path)
    assert app._settings_dir() == tmp_path


# R60: Mac update downloads and the update-failed marker sit in iCloud Documents ---------------

def test_mac_updates_stay_out_of_icloud_documents(monkeypatch):
    monkeypatch.delenv("EDB_APP_HOME", raising=False)
    with mock.patch.object(app.sys, "platform", "darwin"):
        root = app._update_root()
        assert root == Path.home() / "Library" / "Application Support" / "ClassInEDBMVP" / "updates"
        assert app.RUNTIME_DIR not in root.parents
        monkeypatch.setenv("EDB_APP_HOME", "x")
        assert app._update_root() == app.RUNTIME_DIR / "updates"
