"""ClassIn content scales with width inside a full-height scrollable viewport."""
from __future__ import annotations

import unittest
from pathlib import Path

from edb_builder import CANVAS_HEIGHT, CANVAS_WIDTH
from layout_template_schema import LayoutTemplate
from test_ui_placement_details import NODE_SETUP, run_node


ROOT = Path(__file__).resolve().parent
CSS = (ROOT / "ui_prototype" / "board.css").read_text(encoding="utf-8")
FOCUS_CSS = (ROOT / "ui_prototype" / "editor-focus.css").read_text(encoding="utf-8")
APP = (ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")

DISPLAY_SETUP = NODE_SETUP + r"""
const sandbox = placementSandbox();
const fixture = savedFixture(sandbox, [34.3, 0.8]);
const before = JSON.stringify(fixture);
Object.assign(sandbox, {
  items: fixture.items, layoutItems: fixture.items, activeId: 'p1',
  useMemo: calculate => calculate(), columnCount: 1,
  previewFitActive: false, manualPreviewZoom: 1, contentW: 800,
  basePageH: sandbox.boardPreviewBasePageHeight(362, 26, 70),
});
const derivation = section('  const fitPreviewItem = useMemo(', '  // Compute pixel positions');
function derive() {
  const result = vm.runInContext('(() => {' + derivation + 'return { previewZoom, pageH }; })()', sandbox);
  Object.assign(sandbox, result);
  return result;
}
function deriveLayout() {
  return vm.runInContext('(() => {'
    + section('  // Compute pixel positions', '  const previewEstimate =')
    + 'return layout; })()', sandbox);
}
"""


def rule(source: str, selector: str) -> str:
    return source.split(selector + "{", 1)[1].split("}", 1)[0]


class TestClassinBoardRatio(unittest.TestCase):
    def test_content_uses_export_units_instead_of_a_fixed_viewport_ratio(self) -> None:
        # EDB's historic header names are reversed relative to displayed axes.
        slot_height = LayoutTemplate(name="ratio-check").base_slot_height_pages
        run_node(DISPLAY_SETUP + f"""
        assert.equal(vm.runInContext('EDB_CANVAS_WIDTH_PX', sandbox), {CANVAS_HEIGHT});
        assert.equal(vm.runInContext('EDB_CANVAS_PAGE_HEIGHT_PX', sandbox), {CANVAS_WIDTH});
        assert.equal(vm.runInContext('DEFAULT_SLOT_HEIGHT_PAGES', sandbox), {slot_height});
        const pageHeight = derive().pageH * {slot_height};
        assert.equal(pageHeight, sandbox.contentW * {CANVAS_WIDTH} * {slot_height} / {CANVAS_HEIGHT});
        assert.equal(sandbox.contentW / pageHeight, {CANVAS_HEIGHT} / ({CANVAS_WIDTH} * {slot_height}));
        """)
        self.assertNotIn("CLASSIN_BOARD_ASPECT_RATIO", APP)
        self.assertNotIn("--classin-board-aspect-ratio", APP)

    def test_manual_preview_ignores_height_resize_and_tracks_width_proportionally(self) -> None:
        run_node(DISPLAY_SETUP + r"""
        const originalHeight = derive().pageH;
        assert.equal(originalHeight, 800 * 590 / 1280);
        for (const viewportHeight of [180, 600, 1000]) {
          sandbox.basePageH = sandbox.boardPreviewBasePageHeight(viewportHeight, 26, 70);
          assert.equal(derive().pageH, originalHeight, 'extra viewport height must expose more content, not stretch it');
        }
        for (const width of [400, 600, 1200]) {
          sandbox.contentW = width;
          assert.equal(derive().pageH, originalHeight * width / 800);
        }
        sandbox.manualPreviewZoom = 0.5;
        assert.equal(derive().pageH, originalHeight * 1200 / 800 * 0.5);
        assert.equal(JSON.stringify(fixture), before, 'display resize and zoom never modify saved placement');
        """)

    def test_fit_still_uses_available_viewport_height_and_handles_long_images(self) -> None:
        run_node(DISPLAY_SETUP + r"""
        sandbox.previewFitActive = true;
        for (const viewportHeight of [180, 362, 900]) {
          const usable = viewportHeight - 26 - 70;
          sandbox.basePageH = sandbox.boardPreviewBasePageHeight(viewportHeight, 26, 70);
          for (const width of [400, 800, 1200]) {
            sandbox.contentW = width;
            const renderedHeight = derive().pageH * sandbox.itemRenderedHeightPages(fixture.items[0]);
            assert.ok(Math.abs(renderedHeight - usable * 0.9) < 0.001,
              'fit must use visible height even below the manual zoom minimum');
          }
        }
        sandbox.previewFitActive = false;
        assert.equal(derive().pageH, sandbox.contentW * 590 / 1280, 'leaving fit returns to width-based scale');
        assert.equal(JSON.stringify(fixture), before);
        """)

    def test_last_positioned_item_and_add_action_remain_inside_scrollable_content(self) -> None:
        run_node(DISPLAY_SETUP + r"""
        sandbox.layoutItems = sandbox.reflowItemsForBoardOrder([
          fixture.items[0],
          { ...fixture.items[1], placementExtraSlots: 3, placementYRatio: 1 },
        ]);
        sandbox.items = sandbox.layoutItems;
        for (const width of [400, 800, 1200]) {
          sandbox.contentW = width;
          derive();
          const layout = deriveLayout();
          for (const placement of layout.positions) {
            const height = placement.renderedHeightPages * sandbox.pageH;
            const room = Math.max(0, placement.snappedNext * sandbox.pageH - placement.top - height);
            const bottom = placement.top + placement.yRatio * room + height;
            assert.ok(bottom <= layout.totalH, 'even a vertically moved last item must not be clipped');
          }
          assert.equal(layout.totalH, layout.endTop + layout.endH, 'the add action contributes to scroll extent');
          assert.ok(layout.endH > 0);
        }
        assert.equal(JSON.stringify(fixture), before);
        """)

    def test_board_uses_full_height_and_retains_vertical_scrolling(self) -> None:
        stage_css = CSS.split("/* Stage (big board) */", 1)[1]
        wrapper = rule(stage_css, ".stage-wrap")
        board = rule(stage_css, ".stage-board")
        scroll = rule(stage_css, ".stage-scroll")
        self.assertIn("flex: 1", wrapper)
        self.assertIn("min-height: 0", wrapper)
        self.assertNotIn("container-type:", wrapper)
        self.assertNotIn("--stage-preview-max-width", wrapper)
        self.assertIn("flex: 1 1 0", board)
        self.assertIn("min-height: 0", board)
        self.assertIn("width: 100%", board)
        self.assertNotRegex(board, r"(?:aspect-ratio|max-width):")
        self.assertIn("overflow-y: auto", scroll)
        self.assertIn("inset: 0", scroll)
        focus_board = rule(FOCUS_CSS, ".main.editor-focus-mode .stage-board ")
        self.assertNotRegex(focus_board, r"(?:flex|width|max-width|height|aspect-ratio):")
        self.assertNotIn("--stage-preview-max-width:", FOCUS_CSS)
        self.assertIn("--focus-panel-gutter: 0px", FOCUS_CSS)

    def test_real_images_remain_contained_and_top_aligned(self) -> None:
        image = rule(CSS, ".tile-img")
        stage_image = rule(CSS, ".stage-tile .tile-img")
        self.assertIn("object-fit: contain", image)
        self.assertIn("width: 100%; height: 100%", image)
        self.assertIn("object-position: center top", stage_image)
        self.assertNotRegex(stage_image, r"object-fit:\s*(?:fill|cover)")
        self.assertNotRegex(stage_image, r"transform:\s*scale[XY]\(")


if __name__ == "__main__":
    unittest.main()
