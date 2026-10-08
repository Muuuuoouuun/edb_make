from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


DRAG_SETUP = NODE_SETUP + r"""
const sandbox = placementSandbox();
const listeners = new Map(), saved = [], reorders = [];
const frames = new Map();
let frameId = 0;
const requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
const cancelAnimationFrame = id => frames.delete(id);
const flush = () => { const queued = Array.from(frames.values()); frames.clear(); queued.forEach(callback => callback(16)); };
const tile = {
  style: {},
  getBoundingClientRect: () => ({ width: 200, height: 160 }),
};
const handle = {
  getBoundingClientRect: () => ({ width: 180, height: 22 }),
  setPointerCapture() {}, releasePointerCapture() {},
  addEventListener() {}, removeEventListener() {},
};
Object.assign(sandbox, {
  useEffect() {}, tileRefs: { current: { p1: tile } },
  contentRef: { current: { style: { height: '1200px' }, clientWidth: 800, getBoundingClientRect: () => ({ width: 800 }) } },
  scrollRef: { current: { scrollTop: 0, clientHeight: 600, getBoundingClientRect: () => ({ left: 0, right: 800, top: 0, bottom: 600 }) } },
  positionDragRef: { current: null }, positionDragFrameRef: { current: null }, syncLock: { current: 0 },
  suppressClickRef: { current: null }, boardDropTargetRef: { current: null },
  pageH: 200, contentW: 800, columnCount: 1,
  items: [{ id: 'p1', heightFrac: 0.8, startYPages: 0 }, { id: 'p2', heightFrac: 0.8, startYPages: 1.2 }], boardOrderSignature: 'p1|p2',
  setPositioningId() {}, setCurrentBoardDropTarget() {}, setActive() {}, setDragMagnet() {},
  cancelSmoothScroll() {}, applyBoardAutoScroll() {}, updateBoardDropTarget() {}, stopBoardAutoScroll() {},
  captureBoardTileRects() {}, findBoardDropTarget: () => sandbox.dropTarget || null,
  reorderItemsForDrop: require('./ui_prototype/reorder.js').reorderItemsForDrop,
  reorder: (...args) => reorders.push(args),
  setPlacement: (id, patch) => saved.push({ id, patch }),
  window: {
    addEventListener: (type, listener) => listeners.set(type, listener),
    removeEventListener: (type, listener) => { if (listeners.get(type) === listener) listeners.delete(type); },
    setTimeout() {}, requestAnimationFrame, cancelAnimationFrame,
  },
});
sandbox.layout = { items: sandbox.items, totalH: 1200 };
vm.runInContext(section('  const updatePositionDragVisual =', '  const stepBoardAutoScroll =')
  + section('  const removePositionDragWindowListeners =', '  let processedCount = 0;')
  + '\nglobalThis.begin = beginPositionDrag; globalThis.move = movePositionDrag;'
  + '\nglobalThis.end = endPositionDrag; globalThis.cancel = cancelPositionDrag;', sandbox);
const event = (x, y, altKey = false) => ({ button: 0, pointerId: 1, currentTarget: handle, clientX: x, clientY: y, altKey, preventDefault() {} });
const placement = { top: 0, startPages: 0, snappedNext: 1.2, xRatio: 0, yRatio: 0, columnCount: 1 };
"""


class TestUiBoardDrag(unittest.TestCase):
    def test_drag_uses_card_dimensions_and_moves_the_whole_card(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        const drag = sandbox.positionDragRef.current;
        assert.equal(drag.tile, tile, 'the drag handle must resolve to the whole problem card');
        assert.equal(drag.maxLeft, 600);
        assert.equal(drag.maxTopOffset, 80, 'vertical space uses the full problem height');
        sandbox.move(event(320, 30));
        flush();
        assert.equal(tile.style.transform, 'translate3d(300px, 10px, 0)');
        assert.equal(handle.style, undefined, 'never translate the label independently');
        sandbox.end(event(320, 30));
        assert.equal(saved.length, 1);
        assert.equal(saved[0].id, 'p1');
        assert.equal(saved[0].patch.xRatio, 0.5);
        assert.ok(Math.abs(saved[0].patch.yRatio - 0.125) < 1e-8);
        assert.equal(tile.style.transform, '');
        assert.equal(listeners.size, 0);
        assert.equal(sandbox.positionDragRef.current, null);
        """)

    def test_click_or_cancel_does_not_save_a_placement(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(22, 21));
        sandbox.end(event(22, 21));
        assert.equal(saved.length, 0);
        assert.equal(reorders.length, 0);
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(320, 30));
        sandbox.cancel(event(320, 30));
        assert.equal(saved.length, 0);
        assert.equal(tile.style.transform, '');
        assert.equal(listeners.size, 0);
        assert.equal(sandbox.positionDragRef.current, null);
        """)

    def test_vertical_drop_reorders_and_does_not_also_save_position(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.dropTarget = { id: 'p2', position: 'after' };
        sandbox.begin(event(20, 20, true), { id: 'p1' }, placement);
        sandbox.move(event(20, 120));
        sandbox.end(event(20, 120));
        assert.equal(reorders.length, 1);
        assert.equal(reorders[0][0], 'p1');
        assert.equal(reorders[0][1], 'p2');
        assert.equal(reorders[0][2], 'after');
        assert.equal(saved.length, 0);
        assert.equal(listeners.size, 0);
        """)


if __name__ == '__main__':
    unittest.main()
