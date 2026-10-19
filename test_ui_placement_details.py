from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


NODE_SETUP = r"""
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
function placementSandbox() {
  // Review labels/counts are unrelated to persisted placement. Keep the actual
  // placement normalizers, reflow, serialization, mapping and signatures.
  const sandbox = {
    riskFlagsFor: entity => entity.riskFlags || [],
    riskFlagLabel: flag => flag,
    deriveProblemStatus: entity => entity.reviewStatus || 'normal',
    normalizeReviewStatus: status => status || 'normal',
    reviewStatusMeta: status => ({ label: status }),
    applyProblemCounts() {},
  };
  vm.createContext(sandbox);
  vm.runInContext(
    section('const FIXED_LEFT_ZONE_RATIO =', 'const INITIAL_ITEMS =')
      + section('const PROCESSING_STEPS =', 'const stepLabel =')
      + section('const DEFAULT_INPUT_INTENT =', 'const REVIEW_STATUS_META =')
      + section('function cloneSession(', 'function makeUniqueId(')
      + section('function applyItemStateToProblem(', 'function confirmedItemState(')
      + section('function materializeSessionForItems(', 'function prepareSessionPublishRequest(')
      + section('const KIND_BY_SUBJECT =', 'let latestServerSessionRevision =')
      + section('function derivePlacementSaveState(', 'function SidePanel('),
    sandbox,
  );
  return sandbox;
}
function savedFixture(sandbox, heights = [0.8, 0.9]) {
  const raw = {
    session_name: '수업 자료',
    problems: heights.map((height, index) => ({
      id: `p${index + 1}`, title: `문항 ${index + 1}`, actualHeightPages: height,
    })),
    pages: [{ id: 'page-1', problemIds: heights.map((_, index) => `p${index + 1}`) }],
  };
  const reflow = items => sandbox.reflowItemsForBoardOrder(items, 1.2, 1, 'classin-grid');
  const session = sandbox.materializeSessionForItems(
    raw, reflow(raw.problems.map(sandbox.mapProblemToItem)), raw.session_name, 1, 'classin-grid',
  );
  const items = reflow(session.problems.map(sandbox.mapProblemToItem));
  return { session, items };
}
"""


