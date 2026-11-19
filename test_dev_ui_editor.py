"""Regression tests for the ui-editor fixes (board scroll selection, review
Delete key in 영역 조정, delete neighbour selection, multi-selection placement
scope, board group drag, board shortcut sheet, report-dialog key isolation,
home settings focus, crop handle tab stops)."""
from __future__ import annotations

import unittest
from pathlib import Path

from test_ui_board_drag import DRAG_SETUP, SCROLL_SELECTION_SETUP
from test_ui_placement_details import ESTIMATE_RENDER_SETUP, NODE_SETUP, run_node

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")


KEY_SETUP = NODE_SETUP + r"""
const listeners = [];
const sandbox = {
  window: { addEventListener: (type, fn) => listeners.push(fn), removeEventListener() {} },
  useEffect: effect => effect(),
};
vm.createContext(sandbox);
vm.runInContext(section('function isEditableKeyboardTarget(', '\n}\n') + '\n}\n', sandbox);
const dialogButton = { tagName: 'BUTTON', closest: selector => selector.includes('dialog[open]') ? {} : null };
const boardTarget = { tagName: 'DIV', closest: () => null };
function key(name, target, extra = {}) {
  const evt = { key: name, target, defaultPrevented: false, repeat: false, ctrlKey: false, metaKey: false,
    altKey: false, shiftKey: false, prevented: 0, stopped: 0, ...extra };
  evt.preventDefault = () => { evt.prevented += 1; };
  evt.stopPropagation = () => { evt.stopped += 1; };
  return evt;
}
function press(name, target, extra) {
  const evt = key(name, target, extra);
  listeners.forEach(listener => listener(evt));
  return evt;
}
"""


class TestBoardScrollSelection(unittest.TestCase):
    def test_scroll_keeps_a_visible_active_tile_and_follows_once_it_leaves(self) -> None:
        run_node(SCROLL_SELECTION_SETUP + r"""
        sandbox.nearestIndex = 1;
        container.scrollTop = 100;
        sandbox.syncScroll();
        assert.deepEqual(selectionCalls, [], 'p1 (0..320px) is still on screen; the panel must stay on it');
        container.scrollTop = 900;
        sandbox.syncScroll();
        assert.deepEqual(selectionCalls, ['p2'], 'once p1 is off screen, selection follows the scroll');
        """)

    def test_scroll_never_changes_the_split_target(self) -> None:
        run_node(SCROLL_SELECTION_SETUP + r"""
        sandbox.splitTargetId = 'p1';
        sandbox.nearestIndex = 2;
        container.scrollTop = 1900;
        sandbox.syncScroll();
        assert.deepEqual(selectionCalls, [], '나누기 must keep its problem selected while scrolling');
        """)


