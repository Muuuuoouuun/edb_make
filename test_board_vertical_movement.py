"""Regression coverage for extended board movement and its export contract."""
from __future__ import annotations

import subprocess
import inspect
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import app_server
import build_problem_board_edb as board
from layout_template_schema import LayoutTemplate, ProblemLayoutInput
from placement_engine import normalize_placement_extra_slots, place_problems


ROOT = Path(__file__).resolve().parent


class TestBoardVerticalMovement(unittest.TestCase):
    def test_ui_drag_extension_limits_reflow_and_reload(self):
        subprocess.run(["node", "-e", r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
const sandbox = { normalizeInputIntent: value => value };
const names = ['reflowItemsForBoardOrder', 'verticalBoardDragPatch', 'applyPlacementPatchToItem', 'verticalPlacementRoomPages', 'deriveBoardPreviewEstimate'];
vm.runInNewContext(source.slice(source.indexOf('const FIXED_LEFT_ZONE_RATIO ='), source.indexOf('const INITIAL_ITEMS ='))
  + names.map(name => `\nglobalThis.${name} = ${name};`).join(''), sandbox);
const {reflowItemsForBoardOrder: reflow, verticalBoardDragPatch: drag, applyPlacementPatchToItem: patch, verticalPlacementRoomPages: room} = sandbox;
const offset = item => room(item) * (item.placementYRatio || 0);
const close = (a,b) => assert.ok(Math.abs(a-b) < 0.00001, `${a} != ${b}`);
for (const height of [0.4, 0.8, 1.2, 2.5, 5]) {
  for (const columns of [1, 2, 3]) {
    let items = reflow([{id:'a', heightFrac:height}, {id:'b', heightFrac:0.9}, {id:'c', heightFrac:0.8}, {id:'d', heightFrac:0.8}], 1.2, columns);
    const before = items[0];
    const move = drag(before, before.boardRowHeightPages, offset(before), 2.8);
    assert.ok(move.extraSlots > 0);
    items = reflow(items.map(item => item.id === 'a' ? patch(item, move) : item), 1.2, columns);
    close(offset(items[0]), 2.8);
    assert.deepEqual(Array.from(items, item => item.id), ['a','b','c','d']);
    for (const item of items) assert.ok(item.startYPages + offset(item) + item.heightFrac * (item.placementScaleRatio || 1) <= item.snappedNextStartYPages + 0.00001);
    const stable = JSON.stringify(items);
    for (let i=0; i<100; i++) items = reflow(JSON.parse(JSON.stringify(items)), 1.2, columns);
    assert.equal(JSON.stringify(items), stable, 'repeated reflow must not accumulate reserved height');
    const upward = drag(items[0], items[0].boardRowHeightPages, offset(items[0]), -100);
    const movedUp = reflow(items.map(item => item.id === 'a' ? patch(item, upward) : item), 1.2, columns);
    close(offset(movedUp[0]), 0);
    assert.equal(upward.extraSlots, move.extraSlots);
    const bounded = drag(items[0], items[0].boardRowHeightPages, offset(items[0]), 100000);
    assert.equal(bounded.extraSlots, 10);
    assert.ok(Number.isFinite(bounded.yRatio) && bounded.yRatio <= 1);
  }
}
const continuous = reflow([{id:'a', heightFrac:1.5, inputIntent:'page-as-is'}, {id:'b', heightFrac:1, inputIntent:'page-as-is'}]);
const extension = drag(continuous[0], continuous[0].boardRowHeightPages, 0, 2);
const extended = reflow([patch(continuous[0], extension), continuous[1]]);
close(offset(extended[0]), 2);
assert.ok(extended[1].startYPages >= extended[0].heightFrac + 2);
const guard = require('./ui_prototype/publish_guard.js');
for (const scale of [0.6, 1, 1.4, 1.6]) {
  for (const height of [0.4, 0.8, 1.2, 2.5]) {
    const input = reflow([{id:'before', heightFrac:0.8, inputIntent:'page-as-is'}, {id:'target', heightFrac:height, placementScaleRatio:scale}, {id:'after', heightFrac:0.8}]);
    const target = input[1];
    const movement = drag(target, target.boardRowHeightPages, offset(target), 1.9);
    const result = reflow(input.map(item => item.id === 'target' ? patch(item, movement) : item));
    close(offset(result[1]), 1.9);
  }
}
for (const intent of ['multi-problem', 'page-as-is']) {
  const input = [{id:'a', heightFrac:0.8, placementExtraSlots:2, placementYRatio:0.8, inputIntent:intent}, {id:'b', heightFrac:0.9, inputIntent:intent}];
  const ui = reflow(input);
  const validated = guard.simulatedBoardPlacements(ui);
  close(ui[1].startYPages, validated[1].startYPages);
  close(ui[0].startYPages + offset(ui[0]), validated[0].renderedTopYPages);
  assert.equal(guard.findBoardPlacementOverlaps(ui).length, 0);
  const estimate = sandbox.deriveBoardPreviewEstimate(ui, 'a');
  assert.equal(estimate.active.classinStartPage, Math.floor(validated[0].renderedTopYPages / 1.2) + 1);
  close(estimate.active.renderedBottomPages, validated[0].renderedBottomYPages);
}
"""], cwd=ROOT, check=True)

    def test_extra_space_coercion_is_bounded_and_aliases_work(self):
        for raw, expected in [(None, 0), (-5, 0), (1.5, 2), (999, 10), (float("nan"), 0), (float("inf"), 0), ("bad", 0)]:
            self.assertEqual(expected, normalize_placement_extra_slots(raw))
            self.assertEqual(expected, app_server._coerce_placement_extra_slots({"extraSlots": raw}))
        self.assertEqual(3, app_server._coerce_placement_extra_slots({"placement_extra_slots": 3}))
        self.assertEqual(3, normalize_placement_extra_slots({"placementExtraSlots": 3}))
        self.assertEqual(2, normalize_placement_extra_slots({"extraSlots": 2, "placementExtraSlots": 3}))

    def test_entry_preserves_legacy_positional_parameter_order(self):
        parameters = list(inspect.signature(board.ProblemEntry).parameters)
        y_index = parameters.index("placement_y_ratio")
        self.assertEqual("placement_scale_ratio", parameters[y_index + 1])
        self.assertEqual("placement_extra_slots", parameters[-1])

    def test_engine_extra_space_aliases_preserve_scaled_flow(self):
        template = LayoutTemplate(name="movement")
        for key in ["extraSlots", "placementExtraSlots", "placement_extra_slots"]:
            with self.subTest(key=key):
                placement = place_problems([
                    ProblemLayoutInput(problem_id="a", actual_content_height_pages=2,
                                       metadata={key: 1, "placementScaleRatio": 0.6,
                                                 "reserveScaledHeight": True}),
                ], template=template)[0]
                self.assertAlmostEqual(2.4, placement.snapped_next_start_y_pages)
                self.assertAlmostEqual(0, placement.overflow_amount_pages)

    def test_drag_event_lifecycle_batches_moves_and_cleans_up(self):
        subprocess.run(["node", "-e", r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
const stage = source.slice(source.indexOf('function BoardStage({'));
const callbacks = stage.slice(stage.indexOf('  const stopBoardAutoScroll ='), stage.indexOf('  let processedCount ='));
const sandbox = { normalizeInputIntent: value => value, assert };
vm.runInNewContext(source.slice(source.indexOf('const FIXED_LEFT_ZONE_RATIO ='), source.indexOf('const INITIAL_ITEMS =')) + `
const frames = new Map(), listeners = new Map(), captureListeners = new Map(), saved = [];
let frameId = 0, heightWrites = 0, captured = false, height = '1200px';
const requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
const cancelAnimationFrame = id => frames.delete(id);
const window = {requestAnimationFrame, cancelAnimationFrame, setTimeout: () => {},
  addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name)};
const useEffect = () => {}, cancelSmoothScroll = () => {}, captureBoardTileRects = () => {};
const setPositioningId = () => {}, setCurrentBoardDropTarget = () => {}, setActive = () => {}, setDragMagnet = () => {};
const updateBoardDropTarget = () => {}, findBoardDropTarget = () => null;
const setPlacement = (id, patch) => saved.push({id, ...patch});
const positionDragRef = {current:null}, positionDragFrameRef = {current:null}, autoScrollRef = {current:{}}, suppressClickRef = {current:null}, syncLock = {current:0};
const scroll = {scrollTop:0, scrollHeight:1200, clientHeight:400,
  getBoundingClientRect: () => ({left:100,right:1100,top:100,bottom:500})};
const scrollRef = {current:scroll};
const style = {get height() { return height; }, set height(value) { height = value; heightWrites++; }};
const contentRef = {current:{style,clientWidth:1000,getBoundingClientRect: () => ({width:1000})}};
const tile = {style:{},getBoundingClientRect: () => ({width:300,height:320})};
const tileRefs = {current:{a:tile}};
const captureTarget = {setPointerCapture: () => { captured = true; },hasPointerCapture: () => captured,
  releasePointerCapture: () => { captured = false; },
  addEventListener: (name, fn) => captureListeners.set(name, fn),removeEventListener: name => captureListeners.delete(name)};
const pageH = 400, contentW = 1000, columnCount = 1;
const items = [{id:'a',heightFrac:0.8,startYPages:0}], boardOrderSignature = 'a';
const layout = {items,totalH:1200};
const placement = {snappedNext:1.2,startPages:0,top:0,xRatio:0,yRatio:0,columnCount:1};
const event = (x=200,y=200) => ({button:0,pointerId:1,currentTarget:captureTarget,clientX:x,clientY:y,altKey:false,preventDefault:()=>{}});
const flush = () => { const queued = Array.from(frames); frames.clear(); queued.forEach(([,fn]) => fn(16)); };
` + callbacks + `
for (const cancellation of ['Escape', 'blur', 'pointercancel', 'lostpointercapture']) {
  beginPositionDrag(event(), items[0], placement);
  const writesBefore = heightWrites;
  for (let i=0;i<100;i++) movePositionDrag(event(200, 220+i));
  assert.equal(heightWrites - writesBefore, 1, 'extend the DOM height only once per gesture');
  assert.equal(frames.size, 1, 'pointer events must share one rendering frame');
  flush();
  assert.equal(positionDragRef.current.lastClientY, 319);
  assert.ok(tile.style.transform.includes('119px'));
  cancelPositionDrag({pointerId:99});
  assert.ok(positionDragRef.current, 'a different pointer cannot cancel this gesture');
  scroll.scrollTop = 123;
  if (cancellation === 'Escape') listeners.get('keydown')({key:'Escape',preventDefault:()=>{}});
  else if (cancellation === 'lostpointercapture') captureListeners.get(cancellation)();
  else listeners.get(cancellation)({pointerId:1});
  assert.equal(positionDragRef.current, null);
  assert.equal(scroll.scrollTop, 0);
  assert.equal(style.height, '1200px');
  assert.equal(tile.style.transform, '');
  assert.equal(listeners.size, 0);
  assert.equal(captureListeners.size, 0);
  assert.equal(captured, false);
  assert.equal(saved.length, 0);
  flush();
}
beginPositionDrag(event(), items[0], placement);
movePositionDrag(event(200,480));
endPositionDrag(event(200,480)); // pointer-up before the pending frame must use its final coordinates.
assert.equal(saved.length, 1);
assert.equal(saved[0].extraSlots, 1);
assert.ok(Math.abs(saved[0].yRatio * 1.6 - 0.7) < 1e-8);
assert.equal(listeners.size, 0);
flush();
items[0].placementExtraSlots = 10;
placement.snappedNext = 13.2;
beginPositionDrag(event(), items[0], placement);
assert.equal(positionDragRef.current.extendedContentHeight, '1200px', 'no phantom scrolling at the extension limit');
movePositionDrag(event(200,300));
endPositionDrag(event(1200,300));
assert.equal(saved.length, 1, 'dropping outside the board must cancel');
assert.equal(listeners.size, 0);
`, sandbox);
"""], cwd=ROOT, check=True)

    def test_engine_reserves_extra_space_without_counting_it_as_content_overflow(self):
        template = LayoutTemplate(name="movement")
        placements = place_problems([
            ProblemLayoutInput(problem_id="a", actual_content_height_pages=0.8, overflow_allowed=False,
                               metadata={"placement_extra_slots": 3}),
            ProblemLayoutInput(problem_id="b", actual_content_height_pages=0.8),
        ], template=template)
        self.assertAlmostEqual(4.8, placements[0].snapped_next_start_y_pages)
        self.assertEqual(placements[0].snapped_next_start_y_pages, placements[1].start_y_pages)
        self.assertFalse(placements[0].overflow_violation)

    def test_export_records_preserve_extended_y_position_and_next_item_clearance(self):
        for intent in ["multi-problem", "page-as-is"]:
            with self.subTest(intent=intent), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "problem.png"
                Image.new("RGB", (400, 500), "white").save(path)
                problems = [{
                    "id": f"p{index}", "title": str(index), "imagePath": path.as_uri(),
                    "boardRenderPath": path.as_uri(), "inputIntent": intent,
                    "placementExtraSlots": 3 if index == 1 else 0,
                    "placementYRatio": 0.9 if index == 1 else 0,
                    "placementScaleRatio": 1.0, "processingStep": "s1",
                    "bbox": {"left": 0, "top": 0, "width": 400, "height": 500},
                } for index in [1, 2]]
                entries = app_server._problems_to_entries(problems)
                self.assertEqual(3, entries[0].placement_extra_slots)
                records, placements = board.build_image_only_records(entries, LayoutTemplate(name="movement"), dark_board=False)
                self.assertEqual(2, len(placements))
                self.assertTrue(records)
                first, second = placements
                self.assertEqual(3, first["placement_extra_slots"])
                self.assertGreater(first["record_top_y_pages"], 2.0)
                self.assertLessEqual(first["record_bottom_y_pages"], second["record_top_y_pages"] + 0.00001)

    def test_drag_uses_full_tile_and_cancels_safely(self):
        source = (ROOT / "ui_prototype/app.jsx").read_text(encoding="utf-8")
        stage = source.split("function BoardStage({", 1)[1].split("// ─── RIGHT:", 1)[0]
        self.assertIn("const tile = tileRefs.current[item.id]", stage)
        self.assertIn("const tileRect = tile.getBoundingClientRect()", stage)
        self.assertNotIn("tile: evt.currentTarget", stage)
        self.assertIn("drag.reorderMode && drag.moved", stage)
        self.assertNotIn("|| boardDropTargetRef.current", stage)
        self.assertIn("window.addEventListener('blur', drag.windowBlur)", stage)
        self.assertIn("event.key === 'Escape'", stage)
        self.assertIn("hasPointerCapture", stage)
        self.assertIn("positionDragFrameRef.current = window.requestAnimationFrame", stage)
        self.assertIn("height !== drag.pageH", stage)
        self.assertIn("width !== drag.contentWidth", stage)


if __name__ == "__main__":
    unittest.main()
