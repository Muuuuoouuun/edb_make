from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


NODE_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
const end = source.indexOf('const INITIAL_ITEMS =', start);
assert.ok(start >= 0 && end > start, 'placement helper source bounds exist');
const sandbox = { normalizeInputIntent: value => value };
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end), sandbox);
function near(actual, expected, message) {
  assert.ok(Math.abs(actual - expected) < 0.000002,
    `${message}: expected ${expected}, got ${actual}`);
}
function plain(value) { return JSON.parse(JSON.stringify(value)); }
function reflow(items, columns = 1, mode = 'classin-grid') {
  return sandbox.reflowItemsForBoardOrder(items, 1.2, columns, mode);
}
function applyUpdates(items, updates, columns = 1, mode = 'classin-grid') {
  const byId = new Map(updates.map(update => [String(update.id), update.patch]));
  return reflow(items.map(item => byId.has(String(item.id))
    ? sandbox.applyPlacementPatchToItem(item, byId.get(String(item.id))) : item), columns, mode);
}
function assertNoGapAction(action) {
  assert.deepEqual(plain(action.ids), []);
  assert.equal(action.removableGapPages, 0);
  assert.deepEqual(plain(action.restoreUpdates), []);
}
function rootGapController(items, columns = 1) {
  const notifications = [], toasts = [];
  Object.assign(sandbox, {
    items, boardColumns: columns, layoutGapMode: 'classin-grid',
    placementGapHistoryRef: { current: [] }, undoPlacementGapRef: { current: null },
    mutatingRef: { current: false }, sessionEditState: { busy: false },
    setItems: update => { sandbox.items = typeof update === 'function' ? update(sandbox.items) : update; },
    setPublished: value => { sandbox.published = value; },
    setPlacementGapUndoCount: count => { sandbox.undoCount = count; },
    showToast: message => notifications.push(message),
    showActionToast: (message, label, onAction) => toasts.push({ message, label, onAction }),
  });
  const handlerStart = source.indexOf('  const setPlacements = (updates) =>');
  const handlerEnd = source.indexOf('  const updateLayoutGapMode =', handlerStart);
  assert.ok(handlerStart >= 0 && handlerEnd > handlerStart);
  vm.runInContext(source.slice(handlerStart, handlerEnd)
    + '\nglobalThis.reducePlacementGap = reducePlacementGap;'
    + '\nglobalThis.undoPlacementGap = undoPlacementGap;'
    + '\nglobalThis.setPlacement = setPlacement;', sandbox);
  return { state: sandbox, notifications, toasts };
}
"""


class TestUiPreviewSummary(unittest.TestCase):
    def test_visual_y_offsets_determine_selected_and_next_page_ranges(self) -> None:
        run_node(NODE_SETUP + r"""
        const items = reflow([
          { id: 'first', heightFrac: 0.6, placementScaleRatio: 1,
            placementYRatio: 1, placementGapAfterPages: 1.8 },
          { id: 'second', heightFrac: 0.8, placementScaleRatio: 1,
            placementYRatio: 1 },
        ]);
        const estimate = sandbox.deriveBoardPreviewEstimate(items, 'first');
        near(estimate.active.startPages, 1.8, 'selected visual start includes its vertical offset');
        near(estimate.active.renderedBottomPages, 2.4, 'selected visual bottom');
        assert.equal(estimate.active.classinStartPage, 2);
        assert.equal(estimate.active.classinEndPage, 2, 'an exact page end stays on that page');
        near(estimate.active.rowNextStartPages, 2.4, 'row reservation remains separate');
        near(estimate.active.nextStartPages, 2.8, 'next actual item includes its own vertical offset');
        assert.equal(estimate.active.classinNextPage, 3);
        assert.equal(estimate.classinPageCount, 3);
        """)

    def test_same_row_successor_is_the_actual_next_item(self) -> None:
        run_node(NODE_SETUP + r"""
        const items = reflow([
          { id: 'left', heightFrac: 0.8, placementScaleRatio: 1 },
          { id: 'right', heightFrac: 0.4, placementScaleRatio: 1, placementYRatio: 1 },
          { id: 'next-row', heightFrac: 0.7, placementScaleRatio: 1 },
        ], 2);
        const estimate = sandbox.deriveBoardPreviewEstimate(items, 'left');
        near(estimate.active.nextStartPages, 0.8, 'next item is the neighboring column');
        assert.equal(estimate.active.classinNextPage, 1);
        near(estimate.active.rowNextStartPages, 1.2, 'next row is distinct from next item');
        const right = sandbox.deriveBoardPreviewEstimate(items, 'right');
        near(right.active.nextStartPages, 1.2, 'last column advances to the next row');
        assert.equal(right.active.classinNextPage, 2);
        """)

    def test_last_item_has_no_next_item_even_with_reserved_trailing_space(self) -> None:
        run_node(NODE_SETUP + r"""
        const items = reflow([
          { id: 'only', heightFrac: 0.7, placementScaleRatio: 1 },
        ]);
        const active = sandbox.deriveBoardPreviewEstimate(items, 'only').active;
        assert.equal(active.nextStartPages, null);
        assert.equal(active.classinNextPage, null);
        near(active.rowNextStartPages, 1.2, 'last item still has a reserved row boundary');
        assert.equal(active.classinEndPage, 1);
        """)

    def test_missing_selection_and_empty_layout_do_not_invent_gap_actions(self) -> None:
        run_node(NODE_SETUP + r"""
        const items = reflow([{ id: 'a', heightFrac: 0.6, placementScaleRatio: 1 }]);
        assert.equal(sandbox.deriveBoardPreviewEstimate(items, null).active, null);
        assert.equal(sandbox.deriveBoardPreviewEstimate(items, 'missing').active, null);
        assert.equal(sandbox.deriveBoardPreviewEstimate([], null).classinPageCount, 0);
        for (const [list, active] of [[items, null], [items, 'missing'], [[], null]]) {
          assertNoGapAction(sandbox.derivePlacementGapAction(list, active, 1, 'classin-grid'));
        }
        """)

    def test_row_gap_action_overrides_earlier_column_and_restores_null_and_zero(self) -> None:
        run_node(NODE_SETUP + r"""
        const items = reflow([
          { id: 'left', heightFrac: 0.5, placementScaleRatio: 1, placementGapAfterPages: '0.4' },
          { id: 'middle', heightFrac: 0.8, placementScaleRatio: 1 },
          { id: 'right', heightFrac: 0.6, placementScaleRatio: 1, placementGapAfterPages: 0 },
          { id: 'next-row', heightFrac: 0.7, placementScaleRatio: 1 },
        ], 3);
        const original = JSON.stringify(items);
        const action = sandbox.derivePlacementGapAction(items, 'right', 3, 'classin-grid');
        assert.deepEqual(plain(action.ids), ['left', 'middle', 'right']);
        near(action.removableGapPages, 0.4, 'shared row gap uses its tallest item');
        assert.deepEqual(plain(action.restoreUpdates), [
          { id: 'left', patch: { gapAfterPages: 0.4 } },
          { id: 'middle', patch: { gapAfterPages: null } },
          { id: 'right', patch: { gapAfterPages: 0 } },
        ]);
        assert.equal(JSON.stringify(items), original, 'deriving an action must preserve the original values');
        const removed = applyUpdates(items,
          action.ids.map(id => ({ id, patch: { gapAfterPages: 0 } })), 3);
        near(removed[3].startYPages, 0.8, 'all row overrides must be changed to move the next row');
        assertNoGapAction(sandbox.derivePlacementGapAction(removed, 'middle', 3, 'classin-grid'));
        const edited = removed.map(item => item.id === 'middle'
          ? sandbox.applyPlacementPatchToItem(item, { xRatio: 0.37 }) : item);
        const restored = applyUpdates(edited, action.restoreUpdates, 3);
        near(restored[3].startYPages, 1.2, 'undo returns the next row to its original position');
        near(restored[1].placementXRatio, 0.37, 'restoring the gap preserves unrelated edits');
        assert.equal(restored[1].placementGapAfterPages, null);
        assert.equal(restored[2].placementGapAfterPages, 0);
        """)

    def test_reserved_image_geometry_is_not_advertised_as_removable_space(self) -> None:
        run_node(NODE_SETUP + r"""
        for (const reservedHeightPages of [1, 1.2]) {
          const items = reflow([
            { id: 'record', placementScaleRatio: 1,
              imageRecordGeometry: { baseHeightPages: 0.5, reservedHeightPages } },
            { id: 'next', heightFrac: 0.6, placementScaleRatio: 1 },
          ]);
          const action = sandbox.derivePlacementGapAction(items, 'record', 1, 'classin-grid');
          if (reservedHeightPages === 1.2) {
            assertNoGapAction(action);
          } else {
            near(action.removableGapPages, 0.2, 'only space beyond the reserved flow height is removable');
            const removed = applyUpdates(items,
              action.ids.map(id => ({ id, patch: { gapAfterPages: 0 } })));
            near(removed[1].startYPages, 1, 'native reserved geometry must survive gap removal');
          }
        }
        """)

    def test_continuous_items_ignore_stale_slots_and_only_remove_the_actual_gap(self) -> None:
        run_node(NODE_SETUP + r"""
        for (const continuousGapPages of [0, 0.1]) {
          const items = reflow([
            { id: 'page', inputIntent: 'page-as-is', placementScaleRatio: 1,
              snappedNextStartYPages: 4.8,
              imageRecordGeometry: { baseHeightPages: 0.8, reservedHeightPages: 3.6,
                continuousGapPages } },
            { id: 'next', heightFrac: 0.6, placementScaleRatio: 1 },
          ]);
          const action = sandbox.derivePlacementGapAction(items, 'page', 1, 'classin-grid');
          if (!continuousGapPages) {
            assertNoGapAction(action);
          } else {
            assert.deepEqual(plain(action.ids), ['page']);
            near(action.removableGapPages, 0.1, 'only the current continuous gap is removable');
            const removed = applyUpdates(items,
              action.ids.map(id => ({ id, patch: { gapAfterPages: 0 } })));
            near(removed[1].startYPages, 0.8, 'continuous row closes up without its stale reservation');
          }
        }
        """)

    def test_reselected_row_and_consecutive_actions_keep_separate_restore_values(self) -> None:
        run_node(NODE_SETUP + r"""
        const items = reflow([
          { id: 'first', heightFrac: 0.6, placementScaleRatio: 1 },
          { id: 'second', heightFrac: 0.7, placementScaleRatio: 1, placementGapAfterPages: 0.3 },
          { id: 'third', heightFrac: 0.5, placementScaleRatio: 1 },
        ]);
        const firstAction = sandbox.derivePlacementGapAction(items, 'first', 1, 'classin-grid');
        const afterFirst = applyUpdates(items,
          firstAction.ids.map(id => ({ id, patch: { gapAfterPages: 0 } })));
        const secondAction = sandbox.derivePlacementGapAction(afterFirst, 'second', 1, 'classin-grid');
        const afterSecond = applyUpdates(afterFirst,
          secondAction.ids.map(id => ({ id, patch: { gapAfterPages: 0 } })));
        assertNoGapAction(sandbox.derivePlacementGapAction(afterSecond, 'first', 1, 'classin-grid'));
        const secondRestored = applyUpdates(afterSecond, secondAction.restoreUpdates);
        assert.equal(secondRestored[0].placementGapAfterPages, 0, 'undo only restores the second action');
        assert.equal(secondRestored[1].placementGapAfterPages, 0.3);
        const allRestored = applyUpdates(secondRestored, firstAction.restoreUpdates);
        assert.equal(allRestored[0].placementGapAfterPages, null);
        near(allRestored[2].startYPages, items[2].startYPages, 'both prior spacing values remain recoverable');
        """)

    def test_page_amount_converts_internal_units_before_formatting(self) -> None:
        run_node(NODE_SETUP + r"""
        assert.equal(sandbox.formatClassinPageAmount(0.5), '약 0.42쪽');
        assert.equal(sandbox.formatClassinPageAmount(0), '0쪽');
        assert.ok(!sandbox.formatClassinPageAmount(0.5).includes('p'),
          'internal layout unit names must not appear in the user label');
        """)

    def test_root_handlers_keep_consecutive_undo_values_and_preserve_later_edits(self) -> None:
        run_node(NODE_SETUP + r"""
        const original = reflow([
          { id: 'first', heightFrac: 0.6, placementScaleRatio: 1 },
          { id: 'second', heightFrac: 0.7, placementScaleRatio: 1, placementGapAfterPages: 0.3 },
          { id: 'third', heightFrac: 0.5, placementScaleRatio: 1 },
        ]);
        const { state, toasts } = rootGapController(original);
        state.reducePlacementGap('first');
        state.reducePlacementGap('second');
        assert.equal(state.undoCount, 2);
        assert.equal(toasts.length, 2);
        assert.equal(toasts[1].message, '뒤 여백을 줄였어요');
        assert.equal(toasts[1].label, '되돌리기');
        state.activeId = 'third';
        state.setPlacement('first', { xRatio: 0.37 });
        toasts[1].onAction();
        assert.equal(state.undoCount, 1);
        assert.equal(state.items[0].placementGapAfterPages, 0);
        assert.equal(state.items[1].placementGapAfterPages, 0.3);
        state.undoPlacementGap();
        assert.equal(state.undoCount, 0);
        assert.equal(state.items[0].placementGapAfterPages, null);
        near(state.items[0].placementXRatio, 0.37, 'undo retains the newer horizontal edit');
        near(state.items[2].startYPages, original[2].startYPages, 'consecutive undo restores both row gaps');
        assert.equal(state.activeId, 'third', 'undo does not replace the current selection');
        assert.equal(state.published, false, 'undo remains a draft placement change');
        """)

    def test_root_gap_handlers_ignore_no_ops_and_block_changes_while_busy(self) -> None:
        run_node(NODE_SETUP + r"""
        const { state, toasts, notifications } = rootGapController(reflow([
          { id: 'first', heightFrac: 0.6, placementScaleRatio: 1 },
          { id: 'second', heightFrac: 0.7, placementScaleRatio: 1 },
        ]));
        const before = JSON.stringify(state.items);
        state.reducePlacementGap('missing');
        state.undoPlacementGap();
        assert.equal(toasts.length, 0);
        assert.equal(notifications.length, 0);
        for (const guard of [state.mutatingRef, state.sessionEditState]) {
          const key = guard === state.mutatingRef ? 'current' : 'busy';
          guard[key] = true;
          state.reducePlacementGap('first');
          assert.equal(JSON.stringify(state.items), before);
          assert.equal(state.placementGapHistoryRef.current.length, 0);
          guard[key] = false;
        }
        state.reducePlacementGap('first');
        state.reducePlacementGap('first');
        assert.equal(state.undoCount, 1, 'repeated removal without a gap cannot replace the restore values');
        assert.equal(toasts.length, 1);
        const reduced = JSON.stringify(state.items);
        state.sessionEditState.busy = true;
        state.undoPlacementGap();
        assert.equal(state.undoCount, 1, 'busy undo must not pop its saved values');
        assert.equal(JSON.stringify(state.items), reduced);
        state.sessionEditState.busy = false;
        state.undoPlacementGap();
        assert.equal(state.items[0].placementGapAfterPages, null);
        assert.equal(state.undoCount, 0);
        """)

    def test_gap_undo_recomputes_boundary_auto_scale_from_the_original_scale(self) -> None:
        run_node(NODE_SETUP + r"""
        const original = reflow([
          { id: 'first', heightFrac: 0.8, placementScaleRatio: 1 },
          { id: 'next', heightFrac: 0.42, placementScaleRatio: 1 },
        ]);
        const { state } = rootGapController(original);
        state.reducePlacementGap('first');
        near(state.items[1].startYPages, 0.8, 'gap removal shifts the next item toward a page boundary');
        near(state.items[1].placementScaleRatio, 0.4 / 0.42, 'new boundary applies the small automatic reduction');
        assert.equal(state.items[1]._previewAutoScaleAdjusted, true);
        near(state.items[1]._previewOriginalScaleRatio, 1, 'the original requested scale remains recoverable');
        const reducedSignature = JSON.stringify(state.items);
        state.items = reflow(state.items);
        assert.equal(JSON.stringify(state.items), reducedSignature, 'reflow must not compound or lose the automatic reduction');
        state.undoPlacementGap();
        near(state.items[1].startYPages, 1.2, 'undo restores the next row start');
        near(state.items[1].placementScaleRatio, 1, 'undo restores the original scale when the old boundary no longer applies');
        assert.ok(!state.items[1]._previewAutoScaleAdjusted, 'old boundary adjustment must not remain marked');
        const restoredSignature = JSON.stringify(state.items);
        state.items = reflow(state.items);
        assert.equal(JSON.stringify(state.items), restoredSignature, 'restored layout is stable on subsequent reflow');
        """)

    def test_explicit_manual_scale_after_gap_removal_survives_gap_undo(self) -> None:
        run_node(NODE_SETUP + r"""
        const { state } = rootGapController(reflow([
          { id: 'first', heightFrac: 0.8, placementScaleRatio: 1 },
          { id: 'next', heightFrac: 0.42, placementScaleRatio: 1 },
        ]));
        state.reducePlacementGap('first');
        assert.equal(state.items[1]._previewAutoScaleAdjusted, true);
        state.setPlacement('next', { scaleRatio: 0.9 });
        assert.ok(!state.items[1]._previewAutoScaleAdjusted, 'manual scale clears the automatic marker');
        state.undoPlacementGap();
        near(state.items[1].placementScaleRatio, 0.9, 'gap undo must retain the newer explicit scale');
        assert.equal(state.items[1].placementAutoFitDisabled, true);
        near(state.items[1].startYPages, 1.2, 'the gap itself is still restored');
        """)


if __name__ == '__main__':
    unittest.main()
