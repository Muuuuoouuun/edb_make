"""Local board undo, server redo, manual-split undo, enhance save-first and
the crash-safe board draft (ui-undo chain: C9, C14, C12, C24, C28)."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from test_ui_manual_split_performance import SHORTCUT_SETUP
from test_ui_placement_details import NODE_SETUP, run_node
from test_ui_state_preservation import STATE_SETUP
from test_ui_toolbar_controls import NODE_SETUP as TOOLBAR_SETUP


PROJECT_ROOT = Path(__file__).resolve().parent

UNDO_SETUP = STATE_SETUP + r"""
function undoSandbox() {
  const { sandbox, state } = stateSandbox();
  Object.defineProperty(sandbox, 'itemsRef', { value: { get current() { return sandbox.items; } } });
  Object.defineProperty(sandbox, 'layoutGapModeRef', { value: { get current() { return sandbox.layoutGapMode; } } });
  Object.defineProperty(sandbox, 'boardColumnsRef', { value: {
    get current() { return sandbox.boardColumns; }, set current(value) { sandbox.boardColumns = value; } } });
  Object.assign(sandbox, {
    setActionToast: value => { sandbox.actionToast = value; }, showActionToast() {},
    setTweak: (key, value) => { sandbox[key] = value; },
  });
  vm.runInContext(section('  const pushLocalEdit = (burstKey', '  // A legacy session keeps')
    + section('  const setPlacements = (updates) =>', '  const setPlacement = (id, patch)')
    + '\nglobalThis.pushLocalEdit = pushLocalEdit; globalThis.setPlacements = setPlacements;'
    + '\nglobalThis.redoMutation = redoMutation;', sandbox);
  return { sandbox, state };
}
// A server step to undo: the saved lesson before a rename.
function withServerStep(sandbox) {
  const older = { ...JSON.parse(JSON.stringify(sandbox.session)), session_name: '이전 수업' };
  sandbox.setHistoryStack([older]);
  return older;
}
"""


class TestUndoStackHelper(unittest.TestCase):
    def test_push_dedupes_coalesces_bursts_caps_and_pops(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = { appendBoundedHistory: require('./ui_prototype/reorder.js').appendBoundedHistory };
        vm.createContext(sandbox);
        vm.runInContext('const UNDO_HISTORY_LIMIT = 20;'
          + section('function pushUndoEntry(', 'const adjacentReorderCommand ='), sandbox);
        const state = { stack: [], burstKey: '', burstAt: 0 };
        const a = ['a'], b = ['b'];
        assert.equal(sandbox.pushUndoEntry(state, a, '', 20, 1000), true);
        assert.equal(sandbox.pushUndoEntry(state, a, '', 20, 1001), false, 'same value as top is a no-op');
        assert.equal(sandbox.pushUndoEntry(state, b, 'slider', 20, 2000), true);
        assert.equal(sandbox.pushUndoEntry(state, ['c'], 'slider', 20, 2500), false, 'a burst is one step');
        assert.equal(sandbox.pushUndoEntry(state, ['d'], 'slider', 20, 3400), false, 'the burst window slides');
        assert.equal(sandbox.pushUndoEntry(state, ['e'], 'slider', 20, 4500), true, 'a pause starts a new step');
        assert.equal(sandbox.pushUndoEntry(state, ['f'], 'other', 20, 4600), true, 'another control is its own step');
        assert.equal(state.stack.length, 4);
        assert.deepEqual(sandbox.popUndoEntry(state), ['f']);
        assert.equal(state.burstKey, '', 'undo ends the burst so the next edit is recorded');
        const capped = { stack: [], burstKey: '', burstAt: 0 };
        for (let index = 0; index < 35; index++) sandbox.pushUndoEntry(capped, [index], '', 30, index);
        assert.equal(capped.stack.length, 30);
        assert.deepEqual(capped.stack[0], [5], 'the oldest steps fall off first');
        """)


