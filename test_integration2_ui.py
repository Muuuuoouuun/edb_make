"""Round-2 integration fixes (UI): problems that only show once the round-2 branches run together.

- G3: the side panel's 영역 조정 never replaces an open 수동 자르기 or 영역 조정.
- G4/G17: R17's 새 수업 handoff still names the files it left out, and an open 원본 편집 counts as work here.
- G6: a session_conflict with no saved lesson left names the ··· menu's 최신 저장본 불러오기.
- G12: adding a file keeps its 지문 묶음 and 지문 확인 items.
- G16: 종료 with an open 원본 편집 asks about unsaved changes.
- G21: a file refused before sending is not offered the other import method.
(G22 and G26 changed existing tests in test_dev2_review_editors.py and test_dev2_names_copy_cancel.py.)
"""
from __future__ import annotations

from test_dev2_home_journey import HANDLE_FILES_SETUP
from test_ui_home_flows import NODE_SETUP, run_node
from test_ui_placement_details import NODE_SETUP as PLACEMENT_SETUP


# --- G3: side panel 영역 조정 x R48 ------------------------------------------------------------

def test_side_panel_area_edit_keeps_an_open_editor() -> None:
    # The panel's 영역 조정 would run beginBoxEdit, which drops drawn areas without R48's question.
    run_node(NODE_SETUP + r"""
    const body = section('onEditOriginal={id=>{', 'onDownloadItemImage=').trim()
      .replace(/^onEditOriginal=\{/, '').replace(/\}$/, '');
    const calls = [];
    const record = kind => value => calls.push([kind, value]);
    const sandbox = { view: 'review', editorReturnsToBoardRef: { current: false },
      setSelectedProblemIds: record('select'), setActiveId: record('active'), setMobilePane: record('pane'),
      setReviewFocus: record('focus'), requestViewChange: record('view'), showToast: record('toast') };
    vm.createContext(sandbox);
    const onEditOriginal = vm.runInContext(`(${body})`, sandbox);
    sandbox.reviewEditorActive = true;
    onEditOriginal('p1');
    assert.deepEqual(calls, [['toast', '영역 편집을 적용하거나 취소한 뒤 다시 눌러 주세요']]);
    calls.length = 0;
    sandbox.reviewEditorActive = false;
    onEditOriginal('p1');
    assert.equal(calls.find(([kind]) => kind === 'focus')[1].editProblemId, 'p1');
    assert.deepEqual(calls.at(-1), ['view', 'review']);
    """)


def test_manual_split_import_waits_for_an_open_area_editor() -> None:
    # Applying it opens 수동 자르기 for the new pages, which would replace the open editor's drawn areas.
    run_node(NODE_SETUP + r"""
    const body = section('  useEffect(() => {\n    if (!recognitionReview || recognitionReview.applyFailed',
      '\n  useEffect(() => {\n    if (!initialSessionLoaded');
    const applied = [];
    const sandbox = { useEffect: run => run(), applyRecognitionResult: () => applied.push(1),
      confirmingRecognition: false, mutating: false, loading: false, hasPendingSessionConflict: false };
    vm.createContext(sandbox);
    for (const [manualSplit, reviewEditorActive, expected] of [[true, true, 0], [true, false, 1], [false, true, 1]]) {
      applied.length = 0;
      Object.assign(sandbox, { recognitionReview: { kind: 'queue-recognition', manualSplit }, reviewEditorActive });
      vm.runInContext(body, sandbox);
      assert.equal(applied.length, expected, `manualSplit ${manualSplit}, editor open ${reviewEditorActive}`);
    }
    """)


# --- G4/G17: R17's 새 수업 handoff x R82/R39 notices and R48 -----------------------------------

BUILT_LESSON = r"""
const pdf = { name: '3월3주.pdf', size: 9, lastModified: 1 };
const built = { output_dir: 'C:/runtime/sessions/lastweek', publish_summary: { publishedAt: '2026-11-12T15:00:00+09:00' } };
"""


