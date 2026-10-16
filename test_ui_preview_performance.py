from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


PANEL_SETUP = NODE_SETUP + r"""
const sandbox = placementSandbox();
const fixture = savedFixture(sandbox, [0.6, 0.8, 0.7, 0.9]);
const memoSlots = [];
let memoIndex = 0, reflowCalls = 0, saveStateCalls = 0;
const reflow = sandbox.reflowItemsForBoardOrder;
const deriveSaveState = sandbox.derivePlacementSaveState;
sandbox.reflowItemsForBoardOrder = (...args) => { reflowCalls++; return reflow(...args); };
sandbox.derivePlacementSaveState = (...args) => { saveStateCalls++; return deriveSaveState(...args); };
Object.assign(sandbox, {
  session: fixture.session, items: fixture.items, item: fixture.items[0],
  boardColumns: 1, layoutGapMode: 'classin-grid', aiEnabled: false, userSettings: null,
  placementPreset: 'auto', placementScope: 'item',
  passageGroupIdFor: item => item.passageGroupId || '',
  passageRangeLabelFor: () => '', isPassageFragmentProblem: () => false,
  sessionEditState: { dirty: false, canSave: false, label: '수업 변경 저장됨' },
  useMemo: (fn, dependencies) => {
    const index = memoIndex++;
    const previous = memoSlots[index];
    if (previous && dependencies.length === previous.dependencies.length
      && dependencies.every((value, key) => Object.is(value, previous.dependencies[key]))) return previous.value;
    const value = fn();
    memoSlots[index] = { dependencies, value };
    return value;
  },
});
const renderBody = section('  const placementSaveState = useMemo(', '  const placementGroupHeightPages =');
function renderPanel() {
  memoIndex = 0;
  return vm.runInContext('(() => {' + renderBody
    + 'return { placementSaveState, placementLayoutItems, activePlacementPreview, placementGapAction, placementGroupLayoutItems }; })()', sandbox);
}
const plain = value => JSON.parse(JSON.stringify(value));
"""


class TestUiPreviewPerformance(unittest.TestCase):
    def test_changing_selection_reuses_layout_and_parent_save_state(self) -> None:
        run_node(PANEL_SETUP + r"""
        const first = renderPanel();
        assert.equal(reflowCalls, 1, 'the side panel derives its layout once');
        assert.equal(saveStateCalls, 0, 'the root save state must avoid another materialization and signature pass');
        assert.equal(first.placementSaveState, sandbox.sessionEditState);
        for (const item of sandbox.items) {
          sandbox.item = item;
          const current = renderPanel();
          assert.equal(current.placementLayoutItems, first.placementLayoutItems);
          assert.equal(reflowCalls, 1, 'selection-only rendering must not reflow the session');
          assert.deepEqual(plain(current.activePlacementPreview),
            plain(sandbox.deriveBoardPreviewEstimate(first.placementLayoutItems, item.id).active));
          assert.deepEqual(plain(current.placementGapAction),
            plain(sandbox.derivePlacementGapActionFromLayout(first.placementLayoutItems, item.id)));
          assert.deepEqual(plain(current.placementGroupLayoutItems.map(target => target.id)), [item.id]);
        }
        sandbox.sessionEditState = { dirty: true, canSave: false, busy: true, label: '수업 변경 저장 중…' };
        const busy = renderPanel();
        assert.equal(busy.placementSaveState, sandbox.sessionEditState);
        assert.equal(saveStateCalls, 0);
        assert.equal(reflowCalls, 1, 'busy status changes do not alter layout');
        """)

    def test_group_filter_shares_layout_and_retains_matching_ids_and_order(self) -> None:
        run_node(PANEL_SETUP + r"""
        sandbox.items = sandbox.items.map((item, index) => ({ ...item,
          passageGroupId: index % 2 === 0 ? 'passage-1' : 'passage-2' }));
        sandbox.item = sandbox.items[0];
        let current = renderPanel();
        assert.equal(reflowCalls, 1);
        assert.deepEqual(plain(current.placementGroupLayoutItems.map(item => item.id)), ['p1', 'p3']);
        const group = current.placementGroupLayoutItems;
        assert.equal(renderPanel().placementGroupLayoutItems, group, 'unchanged group selection reuses the filtered layout');
        sandbox.item = sandbox.items[1];
        current = renderPanel();
        assert.equal(reflowCalls, 1, 'switching groups must not reflow the session');
        assert.deepEqual(plain(current.placementGroupLayoutItems.map(item => item.id)), ['p2', 'p4']);
        """)

    def test_items_columns_and_spacing_each_invalidate_the_shared_layout(self) -> None:
        run_node(PANEL_SETUP + r"""
        let result = renderPanel();
        const original = result.placementLayoutItems;
        sandbox.items = sandbox.items.map((item, index) => index === 0
          ? sandbox.applyPlacementPatchToItem(item, { gapAfterPages: 0 }) : item);
        result = renderPanel();
        assert.equal(reflowCalls, 2, 'item edits derive one replacement layout');
        assert.notEqual(result.placementLayoutItems, original);
        assert.equal(result.placementGapAction.ids.length, 0);
        sandbox.boardColumns = 2;
        result = renderPanel();
        assert.equal(reflowCalls, 3, 'changing columns invalidates the layout');
        assert.equal(result.placementLayoutItems[1].startYPages, result.placementLayoutItems[0].startYPages);
        sandbox.layoutGapMode = 'compact';
        renderPanel();
        assert.equal(reflowCalls, 4, 'changing spacing invalidates the layout');
        assert.equal(saveStateCalls, 0);
        """)

    def test_standalone_panel_and_mock_keep_their_original_save_behavior(self) -> None:
        run_node(PANEL_SETUP + r"""
        sandbox.sessionEditState = null;
        let result = renderPanel();
        assert.equal(saveStateCalls, 1, 'standalone use still computes the saved-session comparison');
        assert.equal(result.placementSaveState.canSave, false);
        sandbox.session = null;
        sandbox.sessionEditState = { dirty: false, canSave: false, label: '샘플 미리보기 · 저장 안 됨' };
        result = renderPanel();
        assert.equal(result.placementSaveState.canSave, true, 'sample placement can still be applied locally');
        assert.equal(result.placementSaveState.label, '미리보기');
        """)

    def test_precomputed_gap_actions_match_public_wrapper_without_mutating_layout(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const plain = value => JSON.parse(JSON.stringify(value));
        for (const columns of [1, 2, 3]) for (const mode of ['classin-grid', 'compact']) {
          const items = [
            { id: 'a', heightFrac: 0.5, placementScaleRatio: 1, placementGapAfterPages: 0.2 },
            { id: 'b', heightFrac: 0.8, placementScaleRatio: 1 },
            { id: 'c', heightFrac: 0.42, placementScaleRatio: 1, placementGapAfterPages: 0 },
            { id: 'd', inputIntent: 'page-as-is', heightFrac: 1.3, placementScaleRatio: 1 },
          ];
          const layout = sandbox.reflowItemsForBoardOrder(items, 1.2, columns, mode);
          const before = JSON.stringify(layout);
          for (const id of ['a', 'b', 'c', 'd', null, 'missing']) {
            assert.deepEqual(plain(sandbox.derivePlacementGapActionFromLayout(layout, id)),
              plain(sandbox.derivePlacementGapAction(items, id, columns, mode)));
          }
          assert.equal(JSON.stringify(layout), before);
        }
        """)


if __name__ == '__main__':
    unittest.main()
