from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiReorderHelper(unittest.TestCase):
    def test_selection_click_supports_shift_range_and_plain_reset(self) -> None:
        run_node(
            """
            const { applySelectionClick } = require('./ui_prototype/reorder.js');
            const ids = ['a', 'b', 'c', 'd', 'e'];
            const ranged = applySelectionClick(ids, ['b'], 'b', 'd', { shiftKey: true });
            if (JSON.stringify(ranged) !== JSON.stringify({ selectedIds: ['b', 'c', 'd'], anchorId: 'b' })) {
              throw new Error(`shift range failed: ${JSON.stringify(ranged)}`);
            }
            const reversed = applySelectionClick(ids, ranged.selectedIds, ranged.anchorId, 'a', { shiftKey: true });
            if (JSON.stringify(reversed) !== JSON.stringify({ selectedIds: ['a', 'b'], anchorId: 'b' })) {
              throw new Error(`reverse shift range failed: ${JSON.stringify(reversed)}`);
            }
            const reset = applySelectionClick(ids, reversed.selectedIds, reversed.anchorId, 'e');
            if (JSON.stringify(reset) !== JSON.stringify({ selectedIds: ['e'], anchorId: 'e' })) {
              throw new Error(`plain click should reset selection: ${JSON.stringify(reset)}`);
            }
            """
        )

    def test_selection_click_supports_ctrl_and_command_toggle(self) -> None:
        run_node(
            """
            const { applySelectionClick } = require('./ui_prototype/reorder.js');
            const ids = ['a', 'b', 'c', 'd'];
            const added = applySelectionClick(ids, ['b'], 'b', 'd', { ctrlKey: true });
            if (JSON.stringify(added) !== JSON.stringify({ selectedIds: ['b', 'd'], anchorId: 'd' })) {
              throw new Error(`ctrl add failed: ${JSON.stringify(added)}`);
            }
            const removed = applySelectionClick(ids, added.selectedIds, added.anchorId, 'b', { metaKey: true });
            if (JSON.stringify(removed) !== JSON.stringify({ selectedIds: ['d'], anchorId: 'b' })) {
              throw new Error(`command remove failed: ${JSON.stringify(removed)}`);
            }
            """
        )

    def test_selection_click_supports_additive_modifier_range(self) -> None:
        run_node(
            """
            const { applySelectionClick } = require('./ui_prototype/reorder.js');
            const ids = ['a', 'b', 'c', 'd', 'e', 'f'];
            const result = applySelectionClick(
              ids,
              ['a', 'd'],
              'd',
              'f',
              { ctrlKey: true, shiftKey: true },
            );
            if (JSON.stringify(result) !== JSON.stringify({
              selectedIds: ['a', 'd', 'e', 'f'],
              anchorId: 'd',
            })) {
              throw new Error(`additive range failed: ${JSON.stringify(result)}`);
            }
            """
        )

    def test_selection_helpers_select_all_and_clear_in_board_order(self) -> None:
        run_node(
            """
            const {
              clearItemSelection,
              orderedSelectionIds,
              selectAllItems,
              selectionKeyboardCommand,
            } = require('./ui_prototype/reorder.js');
            const ids = ['a', 'b', 'b', null, 'c'];
            if (JSON.stringify(selectAllItems(ids)) !== JSON.stringify(['a', 'b', 'c'])) {
              throw new Error(`select all should normalize ids: ${JSON.stringify(selectAllItems(ids))}`);
            }
            if (JSON.stringify(orderedSelectionIds(ids, ['c', 'missing', 'a'])) !== JSON.stringify(['a', 'c'])) {
              throw new Error('selected ids should follow board order');
            }
            if (JSON.stringify(clearItemSelection()) !== '[]') throw new Error('clear failed');
            const all = selectionKeyboardCommand(ids, ['b'], 'b', 'b', 'a', { metaKey: true });
            if (JSON.stringify(all.selectedIds) !== JSON.stringify(['a', 'b', 'c'])) {
              throw new Error(`command+a failed: ${JSON.stringify(all)}`);
            }
            const cleared = selectionKeyboardCommand(ids, all.selectedIds, all.anchorId, all.focusId, 'A', {
              ctrlKey: true,
              shiftKey: true,
            });
            if (cleared.selectedIds.length || cleared.anchorId !== null) {
              throw new Error(`ctrl+shift+a failed: ${JSON.stringify(cleared)}`);
            }
            """
        )

    def test_selection_keyboard_command_extends_and_contracts_range(self) -> None:
        run_node(
            """
            const { selectionKeyboardCommand } = require('./ui_prototype/reorder.js');
            const ids = ['a', 'b', 'c', 'd', 'e'];
            const down = selectionKeyboardCommand(ids, ['b'], 'b', 'b', 'ArrowDown', { shiftKey: true });
            if (JSON.stringify(down) !== JSON.stringify({
              selectedIds: ['b', 'c'],
              anchorId: 'b',
              focusId: 'c',
            })) {
              throw new Error(`shift+down failed: ${JSON.stringify(down)}`);
            }
            const downAgain = selectionKeyboardCommand(
              ids,
              down.selectedIds,
              down.anchorId,
              down.focusId,
              'ArrowDown',
              { shiftKey: true },
            );
            if (JSON.stringify(downAgain.selectedIds) !== JSON.stringify(['b', 'c', 'd'])) {
              throw new Error(`second shift+down failed: ${JSON.stringify(downAgain)}`);
            }
            const up = selectionKeyboardCommand(
              ids,
              downAgain.selectedIds,
              downAgain.anchorId,
              downAgain.focusId,
              'ArrowUp',
              { shiftKey: true },
            );
            if (JSON.stringify(up) !== JSON.stringify({
              selectedIds: ['b', 'c'],
              anchorId: 'b',
              focusId: 'c',
            })) {
              throw new Error(`shift+up should contract range: ${JSON.stringify(up)}`);
            }
            """
        )

    def test_adjacent_reorder_command_supports_keyboard_moves(self) -> None:
        run_node(
            """
            const { adjacentReorderCommand } = require('./ui_prototype/reorder.js');
            const items = ['a', 'b', 'c'].map(id => ({ id }));
            const up = adjacentReorderCommand(items, 'b', 'up');
            const down = adjacentReorderCommand(items, 'b', 'down');
            if (JSON.stringify(up) !== JSON.stringify({ sourceId: 'b', targetId: 'a', position: 'before', nextIndex: 0 })) {
              throw new Error(`unexpected up command: ${JSON.stringify(up)}`);
            }
            if (JSON.stringify(down) !== JSON.stringify({ sourceId: 'b', targetId: 'c', position: 'after', nextIndex: 2 })) {
              throw new Error(`unexpected down command: ${JSON.stringify(down)}`);
            }
            if (adjacentReorderCommand(items, 'a', 'up') !== null) throw new Error('first item cannot move up');
            if (adjacentReorderCommand(items, 'c', 'down') !== null) throw new Error('last item cannot move down');
            """
        )

    def test_nearest_placement_index_uses_sorted_page_positions(self) -> None:
        run_node(
            """
            const { nearestPlacementIndex } = require('./ui_prototype/reorder.js');
            const positions = [{ top: 0 }, { top: 100 }, { top: 100 }, { top: 260 }];
            if (nearestPlacementIndex(positions, -20) !== 0) throw new Error('top clamp failed');
            if (nearestPlacementIndex(positions, 90) !== 1) throw new Error('nearest middle failed');
            if (nearestPlacementIndex(positions, 100) !== 1) throw new Error('duplicate first match failed');
            if (nearestPlacementIndex(positions, 220) !== 3) throw new Error('lower neighbor failed');
            if (nearestPlacementIndex(positions, 999) !== 3) throw new Error('bottom clamp failed');
            if (nearestPlacementIndex([], 10) !== -1) throw new Error('empty positions failed');
            """
        )

    def test_undo_history_is_bounded_without_mutating_the_original(self) -> None:
        run_node(
            """
            const { appendBoundedHistory } = require('./ui_prototype/reorder.js');
            const original = Array.from({ length: 20 }, (_, index) => ({ index }));
            const next = appendBoundedHistory(original, { index: 20 }, 20);
            if (next.length !== 20 || next[0].index !== 1 || next[19].index !== 20) {
              throw new Error(`history was not capped correctly: ${JSON.stringify(next)}`);
            }
            if (original.length !== 20 || original[0].index !== 0) {
              throw new Error('original history was mutated');
            }
            if (appendBoundedHistory(original, null, 20) !== original) {
              throw new Error('empty snapshots should reuse the original history');
            }
            """
        )

    def test_reorder_helper_inserts_before_and_after_targets(self) -> None:
        run_node(
            """
            const { reorderItemsForDrop } = require('./ui_prototype/reorder.js');
            const ids = xs => xs.map(x => x.id).join(',');
            const items = ['a', 'b', 'c', 'd'].map(id => ({ id }));

            if (ids(reorderItemsForDrop(items, 'a', 'c', 'before')) !== 'b,a,c,d') {
              throw new Error('before insert failed');
            }
            if (ids(reorderItemsForDrop(items, 'a', 'c', 'after')) !== 'b,c,a,d') {
              throw new Error('after insert failed');
            }
            if (ids(reorderItemsForDrop(items, 'c', 'a', 'after')) !== 'a,c,b,d') {
              throw new Error('upward after insert failed');
            }
            if (ids(items) !== 'a,b,c,d') {
              throw new Error('original array mutated');
            }
            """
        )

    def test_group_reorder_preserves_selected_relative_order(self) -> None:
        run_node(
            """
            const { reorderItemGroupForDrop } = require('./ui_prototype/reorder.js');
            const ids = xs => xs.map(x => x.id).join(',');
            const items = ['a', 'b', 'c', 'd', 'e'].map(id => ({ id }));

            if (ids(reorderItemGroupForDrop(items, ['b', 'd'], 'e', 'after')) !== 'a,c,e,b,d') {
              throw new Error('non-contiguous selection should move as one ordered group');
            }
            if (ids(reorderItemGroupForDrop(items, ['d', 'b'], 'a', 'before')) !== 'b,d,a,c,e') {
              throw new Error('group order should follow the board, not selection click order');
            }
            if (reorderItemGroupForDrop(items, ['b', 'c'], 'c', 'after') !== items) {
              throw new Error('dropping onto a selected row should be a no-op');
            }
            if (ids(items) !== 'a,b,c,d,e') {
              throw new Error('group reorder mutated the original items');
            }
            """
        )

    def test_adjacent_group_reorder_supports_keyboard_moves(self) -> None:
        run_node(
            """
            const { adjacentGroupReorderCommand, reorderItemGroupForDrop } = require('./ui_prototype/reorder.js');
            const items = ['a', 'b', 'c', 'd', 'e'].map(id => ({ id }));
            const down = adjacentGroupReorderCommand(items, ['b', 'c'], 'down');
            const up = adjacentGroupReorderCommand(items, ['c', 'd'], 'up');
            if (JSON.stringify(down) !== JSON.stringify({
              sourceId: 'b', sourceIds: ['b', 'c'], targetId: 'd', position: 'after', nextIndex: 2
            })) {
              throw new Error(`unexpected group down command: ${JSON.stringify(down)}`);
            }
            if (JSON.stringify(up) !== JSON.stringify({
              sourceId: 'c', sourceIds: ['c', 'd'], targetId: 'b', position: 'before', nextIndex: 1
            })) {
              throw new Error(`unexpected group up command: ${JSON.stringify(up)}`);
            }
            const moved = reorderItemGroupForDrop(items, down.sourceIds, down.targetId, down.position);
            if (moved.map(item => item.id).join(',') !== 'a,d,b,c,e') {
              throw new Error(`group keyboard command did not move together: ${JSON.stringify(moved)}`);
            }
            if (adjacentGroupReorderCommand(items, ['a', 'b'], 'up') !== null) {
              throw new Error('top group cannot move up');
            }
            if (adjacentGroupReorderCommand(items, ['d', 'e'], 'down') !== null) {
              throw new Error('bottom group cannot move down');
            }
            """
        )

    def test_reorder_helper_keeps_invalid_or_same_drop_as_noop(self) -> None:
        run_node(
            """
            const { reorderItemsForDrop } = require('./ui_prototype/reorder.js');
            const ids = xs => xs.map(x => x.id).join(',');
            const items = ['a', 'b', 'c'].map(id => ({ id }));

            if (reorderItemsForDrop(items, 'a', 'a', 'after') !== items) {
              throw new Error('same item drop should reuse original array');
            }
            if (reorderItemsForDrop(items, 'missing', 'a', 'after') !== items) {
              throw new Error('missing source should reuse original array');
            }
            if (ids(reorderItemsForDrop(items, 'a', 'c', 'sideways')) !== 'b,a,c') {
              throw new Error('unknown position should default to before');
            }
            """
        )

    def test_drop_position_uses_pointer_half(self) -> None:
        run_node(
            """
            const { dropPositionFromClientY } = require('./ui_prototype/reorder.js');
            const rect = { top: 100, height: 40 };

            if (dropPositionFromClientY(rect, 101) !== 'before') {
              throw new Error('top half should drop before');
            }
            if (dropPositionFromClientY(rect, 130) !== 'after') {
              throw new Error('bottom half should drop after');
            }
            """
        )

    def test_scroll_container_content_top_uses_container_relative_coordinates(self) -> None:
        run_node(
            """
            const { scrollContainerContentTop } = require('./ui_prototype/reorder.js');
            const itemRect = { top: 309 };
            const containerRect = { top: 150 };
            if (scrollContainerContentTop(itemRect, containerRect, 0) !== 159) {
              throw new Error('item top should be relative to the scroll container');
            }
            if (scrollContainerContentTop(itemRect, containerRect, 42) !== 201) {
              throw new Error('scroll offset should be included in content coordinates');
            }
            """
        )

    def test_reorder_helper_accepts_numeric_zero_id(self) -> None:
        run_node(
            """
            const { reorderItemsForDrop } = require('./ui_prototype/reorder.js');
            const ids = xs => xs.map(x => x.id).join(',');
            const items = [0, 1, 2].map(id => ({ id }));

            if (ids(reorderItemsForDrop(items, 0, 2, 'after')) !== '1,2,0') {
              throw new Error('numeric zero id should be draggable');
            }
            """
        )

    def test_edge_auto_scroll_accelerates_toward_the_edge(self) -> None:
        run_node(
            """
            const { edgeAutoScrollDelta } = require('./ui_prototype/reorder.js');
            const rect = { top: 100, bottom: 500, height: 400 };
            const upperInner = edgeAutoScrollDelta(rect, 150, 64, 24);
            const upperEdge = edgeAutoScrollDelta(rect, 102, 64, 24);
            const lowerInner = edgeAutoScrollDelta(rect, 450, 64, 24);
            const lowerEdge = edgeAutoScrollDelta(rect, 498, 64, 24);
            if (!(upperEdge < upperInner && upperInner < 0)) {
              throw new Error(`upper edge should accelerate, got ${upperInner}/${upperEdge}`);
            }
            if (!(lowerEdge > lowerInner && lowerInner > 0)) {
              throw new Error(`lower edge should accelerate, got ${lowerInner}/${lowerEdge}`);
            }
            if (edgeAutoScrollDelta(rect, 300, 64, 24) !== 0) {
              throw new Error('middle of the viewport should not auto-scroll');
            }
            """
        )

    def test_edge_auto_scroll_accelerates_with_hold_time_and_normalizes_frames(self) -> None:
        run_node(
            """
            const { acceleratedEdgeAutoScrollDelta } = require('./ui_prototype/reorder.js');
            const rect = { top: 100, bottom: 500, height: 400 };
            const initial = acceleratedEdgeAutoScrollDelta(rect, 496, 64, 24, 0, 1000 / 60);
            const held = acceleratedEdgeAutoScrollDelta(rect, 496, 64, 24, 900, 1000 / 60);
            const highRefresh = acceleratedEdgeAutoScrollDelta(rect, 496, 64, 24, 900, 1000 / 120);
            if (!(held > initial && initial > 0)) {
              throw new Error(`hold duration should accelerate scrolling, got ${initial}/${held}`);
            }
            if (Math.abs((highRefresh * 2) - held) > 2) {
              throw new Error(`frame normalization drifted: 60Hz=${held}, 120Hz=${highRefresh}`);
            }
            if (acceleratedEdgeAutoScrollDelta(rect, 300, 64, 24, 900, 1000 / 60) !== 0) {
              throw new Error('hold acceleration should remain disabled away from the edge');
            }
            """
        )

    def test_problem_display_name_hides_noisy_split_file_names(self) -> None:
        run_node(
            r"""
            const { problemDisplayName } = require('./ui_prototype/reorder.js');
            const noisy = { name: '2026_07_15_very_long_uploaded_exam_scan_final.png (위)' };
            const clean = { name: '이차함수 12번' };
            const generated = { name: 'page-003 problem 7 (아래)' };
            if (problemDisplayName(noisy, 2) !== '문제 3 · 위쪽') {
              throw new Error(`noisy upper split label was not compacted: ${problemDisplayName(noisy, 2)}`);
            }
            if (problemDisplayName(generated, 4) !== '문제 5 · 아래쪽') {
              throw new Error(`generated lower split label was not compacted: ${problemDisplayName(generated, 4)}`);
            }
            if (problemDisplayName(clean, 8) !== clean.name) {
              throw new Error('meaningful problem titles should be preserved');
            }
            """
        )

    def test_problem_source_label_hides_hashes_and_file_paths(self) -> None:
        run_node(
            r"""
            const { problemSourceLabel } = require('./ui_prototype/reorder.js');
            const generated = { source: '096f22823794d518af25f05a6267a8fad8cb2035_exam-page-012' };
            const path = { source: '/tmp/uploads/very_long_exam_scan.png' };
            const clean = { source: '모의고사 · 6월' };
            if (problemSourceLabel(generated) !== '원본 12쪽') {
              throw new Error(`generated page source was not compacted: ${problemSourceLabel(generated)}`);
            }
            if (problemSourceLabel(path) !== '업로드 원본') {
              throw new Error(`file path source was not hidden: ${problemSourceLabel(path)}`);
            }
            if (problemSourceLabel(clean) !== clean.source) {
              throw new Error('meaningful source labels should be preserved');
            }
            """
        )

    def test_board_reflow_reserves_scaled_long_image_height(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            if (start < 0 || end < 0) throw new Error('placement helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            sandbox.normalizeInputIntent = value => value;
            vm.runInNewContext(
              source.slice(start, end) + '\n'
                + 'globalThis.reflowItemsForBoardOrder = reflowItemsForBoardOrder;\n'
                + 'globalThis.placementSlotHeightPages = placementSlotHeightPages;\n',
              sandbox
            );
            const reflowed = sandbox.reflowItemsForBoardOrder([
              { id: 'p13', heightFrac: 1.1, placementScaleRatio: 1.4 },
              { id: 'p14', heightFrac: 0.8, placementScaleRatio: 1.0 },
            ]);
            if (reflowed[0].snappedNextStartYPages !== 2) {
              throw new Error(`expected scaled long image to ceil to page 2, got ${reflowed[0].snappedNextStartYPages}`);
            }
            if (reflowed[0].renderedBottomYPages !== 1.54) {
              throw new Error(`expected rendered bottom 1.54 pages, got ${reflowed[0].renderedBottomYPages}`);
            }
            if (reflowed[1].startYPages !== 2) {
              throw new Error(`expected following item to start at page 2, got ${reflowed[1].startYPages}`);
            }
            if (sandbox.placementSlotHeightPages(reflowed[0]) < 1.54) {
              throw new Error('slot height should include scaled rendered height');
            }
            """
        )

    def test_board_reflow_places_items_across_columns_with_shared_row_height(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            if (start < 0 || end < 0) throw new Error('placement helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            sandbox.normalizeInputIntent = value => value;
            vm.runInNewContext(
              source.slice(start, end) + '\n'
                + 'globalThis.reflowItemsForBoardOrder = reflowItemsForBoardOrder;\n',
              sandbox
            );

            const reflowed = sandbox.reflowItemsForBoardOrder([
              { id: 'p1', heightFrac: 0.5, placementScaleRatio: 1.0 },
              { id: 'p2', heightFrac: 1.4, placementScaleRatio: 1.0 },
              { id: 'p3', heightFrac: 0.7, placementScaleRatio: 1.0 },
            ], 1.2, 2);

            if (reflowed[0].startYPages !== 0 || reflowed[1].startYPages !== 1.2) {
              throw new Error(`long item should start on its own row, got ${reflowed[0].startYPages}/${reflowed[1].startYPages}`);
            }
            if (reflowed[1].boardColumnCount !== 1 || reflowed[1].placementXRatio !== 0) {
              throw new Error(`long item should reserve a full row, got ${reflowed[1].boardColumnCount}/${reflowed[1].placementXRatio}`);
            }
            if (reflowed[0].snappedNextStartYPages !== 1.2 || reflowed[1].snappedNextStartYPages !== 3) {
              throw new Error(`long row should ceil to page 3, got ${reflowed[0].snappedNextStartYPages}/${reflowed[1].snappedNextStartYPages}`);
            }
            if (reflowed[2].startYPages !== 3) {
              throw new Error(`next row should start at page 3, got ${reflowed[2].startYPages}`);
            }
            const magnetReflowed = sandbox.reflowItemsForBoardOrder([
              { id: 'p4', heightFrac: 0.5, placementScaleRatio: 1.0, placementXEdited: true, placementXRatio: 0.5, placementMagnetColumnIndex: 1 },
            ], 1.2, 3);
            if (magnetReflowed[0].placementMagnetColumnIndex !== 1 || magnetReflowed[0].placementXRatio !== 0.5) {
              throw new Error(`manual magnet column should survive reflow, got ${JSON.stringify(magnetReflowed[0])}`);
            }
          """
        )

    def test_board_reflow_keeps_finite_alias_height_and_never_rounds_down(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            sandbox.normalizeInputIntent = value => value;
            vm.runInNewContext(
              source.slice(start, end) + '\n'
                + 'globalThis.reflowItemsForBoardOrder = reflowItemsForBoardOrder;\n',
              sandbox
            );
            const aliased = sandbox.reflowItemsForBoardOrder([
              { id: 'long', heightFrac: 'bad', actual_height_pages: '2.05' },
              { id: 'next', heightFrac: 0.8 },
            ]);
            if (aliased[0].heightFrac !== 2.05 || aliased[0].snappedNextStartYPages !== 3) {
              throw new Error(`valid snake-case height was not preserved: ${JSON.stringify(aliased[0])}`);
            }
            if (aliased[1].startYPages < aliased[0].renderedBottomYPages) {
              throw new Error('following item overlaps the aliased long item');
            }

            const boundary = sandbox.reflowItemsForBoardOrder([
              { id: 'edge', heightFrac: 1.2005 },
              { id: 'after', heightFrac: 0.8 },
            ]);
            if (boundary[0].snappedNextStartYPages !== 2 || boundary[1].startYPages !== 2) {
              throw new Error(`page ceil failed at the slot boundary: ${JSON.stringify(boundary)}`);
            }
            if (boundary[1].startYPages < boundary[0].renderedBottomYPages) {
              throw new Error('boundary item was rounded down into an overlap');
            }
            """
        )

    def test_page_as_is_reflow_uses_scaled_continuous_flow(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            if (start < 0 || end < 0) throw new Error('placement helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            sandbox.normalizeInputIntent = value => value;
            vm.runInNewContext(
              source.slice(start, end) + '\n'
                + 'globalThis.reflowItemsForBoardOrder = reflowItemsForBoardOrder;\n',
              sandbox
            );

            const reflowed = sandbox.reflowItemsForBoardOrder([
              { id: 'page-1', heightFrac: 0.9, placementScaleRatio: 1.6, inputIntent: 'page-as-is' },
              { id: 'page-2', heightFrac: 0.5, placementScaleRatio: 1.6, placementMode: 'continuous-page-as-is' },
            ], 1.2, 3);

            if (reflowed[0].boardColumnCount !== 1 || reflowed[1].boardColumnCount !== 1) {
              throw new Error(`page-as-is should ignore multi-column grouping, got ${reflowed[0].boardColumnCount}/${reflowed[1].boardColumnCount}`);
            }
            if (reflowed[0].snappedNextStartYPages !== 1.44) {
              throw new Error(`first page should reserve scaled proportional height 1.44p, got ${reflowed[0].snappedNextStartYPages}`);
            }
            if (reflowed[1].startYPages !== 1.44) {
              throw new Error(`second page should start immediately after first, got ${reflowed[1].startYPages}`);
            }
            if (reflowed[1].snappedNextStartYPages !== 2.24) {
              throw new Error(`second page should keep proportional continuous height, got ${reflowed[1].snappedNextStartYPages}`);
            }
          """
        )

    def test_board_column_magnet_snaps_near_guides(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            if (start < 0 || end < 0) throw new Error('placement helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(
              source.slice(start, end) + '\n'
                + 'globalThis.nearestBoardColumnMagnet = nearestBoardColumnMagnet;\n'
                + 'globalThis.resolveBoardDragMagnet = resolveBoardDragMagnet;\n'
                + 'globalThis.boardColumnRatios = boardColumnRatios;\n',
              sandbox
            );

            const ratios = sandbox.boardColumnRatios(3).join(',');
            if (ratios !== '0,0.5,1') {
              throw new Error(`unexpected 3-column ratios: ${ratios}`);
            }
            const snapped = sandbox.nearestBoardColumnMagnet(0.96, 3, 640, 200);
            if (!snapped.snapped || snapped.ratio !== 1 || snapped.index !== 2) {
              throw new Error(`expected snap to right column, got ${JSON.stringify(snapped)}`);
            }
            const free = sandbox.nearestBoardColumnMagnet(0.73, 3, 640, 200);
            if (free.snapped || free.ratio !== 0.73) {
              throw new Error(`expected free placement away from guides, got ${JSON.stringify(free)}`);
            }
            const single = sandbox.nearestBoardColumnMagnet(0.42, 1, 640, 200);
            if (single.snapped || single.ratio !== 0.42) {
              throw new Error(`single-column drag should stay free, got ${JSON.stringify(single)}`);
            }
            const stickyStart = sandbox.resolveBoardDragMagnet(0.04, {
              columnCount: 3,
              tileWidth: 200,
              startMagnetColumnIndex: 0,
              currentDxPx: 6,
            }, 640);
            if (!stickyStart.snapped || stickyStart.ratio !== 0) {
              throw new Error(`tiny movement should still honor start magnet, got ${JSON.stringify(stickyStart)}`);
            }
            const releasedStart = sandbox.resolveBoardDragMagnet(0.04, {
              columnCount: 3,
              tileWidth: 200,
              startMagnetColumnIndex: 0,
              currentDxPx: 18,
            }, 640);
            if (releasedStart.snapped || releasedStart.ratio !== 0.04) {
              throw new Error(`horizontal drag should unstick from start magnet, got ${JSON.stringify(releasedStart)}`);
            }
          """
        )

    def test_fit_width_page_as_is_uses_full_board_scale(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            if (start < 0 || end < 0) throw new Error('placement helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            sandbox.normalizeInputIntent = value => value;
            vm.runInNewContext(
              source.slice(start, end) + '\n'
                + 'globalThis.fitWidthContinuousPageItem = fitWidthContinuousPageItem;\n',
              sandbox
            );

            const fitted = sandbox.fitWidthContinuousPageItem({ id: 'page-1', heightFrac: 0.8 });
            if (fitted.placementScaleRatio !== 3) {
              throw new Error(`expected fit-width scale 3, got ${fitted.placementScaleRatio}`);
            }
            if (fitted.snappedNextStartYPages !== 2.4) {
              throw new Error(`expected proportional continuous height 2.4p, got ${fitted.snappedNextStartYPages}`);
            }
          """
        )

    def test_classin_preview_page_number_uses_12_page_basis(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            if (start < 0 || end < 0) throw new Error('placement helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            sandbox.normalizeInputIntent = value => value;
            vm.runInNewContext(
              source.slice(start, end) + '\n'
                + 'globalThis.classinBoardPageNumberAtOffset = classinBoardPageNumberAtOffset;\n',
              sandbox
            );

            const cases = [
              [0, 1],
              [1, 1],
              [2, 2],
              [6, 6],
              [7, 6],
              [7.2, 7],
              ['2.4', 3],
              [Infinity, 1],
              [-Infinity, 1],
              [NaN, 1],
              ['invalid', 1],
            ];
            for (const [offset, expected] of cases) {
              const actual = sandbox.classinBoardPageNumberAtOffset(offset);
              if (actual !== expected) {
                throw new Error(`offset ${offset} should be ClassIn page ${expected}, got ${actual}`);
              }
            }
            """
        )

    def test_fit_width_page_as_is_preserves_korean_english_step_state(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const FIXED_LEFT_ZONE_RATIO =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            if (start < 0 || end < 0) throw new Error('placement helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            sandbox.normalizeInputIntent = value => value;
            vm.runInNewContext(
              source.slice(start, end) + '\n'
                + 'globalThis.fitWidthContinuousPageItem = fitWidthContinuousPageItem;\n',
              sandbox
            );

            for (const { step, name, sourceName } of [
              { step: 's1', name: '국어 지문 1', sourceName: '국어 6월 모의고사' },
              { step: 's2', name: 'English passage 24', sourceName: 'English Reading Set' },
              { step: 's3', name: '영어 재구성 테스트', sourceName: 'English reconstruction page' },
            ]) {
              const fitted = sandbox.fitWidthContinuousPageItem({
                id: `${step}-page`,
                name,
                source: sourceName,
                step,
                heightFrac: 0.72,
                placementScaleRatio: 1,
              });
              if (fitted.name !== name || fitted.source !== sourceName) {
                throw new Error(`Korean/English labels should survive fit width, got ${fitted.name}/${fitted.source}`);
              }
              if (fitted.step !== step) {
                throw new Error(`step ${step} should survive fit width, got ${fitted.step}`);
              }
              if (fitted.inputIntent !== 'page-as-is' || fitted.input_intent !== 'page-as-is') {
                throw new Error(`input intent should stay page-as-is, got ${fitted.inputIntent}/${fitted.input_intent}`);
              }
              if (fitted.placementMode !== 'continuous-page-as-is' || fitted.placement_mode !== 'continuous-page-as-is') {
                throw new Error(`placement mode should stay continuous, got ${fitted.placementMode}/${fitted.placement_mode}`);
              }
              if (!fitted.forceFullPageBounds || !fitted.force_full_page_bounds) {
                throw new Error('page-as-is should keep full page bounds');
              }
              if (fitted.placementScaleRatio !== 3 || fitted.snappedNextStartYPages !== 2.16) {
                throw new Error(`fit width should force proportional full-board flow, got ${fitted.placementScaleRatio}/${fitted.snappedNextStartYPages}`);
              }
            }
          """
        )


if __name__ == "__main__":
    unittest.main()
