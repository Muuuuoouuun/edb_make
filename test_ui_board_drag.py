from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


DRAG_SETUP = NODE_SETUP + r"""
const sandbox = placementSandbox();
const listeners = new Map(), saved = [], reorders = [], gaps = [], effects = [];
const frames = new Map();
let frameId = 0;
const requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
const cancelAnimationFrame = id => frames.delete(id);
const flush = () => { const queued = Array.from(frames.values()); frames.clear(); queued.forEach(callback => callback(16)); };
const tile = {
  style: {},
  getBoundingClientRect: () => ({ left: 0, right: 200, top: 0, bottom: 160, width: 200, height: 160 }),
};
const handle = {
  getBoundingClientRect: () => ({ width: 180, height: 22 }),
  setPointerCapture() {}, releasePointerCapture() {},
  addEventListener() {}, removeEventListener() {},
};
Object.assign(sandbox, {
  useEffect(effect, deps) { effects.push({ effect, deps }); }, smartMove: false, interactionBusy: false,
  tileRefs: { current: { p1: tile } },
  contentRef: { current: { style: { height: '1200px' }, clientWidth: 800, getBoundingClientRect: () => ({ width: 800 }) } },
  scrollRef: { current: { scrollTop: 0, clientHeight: 600, getBoundingClientRect: () => ({ left: 0, right: 800, top: 0, bottom: 600 }) } },
  positionDragRef: { current: null }, positionDragFrameRef: { current: null }, syncLock: { current: 0 },
  syncLockOriginIdRef: { current: null }, boardSelectionIntentRef: { current: null },
  suppressClickRef: { current: null }, boardDropTargetRef: { current: null },
  pageH: 200, contentW: 800, columnCount: 1,
  items: [{ id: 'p1', heightFrac: 0.8, startYPages: 0 }, { id: 'p2', heightFrac: 0.8, startYPages: 1.2 }], boardOrderSignature: 'p1|p2',
  setPositioningId() {}, setCurrentBoardDropTarget() {}, setActive() {}, setDragMagnet() {},
  cancelSmoothScroll() {}, applyBoardAutoScroll() {}, updateBoardDropTarget() {}, stopBoardAutoScroll() {},
  captureBoardTileRects() {}, findBoardDropTarget: () => sandbox.dropTarget || null,
  findSmartBoardDropTarget: () => sandbox.smartDropTarget || null,
  reorderItemsForDrop: require('./ui_prototype/reorder.js').reorderItemsForDrop,
  reorder: (...args) => reorders.push(args),
  reducePlacementGap: id => gaps.push(id),
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


SCROLL_SELECTION_SETUP = NODE_SETUP + r"""
const sandbox = placementSandbox();
const selectionEffects = [], scrollCalls = [], selectionCalls = [];
let now = 10000;
const container = { scrollTop: 0 };
Object.assign(sandbox, {
  activeId: 'p1', previewFitActive: false, pageH: 400, columnCount: 1,
  boardOrderSignature: 'p1|p2|p3', syncLock: { current: 0 },
  syncLockOriginIdRef: { current: null }, boardSelectionIntentRef: { current: null }, restoredScrollRef: { current: false },
  scrollRef: { current: container }, boardScrollTopRef: { current: 0 },
  positionDragRef: { current: null }, suppressClickRef: { current: null },
  selectionAnchorRef: { current: null }, selectedIds: new Set(),
  items: [{ id: 'p1' }, { id: 'p2' }, { id: 'p3' }],
  previewEstimate: { classinPageCount: 6 },
  layout: { positions: [
    { top: 0, yRatio: 0, rowHeightPages: 1.2, renderedHeightPages: .8 },
    { top: 1000, yRatio: 0, rowHeightPages: 1.2, renderedHeightPages: .8 },
    { top: 2000, yRatio: .5, rowHeightPages: 1.2, renderedHeightPages: .8 },
  ] },
  Date: { now: () => now }, useEffect: effect => selectionEffects.push(effect),
  setCurrentPage() {}, setSelectedIds() {},
  setActive: id => selectionCalls.push(id),
  smoothScrollTo: (target, top) => { scrollCalls.push(top); target.scrollTop = top; },
  nearestPlacementIndex: () => sandbox.nearestIndex ?? 0,
  applySelectionClick: require('./ui_prototype/reorder.js').applySelectionClick,
});
vm.runInContext(
  'globalThis.restoreScroll = () => {\n'
    + section('    if (restoredScrollRef.current || !scrollRef.current) return;', '  }, [previewEstimate.classinPageCount, pageH]);')
    + '\n};\n'
    + section('  // when activeId changes externally, scroll the board to it', '  const onScroll =')
    + section('  const onTileClick =', '  useLayoutEffect(() => {\n    boardTileHandlersRef.current =')
    + '\nglobalThis.syncScroll = syncBoardScrollState; globalThis.tileClick = onTileClick;', sandbox);