class TestReviewDeleteKeys(unittest.TestCase):
    DELETE_EFFECT = r"""
    vm.runInContext(section("  useEffect(() => {\n    // 영역 조정 owns Delete/Backspace", "  useEffect(() => {\n    const onMove"), sandbox);
    """

    def test_delete_in_box_edit_never_excludes_the_problem(self) -> None:
        run_node(KEY_SETUP + r"""
        let excluded = 0;
        Object.assign(sandbox, { manualSplit: null, boxEdit: { multi: false }, selectedActionIds: ['p1'],
          mutating: false, doExclude: async () => { excluded += 1; } });
        """ + self.DELETE_EFFECT + r"""
        assert.equal(listeners.length, 0, 'the problem-delete listener is off while 영역 조정 is open');
        sandbox.boxEdit = null;
        """ + self.DELETE_EFFECT + r"""
        press('Delete', boardTarget);
        assert.equal(excluded, 1, 'outside the box editor Delete still excludes the selection');
        """)

    def test_box_edit_delete_removes_only_the_selected_area(self) -> None:
        run_node(KEY_SETUP + r"""
        const deleted = [];
        Object.assign(sandbox, { mutating: false, cancelBoxEdit() {}, applyBoxEdit: async () => {},
          nudgeBoxEditSelection() {}, deleteBoxEditSegment: id => deleted.push(id),
          boxEdit: { multi: true, selectedSegmentId: 's2', segments: [{ id: 's1' }, { id: 's2' }] } });
        vm.runInContext(section("  useEffect(() => {\n    if (!boxEdit || mutating) return undefined;\n    const onBoxEditKeyDown",
          "  useLayoutEffect(() => {"), sandbox);
        let evt = press('Backspace', boardTarget);
        assert.deepEqual(deleted, ['s2']);
        assert.equal(evt.prevented, 1, 'Backspace must not navigate back');
        sandbox.boxEdit = { multi: false, segments: [{ id: 's1' }] };
        evt = press('Delete', boardTarget);
        assert.deepEqual(deleted, ['s2'], 'a single box has nothing to delete');
        assert.equal(evt.prevented, 1);
        """)

    def test_window_shortcuts_ignore_keys_from_an_open_dialog(self) -> None:
        run_node(KEY_SETUP + r"""
        const calls = [];
        Object.assign(sandbox, {
          manualSplit: null, boxEdit: null, selectedActionIds: ['p1'], mutating: false,
          doExclude: async () => calls.push('exclude'),
          reviewEditorActive: false, reviewFlow: { complete: false }, selectedUnresolvedProblemIds: ['p1'],
          confirmSelectedAndAdvance: async () => calls.push('confirm'), nextUnresolvedPageFocused: false,
          confirmPageAndAdvance: async () => calls.push('page'), selectNextUnresolvedProblem() {},
          nextUnresolvedProblemId: null,
          historyStack: [{}], undoMutation: () => calls.push('undo'),
          // dev/ui-undo rewrites the same listener with these names; keep the guard there too.
          canUndo: true, canRedo: false, redoMutation: () => calls.push('redo'),
          manualSplitShortcutKey: evt => String(evt.key).toLowerCase(),
        });
        """ + self.DELETE_EFFECT + r"""
        vm.runInContext(section("  useEffect(() => {\n    if (reviewEditorActive || reviewFlow.complete) return undefined;",
          "  useEffect(() => {\n    // 영역 조정 owns Delete/Backspace"), sandbox);
        vm.runInContext(section('  // Ctrl/Cmd+Z → undo', '  // initial session fetch'), sandbox);
        assert.equal(listeners.length, 3);
        press('Backspace', dialogButton);
        press('Enter', dialogButton, { ctrlKey: true });
        press('z', dialogButton, { ctrlKey: true });
        assert.deepEqual(calls, [], 'keys on 문제 신고 buttons must not change the lesson behind it');
        press('Backspace', boardTarget);
        press('Enter', boardTarget, { ctrlKey: true });
        press('z', boardTarget, { ctrlKey: true });
        assert.deepEqual(calls, ['exclude', 'confirm', 'undo']);
        """)

    def test_bug_report_dialog_stops_every_key(self) -> None:
        run_node(NODE_SETUP + r"""
        const dialog = source.slice(source.indexOf('function BugReportDialog('));
        const from = dialog.indexOf('  const handleKeyDown = event => {');
        const body = dialog.slice(from, dialog.indexOf('  const handleSubmit =', from));
        let closed = 0;
        const sandbox = { busy: false, onClose: () => { closed += 1; }, dialogRef: { current: null }, document: {} };
        vm.createContext(sandbox);
        vm.runInContext(body + '\nglobalThis.handleKeyDown = handleKeyDown;', sandbox);
        let stopped = 0, prevented = 0;
        sandbox.handleKeyDown({ key: 'Backspace', stopPropagation: () => { stopped += 1; }, preventDefault: () => { prevented += 1; } });
        assert.equal(stopped, 1, 'Backspace must not reach the window Delete/undo shortcuts');
        assert.equal(prevented, 0, 'typing in the report keeps its default behaviour');
        assert.equal(closed, 0);
        """)