class TestLocalBoardUndo(unittest.TestCase):
    def test_local_edits_undo_first_then_the_saved_step(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox, state } = undoSandbox();
          withServerStep(sandbox);
          const original = sandbox.items;
          sandbox.setPlacements([{ id: 'missing', patch: { xRatio: 0.3 } }]);
          assert.equal(sandbox.localEditHistoryRef.current.stack.length, 0, 'a no-op placement is not an undo step');
          for (const scale of [0.9, 0.85, 0.8]) sandbox.setPlacements([{ id: 'p1', patch: { scaleRatio: scale } }]);
          assert.equal(sandbox.localEditHistoryRef.current.stack.length, 1, 'one slider drag is one step');
          const scaled = sandbox.items;
          sandbox.setPlacements([{ id: 'p1', patch: { xRatio: 0.3 } }]);
          assert.equal(sandbox.localEditHistoryRef.current.stack.length, 2);
          assert.equal(await sandbox.undoMutation(), true);
          assert.equal(sandbox.items, scaled, 'Ctrl+Z reverts only the last local edit');
          assert.equal(state.restores, 0, 'local undo never touches the saved lesson');
          assert.equal(sandbox.historyStack.length, 1);
          await sandbox.undoMutation();
          assert.equal(sandbox.items, original);
          assert.equal(state.restores, 0);
          await sandbox.undoMutation();
          assert.equal(state.restores, 1, 'with no local edits left, undo restores the saved step');
          assert.equal(sandbox.historyStack.length, 0);
          assert.equal(sandbox.fileName, '이전 수업');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_bulk_step_and_selected_step_are_undoable(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox } = undoSandbox();
          Object.assign(sandbox, { stepLabel: step => step, PRIMARY_MODIFIER_LABEL: 'Ctrl' });
          vm.runInContext(section('  const applySelectedStep =', '  const classifySelected =')
            + section('  const applyToAll =', '  const setPlacements =')
            + '\nglobalThis.applyToAll = applyToAll; globalThis.applySelectedStep = applySelectedStep;', sandbox);
          const before = sandbox.items.map(item => item.step).join(',');
          sandbox.applyToAll('s2');
          assert.equal(sandbox.items.map(item => item.step).join(','), 's2,s2');
          sandbox.applySelectedStep(['p1'], 'raw');
          await sandbox.undoMutation();
          assert.equal(sandbox.items.map(item => item.step).join(','), 's2,s2');
          await sandbox.undoMutation();
          assert.equal(sandbox.items.map(item => item.step).join(','), before, '전체 칠판용 can be taken back');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_column_and_gap_mode_changes_undo_one_step_at_a_time(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox, state } = undoSandbox();
          withServerStep(sandbox);
          sandbox.window.confirm = () => true;
          const start = source.indexOf('setBoardColumns={v => {');
          const changeColumns = source.slice(start + 'setBoardColumns={'.length, source.indexOf('}}', start) + 1);
          vm.runInContext(section('  const updateLayoutGapMode =', '  const resetPlacementGaps =')
            + `\nglobalThis.updateLayoutGapMode = updateLayoutGapMode; globalThis.setBoardColumns = ${changeColumns};`, sandbox);
          sandbox.boardColumns = 2;  // a legacy two-column lesson
          const original = sandbox.items;
          sandbox.setPlacements([{ id: 'p1', patch: { xRatio: 0.3 } }]);
          const moved = sandbox.items;
          sandbox.setBoardColumns(1);
          sandbox.setBoardColumns(1);
          assert.equal(sandbox.localEditHistoryRef.current.stack.length, 2, 'picking the same column count is not a step');
          sandbox.updateLayoutGapMode('compact');
          assert.equal(sandbox.localEditHistoryRef.current.stack.length, 3);
          assert.notEqual(sandbox.items, moved);
          await sandbox.undoMutation();
          assert.equal(sandbox.layoutGapMode, 'classin-grid', 'Ctrl+Z takes back the 여백 change');
          assert.equal(sandbox.items, moved);
          assert.equal(sandbox.boardColumns, 1);
          await sandbox.undoMutation();
          assert.equal(sandbox.boardColumns, 2, 'then the column change');
          assert.equal(sandbox.items, moved, 'and the move survives both');
          await sandbox.undoMutation();
          assert.equal(sandbox.items, original);
          assert.equal(state.restores, 0, 'none of this touched the saved lesson');
          await sandbox.undoMutation();
          assert.equal(state.restores, 1);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_only_a_new_lesson_clears_local_history(self) -> None:
        run_node(NODE_SETUP + r"""
        const effects = [];
        const sandbox = {
          useEffect: (fn, deps) => effects.push({ fn, deps }), session: 'lesson', usingMock: false,
          boardColumns: 2, layoutGapMode: 'compact', placementGapHistoryRef: { current: [] },
          localEditHistoryRef: { current: null }, setPlacementGapUndoCount() {}, setLocalEditUndoCount() {},
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  // Gap undo belongs', '  const boardColumnsRef = useRef(boardColumns);'), sandbox);
        const clearing = effects.filter(effect => {
          sandbox.localEditHistoryRef.current = { stack: ['entry'] };
          effect.fn();
          return !sandbox.localEditHistoryRef.current.stack.length;
        });
        assert.deepEqual(clearing.map(effect => [...effect.deps]), [['lesson', false]],
          'a column or 여백 change must not wipe the local undo history');
        """)

    def test_sample_board_reorder_is_one_undo_step(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox } = undoSandbox();
          const helpers = require('./ui_prototype/reorder.js');
          Object.assign(sandbox, {
            session: null, PRIMARY_MODIFIER_LABEL: 'Ctrl', moveSequenceRef: { current: 0 },
            moveFeedbackTimerRef: { current: null }, setMoveFeedback() {},
            reorderItemsForDrop: helpers.reorderItemsForDrop, reorderItemGroupForDrop: helpers.reorderItemGroupForDrop,
          });
          Object.assign(sandbox.window, { setTimeout: () => 1, clearTimeout() {} });
          vm.runInContext(section('  const reorder = (fromId, toId', '  const removeItem = async')
            + '\nglobalThis.reorder = reorder;', sandbox);
          sandbox.setPlacements([{ id: 'p1', patch: { xRatio: 0.3 } }]);
          const moved = sandbox.items;
          assert.equal(sandbox.reorder('p1', 'p2', 'after'), true);
          assert.equal(sandbox.items.map(item => item.id).join(','), 'p2,p1');
          await sandbox.undoMutation();
          assert.equal(sandbox.items, moved, 'Ctrl+Z takes back only the reorder');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_a_new_board_edit_retires_an_older_toast_undo(self) -> None:
        run_node(UNDO_SETUP + r"""
        const { sandbox } = undoSandbox();
        sandbox.actionToast = { message: '1개 문제를 삭제했어요', actionLabel: '실행 취소' };
        sandbox.setPlacements([{ id: 'p1', patch: { xRatio: 0.3 } }]);
        assert.equal(sandbox.actionToast, null, 'its button would now undo this edit instead of the deletion');
        """)


