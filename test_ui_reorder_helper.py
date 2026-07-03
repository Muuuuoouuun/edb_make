from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiReorderHelper(unittest.TestCase):
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
            if (reflowed[0].snappedNextStartYPages !== 2.4) {
              throw new Error(`expected scaled long image to reserve 2.4 pages, got ${reflowed[0].snappedNextStartYPages}`);
            }
            if (reflowed[0].renderedBottomYPages !== 1.54) {
              throw new Error(`expected rendered bottom 1.54 pages, got ${reflowed[0].renderedBottomYPages}`);
            }
            if (reflowed[1].startYPages !== 2.4) {
              throw new Error(`expected following item to start at 2.4 pages, got ${reflowed[1].startYPages}`);
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

            if (reflowed[0].startYPages !== 0 || reflowed[1].startYPages !== 0) {
              throw new Error(`first row should share start page, got ${reflowed[0].startYPages}/${reflowed[1].startYPages}`);
            }
            if (reflowed[0].placementXRatio !== 0 || reflowed[1].placementXRatio !== 1) {
              throw new Error(`expected two column x ratios 0/1, got ${reflowed[0].placementXRatio}/${reflowed[1].placementXRatio}`);
            }
            if (reflowed[0].snappedNextStartYPages !== 2.4 || reflowed[1].snappedNextStartYPages !== 2.4) {
              throw new Error(`row height should follow taller neighbor, got ${reflowed[0].snappedNextStartYPages}/${reflowed[1].snappedNextStartYPages}`);
            }
            if (reflowed[2].startYPages !== 2.4) {
              throw new Error(`next row should start after shared row height, got ${reflowed[2].startYPages}`);
            }
            const magnetReflowed = sandbox.reflowItemsForBoardOrder([
              { id: 'p4', heightFrac: 0.5, placementScaleRatio: 1.0, placementXEdited: true, placementXRatio: 0.5, placementMagnetColumnIndex: 1 },
            ], 1.2, 3);
            if (magnetReflowed[0].placementMagnetColumnIndex !== 1 || magnetReflowed[0].placementXRatio !== 0.5) {
              throw new Error(`manual magnet column should survive reflow, got ${JSON.stringify(magnetReflowed[0])}`);
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
          """
        )


if __name__ == "__main__":
    unittest.main()
