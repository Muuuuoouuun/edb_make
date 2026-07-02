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
            const start = source.indexOf('const DEFAULT_SLOT_HEIGHT_PAGES =');
            const end = source.indexOf('const INITIAL_ITEMS =');
            if (start < 0 || end < 0) throw new Error('placement helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
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


if __name__ == "__main__":
    unittest.main()