const followActiveSelection = selectionEffects[0];
"""


class TestUiBoardDrag(unittest.TestCase):
    def test_pending_scroll_event_cannot_replace_a_tile_click_selection(self) -> None:
        run_node(SCROLL_SELECTION_SETUP + r"""
        sandbox.tileClick('p2');sandbox.activeId='p2';sandbox.nearestIndex=0;
        sandbox.syncScroll();
        assert.deepEqual(selectionCalls,['p2'],'pending scroll cannot undo the explicit click');
        sandbox.boardSelectionIntentRef.current=null;
        sandbox.syncScroll();
        assert.deepEqual(selectionCalls,['p2','p1'],'direct manual scrolling resumes selection sync');
        """)

    def test_external_rail_selection_navigates_during_initial_scroll_restore_lock(self) -> None:
        run_node(SCROLL_SELECTION_SETUP + r"""
        sandbox.restoreScroll();
        assert.equal(sandbox.syncLockOriginIdRef.current, 'p1');
        followActiveSelection();
        assert.equal(scrollCalls.length, 0, 'initial selection must keep the restored viewport');
        now += 40;
        sandbox.activeId = 'p3';
        followActiveSelection();
        assert.equal(scrollCalls.length, 1, 'an external selection cannot be swallowed by the restore lock');
        assert.ok(Math.abs(container.scrollTop - 2072) < 1e-8, 'navigation includes the selected card Y offset');
        assert.equal(sandbox.syncLockOriginIdRef.current, null);
        sandbox.activeId = 'p1';
        now += 40;
        followActiveSelection();
        assert.equal(scrollCalls.length, 2, 'rapid navigation back to the old origin must also work');
        assert.equal(container.scrollTop, 0);
        """)

    def test_local_scroll_selection_does_not_snap_but_external_selection_still_navigates(self) -> None:
        run_node(SCROLL_SELECTION_SETUP + r"""
        container.scrollTop = 940;
        sandbox.nearestIndex = 1;
        sandbox.syncScroll();
        assert.deepEqual(selectionCalls, ['p2']);
        assert.equal(sandbox.syncLockOriginIdRef.current, 'p2');
        sandbox.activeId = 'p2';
        now += 30;
        followActiveSelection();
        assert.equal(scrollCalls.length, 0, 'scroll-generated selection cannot snap the viewport');
        assert.equal(container.scrollTop, 940);
        sandbox.activeId = 'p3';
        now += 30;
        followActiveSelection();
        assert.equal(scrollCalls.length, 1, 'a distinct rail selection must override the local scroll lock');
        """)

    def test_local_tile_click_lock_tracks_only_its_own_selection(self) -> None:
        run_node(SCROLL_SELECTION_SETUP + r"""
        sandbox.tileClick('p2');
        assert.deepEqual(selectionCalls, ['p2']);
        assert.equal(sandbox.syncLockOriginIdRef.current, 'p2');
        sandbox.activeId = 'p2';
        followActiveSelection();
        assert.equal(scrollCalls.length, 0);
        sandbox.activeId = 'p3';
        followActiveSelection();
        assert.equal(scrollCalls.length, 1);
        // Once the short lock expires, layout changes still align the same item.
        sandbox.syncLockOriginIdRef.current = 'p3';
        sandbox.syncLock.current = now;
        container.scrollTop = 0;
        now += 451;
        followActiveSelection();
        assert.equal(scrollCalls.length, 2);
        """)

    def test_drag_uses_card_dimensions_and_moves_the_whole_card(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        const drag = sandbox.positionDragRef.current;
        assert.equal(drag.tile, tile, 'the drag handle must resolve to the whole problem card');
        assert.equal(sandbox.syncLockOriginIdRef.current, 'p1');
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

    def test_smart_overlap_reorders_without_saving_free_position(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.smartMove = true;
        sandbox.smartDropTarget = { id: 'p2', mode: 'reorder', position: 'after' };
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(20, 320));
        flush();
        assert.equal(tile.style.transform, 'translate3d(0px, 300px, 0)');
        assert.equal(sandbox.contentRef.current.style.height, '1200px');
        sandbox.end(event(20, 320));
        assert.equal(reorders.length, 1);
        assert.equal(reorders[0][1], 'p2');
        assert.equal(reorders[0][2], 'after');
        assert.equal(saved.length, 0);
        assert.equal(gaps.length, 0);
        assert.equal(listeners.size, 0);
        """)

    def test_smart_side_drop_pulls_gap_only_and_zero_gap_is_noop(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.smartMove = true;
        for (const gap of [0.4, 0]) {
          sandbox.smartDropTarget = { id: 'p2', mode: 'gap', position: 'gap', removableGapPages: gap };
          sandbox.begin(event(20, 20), { id: 'p1' }, placement);
          sandbox.move(event(240, 300));
          sandbox.end(event(240, 300));
        }
        assert.deepEqual(gaps, ['p2']);
        assert.equal(reorders.length, 0);
        assert.equal(saved.length, 0, 'zero-gap side drops must never fall through to placement changes');
        assert.equal(listeners.size, 0);
        """)

    def test_smart_noop_reorder_does_not_move_the_card(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.smartMove = true;
        sandbox.smartDropTarget = { id: 'p2', mode: 'reorder', position: 'before' };
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(20, 250));
        sandbox.end(event(20, 250));
        assert.equal(reorders.length, 0, 'p1 is already before p2');
        assert.equal(saved.length, 0);
        assert.equal(gaps.length, 0);
        """)

    def test_smart_toggle_off_and_alt_modifier_preserve_legacy_drag(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.smartDropTarget = { id: 'p2', mode: 'gap', position: 'gap', removableGapPages: 0.4 };
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(240, 30));
        sandbox.end(event(240, 30));
        assert.equal(saved.length, 1);
        assert.equal(gaps.length, 0);
        sandbox.smartMove = true;
        sandbox.dropTarget = { id: 'p2', position: 'after' };
        sandbox.begin(event(20, 20, true), { id: 'p1' }, placement);
        assert.equal(sandbox.positionDragRef.current.smartMode, false);
        sandbox.move(event(20, 250));
        sandbox.end(event(20, 250));
        assert.equal(reorders.length, 1);
        assert.equal(saved.length, 1);
        assert.equal(gaps.length, 0);
        """)

    def test_smart_click_escape_and_outside_drop_never_mutate(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.smartMove = true;
        sandbox.smartDropTarget = { id: 'p2', mode: 'gap', position: 'gap', removableGapPages: 0.4 };
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(22, 21));
        sandbox.end(event(22, 21));
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(240, 300));
        listeners.get('keydown')({ key: 'Escape', preventDefault() {} });
        assert.equal(sandbox.positionDragRef.current, null);
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(240, 300));
        sandbox.end(event(801, 300));
        assert.equal(gaps.length, 0);
        assert.equal(saved.length, 0);
        assert.equal(reorders.length, 0);
        assert.equal(listeners.size, 0);
        assert.equal(tile.style.transform, '');
        """)

    def test_busy_guards_and_pointer_identity_block_accidental_mutations(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.interactionBusy = true;
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        assert.equal(sandbox.positionDragRef.current, null);
        assert.equal(listeners.size, 0);
        sandbox.interactionBusy = false;
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move({ ...event(240, 300), pointerId: 2 });
        assert.equal(sandbox.positionDragRef.current.moved, false);
        sandbox.move(event(240, 300));
        sandbox.interactionBusy = true;
        sandbox.end(event(240, 300));
        assert.equal(saved.length, 0);
        assert.equal(reorders.length, 0);
        assert.equal(gaps.length, 0);
        assert.equal(listeners.size, 0);
        """)

    def test_smart_drag_visual_accounts_for_scroll_and_cancel_restores_it(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.smartMove = true;
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.scrollRef.current.scrollTop = 80;
        sandbox.move(event(20, 60));
        flush();
        assert.equal(sandbox.positionDragRef.current.contentDy, 120);
        assert.equal(tile.style.transform, 'translate3d(0px, 120px, 0)');
        sandbox.cancel(event(20, 60));
        assert.equal(sandbox.scrollRef.current.scrollTop, 0);
        assert.equal(saved.length, 0);
        """)

    def test_smart_hit_testing_prefers_overlap_then_nearby_side_gutter(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const rect = (left, top, width = 200, height = 160) => ({
          left, top, width, height, right: left + width, bottom: top + height,
        });
        const candidates = [{ id: 'p1', rect: rect(0, 0) }, { id: 'p2', rect: rect(0, 240) }, { id: 'p3', rect: rect(230, 240) }];
        const hit = (x, y) => sandbox.resolveSmartBoardDropTarget(candidates, x, y, 'p1');
        assert.equal(hit(20, 260).mode, 'reorder');
        assert.equal(hit(20, 260).position, 'before');
        assert.equal(hit(20, 360).position, 'after');
        assert.equal(hit(240, 300).id, 'p3', 'overlap must beat a neighbouring gutter');
        assert.equal(hit(210, 300).id, 'p2');
        assert.equal(hit(210, 300).mode, 'gap');
        assert.equal(hit(-20, 300).mode, 'gap');
        assert.equal(hit(20, 20), null, 'source footprint is not an overlapping destination');
        assert.equal(hit(600, 300), null, 'distant blank space is a placement move');
        assert.equal(hit(210, 200), null, 'gutter must be level with a card');
        assert.equal(hit(NaN, 300), null);
        assert.equal(sandbox.resolveSmartBoardDropTarget([{ id: 'absent' }], 20, 20, 'p1'), null);
        """)

    def test_smart_source_hitbox_is_stationary_after_ghost_move_and_autoscroll(self) -> None:
        run_node(DRAG_SETUP + r"""
        vm.runInContext(section('  const findSmartBoardDropTarget =', '  const updateBoardDropTarget =')
          + '\nglobalThis.actualFindSmartTarget = findSmartBoardDropTarget;', sandbox);
        sandbox.layout.items = sandbox.reflowItemsForBoardOrder(sandbox.items, 1.2, 1, 'classin-grid');
        sandbox.tileRefs.current.p2 = { getBoundingClientRect: () => ({ left: 0, right: 200, top: 240, bottom: 400, width: 200, height: 160 }) };
        sandbox.smartMove = true;
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        const drag = sandbox.positionDragRef.current;
        tile.getBoundingClientRect = () => ({ left: 220, right: 420, top: 300, bottom: 460, width: 200, height: 160 });
        sandbox.scrollRef.current.scrollTop = 80;
        assert.equal(sandbox.actualFindSmartTarget(20, 40, drag), null);
        const target = sandbox.actualFindSmartTarget(210, 40, drag);
        assert.equal(target.id, 'p1');
        assert.equal(target.mode, 'gap');
        assert.ok(target.removableGapPages > 0);
        sandbox.cancel(event(20, 20));
        """)

    def test_actual_smart_finder_reorders_third_after_fourth_only_inside_lower_half(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.items = [1, 2, 3, 4].map(number => ({ id: `p${number}`, heightFrac: .8 }));
        sandbox.boardOrderSignature = 'p1|p2|p3|p4';
        sandbox.layout.items = sandbox.reflowItemsForBoardOrder(sandbox.items, 1.2, 1, 'classin-grid');
        sandbox.scrollRef.current.scrollTop = 380;
        sandbox.items.forEach((item, index) => {
          sandbox.tileRefs.current[item.id] = {
            style: {}, getBoundingClientRect: () => {
              const top = index * 240 - sandbox.scrollRef.current.scrollTop;
              return { left: 0, right: 200, top, bottom: top + 160, width: 200, height: 160 };
            },
          };
        });
        vm.runInContext(section('  const findSmartBoardDropTarget =', '  const updateBoardDropTarget ='), sandbox);
        sandbox.smartMove = true;
        const thirdPlacement = { top: 480, startPages: 2.4, snappedNext: 3.6, xRatio: 0, yRatio: 0, columnCount: 1 };

        // Card 4 spans y=340..500; its upper half leaves 3 immediately before 4.
        sandbox.begin(event(100, 180), { id: 'p3' }, thirdPlacement);
        sandbox.move(event(100, 360));
        sandbox.end(event(100, 360));
        assert.equal(reorders.length, 0);
        assert.equal(saved.length, 0);

        // The lower half, including the centre line, means AFTER card 4.
        for (const y of [420, 460, 500]) {
          const priorCount = reorders.length;
          sandbox.begin(event(100, 180), { id: 'p3' }, thirdPlacement);
          sandbox.move(event(100, y));
          sandbox.end(event(100, y));
          assert.equal(reorders.length, priorCount + 1, `expected reorder at y=${y}`);
          const command = reorders[reorders.length - 1];
          assert.equal(command.slice(0, 3).join('|'), 'p3|p4|after');
          assert.equal(sandbox.reorderItemsForDrop(sandbox.items, ...command.slice(0, 3))
            .map(item => item.id).join('|'), 'p1|p2|p4|p3');
        }
        assert.equal(saved.length, 0);

        // Beyond the CARD but inside the viewport is free placement, not reorder.
        sandbox.begin(event(100, 180), { id: 'p3' }, thirdPlacement);
        sandbox.move(event(100, 510));
        sandbox.end(event(100, 510));
        assert.equal(reorders.length, 3);
        assert.equal(saved.length, 1);

        // Beyond the VIEWPORT is cancellation, even if a card extends below it.
        sandbox.begin(event(100, 180), { id: 'p3' }, thirdPlacement);
        sandbox.move(event(100, 601));
        sandbox.end(event(100, 601));
        assert.equal(reorders.length, 3);
        assert.equal(saved.length, 1);
        assert.equal(listeners.size, 0);
        """)

    def test_actual_smart_finder_uses_target_rect_after_autoscroll(self) -> None:
        run_node(DRAG_SETUP + r"""
        sandbox.items = [1, 2, 3, 4].map(number => ({ id: `p${number}`, heightFrac: .8 }));
        sandbox.boardOrderSignature = 'p1|p2|p3|p4';
        sandbox.layout.items = sandbox.reflowItemsForBoardOrder(sandbox.items, 1.2, 1, 'classin-grid');
        sandbox.scrollRef.current.scrollTop = 380;
        sandbox.items.forEach((item, index) => {
          sandbox.tileRefs.current[item.id] = {
            style: {}, getBoundingClientRect: () => {
              const top = index * 240 - sandbox.scrollRef.current.scrollTop;
              return { left: 0, right: 200, top, bottom: top + 160, width: 200, height: 160 };
            },
          };
        });
        vm.runInContext(section('  const findSmartBoardDropTarget =', '  const updateBoardDropTarget ='), sandbox);
        sandbox.smartMove = true;
        sandbox.begin(event(100, 180), { id: 'p3' }, {
          top: 480, startPages: 2.4, snappedNext: 3.6, xRatio: 0, yRatio: 0, columnCount: 1,
        });
        sandbox.move(event(100, 360));
        sandbox.scrollRef.current.scrollTop = 480;
        // y=360 was the old upper half, but now lies inside the moved lower half.
        sandbox.end(event(100, 360));
        assert.equal(reorders.length, 1);
        assert.equal(reorders[0].slice(0, 3).join('|'), 'p3|p4|after');
        assert.equal(saved.length, 0);
        """)

    def test_background_item_or_busy_change_cancels_captured_pointer_handlers(self) -> None:
        run_node(DRAG_SETUP + r"""
        const staleGuard = effects.find(({ deps }) => deps?.length === 2 && deps[1] === sandbox.items);
        assert.ok(staleGuard, 'items/busy changes must invalidate the drag closure');
        sandbox.smartMove = true;
        sandbox.smartDropTarget = { id: 'p2', mode: 'reorder', position: 'after' };
        sandbox.begin(event(20, 20), { id: 'p1' }, placement);
        sandbox.move(event(20, 300));
        const staleEnd = listeners.get('pointerup');
        sandbox.items = [...sandbox.items, { id: 'new-recognition-result', heightFrac: .8 }];
        staleGuard.effect();
        assert.equal(sandbox.positionDragRef.current, null);
        assert.equal(listeners.size, 0);
        staleEnd(event(20, 300));
        assert.equal(reorders.length, 0);
        assert.equal(saved.length, 0);
        assert.equal(gaps.length, 0);
        assert.equal(sandbox.items.length, 3, 'new results must survive an obsolete pointerup');
        """)

    def test_gap_reduction_pulls_following_rows_up_and_restores_without_reordering(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const reflow = items => sandbox.reflowItemsForBoardOrder(items, 1.2, 1, 'classin-grid');
        const before = reflow([{ id: 'p1', heightFrac: .8 }, { id: 'p2', heightFrac: .7 }, { id: 'p3', heightFrac: .6 }]
          .map(item => sandbox.applyPlacementPatchToItem(item, { gapAfterPages: null })));
        const action = sandbox.derivePlacementGapActionFromLayout(before, 'p1');
        const after = reflow(before.map(item => {
          const update = action.applyUpdates?.find(update => update.id === item.id);
          return update ? sandbox.applyPlacementPatchToItem(item, update.patch) : item;
        }));
        assert.equal(after.map(item => item.id).join('|'), 'p1|p2|p3');
        assert.equal(after[0].startYPages, before[0].startYPages);
        assert.ok(Math.abs(after[1].startYPages - .8) < 1e-8);
        assert.ok(Math.abs(before[1].startYPages - after[1].startYPages - action.removableGapPages) < 1e-8);
        const restored = reflow(after.map(item => {
          const update = action.restoreUpdates.find(update => update.id === item.id);
          return update ? sandbox.applyPlacementPatchToItem(item, update.patch) : item;
        }));
        assert.equal(JSON.stringify(restored), JSON.stringify(before));
        """)

    def test_gap_action_reports_only_gap_that_will_really_be_removed(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const reflow = items => sandbox.reflowItemsForBoardOrder(items, 1.2, 1, 'classin-grid');
        for (const gapAfterPages of [null, 0, .3]) {
          const target = sandbox.applyPlacementPatchToItem({ id: 'p1', heightFrac: .8 }, { extraSlots: 1, gapAfterPages });
          const before = reflow([target, { id: 'p2', heightFrac: .7 }]);
          const action = sandbox.derivePlacementGapActionFromLayout(before, 'p1');
          const after = reflow(before.map(item => {
            const update = action.applyUpdates?.find(update => update.id === item.id);
            return update ? sandbox.applyPlacementPatchToItem(item, update.patch) : item;
          }));
          const actualReduction = before[1].startYPages - after[1].startYPages;
          assert.ok(Math.abs(action.removableGapPages - actualReduction) < 1e-8,
            `advertised ${action.removableGapPages} but actually removed ${actualReduction}; original gap=${gapAfterPages}`);
          if (actualReduction < 1e-8) assert.equal(action.ids.length, 0, 'already compact rows must not offer a no-op pull action');
          assert.equal(after[0].placementExtraSlots, before[0].placementExtraSlots, 'explicit reserved rows remain intact');
        }
        """)

    def test_gap_pull_preserves_half_and_bottom_aligned_problem_visual_position(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const reflow = items => sandbox.reflowItemsForBoardOrder(items, 1.2, 1, 'classin-grid');
        const apply = (items, updates = []) => reflow(items.map(item => {
          const update = updates.find(update => update.id === item.id);
          return update ? sandbox.applyPlacementPatchToItem(item, update.patch) : item;
        }));
        const stable = items => JSON.stringify(items.map(item => ({
          id: item.id, x: item.placementXRatio, scale: item.placementScaleRatio,
          xEdited: item.placementXEdited, extraSlots: item.placementExtraSlots,
        })));
        for (const yRatio of [.5, 1]) {
          const before = reflow([
            sandbox.applyPlacementPatchToItem({ id: 'p1', heightFrac: 1, placementAutoFitDisabled: true },
              { yRatio, scaleRatio: .8, xRatio: .3, gapAfterPages: null }),
            sandbox.applyPlacementPatchToItem({ id: 'p2', heightFrac: .7, placementAutoFitDisabled: true },
              { yRatio: 0, scaleRatio: 1, xRatio: .1, gapAfterPages: null }),
          ]);
          const action = sandbox.derivePlacementGapActionFromLayout(before, 'p1');
          const after = apply(before, action.applyUpdates);
          const oldBounds = sandbox.boardItemVisualBounds(before[0]);
          const newBounds = sandbox.boardItemVisualBounds(after[0]);
          assert.ok(Math.abs(newBounds.start - oldBounds.start) < 1e-8, `target moved with yRatio=${yRatio}`);
          assert.ok(Math.abs(newBounds.bottom - oldBounds.bottom) < 1e-8);
          assert.ok(Math.abs(before[1].startYPages - after[1].startYPages - action.removableGapPages) < 1e-8);
          assert.equal(stable(after), stable(before), 'order, horizontal placement and scale must not change');
          if (yRatio === .5) {
            assert.ok(Math.abs(action.removableGapPages - .2) < 1e-8);
            assert.equal(after[0].placementYRatio, 1, 'ratio adjusts to preserve the original visual offset');
          } else {
            assert.equal(action.ids.length, 0, 'a bottom-aligned problem has no trailing gap to remove');
            assert.equal(action.removableGapPages, 0);
          }
          const restored = apply(after, action.restoreUpdates);
          assert.equal(JSON.stringify(restored), JSON.stringify(before), 'undo restores both the gap and old Y ratio');
        }
        """)

    def test_gap_pull_preserves_unequal_multicolumn_row_geometry_and_undo(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const reflow = items => sandbox.reflowItemsForBoardOrder(items, 1.2, 2, 'classin-grid');
        const apply = (items, updates = []) => reflow(items.map(item => {
          const update = updates.find(update => update.id === item.id);
          return update ? sandbox.applyPlacementPatchToItem(item, update.patch) : item;
        }));
        const before = reflow([
          ['p1', 1, .8, 0, .2],
          ['p2', .5, .8, .5, .8],
          ['p3', .7, 1, .2, 0],
          ['p4', .6, 1, .3, 1],
        ].map(([id, heightFrac, scaleRatio, yRatio, xRatio]) =>
          sandbox.applyPlacementPatchToItem({ id, heightFrac, placementAutoFitDisabled: true },
            { scaleRatio, yRatio, xRatio, gapAfterPages: null })));
        const action = sandbox.derivePlacementGapActionFromLayout(before, 'p2');
        assert.equal(Array.from(action.ids).join('|'), 'p1|p2', 'whole legacy row must stay coherent');
        const after = apply(before, action.applyUpdates);
        assert.equal(after.map(item => item.id).join('|'), 'p1|p2|p3|p4');
        assert.ok(Math.abs(action.removableGapPages - .4) < 1e-8);
        for (let index = 0; index < before.length; index += 1) {
          assert.equal(after[index].placementXRatio, before[index].placementXRatio);
          assert.equal(after[index].placementScaleRatio, before[index].placementScaleRatio);
          assert.equal(after[index].boardColumnIndex, before[index].boardColumnIndex);
          const oldBounds = sandbox.boardItemVisualBounds(before[index]);
          const newBounds = sandbox.boardItemVisualBounds(after[index]);
          const expectedDelta = index < 2 ? 0 : action.removableGapPages;
          assert.ok(Math.abs(oldBounds.start - newBounds.start - expectedDelta) < 1e-8);
          assert.ok(Math.abs(oldBounds.bottom - newBounds.bottom - expectedDelta) < 1e-8);
        }
        assert.equal(after[0].placementYRatio, 0);
        assert.ok(Math.abs(after[1].placementYRatio - 1) < 1e-8);
        const again = sandbox.derivePlacementGapActionFromLayout(after, 'p1');
        assert.equal(again.ids.length, 0, 'a second pull must not move the row or claim nonexistent whitespace');
        const restored = apply(after, action.restoreUpdates);
        assert.equal(JSON.stringify(restored), JSON.stringify(before));
        """)


if __name__ == '__main__':
    unittest.main()
