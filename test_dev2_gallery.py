"""Round-2 gallery (문제 추출) fixes: an installed Tesseract off PATH is found and a
missing engine is a quiet install note (R20), the automatic pass keeps extraction
failures (R38), a queued file that changed on disk is named as such (R43), sources
can be deleted and old ZIPs are pruned (R55), gallery jobs no longer take the
lesson lock (R56), gallery errors are coded and logged (R57), and keyboard and
screen-reader fixes (R58)."""
from __future__ import annotations

import errno
import io
import json
import os
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import pytest

import app_server
import gallery_recognition
import ocr_backend
from problem_gallery import GalleryStore
from test_dev_srv_jobs import make_handler, server  # noqa: F401  (server is a pytest fixture)
from test_problem_gallery import fake_parse, pdf_bytes
from test_stabilize_server_errors import _handler
from test_ui_home_flows import NODE_SETUP, run_node
from test_ui_problem_gallery import GALLERY_SETUP


@contextmanager
def _gallery_server(runtime_dir: Path):
    with mock.patch.object(app_server, "RUNTIME_DIR", runtime_dir), \
            mock.patch.object(app_server, "load_latest_session", return_value=None), \
            mock.patch.object(app_server, "load_session_history", return_value=[]), \
            mock.patch.object(app_server, "_recover_interrupted_session_reset"):
        srv = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        def request(method, path, body=None, headers=None):
            connection = HTTPConnection(*srv.server_address, timeout=60)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            connection.close()
            return response.status, data

        try:
            yield request
        finally:
            srv.shutdown()
            srv.server_close()


def _extracted(store: GalleryStore, pages: int = 2) -> dict:
    data = pdf_bytes(pages)
    job = store.upload(io.BytesIO(data), len(data), "교재.pdf")
    with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
        return store.parse_next(job["id"])


# R20 ------------------------------------------------------------------------