class TestUiPlacementSaveState(unittest.TestCase):
    def test_empty_and_preview_data_never_claim_a_persisted_save(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const empty = sandbox.derivePlacementSaveState(null, [], 1, 'classin-grid');
        assert.equal(empty.label, '자료 없음');
        assert.equal(empty.canSave, false);
        assert.equal(empty.dirty, false);
        const preview = sandbox.derivePlacementSaveState(null, [{ id: 'sample' }], 1, 'classin-grid');
        assert.equal(preview.label, '미리보기');
        assert.equal(preview.canSave, true, 'sample placement can be applied to its preview');
        assert.equal(preview.dirty, false);
        """)

    def test_adjustments_compare_with_saved_session_and_only_clear_after_persistence(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const { session, items } = savedFixture(sandbox);
        const savedBytes = JSON.stringify(session);
        const saved = sandbox.derivePlacementSaveState(session, items, 1, 'classin-grid');
        assert.equal(saved.label, '저장됨');
        assert.equal(saved.canSave, false);
        for (const patch of [{ xRatio: 0.4 }, { yRatio: 0.6 }, { scaleRatio: 0.85 }]) {
          const changed = [sandbox.applyPlacementPatchToItem(items[0], patch), items[1]];
          const dirty = sandbox.derivePlacementSaveState(session, changed, 1, 'classin-grid');
          assert.equal(dirty.label, '미저장 변경', JSON.stringify(patch));
          assert.equal(dirty.dirty, true);
          assert.equal(dirty.canSave, true);
          assert.notEqual(dirty.draftSignature, dirty.savedSignature);
          // A failed request leaves the server baseline intact and must stay dirty.
          assert.equal(sandbox.derivePlacementSaveState(session, changed, 1, 'classin-grid').dirty, true);
          const restored = sandbox.materializeSessionForItems(session, changed, session.session_name, 1, 'classin-grid');
          const reloaded = sandbox.reflowItemsForBoardOrder(restored.problems.map(sandbox.mapProblemToItem), 1.2, 1, 'classin-grid');
          assert.equal(sandbox.derivePlacementSaveState(restored, reloaded, 1, 'classin-grid').label, '저장됨');
        }
        assert.equal(JSON.stringify(session), savedBytes, 'status derivation must not overwrite the saved baseline');
        """)

    def test_spacing_only_edit_is_dirty_even_when_item_positions_do_not_change(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const { session, items } = savedFixture(sandbox, [1.2]);
        const compactItems = sandbox.reflowItemsForBoardOrder(items, 1.2, 1, 'compact');
        assert.equal(compactItems[0].startYPages, items[0].startYPages);
        assert.equal(compactItems[0].snappedNextStartYPages, items[0].snappedNextStartYPages);
        const dirty = sandbox.derivePlacementSaveState(session, compactItems, 1, 'compact');
        assert.equal(dirty.dirty, true, 'spacing itself must be persisted even with identical coordinates');
        assert.equal(dirty.canSave, true);
        assert.equal(session.layoutGapMode, 'classin-grid');
        const restored = sandbox.materializeSessionForItems(session, compactItems, session.session_name, 1, 'compact');
        assert.equal(sandbox.derivePlacementSaveState(restored, compactItems, 1, 'compact').dirty, false);
        """)

    def test_reordering_and_reverting_are_reflected_without_click_history(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const { session, items } = savedFixture(sandbox);
        assert.equal(sandbox.derivePlacementSaveState(session, [...items].reverse(), 1, 'classin-grid').dirty, true);
        const edited = [sandbox.applyPlacementPatchToItem(items[0], { xRatio: 0.4 }), items[1]];
        assert.equal(sandbox.derivePlacementSaveState(session, edited, 1, 'classin-grid').dirty, true);
        assert.equal(sandbox.derivePlacementSaveState(session, items, 1, 'classin-grid').dirty, false,
          'returning to saved values must clear dirty state without a save click');
        """)

    def test_gap_mode_edit_preserves_saved_baseline_and_cancel_preserves_preview(self) -> None:
        run_node(NODE_SETUP + r"""
        function controller(confirmed) {
          const sandbox = placementSandbox();
          const fixture = savedFixture(sandbox, [1.2]);
          Object.assign(sandbox, fixture, {
            boardColumns: 1, layoutGapMode: 'classin-grid',
            window: { confirm: () => confirmed },
            setLayoutGapModeState: mode => { sandbox.layoutGapMode = mode; },
            setItems: update => { sandbox.items = update(sandbox.items); },
            setSession() { throw new Error('local spacing must not overwrite the persisted baseline'); },
            setPublished() {}, showToast() {},
          });
          vm.runInContext(section('  const updateLayoutGapMode =', '  const resetPlacementGaps =')
            + '\nglobalThis.updateLayoutGapMode = updateLayoutGapMode;', sandbox);
          return sandbox;
        }
        const cancelled = controller(false);
        const originalItems = cancelled.items;
        cancelled.updateLayoutGapMode('compact');
        assert.equal(cancelled.layoutGapMode, 'classin-grid');
        assert.equal(cancelled.items, originalItems);
        const applied = controller(true);
        const originalSession = JSON.stringify(applied.session);
        applied.updateLayoutGapMode('compact');
        assert.equal(applied.layoutGapMode, 'compact');
        assert.equal(JSON.stringify(applied.session), originalSession);
        assert.equal(applied.derivePlacementSaveState(applied.session, applied.items, 1, applied.layoutGapMode).dirty, true);
        """)

    def test_review_mutation_preserves_unsaved_spacing_in_the_server_snapshot(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const { session, items } = savedFixture(sandbox, [1.2]);
        const restoredSnapshots = [];
        Object.assign(sandbox, {
          session, items, fileName: session.session_name, boardColumns: 1, layoutGapMode: 'compact',
          useCallback: fn => fn, mutatingRef: { current: false },
          setMutating() {}, setLoading() {}, setHistoryStack() {},
          adoptMutatedSession() {}, refreshSessionHistory() {}, showSimpleErrorToast() {}, showToast() {},
          postRestore: async snapshot => { restoredSnapshots.push(snapshot); },
          postMutate: async () => session,
        });
        vm.runInContext(section('  const mutateSession = useCallback(', '  const retryAiSession = useCallback')
          + '\nglobalThis.mutateSession = mutateSession;', sandbox);
        (async () => {
          await sandbox.mutateSession('confirm', { id: 'p1' });
          assert.equal(restoredSnapshots.length, 1);
          assert.equal(restoredSnapshots[0].layoutGapMode, 'compact',
            'confirm/crop mutations must preserve the unsaved spacing preview');
          assert.equal(restoredSnapshots[0].layout_gap_mode, 'compact');
          assert.equal(session.layoutGapMode, 'classin-grid', 'snapshot construction must keep saved baseline immutable');
          assert.equal(sandbox.mutatingRef.current, false);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_vertical_hint_explains_why_controls_are_disabled(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        assert.equal(sandbox.placementVerticalRoomHint(null, 1), '');
        const exact = { id: 'exact', heightFrac: 1.2, placementScaleRatio: 1, startYPages: 0, snappedNextStartYPages: 1.2 };
        assert.ok(sandbox.placementVerticalRoomHint(exact, 1).includes('크기를 줄이면'));
        assert.equal(sandbox.placementVerticalRoomHint(exact, 0.8), '', 'smaller material has movable vertical space');
        const continuous = { ...exact, inputIntent: 'page-as-is' };
        assert.ok(sandbox.placementVerticalRoomHint(continuous, 1).includes('상하 위치가 고정'));
        """)

    def test_scope_switch_after_passage_preset_changes_the_actual_adjustment_targets(self) -> None:
        run_node(NODE_SETUP + r"""
        const passage = { id: 'passage' }, question = { id: 'question' }, other = { id: 'other' };
        const sandbox = {
          placementPreset: 'passage-only', placementScope: 'group',
          selectedPassageFragments: [passage], selectedGroupItems: [passage, question],
          selectedPassageGroupId: 'group-1', item: question, items: [passage, question, other],
          setPlacementScope: scope => { sandbox.placementScope = scope; },
          setPlacementPreset: preset => { sandbox.placementPreset = preset; },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const selectPlacementScope =', '  const updatePlacement =')
          + '\nglobalThis.selectPlacementScope = selectPlacementScope;', sandbox);
        const targets = () => vm.runInContext('(() => {'
          + section('  const placementTargets =', '  const placementTargetLabel =')
          + 'return placementTargets.map(item => item.id).join(","); })()', sandbox);
        assert.equal(targets(), 'passage', 'passage preset intentionally adjusts only extracted material');
        sandbox.selectPlacementScope('all');
        assert.equal(targets(), 'passage,question,other', 'all-scope must include questions after the preset');
        sandbox.placementPreset = 'passage-only';
        sandbox.selectPlacementScope('item');
        assert.equal(targets(), 'question', 'item-scope must use the selected item');
        sandbox.placementPreset = 'passage-only';
        sandbox.selectPlacementScope('group');
        assert.equal(targets(), 'passage,question', 'group-scope must include its child questions');
        """)


ESTIMATE_RENDER_SETUP = NODE_SETUP + r"""
const Babel = require('./scripts/vendor/babel.min.js');
const fragment = section('          <div className="stage-toolbar stage-unified-toolbar"', '        <div className="stage-wrap">');
const portals = [], focusCalls = [], listeners = new Map();
const panel = { style: { setProperty() {} }, focus: options => focusCalls.push(['panel', options]), contains: target => target === panel };
const toggleElement = { getBoundingClientRect: () => ({ left: 100, right: 400, bottom: 120 }), focus: options => focusCalls.push(['toggle', options]), contains: target => target === toggleElement };
const sandbox = placementSandbox();
Object.assign(sandbox, {
  React: { createElement(type, props, ...children) {
    return { type, props: props || {}, children: children.flat(Infinity).filter(child => child != null && child !== false) };
  } },
  ReactDOM: { createPortal(node, container) { portals.push({ node, container }); return null; } },
  Icon: { chevronUp: 'up', chevronDown: 'down', close: 'close' },
  ToolbarActionMenu() {},
  reviewAttentionCount: 0, interactionBusy: false, positioningId: null, focusMode: false,
  panelCollapsed: false, panelReopenRef: { current: null }, onOpenPanel() {}, onOpenPendingReview() {},
  previewZoom: 1, previewFitActive: false, activePlacement: null,
  BOARD_PREVIEW_ZOOM_MIN: 0.2, BOARD_PREVIEW_ZOOM_MAX: 2, BOARD_PREVIEW_ZOOM_STEP: 0.1,
  focusActiveTileAtPreviewZoom() {}, fitActiveTileInView() {},
  smartMove: false, setSmartMove() {}, pageGuides: true, togglePageGuides() {},
  activeId: 'p1', layout: { items: [] }, reducePlacementGap() {},
  sourcePageCount: 0, onRecognizeSession: null, canRecognizeSession: false,
  recognitionDisabledReason: '', sessionEditState: {},
  activeContinuousFlow: false, columnCount: 1, processedCount: 1, items: [{id: 'p1'}],
  rawCount: 0, s1Count: 0, aiCount: 1, reconstructCount: 0, ALTERNATE_MODIFIER_LABEL: 'Alt',
  estimateDetailsOpen: false,
  estimateBarRef: { current: { getBoundingClientRect: () => ({ right: 800, bottom: 120 }) } },
  estimateToggleRef: { current: toggleElement }, estimateDetailsRef: { current: panel },
  previewEstimate: {
    classinPageCount: 3, displayPageCount: 4, utilizationRatio: 0.8, blankHeightPages: 0.4,
    reservedEndPages: 3.6, usedHeightPages: 3.2,
    active: { startPages: 0, renderedBottomPages: 0.8, nextStartPages: 1.2, rowNextStartPages: 1.2,
      classinStartPage: 1, classinEndPage: 1, classinNextPage: 2, autoScaleAdjusted: false, scaleRatio: 1 },
  },
  selectedIds: new Set(['p1']), layoutGapMode: 'classin-grid', activeIndex: 0,
  setEstimateDetailsOpen: value => {
    sandbox.estimateDetailsOpen = typeof value === 'function' ? value(sandbox.estimateDetailsOpen) : value;
  },
  useLayoutEffect: effect => { sandbox.runDisclosureEffect = effect; },
  document: {
    body: {},
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); },
  },
  window: { innerWidth: 1024, innerHeight: 800, addEventListener() {}, removeEventListener() {} },
});
vm.runInContext(section('  const closeEstimateDetails =', '  const [contentW, setContentW] ='), sandbox);
vm.runInContext(Babel.transform('globalThis.renderEstimateFragment = () => (' + fragment + ');', { presets: ['react'] }).code, sandbox);
function renderEstimate() { portals.length = 0; return sandbox.renderEstimateFragment(); }
function walk(node) {
  if (!node || typeof node !== 'object') return [];
  return [node, ...node.children.flatMap(walk)];
}
function text(node) {
  return typeof node === 'string' || typeof node === 'number' ? String(node) : node?.children?.map(text).join('') || '';
}
function findClass(tree, className) {
  return walk(tree).find(node => String(node.props.className || '').split(' ').includes(className));
}
"""


class TestUiEstimateDisclosure(unittest.TestCase):
    def test_detail_toggle_opens_portal_dialog_with_accessible_state(self) -> None:
        run_node(ESTIMATE_RENDER_SETUP + r"""
        let tree = renderEstimate();
        const toggle = findClass(tree, 'stage-estimate-details-toggle');
        assert.equal(portals.length, 0, 'closed details are not mounted');
        assert.equal(toggle.type, 'button');
        assert.equal(toggle.props.type, 'button');
        assert.equal(toggle.props['aria-expanded'], false);
        assert.equal(toggle.props['aria-haspopup'], 'dialog');
        assert.equal(toggle.props['aria-label'], '배치 정보');
        assert.ok(text(toggle).includes('전체 3쪽'));
        toggle.props.onClick();
        tree = renderEstimate();
        assert.equal(portals.length, 1);
        const details = portals[0].node;
        assert.equal(portals[0].container, sandbox.document.body, 'details render outside the board layout');
        assert.equal(findClass(tree, 'stage-estimate-details'), undefined);
        assert.equal(details.props.role, 'dialog');
        assert.equal(details.props.tabIndex, -1);
        assert.ok(walk(details).some(node => node.props.id === details.props['aria-labelledby']));
        assert.equal(findClass(tree, 'stage-estimate-details-toggle').props['aria-controls'], details.props.id);
        assert.equal(findClass(tree, 'stage-estimate-details-toggle').props['aria-expanded'], true);
        findClass(tree, 'stage-estimate-details-toggle').props.onClick();
        renderEstimate();
        assert.equal(portals.length, 0);
        assert.equal(focusCalls.at(-1)[0], 'toggle');
        assert.equal(focusCalls.at(-1)[1].preventScroll, true);
        """)

    def test_summary_is_quiet_and_keeps_only_total_selection_and_details_action(self) -> None:
        run_node(ESTIMATE_RENDER_SETUP + r"""
        const tree = renderEstimate();
        const summary = findClass(tree, 'stage-estimate-summary');
        assert.equal(summary.props.role, undefined, 'drag updates must not become status announcements');
        assert.equal(summary.props['aria-live'], undefined);
        assert.ok(text(summary).includes('ClassIn 전체 3쪽'));
        assert.ok(text(summary).includes('선택 1번 · 1쪽'));
        for (const label of ['다음', 'EDB', '사용률', 'p']) assert.ok(!text(summary).includes(label), label);
        assert.equal(walk(summary).filter(node => node.type === 'button').length, 1,
          'page summary is itself the single details trigger');
        assert.equal(findClass(tree, 'stage-estimate-action'), undefined, 'gap action belongs in placement editing');
        sandbox.previewEstimate.active = null;
        const noSelection = findClass(renderEstimate(), 'stage-estimate-summary');
        assert.ok(text(noSelection).includes('전체 3쪽'));
        assert.ok(!text(noSelection).includes('선택'));
        """)

    def test_details_hide_next_for_last_item_and_collapse_diagnostic_units(self) -> None:
        run_node(ESTIMATE_RENDER_SETUP + r"""
        sandbox.estimateDetailsOpen = true;
        renderEstimate();
        let details = portals[0].node;
        for (const label of ['다음 문항 시작', '전체 여백', '간격 방식', '자동 축소']) {
          assert.ok(text(details).includes(label), label);
        }
        const diagnostics = findClass(details, 'stage-estimate-diagnostics');
        assert.equal(diagnostics.type, 'details');
        assert.ok(!diagnostics.props.open, 'internal units are collapsed by default');
        assert.ok(text(diagnostics).includes('예약 높이 / 사용 높이'));
        sandbox.previewEstimate.active.classinNextPage = null;
        sandbox.previewEstimate.active.nextStartPages = null;
        renderEstimate();
        assert.ok(!text(portals[0].node).includes('다음 문항 시작'));
        sandbox.selectedIds = new Set(['p1', 'p2']);
        const summary = findClass(renderEstimate(), 'stage-estimate-summary');
        assert.ok(text(summary).includes('2개 선택 중'));
        """)

    def test_escape_and_outside_pointer_close_and_restore_focus(self) -> None:
        run_node(ESTIMATE_RENDER_SETUP + r"""
        sandbox.estimateDetailsOpen = true;
        let cleanup = sandbox.runDisclosureEffect();
        assert.equal(focusCalls[0][0], 'panel');
        listeners.get('pointerdown')({ target: panel });
        assert.equal(sandbox.estimateDetailsOpen, true, 'inside clicks keep the dialog open');
        listeners.get('pointerdown')({ target: toggleElement });
        assert.equal(sandbox.estimateDetailsOpen, true, 'toggle handles its own click');
        let prevented = false;
        listeners.get('keydown')({ key: 'Escape', preventDefault() { prevented = true; }, stopPropagation() {} });
        assert.equal(prevented, true);
        assert.equal(sandbox.estimateDetailsOpen, false);
        assert.equal(focusCalls.at(-1)[0], 'toggle');
        cleanup();
        assert.equal(listeners.size, 0, 'closed dialog cleans up document listeners');
        sandbox.estimateDetailsOpen = true;
        cleanup = sandbox.runDisclosureEffect();
        listeners.get('pointerdown')({ target: {} });
        assert.equal(sandbox.estimateDetailsOpen, false);
        assert.equal(focusCalls.at(-1)[1].preventScroll, true);
        cleanup();
        """)


if __name__ == '__main__':
    unittest.main()