def test_new_lesson_handoff_still_names_the_files_left_out() -> None:
    run_node(HANDLE_FILES_SETUP + BUILT_LESSON + r"""
    (async () => {
      for (const [runtimeDiagnostics, leftOut, notice] of [
        [null, 'IMG_0412.HEIC', /^지원하지 않는 파일 1개는 빼고 담았어요\. HEIC 사진은 넣을 수 없어요/],
        [{ hangul: { status: 'blocked' } }, '학습지.hwp', /^한글 파일은 바로 열 수 없어 빼 두었어요\(학습지\.hwp\)/],
      ]) {
        const { sandbox, state } = handleFilesSandbox({ session: built, items: [{ id: 'p1' }], runtimeDiagnostics });
        // 새 수업 ends in hideMockItems, whose toast replaces the notice shown before the question.
        const reset = sandbox.resetSession;
        sandbox.resetSession = async options => {
          const ok = await reset(options);
          if (ok) state.toasts.push(['새 수업을 시작해요 · 지난 수업은 최근 수업 목록에 있어요', 'info']);
          return ok;
        };
        assert.equal(sandbox.handleFiles([{ name: leftOut, size: 1, lastModified: 1 }, pdf]), 0);
        await flush();
        assert.equal(state.prompts.length, 1, `${leftOut}: one question`);
        assert.equal(queued(sandbox), '3월3주.pdf');
        assert.match(state.toasts.at(-1)[0], notice, `${leftOut}: the last toast names the file left out`);
      }
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_a_drop_or_paste_while_editing_areas_joins_this_lesson_without_a_question() -> None:
    # Otherwise 확인 leads to resetSession's second question, and its 취소 dropped the file unmentioned.
    run_node(HANDLE_FILES_SETUP + BUILT_LESSON + r"""
    const { sandbox, state } = handleFilesSandbox({ session: built, reviewEditorActive: true });
    assert.equal(sandbox.handleFiles([pdf]), 1, 'the paste toast needs the added count');
    assert.equal(queued(sandbox), '3월3주.pdf');
    assert.equal(state.prompts.length + state.resets.length, 0);
    """)


# --- G6: R22 deletion takeover x R1 adopt path ----------------------------------------------

def test_session_conflict_toast_names_the_reload_menu_item() -> None:
    run_node(NODE_SETUP + r"""
    const sandbox = {};
    vm.createContext(sandbox);
    vm.runInContext(section('function formatApiError(', 'function operationErrorFromResponse(')
      + section('function isNetworkRequestError(', 'async function probeLocalServerHealth(')
      + section('function simpleToastErrorMessage(', 'async function fetchUserSettings(')
      + section('function assertOkJson(', 'function edbFileNameFromSessionName('), sandbox);
    // The 409 the app sends once the saved lesson is gone (새 수업 or 초기화 in another window or on another PC).
    const reply = { ok: false, code: 'session_conflict', session: null, sessionRevision: 7,
      error: '다른 창이나 작업에서 먼저 저장해서 방금 한 작업을 적용하지 않았어요.',
      recoverySteps: ['화면이 최신 상태인지 확인해 주세요.', '방금 수행한 작업을 최신 상태에서 다시 시도해 주세요.'] };
    let error;
    try { sandbox.assertOkJson({ ok: false, status: 409 }, reply, '이전 상태 복원 실패'); } catch (e) { error = e; }
    for (const fallback of ['수정 실패', '배치 저장 실패 · 조정값은 화면에 유지했어요', '순서 저장 실패 · 이전 순서로 복구했습니다', '실행 취소 실패']) {
      assert.equal(sandbox.simpleToastErrorMessage(error, fallback),
        `${fallback} · 저장된 수업이 바뀌었어요 · 위쪽 ··· 메뉴에서 ‘최신 저장본 불러오기’를 눌러 주세요`);
    }
    // The toast names the ··· menu item by its label: renaming one must rename the other.
    assert.ok(source.includes("label: refreshing ? '불러오는 중…' : '최신 저장본 불러오기'"));
    """)


def test_ai_jobs_do_not_ask_for_a_rerun_when_no_saved_lesson_is_left() -> None:
    run_node(NODE_SETUP + r"""
    const EPOCH = '00000000000000000001-a';
    const generic = [], toasts = [], applied = [];
    const sandbox = {
      useCallback: fn => fn, session: { problems: [{ id: 'p1' }] }, items: [], fileName: '수업',
      boardColumns: 1, layoutGapMode: 'compact', aiEnabled: true, userSettings: { hasGeminiApiKey: true },
      hasRunningBackgroundJobs: false, UNDO_HISTORY_LIMIT: 5, listUnique: values => [...new Set(values)],
      materializeSessionForItems: () => ({ problems: [] }), cloneSession: value => value,
      sessionEditorDraftSignature: value => (sandbox.dirty && value !== sandbox.session ? 'edited' : 'saved'),
      setSession() {},
      startBackgroundJob: () => ({ id: 'job', controller: { signal: { aborted: false } } }),
      settleBackgroundJob() {}, setHistoryStack() {}, appendBoundedHistory: list => list,
      adoptMutatedSession: next => applied.push(next), setRecognitionReview() {}, aiModelFallbackToast: () => '',
      sessionRecognitionInFlightRef: { current: false }, sessionRecognitionGuardUntilRef: { current: 0 },
      showToast: (message, type) => toasts.push([message, type]),
      showSimpleErrorToast: (_error, fallback) => generic.push(fallback),
      postRestore: async () => { if (sandbox.restoreError) throw sandbox.restoreError; return {}; },
      postEnhanceImage: async () => { if (sandbox.jobError) throw sandbox.jobError; return { session: {}, enhance: [] }; },
      postRetryAi: async () => { if (sandbox.jobError) throw sandbox.jobError; return { session: {} }; },
    };
    vm.createContext(sandbox);
    vm.runInContext(section('let latestServerSessionRevision = null;', 'async function fetchLatestSession(')
      + section('const AI_RESULT_BOARD_CHANGED_MESSAGE', 'async function postRestore(')
      + section('  const retryAiSession = useCallback(', '  const recognizeCurrentSession = useCallback(')
      + section('  const enhanceImageSession = useCallback(', '  const resetSession = useCallback(')
      + `\nglobalThis.t = { retryAiSession, enhanceImageSession, captureSessionRevision,
        expected: () => withExpectedSessionRevision({}).expectedSessionRevision };`, sandbox);
    const { t } = sandbox;
    const gone = () => Object.assign(new Error('conflict'), { code: 'session_conflict', latestSession: null,
      hasLatestSessionState: true, sessionRevision: 9, sessionEpoch: EPOCH });
    (async () => {
      t.captureSessionRevision({ session: {}, sessionRevision: 4, sessionEpoch: EPOCH });
      for (const dirty of [false, true]) {
        for (const kind of ['retry', 'enhance']) {
          for (const where of ['restoreError', 'jobError']) {
            Object.assign(sandbox, { dirty, restoreError: null, jobError: null, [where]: gone() });
            sandbox.sessionRecognitionGuardUntilRef.current = 0;
            if (kind === 'retry') await t.retryAiSession({ pageIds: ['page-1'] });
            else await t.enhanceImageSession(['p1'], { mode: 'preserve' });
            const label = `${kind}/${where}/dirty=${dirty}`;
            assert.deepEqual(toasts, [], `${label}: no '다시 실행해 주세요' toast`);
            assert.deepEqual(generic.splice(0), [kind === 'retry' ? 'AI 재인식 실패' : '고화질 처리 실패'], label);
            assert.deepEqual(applied, [], `${label}: the board stays`);
            assert.equal(t.expected(), 4, `${label}: the revision stays`);
          }
        }
      }
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


# --- G12: mergeSessions x R11 passage groups -------------------------------------------------

def test_merge_sessions_keeps_the_added_files_passage_groups_and_review_items() -> None:
    run_node(PLACEMENT_SETUP + r"""
    const sandbox = placementSandbox();
    vm.runInContext(section('function makeUniqueId(', 'function applyItemStateToProblem(')
      + section('function mergeSessions(', 'function sessionReusableSourcePaths('), sandbox);
    const lesson = (name, missing, groupId = `${name}-passage-4-6`) => {
      const ids = [`${name}-passage-4-6`, `${name}-problem-2`, `${name}-problem-3`];
      const group = { groupId, numberLabel: '4~6', missingChildProblemNumbers: missing,
        problemIds: ids, coreProblemIds: ids.slice(1), fragmentProblemIds: ids.slice(0, 1) };
      const item = { groupId, numberLabel: '4~6', missingChildProblemNumbers: missing, problemIds: ids.slice(1),
        fragmentProblemIds: ids.slice(0, 1), reviewReasonCodes: ['passage_missing_child_questions'] };
      return { session_name: name, pages: [{ id: `${name}-page-001`, problemIds: ids }],
        problems: ids.map((id, index) => ({ id, title: id, actualHeightPages: 0.8, passageGroupId: groupId,
          riskFlags: index ? ['passage_missing_child_questions'] : [] })),
        passageGroups: [group], passage_groups: [group], passageReviewItems: [item], passage_review_items: [item] };
    };
    // vm arrays come from another realm: Array.from before a strict deepEqual.
    const merged = sandbox.mergeSessions(lesson('a', [2]), lesson('b', [5]), 'lesson', 1);
    for (const key of ['passageGroups', 'passage_groups', 'passageReviewItems', 'passage_review_items']) {
      assert.deepEqual(Array.from(merged[key], entry => entry.groupId), ['a-passage-4-6', 'b-passage-4-6'], key);
    }
    const added = sandbox.mapProblemToItem(merged.problems.find(problem => problem.id === 'b-problem-2'), 4, merged);
    assert.equal(added.statusReason, '4~6번 지문의 5번 문항이 없어요');
    // The same file added twice keeps one entry per group; renamed problems are followed.
    const again = sandbox.mergeSessions(lesson('a', [2]), lesson('a', [2]), 'lesson', 1);
    assert.deepEqual(Array.from(again.passageReviewItems, entry => entry.groupId), ['a-passage-4-6']);
    const renamed = sandbox.mergeSessions(lesson('a', [2]), lesson('a', [8], 'a-passage-7-9'), 'lesson', 1);
    assert.deepEqual(Array.from(renamed.passageReviewItems[1].problemIds), ['a-problem-2-add-2', 'a-problem-3-add-2']);
    assert.deepEqual(Array.from(renamed.passageReviewItems[1].fragmentProblemIds), ['a-passage-4-6-add-2']);
    """)


# --- G16: R72's 종료 x R48 ----------------------------------------------------------------------

def test_quit_with_an_open_area_editor_asks_about_unsaved_changes() -> None:
    run_node(NODE_SETUP + r"""
    const prompts = [];
    let memo = null;
    const sandbox = {
      // React's useCallback: the same function until a listed dependency changes.
      useCallback: (fn, deps) => {
        if (!memo || deps.some((dep, index) => !Object.is(dep, memo.deps[index]))) memo = { fn, deps };
        return memo.fn;
      },
      useRef: value => ({ current: value }), useEffect() {}, showToast() {}, showSimpleErrorToast() {},
      window: { addEventListener() {}, removeEventListener() {}, confirm: message => { prompts.push(message); return false; } },
      postShutdown: async () => { throw new Error('a declined quit never reaches the app'); },
    };
    vm.createContext(sandbox);
    // Each render gets its own values, as App's locals do.
    const render = vm.runInContext('(function ({ sessionEditState, workInProgress, reviewEditorActive }) {\n'
      + section('  // Warn before the tab closes', '  const refreshSession = useCallback(') + '\nreturn shutdownApp;\n})', sandbox);
    (async () => {
      const idle = { sessionEditState: { dirty: false }, workInProgress: false };
      await render({ ...idle, reviewEditorActive: false })();
      assert.match(prompts.at(-1), /^앱을 종료할까요\?/, 'a saved idle board gets the plain question');
      // Opening 원본 편집 changes neither dirty nor workInProgress: the memoized 종료 must still see it.
      await render({ ...idle, reviewEditorActive: true })();
      assert.match(prompts.at(-1), /^저장하지 않은 수업 변경이 있어요/, 'areas drawn in an open 원본 편집 are unsaved work');
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


# --- G21: R43's upload_source_changed x R28's other import method ------------------------------

def test_a_file_refused_before_sending_is_not_offered_the_other_import_method() -> None:
    run_node(NODE_SETUP + r"""
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
    const render = (alternativeLabel, code) => text(banner.OperationRecoveryBanner({ onRetry() {},
      recovery: { kind: 'recognition', alternativeLabel, retryKeys: ['bad'], error: { code, message: 'x', retryable: false } } }));
    // The other method uploads the same File objects, so it fails the same way before sending.
    for (const code of ['upload_source_changed', 'upload_file_too_large', 'upload_batch_too_large']) {
      const shown = render('페이지 PNG로 등록', code);
      assert.equal(shown.includes('페이지 PNG로 등록'), false, `${code}: ${shown}`);
      assert.ok(shown.includes('문제 신고 열기'), shown);
    }
    assert.ok(render('수동 자르기로 열기', 'pdf_invalid_document').includes('수동 자르기로 열기'), 'other failures keep it');
    """)
