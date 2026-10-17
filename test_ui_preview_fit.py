from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


FIT_SETUP = NODE_SETUP + r"""
const sandbox = placementSandbox();
const fixture = savedFixture(sandbox, [34.3, 0.8]);
const savedSignature = JSON.stringify(fixture);
Object.assign(sandbox, {
  items: fixture.items, layoutItems: fixture.items, activeId: 'p1', useMemo: fn => fn(),
  manualPreviewZoom: 1, previewFitActive: false,
  basePageH: sandbox.boardPreviewBasePageHeight(362, 26, 70),
  pageH: 266 / 1.2, boardOrderSignature: 'p1|p2', contentW: 800,
  scrollRef: { current: { scrollTop: 0 } }, tileRefs: { current: { p1: { offsetTop: 84 }, p2: { offsetTop: 168 } } },
  layout: { positions: [{ top: 10 }, { top: 20 }] },
  syncLock: { current: 0 }, syncLockOriginIdRef: { current: null }, boardSelectionIntentRef: { current: null }, previewFocusRequestRef: { current: null },
  setPreviewFitActive: value => { sandbox.previewFitActive = value; },
  setPreviewZoom: value => { sandbox.manualPreviewZoom = value; },
  smoothScrollTo: (container, top) => { container.scrollTop = top; },
  useLayoutEffect: effect => { sandbox.runFocusEffect = effect; },
});
const derivation = section('  const fitPreviewItem = useMemo(', '  // Compute pixel positions');
function derive() {
  const result = vm.runInContext('(() => {' + derivation + 'return { previewZoom, pageH }; })()', sandbox);
  Object.assign(sandbox, result);
  return result;
}
vm.runInContext(section('  const focusActiveTileAtPreviewZoom =', '  const setCurrentBoardDropTarget =')
  + '\nglobalThis.focusActiveTileAtPreviewZoom = focusActiveTileAtPreviewZoom;'
  + '\nglobalThis.fitActiveTileInView = fitActiveTileInView;', sandbox);
"""


SCROLL_SETUP = NODE_SETUP + r"""
const sandbox = placementSandbox();
const activeChanges = [], frames = new Map();
let clock = 0, nextFrame = 1;
Object.assign(sandbox, {
  items: [{ id: 'p1' }, { id: 'p2' }], activeId: 'p2', previewFitActive: false,
  layout: { positions: [{ top: 0 }, { top: 24.228814 * 250 }] },
  pageH: 250, boardOrderSignature: 'p1|p2', columnCount: 1,
  scrollRef: { current: { scrollTop: 0, scrollHeight: 8000, clientHeight: 400 } },
  syncLock: { current: 0 }, syncLockOriginIdRef: { current: null }, boardSelectionIntentRef: { current: null }, positionDragRef: { current: null },
  currentPage: 1, previewEstimate: { classinPageCount: 22 },
  Date: { now: () => 1000 + clock }, performance: { now: () => clock },
  requestAnimationFrame: callback => { const id = nextFrame++; frames.set(id, callback); return id; },
  cancelAnimationFrame: id => frames.delete(id),
  window: { matchMedia: () => ({ matches: false }) },
  useEffect: effect => effect(),
  nearestPlacementIndex: require('./ui_prototype/reorder.js').nearestPlacementIndex,
  setCurrentPage: update => { sandbox.currentPage = update(sandbox.currentPage); },
  setActive: id => { activeChanges.push(id); sandbox.activeId = id; },
});
vm.runInContext(section('function cancelSmoothScroll(', 'const Icon = {')
  + section('  const syncBoardScrollState =', '  const onScroll =')
  + '\nglobalThis.syncBoardScrollState = syncBoardScrollState;', sandbox);
function externalSelectionScroll() {
  vm.runInContext(section('  // when activeId changes externally, scroll the board to it',
    '  const syncBoardScrollState ='), sandbox);
}
function frame(time) {
  clock = time;
  const scheduled = Array.from(frames.values());
  frames.clear();
  scheduled.forEach(callback => callback(clock));
  sandbox.syncBoardScrollState();
}
"""