class TestDeleteKeepsNeighbourSelected(unittest.TestCase):
    def test_exclude_selects_the_next_then_previous_survivor(self) -> None:
        run_node(NODE_SETUP + r"""
        let active = null;
        const sandbox = {
          useCallback: fn => fn, replacementSourceIdFor: problem => problem.replacementSourceId || null,
          normalizeLayoutGapMode: value => value || 'classin-grid', sessionBoardColumns: () => 1,
          boardColumnsRef: { current: 1 }, setTweak() {}, reflowItemsForBoardOrder: items => items,
          mapProblemToItem: problem => ({ id: problem.id }), DEFAULT_SLOT_HEIGHT_PAGES: 1.2,
          layoutGapMode: 'classin-grid', boardColumns: 1, items: [], activeId: null,
          setItems() {}, setSession() {}, setLayoutGapModeState() {}, setPublished() {},
          setActiveId: id => { active = id; },
        };
        vm.createContext(sandbox);
        vm.runInContext('globalThis.adopt = (() => {'
          + section('  const adoptMutatedSession = useCallback(', '  // Run a server-side mutation')
          + 'return adoptMutatedSession; })()', sandbox);
        const run = (activeId, survivors) => {
          active = 'unchanged';
          sandbox.items = ['p1', 'p2', 'p3', 'p4'].map(id => ({ id }));
          sandbox.activeId = activeId;
          sandbox.adopt({ problems: survivors.map(id => ({ id })) });
          return active;
        };
        assert.equal(run('p2', ['p1', 'p3', 'p4']), 'p3', 'deleting the middle selects the next problem');
        assert.equal(run('p4', ['p1', 'p2', 'p3']), 'p3', 'deleting the last selects the previous problem');
        assert.equal(run('p2', ['p1', 'p4']), 'p4', 'multi-delete skips deleted neighbours');
        assert.equal(run('gone', ['p1', 'p2']), 'p1', 'an unknown active id falls back to the first problem');
        assert.equal(run('p1', ['p1', 'p3']), 'unchanged', 'a surviving active problem stays selected');
        """)


class TestPlacementSelectionScope(unittest.TestCase):
    def test_selected_scope_targets_the_whole_multi_selection(self) -> None:
        run_node(NODE_SETUP + r"""
        const a = { id: 'a' }, b = { id: 'b' }, c = { id: 'c' };
        const sandbox = { placementPreset: 'auto', placementScope: 'selected', selectedPassageFragments: [],
          selectedGroupItems: [b], selectedPassageGroupId: '', item: b, items: [a, b, c],
          selectedPanelItems: [a, b, c], multiSelectionCount: 3 };
        vm.createContext(sandbox);
        const resolve = () => vm.runInContext('(() => {'
          + section('  const placementTargets =', '  const passageFragmentCount =')
          + 'return placementTargets.map(item => item.id).join(",") + "|" + placementTargetLabel; })()', sandbox);
        assert.equal(resolve(), 'a,b,c|선택 3개');
        Object.assign(sandbox, { selectedPanelItems: [b], multiSelectionCount: 0 });
        assert.equal(resolve(), 'b|현재 자료 1개', 'a collapsed selection falls back to the active problem');
        """)

    def test_multi_selection_keeps_the_safe_scope_and_offers_the_button(self) -> None:
        run_node(NODE_SETUP + r"""
        const scopes = [];
        const sandbox = { useEffect: effect => effect(), setPlacementScope: scope => scopes.push(scope),
          setPlacementPreset() {}, item: { id: 'b' }, selectedPassageGroupId: '', multiSelectionCount: 3 };
        vm.createContext(sandbox);
        const reset = section("  useEffect(() => {\n    // '선택 N개' stays opt-in", '  const updatePlacementTargets =');
        vm.runInContext(reset, sandbox);
        sandbox.selectedPassageGroupId = 'g1';
        vm.runInContext(reset, sandbox);
        assert.deepEqual(scopes, ['item', 'group'], '선택 N개 is opt-in; a multi-selection never widens the scope by itself');
        """)
        self.assertIn("}, [item?.id, selectedPassageGroupId, multiSelectionCount]);", SOURCE)
        self.assertIn("onClick={() => selectPlacementScope('selected')}", SOURCE)


