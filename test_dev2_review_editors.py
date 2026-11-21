"""Regression tests for the review-editors chain: Gemini failures on AI 재인식 and
import (R8), editors that stay in 검수 (R13), short laptop screens (R5), asking
before drawn areas are dropped (R48), the import toast's 확인하기 (R75), 확인
after a re-crop that still overlaps (R77), 영역 넓히기 (R84) and one name per
crop tool (R85, see also test_dev_ui_copy.py)."""
from __future__ import annotations

import os
import re
import tempfile
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import app_server
from test_app_server_retry import _build_session, _fake_run_problem_export
from test_dev_srv_jobs import make_handler, server  # noqa: F401 - server is a pytest fixture
from test_dev_ui_editor import KEY_SETUP
from test_dev_ui_review import guard_issue_counts, overlapping_pair, python_issue_counts
from test_ui_home_flows import NODE_SETUP, run_node
from test_ui_recognition_auto_apply import SETUP as RECOGNITION_SETUP

PROJECT_ROOT = Path(__file__).resolve().parent
APP = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
CSS = (PROJECT_ROOT / "ui_prototype" / "board.css").read_text(encoding="utf-8")
OFFLINE = "AI repair failed after model fallback: Gemini request failed: [WinError 10061]"


# R8 -------------------------------------------------------------------------

def _offline_export(source_path, **kwargs):
    # Gemini unreachable: the pipeline raises only when asked to; otherwise it returns local results.
    if kwargs.get("fail_on_ai_error"):
        raise RuntimeError(OFFLINE)
    return _fake_run_problem_export(source_path, **kwargs)


def _retry_offline(session: dict, payload: dict) -> dict:
    with (
        patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
        patch.object(app_server, "_global_ai_enabled", return_value=True),
        patch.object(app_server, "run_problem_export", side_effect=_offline_export),
    ):
        return app_server._mutate_retry_ai(session, payload)


def test_ai_retry_keeps_the_page_when_gemini_cannot_be_reached(tmp_path):
    session = _build_session(tmp_path, present_page_ids={"page-1"})
    session["output_dir"] = str(tmp_path / "out")
    new_session = _retry_offline(session, {"pageIds": ["page-1"]})
    [row] = new_session["ai_retry_summary"]
    assert row["status"] == "failed" and "10061" in row["error"]
    page = next(page for page in new_session["pages"] if page["id"] == "page-1")
    assert page["problemIds"] == ["page-1-p1"]
    assert "page-1-new" not in {problem["id"] for problem in new_session["problems"]}


def test_partial_ai_retry_keeps_the_problem_when_gemini_cannot_be_reached(tmp_path):
    page_path = tmp_path / "page-1.png"
    Image.new("RGB", (240, 180), "white").save(page_path)
    boxes = [{"left": 20, "top": 30, "width": 80, "height": 90}, {"left": 120, "top": 30, "width": 80, "height": 90}]
    session = {
        "output_dir": str(tmp_path / "out"),
        "pages": [{"id": "page-1", "sourceImageUri": page_path.resolve().as_uri(), "problemIds": ["p1", "p2"]}],
        "problems": [
            {"id": f"p{index}", "sourcePageId": "page-1", "bbox": dict(box), "riskFlags": []}
            for index, box in enumerate(boxes, start=1)
        ],
        "ai_fallback": {"provider": "gemini"},
    }
    new_session = _retry_offline(session, {
        "partial": True, "problemIds": ["p1"], "cropBox": {"left": 10, "top": 20, "width": 80, "height": 90},
    })
    [row] = new_session["ai_retry_summary"]
    assert row["status"] == "failed" and "10061" in row["error"]
    assert [problem["id"] for problem in new_session["problems"]] == ["p1", "p2"]
    assert [problem["bbox"] for problem in new_session["problems"]] == boxes


