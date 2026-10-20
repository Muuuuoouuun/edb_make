"""Regression tests for the five known issues listed for 0.2.4."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw

import app_server
import build_problem_board_edb as board
from test_ui_placement_details import NODE_SETUP, run_node


KEY_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `source bounds missing: ${start}`);
  return source.slice(from, to);
}
const body = { tagName: 'BODY' };
let modalOpen = false;
const doc = { body, documentElement: { tagName: 'HTML' },
  querySelector: () => (modalOpen ? {} : null) };
const tile = { tagName: 'BUTTON' };
const inside = { tagName: 'I' };
const overlay = { tagName: 'DIV', ownerDocument: doc,
  contains: node => node === inside, closest: selector => (selector === '.stage-tile' ? tile : null) };
const sandbox = { document: doc };
vm.createContext(sandbox);
vm.runInContext(section('function isEditableKeyboardTarget(', '// A problem moved to another row')
  + section('function boardSplitKeyBelongsToOverlay(', 'function BoardSplitOverlay('), sandbox);
const key = (target, extra = {}) => ({ key: 'Enter', target, defaultPrevented: false, ...extra });
const belongs = (event) => sandbox.boardSplitKeyBelongsToOverlay(event, overlay);
"""


INSERT_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
const from = source.indexOf('function smartBoardEndInsertionTarget(');
const to = source.indexOf('function BoardTileContents(', from);
assert.ok(from >= 0 && to > from);
const sandbox = { Math, Number, Array, String };
vm.createContext(sandbox);
vm.runInContext(source.slice(from, to), sandbox);
const target = (...args) => JSON.parse(JSON.stringify(sandbox.smartBoardEndInsertionTarget(...args)));
const pageH = 100;
const pos = (top, rendered, row = rendered, yRatio = 0) => ({ top, renderedHeightPages: rendered, rowHeightPages: row, yRatio });
"""


class BoardSplitEnterScopeTest(unittest.TestCase):
    """Issue 1: Enter in another input no longer confirms the board split."""

    def test_split_keys_only_act_on_the_board(self) -> None:
        run_node(KEY_SETUP + r"""
        assert.equal(belongs(key(overlay)), true, 'the focused overlay');
        assert.equal(belongs(key(inside)), true, 'a child of the overlay');
        assert.equal(belongs(key(tile)), true, 'its own tile');
        assert.equal(belongs(key(body)), true, 'nothing focused');
        assert.equal(belongs(key({ tagName: 'INPUT' })), false, 'typing in an input');
        assert.equal(belongs(key({ tagName: 'TEXTAREA' })), false);
        assert.equal(belongs(key({ tagName: 'DIV', isContentEditable: true })), false);
        assert.equal(belongs(key({ tagName: 'BUTTON' })), false, 'another control keeps its Enter');
        assert.equal(belongs(key({ tagName: 'BUTTON' }, { key: 'ArrowDown' })), false);
        assert.equal(belongs(key({ tagName: 'BUTTON' }, { key: 'Escape' })), true, 'Esc still cancels from a button');
        assert.equal(belongs(key({ tagName: 'INPUT' }, { key: 'Escape' })), false, 'but not from an input');
        assert.equal(belongs(key(overlay, { isComposing: true })), false, 'Korean IME composition');
        assert.equal(belongs(key(overlay, { keyCode: 229 })), false);
        assert.equal(belongs(key(overlay, { ctrlKey: true })), false);
        assert.equal(belongs(key(overlay, { defaultPrevented: true })), false);
        modalOpen = true;
        assert.equal(belongs(key(body)), false, 'an open dialog');
        """)

    def test_overlay_registers_its_key_listener_once(self) -> None:
        source = Path("ui_prototype/app.jsx").read_text(encoding="utf-8")
        start = source.index("function BoardSplitOverlay(")
        overlay = source[start:source.index("function HomeDashboard(", start)]
        self.assertIn("callbacksRef.current = { onConfirm, onCancel };", overlay)
        self.assertIn("if (!boardSplitKeyBelongsToOverlay(event, overlay)) return;", overlay)
        self.assertNotIn("}, [onCancel, onConfirm]);", overlay)


class BoardSplitPositionTest(unittest.TestCase):
    """Issue 2: the cut lands where the board preview line was."""

    def _page(self, root: Path) -> Path:
        page = Image.new("RGB", (400, 1000), "white")
        draw = ImageDraw.Draw(page)
        for top in (300, 330, 600, 630):  # two text blocks, gap 340..600
            draw.rectangle((60, top, 340, top + 12), fill="black")
        path = root / "page.png"
        page.save(path)
        return path

    def test_recrop_reports_where_the_trimmed_padded_crop_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = Image.open(self._page(root)).convert("RGB")
            offset: dict[str, int] = {}
            board.recrop_problem(page, board.Box(left=0, top=200, width=400, height=300), root / "c.png",
                                 source_offset_output=offset)
            crop = Image.open(root / "c.png").convert("L")
        first_ink_row = next(y for y in range(crop.height) if any(crop.getpixel((x, y)) < 128 for x in range(crop.width)))
        self.assertEqual(300, offset["top"] + first_ink_row)

    def test_a_split_child_can_be_split_again_on_its_preview_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = self._page(root)
            crop = root / "p1.png"
            Image.open(page_path).crop((0, 250, 400, 700)).save(crop)
            session = {
                "sessionDir": str(root),
                "pages": [{"id": "page-1", "sourceImagePath": page_path.resolve().as_uri()}],
                "problems": [{"id": "p1", "sourcePageId": "page-1", "imagePath": crop.resolve().as_uri(),
                              "bbox": {"left": 0, "top": 250, "width": 400, "height": 450}}],
            }
            with mock.patch.object(app_server, "_crop_dir_for_session", return_value=root / "crops"):
                split = app_server._mutate_split(session, "p1", (470 - 250) / 450)
            upper, lower = split["problems"]
            self.assertIn("cropSourceOffset", upper)
            self.assertIn("cropSourceOffset", lower)
            # Pick the blank band between the two lines of the upper child on
            # its own (trimmed + padded) crop, exactly as the board would.
            upper_crop = Image.open(app_server._resolve_session_path(upper["imagePath"]))
            crop_row = 321 - upper["cropSourceOffset"]["top"]
            ratio = app_server._split_ratio_from_crop_ratio(split, upper["id"], crop_row / upper_crop.height)
        bbox = upper["bbox"]
        self.assertAlmostEqual(321, bbox["top"] + ratio * bbox["height"], delta=0.6)

    def test_enhanced_crop_maps_through_its_first_generation_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = self._page(root)
            original = root / "orig.png"
            Image.new("RGB", (400, 300), "white").save(original)
            enhanced = root / "hq.png"
            Image.new("RGB", (800, 600), "white").save(enhanced)
            session = {
                "pages": [{"id": "page-1", "sourceImagePath": page_path.resolve().as_uri()}],
                "problems": [{"id": "p1", "sourcePageId": "page-1", "imagePath": enhanced.resolve().as_uri(),
                              "originalImagePath": original.resolve().as_uri(),
                              "cropSourceOffset": {"left": 0, "top": 214},
                              "bbox": {"left": 0, "top": 250, "width": 400, "height": 400}}],
            }
            ratio = app_server._split_ratio_from_crop_ratio(session, "p1", 0.5)
        # Middle of the 300px first-generation crop: page y = 214 + 150 = 364.
        self.assertAlmostEqual((364 - 250) / 400, ratio)

    def test_a_cut_that_touches_text_moves_into_the_nearest_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = self._page(root)
            crop = root / "p1.png"
            Image.open(page_path).crop((0, 250, 400, 700)).save(crop)
            session = {
                "pages": [{"id": "page-1", "sourceImagePath": page_path.resolve().as_uri()}],
                "problems": [{"id": "p1", "sourcePageId": "page-1", "imagePath": crop.resolve().as_uri(),
                              "bbox": {"left": 0, "top": 250, "width": 400, "height": 450}}],
            }
            # Row 340 is inside the second line (330..342); the nearest blank
            # band starts at 343 just below it. The cut goes into that band,
            # clear of the glyph edge (the search window is 9px here).
            ratio = app_server._split_ratio_from_crop_ratio(session, "p1", (340 - 250) / 450)
            cut = 250 + ratio * 450
            self.assertGreater(cut, 344)
            self.assertLessEqual(cut, 350)
            # A cut already in blank space is left exactly where it was.
            ratio = app_server._split_ratio_from_crop_ratio(session, "p1", (470 - 250) / 450)
            self.assertAlmostEqual(470, 250 + ratio * 450)


class SmartMoveEndInsertionTest(unittest.TestCase):
    """Issue 3: blank slots in 2/3-column layouts are not 'front' or 'end'."""

    def test_two_column_empty_slots_stay_free_moves(self) -> None:
        run_node(INSERT_SETUP + r"""
        // Order p1, p2 (row 1, two columns), p3 (row 2, left). p2 is tall,
        // so the last problem in order (p3) is not the lowest tile.
        const items = [{ id: 'drag' }, { id: 'p1' }, { id: 'p2' }, { id: 'p3' }];
        const positions = [pos(0, .5), pos(50, .5), pos(50, 3), pos(170, .5)];
        assert.equal(sandbox.smartBoardEndInsertionTarget(items, positions, 260, pageH, 'drag'), null,
          'below p3 but beside the tall p2 is an empty slot, not the end');
        assert.deepEqual(target(items, positions, 360, pageH, 'drag'), { id: 'p3', mode: 'reorder', position: 'after' });
        assert.deepEqual(target(items, positions, 20, pageH, 'drag'), { id: 'p1', mode: 'reorder', position: 'before' });
        """)

    def test_first_in_order_pushed_down_is_not_the_board_top(self) -> None:
        run_node(INSERT_SETUP + r"""
        // A legacy layout where p1 sits lower in its row than p2 beside it.
        const items = [{ id: 'p1' }, { id: 'p2' }, { id: 'drag' }];
        const positions = [pos(0, .4, 1.2, 1), pos(0, .4), pos(200, .4)];
        assert.equal(sandbox.smartBoardEndInsertionTarget(items, positions, 60, pageH, 'drag'), null,
          'above p1 but below the top of p2 is not the front');
        assert.deepEqual(target(items, positions, -5, pageH, 'drag'), { id: 'p1', mode: 'reorder', position: 'before' });
        assert.equal(sandbox.smartBoardEndInsertionTarget([{ id: 'drag' }], [pos(0, .4)], 500, pageH, 'drag'), null);
        """)


class ChalkCutoutCacheTest(unittest.TestCase):
    """Issue 4: re-exports reuse the background-removed figure."""

    def test_cached_cutout_is_identical_and_skips_the_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crop_path = Path(tmp) / "problem.png"
            image = Image.new("RGB", (240, 160), "white")
            ImageDraw.Draw(image).ellipse((40, 30, 200, 130), outline="black", width=4)
            image.save(crop_path)
            crop = Image.open(crop_path).convert("RGB")
            chalk = board._resolve_chalk_color(None)
            first = board._cached_chalk_cutout(crop_path, crop, chalk_color=chalk, text_priority=False)
            direct = board._extract_problem_cutout(board._enhance_problem_crop(crop), chalk_color=chalk)
            self.assertEqual(direct.tobytes(), first.tobytes())
            with mock.patch.object(board, "_extract_problem_cutout", side_effect=AssertionError("rebuilt")):
                second = board._cached_chalk_cutout(crop_path, crop, chalk_color=chalk, text_priority=False)
            self.assertEqual(first.tobytes(), second.tobytes())
            self.assertEqual(first.mode, second.mode)
            # A changed crop or another chalk tone never reuses the old cutout.
            ImageDraw.Draw(image).rectangle((10, 10, 30, 30), fill="black")
            image.save(crop_path)
            changed = Image.open(crop_path).convert("RGB")
            third = board._cached_chalk_cutout(crop_path, changed, chalk_color=chalk, text_priority=False)
            self.assertNotEqual(first.tobytes(), third.tobytes())
            cache_files = list((crop_path.parent / board.CHALK_CUTOUT_CACHE_DIRNAME).glob("*.png"))
            self.assertEqual(2, len(cache_files))


class WholePageGapTest(unittest.TestCase):
    """Issue 5: only "페이지 전체 넣기" pages stack with no gap."""

    def test_only_page_as_is_whole_pages_drop_the_gap(self) -> None:
        whole = board._is_whole_page_record
        self.assertTrue(whole(input_intent="page-as-is", force_full_page_bounds=True))
        self.assertFalse(whole(input_intent="single-problem", force_full_page_bounds=True))
        self.assertFalse(whole(input_intent="auto", force_full_page_bounds=True))
        self.assertFalse(whole(input_intent="page-as-is", force_full_page_bounds=False))
        self.assertFalse(whole(input_intent="page-as-is", force_full_page_bounds=True, fit_width_converted=True))
        entry = SimpleNamespace(input_intent="page-as-is", force_full_page_bounds=True, fit_width_converted=True)
        self.assertFalse(board._entry_is_whole_page(entry))

    def test_session_geometry_keeps_the_gap_for_fit_width_and_passages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "c.png"
            Image.new("RGB", (800, 1000), "white").save(crop)
            base = {"imagePath": crop.resolve().as_uri(), "inputIntent": "page-as-is", "forceFullPageBounds": True}
            problems = [
                {**base, "id": "page"},
                {**base, "id": "fit", "fitWidthConverted": True},
                {**base, "id": "passage", "passageRole": "passage_fragment"},
                {**base, "id": "single", "inputIntent": "single-problem"},
            ]
            session = {"cropFormat": board.CROP_FORMAT_V2, "problems": problems}
            board.annotate_session_image_record_geometry(session)
        gaps = {problem["id"]: problem["imageRecordGeometry"]["continuousGapPages"] for problem in problems}
        record_gap = board.CONTINUOUS_RECORD_GAP_PX / board.CANVAS_WIDTH
        self.assertEqual(0.0, gaps["page"])
        self.assertAlmostEqual(record_gap, gaps["fit"])
        self.assertAlmostEqual(record_gap, gaps["passage"])
        self.assertAlmostEqual(record_gap, gaps["single"])

    def test_placements_without_the_new_key_fall_back_to_page_as_is_intent(self) -> None:
        template = board.LayoutTemplate("t", fixed_left_zone_ratio=1 / 3)
        base = {"record_mode": "image-only", "placement_scale_ratio": 1.0, "rendered_width_px": 800.0,
                "rendered_height_px": 1000.0, "actual_content_height_pages": 1.0, "force_full_page_bounds": True}
        geometry = lambda placement: board._image_record_geometry_from_placement(
            placement, template, crop_format=board.CROP_FORMAT_V2)["continuousGapPages"]
        self.assertEqual(0.0, geometry({**base, "input_intent": "page-as-is"}))
        self.assertGreater(geometry({**base, "input_intent": "single-problem"}), 0.0)
        self.assertGreater(geometry({**base, "input_intent": "page-as-is", "whole_page_record": False}), 0.0)

    def test_session_entries_carry_the_fit_width_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "c.png"
            Image.new("RGB", (800, 400), "white").save(crop)
            base = {"imagePath": crop.resolve().as_uri(), "inputIntent": "page-as-is", "forceFullPageBounds": True,
                    "bbox": {"left": 0, "top": 0, "width": 800, "height": 400}}
            entries = app_server._problems_to_entries([
                {**base, "id": "page"},
                {**base, "id": "fit", "fitWidthConverted": True},
                {**base, "id": "passage", "metadata": {"passage_role": "passage_fragment"}},
            ])
        self.assertEqual([True, False, False], [board._entry_is_whole_page(entry) for entry in entries])

    def test_fit_width_marks_converted_problems_but_not_whole_pages(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const passage = sandbox.fitWidthContinuousPageItem({ id: 'a', inputIntent: 'auto', forceFullPageBounds: false });
        assert.equal(passage.fitWidthConverted, true);
        assert.equal(passage.forceFullPageBounds, true);
        const page = sandbox.fitWidthContinuousPageItem({ id: 'b', inputIntent: 'page-as-is', forceFullPageBounds: true });
        assert.equal(page.fitWidthConverted, false);
        const again = sandbox.fitWidthContinuousPageItem(passage);
        assert.equal(again.fitWidthConverted, true, 'a converted passage never becomes a whole page');
        const problem = sandbox.applyItemStateToProblem({ id: 'a' }, passage);
        assert.equal(problem.fitWidthConverted, true);
        assert.equal(problem.fit_width_converted, true);
        """)


if __name__ == "__main__":
    unittest.main()