class TestUiPreviewFit(unittest.TestCase):
    def test_loading_items_after_the_empty_home_attaches_measurement_and_resize_observers(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const observed = [], callbacks = [];
        const effectRecords = [];
        let effectIndex = 0;
        Object.assign(sandbox, {
          items: [], scrollRef: { current: null }, contentRef: { current: null },
          positionDragRef: { current: null },
          basePageH: 400 / 1.2, contentW: 0,
          boardViewport: { top: 0, height: 800 },
          setBoardViewport: update => { sandbox.boardViewport = update(sandbox.boardViewport); },
          setBasePageH: update => { sandbox.basePageH = update(sandbox.basePageH); },
          setContentW: update => { sandbox.contentW = update(sandbox.contentW); },
          window: { getComputedStyle: () => ({ paddingTop: '26px', paddingBottom: '70px' }) },
          ResizeObserver: class {
            constructor(callback) { callbacks.push(callback); }
            observe(element) { observed.push(element); }
            disconnect() {}
          },
          useEffect: (effect, dependencies) => {
            const index = effectIndex++;
            const previous = effectRecords[index];
            if (!previous || dependencies.some((value, key) => value !== previous[key])) effect();
            effectRecords[index] = dependencies;
          },
        });
        const effects = section('  // One ClassIn page is 1.2 layout units.', '  // Reflow is independent');
        const renderEffects = () => { effectIndex = 0; vm.runInContext(effects, sandbox); };
        renderEffects();
        assert.equal(observed.length, 0);
        sandbox.items = [{ id: 'loaded' }];
        sandbox.scrollRef.current = { clientHeight: 362 };
        sandbox.contentRef.current = { clientWidth: 700 };
        renderEffects();
        assert.equal(observed.length, 2, 'refs appearing after upload must attach both observers');
        assert.equal(sandbox.basePageH, 266 / 1.2);
        assert.equal(sandbox.contentW, 700);
        sandbox.scrollRef.current.clientHeight = 180;
        sandbox.contentRef.current.clientWidth = 430;
        callbacks.forEach(callback => callback());
        assert.equal(sandbox.basePageH, 84 / 1.2);
        assert.equal(sandbox.contentW, 430);
        renderEffects();
        assert.equal(observed.length, 2, 'ordinary edits must not duplicate observers');
        """)

    def test_automatic_fit_handles_long_items_and_small_viewports_below_manual_zoom_minimum(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        for (const viewport of [362, 120, 80]) {
          const usable = Math.max(1, viewport - 26 - 70);
          const base = sandbox.boardPreviewBasePageHeight(viewport, 26, 70);
          for (const height of [12, 34.3, 60]) {
            const zoom = sandbox.boardPreviewZoomForRenderedHeight(height);
            const renderedPixels = height * base * zoom;
            assert.ok(zoom > 0 && zoom < 0.12);
            assert.ok(renderedPixels <= usable, `${height} pages exceed the ${usable}px viewport: ${renderedPixels}`);
            assert.ok(Math.abs(renderedPixels - usable * 0.9) < 0.001);
          }
        }
        assert.equal(sandbox.normalizeBoardPreviewZoom(0.01), 0.12, 'manual zoom keeps its usable lower bound');
        assert.equal(sandbox.normalizeBoardPreviewZoom(9), 1.4);
        """)

    def test_fit_tracks_resize_and_selected_item_without_changing_saved_placement(self) -> None:
        run_node(FIT_SETUP + r"""
        derive();
        sandbox.fitActiveTileInView();
        assert.equal(sandbox.previewFitActive, true);
        let result = derive();
        assert.ok(result.pageH < 24, 'automatic fit must bypass the manual page-height floor');
        assert.ok(result.pageH * 34.3 <= 266);
        sandbox.basePageH = sandbox.boardPreviewBasePageHeight(180, 26, 70);
        result = derive();
        assert.ok(result.pageH * 34.3 <= 84, 'resize must recompute automatic fit');
        sandbox.activeId = 'p2';
        result = derive();
        assert.ok(result.previewZoom > 1, 'selection change must fit the new, short item');
        assert.ok(result.pageH * 0.8 <= 84);
        assert.equal(JSON.stringify(fixture), savedSignature, 'display zoom must not alter export placement data');
        """)

    def test_fit_focus_uses_the_rendered_tile_offset_and_manual_zoom_exits_fit_mode(self) -> None:
        run_node(FIT_SETUP + r"""
        derive();
        sandbox.fitActiveTileInView(); derive(); sandbox.runFocusEffect();
        assert.equal(sandbox.scrollRef.current.scrollTop, 76, 'focus follows the actual positioned tile, including its offset');
        sandbox.activeId = 'p2'; derive(); sandbox.runFocusEffect();
        assert.equal(sandbox.scrollRef.current.scrollTop, 160);
        sandbox.focusActiveTileAtPreviewZoom(0.01);
        assert.equal(sandbox.previewFitActive, false);
        assert.equal(sandbox.manualPreviewZoom, 0.12);
        assert.equal(derive().pageH, Math.max(24, sandbox.contentW * 590 / 1280 * 0.12));
        assert.equal(JSON.stringify(fixture), savedSignature);
        """)

    def test_fit_scroll_updates_the_page_counter_without_changing_the_selection(self) -> None:
        run_node(FIT_SETUP + r"""
        const activeChanges = [];
        Object.assign(sandbox, {
          previewFitActive: true, pageH: 5, previewEstimate: { classinPageCount: 50 },
          positionDragRef: { current: null }, currentPage: 1,
          setCurrentPage: update => { sandbox.currentPage = update(sandbox.currentPage); },
          setActive: id => { activeChanges.push(id); }, nearestPlacementIndex: () => 1,
        });
        vm.runInContext(section('  const syncBoardScrollState =', '  const onScroll =')
          + '\nglobalThis.syncBoardScrollState = syncBoardScrollState;', sandbox);
        sandbox.scrollRef.current.scrollTop = 40;
        sandbox.syncBoardScrollState();
        assert.ok(sandbox.currentPage > 1);
        assert.equal(activeChanges.length, 0, 'fit-triggered scroll must not replace the fitted selection');
        sandbox.previewFitActive = false;
        sandbox.syncBoardScrollState();
        assert.deepEqual(activeChanges, ['p2']);
        """)

    def test_manual_zoom_rail_selection_keeps_its_target_during_the_actual_smooth_scroll(self) -> None:
        run_node(SCROLL_SETUP + r"""
        externalSelectionScroll();
        assert.equal(typeof sandbox.scrollRef.current.__scrollResolve, 'function');
        for (const time of [16, 32, 48, 64, 80, 100, 200, 300]) {
          frame(time);
          assert.equal(sandbox.activeId, 'p2', `intermediate frame ${time}ms replaced the rail selection`);
        }
        assert.ok(sandbox.currentPage > 1, 'the page counter must follow the animated scroll');
        frame(400);
        assert.equal(sandbox.scrollRef.current.__scrollResolve, null);
        assert.equal(sandbox.scrollRef.current.__scrollRaf, null);
        assert.equal(sandbox.activeId, 'p2');
        assert.deepEqual(activeChanges, [], 'the sidebar target must stay stable until the animation finishes');
        assert.ok(Math.abs(sandbox.scrollRef.current.scrollTop - (24.228814 * 250 - 8)) < 0.001);
        """)

    def test_user_wheel_or_touch_pointer_interrupts_focus_scroll_and_restores_selection_sync(self) -> None:
        run_node(SCROLL_SETUP + r"""
        const stageScroll = section('              className="stage-scroll"', '              <div\n                className={`stage-content');
        for (const eventName of ['onWheel', 'onPointerDown']) {
          const handler = stageScroll.match(new RegExp(eventName + '=\\{([^}]+)\\}'));
          assert.ok(handler, `${eventName} must interrupt automatic scroll`);
          sandbox.activeId = 'p2'; sandbox.syncLock.current = 0;
          sandbox.scrollRef.current.scrollTop = 0;
          externalSelectionScroll();
          frame(clock + 16);
          assert.equal(sandbox.activeId, 'p2');
          const interrupt = vm.runInContext('(' + handler[1] + ')', sandbox);
          interrupt({ pointerType: eventName === 'onPointerDown' ? 'touch' : undefined });
          assert.equal(sandbox.scrollRef.current.__scrollResolve, null);
          assert.equal(sandbox.scrollRef.current.__scrollRaf, null);
          assert.equal(frames.size, 0, 'manual input cancels the pending animation frame');
          sandbox.scrollRef.current.scrollTop = 120;
          sandbox.syncBoardScrollState();
          assert.equal(sandbox.activeId, 'p1', `${eventName} must allow manual nearest-item selection`);
          assert.equal(sandbox.currentPage, 1);
        }
        assert.deepEqual(activeChanges, ['p1', 'p1']);
        """)


if __name__ == '__main__':
    unittest.main()
