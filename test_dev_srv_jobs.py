"""Regression tests for the srv-jobs fixes: AI jobs beside board edits, job cancel,
quit/update busy guard, Gemini outage fallback, publish folder rename retry,
unreadable history/settings, and app.log growth."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch
from urllib import error

import pytest
from PIL import Image

import app_server
import image_reconstruction_backend as image_backend
import user_settings
from test_ui_home_flows import NODE_SETUP, run_node


@pytest.fixture
def server(tmp_path):
    with (
        patch.object(app_server, "RUNTIME_DIR", tmp_path),
        patch.object(app_server, "LATEST_SESSION_JSON", tmp_path / "latest_session.json"),
        patch.object(app_server, "SESSION_HISTORY_JSON", tmp_path / "session_history.json"),
        patch.object(app_server, "GENERATED_SESSION_JS", tmp_path / "generated_session.js"),
    ):
        srv = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
        srv.latest_session = {"problems": [], "pages": [], "session_name": "수업"}
        srv._session_revision = 1
        try:
            yield srv
        finally:
            srv.server_close()


def make_handler(srv, path, body=None):
    handler = object.__new__(app_server.AppRequestHandler)
    handler.server = srv
    handler.path = path
    handler.headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
    payload = {
        "expectedSessionRevision": srv.session_revision(),
        "expectedSessionEpoch": srv.session_epoch(),
        **(body or {}),
    }
    handler._read_json_body = lambda: payload
    handler.responses = []
    handler._send_json = lambda data, **kw: handler.responses.append((data, kw.get("status")))
    return handler


# L1 -------------------------------------------------------------------------

def test_board_edit_returns_while_ai_retry_runs_and_stale_ai_result_is_rejected(server):
    started, release = threading.Event(), threading.Event()

    def slow_retry(session, payload, *, should_cancel=None):
        started.set()
        assert release.wait(5)
        session["problems"] = [{"id": "ai-result"}]
        return session

    ai = make_handler(server, "/api/session/retry-ai")
    with patch.object(app_server, "_mutate_retry_ai", side_effect=slow_retry):
        worker = threading.Thread(target=ai._dispatch_post)
        worker.start()
        try:
            assert started.wait(5)
            assert server.active_writer_jobs() == 1
            with pytest.raises(app_server.ArtifactCleanupBusy):
                server.clear_session()  # reset stays blocked while AI runs
            edit = make_handler(server, "/api/session/mutate", {"action": "session-name", "sessionName": "새 이름"})
            edit_thread = threading.Thread(target=edit._dispatch_post)
            edit_thread.start()
            edit_thread.join(3)
            assert not edit_thread.is_alive(), "a board edit must not wait behind the AI job"
            assert edit.responses[0][0]["ok"] is True
        finally:
            release.set()
            worker.join(5)

    body, status = ai.responses[0]
    assert (body["code"], status) == ("session_conflict", HTTPStatus.CONFLICT)
    assert server.session_snapshot()["session_name"] == "새 이름"
    assert server.session_snapshot()["problems"] == []
    assert server.active_writer_jobs() == 0


def test_ai_output_paths_are_unique_per_run():
    first, second = app_server._unique_artifact_stamp(), app_server._unique_artifact_stamp()
    assert first != second
    out = Path("out")
    assert app_server._image_generation_output_path(out, "p1", first) != app_server._image_generation_output_path(out, "p1", second)
    assert app_server._managed_retry_dir(out, "page-1", first) != app_server._managed_retry_dir(out, "page-1", second)


# C23 ------------------------------------------------------------------------

def test_cancel_stops_only_the_job_it_names(server):
    enhance, recognition = server.cancel_checker("job-enhance"), server.cancel_checker("job-recognition")
    server.request_job_cancel(None)  # a cancel without a job id stops nothing
    server.request_job_cancel("job-recognition")
    assert (enhance(), recognition()) == (False, True)
    assert not server.cancel_checker("job-later")(), "an old cancel never stops a later job"
    assert not server.cancel_checker(None)()
    server.request_job_cancel("job-early")  # the cancel overtook its own request
    assert server.cancel_checker("job-early")()


def _png(path: Path, size=(64, 40)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def _enhance_session(root: Path, count: int) -> dict:
    problems = []
    for index in range(1, count + 1):
        uri = _png(root / f"p{index}.png").resolve().as_uri()
        problems.append({
            "id": f"p{index}", "imagePath": uri, "boardRenderPath": uri, "sourcePageId": "page-1",
            "bbox": {"left": 0, "top": 0, "width": 64, "height": 40}, "riskFlags": [],
        })
    return {
        "output_dir": str(root / "out"),
        "problems": problems,
        "pages": [{"id": "page-1", "problemIds": [p["id"] for p in problems]}],
    }


def _fake_ai_result(source_path, output_path, **kwargs):
    Image.new("RGBA", (128, 80), (0, 0, 0, 0)).save(output_path)
    return image_backend.ImageReconstructionResult(
        output_path=Path(output_path), provider="gemini", model=kwargs.get("model") or "m",
        prompt="", source_path=Path(source_path), latency_ms=1,
        postprocess={"content_preservation": {"status": "pass", "review_required": False, "reasons": []}},
    )


def test_cancel_stops_enhance_before_the_next_api_call_and_before_saving(server, tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    server.latest_session = _enhance_session(tmp_path, 3)
    calls = []
    started, release = threading.Event(), threading.Event()

    def blocking_reconstruct(source_path, output_path, **kwargs):
        calls.append(output_path)
        started.set()
        assert release.wait(5)
        return _fake_ai_result(source_path, output_path, **kwargs)

    handler = make_handler(
        server, "/api/session/enhance-image", {"problemIds": ["p1", "p2", "p3"], "mode": "ai", "jobId": "job-1"},
    )
    with patch.object(app_server, "reconstruct_problem_image", side_effect=blocking_reconstruct):
        worker = threading.Thread(target=handler._dispatch_post)
        worker.start()
        assert started.wait(10)
        canceller = make_handler(server, "/api/jobs/cancel", {"jobId": "job-1"})
        canceller._dispatch_post()
        release.set()
        worker.join(10)

    assert canceller.responses == [({"ok": True}, None)]
    body, status = handler.responses[0]
    assert (body["code"], status) == ("job_cancelled", HTTPStatus.CONFLICT)
    assert len(calls) == 1, "no Gemini call after the cancel"
    assert server.session_revision() == 1
    assert not (tmp_path / "latest_session.json").exists()


# C27 ------------------------------------------------------------------------

@pytest.mark.parametrize("raised,reason", [
    (error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"RESOURCE_EXHAUSTED")), "quota"),
    (error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"denied")), "key"),
    (error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b'{"reason": "API_KEY_INVALID"}')), "key"),
    (error.URLError("offline"), "network"),
    (TimeoutError("read timed out"), "network"),
    (error.HTTPError("u", 500, "Server", {}, io.BytesIO(b"oops")), None),
    (error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b"bad image")), None),
])
def test_gemini_failures_that_hit_every_call_are_marked_unavailable(tmp_path, raised, reason):
    source = _png(tmp_path / "s.png")
    with patch.object(image_backend.request, "urlopen", side_effect=raised):
        with pytest.raises(RuntimeError) as caught:
            image_backend._reconstruct_with_gemini(
                source, tmp_path / "o.png", api_key="k", model="m", prompt="p", size="1k",
                timeout_ms=1000, transparent_background=True, sharpen=False,
            )
    assert "Gemini image reconstruction failed" in str(caught.value)
    assert getattr(caught.value, "reason", None) == reason
    assert isinstance(caught.value, image_backend.ImageApiUnavailable) == (reason is not None)


def test_enhance_stops_calling_gemini_after_quota_error_and_reports_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(app_server, "_global_ai_enabled", lambda: True)
    session = _enhance_session(tmp_path, 3)
    quota = image_backend.ImageApiUnavailable("Gemini image reconstruction failed (429): quota", "quota")
    with patch.object(app_server, "reconstruct_problem_image", side_effect=quota) as reconstruct:
        updated = app_server._mutate_enhance_image(session, {"problemIds": ["p1", "p2", "p3"], "mode": "ai"})

    assert reconstruct.call_count == 1
    rows = updated["ai_image_reconstruction_summary"]
    assert [row["deliveryMode"] for row in rows] == ["content_safe_fallback"] * 3
    assert rows[1]["attempts"][0]["status"] == "skipped"
    assert "사용량" in updated["ai_image_reconstruction_unavailable"]


def test_enhance_keeps_trying_gemini_after_a_per_image_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(app_server, "_global_ai_enabled", lambda: True)
    session = _enhance_session(tmp_path, 2)
    session["ai_image_reconstruction_unavailable"] = "stale notice from a previous run"
    no_image = RuntimeError("Gemini image reconstruction returned no image data (finish=SAFETY)")
    with patch.object(app_server, "reconstruct_problem_image", side_effect=no_image) as reconstruct:
        updated = app_server._mutate_enhance_image(session, {"problemIds": ["p1", "p2"], "mode": "ai"})

    assert reconstruct.call_count == 2
    assert "ai_image_reconstruction_unavailable" not in updated


# L3 -------------------------------------------------------------------------

def test_quit_is_refused_while_a_job_writes_files_unless_forced(server):
    stopped = threading.Event()
    server.request_shutdown = lambda reason: stopped.set()
    server.begin_artifact_read()  # an open thumbnail never blocks quitting
    server.begin_artifact_job(exclusive=False)
    try:
        handler = make_handler(server, "/api/system/shutdown")
        handler._handle_shutdown()
        body, status = handler.responses[0]
        assert (body["code"], status) == ("shutdown_busy", HTTPStatus.CONFLICT)
        assert "작업" in body["error"]
        assert not stopped.wait(0.5)

        forced = make_handler(server, "/api/system/shutdown", {"force": True})
        forced._handle_shutdown()
        assert forced.responses == [({"ok": True}, None)]
        assert stopped.wait(2)
    finally:
        server.end_artifact_job(exclusive=False)
        server.end_artifact_read()


@pytest.mark.parametrize("path", ["/api/system/shutdown", "/api/jobs/cancel"])
def test_quit_and_cancel_from_another_site_are_refused_in_korean(server, path):
    stopped = threading.Event()
    server.request_shutdown = lambda reason: stopped.set()
    cancelled = server.cancel_checker("job-1")
    handler = make_handler(server, path, {"jobId": "job-1"})
    handler.headers = {"Host": "127.0.0.1:8765", "Referer": "https://example.test/page"}
    handler._dispatch_post()
    body, status = handler.responses[0]
    assert (body["code"], status) == ("cross_origin_rejected", HTTPStatus.FORBIDDEN)
    assert body["error"] == "이 작업은 앱 화면에서만 할 수 있어요." and body["detail"]
    assert not stopped.wait(0.2) and not cancelled()


def test_update_install_is_refused_before_the_installer_starts_while_busy(server):
    server.begin_artifact_job()
    try:
        handler = make_handler(server, "/api/app/update/install", {"pin": "4826", "expectedUpdate": {"version": "9"}})
        with patch.object(app_server, "begin_app_update") as begin:
            handler._handle_app_update_install()
        begin.assert_not_called()
        assert handler.responses[0][0]["code"] == "shutdown_busy"
    finally:
        server.end_artifact_job()


# C26 ------------------------------------------------------------------------

def test_publish_folder_rename_waits_out_a_brief_scanner_lock(server, tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "lesson.edb").write_bytes(b"edb")
    final = tmp_path / "published" / "gen-1"
    real_replace, attempts = os.replace, []

    def locked_twice(source, target):
        attempts.append(Path(target))
        if len(attempts) <= 2:
            exc = PermissionError(13, "in use")
            exc.winerror = 32
            raise exc
        return real_replace(source, target)

    monkeypatch.setattr(app_server.os, "replace", locked_twice)
    monkeypatch.setattr(app_server.time, "sleep", lambda _s: None)
    base, revision = server.session_snapshot_with_revision()
    committed = server.adopt_staged_publish_if_current(
        base, {**base, "marker": "published"}, staging_dir=staging, final_dir=final, expected_revision=revision,
    )
    assert committed == revision + 1
    assert (final / "lesson.edb").read_bytes() == b"edb"
    assert attempts[:3] == [final, final, final]


# C25 ------------------------------------------------------------------------

def test_locked_history_file_is_left_alone_when_a_lesson_is_saved(server, tmp_path, monkeypatch):
    history = tmp_path / "session_history.json"
    history.write_text(json.dumps([{"id": "older", "session_name": "지난 수업"}]), encoding="utf-8")
    before = history.read_bytes()
    real_read_text = Path.read_text

    def locked_history(self, *args, **kwargs):
        if self == history:
            exc = PermissionError(13, "in use")
            exc.winerror = 32
            raise exc
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", locked_history)
    monkeypatch.setattr(app_server.time, "sleep", lambda _s: None)
    base, revision = server.session_snapshot_with_revision()
    assert server.remember_session_if_current(base, {**base, "session_name": "새 수업"}, expected_revision=revision)
    assert history.read_bytes() == before
    assert json.loads((tmp_path / "latest_session.json").read_text(encoding="utf-8"))["session_name"] == "새 수업"


def test_locked_settings_file_fails_the_save_instead_of_dropping_the_key(tmp_path, monkeypatch):
    user_settings.save_user_settings(tmp_path, {"gemini_api_key": "kept-key", "ai_enabled": True})
    path = user_settings.settings_path(tmp_path)
    real_read_text = Path.read_text

    def locked(self, *args, **kwargs):
        if self == path:
            exc = PermissionError(13, "in use")
            exc.winerror = 32
            raise exc
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(user_settings.time, "sleep", lambda _s: None)
    with patch.object(Path, "read_text", locked):
        with pytest.raises(OSError):
            user_settings.update_ai_enabled(tmp_path, False)
    assert user_settings.load_user_settings(tmp_path) == {"gemini_api_key": "kept-key", "ai_enabled": True}
    # A missing file is still a fresh start, not an error.
    path.unlink()
    assert user_settings.update_ai_enabled(tmp_path, False)["aiEnabled"] is False


# L11 ------------------------------------------------------------------------

def test_app_log_is_rotated_in_place_while_the_app_runs(tmp_path, monkeypatch):
    log = tmp_path / "app.log"
    original = (sys.stdout, sys.stderr)
    monkeypatch.setattr(app_server, "APP_LOG_MAX_BYTES", 200)
    monkeypatch.setattr(app_server, "_app_log_stream", None)  # teardown puts the old value back
    try:
        app_server.configure_app_logging(log)
        print("x" * 300)
        app_server._rotate_app_log_if_large()
        print("after rotation")
    finally:
        sys.stdout, sys.stderr = original
    app_server._app_log_stream.close()
    assert "x" * 300 in (tmp_path / "app.log.1").read_text(encoding="utf-8")
    assert log.read_text(encoding="utf-8") == "after rotation\n"


def test_successful_reads_are_not_logged_but_writes_and_failures_are():
    handler = object.__new__(app_server.AppRequestHandler)
    handler.requestline = "GET /api/health HTTP/1.1"
    logged = []
    handler.log_message = lambda fmt, *args: logged.append(fmt % args)
    handler.command = "GET"
    handler.log_request(HTTPStatus.OK)
    handler.log_request(304)
    # Export and update progress are polled every 1-1.5 s.
    for path in ("/api/export/progress?id=abc", "/api/app/update/progress"):
        handler.requestline = f"GET {path} HTTP/1.1"
        handler.log_request(HTTPStatus.OK)
    assert logged == []
    handler.log_request(HTTPStatus.NOT_FOUND)
    handler.command = "POST"
    handler.log_request(HTTPStatus.OK)
    assert len(logged) == 2


# UI -------------------------------------------------------------------------

def test_quit_asks_again_and_forces_when_the_app_is_still_busy() -> None:
    run_node(NODE_SETUP + r"""
    const calls = [], prompts = [], toasts = [];
    let answers = [true, true];
    const sandbox = {
      useRef: value => ({ current: value }), useCallback: fn => fn, useEffect() {},
      showToast: message => toasts.push(message), showSimpleErrorToast() { throw new Error('no error toast'); },
      sessionEditState: { dirty: false }, workInProgress: false,
      window: { addEventListener() {}, removeEventListener() {}, confirm: message => { prompts.push(message); return answers.shift(); } },
      postShutdown: async force => {
        calls.push(Boolean(force));
        if (!force) { const error = new Error('busy'); error.code = 'shutdown_busy'; throw error; }
        return { ok: true };
      },
    };
    vm.createContext(sandbox);
    vm.runInContext('{' + section('  // Warn before the tab closes', '  const refreshSession = useCallback(')
      + '\nglobalThis.shutdownApp = shutdownApp;}', sandbox);
    (async () => {
      await sandbox.shutdownApp();
      assert.deepEqual(calls, [false, true]);
      assert.match(prompts[1], /아직 작업이 진행 중/);
      assert.match(toasts[0], /앱을 종료합니다/);
      calls.length = 0; answers = [true, false];
      await sandbox.shutdownApp();
      assert.deepEqual(calls, [false], 'declining the second question keeps the app running');
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_cancel_also_stops_the_apps_ai_work() -> None:
    run_node(NODE_SETUP + r"""
    const posts = [];
    const sandbox = {
      useCallback: fn => fn, jobControllersRef: { current: new Map() }, settleBackgroundJob() {}, showToast() {},
      fetch: async (url, options) => { posts.push([url, options.method, JSON.parse(options.body).jobId]); return { ok: true }; },
      backgroundJobs: [{ id: 'a', scope: 'image-enhance' }, { id: 'b', scope: 'queue-recognition' }, { id: 'c', scope: 'session-recognition' }],
    };
    vm.createContext(sandbox);
    vm.runInContext(section('  const cancelBackgroundJob = useCallback(', '\n  useEffect(() => {')
      + '\nglobalThis.cancelBackgroundJob = cancelBackgroundJob;', sandbox);
    sandbox.cancelBackgroundJob('a');
    sandbox.cancelBackgroundJob('b');
    sandbox.cancelBackgroundJob('c');
    // Each cancel names its own job, so a second running AI job keeps going.
    assert.deepEqual(posts, [['/api/jobs/cancel', 'POST', 'a'], ['/api/jobs/cancel', 'POST', 'c']]);
    """)


def test_ai_requests_carry_the_job_id_their_cancel_names() -> None:
    run_node(NODE_SETUP + r"""
    const bodies = [];
    const sandbox = {
      fetch: async (url, options) => { bodies.push([url, JSON.parse(options.body).jobId]); return {}; },
      withExpectedSessionRevision: payload => payload, expectOkJson: async () => ({ ok: true }),
    };
    vm.createContext(sandbox);
    vm.runInContext(section('async function postRetryAi(', '\n// AI jobs do not hold up board edits')
      + '\nglobalThis.postRetryAi = postRetryAi; globalThis.postEnhanceImage = postEnhanceImage;', sandbox);
    assert.match(section('  const retryAiSession = useCallback(', '  const recognizeCurrentSession = useCallback('),
      /postRetryAi\(args, \{[^}]*jobId: job\.id \}\)/);
    (async () => {
      await sandbox.postRetryAi({ pageIds: ['page-1'] }, { preview: true, jobId: 'job-r' });
      await sandbox.postEnhanceImage({ problemIds: ['p1'] }, { jobId: 'job-e' });
      assert.deepEqual(bodies, [['/api/session/retry-ai', 'job-r'], ['/api/session/enhance-image', 'job-e']]);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_enhance_summary_counts_fallbacks_as_text_preserve_and_explains_conflicts() -> None:
    run_node(NODE_SETUP + r"""
    const toasts = [], settled = [], jobIds = [];
    let reply;
    const sandbox = {
      useCallback: fn => fn, session: { problems: [] }, aiEnabled: true, userSettings: { hasGeminiApiKey: true },
      hasRunningBackgroundJobs: false, setSession() {}, sessionEditorDraftSignature: () => '',
      items: [], fileName: '수업', boardColumns: 1, layoutGapMode: 'compact', UNDO_HISTORY_LIMIT: 5,
      listUnique: values => [...new Set(values)], materializeSessionForItems: () => ({}), cloneSession: value => value,
      startBackgroundJob: () => ({ id: 'job', controller: { signal: { aborted: false } } }),
      settleBackgroundJob: (_id, patch) => settled.push(patch), setHistoryStack() {}, appendBoundedHistory: list => list,
      adoptMutatedSession() {}, showToast: (message, type) => toasts.push([message, type || 'info']),
      showSimpleErrorToast() { throw new Error('no generic error toast'); },
      postEnhanceImage: async (_args, options) => { jobIds.push(options.jobId); return reply(); },
      postRestore: async () => ({}),  // dev/ui-undo saves unsaved board edits before enhancing
    };
    vm.createContext(sandbox);
    vm.runInContext(section('const AI_RESULT_BOARD_CHANGED_MESSAGE', 'async function postRestore(')
      + section('  const enhanceImageSession = useCallback(', '  const resetSession = useCallback(')
      + '\nglobalThis.enhanceImageSession = enhanceImageSession;', sandbox);
    (async () => {
      reply = async () => ({ session: {}, aiUnavailable: 'Gemini 사용량 한도에 걸려 AI 대신 글자 보존 방식으로 처리했어요.', enhance: [
        { status: 'applied', resolvedMode: 'ai', deliveryMode: 'content_safe_fallback' },
        { status: 'applied', resolvedMode: 'ai', deliveryMode: 'ai_primary' },
      ] });
      await sandbox.enhanceImageSession(['p1', 'p2'], { mode: 'ai' });
      assert.deepEqual(jobIds, ['job'], 'the request names the job a cancel would stop');
      assert.deepEqual(toasts.pop(), ['Gemini 사용량 한도에 걸려 AI 대신 글자 보존 방식으로 처리했어요.', 'error']);
      assert.equal(settled.pop().hint, '글자 보존 1 · AI 1 완료');

      reply = async () => ({ session: {}, enhance: [
        { status: 'applied', resolvedMode: 'ai', deliveryMode: 'content_safe_fallback' },
        { status: 'applied', resolvedMode: 'preserve', deliveryMode: 'content_safe_primary' },
      ] });
      await sandbox.enhanceImageSession(['p1', 'p2'], { mode: 'auto' });
      assert.deepEqual(toasts.pop(), ['고화질 적용 · 글자 보존 2', 'info']);

      reply = async () => { const error = new Error('conflict'); error.code = 'session_conflict'; throw error; };
      await sandbox.enhanceImageSession(['p1'], { mode: 'ai' });
      assert.deepEqual(toasts.pop(), ['작업하는 동안 칠판이 바뀌어서 결과를 적용하지 않았어요. 다시 실행해 주세요.', 'error']);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)