class TestBoardGroupDrag(unittest.TestCase):
    GROUP = r"""
    sandbox.items = [{ id: 'p1', heightFrac: .8, startYPages: 0 }, { id: 'p2', heightFrac: .8, startYPages: 1.2 },
      { id: 'p3', heightFrac: .8, startYPages: 2.4 }];
    sandbox.layout.items = sandbox.items;
    sandbox.boardOrderSignature = 'p1|p2|p3';
    """

    def test_alt_drag_of_a_selected_problem_moves_the_selection(self) -> None:
        run_node(DRAG_SETUP + self.GROUP + r"""
        sandbox.selectedIds = new Set(['p1', 'p2']);
        sandbox.dropTarget = { id: 'p3', position: 'after' };
        sandbox.begin(event(20, 20, true), { id: 'p1' }, placement);
        assert.deepEqual(sandbox.positionDragRef.current.groupIds, ['p1', 'p2']);
        sandbox.move(event(20, 120)); sandbox.end(event(20, 120));
        assert.equal(reorders.length, 1);
        assert.deepEqual(reorders[0][3].sourceIds, ['p1', 'p2'], 'the board moves the group like the list');

        sandbox.dropTarget = { id: 'p2', position: 'after' };
        sandbox.begin(event(20, 20, true), { id: 'p1' }, placement);
        sandbox.move(event(20, 120)); sandbox.end(event(20, 120));
        assert.equal(reorders.length, 1, 'dropping on another selected problem is a no-op');
        """)

    def test_unselected_problem_still_moves_alone(self) -> None:
        run_node(DRAG_SETUP + self.GROUP + r"""
        sandbox.selectedIds = new Set(['p2', 'p3']);
        sandbox.dropTarget = { id: 'p3', position: 'after' };
        sandbox.begin(event(20, 20, true), { id: 'p1' }, placement);
        assert.equal(sandbox.positionDragRef.current.groupIds, null);
        sandbox.move(event(20, 120)); sandbox.end(event(20, 120));
        assert.equal(reorders.length, 1);
        assert.equal(reorders[0][3].sourceIds, undefined);
        """)

    def test_preview_does_not_shift_group_members(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const items = ['p1', 'p2', 'p3', 'p4'].map((id, index) => ({ id, heightFrac: .8, startYPages: index * 1.2 }));
        Object.assign(sandbox, {
          useMemo: fn => fn(), positioningId: 'p1', boardDropTarget: { mode: 'reorder', id: 'p4', position: 'after' },
          items, layout: { items }, columnCount: 1, layoutGapMode: 'classin-grid', pageH: 100,
          selectedIds: new Set(['p1', 'p2']),
          reorderItemsForDrop: require('./ui_prototype/reorder.js').reorderItemsForDrop,
          reorderItemGroupForDrop: require('./ui_prototype/reorder.js').reorderItemGroupForDrop,
        });
        const shifts = () => vm.runInContext('(() => {'
          + section('  const reorderPreviewShifts = useMemo(', '  const reorderPreviewShiftsRef =')
          + 'return reorderPreviewShifts; })()', sandbox);
        let result = shifts();
        assert.ok(result && !result.has('p1') && !result.has('p2'), 'group members are not static neighbours');
        assert.ok(result.get('p3') < 0 && result.get('p4') < 0, 'p3 and p4 slide up by two rows');
        sandbox.boardDropTarget = { mode: 'reorder', id: 'p2', position: 'after' };
        assert.equal(shifts(), null, 'no preview for a drop on a selected sibling');
        """)

    def test_group_drag_shows_no_drop_target_on_its_own_problems(self) -> None:
        run_node(NODE_SETUP + r"""
        let shown = 'unset';
        const sandbox = { boardDropTargetRef: { current: null }, positionDragRef: { current: { groupIds: ['p1', 'p2'] } },
          setBoardDropTarget: update => { shown = update(null); } };
        vm.createContext(sandbox);
        vm.runInContext(section('  const setCurrentBoardDropTarget = (target) => {', '  const findBoardDropTarget =')
          + '\nglobalThis.setTarget = setCurrentBoardDropTarget;', sandbox);
        const show = target => { sandbox.setTarget(target); return shown; };
        assert.equal(show({ id: 'p2', mode: 'reorder', position: 'after' }), null, 'smart move over a selected sibling');
        assert.equal(show({ id: 'p2', position: 'before' }), null, 'Alt-drag over a selected sibling');
        assert.equal(show({ id: 'p3', mode: 'reorder', position: 'after' }).id, 'p3');
        assert.equal(show({ id: 'p2', mode: 'gap', position: 'gap' }).mode, 'gap', 'gap pulls still work');
        sandbox.positionDragRef.current = { groupIds: null };
        assert.equal(show({ id: 'p2', position: 'before' }).id, 'p2', 'a single-problem drag is unchanged');
        """)


class TestBoardShortcutSheet(unittest.TestCase):
    def test_more_menu_toggles_an_os_aware_shortcut_sheet(self) -> None:
        run_node(ESTIMATE_RENDER_SETUP + r"""
        Object.assign(sandbox, {
          setBoardKeysOpen: update => { sandbox.boardKeysOpen = update(sandbox.boardKeysOpen); },
          PRIMARY_MODIFIER_LABEL: '⌘', ALTERNATE_MODIFIER_LABEL: 'Option',
        });
        const keys = () => walk(renderEstimate()).find(n => n.props.id === 'board-tools').props.items
          .find(item => item.id === 'keys');
        assert.equal(findClass(renderEstimate(), 'board-shortcut-guide'), undefined);
        assert.equal(keys().label, '단축키 보기');
        keys().onSelect();
        assert.equal(keys().label, '단축키 닫기');
        const guide = text(findClass(renderEstimate(), 'board-shortcut-guide'));
        for (const label of ['F2', '⌘ Z', '⌘ A', 'Option 1·2·3', 'Option ↑↓', 'Option 드래그', 'Shift ↑↓', 'Delete', 'Esc']) {
          assert.ok(guide.includes(label), `missing ${label}`);
        }
        assert.ok(!guide.includes('Ctrl') && !guide.includes('Alt'), 'Mac wording must not show Windows keys');
        assert.ok(!guide.includes('+'), 'every row writes modifier keys the same way (no "+")');
        assert.ok(guide.includes('왼쪽 목록에서 문제를 클릭한 뒤'), 'list keys need a focused list row; say so');
        keys().onSelect();
        assert.equal(findClass(renderEstimate(), 'board-shortcut-guide'), undefined);
        """)

    def test_every_listed_key_has_a_real_handler(self) -> None:
        for bound in (
            "if (event.key !== 'F2' || event.altKey",  # F2 rename
            "const RIGHT_PANEL_TAB_SHORTCUTS = { Digit1: 'item', Digit2: 'placement', Digit3: 'board' };",
            "if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {",  # list reorder
            "const isRangeArrow = event.shiftKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown');",
            "const isSelectAll = (event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === 'a';",
            "if ((e.key === 'Delete' || e.key === 'Backspace') && handlersRef.current.selectedItemCount > 0) {",
            "void (redo ? redoMutation() : undoMutation());",  # undo / redo (dev/ui-undo)
            "reorderMode: evt.altKey,",  # Alt-drag on the board
            "cancelPositionDrag({ pointerId: drag.pointerId });",  # Esc cancels a board drag
        ):
            self.assertIn(bound, SOURCE)


class TestHomeSettingsFocus(unittest.TestCase):
    def test_settings_open_focuses_the_panel_and_close_returns_to_the_menu(self) -> None:
        run_node(NODE_SETUP + r"""
        const focused = [];
        const element = name => ({ focus: () => focused.push(name) });
        const sandbox = {
          useEffect: effect => effect(),
          window: { requestAnimationFrame: callback => callback() },
          document: {
            getElementById: id => id === 'side-panel-board' ? element('settings') : null,
            querySelector: selector => selector === '[aria-controls="topbar-more-menu"]' ? element('more') : null,
          },
          settingsRequest: 1, consumedSettingsRequestRef: { current: 0 }, emptyHome: true,
          setTab() {}, setHomeSettingsOpen() {}, onRevealPanel() {},
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  useEffect(() => {\n    if (!settingsRequest', '  useEffect(() => {\n    if (!emptyHome)'), sandbox);
        assert.deepEqual(focused, ['settings']);
        vm.runInContext(section('  const closeHomeSettings = () => {', '  const panelVisible =')
          + '\nglobalThis.close = closeHomeSettings;', sandbox);
        sandbox.close();
        assert.deepEqual(focused, ['settings', 'more']);
        """)


class TestCropHandles(unittest.TestCase):
    def test_crop_handles_are_mouse_only_without_english_names(self) -> None:
        self.assertEqual(SOURCE.count("className={`crop-frame-handle ${mode}`}"), 2)
        for block in SOURCE.split("className={`crop-frame-handle ${mode}`}")[1:]:
            attrs = block.split("/>", 1)[0]
            self.assertIn("tabIndex={-1}", attrs)
            self.assertIn('aria-hidden="true"', attrs)
            self.assertNotIn("aria-label", attrs)


if __name__ == "__main__":
    unittest.main()
