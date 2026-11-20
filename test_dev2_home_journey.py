"""Home journey round 2: new lesson on a built board, blocked HWP, name order, HEIC notice,
queue-failure recovery, ClassIn open failure and the HWP error's file name."""
from __future__ import annotations

import subprocess
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

import pytest

import app_server
import preprocess
from test_stabilize_server_errors import _handler
from test_ui_home_flows import NODE_SETUP, run_node


HANDLE_FILES_SETUP = NODE_SETUP + r"""
const plain = value => JSON.parse(JSON.stringify(value));
function handleFilesSandbox(overrides = {}) {
  const state = { toasts: [], prompts: [], resets: [], answer: true, resetOk: true };
  const sandbox = {
    useCallback: fn => fn, pendingFilesRef: { current: [] }, autoProcessKeysRef: { current: new Set() },
    pendingFileKeysRef: { current: new Set() }, queueGenerationRef: { current: 0 }, items: [],
    fileInputRef: { current: { value: 'picked' } }, setPendingFiles() {}, setRecognitionReview() {},
    selectPendingFile() {}, showToast: (message, tone) => state.toasts.push([message, tone]),
    session: null, runtimeDiagnostics: null, IS_MAC_PLATFORM: false,
    sessionPublishSummary: s => s?.publish_summary || null, sessionEditState: { dirty: false },
    fileName: '3월 2주 수학', newLessonAskedRef: { current: null },
    window: { confirm: message => { state.prompts.push(message); return state.answer; } },
    resetSession: async options => {
      state.resets.push(options);
      if (state.resetOk) sandbox.pendingFilesRef.current = [];
      return state.resetOk;
    },
    ...overrides,
  };
  // The app re-adds the files through this ref, whose closure may still hold the old lesson.
  sandbox.handleFilesRef = { current: files => sandbox.handleFiles(files) };
  vm.createContext(sandbox);
  vm.runInContext(section('function fileQueueKey(', 'function isImageOnlyFileBatch(')
    + section('  const setPendingFilesTracked = useCallback(', '  const queueRequestIsCurrent =')
    + section('  const handleFiles = (fileList) => {', '  const requestPendingFileReplacement =')
    + '\nglobalThis.handleFiles = handleFiles;', sandbox);
  return { sandbox, state };
}
const queued = sandbox => sandbox.pendingFilesRef.current.map(file => file.name).join();
"""


