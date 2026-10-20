from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


NODE_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `missing section ${start}`);
  return source.slice(from, to);
}
const plain = value => JSON.parse(JSON.stringify(value));
const finiteNumber = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
function maskWithInk(width, height, inkRects) {
  const dark = new Uint8Array(width * height);
  inkRects.forEach(([x0, y0, x1, y1]) => {
    for (let y = y0; y < y1; y += 1) for (let x = x0; x < x1; x += 1) dark[y * width + x] = 1;
  });
  return { width, height, dark };
}
// Text lines 10px tall every 20px from `top`, `count` lines, between x0 and x1.
function lines(x0, x1, top, count) {
  return Array.from({ length: count }, (_, index) => [x0, top + index * 20, x1, top + index * 20 + 10]);
}
const sandbox = { finiteNumber, Math, Number, Uint8Array, Map, Promise, Array };
vm.createContext(sandbox);
vm.runInContext(section('function adjustOriginalBox(', 'function OriginalNumberInput('), sandbox);
const page = { width: 1000, height: 1400 };
const wholePage = { left: 0, top: 0, width: 1000, height: 1400 };
const tops = boxes => plain(boxes).map(box => [box.top, box.top + box.height]);
"""


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", NODE_SETUP + script], cwd=PROJECT_ROOT, check=True)


class TestManualSplitAutoBoxes(unittest.TestCase):
    def test_single_column_splits_at_wide_gaps_but_not_between_lines(self) -> None:
        run_node(r"""
        const mask = maskWithInk(1000, 1400, [
          ...lines(100, 900, 100, 4),   // problem 1: rows 100..170
          ...lines(100, 700, 300, 3),   // problem 2: rows 300..350
          ...lines(100, 900, 520, 5),   // problem 3: rows 520..610
        ]);
        const boxes = sandbox.manualSplitAutoBoxes(mask, wholePage, page, { wholePage: true });
        assert.deepEqual(tops(boxes), [[94, 176], [294, 356], [514, 616]]);
        const widths = plain(boxes).map(box => [box.left, box.width]);
        assert.deepEqual(widths, [[94, 812], [94, 812], [94, 812]], 'one column shares the same ink width');
        """)

    def test_two_columns_with_a_gutter_rule_split_per_column_in_reading_order(self) -> None:
        run_node(r"""
        const mask = maskWithInk(1000, 1400, [
          ...lines(60, 460, 100, 4), ...lines(60, 460, 400, 4),
          ...lines(540, 940, 150, 4), ...lines(540, 940, 500, 4),
          [499, 60, 501, 1340],         // thin vertical rule in the gutter
        ]);
        const boxes = plain(sandbox.manualSplitAutoBoxes(mask, wholePage, page, { wholePage: true }));
        assert.equal(boxes.length, 4);
        assert.deepEqual(boxes.map(box => box.left < 500 ? 'L' : 'R'), ['L', 'L', 'R', 'R']);
        assert.ok(boxes.every(box => box.left + box.width <= 506 || box.left >= 494), 'no box spans the gutter');
        """)

    def test_lone_page_number_and_running_header_are_left_out_on_a_whole_page(self) -> None:
        run_node(r"""
        const mask = maskWithInk(1000, 1400, [
          [450, 30, 550, 45],           // running header
          ...lines(100, 900, 200, 4),
          ...lines(100, 900, 500, 4),
          [480, 1350, 520, 1365],       // page number
        ]);
        assert.deepEqual(tops(sandbox.manualSplitAutoBoxes(mask, wholePage, page, { wholePage: true })), [[194, 276], [494, 576]]);
        assert.equal(sandbox.manualSplitAutoBoxes(mask, wholePage, page, { wholePage: false }).length, 4,
          'inside a drawn region every block is kept');
        """)

    def test_selected_region_is_split_within_its_own_bounds(self) -> None:
        run_node(r"""
        const mask = maskWithInk(1000, 1400, [...lines(100, 900, 100, 4), ...lines(100, 900, 400, 4)]);
        const region = { left: 50, top: 60, width: 900, height: 500 };
        const boxes = plain(sandbox.manualSplitAutoBoxes(mask, region, page));
        assert.deepEqual(tops(boxes), [[94, 176], [394, 476]]);
        assert.ok(boxes.every(box => box.left >= 50 && box.left + box.width <= 950));
        assert.deepEqual(plain(sandbox.manualSplitAutoBoxes(maskWithInk(1000, 1400, []), region, page)), []);
        """)

    def test_merge_and_blank_bands(self) -> None:
        run_node(r"""
        assert.deepEqual(plain(sandbox.mergedManualSplitBox([
          { left: 100, top: 100, width: 300, height: 50 }, { left: 80, top: 300, width: 200, height: 40 },
        ], page)), { left: 80, top: 100, width: 320, height: 240 });
        assert.equal(sandbox.mergedManualSplitBox([], page), null);
        const mask = maskWithInk(10, 100, [[0, 10, 10, 20], [0, 22, 10, 30], [0, 60, 10, 70]]);
        assert.deepEqual(plain(sandbox.inkBlankBands(mask, 3)), [{ start: 30, end: 60 }],
          'gaps under three rows and the blank paper outside the ink are not bands');
        """)


class TestManualSplitRegionOrder(unittest.TestCase):
    def test_two_column_regions_read_left_column_first(self) -> None:
        run_node(r"""
        const order = Object.assign(vm.createContext({ finiteNumber, MANUAL_SPLIT_ROW_TOLERANCE_RATIO: 0.03,
          manualSplitTitle: index => `문제 ${index + 1}` }), {});
        vm.runInContext(section('function renumberManualSplitRegions(', 'function serializeManualSplitRegions('), order);
        const region = (id, left, top, width = 400) => ({ id, bbox: { left, top, width, height: 80 } });
        const columns = order.sortManualSplitRegions([region('R1', 540, 100), region('L2', 60, 500), region('L1', 60, 120),
          region('R2', 540, 480)], page);
        assert.deepEqual(plain(columns.map(item => item.id)), ['L1', 'L2', 'R1', 'R2']);
        assert.deepEqual(plain(columns.map(item => item.order)), [1, 2, 3, 4]);
        const mixed = order.sortManualSplitRegions([region('B', 540, 300), region('wide', 60, 100, 880), region('A', 60, 300)], page);
        assert.deepEqual(plain(mixed.map(item => item.id)), ['wide', 'A', 'B'], 'a full-width region keeps row order');
        """)


class TestBoardSplitLine(unittest.TestCase):
    def test_line_snaps_to_blank_bands_and_opens_on_the_widest_middle_band(self) -> None:
        run_node(r"""
        vm.runInContext(section('const BOARD_SPLIT_EDGE_RATIO', 'function BoardSplitOverlay('), sandbox);
        const mask = maskWithInk(10, 1000, [[0, 20, 10, 300], [0, 330, 10, 420], [0, 600, 10, 980]]);
        const candidates = plain(sandbox.boardSplitCandidates(mask));
        assert.deepEqual(candidates.map(c => c.center), [0.315, 0.51]);
        assert.equal(sandbox.initialBoardSplitRatio(candidates), 0.51, 'the wide band between the problems wins');
        assert.equal(sandbox.snapBoardSplitRatio(0.49, candidates), 0.51);
        assert.equal(sandbox.snapBoardSplitRatio(0.42, candidates), 0.42, 'far from a band the line follows the pointer');
        assert.equal(sandbox.snapBoardSplitRatio(0.01, candidates), 0.06, 'the line stays off the very ends');
        assert.equal(sandbox.steppedBoardSplitRatio(0.51, candidates, -1), 0.315);
        assert.equal(sandbox.steppedBoardSplitRatio(0.51, candidates, 1), 0.53, 'past the last band it moves in small steps');
        assert.equal(sandbox.initialBoardSplitRatio([]), 0.5);
        """)


if __name__ == "__main__":
    unittest.main()
