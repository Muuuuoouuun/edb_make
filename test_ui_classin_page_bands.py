"""ClassIn page guides follow rendered positions and preserve page-first defaults."""
from pathlib import Path
import re
import unittest

from test_ui_preview_summary import NODE_SETUP, run_node


ROOT = Path(__file__).resolve().parent
CSS = (ROOT / "ui_prototype" / "board.css").read_text(encoding="utf-8")

BAND_SETUP = NODE_SETUP + r"""
const bandStart = source.indexOf('function buildClassinPageBands(');
const bandEnd = source.indexOf('function BoardStage(', bandStart);
assert.ok(bandStart >= 0 && bandEnd > bandStart);
vm.runInContext(source.slice(bandStart, bandEnd), sandbox);
"""


class TestClassinPageBands(unittest.TestCase):
    def test_default_layout_puts_each_regular_problem_on_its_own_classin_page(self):
        run_node(NODE_SETUP + r"""
        const original = [
          { id: 'short', heightFrac: 0.4 },
          { id: 'normal', heightFrac: 0.8 },
          { id: 'near-boundary', heightFrac: 1.26 },
          { id: 'next', heightFrac: 0.5 },
        ];
        const before = JSON.stringify(original);
        const items = sandbox.reflowItemsForBoardOrder(original);
        assert.equal(JSON.stringify(original), before, 'reflow does not mutate source items');
        items.forEach((item, index) => {
          near(item.startYPages, index * 1.2, 'each regular problem starts on its own ClassIn page');
          near(item.snappedNextStartYPages, (index + 1) * 1.2, 'one page remains reserved');
          assert.equal(item.boardColumnCount, 1);
          assert.equal(item.layoutGapMode, 'classin-grid');
          assert.ok(sandbox.itemRenderedHeightPages(item) <= 1.2 + 1e-9);
        });
        near(items[2].placementScaleRatio, 1.2 / 1.26, 'slight overflow fits without cutting');
        assert.equal(sandbox.deriveBoardPreviewEstimate(items, null).classinPageCount, 4);
        """)

    def test_long_original_and_explicit_compact_placement_remain_intact(self):
        run_node(NODE_SETUP + r"""
        const long = sandbox.reflowItemsForBoardOrder([
          { id: 'long', heightFrac: 2.7 }, { id: 'next', heightFrac: 0.5 },
        ]);
        near(sandbox.itemRenderedHeightPages(long[0]), 2.7, 'long problem is never cut or squeezed');
        near(long[1].startYPages, 3.6, 'next problem starts on the next full ClassIn page');
        const original = sandbox.reflowItemsForBoardOrder([
          { id: 'original', heightFrac: 4.3, inputIntent: 'page-as-is' },
        ])[0];
        near(sandbox.itemRenderedHeightPages(original), 4.3, 'full-page original stays continuous');
        near(original.snappedNextStartYPages, 4.3, 'original flow is not padded into a problem slot');
        const manual = sandbox.reflowItemsForBoardOrder([
          { id: 'manual', heightFrac: 1.26, placementScaleRatio: 1, placementAutoFitDisabled: true },
        ])[0];
        near(manual.placementScaleRatio, 1, 'manual scale is not overwritten by page-first defaults');
        const compact = reflow([{ id: 'a', heightFrac: 0.4 }, { id: 'b', heightFrac: 0.5 }], 1, 'compact');
        near(compact[1].startYPages, 0.4, 'explicit compact choice is retained');
        """)

    def test_page_labels_follow_vertical_move_and_continuation_without_changing_layout(self):
        run_node(BAND_SETUP + r"""
        const layout = { positions: [
          { startPages: 0, renderedHeightPages: 0.6, rowHeightPages: 2.4, yRatio: 1 },
          { startPages: 2.4, renderedHeightPages: 2.2, rowHeightPages: 2.4, yRatio: 0 },
        ] };
        const before = JSON.stringify(layout);
        const bands = sandbox.buildClassinPageBands(layout, 240, 5);
        assert.equal(JSON.stringify(layout), before);
        assert.deepEqual(plain(bands.map(band => band.label)), [
          '판서 공간', '01번', '02번', '앞 문항 이어짐', '판서 공간',
        ]);
        bands.forEach((band, index) => {
          near(band.top, index * 1.2 * 240, 'shade begins at exact page boundary');
          near(band.offsetPages, index * 1.2, 'internal height accompanies ClassIn page number');
        });
        const exactBoundary = sandbox.buildClassinPageBands({ positions: [
          { startPages: 0, renderedHeightPages: 1.2, rowHeightPages: 1.2 },
        ] }, 240, 2);
        assert.equal(exactBoundary[1].label, '판서 공간', 'exact boundary does not leak continuation');
        assert.ok(source.includes('ClassIn {band.page}쪽 · {band.offsetPages.toFixed(1)}p'));
        """)

    def test_shading_is_noninteractive_and_page_head_is_not_a_pill_or_extra_toolbar(self):
        band = re.search(r"\.page-band\{([^}]+)\}", CSS)[1]
        even = re.search(r"\.page-band\.is-even\{([^}]+)\}", CSS)[1]
        head = re.search(r"\.page-head\{([^}]+)\}", CSS)[1]
        chip = re.search(r"\.page-head-chip\{([^}]+)\}", CSS)[1]
        self.assertIn("pointer-events: none", band)
        self.assertIn("background:", band)
        self.assertIn("background:", even)
        self.assertIn("1px dashed", head)
        self.assertIn("height: 0", head)
        self.assertIn("border-radius: 4px", chip)
        self.assertNotIn("999px", chip)


if __name__ == '__main__':
    unittest.main()