def test_material_dropped_on_a_built_lesson_asks_to_start_a_new_lesson():
    run_node(HANDLE_FILES_SETUP + r"""
    const pdf = { name: '3월3주.pdf', size: 9, lastModified: 1 };
    const built = (publishedAt = '2026-11-12T15:00:00+09:00') => (
      { output_dir: 'C:/runtime/sessions/lastweek', publish_summary: { publishedAt } });
    (async () => {
      // 확인: 새 수업 first (last week's lesson stays in 최근 수업), then the PDF goes into the new lesson.
      let { sandbox, state } = handleFilesSandbox({ session: built(), items: [{ id: 'p1' }] });
      assert.equal(sandbox.handleFiles([pdf]), 0);
      assert.equal(queued(sandbox), '', 'nothing is queued before 새 수업 finishes');
      await flush();
      assert.deepEqual(plain(state.resets), [{ keepHistory: true }]);
      assert.equal(state.prompts.length, 1, 'the re-call after 새 수업 must not ask again');
      assert.match(state.prompts[0], /^새 수업으로 시작할까요\?/);
      assert.match(state.prompts[0], /‘3월 2주 수학’ 수업은 EDB까지 만들었어요/);
      assert.equal(queued(sandbox), '3월3주.pdf');

      // 취소: added to the open lesson as before, and not asked again until the next EDB build.
      ({ sandbox, state } = handleFilesSandbox({ session: built() }));
      state.answer = false;
      assert.equal(sandbox.handleFiles([pdf]), 1);
      assert.equal(queued(sandbox), '3월3주.pdf');
      assert.equal(state.resets.length, 0);
      sandbox.pendingFilesRef.current = [];
      assert.equal(sandbox.handleFiles([{ ...pdf, name: '붙여넣은 이미지.png' }]), 1);
      assert.equal(state.prompts.length, 1, 'one question per EDB build');
      sandbox.pendingFilesRef.current = [];
      sandbox.session = built('2026-11-19T09:00:00+09:00');
      sandbox.handleFiles([{ ...pdf, name: 'c.png' }]);
      assert.equal(state.prompts.length, 2, 'a new EDB build asks again');

      // No EDB yet, unsaved edits, files already waiting, or no lesson: the teacher is still working here.
      for (const overrides of [
        { session: { output_dir: 'x' } },
        { session: built(), sessionEditState: { dirty: true } },
        { session: built(), pendingFilesRef: { current: [{ name: 'q.pdf', size: 1, lastModified: 0 }] } },
        { session: null },
      ]) {
        ({ sandbox, state } = handleFilesSandbox(overrides));
        assert.ok(sandbox.handleFiles([pdf]) > 0, JSON.stringify(overrides));
        assert.equal(state.prompts.length + state.resets.length, 0, JSON.stringify(overrides));
      }

      // 새 수업 refused (busy): nothing is queued and the next drop asks again.
      ({ sandbox, state } = handleFilesSandbox({ session: built() }));
      state.resetOk = false;
      sandbox.handleFiles([pdf]);
      await flush();
      assert.equal(queued(sandbox), '');
      sandbox.handleFiles([pdf]);
      assert.equal(state.prompts.length, 2);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_hwp_that_cannot_be_opened_stays_out_of_the_batch_and_is_named():
    run_node(HANDLE_FILES_SETUP + r"""
    let { sandbox, state } = handleFilesSandbox({ runtimeDiagnostics: { hangul: { status: 'blocked' } } });
    assert.equal(sandbox.handleFiles([{ name: '국어1.pdf', size: 1 }, { name: '학습지.hwp', size: 2 }]), 1);
    assert.equal(queued(sandbox), '국어1.pdf');
    assert.equal([...sandbox.autoProcessKeysRef.current].some(key => key.includes('.hwp')), false);
    assert.deepEqual(plain(state.toasts), [['한글 파일은 바로 열 수 없어 빼 두었어요(학습지.hwp). '
      + '한글 프로그램에서 PDF로 저장한 뒤 그 PDF를 넣어 주세요', 'info']]);
    assert.equal(sandbox.handleFiles([{ name: '학습지2.hwpx', size: 3 }, { name: '학습지3.HWP', size: 4 }]), 0);
    assert.match(state.toasts.at(-1)[0], /빼 두었어요\(학습지2\.hwpx 외 1개\)/);
    assert.equal(state.toasts.at(-1)[1], 'error');
    // 'partial' (and unknown diagnostics) can still open the file, so it is queued as before.
    for (const runtimeDiagnostics of [{ hangul: { status: 'ready' } }, { hangul: { status: 'partial' } }, null]) {
      ({ sandbox, state } = handleFilesSandbox({ runtimeDiagnostics }));
      assert.equal(sandbox.handleFiles([{ name: '학습지.hwp', size: 2 }]), 1);
      assert.equal(state.toasts.length, 0);
    }
    """)


def test_files_added_together_are_queued_in_name_order_after_the_queue():
    run_node(HANDLE_FILES_SETUP + r"""
    const { sandbox } = handleFilesSandbox();
    sandbox.handleFiles([{ name: '시험지.pdf', size: 99 }]);
    sandbox.handleFiles(['IMG_4.jpg', 'IMG_10.jpg', 'IMG.jpg', 'IMG_2.jpg'].map((name, size) => ({ name, size })));
    assert.equal(queued(sandbox), '시험지.pdf,IMG.jpg,IMG_2.jpg,IMG_4.jpg,IMG_10.jpg');
    for (const [picked, expected] of [
      [['Scan (10).png', 'Scan.png', 'Scan (2).png'], 'Scan.png,Scan (2).png,Scan (10).png'],
      [['스캔_10.png', '스캔_02.png', '스캔.png'], '스캔.png,스캔_02.png,스캔_10.png'],
      [['붙여넣은 이미지 20261019-093005-2.png', '붙여넣은 이미지 20261019-093005.png'],
        '붙여넣은 이미지 20261019-093005.png,붙여넣은 이미지 20261019-093005-2.png'],
    ]) {
      sandbox.pendingFilesRef.current = [];
      sandbox.handleFiles(picked.map((name, size) => ({ name, size })));
      assert.equal(queued(sandbox), expected);
    }
    """)


def test_heic_photos_get_a_conversion_hint_on_every_way_in():
    run_node(HANDLE_FILES_SETUP + r"""
    let { sandbox, state } = handleFilesSandbox({ IS_MAC_PLATFORM: true });
    assert.equal(sandbox.handleFiles([{ name: '묶음.zip', size: 3 }]), 0);
    assert.deepEqual(plain(state.toasts.at(-1)), ['PDF, HWP, PNG, JPG 파일만 넣을 수 있어요', 'error']);
    assert.equal(sandbox.handleFiles([{ name: 'IMG_0001.HEIC', size: 4 }]), 0);
    assert.match(state.toasts.at(-1)[0], /^HEIC 사진은 넣을 수 없어요\. Finder에서 .*빠른 동작 → 이미지 변환으로 JPEG로/);
    sandbox.IS_MAC_PLATFORM = false;
    assert.equal(sandbox.handleFiles([{ name: '정답.png', size: 5 }, { name: 'IMG_0002.heif', size: 6 }]), 1);
    assert.deepEqual(plain(state.toasts.at(-1)),
      ['지원하지 않는 파일 1개는 빼고 담았어요. HEIC 사진은 넣을 수 없어요. JPG로 바꾼 뒤 넣어 주세요', 'info']);

    // Ctrl/Cmd+V of a HEIC file.
    const listeners = new Map(), toasts = [];
    const paste = {
      File, IS_MAC_PLATFORM: true, useEffect: effect => { effect(); }, isEditableKeyboardTarget: () => false,
      document: { activeElement: null, querySelector: () => null },
      window: { addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener() {} },
      showToast: message => toasts.push(message), handleFilesRef: { current: () => assert.fail('nothing to add') },
    };
    vm.createContext(paste);
    vm.runInContext(section('const UPLOAD_FILE_EXTENSION_PATTERN', 'function appendUniquePendingFiles(')
      + section('  // Ctrl/Cmd+V adds a copied screenshot', '\n  useEffect(() => {\n    if (recognitionReview?.kind'), paste);
    const heic = new File(['heic'], 'IMG_0001.HEIC', { type: 'image/heic' });
    const event = { target: {}, defaultPrevented: false, preventDefault() { this.prevented = true; },
      clipboardData: { items: [{ kind: 'file', getAsFile: () => heic }] } };
    listeners.get('paste')(event);
    assert.equal(event.prevented, true);
    assert.match(toasts.at(-1), /^HEIC 사진은 넣을 수 없어요\. Finder에서/);

    // '선택 파일 교체…' keeps the queued file instead of queueing a file the upload would refuse.
    const replace = { IS_MAC_PLATFORM: false };
    vm.createContext(replace);
    vm.runInContext(section('function replacePendingSourceFile(', 'function SourceFilePicker(')
      + section('function fileQueueKey(', 'function appendUniquePendingFiles('), replace);
    const files = [{ name: 'a.pdf', size: 10, lastModified: 1 }], key = replace.fileQueueKey(files[0]);
    assert.throws(() => replace.replacePendingSourceFile(files, key, { name: 'IMG_0003.HEIC', size: 3 }),
      /^Error: HEIC 사진은 넣을 수 없어요\. JPG로 바꾼 뒤/);
    assert.throws(() => replace.replacePendingSourceFile(files, key, { name: '정답.docx', size: 4 }),
      /PDF, HWP, PNG, JPG 파일만/);
    assert.equal(replace.replacePendingSourceFile(files, key, { name: 'b.png', size: 5 })[0].name, 'b.png');
    """)


def test_queue_failure_recovery_reruns_only_the_failed_files():
    run_node(NODE_SETUP + r"""
    const plain = value => JSON.parse(JSON.stringify(value));
    function recoveryHandlers(recovery, pendingKeys) {
      const state = { runs: [], toasts: [], cleared: 0, pending: pendingKeys.map(key => ({ key })) };
      const sandbox = {
        useCallback: fn => fn, operationRecovery: recovery, pendingFiles: state.pending,
        fileQueueKey: file => file.key, processQueuedFiles: (...args) => state.runs.push(args),
        clearOperationRecovery: () => { state.cleared += 1; }, showToast: message => state.toasts.push(message),
        setPendingFilesTracked: update => { state.pending = update(state.pending); },
      };
      vm.createContext(sandbox);
      vm.runInContext(section('  const retryRecoveryOperation = () => {', '  const resetAfterOperationError')
        + section('  const runRecoveryAlternative = () => {', '  const loadLatestConflictSession')
        + section('  const removePendingFile = useCallback(', '  const clearPendingFiles')
        + '\nObject.assign(globalThis, { retryRecoveryOperation, runRecoveryAlternative, removePendingFile });', sandbox);
      return { sandbox, state };
    }
    const failed = { kind: 'recognition', retryMode: 'recognize', alternativeMode: 'register', retryKeys: ['bad'] };
    let { sandbox, state } = recoveryHandlers({ ...failed, error: { retryable: true } }, ['older-failed', 'bad', 'added-later']);
    sandbox.retryRecoveryOperation();
    assert.deepEqual(plain(state.runs), [['recognize', null, ['bad']]]);
    ({ sandbox, state } = recoveryHandlers({ ...failed, error: { retryable: false } }, ['older-failed', 'bad']));
    sandbox.runRecoveryAlternative();
    assert.deepEqual(plain(state.runs), [['register', null, ['bad']]]);
    ({ sandbox, state } = recoveryHandlers({ ...failed, error: { retryable: false } }, ['older-failed']));
    sandbox.runRecoveryAlternative();
    assert.equal(state.runs.length, 0, 'the other queued file must not be sent in its place');
    assert.equal(state.cleared, 1);
    assert.match(state.toasts[0], /대기열에 없습니다/);
    ({ sandbox, state } = recoveryHandlers({ ...failed, error: { retryable: false } }, ['bad', 'good']));
    sandbox.removePendingFile('good');
    assert.equal(state.cleared, 0, 'removing another file keeps the banner');
    sandbox.removePendingFile('bad');
    assert.equal(state.cleared, 1, 'removing the failed file closes its banner');

    const queue = section('  const processQueuedFiles = useCallback(', '  const applyRecognitionResult =');
    assert.equal(queue.split('retryKeys: fileKeys,').length - 1, 2);
    assert.equal(source.includes('retryTargetKey'), false);

    // The queue banner never offers 초기화 (it wipes the queue, the board and 최근 수업); 제작 keeps it.
    const Babel = require('./scripts/vendor/babel.min.js');
    const banner = {
      React: { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
        Fragment: 'fragment' },
      useState: initial => [initial, () => {}], useEffect() {},
      Icon: new Proxy({}, { get: (_, name) => String(name) }), IS_MAC_PLATFORM: false,
      probeLocalServerHealth: async () => true,
    };
    vm.createContext(banner);
    vm.runInContext(Babel.transform(section('const OPERATION_RECOVERY_SUMMARIES', 'function operationRecoveryStateTransition(')
      + section('function OperationRecoveryBanner(', 'function LoadingOverlay('), { presets: ['react'] }).code, banner);
    const text = node => typeof node === 'string' ? node : (node?.children || []).map(text).join(' ');
    const render = recovery => text(banner.OperationRecoveryBanner({ recovery, onRetry() {} }));
    for (const [kind, alternativeLabel] of [['recognition', '페이지 PNG로 등록'], ['registration', '수동 자르기로 열기'],
      ['apply-recognition', undefined]]) {
      for (const retryable of [true, false]) {
        const shown = render({ kind, alternativeLabel, retryKeys: ['bad'],
          error: { code: 'pdf_password_protected', message: '암호', retryable } });
        assert.equal(shown.includes('초기화'), false, `${kind}/${retryable}: ${shown}`);
        if (alternativeLabel) assert.ok(shown.includes(alternativeLabel), shown);
      }
    }
    assert.ok(render({ kind: 'publish', error: { code: 'publish_failed', message: 'x', retryable: true } })
      .includes('초기화 후 다시 시작'));
    """)


def test_classin_open_failure_offers_the_folder():
    run_node(NODE_SETUP + r"""
    const notices = [], folders = [];
    let reply = { ok: false, status: 500 };
    const sandbox = {
      console: { warn() {} },
      window: { dispatchEvent: event => notices.push(event.detail) },
      CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
      fetch: async () => reply,
      readJsonResponse: async () => ({ ok: false, code: 'open_file_failed', detail: 'failed to open: No application knows' }),
      openOutputFolder: path => folders.push(path), userErrorText: error => error.message,
    };
    vm.createContext(sandbox);
    vm.runInContext(section('async function openPublishedEdb(', 'function readSettingsSectionsOpen('), sandbox);
    (async () => {
      const target = { canOpenEdbFile: true, edbPath: 'C:/out/수업.edb', edbParts: [] };
      await sandbox.openPublishedEdb(target);
      assert.equal(notices.length, 1);
      assert.equal(notices[0].message, 'ClassIn에서 바로 열지 못했어요. 폴더에서 EDB 파일을 찾아 ClassIn에서 불러와 주세요.');
      assert.equal(notices[0].actionLabel, '폴더 열기');
      notices[0].onAction();
      assert.deepEqual(folders, ['C:/out/수업.edb'], 'the folder that holds the EDB opens');
      reply = { ok: false, status: 404 };
      await sandbox.openPublishedEdb(target);
      assert.equal(JSON.stringify(notices[1]), JSON.stringify({ message: '파일을 찾지 못했어요. EDB를 다시 제작해 주세요.', type: 'error' }));

      // App turns a notice with an action into the action toast, and any other notice into a plain toast.
      const calls = [], listeners = {};
      const app = {
        useEffect: fn => fn(), showToast: (...args) => calls.push(['toast', ...args]),
        showActionToast: (message, label) => calls.push(['action', message, label]),
        window: { addEventListener: (type, fn) => { listeners[type] = fn; }, removeEventListener() {} },
      };
      vm.createContext(app);
      vm.runInContext(section('  useEffect(() => {\n    const onNotice', '  const clearOperationRecovery'), app);
      listeners['edb-notice']({ detail: notices[0] });
      listeners['edb-notice']({ detail: notices[1] });
      assert.deepEqual(calls, [['action', notices[0].message, '폴더 열기'],
        ['toast', '파일을 찾지 못했어요. EDB를 다시 제작해 주세요.', 'error']]);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_mac_open_failure_is_reported_with_code():
    target = Path("/x/lesson.edb")
    refused = subprocess.CompletedProcess([], 1, b"", b"No application knows how to open URL file:///x/lesson.edb")
    with patch.object(app_server.sys, "platform", "darwin"):
        with patch.object(app_server.subprocess, "run", return_value=refused) as run:
            with pytest.raises(OSError, match="No application knows"):
                app_server._open_system_target(target)
        assert run.call_args[0][0] == ["/usr/bin/open", str(target)]
        with patch.object(app_server.subprocess, "run", side_effect=subprocess.TimeoutExpired("open", 10)):
            app_server._open_system_target(target)
        with patch.object(app_server.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            app_server._open_system_target(target)

    handler, responses = _handler("/api/system/open-file")
    handler.headers = {}
    handler._read_json_body = lambda: {"path": "lesson.edb"}
    with patch.object(app_server, "_request_is_same_origin", return_value=True), \
            patch.object(app_server, "_resolve_open_target", return_value=Path("lesson.edb")), \
            patch.object(app_server, "_open_system_target", side_effect=OSError("No application knows how to open")):
        handler._handle_open_file()
    payload, kwargs = responses[0]
    assert kwargs["status"] == HTTPStatus.INTERNAL_SERVER_ERROR
    assert payload["code"] == "open_file_failed"
    assert payload["error"] == "ClassIn에서 바로 열지 못했어요."
    assert "No application knows" in payload["detail"]


def test_hwp_conversion_error_names_the_teachers_file(tmp_path):
    error = ValueError("HWP/HWPX conversion failed. Install LibreOffice with HWP support, or convert the HWP file to PDF first.")
    error.source_file = "a" * 64 + "_학습지.hwp"
    payload = app_server._export_error_payload(error)
    assert payload["code"] == "hwp_conversion_failed"
    assert payload["sourceFile"] == payload["fileName"] == "학습지.hwp"

    source = tmp_path / ("b" * 64 + "_worksheet.hwp")
    source.write_bytes(b"hwp")
    with patch.object(preprocess, "inspect_hangul_document", return_value={}):
        with patch.object(preprocess, "_iter_hwp_pdf_converter_commands", return_value=[]):
            with pytest.raises(ValueError, match="LibreOffice") as missing:
                preprocess.convert_hwp_to_pdf(source, tmp_path / "missing")
        with patch.object(preprocess, "_iter_hwp_pdf_converter_commands", return_value=[["soffice"]]), \
                patch.object(preprocess, "_iter_pyhwp_html_converter_commands", return_value=[]), \
                patch.object(preprocess, "_run_hwp_pdf_converter_commands", return_value=(None, ["soffice: failed"])), \
                patch.object(preprocess, "_convert_hwp_to_hwpx_with_hwpilot", return_value=(None, [])), \
                patch.object(preprocess, "_convert_hwp_to_pdf_with_pyhwp_html", return_value=(None, [])):
            with pytest.raises(ValueError, match="HWP/HWPX conversion failed") as failed:
                preprocess.convert_hwp_to_pdf(source, tmp_path / "failed")
    assert missing.value.source_file == failed.value.source_file == source.name
    assert app_server._export_error_payload(failed.value)["sourceFile"] == "worksheet.hwp"