class TestServerRedo(unittest.TestCase):
    def test_undo_then_redo_restores_the_newer_state(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox, state } = undoSandbox();
          withServerStep(sandbox);
          const newerName = sandbox.fileName;
          await sandbox.undoMutation();
          assert.equal(sandbox.fileName, '이전 수업');
          assert.equal(sandbox.redoStackRef.current.length, 1);
          assert.equal(await sandbox.redoMutation(), true);
          assert.equal(state.restores, 2);
          assert.equal(sandbox.fileName, newerName);
          assert.equal(sandbox.historyStack.length, 1, 'redo puts the older state back on the undo history');
          assert.equal(sandbox.historyStack[0].session_name, '이전 수업');
          assert.equal(sandbox.redoStackRef.current.length, 0);
          await sandbox.undoMutation();
          assert.equal(sandbox.fileName, '이전 수업', 'undo and redo can alternate');
          assert.equal(await sandbox.redoMutation(), true);
          assert.equal(sandbox.fileName, newerName);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_any_newer_change_makes_redo_stale(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const changes = {
            'new history push': sandbox => sandbox.setHistoryStack(prev => sandbox.appendBoundedHistory(prev, sandbox.session, 20)),
            'history reset (conflict reload)': sandbox => { sandbox.historyStackRef.current = []; sandbox.setHistoryStack([]); },
            'session replaced (job, refresh, reset)': sandbox => { sandbox.session = JSON.parse(JSON.stringify(sandbox.session)); },
            'unsaved edit on the board': sandbox => { sandbox.sessionEditState = { dirty: true }; },
            'local edit pushed': sandbox => { sandbox.pushLocalEdit(); },
          };
          for (const [label, change] of Object.entries(changes)) {
            const { sandbox, state } = undoSandbox();
            withServerStep(sandbox);
            await sandbox.undoMutation();
            const restoresAfterUndo = state.restores;
            change(sandbox);
            assert.equal(await sandbox.redoMutation(), false, label);
            assert.equal(state.restores, restoresAfterUndo, `${label}: a stale redo must never be sent`);
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_failed_redo_or_undo_keeps_both_stacks(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox, state } = undoSandbox();
          withServerStep(sandbox);
          await sandbox.undoMutation();
          const history = sandbox.historyStack, redo = sandbox.redoStackRef.current;
          state.restoreError = new Error('offline');
          assert.equal(await sandbox.redoMutation(), false);
          assert.equal(sandbox.historyStack, history);
          assert.equal(sandbox.redoStackRef.current, redo);
          assert.equal(sandbox.mutatingRef.current, false);
          state.restoreError = null;
          assert.equal(await sandbox.redoMutation(), true, 'the kept redo still works after the failure');
          const historyAfterRedo = sandbox.historyStack;
          state.restoreError = new Error('offline');
          await sandbox.undoMutation();
          assert.equal(sandbox.historyStack, historyAfterRedo);
          assert.equal(sandbox.redoStackRef.current.length, 0);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_keyboard_routes_undo_and_redo_and_leaves_text_fields_alone(self) -> None:
        run_node(NODE_SETUP + r"""
        let handler;
        const calls = [];
        const sandbox = {
          useEffect: fn => fn(), canUndo: true, canRedo: true,
          window: { addEventListener: (_type, fn) => { handler = fn; }, removeEventListener() {} },
          undoMutation: async () => calls.push('undo'), redoMutation: async () => calls.push('redo'),
        };
        vm.createContext(sandbox);
        vm.runInContext(section('function manualSplitShortcutKey(', 'function translateManualSplitBoxes(')
          + section('function isEditableKeyboardTarget(', 'function resetItemPlacement(')
          + section('  // Ctrl/Cmd+Z → undo;', '  // initial session fetch'), sandbox);
        const fire = extra => { const event = { key: '', code: '', target: { tagName: 'DIV' },
          preventDefault() { this.prevented = true; }, ...extra }; handler(event); return event; };
        fire({ key: 'z', ctrlKey: true }); fire({ key: 'Z', metaKey: true, shiftKey: true });
        fire({ key: 'y', ctrlKey: true }); fire({ key: 'ㅋ', code: 'KeyZ', ctrlKey: true });
        assert.deepEqual(calls.splice(0), ['undo', 'redo', 'redo', 'undo']);
        fire({ key: 'z', ctrlKey: true, target: { tagName: 'INPUT' } });
        fire({ key: 'z' }); fire({ key: 'y', metaKey: true });
        sandbox.canRedo = false;
        assert.equal(fire({ key: 'z', ctrlKey: true, shiftKey: true }).prevented, undefined);
        assert.deepEqual(calls, []);
        """)

    def test_topbar_shows_redo_only_when_there_is_something_to_redo(self) -> None:
        subprocess.run(["node", "-e", TOOLBAR_SETUP + r"""
        const onRedo = () => {};
        const redoButton = tree => walk(tree).find(node => node.props?.['aria-label'] === '다시 실행');
        assert.equal(redoButton(sandbox.TopBar({ ...base, onRedo })), undefined);
        const shown = redoButton(sandbox.TopBar({ ...base, onRedo, canRedo: true }));
        assert.equal(shown.props.onClick, onRedo);
        assert.ok(!shown.props.disabled);
        assert.ok(shown.props.title.includes('Ctrl+Shift+Z'));
        assert.equal(redoButton(sandbox.TopBar({ ...base, onRedo, canRedo: true, operationBusy: true })).props.disabled, true);
        """], cwd=PROJECT_ROOT, check=True)


class TestEnhanceSavesBoardFirst(unittest.TestCase):
    def test_unsaved_board_is_saved_before_the_enhance_request(self) -> None:
        run_node(NODE_SETUP + r"""
        (async () => {
          const calls = [], errors = [];
          const draft = { problems: [{ id: 'p1', step: 'raw' }] };
          const sandbox = {
            useCallback: fn => fn, session: { problems: [{ id: 'p1' }] }, items: [], fileName: '수업',
            boardColumns: 1, layoutGapMode: 'classin-grid', aiEnabled: true, userSettings: {},
            materializeSessionForItems: () => draft, cloneSession: value => value,
            listUnique: values => [...new Set(values)],
            startBackgroundJob: () => ({ id: 'job', controller: new AbortController() }),
            settleBackgroundJob() {}, setHistoryStack() {}, adoptMutatedSession: next => calls.push(['adopt', next]),
            appendBoundedHistory: (history, entry) => [...history, entry], UNDO_HISTORY_LIMIT: 20,
            showToast() {}, showSimpleErrorToast: error => errors.push(error),
            postRestore: async snapshot => { calls.push(['restore', snapshot]); if (sandbox.restoreError) throw sandbox.restoreError; return snapshot; },
            postEnhanceImage: async () => { calls.push(['enhance']); return { session: draft, enhance: [] }; },
          };
          vm.createContext(sandbox);
          vm.runInContext(section('  const enhanceImageSession = useCallback(', '  const resetSession = useCallback(')
            + '\nglobalThis.enhanceImageSession = enhanceImageSession;', sandbox);
          await sandbox.enhanceImageSession(['p1'], { mode: 'preserve' });
          assert.deepEqual(calls.map(call => call[0]), ['restore', 'enhance', 'adopt']);
          assert.equal(calls[0][1], draft, 'the unsaved board is what gets saved');
          calls.length = 0;
          sandbox.restoreError = new Error('offline');
          await sandbox.enhanceImageSession(['p1'], { mode: 'preserve' });
          assert.deepEqual(calls.map(call => call[0]), ['restore'], 'no enhance runs on top of an unsaved board');
          assert.equal(errors.length, 1);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestManualSplitUndo(unittest.TestCase):
    def test_ctrl_z_is_the_editor_undo_key_outside_text_fields(self) -> None:
        run_node(SHORTCUT_SETUP + r"""
        sandbox.undoManualSplit = () => calls.push('undo');
        fire({ key: 'z', ctrlKey: true }); fire({ key: 'ㅋ', code: 'KeyZ', metaKey: true });
        assert.deepEqual(calls.splice(0), ['undo', 'undo']);
        fire({ key: 'z', ctrlKey: true, target: { tagName: 'INPUT' } });
        fire({ key: 'z', ctrlKey: true, shiftKey: true });
        assert.deepEqual(calls, []);
        """)

    def test_auto_split_merge_and_delete_undo_in_order_and_reset_on_page_change(self) -> None:
        run_node(NODE_SETUP + r"""
        (async () => {
          const notices = [];
          const hand = [
            { id: 'r1', pageId: 'page', bbox: { left: 0, top: 0, width: 100, height: 100 } },
            { id: 'r2', pageId: 'page', bbox: { left: 0, top: 120, width: 100, height: 100 } },
          ];
          const sandbox = {
            useRef: current => ({ current }), useEffect: fn => fn(), mutating: false, PRIMARY_MODIFIER_LABEL: 'Ctrl',
            manualSplit: { pageId: 'page', regions: hand, selectedRegionIds: [] },
            manualSplitPage: { id: 'page', width: 400, height: 600 },
            manualSplitDragRef: { current: null }, manualSplitSeqRef: { current: 10 },
            appendBoundedHistory: require('./ui_prototype/reorder.js').appendBoundedHistory, UNDO_HISTORY_LIMIT: 20,
            renumberManualSplitRegions: regions => regions, loadPageInkMask: async () => ({}),
            manualSplitAutoBoxes: (_mask, box) => [{ ...box, height: box.height / 2 }, { ...box, top: box.top + box.height / 2, height: box.height / 2 }],
            mergedManualSplitBox: boxes => boxes[0],
            setManualSplitNotice: notice => notices.push(notice), setManualSplitDraftBox() {},
          };
          sandbox.setManualSplit = update => {
            sandbox.manualSplit = typeof update === 'function' ? update(sandbox.manualSplit) : update;
            sandbox.manualSplitRef.current = sandbox.manualSplit;
          };
          vm.createContext(sandbox);
          vm.runInContext(section('function pushUndoEntry(', 'const adjacentReorderCommand =')
            + section('  const manualSplitRef = useRef(manualSplit);', '  const [manualSplitDraftBox')
            + section('  const deleteManualSplitSelected = () => {', '  const selectAllManualSplitRegions =')
            + section("  const undoManualSplit = () => {", '  // Splits the selected regions')
            + section('  const autoSplitManualSplitRegions = async () => {', '  useEffect(() => {\n    if (manualSplitPage && manualSplitTrimToInk)')
            + '\nglobalThis.manualSplitRef = manualSplitRef; globalThis.undo = undoManualSplit;'
            + '\nglobalThis.autoSplit = autoSplitManualSplitRegions; globalThis.merge = mergeManualSplitSelected;'
            + '\nglobalThis.deleteSelected = deleteManualSplitSelected; globalThis.history = manualSplitUndoRef;', sandbox);
          await sandbox.autoSplit();
          assert.equal(sandbox.manualSplit.regions.length, 4, 'A with nothing selected re-splits every drawn area');
          assert.ok(notices.at(-1).includes('Ctrl+Z로 되돌리'), 'the notice tells the teacher how to take it back');
          const split = sandbox.manualSplit.regions;
          sandbox.setManualSplit(prev => ({ ...prev, selectedRegionIds: [split[0].id, split[1].id] }));
          sandbox.merge();
          assert.equal(sandbox.manualSplit.regions.length, 3);
          sandbox.setManualSplit(prev => ({ ...prev, selectedRegionIds: [prev.regions[0].id] }));
          sandbox.deleteSelected();
          assert.equal(sandbox.manualSplit.regions.length, 2);
          sandbox.undo();
          assert.equal(sandbox.manualSplit.regions.length, 3);
          sandbox.undo();
          assert.equal(sandbox.manualSplit.regions, split);
          sandbox.undo();
          assert.equal(sandbox.manualSplit.regions, hand, 'the hand-drawn areas come back unchanged');
          sandbox.undo();
          assert.equal(notices.at(-1), '되돌릴 영역 변경이 없어요');
          sandbox.deleteSelected();
          assert.equal(sandbox.history.current.stack.length, 0, 'deleting with nothing selected is not a step');
          sandbox.setManualSplit(prev => ({ ...prev, selectedRegionIds: ['r1'] }));
          sandbox.deleteSelected();
          assert.equal(sandbox.history.current.stack.length, 1);
          vm.runInContext(section("  useEffect(() => {\n    setManualSplitNotice('');", '  const undoManualSplit'), sandbox);
          assert.equal(sandbox.history.current.stack.length, 0, 'another page starts with an empty history');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestBoardDraft(unittest.TestCase):
    def test_draft_round_trips_only_onto_the_same_saved_lesson(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const { session, items } = savedFixture(sandbox);
        session.output_dir = 'C:/out/lesson-a';
        const sig = value => sandbox.sessionEditorDraftSignature(value);
        const reflow = list => sandbox.reflowItemsForBoardOrder(list, 1.2, 1, 'classin-grid');
        const edited = reflow([sandbox.applyPlacementPatchToItem(items[0], { xRatio: 0.4 }), { ...items[1], step: 's3' }]);
        const draft = JSON.parse(JSON.stringify(sandbox.buildBoardDraft(session, edited, '새 이름', 1, 'classin-grid')));
        assert.ok(!('id' in draft.patches.p1) && !('title' in draft.patches.p1), 'only changed fields are stored');
        const restore = sandbox.boardDraftRestoreState(session, draft);
        assert.ok(restore);
        assert.equal(restore.fileName, '새 이름');
        assert.equal(
          sig(sandbox.materializeSessionForItems(session, restore.items, restore.fileName, restore.boardColumns, restore.layoutGapMode)),
          sig(sandbox.materializeSessionForItems(session, edited, '새 이름', 1, 'classin-grid')),
          'restoring reproduces exactly what 저장 would have saved');
        assert.equal(sandbox.boardDraftRestoreState({ ...session, output_dir: 'C:/out/other' }, draft), null, 'other lesson');
        const resaved = sandbox.materializeSessionForItems(session, edited, session.session_name, 1, 'classin-grid');
        resaved.output_dir = session.output_dir;
        assert.equal(sandbox.boardDraftRestoreState(resaved, draft), null, 'another saved version of the lesson');
        const unchanged = sandbox.buildBoardDraft(session, items, session.session_name, 1, 'classin-grid');
        assert.equal(sandbox.boardDraftRestoreState(session, unchanged), null, 'nothing to restore');
        const ghost = { ...draft, patches: { ...draft.patches, ghost: { title: 'x' } } };
        assert.equal(sandbox.boardDraftRestoreState(session, ghost).items.length, 2, 'unknown ids are ignored');
        for (const bad of [null, 'x', {}, { ...draft, v: 2 }, { ...draft, patches: null }]) {
          assert.equal(sandbox.boardDraftRestoreState(session, bad), null);
        }
        """)

    def test_app_offers_the_draft_once_writes_while_dirty_and_clears_on_save(self) -> None:
        run_node(NODE_SETUP + r"""
        function app(answer) {
          const sandbox = placementSandbox();
          const { session, items } = savedFixture(sandbox);
          session.output_dir = 'C:/out/lesson-a';
          const store = new Map(), timers = [], asked = [];
          Object.assign(sandbox, {
            window: {
              localStorage: { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value),
                removeItem: key => store.delete(key) },
              setTimeout: fn => timers.push(fn), clearTimeout() {},
              confirm: message => { asked.push(message); return answer; },
            },
            session, items, fileName: session.session_name, boardColumns: 1, layoutGapMode: 'classin-grid',
            initialSessionLoaded: false, boardColumnsRef: { current: 1 },
            useEffect: fn => fn(), useRef: initial => sandbox.ref || (sandbox.ref = { current: initial }),
            setTweak: (_key, value) => { sandbox.boardColumns = value; },
            setLayoutGapModeState: value => { sandbox.layoutGapMode = value; },
            setItems: value => { sandbox.items = value; }, setFileName: value => { sandbox.fileName = value; },
            setPublished() {}, showToast() {},
            pushLocalEdit: () => { sandbox.pushed = { items: sandbox.items, boardColumns: sandbox.boardColumns }; },
          });
          vm.runInContext(section('function readStoredPreference(', 'function useStoredPreference(')
            + 'globalThis.renderEffects = () => {'
            + section('  // Keep unsaved board edits in this browser', '  // load user settings') + '};', sandbox);
          const render = () => {
            sandbox.sessionEditState = sandbox.deriveSessionEditState(sandbox.session, sandbox.items,
              sandbox.fileName, sandbox.boardColumns, sandbox.layoutGapMode, false);
            sandbox.renderEffects();
          };
          const edited = sandbox.reflowItemsForBoardOrder(
            [sandbox.applyPlacementPatchToItem(items[0], { xRatio: 0.4 }), items[1]], 1.2, 1, 'classin-grid');
          store.set('edb.boardDraft.v1', JSON.stringify(sandbox.buildBoardDraft(session, edited, '새 이름', 1, 'classin-grid')));
          return { sandbox, store, timers, asked, render };
        }
        const accepted = app(true);
        accepted.render();
        assert.equal(accepted.asked.length, 0, 'nothing is offered before the saved lesson has loaded');
        accepted.sandbox.initialSessionLoaded = true;
        const savedItems = accepted.sandbox.items;
        accepted.render();
        assert.deepEqual(accepted.asked, ['저장하지 않은 배치가 있어요. 복원할까요?']);
        assert.equal(accepted.sandbox.pushed.items, savedItems, 'Ctrl+Z after restoring returns to the saved board');
        assert.equal(accepted.sandbox.fileName, '새 이름');
        assert.equal(accepted.sandbox.items[0].placementXRatio, 0.4);
        accepted.render();
        assert.equal(accepted.asked.length, 1, 'the offer is made once');
        assert.equal(accepted.timers.length, 1, 'the restored, still unsaved board is kept again');
        accepted.store.clear();
        accepted.timers[0]();
        assert.ok(accepted.store.has('edb.boardDraft.v1'));
        accepted.sandbox.session = accepted.sandbox.materializeSessionForItems(accepted.sandbox.session,
          accepted.sandbox.items, accepted.sandbox.fileName, 1, 'classin-grid');
        accepted.render();
        assert.equal(accepted.store.has('edb.boardDraft.v1'), false, 'saving removes the copy');

        const declined = app(false);
        declined.sandbox.initialSessionLoaded = true;
        const before = declined.sandbox.items;
        declined.render();
        assert.equal(declined.asked.length, 1);
        assert.equal(declined.sandbox.items, before);
        assert.equal(declined.store.has('edb.boardDraft.v1'), false, 'declining drops the copy');

        const other = app(true);
        other.sandbox.session = { ...other.sandbox.session, output_dir: 'C:/out/lesson-b' };
        other.sandbox.initialSessionLoaded = true;
        other.render();
        assert.equal(other.asked.length, 0, 'a draft is never offered for another lesson');

        const unreadable = app(true);
        const stored = JSON.parse(unreadable.store.get('edb.boardDraft.v1'));
        stored.patches.p1.title = 5;  // a copy this version cannot map
        unreadable.store.set('edb.boardDraft.v1', JSON.stringify(stored));
        unreadable.sandbox.initialSessionLoaded = true;
        unreadable.render();
        assert.equal(unreadable.asked.length, 0);
        assert.equal(unreadable.store.has('edb.boardDraft.v1'), false, 'an unreadable copy is dropped, not a startup crash');
        """)

    def test_storage_failures_never_break_the_editor(self) -> None:
        run_node(NODE_SETUP + r"""
        const thrower = () => { throw new Error('QuotaExceededError'); };
        const sandbox = { window: { localStorage: { getItem: () => '{broken', setItem: thrower, removeItem: thrower } } };
        vm.createContext(sandbox);
        vm.runInContext(section('function readStoredPreference(', 'function useStoredPreference('), sandbox);
        assert.equal(sandbox.readStoredPreference('edb.boardDraft.v1', null), null, 'corrupt JSON reads as no draft');
        sandbox.writeStoredPreference('edb.boardDraft.v1', { v: 1 });
        sandbox.removeStoredPreference('edb.boardDraft.v1');
        sandbox.window = {};
        assert.equal(sandbox.readStoredPreference('edb.boardDraft.v1', null), null);
        sandbox.removeStoredPreference('edb.boardDraft.v1');
        """)


if __name__ == "__main__":
    unittest.main()
