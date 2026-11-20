"""Round-2 names-copy-cancel chain.

- A cancelled, refreshed or closed import stops on the server and frees the app's file lock (R30).
- Lesson and EDB names keep the teacher's file name: no upload hash tail, Mac (NFD) names whole (R4, R50).
- Windows EDB names count path units, and every build gets its own download name (R36, R51).
- One name per open action after a build (R79); the list names a status, not just a dot colour (R83).
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

import pytest

import app_server
import build_problem_board_edb as board
from structured_schema import Subject
from test_dev_srv_jobs import make_handler, server  # noqa: F401 - server is the shared app fixture
from test_large_pdf_batches import make_pdf
from test_ui_home_flows import NODE_SETUP, run_node
from test_ui_sidebar_hierarchy import RENDER_SETUP


PROJECT_ROOT = Path(__file__).resolve().parent
APP = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
CSS = (PROJECT_ROOT / "ui_prototype" / "board.css").read_text(encoding="utf-8")


# R30 ------------------------------------------------------------------------

def test_cancel_stops_an_import_and_frees_the_app(server, tmp_path):
    started = threading.Event()

    def long_import(_source, *, progress=None, **_kwargs):
        for page in range(500):
            board._report_progress(progress, "segment", page, 500)  # the real gate, so the builder re-raise is covered
            started.set()
            time.sleep(0.01)
        raise AssertionError("the cancel never reached the import")

    preview = tmp_path / "preview"
    handler = make_handler(server, "/api/export", {
        "preview": True, "exportEdb": False, "ocr": "none", "exportMode": "question", "jobId": "job-import",
    })
    handler._resolve_source_paths = lambda _payload, **_kwargs: [tmp_path / "scan.pdf"]
    handler._resolve_preview_output_dir = lambda _payload, _sources: preview
    with patch.object(app_server, "run_problem_export", side_effect=long_import):
        worker = threading.Thread(target=handler._dispatch_post)  # takes the exclusive lock like a real import
        worker.start()
        assert started.wait(5)
        assert preview.is_dir()
        make_handler(server, "/api/jobs/cancel", {"jobId": "job-import"})._dispatch_post()
        worker.join(2)
        assert not worker.is_alive(), "the import must stop at the next progress point after the cancel"

    body, status = handler.responses[0]
    assert (body["code"], status) == ("job_cancelled", HTTPStatus.CONFLICT)
    assert body["error"] and body["detail"]
    assert not preview.exists()
    assert server._artifact_job_lock.acquire(timeout=0.5), "the next upload must not wait for the cancelled import"
    server._artifact_job_lock.release()
    server.clear_session(keep_history=True)  # 새 수업 works right after the cancel


def test_export_cancel_crosses_the_page_pool_and_the_batch_loop(tmp_path):
    source = tmp_path / "exam.pdf"
    make_pdf(source, 12, badges=True)
    seen = []

    def cancel_after_two(_stage, done, _total):
        seen.append(done)
        if done >= 2:
            raise board.ExportCancelled()

    with pytest.raises(board.ExportCancelled):
        board.build_pages(source, subject=Subject.UNKNOWN, ocr_mode="none", ai_fallback_config=None, pdf_dpi=72,
                          detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None,
                          progress=cancel_after_two)
    assert max(seen) < 12, "the second 10-page batch never starts"


def test_import_requests_carry_the_job_id_their_cancel_names() -> None:
    run_node(NODE_SETUP + r"""
    const bodies = [];
    const sandbox = {
      fetch: async (url, init) => { bodies.push([url, JSON.parse(init.body).jobId]); return { ok: true }; },
      normalizeInputIntent: value => value, normalizeContentTarget: value => value || 'all',
      INPUT_INTENT_BY_VALUE: { 'multi-problem': { exportMode: 'question' } }, DEFAULT_INPUT_INTENT: 'multi-problem',
      assertExportPayloadFits() {}, uploadSourceFiles: async () => [{ path: 'a.pdf' }],
      edbFileNameFromSessionName: () => 'a.edb', withExpectedSessionRevision: payload => payload,
      isDocumentLikeFile: () => true, DEFAULT_RECOGNITION_MAX_DIMENSION: 4096, AI_FALLBACK_OFF: { enabled: false },
      readJsonResponse: async () => ({ ok: true, session: { id: 's' } }), sessionResponseRevisionIsStale: () => false,
      captureSessionRevision() {}, operationErrorFromResponse: () => new Error('failed'),
      sessionReusableSourcePaths: () => ['C:/src/a.pdf'], formatApiError: () => 'failed',
    };
    vm.createContext(sandbox);
    vm.runInContext(section('async function postExport(', 'async function postPublishPlan(')
      + section('async function postReextractSharedPassages(', 'function formatApiError('), sandbox);
    // Both imports (문항 AI 인식, 페이지 등록) and 공통 지문 재추출 name their job.
    const queue = section('  const processQueuedFiles = useCallback(', '  // Recognition results are applied');
    const exportCalls = queue.match(/await postExport\(files, aiFallback, resolvedInputIntent, \{[\s\S]*?\n\s*\}\);/g);
    assert.equal(exportCalls.length, 2);
    for (const call of exportCalls) assert.match(call, /\n\s*jobId: job\.id,\n/);
    assert.match(section('      const incomingSession = await postReextractSharedPassages({', '      });'), /jobId: job\.id,/);
    (async () => {
      await sandbox.postExport([{ name: 'a.pdf' }], null, 'multi-problem', { preview: true, jobId: 'job-q' });
      await sandbox.postExport([{ name: 'a.pdf' }], null, 'multi-problem', { preview: true });
      await sandbox.postReextractSharedPassages({ session: {}, jobId: 'job-p' });
      assert.deepEqual(bodies, [['/api/export', 'job-q'], ['/api/export', undefined], ['/api/export', 'job-p']]);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_closing_or_refreshing_the_page_cancels_running_imports_only() -> None:
    run_node(NODE_SETUP + r"""
    const listeners = new Map(), cancels = [];
    const sandbox = {
      postJobCancel: id => cancels.push(id),
      window: {
        addEventListener: (type, fn) => listeners.set(type, fn),
        removeEventListener: (type, fn) => { if (listeners.get(type) === fn) listeners.delete(type); },
      },
    };
    sandbox.useEffect = effect => { sandbox.cleanup = effect(); };
    vm.createContext(sandbox);
    const effect = section('  // Refresh, close or back leaves a running import', '\n  useEffect(() => {\n    return () => {');
    sandbox.backgroundJobs = [
      { id: 'q', scope: 'queue-recognition', status: 'running' },
      { id: 'e', scope: 'image-enhance', status: 'running' },  // 고화질 keeps its paid result
      { id: 'd', scope: 'queue-registration', status: 'done' },
    ];
    vm.runInContext(effect, sandbox);
    listeners.get('pagehide')();
    assert.deepEqual(cancels, ['q']);
    sandbox.cleanup();
    assert.equal(listeners.size, 0, 'the listener goes away with the effect');
    sandbox.backgroundJobs = [{ id: 'e', scope: 'image-enhance', status: 'running' }];
    vm.runInContext(effect, sandbox);
    assert.equal(listeners.size, 0, 'no running import, no listener');
    assert.equal(sandbox.cleanup, undefined);
    """)


# R4, R50 --------------------------------------------------------------------

def test_new_upload_lesson_name_drops_the_digest_prefix_and_its_name_hash_tail() -> None:
    uploads = Path("/uploads")
    upload = uploads / app_server._managed_upload_target_name("26-6월 수학영역 문제지.pdf", "a1" * 32, upload_dir=uploads)
    assert re.search(r"_[0-9a-f]{10}\.pdf$", upload.name), upload.name  # the stored name carries the tail
    assert app_server._lesson_name_from_sources([upload]) == "26-6월 수학영역 문제지"
    assert app_server._lesson_name_from_sources([upload, Path("/uploads/b.png")]) == "26-6월 수학영역 문제지 외 1개"
    # Without the managed prefix, a teacher's own hex-looking tail stays.
    assert app_server._lesson_name_from_sources([Path("/x/plain_0123456789.pdf")]) == "plain_0123456789"


def test_mac_nfd_upload_name_keeps_the_full_lesson_and_edb_name() -> None:
    name = "2026학년도 1학기 중간고사 대비 수학(상) 2단원"
    # "C:/uploads" gives the same length budget on Windows, macOS and Linux runners.
    target = app_server._managed_upload_target_name(
        unicodedata.normalize("NFD", f"{name}.pdf"), "a1" * 32, upload_dir="C:/uploads",
    )
    assert app_server._lesson_name_from_sources([Path("C:/uploads") / target]) == name
    assert app_server.sanitize_edb_file_name(unicodedata.normalize("NFD", "고1 샘플")) == "고1_샘플.edb"


# R36, R51 -------------------------------------------------------------------

def test_edb_name_unit_budget_counts_hangul_as_one_unit() -> None:
    name = "고2 문학 3단원 현대시 기출 변형"  # 19 path units, 49 UTF-8 bytes
    reserve = app_server.EDB_PART_SUFFIX_RESERVED_BYTES
    assert app_server.sanitize_edb_file_name(name, max_units=reserve + 19) == "고2_문학_3단원_현대시_기출_변형.edb"
    assert app_server.sanitize_edb_file_name(name, max_units=reserve + 10) == "고2_문학_3단원.edb"
    assert app_server._truncate_utf16_units("가😀", 2) == "가", "half an emoji is dropped whole"


@patch.object(app_server.sys, "platform", "win32")
def test_windows_publish_name_keeps_the_hangul_name_and_the_build_stamp() -> None:
    # The adopted preview run when Documents is D:\내 문서 (148 path units). POSIX-style so the
    # macOS and Linux runners resolve it as an absolute path too.
    output_dir = Path("/내 문서/ClassInEDBMVP/.app_runtime/outputs/previews").resolve() / (
        "a" * 47 + "_20261119_101010_1787549457154522500_67513cd0436a"
    )
    edb_name, staging, final = app_server._managed_publish_artifact_paths(
        output_dir, "고2_문학_3단원_현대시.edb", fallback_stem="x",
    )
    assert re.fullmatch(r"고2_문학_3단원_현대시_\d{4}_\d{6}\.edb", edb_name), edb_name
    for directory in (staging, final):
        part = directory / app_server._edb_part_file_name(edb_name, 98, 99)
        assert app_server._windows_path_units(part) <= app_server.WINDOWS_MANAGED_PATH_MAX_UNITS, part


@patch.object(app_server.sys, "platform", "win32")
def test_long_onedrive_root_keeps_the_build_stamp_and_every_part_path_fits() -> None:
    output_dir = Path(
        "/Users/" + "LongProfileName" * 2 + "/OneDrive - International Academy/Shared Documents/문서/"
        "ClassInEDBMVP/.app_runtime/outputs/수학영_021c5ba170"
    ).resolve()
    edb_name, staging, final = app_server._managed_publish_artifact_paths(
        output_dir, "아주 긴 기말고사 EDB 이름" * 40, fallback_stem="classin",
    )
    assert re.fullmatch(r"아주_긴_기말고사_EDB_이름\S*_\d{4}_\d{6}\.edb", edb_name), edb_name
    for directory in (staging, final):
        part = directory / app_server._edb_part_file_name(edb_name, 9998, 9999)
        assert app_server._windows_path_units(part) <= app_server.WINDOWS_MANAGED_PATH_MAX_UNITS, part


def test_each_build_gets_its_own_download_name_but_the_fallback_is_not_stamped(tmp_path) -> None:
    stamp = "1120_153012"
    assert app_server.sanitize_edb_file_name("../고1 샘플.edb", suffix=stamp) == "고1_샘플_1120_153012.edb"
    # The fallback (no or empty requested name) already ends in '-published-<time>'.
    assert app_server.sanitize_edb_file_name(None, fallback_stem="fallback name", suffix=stamp) == "fallback_name.edb"
    assert app_server.sanitize_edb_file_name("", fallback_stem="fallback name", suffix=stamp) == "fallback_name.edb"
    edb_name, _staging, _final = app_server._managed_publish_artifact_paths(tmp_path, "수학_학습지.edb", fallback_stem="x")
    assert re.fullmatch(r"수학_학습지_\d{4}_\d{6}\.edb", edb_name), edb_name


# R79, R83 -------------------------------------------------------------------

def test_built_lesson_open_actions_keep_the_top_bar_names() -> None:
    menu = APP.split('className="session-history-menu"', 1)[1].split("</details>", 1)[0]
    assert "<span>ClassIn에서 열기</span>" in menu
    assert "<span>폴더 열기</span>" in menu
    # Sized from their labels, ClassIn에서 열기 and 폴더 열기 share one row without wrapping.
    assert re.search(r"\.publish-result-primary-actions \.btn\{[^}]*flex: 1 1 auto;", CSS)


def test_rail_rows_name_review_status_not_only_by_dot_colour() -> None:
    run_node(RENDER_SETUP + r"""
    const sandbox = renderSandbox();
    const items = [{id:'a',name:'문항 A',step:'s2',reviewStatus:'check_needed',statusLabel:'확인 필요'},
      {id:'b',name:'문항 B',step:'s2',reviewStatus:'failed',statusLabel:'인식 실패'},
      {id:'c',name:'문항 C',step:'s2',reviewStatus:'normal',statusLabel:'정상'}];
    Object.assign(sandbox, {
      items, displayedItemRows: items.map((item,displayIndex)=>({item,displayIndex})),
      editScope:null, editScopeIds:new Set(), selectedItemIds:new Set(), dropTarget:null,
      hasPageChromeArtifactFlag:()=>false, originalProblemNumberInfo:()=>({status:'known',description:''}),
      activeId:'a', itemRefs:{current:{}}, railRef:{current:null}, suppressClickRef:{current:false},
      filterActive:false, moveFeedback:null, pressedItemId:null, downloadingItemId:null, reorderBusy:false, selectedItemCount:0,
      railRowHandlersRef:{current:{}}, PRIMARY_MODIFIER_NAME:'Control', ALTERNATE_MODIFIER_NAME:'Alt',
      problemDisplayName:it=>it.name, reviewStatusClass:s=>`status-${s}`, problemSourceLabel:()=>'원본 1쪽',
      TileImage:()=>null, railThumbClass:()=>'is-board', RAIL_THUMB_PREVIEW_MAX_DIMENSION:256,
    });
    sandbox.React.memo = fn => fn;
    compile(sandbox, section('function RailItemDetails(', 'function BoardTile(')
      + section('function RailRow(', '// ─── CENTER:')
      + 'globalThis.rows = () => (<>'
      + section('        {displayedItemRows.map(', '        {hasSessionItems && visibleItemRows.length === 0 && (') + '</>);'
      + 'globalThis.mini = () => (' + section('<div className="items rail-mini-list"', '        </div>\n      </div>\n    );') + '</div>);');
    const rowLabels = walk(sandbox.rows()).filter(node=>node.props['data-item-id']).map(node=>node.props['aria-label']);
    const miniItems = walk(sandbox.mini()).filter(node=>String(node.props.className).includes('rail-mini-item'));
    for (const labels of [rowLabels, miniItems.map(node=>node.props['aria-label'])]) {
      assert.ok(labels[0].includes('문항 A, 확인 필요'), labels[0]);
      assert.ok(labels[1].includes('문항 B, 인식 실패'), labels[1]);
      assert.ok(!labels[2].includes('정상'), labels[2]);
    }
    assert.ok(rowLabels[0].endsWith('Alt 키와 위아래 방향키로 순서 이동'), rowLabels[0]);
    // Collapsed list: like the full list, only items that need checking keep a dot.
    assert.deepEqual(miniItems.map(node=>findClass(node,'status-dot').props.hidden), [false,false,true]);
    """)