def test_tesseract_probe_finds_a_standard_install_off_path(monkeypatch, tmp_path):
    class FakePytesseractModule:
        class pytesseract:
            tesseract_cmd = "tesseract"

        def get_tesseract_version(self):
            if self.pytesseract.tesseract_cmd == "tesseract":
                raise RuntimeError("tesseract is not installed or it's not in your PATH")
            return "5.4"

    fake = FakePytesseractModule()
    installed = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    installed.parent.mkdir()
    installed.write_bytes(b"")
    monkeypatch.setattr(ocr_backend, "pytesseract", fake)
    monkeypatch.setattr(ocr_backend, "PaddleOCR", None)
    monkeypatch.setattr(ocr_backend.sys, "platform", "win32")
    monkeypatch.setattr(ocr_backend.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    ocr_backend.clear_ocr_capability_cache()
    try:
        # The Windows installer puts Tesseract in Program Files without touching PATH.
        assert ocr_backend._tesseract_binary_available()
        assert fake.pytesseract.tesseract_cmd == str(installed)
        assert ocr_backend.preferred_ocr_backend_name("local") == "tesseract"

        # A Finder-launched Mac app does not get Homebrew's PATH either.
        fake.pytesseract.tesseract_cmd = "tesseract"
        monkeypatch.setattr(ocr_backend.sys, "platform", "darwin")
        monkeypatch.setattr(ocr_backend.os.path, "isfile", lambda path: path == "/opt/homebrew/bin/tesseract")
        assert ocr_backend._tesseract_binary_available()
        assert fake.pytesseract.tesseract_cmd == "/opt/homebrew/bin/tesseract"
    finally:
        ocr_backend.clear_ocr_capability_cache()


def test_an_engine_too_old_for_pytesseract_counts_as_missing(monkeypatch, tmp_path):
    class FakePytesseractModule:
        class pytesseract:
            tesseract_cmd = "tesseract"

        def get_tesseract_version(self):
            # pytesseract exits instead of raising below 3.05, e.g. UB Mannheim's 3.05.00dev.
            raise SystemExit('Invalid tesseract version: "tesseract 3.05.00dev"')

    fake = FakePytesseractModule()
    old = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    old.parent.mkdir()
    old.write_bytes(b"")
    monkeypatch.setattr(ocr_backend, "pytesseract", fake)
    monkeypatch.setattr(ocr_backend, "PaddleOCR", None)
    monkeypatch.setattr(ocr_backend.sys, "platform", "win32")
    monkeypatch.setattr(ocr_backend.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    ocr_backend.clear_ocr_capability_cache()
    ocr_backend._tesseract_tool_version.cache_clear()
    try:
        # Otherwise the SystemExit ends the request thread with no response and no log line.
        assert ocr_backend._tesseract_binary_available() is False
        assert fake.pytesseract.tesseract_cmd == str(old)
        assert ocr_backend.build_ocr_backend("local").name == "none"
        assert ocr_backend._tesseract_tool_version() == "unavailable"
    finally:
        ocr_backend.clear_ocr_capability_cache()
        ocr_backend._tesseract_tool_version.cache_clear()


def test_missing_ocr_is_a_coded_install_note_without_a_forced_reprobe():
    with mock.patch("gallery_recognition.build_ocr_backend", return_value=ocr_backend.NoOcrBackend()) as build:
        with pytest.raises(ValueError) as caught:
            gallery_recognition.local_label_backend()
    build.assert_called_once_with("local")
    assert caught.value.code == "ocr_unavailable"
    message = str(caught.value)
    assert "Tesseract" in message and "번호·지문 다시 인식" in message
    assert "PaddleOCR" not in message and "로컬" not in message
    assert "PaddleOCR" in caught.value.detail


def test_recognize_labels_without_ocr_is_a_coded_400_and_changes_no_record(tmp_path):
    with _gallery_server(tmp_path) as request:
        status, data = request("POST", "/api/parser/upload?name=scan.pdf", pdf_bytes(1), {"Content-Type": "application/octet-stream"})
        assert status == 201, data
        job = json.loads(data)["job"]
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
            status, data = request("POST", "/api/parser/next", json.dumps({"jobId": job["id"]}), {"Content-Type": "application/json"})
        assert status == 200, data
        problem_id = json.loads(data)["job"]["problems"][0]["id"]
        store = GalleryStore(tmp_path / "problem-gallery")
        unread = store.load(job["id"])
        unread["problems"][0].pop("problemNumberSource")  # a crop whose number was never read
        store.save(unread)
        with mock.patch("gallery_recognition.build_ocr_backend", return_value=ocr_backend.NoOcrBackend()):
            status, data = request("POST", "/api/parser/recognize-labels",
                json.dumps({"problemIds": [problem_id], "automatic": True}), {"Content-Type": "application/json"})
    assert status == 400, data
    payload = json.loads(data)
    assert payload["code"] == "ocr_unavailable"
    assert "Tesseract" in payload["error"] and "PaddleOCR" not in payload["error"]
    assert "PaddleOCR" in payload["detail"]
    assert "labelRecognitionAttempted" not in store.load(job["id"])["problems"][0], "the next open tries again"


def test_automatic_pass_without_ocr_shows_an_install_note_not_an_alert():
    run_node(GALLERY_SETUP + r"""
    (async()=>{
      const INSTALL="사진·스캔 자료의 번호를 읽으려면 Tesseract와 한국어 언어 데이터를 설치한 뒤 '번호·지문 다시 인식'을 눌러 주세요.";
      const base=sandbox.fetch;
      sandbox.fetch=async(url,options)=>{
        if(url.endsWith('/recognize-labels')){
          requests.push({url,payload:JSON.parse(options.body)});
          throw Object.assign(new Error(INSTALL),{code:'ocr_unavailable'});
        }
        return base(url,options);
      };
      serverJobs=[{id:'source',sourceName:'scan.png',sourcePageCount:1,processedPages:1,status:'complete',
        problems:[1,2,3].map(i=>({...makeProblem(i),number:null,problemNumberSource:'',riskFlags:['number_unconfirmed']}))}];
      const auto=async()=>{render();effects.at(-1)();await new Promise(setImmediate);return render();};
      const calls=()=>requests.filter(r=>r.url.endsWith('/recognize-labels')).length;
      const alerts=tree=>nodes(tree).filter(n=>n.props?.role==='alert');
      await mount();
      let tree=await auto();
      assert.equal(calls(),1);
      assert.ok(text(tree).includes('Tesseract'),'the status line carries the install note');
      assert.equal(alerts(tree).length,0,'opening the gallery without OCR must not raise an alert');
      await auto();
      assert.equal(calls(),1,'the automatic pass is not repeated');
      await button(render(),'번호·지문 다시 인식').props.onClick();
      assert.equal(calls(),2);
      tree=render();
      assert.equal(alerts(tree).length,1,'an explicit retry still alerts');
      assert.ok(text(alerts(tree)[0]).includes('Tesseract'));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


# R38 ------------------------------------------------------------------------

def test_automatic_recognition_keeps_extraction_failures():
    run_node(GALLERY_SETUP + r"""
    (async()=>{
      const base=sandbox.fetch;
      sandbox.fetch=async(url,options)=>{
        if(url.includes('/upload?'))throw new Error('읽을 수 없는 PDF입니다. 원본 파일을 확인해 주세요.');
        const response=await base(url,options);
        if(url.endsWith('/next'))serverJobs.forEach(job=>job.problems.forEach(problem=>{problem.problemNumberSource='';}));
        return response;
      };
      const readable={arrayBuffer:async()=>new ArrayBuffer(1)};
      const auto=async()=>{render();effects.at(-1)();await new Promise(setImmediate);return render();};
      const extract=async()=>{
        serverJobs=[{id:'source',sourceName:'source.pdf',sourcePageCount:1,processedPages:0,status:'pending',problems:[]}];
        hooks=[];await mount();
        nodes(render()).find(n=>n.type==='input'&&n.props.type==='file').props.onChange({target:{files:[{name:'broken.pdf',size:10,slice:()=>readable}]}});
        await button(render(),'이어서 추출').props.onClick();
        await auto();
      };
      const reason='broken.pdf: 읽을 수 없는 PDF입니다';
      await extract();
      assert.equal(requests.filter(r=>r.url.endsWith('/recognize-labels')).at(-1).payload.automatic,true);
      assert.ok(text(render()).includes(reason),'automatic recognition must not erase the run failure');
      failLabels=true;await extract();
      assert.ok(text(render()).includes(reason)&&text(render()).includes('OCR 엔진 확인 필요'),'an automatic failure is added, not swapped in');
      failLabels=false;
      serverJobs[0].problems.forEach(problem=>{problem.labelRecognitionAttempted=false;});
      await button(render(),'번호·지문 다시 인식').props.onClick();
      assert.ok(!text(render()).includes(reason),'an explicit retry starts from a clean message');
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


# R43 ------------------------------------------------------------------------

def test_a_queued_file_changed_on_disk_is_named_before_any_upload():
    run_node(NODE_SETUP + r"""
    let calls = [];
    const sandbox = {
      formatBytes: bytes => `${bytes / 1048576}MB`,
      readJsonResponse: async response => response.payload,
      operationErrorFromResponse: payload => Object.assign(new Error(payload.error), payload),
      fetch: async (url, options) => { calls.push(options.body); return { ok: true, payload: { ok: true, sourcePath: `/uploads/${options.body.name}` } }; },
    };
    vm.runInNewContext(section('const MAX_EXPORT_FILE_BYTES', 'function fileQueueKey'), sandbox);
    (async () => {
      const readable = { arrayBuffer: async () => new ArrayBuffer(1) };
      const fresh = { name: '시험지.pdf', size: 1048576, slice: () => readable };
      // Chrome and Edge would fail the upload of this file as 'Failed to fetch'.
      const stale = { name: '고친 시험지.pdf', size: 1048576, slice: () => ({ arrayBuffer: async () => {
        throw Object.assign(new Error('The requested file could not be read'), { name: 'NotReadableError' });
      } }) };
      const isStale = error => error.code === 'upload_source_changed' && error.retryable === false
        && error.sourceFile === stale.name && error.detail.startsWith('NotReadableError')
        && error.recoverySteps[0].includes('선택 파일 교체') && !/fetch|network/i.test(error.message);
      await assert.rejects(sandbox.uploadSourceFiles([stale]), isStale);
      assert.equal(calls.length, 0, 'never send a file the browser can no longer read');
      await assert.rejects(sandbox.uploadSourceFiles([fresh, stale]), isStale);
      assert.equal(calls.length, 1, 'the batch stops at the file that went stale');
    })().catch(error => { console.error(error); process.exitCode = 1; });

    const Babel = require('./scripts/vendor/babel.min.js');
    const banner = {
      React: { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }), Fragment: 'fragment' },
      useState: initial => [initial, () => {}], useEffect() {},
      Icon: new Proxy({}, { get: (_, name) => String(name) }), IS_MAC_PLATFORM: false,
      probeLocalServerHealth: async () => true,
    };
    vm.createContext(banner);
    vm.runInContext(Babel.transform(section('const OPERATION_RECOVERY_SUMMARIES', 'function operationRecoveryStateTransition(')
      + section('function OperationRecoveryBanner(', 'function LoadingOverlay('), { presets: ['react'] }).code, banner);
    const words = node => typeof node === 'string' ? node : (node?.children || []).map(words).join(' ');
    const shown = words(banner.OperationRecoveryBanner({ onRetry() {}, recovery: { kind: 'recognition', error: {
      code: 'upload_source_changed', retryable: false, message: '대기열에 넣은 뒤 원본 파일이 바뀌었거나 옮겨졌습니다. 파일을 다시 선택해 주세요.',
      recoverySteps: ['‘원본 파일’ 목록에서 이 파일을 고른 뒤 ‘선택 파일 교체…’로 같은 파일을 다시 선택해 주세요.'] } } }));
    assert.ok(shown.includes('대기열의 파일을 읽을 수 없습니다'), shown);
    assert.ok(!shown.includes('원본 파일과 대기열은 그대로 있습니다'), shown);
    assert.ok(shown.includes('선택 파일 교체'), shown);
    """)


def test_gallery_names_a_queued_file_changed_on_disk_without_uploading_it():
    run_node(GALLERY_SETUP + r"""
    (async()=>{
      let uploads=0;
      const base=sandbox.fetch;
      sandbox.fetch=async(url,options)=>{
        if(url.includes('/upload?')){uploads++;throw new TypeError('Failed to fetch');}
        return base(url,options);
      };
      serverJobs=[];
      await mount();
      const stale={name:'고친 시험지.pdf',size:10,slice:()=>({arrayBuffer:async()=>{throw Object.assign(new Error('could not be read'),{name:'NotReadableError'});}})};
      const pick=file=>nodes(render()).find(n=>n.type==='input'&&n.props.type==='file').props.onChange({target:{files:[file]}});
      const queued=()=>nodes(render()).filter(n=>n.props?.className==='gallery-pending-file').length;
      pick(stale);
      await button(render(),'문제 추출하기').props.onClick();
      assert.equal(uploads,0);
      assert.ok(text(render()).includes('고친 시험지.pdf: 대기열에 넣은 뒤 원본 파일이 바뀌었거나 옮겨졌습니다'), text(render()));
      assert.ok(!text(render()).includes('앱과 연결이 끊겼습니다'));
      assert.equal(queued(),0,'the stale entry leaves 업로드 대기');
      pick(stale);
      assert.equal(queued(),1,'picking the same name, size and date again queues the file');
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


# R55 ------------------------------------------------------------------------

def test_delete_removes_the_source_its_crops_and_cached_zips_only(tmp_path):
    store = GalleryStore(tmp_path / "gallery")
    kept, gone = _extracted(store), _extracted(store)
    ids = [problem["id"] for problem in gone["problems"]]
    store.export_images(ids[:1])
    store.export_images(ids)
    assert len(list((store.root / "exports").rglob("*.zip"))) == 2
    store.delete_job(gone["id"])
    assert not store.job_dir(gone["id"]).exists()
    assert not (store.root / "exports").exists()
    assert [job["id"] for job in store.list_jobs()] == [kept["id"]]
    assert (store.job_dir(kept["id"]) / kept["problems"][0]["imageFile"]).is_file()
    store.delete_job(gone["id"])  # a second click or another tab is harmless
    with pytest.raises(ValueError):
        store.delete_job("../outputs")


def test_zips_older_than_an_hour_are_pruned_by_the_next_save(tmp_path):
    store = GalleryStore(tmp_path / "gallery")
    ids = [problem["id"] for problem in _extracted(store)["problems"]]
    old, _ = store.export_images(ids[:1])
    hour_ago = time.time() - 3700
    os.utime(old.parent, (hour_ago, hour_ago))
    new, _ = store.export_images(ids)
    assert not old.exists()
    assert new.is_file()
    assert store.export_images(ids)[0] == new, "a recent ZIP is still reused"


def test_delete_endpoint_is_same_origin_coded_and_reports_a_locked_folder(tmp_path):
    with _gallery_server(tmp_path) as request:
        json_type = {"Content-Type": "application/json"}
        status, data = request("POST", "/api/parser/upload?name=exam.pdf", pdf_bytes(1), {"Content-Type": "application/octet-stream"})
        job = json.loads(data)["job"]
        body = json.dumps({"jobId": job["id"]})
        status, data = request("POST", "/api/parser/delete", body, {**json_type, "Origin": "https://evil.example"})
        assert status == 403, data
        with mock.patch.object(GalleryStore, "delete_job", side_effect=PermissionError(32, "in use")):
            status, data = request("POST", "/api/parser/delete", body, json_type)
        assert status == 409, data
        locked = json.loads(data)
        assert locked["code"] == "gallery_delete_failed" and "in use" in locked["detail"]
        assert "in use" not in locked["error"]
        status, data = request("POST", "/api/parser/delete", body, json_type)
        assert (status, json.loads(data)) == (200, {"ok": True})
        assert json.loads(request("GET", "/api/parser/jobs")[1])["jobs"] == []
        status, data = request("POST", "/api/parser/delete", json.dumps({"jobId": "x"}), json_type)
        assert status == 400, data


def test_deleting_a_source_asks_first_and_drops_its_cards_and_selection():
    run_node(GALLERY_SETUP + r"""
    (async () => {
      let deletes = [], confirmAnswer = false, prompts = [], holdExport = false, releaseExport = null;
      sandbox.window = { confirm: message => { prompts.push(message); return confirmAnswer; } };
      const baseFetch = sandbox.fetch;
      sandbox.fetch = async (url, options) => {
        if (url.endsWith('/delete')) {
          const payload = JSON.parse(options.body);
          deletes.push(payload.jobId);
          serverJobs = serverJobs.filter(job => job.id !== payload.jobId);
          return { json: async () => ({ ok: true }) };
        }
        if (url.endsWith('/export-images') && holdExport) await new Promise(resolve => { releaseExport = resolve; });
        return baseFetch(url, options);
      };
      serverJobs = [
        { id: 'old', sourceName: 'old.pdf', sourcePageCount: 30, processedPages: 12, status: 'partial', problems: Array.from({ length: 12 }, (_, i) => makeProblem(i + 1, 'old')) },
        { id: 'kept', sourceName: 'kept.pdf', sourcePageCount: 1, processedPages: 1, status: 'complete', problems: [makeProblem(1, 'kept')] },
      ];
      let tree = await mount();
      assert.ok(text(tree).includes('이어서 추출'), 'the abandoned 12/30 source keeps the main button on resume');
      const gallerySource = label => nodes(render()).find(node => node.type === 'button' && node.props.className?.startsWith('gallery-source ') && text(node).includes(label));
      const deleteOf = name => nodes(render()).find(node => node.type === 'button' && node.props['aria-label'] === `${name} 자료 삭제`);
      // Select every card of old.pdf and the kept card, then filter to old.pdf.
      gallerySource('old.pdf').props.onClick();
      nodes(render()).find(node => node.type === 'input' && node.props.type === 'checkbox').props.onChange();
      gallerySource('전체 문제').props.onClick();
      const keptCard = nodes(render()).find(node => node.type === 'article' && node.props['data-gallery-id'] === 'kept:p1');
      nodes(keptCard).find(node => node.type === 'input').props.onChange({});
      gallerySource('old.pdf').props.onClick();
      await deleteOf('old.pdf').props.onClick();
      assert.deepEqual(deletes, [], 'a cancelled confirm deletes nothing');
      assert.ok(prompts[0].includes("'old.pdf' 자료와 추출한 문제 12개") && prompts[0].includes('원본 파일'), prompts[0]);
      confirmAnswer = true;
      let focused = null;
      gallerySource('전체 문제').props.ref.current = { focus: options => { focused = options; } };
      await deleteOf('old.pdf').props.onClick();
      assert.deepEqual(deletes, ['old']);
      assert.equal(focused?.preventScroll, true, 'the removed 삭제 button hands focus to 전체 문제');
      tree = render();
      assert.ok(!gallerySource('old.pdf'), 'the deleted source leaves the list');
      assert.equal(gallerySource('전체 문제').props['aria-pressed'], true, 'the filter falls back to every source');
      assert.ok(text(tree).includes("'old.pdf' 자료를 지웠습니다."));
      assert.ok(text(tree).includes('문제 추출하기'), 'no pending source is left to resume');
      holdExport = true;
      const saving = button(render(), '선택 1개 PNG 받기').props.onClick();
      await new Promise(setImmediate);
      assert.equal(deleteOf('kept.pdf').props.disabled, true, 'no delete while a save runs');
      releaseExport(); await saving;
      assert.deepEqual(requests.at(-1).payload.problemIds, ['kept:p1'], 'deleted ids leave the selection');
      assert.equal(deleteOf('kept.pdf').props.disabled, false);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


# R56 ------------------------------------------------------------------------

def test_board_edit_returns_while_a_gallery_batch_runs_and_gallery_jobs_still_queue(server):  # noqa: F811
    started, release, calls = threading.Event(), threading.Event(), []

    def slow_batch(self, job_id, expected_processed_pages=None):
        calls.append(job_id)
        started.set()
        assert release.wait(5)
        raise ValueError("묶음 중지")

    batch = {"jobId": "a" * 32, "expectedProcessedPages": 0}
    gallery, other_tab = make_handler(server, "/api/parser/next", batch), make_handler(server, "/api/parser/next", batch)
    worker, queued = threading.Thread(target=gallery._dispatch_post), threading.Thread(target=other_tab._dispatch_post)
    with mock.patch.object(app_server.GalleryStore, "parse_next", slow_batch):
        worker.start()
        try:
            assert started.wait(5)
            assert server.active_writer_jobs() == 1  # quitting still waits for the batch
            edit = make_handler(server, "/api/session/mutate", {"action": "session-name", "sessionName": "새 이름"})
            edit_thread = threading.Thread(target=edit._dispatch_post)
            edit_thread.start()
            edit_thread.join(3)
            assert not edit_thread.is_alive(), "a board edit must not wait behind a gallery batch"
            assert edit.responses[0][0]["ok"] is True
            queued.start()
            queued.join(0.5)
            assert queued.is_alive() and len(calls) == 1, "a second gallery job must wait for the first"
        finally:
            release.set()
            worker.join(5)
            if queued.ident:
                queued.join(5)
    assert len(calls) == 2
    assert server.session_snapshot()["session_name"] == "새 이름"
    assert server.active_writer_jobs() == 0


# R57 ------------------------------------------------------------------------

@pytest.mark.parametrize("exc, code", [
    (PermissionError(13, "locked", "C:\\Users\\t\\x"), "runtime_storage_access_denied"),
    (OSError(errno.ENOSPC, "No space left on device"), "runtime_storage_full"),
    (RuntimeError("parser crashed"), "gallery_failed"),
])
def test_gallery_errors_are_korean_logged_and_keep_raw_text_in_detail(exc, code):
    handler, responses = _handler("/api/parser/next")
    handler._read_json_body = lambda: {"jobId": "0" * 32}
    with mock.patch.object(GalleryStore, "parse_next", side_effect=exc), \
            mock.patch.object(app_server, "_log_operation_exception") as log:
        handler._handle_parser_request("next", urlparse("/api/parser/next"))
    payload, kwargs = responses[0]
    assert payload["code"] == code
    assert "Errno" not in payload["error"] and "Users" not in payload["error"]
    assert str(exc) in payload["detail"]
    assert not any("신고" in step for step in payload.get("recoverySteps", [])), "the gallery has no 문제 신고"
    assert kwargs["status"] == 500
    log.assert_called_once_with("problem_gallery", exc)


@pytest.mark.parametrize("exc, korean", [
    (ValueError("추출 진행 정보를 다시 불러와 주세요."), True),
    # A parser bug surfaces as Python's own English ValueError.
    (ValueError("max() arg is an empty sequence"), False),
])
def test_gallery_value_errors_are_coded_400s_in_korean(exc, korean):
    handler, responses = _handler("/api/parser/next")
    handler._read_json_body = lambda: {"jobId": "0" * 32}
    with mock.patch.object(GalleryStore, "parse_next", side_effect=exc), \
            mock.patch.object(app_server, "_log_operation_exception") as log:
        handler._handle_parser_request("next", urlparse("/api/parser/next"))
    payload, kwargs = responses[0]
    assert payload["code"] == "gallery_request_invalid"
    assert payload["error"] == (str(exc) if korean else "문제 추출에 실패했습니다. 완료된 결과는 보관됩니다. 다시 시도해 주세요.")
    assert payload["detail"] == f"ValueError: {exc}"
    assert kwargs["status"] == 400
    assert log.call_args_list == ([] if korean else [mock.call("problem_gallery", exc)])


# R58 ------------------------------------------------------------------------

def test_preview_returns_focus_and_escape_closes_hover_before_selection():
    run_node(GALLERY_SETUP + r"""
    (async()=>{
      serverJobs=[{id:'source',sourceName:'source.pdf',sourcePageCount:3,processedPages:3,status:'complete',
        problems:[1,2,3].map(n=>({...makeProblem(n),problemNumberSource:'',labelRecognitionAttempted:true}))}];
      await mount();
      const names=nodes(render()).filter(n=>n.type==='input'&&n.props['aria-label']?.endsWith(' 선택')).map(n=>n.props['aria-label']);
      assert.deepEqual(names,[1,2,3].map(n=>`번호 확인 필요 · source.pdf ${n}쪽 선택`),'unconfirmed cards need distinct names');
      const image=n=>nodes(render()).find(node=>node.props?.['aria-label']===`번호 확인 필요 · source.pdf ${n}쪽 크게 보기`);
      assert.ok(nodes(render()).some(node=>node.props?.['aria-label']==='번호 확인 필요 · source.pdf 3쪽 PNG 다운로드'));
      image(1).props.onClick({ctrlKey:true});image(2).props.onClick({ctrlKey:true});
      sandbox.window={innerWidth:1280,innerHeight:800};
      const target={isConnected:true,matches:()=>true,getBoundingClientRect:()=>({left:0,right:100,top:0})};
      image(3).props.onFocus({type:'focus',currentTarget:target});
      await new Promise(resolve=>setTimeout(resolve,250));
      const tooltip=()=>nodes(render()).find(n=>n.props?.role==='tooltip');
      assert.ok(tooltip(),'keyboard focus opens the hover preview');
      const section=()=>nodes(render()).find(n=>n.props?.className==='gallery-results');
      const escape={key:'Escape',target:{closest:()=>null},preventDefault(){},stopPropagation(){}};
      section().props.onKeyDown(escape);
      assert.ok(!tooltip());
      assert.ok(text(render()).replace(/\s+/g,'').includes('2개선택해제'),'Esc on the hover preview keeps the selection');
      section().props.onKeyDown(escape);
      assert.ok(!text(render()).includes('개 선택 해제'),'a second Esc still clears the selection');
      // The large preview hands focus back to the card that opened it, or to the grid.
      let opened=0,closed=0,restored=0,gridFocus=0,layout;
      const props={problem:makeProblem(1),onClose(){},mode:'raw',onModeChange(){},onDownload(){},saving:false,message:''};
      const opener={isConnected:true,focus(options){restored++;assert.equal(options.preventScroll,true);}};
      sandbox.document.activeElement=opener;sandbox.useLayoutEffect=fn=>{layout=fn;};
      hooks=[];cursor=0;
      sandbox.GalleryProblemPreview(props);
      hooks[0].current={showModal(){opened++;},close(){closed++;}};
      const cleanup=layout();assert.equal(opened,1);
      cleanup();assert.equal(closed,1);assert.equal(restored,1);
      sandbox.document.activeElement={isConnected:false,focus(){throw new Error('the opener left the page');}};
      sandbox.document.querySelector=selector=>selector==='.gallery-grid'?{focus(options){gridFocus++;assert.equal(options.preventScroll,true);}}:null;
      hooks=[];cursor=0;
      sandbox.GalleryProblemPreview(props);
      hooks[0].current={showModal(){},close(){}};
      layout()();
      assert.equal(gridFocus,1);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)