def test_retry_answers_502_only_when_no_page_got_an_ai_result(server):  # noqa: F811
    before = server.session_snapshot()
    failed = {"pageId": "page-1", "status": "failed", "error": OFFLINE}

    def retry_with(rows):
        def fake_retry(session, payload, *, should_cancel=None):
            session["problems"] = [{"id": "local-result"}]
            session["ai_retry_summary"] = [dict(row) for row in rows]
            return session
        return fake_retry

    for preview in (True, False):
        handler = make_handler(server, "/api/session/retry-ai", {"preview": preview})
        with patch.object(app_server, "_mutate_retry_ai", side_effect=retry_with([failed])):
            handler._handle_session_retry_ai()
        body, status = handler.responses[0]
        assert status == HTTPStatus.BAD_GATEWAY, preview
        assert (body["ok"], body["code"], body["operation"], body["retryable"]) == (
            False, "ai_retry_not_applied", "session_retry_ai", True)
        assert re.search("[가-힣]", body["error"]) and "그대로" in body["error"]
        assert "page-1" in body["detail"] and "10061" in body["detail"]
        assert "Gemini API 키" in body["recoverySteps"][0]
        assert server.session_snapshot() == before, "nothing from a failed retry is saved"

    applied = {"pageId": "page-2", "status": "applied", "replacedProblemCount": 1}
    handler = make_handler(server, "/api/session/retry-ai", {"preview": True})
    with patch.object(app_server, "_mutate_retry_ai", side_effect=retry_with([failed, applied])):
        handler._handle_session_retry_ai()
    body, status = handler.responses[0]
    assert (body["ok"], status) == (True, None), "one applied page still returns its result"
    assert [row["status"] for row in body["retry"]] == ["failed", "applied"]


def test_failed_retry_toast_says_the_items_stayed_and_what_to_check() -> None:
    run_node(NODE_SETUP + r"""
    const sandbox = { isNetworkRequestError: () => false };
    vm.createContext(sandbox);
    vm.runInContext(section('function simpleToastErrorMessage(', 'async function fetchUserSettings('), sandbox);
    const message = sandbox.simpleToastErrorMessage({ code: 'ai_retry_not_applied',
      message: 'AI 결과를 받지 못해 문항을 그대로 두었어요. Gemini request failed' }, 'AI 재인식 실패');
    assert.equal(message, 'AI 재인식 실패 · 문항은 그대로예요 · 인터넷 연결과 Gemini API 키·사용량을 확인해 주세요');
    """)


