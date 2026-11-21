"""Update/settings copy and plumbing: legacy prune, plain recovery steps, no-release state,
download progress, Mac blocked steps, Gemini key help link, HWP first-run diagnostics."""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app_server as app
from test_ui_home_flows import NODE_SETUP, run_node
from test_ui_update_entry import UPDATE_SETUP

APP_JSX = (Path(__file__).resolve().parent / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
TRANSLOCATED = Path("/private/var/folders/x/AppTranslocation/ABC/d/ClassInEDBMVP.app")
IDENTIFIERS = ("sizeBytes", "sha256", "updatePinHash", "피드")


def _mac_status(body: bytes) -> dict:
    return {
        "platform": "macos", "arch": "arm64", "currentVersion": "0.2.8", "updateAvailable": True,
        "latest": {"version": "0.2.9", "downloadUrl": "https://example.test/ClassInEDBMVP.dmg",
                   "fileName": "ClassInEDBMVP.dmg", "artifactType": "dmg",
                   "sha256": hashlib.sha256(body).hexdigest(), "sizeBytes": len(body)},
    }


class _FakeDownload:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.headers = {"Content-Length": str(sum(map(len, chunks)))}
        self.progress_seen: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def geturl(self):
        return "https://example.test/ClassInEDBMVP.dmg"

    def read(self, _size):
        self.progress_seen.append(dict(app._update_download_progress))
        return self.chunks.pop(0) if self.chunks else b""


def _open_url_handler(url: str):
    handler = object.__new__(app.AppRequestHandler)
    payload = json.dumps({"url": url}).encode("utf-8")
    handler.headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765", "Content-Length": str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    handler.wfile = io.BytesIO()
    handler.statuses = []
    handler.send_response = handler.statuses.append
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None
    return handler


# L10 -------------------------------------------------------------------------------------------

def test_prune_sweeps_current_and_legacy_update_roots_and_runs_at_startup(tmp_path):
    current, legacy = tmp_path / "local" / "updates", tmp_path / "runtime" / "updates"
    week_ago = time.time() - 8 * 24 * 3600
    old_dirs = [current / "pending-old", legacy / "pending-old"]
    for folder in old_dirs:
        folder.mkdir(parents=True)
        (folder / "ClassInEDBMVP-Setup.exe").write_bytes(b"x")
        os.utime(folder, (week_ago, week_ago))
    recent = legacy / "pending-recent"
    recent.mkdir()
    with patch.object(app, "_update_root", return_value=current), patch.object(app, "RUNTIME_DIR", tmp_path / "runtime"):
        app._prune_stale_update_downloads()
    assert not any(folder.exists() for folder in old_dirs)
    assert recent.exists()
    assert "target=_prune_stale_update_downloads" in inspect.getsource(app.run_server)


# L6 --------------------------------------------------------------------------------------------

def test_update_recovery_steps_are_plain_korean_with_identifiers_only_in_detail():
    status = _mac_status(b"x")
    del status["latest"]["sha256"]
    with pytest.raises(app.AppUpdateError) as missing_integrity:
        app._update_artifact_details(status)
    app.clear_update_pin_attempts()
    with (patch.object(app, "build_app_update_status", return_value={"updateAvailable": True}),
          patch.object(app, "load_app_update_config", return_value={})):
        with pytest.raises(app.AppUpdateError) as no_pin:
            app.prepare_app_update("1234")
    for raised, identifier in ((missing_integrity, "sha256"), (no_pin, "updatePinHash")):
        steps = " ".join(raised.value.recovery_steps)
        assert steps and not any(word in steps + str(raised.value) for word in IDENTIFIERS), steps
        assert identifier in raised.value.detail


# L4 --------------------------------------------------------------------------------------------

def test_unpublished_feed_is_a_neutral_no_release_state_in_the_ui():
    run_node(UPDATE_SETUP + r"""
    const shown = [];
    sandbox.showToast = (message, tone) => shown.push([message, tone]);
    (async () => {
      const check = sandbox.checkForUpdates();
      pendingRequests[0].resolve({channelStatus: 'no_release', error: '아직 받을 수 있는 업데이트가 없습니다.', detail: 'HTTPError: HTTP Error 404'});
      await check;
      assert.deepEqual(shown, [['아직 받을 수 있는 업데이트가 없어요', 'info']]);
      const panel = section('  const updateControls = (', '  const updateDialog = (');
      assert.match(panel, /updateStatus === 'no_release' \? \(\s*<div className="runtime-note" role="status">/);
      const tone = section('  const updateStatusTone =', '  const updateVersionLine =');
      assert.ok(!tone.includes('no_release'), 'no_release falls through to the neutral tone');
      assert.match(section('  const updateStatusLabel =', '  const updateStatusTone ='), /'no_release'\s*\?\s*'아직 없음'/);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


# C37 -------------------------------------------------------------------------------------------

def test_download_progress_is_reported_mid_download_and_reset_after_success_and_failure(tmp_path):
    body = [b"a" * 10, b"b" * 6]
    good = _FakeDownload(body)
    with patch.object(app, "urlopen", return_value=good):
        artifact = app._download_update_artifact(_mac_status(b"".join(body)), runtime_dir=tmp_path)
    assert artifact.read_bytes() == b"".join(body)
    assert good.progress_seen == [{"received": 0, "total": 16}, {"received": 10, "total": 16}, {"received": 16, "total": 16}]
    assert app._update_download_progress == {"received": 0, "total": 0}

    tampered = _FakeDownload([b"c" * 16])
    with patch.object(app, "urlopen", return_value=tampered), pytest.raises(app.AppUpdateError) as caught:
        app._download_update_artifact(_mac_status(b"".join(body)), runtime_dir=tmp_path)
    assert caught.value.code == "update_checksum_mismatch"
    assert not any(word in " ".join(caught.value.recovery_steps) for word in IDENTIFIERS)
    assert "sha256" in caught.value.detail
    assert app._update_download_progress == {"received": 0, "total": 0}


def test_progress_route_answers_while_the_install_lock_is_held():
    replies = []
    handler = SimpleNamespace(path="/api/app/update/progress", _rewrite_legacy_ui_asset_path=lambda _p: False,
                              _send_json=replies.append)
    assert app._update_install_lock.acquire(blocking=False)
    try:
        app._update_download_progress.update(received=5, total=20)
        app.AppRequestHandler.do_GET(handler)
    finally:
        app._update_download_progress.update(received=0, total=0)
        app._update_install_lock.release()
    assert replies == [{"ok": True, "received": 5, "total": 20}]


def test_install_button_label_and_polling_effect():
    run_node(NODE_SETUP + r"""
    const MB = 1024 * 1024;
    let effect, tick, cleared = 0;
    const states = [];
    const sandbox = {
      useEffect: fn => { effect = fn; },
      setUpdateInstallProgress: value => states.push(value),
      fetchAppUpdateProgress: async () => ({ ok: true, received: 10, total: 20 }),
      window: { setInterval: fn => { tick = fn; return 7; }, clearInterval: id => { assert.equal(id, 7); cleared += 1; } },
      updateInstallBusy: true,
    };
    vm.createContext(sandbox);
    vm.runInContext(section('function updateInstallProgressLabel(', '// Main copy for failed update checks')
      + section('  // The PIN install is one long request', '  const installUpdateVersion ='), sandbox);
    const label = sandbox.updateInstallProgressLabel;
    assert.equal(label(null), '검증·설치 준비 중...');
    assert.equal(label({ received: 0, total: 0 }), '검증·설치 준비 중...');
    assert.equal(label({ received: 49 * MB, total: 132 * MB }), '내려받는 중 37% (49/132MB)');
    assert.equal(label({ received: 132 * MB, total: 132 * MB }), '검증·설치 준비 중...', 'checksum and launch follow the bytes');
    (async () => {
      const cleanup = effect();
      await tick();
      assert.deepEqual(states, [null, { ok: true, received: 10, total: 20 }]);
      cleanup();
      assert.equal(cleared, 1);
      await tick();
      assert.equal(states.length, 2, 'a poll that lands after the install ends must not set progress');
      sandbox.updateInstallBusy = false;
      assert.equal(effect(), undefined);
      assert.deepEqual(states.at(-1), null);
      assert.match(source, /updateInstallBusy \? updateInstallProgressLabel\(updateInstallProgress\)/);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


# C38 -------------------------------------------------------------------------------------------

def test_translocated_mac_app_status_carries_the_move_to_applications_step():
    app.clear_app_update_status_cache()
    config = {"appId": "ClassInEDBMVP", "appName": "ClassInEDBMVP", "platform": "macos", "version": "0.2.8"}
    with (patch.object(app, "load_app_update_config", return_value=config),
          patch.object(app, "is_frozen_app", return_value=True),
          patch.object(app, "_current_macos_bundle_path", return_value=TRANSLOCATED)):
        status = app.build_app_update_status()
    app.clear_app_update_status_cache()
    assert any("응용 프로그램" in step for step in status["automaticUpdateBlockedSteps"])


def test_blocked_update_shows_server_steps_instead_of_the_download_sentence():
    run_node(NODE_SETUP + r"""
    const reason = '앱이 ‘응용 프로그램’ 폴더 밖에서 실행 중이라 자동 업데이트할 수 없습니다.';
    const step = '앱을 종료하고 ‘응용 프로그램’ 폴더로 옮긴 뒤 다시 열어 주세요.';
    const sandbox = {
      useCallback: fn => fn, updateBusy: false, updateInstallBusy: false, updateInstallBlockedReason: '', mutatingRef: { current: false },
      updateInfo: { updateAvailable: true, automaticUpdateEnabled: true, automaticUpdateReady: false,
        automaticUpdateBlockedReason: reason, automaticUpdateBlockedSteps: [step], latest: { version: '0.2.9' } },
      checkForUpdates: async () => null, setUpdateInstallBusy: () => {}, showSimpleErrorToast: () => {},
      toasts: [], showToast: (message, tone) => sandbox.toasts.push([message, tone]),
      postAppUpdateInstall: async () => { throw new Error('must not install'); },
    };
    vm.createContext(sandbox);
    vm.runInContext(section('  const installUpdateVersion =', '  const refreshSessionHistory =') + '\nglobalThis.install=installUpdateVersion;', sandbox);
    (async () => {
      assert.equal(await sandbox.install('1234', {}), false);
      assert.deepEqual(sandbox.toasts, [[`${reason} ${step}`, 'error']]);
      const panel = section('  const updateControls = (', '  const updateDialog = (');
      assert.match(panel, /updateAutomaticBlockedSteps\.length\s*\?\s*updateAutomaticBlockedSteps\.join\(' '\)\s*:\s*'‘다운로드 열기’로 새 설치파일을 받아 설치해 주세요\.'/);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


# C36 -------------------------------------------------------------------------------------------

def test_gemini_key_help_url_opens_without_update_metadata_and_other_urls_get_korean_errors():
    help_handler = _open_url_handler(app.GEMINI_KEY_HELP_URL)
    with (patch.object(app, "_allowed_update_urls", side_effect=AssertionError("no feed lookup")),
          patch.object(app.webbrowser, "open", return_value=True) as browser_open):
        help_handler._handle_open_url()
    browser_open.assert_called_once_with(app.GEMINI_KEY_HELP_URL)
    assert help_handler.statuses == [app.HTTPStatus.OK]

    other = _open_url_handler("https://example.test/elsewhere")
    with (patch.object(app, "_allowed_update_urls", return_value=set()),
          patch.object(app.webbrowser, "open", side_effect=AssertionError("must not open"))):
        other._handle_open_url()
    reply = json.loads(other.wfile.getvalue().decode("utf-8"))
    assert other.statuses == [app.HTTPStatus.FORBIDDEN]
    assert reply["code"] == "open_url_forbidden"
    assert reply["error"] == "열 수 없는 주소예요."
    assert "update metadata" in reply["detail"]
    assert f"const GEMINI_KEY_HELP_URL = '{app.GEMINI_KEY_HELP_URL}';" in APP_JSX


def test_key_help_button_falls_back_to_a_selectable_address():
    run_node(NODE_SETUP + r"""
    const errors = [];
    let reply = { ok: true, opened: true };
    const sandbox = {
      setKeySaveError: value => errors.push(value),
      postOpenUrl: async url => { sandbox.opened = url; if (reply instanceof Error) throw reply; return reply; },
    };
    vm.createContext(sandbox);
    vm.runInContext(section('const GEMINI_KEY_HELP_URL', 'async function postOpenUrl(')
      + section('  const openGeminiKeyHelp =', '  const updateCropDraft =') + '\nglobalThis.openHelp=openGeminiKeyHelp;', sandbox);
    (async () => {
      await sandbox.openHelp();
      assert.equal(sandbox.opened, 'https://aistudio.google.com/apikey');
      assert.deepEqual(errors, ['']);
      for (reply of [{ ok: true, opened: false }, new Error('열 수 없는 주소예요.')]) {
        await sandbox.openHelp();
        assert.match(errors.at(-1), /인터넷 창을 열지 못했어요.*https:\/\/aistudio\.google\.com\/apikey/);
      }
      const editor = section('            {keyEditorOpen && (', '            </SettingsSection>');
      assert.match(editor, /type="button" onClick=\{\(\) => \{ void openGeminiKeyHelp\(\); \}\}>키 발급 방법<\/button>/);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


# C35 -------------------------------------------------------------------------------------------

def test_hwpx_gap_with_html_fallback_gets_teacher_copy():
    with (
        patch.object(app.preprocess, "_iter_hwp_pdf_converter_commands", return_value=[]),
        patch.object(app.preprocess, "_iter_hwp_hwpx_converter_commands", return_value=[]),
        patch.object(app.preprocess, "_iter_pyhwp_html_converter_commands", return_value=[["/venv/bin/hwp5html"]]),
        patch.object(app.preprocess, "_iter_hwp_text_converter_commands", return_value=[]),
        patch.object(app.preprocess, "_iter_chrome_pdf_commands", return_value=[["/usr/bin/chrome"]]),
        patch.object(app.preprocess, "_iter_rhwp_core_renderer_commands", return_value=[]),
        patch.object(app.preprocess, "_iter_rhwp_python_renderer_commands", return_value=[]),
    ):
        hangul = app.describe_runtime_diagnostics()["hangul"]
    assert hangul["status"] == "partial"
    assert hangul["summary"] == "HWP 바로 열기 가능 · HWPX는 PDF로 저장해서 사용"
    assert hangul["warnings"] == ["HWPX 파일은 바로 열 수 없어요.", "한글 파일의 문항 수 사전 점검이 약해질 수 있어요."]
    assert hangul["recommendedActions"] == ["한글 프로그램에서 PDF로 저장한 뒤 그 PDF를 넣어 주세요."]
    assert any("text extractor" in note for note in hangul["technicalNotes"])


def test_hwp_settings_hide_tool_grid_and_home_pill_explains_the_action():
    run_node(NODE_SETUP + r"""
    const sandbox = {};
    vm.createContext(sandbox);
    vm.runInContext(section('function hangulRuntimeSummary(', 'function hangulRuntimeToolRows('), sandbox);
    assert.equal(sandbox.hangulRuntimeSummary({ hwpReady: false, hwpxReady: false }), 'PDF로 저장해서 사용');
    assert.equal(sandbox.hangulRuntimeSummary({ hwpReady: true, hwpxReady: true }), '바로 열기 가능');
    assert.equal(sandbox.hangulRuntimeSummary({ summary: '서버 문구' }), '서버 문구');
    assert.match(source, /<details>\s*<summary>자세한 정보<\/summary>\s*<div className="runtime-tool-grid">/);
    assert.match(source, /title=\{hangulDiagnostics\?\.recommendedActions\?\.\[0\]\}>\s*한글\(HWP\): \{hangulMeta\.label\}/);
    """)
