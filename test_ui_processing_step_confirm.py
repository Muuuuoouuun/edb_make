from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


CONFIRM_SETUP = NODE_SETUP + r"""
function controller() {
  const sandbox = placementSandbox();
  vm.runInContext(section('function confirmedItemState(', 'function confirmedProblemState('), sandbox);
  const heights = [540 * 6000 / 480 / 590, 540 * 564 / 420 / 590];
  const raw = {
    session_name: '처리 방식과 자르기 보존', crop_format: 'v1', layoutGapMode: 'compact',
    problems: heights.map((height, index) => ({
      id: `p${index + 1}`, title: `문항 ${index + 1}`, actualHeightPages: height,
      step: index === 0 ? 's2' : 's1', processingStep: index === 0 ? 's2' : 's1',
      inputIntent: index === 0 ? 'page-as-is' : 'single-problem',
      placementScaleRatio: index === 0 ? 3 : 1.26,
      placementXRatio: index === 0 ? 0 : 0.4, placementXEdited: index === 1,
      placementYRatio: index === 0 ? 0 : 0.3, placementAutoFitDisabled: true,
      manualCrop: { leftRatio: 0.02, rightRatio: 0, topRatio: 0.029, bottomRatio: 0 },
      cropBaseImagePath: `file:///tmp/base-${index}.png`,
      imageRecordGeometry: { baseDisplayWidthPx: 540, maxDisplayWidthPx: 1142,
        baseHeightPages: height, reservedHeightPages: height, continuousGapPages: 20 / 590,
        cropFormat: 'v1', canvasPageHeightPx: 590 },
      riskFlags: ['needs-review'], reviewStatus: 'check_needed',
    })),
    pages: [{ id: 'page', problemIds: ['p1', 'p2'] }],
  };
  const reflow = items => sandbox.reflowItemsForBoardOrder(items, 1.2, 1, 'compact');
  const session = sandbox.materializeSessionForItems(raw, reflow(raw.problems.map(sandbox.mapProblemToItem)),
    raw.session_name, 1, 'compact');
  const state = { restores: [], mutations: [], errors: [], toasts: [], views: [], tweaks: [] };
  Object.assign(sandbox, {
    session, items: reflow(session.problems.map(sandbox.mapProblemToItem)),
    fileName: session.session_name, boardColumns: 1, layoutGapMode: 'compact', activeId: 'p2',
    boardColumnsRef: { current: 1 },
    published: true, history: [], mutatingRef: { current: false }, UNDO_HISTORY_LIMIT: 20,
    useCallback: fn => fn, replacementSourceIdFor: () => null,
    appendBoundedHistory: require('./ui_prototype/reorder.js').appendBoundedHistory,
    setItems: value => { sandbox.items = typeof value === 'function' ? value(sandbox.items) : value; },
    setSession: value => { sandbox.session = value; },
    setPublished: value => { sandbox.published = value; },
    setTweak: (key, value) => {
      assert.equal(key, 'boardColumns', 'session adoption only restores the board column setting');
      state.tweaks.push({ key, value });
      sandbox.boardColumns = value;
    },
    setLayoutGapModeState: value => { sandbox.layoutGapMode = value; },
    setActiveId: value => { sandbox.activeId = value; },
    setHistoryStack: value => { sandbox.history = value(sandbox.history); },
    setMutating() {}, setLoading() {}, refreshSessionHistory() {}, setReviewFocus() {}, setActionToast() {},
    showToast: message => state.toasts.push(message), showSimpleErrorToast: error => state.errors.push(error),
    showActionToast() {}, stepLabel: value => value,
    requestViewChange: value => state.views.push(value),
    sessionReviewSummary: s => ({ remaining: s.problems.filter(p => p.reviewStatus !== 'normal').length }),
    reviewFlowState: summary => ({ remaining: summary.remaining, complete: summary.remaining === 0 }),
    postRestore: async snapshot => {
      state.restores.push(JSON.parse(JSON.stringify(snapshot)));
      if (state.failRestore) throw new Error('restore failed');
      state.serverSnapshot = JSON.parse(JSON.stringify(snapshot));
      return state.serverSnapshot;
    },
    postMutate: async (action, args) => {
      state.mutations.push({ action, args });
      if (state.failMutation) throw new Error('confirm failed');
      const next = JSON.parse(JSON.stringify(state.serverSnapshot));
      next.problems.forEach(problem => {
        if (args.problemIds.includes(problem.id)) {
          problem.reviewStatus = 'normal'; problem.review_status = 'normal';
          problem.riskFlags = []; problem.risk_flags = [];
        }
      });
      return next;
    },
  });
  function render() {
    // React handlers retain the values from their render even when setItems is
    // called in the same click. Freeze those bindings to reproduce that race.
    vm.runInContext('(() => { const items = globalThis.items; const session = globalThis.session;'
      + section('  const adoptMutatedSession = useCallback(', '  // Run a server-side mutation')
      + section('  const mutateSession = useCallback(', '  const retryAiSession = useCallback')
      + section('  const applySelectedStep =', '  const classifySelected =')
      + section('  const applyToAll =', '  const setPlacements =')
      + section('  const onConfirm = async (id, options = {}) => {', '  const markClassinReviewComplete =')
      + 'globalThis.confirm = onConfirm; globalThis.applyAll = applyToAll;'
      + 'globalThis.applySelected = applySelectedStep; })()', sandbox);
  }
  function clickFooter(bulk) {
    const call = source.indexOf('onConfirm(item.id, { bulk, problemIds: bulk ? remainingApplyIds : [item.id], processingStep: item.step });');
    const start = source.lastIndexOf('onClick={() => {', call) + 'onClick={() => {'.length;
    const end = source.indexOf('\n              }}', call);
    assert.ok(call >= 0 && end > start, 'processing confirmation click handler is missing');
    sandbox.footerBulk = bulk;
    vm.runInContext('(() => { const item = globalThis.items.find(item => item.id === globalThis.activeId);'
      + 'const items=globalThis.items;' + section('  const remainingReviewItems =', '  const showItemConfirmBar =')
      + 'const bulk = globalThis.footerBulk; const onConfirm = (...args) => {'
      + 'globalThis.pendingConfirm = globalThis.confirm(...args); };'
      + source.slice(start, end) + '})()', sandbox);
    return sandbox.pendingConfirm;
  }
  render();
  return { sandbox, state, render, clickFooter };
}
const steps = session => session.problems.map(problem => problem.processingStep || problem.step).join(',');
function assertEditorStatePreserved(before, after) {
  assert.equal(after.layoutGapMode, before.layoutGapMode);
  for (let index = 0; index < before.problems.length; index++) {
    for (const field of ['placementScaleRatio', 'placementXRatio', 'placementYRatio',
      'placementAutoFitDisabled', 'manualCrop', 'cropBaseImagePath', 'imageRecordGeometry']) {
      assert.equal(JSON.stringify(after.problems[index][field]), JSON.stringify(before.problems[index][field]), field);
    }
  }
}
"""