def test_mixed_ai_retry_toast_names_the_pages_left_as_they_were() -> None:
    run_node(RECOGNITION_SETUP + r"""
    (async () => {
      const applied = { pageId: 'pg1', status: 'applied', replacedProblemCount: 1 };
      for (const [rows, expected] of [
        [[applied, { pageId: 'pg2', status: 'failed', error: 'Gemini request failed: [WinError 10061]' }],
          'AI 결과를 받지 못한 1쪽은 그대로 뒀어요 · 인터넷 연결과 Gemini API 키·사용량을 확인해 주세요 · AI 인식 적용 · 1 item · Ctrl+Z로 실행 취소'],
        [[applied], 'AI 인식 적용 · 1 item · Ctrl+Z로 실행 취소'],
      ]) {
        const { sandbox: s, events } = transaction({
          PRIMARY_MODIFIER_LABEL: 'Ctrl',
          recognitionReview: { kind: 'retry-ai', session: { problems: [{ id: 'p1' }] }, pageIds: ['pg1', 'pg2'],
            problemIds: [], snapshotBefore: { problems: [{ id: 'existing' }] }, retrySummary: rows },
        });
        await s.applyResult();
        assert.deepEqual(events.filter(event => event[0] === 'toast').map(event => event[1]), [expected]);
      }
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_import_notice_names_pages_split_without_ai() -> None:
    run_node(NODE_SETUP + r"""
    const sandbox = {
      sessionReviewSummary: () => ({ reviewTargets: [] }),
      countSessionProblems: list => ({ total: list.length, core: list.length, supplemental: 0 }),
      reviewStageCopyFor: (_session, counts) => ({ countLabel: `${counts.core}문항` }),
    };
    vm.createContext(sandbox);
    vm.runInContext(section('function importResultNotice(', 'function mergeRetryCandidateIntoCurrent('), sandbox);
    const scope = { scopeProblemIds: ['p1'], scopePageIds: [] };
    const restored = { problems: [{ id: 'p1' }] };
    const plain = '1문항 칠판에 넣었어요 · 순서를 확인하고 EDB 제작을 누르세요';
    const notice = incoming => sandbox.importResultNotice(scope, incoming, restored).message;
    assert.equal(notice({ ai_summary: { requested: true, status_counts: { error: 2, applied: 1 } } }),
      `AI 연결 실패 2쪽은 AI 없이 나눴어요 · 인터넷 연결과 Gemini API 키·사용량을 확인해 주세요 · ${plain}`);
    assert.ok(notice({ aiSummary: { requested: true, statusCounts: { error: 1 } } }).startsWith('AI 연결 실패 1쪽'));
    for (const incoming of [{ ai_summary: { requested: true, status_counts: { applied: 3 } } },
      { ai_summary: { requested: false, status_counts: { error: 1 } } }, {}]) {
      assert.equal(notice(incoming), plain);
    }
    assert.ok(sandbox.importResultNotice(scope, { ai_summary: { requested: true, status_counts: { error: 1 } } },
      restored, 'Gemini 3 호출 오류').message.startsWith('Gemini 3 호출 오류 · AI 연결 실패 1쪽'));
    """)


# R13 ------------------------------------------------------------------------

def test_closing_an_editor_returns_to_the_board_only_when_it_started_there() -> None:
    run_node(NODE_SETUP + r"""
    const calls = [];
    const record = kind => value => calls.push([kind, value]);
    const sandbox = { useRef: value => ({ current: value }), useCallback: fn => fn,
      setView: record('view'), setReviewFocus: record('focus'), setReviewEditorActive: record('active') };
    vm.createContext(sandbox);
    vm.runInContext(section('  const sourceEditorWasActiveRef = useRef(false);', '  const [reviewEditScope, setReviewEditScope]')
      + '\nglobalThis.api = { handleSourceEditorMode, editorReturnsToBoardRef };', sandbox);
    const moves = () => calls.filter(([kind]) => kind !== 'active');
    const { api } = sandbox;
    api.handleSourceEditorMode(true);
    api.handleSourceEditorMode(false);
    assert.deepEqual(moves(), [], '적용/취소 inside 검수 stays in 검수');
    api.editorReturnsToBoardRef.current = true;
    api.handleSourceEditorMode(true);
    api.handleSourceEditorMode(false);
    assert.deepEqual(moves(), [['view', 'board'], ['focus', null]], '영역 조정 from the 칠판 goes back');
    assert.equal(api.editorReturnsToBoardRef.current, false);
    calls.length = 0;
    api.handleSourceEditorMode(true);
    api.handleSourceEditorMode(false);
    assert.deepEqual(moves(), [], 'the return is one-shot');

    // Leaving 검수 drops the one-shot edit request and the return flag; a finished 직접 자르기 queue still ends on the 칠판.
    calls.length = 0;
    const flow = { useCallback: fn => fn, useEffect: fn => fn(), view: 'board', editorReturnsToBoardRef: { current: true },
      setView: record('view'), setReviewFocus: record('focus'), setReviewEditorActive: record('active') };
    vm.createContext(flow);
    vm.runInContext(section('  // A queued 직접 자르기 request is one-shot', '  useEffect(() => {\n    historyStackRef.current')
      + '\nglobalThis.finish = finishManualSplitFlow;', flow);
    const strip = calls.find(([kind]) => kind === 'focus')[1];
    assert.equal(JSON.stringify(strip({ filter: 'all', problemIds: ['p1'], editProblemId: 'p1', manualSplitPageId: 'pg1' })),
      JSON.stringify({ filter: 'all', problemIds: ['p1'] }), 'reopening 검수 must not reopen the editor');
    assert.equal(flow.editorReturnsToBoardRef.current, false);
    calls.length = 0;
    flow.finish();
    assert.deepEqual(calls, [['view', 'board'], ['focus', null]]);

    const onEdit = section('onEditOriginal={id=>{', 'onDownloadItemImage=');
    const flag = onEdit.indexOf("editorReturnsToBoardRef.current = view === 'board';");
    assert.ok(flag >= 0 && flag < onEdit.indexOf("requestViewChange('review')"), onEdit);
    """)


# R5 -------------------------------------------------------------------------

def test_short_desktop_review_leaves_room_for_the_page() -> None:
    rule = CSS.split("@media (min-width: 901px) and (max-height: 760px){", 1)[1].split("\n  }", 1)[0]
    assert ".unified-source-editor:not(.is-editing) .review-actionbar{ position: static; }" in rule
    assert not re.search(r"(?m)^\s*\.review-actionbar\{", rule), "crop editor bars must stay sticky"
    assert ".review-completion-bar{ min-height: 0;" in rule
    assert ".workspace-session-actions > summary{ min-height: 28px; }" in rule
    assert ".review-stage-toolbar .review-zoom-controls{ padding: 0; border: 0; background: transparent; }" in rule
    assert ".original-toolbar-help{ display: none; }" in rule
    assert ".review-summary-strip" not in rule, "warning and overlap chips stay reachable"
    assert ".workspace-session-actions{ display: none" not in rule
    assert ".home-hero-dropzone{ padding-block: 20px; gap: 8px; }" in rule


# R48 ------------------------------------------------------------------------

def test_closing_the_manual_split_asks_before_drawn_areas_are_lost() -> None:
    run_node(NODE_SETUP + r"""
    let state, prompts = [], answer = false, flowDone = 0;
    const sandbox = {
      window: { confirm: message => { prompts.push(message); return answer; } },
      manualSplitDragRef: { current: null }, manualSplitCommitRef: { current: false },
      setManualSplitDraftBox() {}, setManualSplit: value => { state = value; }, setManualSplitShortcutHelpOpen() {},
      setGuidedSplitPageIds() {}, onManualSplitFlowDone: () => { flowDone += 1; },
    };
    vm.createContext(sandbox);
    vm.runInContext(section('  const cancelManualPageSplit = () => {', '  const setManualSplitMode =')
      + '\nglobalThis.cancel = cancelManualPageSplit;', sandbox);
    const open = { regions: [{ id: 'r1' }, { id: 'r2' }], guided: true };
    state = sandbox.manualSplit = open;
    sandbox.cancel();
    assert.equal(state, open, 'declining keeps the drawn areas and their undo history');
    assert.equal(flowDone, 0, 'declining keeps the guided queue');
    // The toolbar button is '취소' too, so the dialog says what its own 취소 does.
    assert.equal(prompts[0], '적용하지 않은 영역 2개가 사라집니다. 편집을 닫을까요?\n취소를 누르면 계속 편집할 수 있어요.');
    answer = true;
    sandbox.cancel();
    assert.equal(state, null);
    assert.equal(flowDone, 1);
    prompts = [];
    state = sandbox.manualSplit = { regions: [], guided: false };
    sandbox.cancel();
    assert.equal(state, null);
    assert.equal(prompts.length, 0, 'nothing drawn closes without asking');
    """)


def test_closing_the_box_editor_asks_only_when_drawn_areas_would_be_lost() -> None:
    run_node(NODE_SETUP + r"""
    let state, prompts = [], answer = false;
    const sandbox = {
      finiteNumber: (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback,
      window: { confirm: message => { prompts.push(message); return answer; } },
      boxEditDragRef: { current: null }, boxEditCommitRef: { current: false },
      setBoxEditDraftBox() {}, setBoxEdit: value => { state = value; },
      originalModeDrafts: { current: { crop: null, split: null } },
    };
    vm.createContext(sandbox);
    vm.runInContext(section('function reviewBoxesEqual(', 'function editableSourceSegmentsForProblem(')
      + section('function boxEditSegmentsEqual(', 'function renumberBoxEditSegments(')
      + section('  const cancelBoxEdit = () => {', '  const resetBoxEdit =') + '\nglobalThis.cancel = cancelBoxEdit;', sandbox);
    const segment = (id, top) => ({ id, pageId: 'pg', bbox: { left: 0, top, width: 10, height: 10 } });
    const changed = { multi: true, originalSegments: [segment('a', 0)], segments: [segment('a', 0), segment('b', 50)] };
    state = sandbox.boxEdit = changed;
    sandbox.cancel();
    assert.equal(state, changed, 'declining keeps the stitched areas');
    assert.equal(prompts[0], '적용하지 않은 영역 변경이 사라집니다. 편집을 닫을까요?\n취소를 누르면 계속 편집할 수 있어요.');
    answer = true;
    sandbox.cancel();
    assert.equal(state, null);
    prompts = [];
    state = sandbox.boxEdit = { multi: true, originalSegments: [segment('a', 0)], segments: [segment('a', 0)] };
    sandbox.cancel();
    assert.equal(state, null);
    state = sandbox.boxEdit = { multi: false, originalBox: segment('a', 0).bbox, box: segment('a', 40).bbox,
      originalSegments: [segment('a', 0)], segments: [segment('a', 40)] };
    sandbox.cancel();
    assert.equal(state, null);
    assert.equal(prompts.length, 0, 'an untouched passage and a single 영역 조정 close without asking');

    // 수동 자르기 → 영역 조정 tab: the drawn areas wait in originalModeDrafts and close with the editor.
    answer = false;
    sandbox.originalModeDrafts.current.split = { regions: [{ id: 'r1' }, { id: 'r2' }] };
    const single = { multi: false, originalSegments: [segment('a', 0)], segments: [segment('a', 0)] };
    state = sandbox.boxEdit = single;
    sandbox.cancel();
    assert.equal(state, single, 'declining keeps the areas drawn in 수동 자르기');
    assert.deepEqual(prompts, ['적용하지 않은 영역 2개가 사라집니다. 편집을 닫을까요?\n취소를 누르면 계속 편집할 수 있어요.']);
    answer = true;
    sandbox.cancel();
    assert.equal(state, null);
    """)


def test_esc_leaves_add_area_mode_before_it_closes_the_editor() -> None:
    run_node(KEY_SETUP + r"""
    const calls = [];
    Object.assign(sandbox, { mutating: false, applyBoxEdit: async () => {}, nudgeBoxEditSelection() {},
      deleteBoxEditSegment() {}, cancelBoxEdit: () => calls.push('cancel'),
      toggleBoxEditAddSegment: () => calls.push('toggle') });
    const effect = section("  useEffect(() => {\n    if (!boxEdit || mutating) return undefined;\n    const onBoxEditKeyDown",
      "  useLayoutEffect(() => {");
    for (const addingSegment of [true, false]) {
      listeners.length = 0;
      sandbox.boxEdit = { multi: true, addingSegment, segments: [{ id: 's1' }] };
      vm.runInContext(effect, sandbox);
      press('Escape', boardTarget);
    }
    assert.deepEqual(calls, ['toggle', 'cancel']);
    """)


# R75 ------------------------------------------------------------------------

def test_check_action_opens_the_added_files_first_check() -> None:
    run_node(NODE_SETUP + r"""
    const selected = [];
    const sandbox = {
      items: [{ id: 'p1' }, { id: 'p3' }],
      sessionReviewSummary: () => ({ reviewTargets: [
        { id: 'p1', pageId: 'pg1', status: 'check_needed', type: 'problem', problemIds: ['p1'] },
        { id: 'p2', pageId: 'pg2', status: 'normal', type: 'problem', problemIds: ['p2'] },
        { id: 'p3', pageId: 'pg2', status: 'check_needed', type: 'problem', problemIds: ['p3'] },
      ] }),
      selectBoardItem: id => selected.push(id),
      setSelectedProblemIds() {}, revealSidePanel() {}, setReviewFocus() {}, requestViewChange() {},
    };
    vm.createContext(sandbox);
    vm.runInContext(section('  const openFirstReviewTarget = ', '  // Toast actions fire after later renders')
      + '\nglobalThis.open = openFirstReviewTarget;', sandbox);
    const session = {};
    sandbox.open(session, { scopeProblemIds: ['p2', 'p3'], scopePageIds: ['pg2'] });
    sandbox.open(session);  // the 제작 gate and the board's 확인 필요 button stay lesson-wide
    sandbox.open(session, { scopeProblemIds: ['gone'], scopePageIds: [] });  // nothing left in the file
    assert.deepEqual(selected, ['p3', 'p1', 'p1']);
    """)


def test_import_toast_check_action_passes_the_imports_scope() -> None:
    run_node(RECOGNITION_SETUP + r"""
    (async () => {
      const scope = { scopeProblemIds: ['new'], scopePageIds: ['pg2'], source: 'queue-recognition' };
      const opened = [];
      const { sandbox: s, events } = transaction({
        importResultNotice: () => ({ checkCount: 1, firstNewProblemId: 'new', message: '1문항 칠판에 넣었어요 · 확인 필요 1개' }),
        reviewFocusForNewSession: () => scope,
        openPendingReviewRef: { current: value => opened.push(value) },
      });
      await s.applyResult();
      const action = events.find(event => event[0] === 'actionToast');
      action[3]({ type: 'click' });
      assert.equal(opened.length, 1);
      assert.equal(opened[0], scope, 'the click event never reaches the jump');
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


# R77 ------------------------------------------------------------------------

def test_recrop_that_still_overlaps_asks_for_confirm_again() -> None:
    # A re-crop voids the 확인 record; 검수 must offer 확인 again, not only block 제작.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        page = root / "page.png"
        Image.new("RGB", (800, 1000), "white").save(page)
        for passage, key, flag in ((False, "overlap", "source_problem_bbox_overlap"),
                                   (True, "reuse", "passage_group_source_reuse")):
            session = {"problems": overlapping_pair(root, passage=passage), "output_dir": str(root / "out"),
                       "pages": [{"id": "page-1", "sourceImageUri": page.resolve().as_uri(), "problemIds": ["p1", "p2"]}]}
            app_server._mutate_confirm(session, ["p1", "p2"])
            session = app_server._mutate_crop(session, "p1", {"cropBox": {"left": 40, "top": 100, "width": 500, "height": 290}})
            assert [p["reviewStatus"] for p in session["problems"]] == ["check_needed"] * 2, key
            assert all(flag in p["riskFlags"] for p in session["problems"]), key
            app_server._mutate_confirm(session, ["p1", "p2"])
            assert python_issue_counts(session["problems"])[key] == 0, key
            assert guard_issue_counts(app_server.rewrite_session_for_http(session)["problems"])[key] == 0, key
            # A re-crop that no longer overlaps asks for nothing.
            session = app_server._mutate_crop(session, "p1", {"cropBox": {"left": 40, "top": 0, "width": 500, "height": 100}})
            assert [p["reviewStatus"] for p in session["problems"]] == ["normal"] * 2, key


# R84 ------------------------------------------------------------------------

def test_widen_button_says_what_it_does_and_skips_stitched_passages() -> None:
    assert "{Icon.crop} 영역 넓히기" in APP
    head = APP.split("{Icon.crop} 영역 넓히기", 1)[0]
    button = head[head.rindex("<button"):]
    assert "applyExpandedCrop()" in button
    assert "title={`글자가 잘렸을 때 영역을 상하좌우로 넓혀 바로 적용해요 · ${PRIMARY_MODIFIER_LABEL}+Z로 되돌릴 수 있어요`}" in button
    assert "!isPassageFragmentProblem(selectedSingleProblem)" in head[head.rindex("{selectedList.length === 1 && ("):]