class TestUiProcessingStepConfirm(unittest.TestCase):
    def test_remaining_bulk_leaves_already_reviewed_processing_unchanged(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        (async () => {
          const {sandbox,state,clickFooter}=controller();
          sandbox.items[0].reviewStatus='normal';
          await clickFooter(true);
          assert.equal(steps(state.restores[0]),'s2,s1');
          assert.deepEqual(Array.from(state.mutations[0].args.problemIds),['p2']);
        })().catch(error=>{console.error(error);process.exitCode=1;});
        """)

    def test_same_click_bulk_processing_restores_chosen_step_and_keeps_prechange_undo(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        (async () => {
          const { sandbox, state, clickFooter } = controller();
          const before = JSON.parse(JSON.stringify(sandbox.session));
          assert.equal(steps(before), 's2,s1');
          await clickFooter(true);
          assert.equal(steps(state.restores[0]), 's1,s1', 'the confirm request must serialize the chosen step');
          assert.equal(steps(sandbox.session), 's1,s1');
          assert.equal(sandbox.items.map(item => item.step).join(','), 's1,s1');
          assert.equal(steps(sandbox.history[0]), 's2,s1', 'undo restores the state before bulk processing');
          assertEditorStatePreserved(before, state.restores[0]);
          assert.equal(sandbox.activeId, 'p2');
          assert.equal(sandbox.published, false);
          assert.equal(sandbox.boardColumns, 1);
          assert.equal(sandbox.boardColumnsRef.current, 1);
          assert.deepEqual(state.tweaks.at(-1), { key: 'boardColumns', value: 1 });
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_single_processing_and_review_bulk_confirmation_keep_other_steps(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        (async () => {
          for (const footer of [true, false]) {
            const { sandbox, state, clickFooter } = controller();
            if (footer) await clickFooter(false);
            else await sandbox.confirm(null, { problemIds: ['p1', 'p2'], bulk: true });
            assert.equal(steps(state.restores[0]), 's2,s1', 'review bulk must not impose one processing step');
            assert.equal(steps(sandbox.session), 's2,s1');
            assert.equal(sandbox.items.map(item => item.step).join(','), 's2,s1');
            assert.equal(sandbox.boardColumnsRef.current, sandbox.boardColumns);
            assert.equal(sandbox.boardColumns, 1, 'confirmed one-column layout stays synchronized');
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_header_and_selected_processing_drafts_survive_later_confirmation(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        (async () => {
          for (const all of [true, false]) {
            const { sandbox, state, render } = controller();
            if (all) sandbox.applyAll('s3');
            else sandbox.applySelected(['p2'], 's3');
            assert.equal(state.restores.length, 0, 'processing selection remains a local draft until confirmation');
            render();
            await sandbox.confirm(null, { problemIds: all ? ['p1', 'p2'] : ['p2'], bulk: true });
            assert.equal(steps(state.restores[0]), all ? 's3,s3' : 's2,s3');
            assert.equal(steps(sandbox.session), all ? 's3,s3' : 's2,s3');
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_failed_confirmation_keeps_the_selected_bulk_draft_without_completed_status_or_undo(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        (async () => {
          for (const failure of ['failRestore', 'failMutation']) {
            const { sandbox, state, clickFooter } = controller();
            const before = JSON.parse(JSON.stringify(sandbox.session));
            state[failure] = true;
            assert.equal(await clickFooter(true), false);
            assert.equal(sandbox.items.map(item => item.step).join(','), 's1,s1', 'keep the chosen draft for retry');
            assert.equal(steps(sandbox.session), 's2,s1', 'failure must not replace the saved baseline');
            assert.equal(sandbox.published, false);
            assert.equal(sandbox.history.length, 0);
            assert.equal(state.errors.length, 1);
            assert.equal(sandbox.mutatingRef.current, false);
            assertEditorStatePreserved(before, state.restores[0]);
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_busy_confirmation_does_not_apply_a_competing_bulk_draft(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        (async () => {
          const { sandbox, state, clickFooter } = controller();
          sandbox.mutatingRef.current = true;
          const items = sandbox.items;
          assert.equal(await clickFooter(true), false);
          assert.equal(sandbox.items, items);
          assert.equal(state.restores.length, 0);
          assert.equal(sandbox.history.length, 0);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_mock_bulk_processing_updates_steps_and_confirmation_together(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        (async () => {
          const { sandbox, state, render, clickFooter } = controller();
          sandbox.session = null;
          render();
          assert.equal(await clickFooter(true), true);
          assert.equal(sandbox.items.map(item => item.step).join(','), 's1,s1');
          assert.ok(sandbox.items.every(item => item.reviewStatus === 'normal'));
          assert.equal(state.restores.length, 0);
          assert.equal(state.views.at(-1), 'board');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


if __name__ == '__main__':
    unittest.main()
