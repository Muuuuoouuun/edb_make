"""Execute the actual item-rail keyboard handler and its selection helper."""
from __future__ import annotations

import unittest

from test_ui_reorder_helper import run_node


NODE_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { selectionKeyboardCommand } = require('./ui_prototype/reorder.js');
const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
const start = source.indexOf('  const applyRailSelectionKeyboard = (item, event) => {');
const end = source.indexOf('  const toggleRecentSessionsCollapsed =', start);
assert.ok(start >= 0 && end > start, 'extract the real ItemsRail handler');

function railSandbox(visibleIds = ['a', 'b', 'c', 'd'], selectedIds = ['b'], anchor = 'b') {
  const calls = [];
  const sandbox = {
    visibleItemRows: visibleIds.map(id => ({ item: { id } })),
    selectedItemIds: new Set(selectedIds),
    selectionAnchorRef: { current: anchor },
    railSelectionSnapshotRef: { current: selectedIds.join('|') },
    pendingKeyboardFocusRef: { current: null },
    itemRefs: { current: {} },
    focusedId: anchor,
    focusCalls: [],
    selectionKeyboardCommand,
    activeId: anchor,
    selectionPopupOpen: false,
    selectionKey: '',
    dismissedSelection: null,
    setDismissedSelection(value) { sandbox.dismissedSelection = value; calls.push('dismiss'); },
    setSelectedItemIds(value) {
      sandbox.selectedItemIds = value;
      calls.push('selection');
    },
    setActive(value) { sandbox.activeId = value; calls.push('active'); },
    setReorderAnnouncement(value) { sandbox.announcement = value; calls.push('announce'); },
  };
  for (const id of visibleIds) {
    sandbox.itemRefs.current[id] = {
      focus(options) {
        sandbox.focusedId = id;
        sandbox.focusCalls.push({ id, preventScroll: options.preventScroll });
      },
    };
  }
  vm.createContext(sandbox);
  vm.runInContext(source.slice(start, end)
    + '\nglobalThis.handle = applyRailSelectionKeyboard;', sandbox);
  sandbox.press = (id, props) => {
    let prevented = 0;
    const consumed = sandbox.handle({ id }, {
      ...props,
      preventDefault() { prevented += 1; },
    });
    return { consumed, prevented };
  };
  sandbox.calls = calls;
  return sandbox;
}
"""


class TestUiRailKeyboard(unittest.TestCase):
    def test_first_shift_click_uses_active_item_without_prior_selection(self) -> None:
        run_node(NODE_SETUP + r"""
        const { applySelectionClick } = require('./ui_prototype/reorder.js');
        const sandbox = {
          visibleItemRows: ['a','b','c','d'].map(id => ({item:{id}})),
          selectedItemIds: new Set(), activeId: 'a',
          selectionAnchorRef: {current:null}, railSelectionSnapshotRef: {current:''},
          applySelectionClick,
          setSelectedItemIds(value) { sandbox.selectedItemIds = value; },
          setActive(value) { sandbox.activeId = value; },
        };
        vm.createContext(sandbox);
        const clickStart = source.indexOf('  const selectRailItem = (itemId, event = {}) => {');
        const clickEnd = source.indexOf('  const clearRailSelection =', clickStart);
        vm.runInContext(source.slice(clickStart, clickEnd) + '\nglobalThis.click = selectRailItem;', sandbox);
        sandbox.click('c', {shiftKey:true});
        assert.deepEqual(Array.from(sandbox.selectedItemIds), ['a','b','c']);
        assert.equal(sandbox.selectionAnchorRef.current, 'a');
        sandbox.click('d', {shiftKey:true});
        assert.deepEqual(Array.from(sandbox.selectedItemIds), ['a','b','c','d']);
        sandbox.click('b', {shiftKey:true});
        assert.deepEqual(Array.from(sandbox.selectedItemIds), ['a','b']);
        """)

    def test_control_and_command_select_all_are_consumed_without_throwing(self) -> None:
        run_node(NODE_SETUP + r"""
        for (const modifier of ['ctrlKey', 'metaKey']) {
          const rail = railSandbox();
          assert.deepEqual(rail.press('b', {key:'a', [modifier]:true}),
            {consumed:true, prevented:1});
          assert.deepEqual(Array.from(rail.selectedItemIds), ['a','b','c','d']);
          assert.equal(rail.selectionAnchorRef.current, 'b');
          assert.equal(rail.railSelectionSnapshotRef.current, 'a|b|c|d');
          assert.equal(rail.pendingKeyboardFocusRef.current, null);
          assert.equal(rail.focusedId, 'b');
          assert.equal(rail.announcement, '4개 문제를 선택했습니다.');
        }
        """)

    def test_shift_arrows_extend_and_shrink_range_with_focus(self) -> None:
        run_node(NODE_SETUP + r"""
        const rail = railSandbox();
        assert.deepEqual(rail.press('b', {key:'ArrowDown', shiftKey:true}),
          {consumed:true, prevented:1});
        assert.deepEqual(Array.from(rail.selectedItemIds), ['b','c']);
        assert.equal(rail.activeId, 'c');
        assert.equal(rail.pendingKeyboardFocusRef.current, null);
        assert.equal(rail.focusedId, 'c');
        assert.equal(rail.selectionAnchorRef.current, 'b');
        assert.equal(rail.press(rail.focusedId, {key:'ArrowDown', shiftKey:true}).consumed, true);
        assert.deepEqual(Array.from(rail.selectedItemIds), ['b','c','d']);
        assert.equal(rail.focusedId, 'd');
        assert.equal(rail.press(rail.focusedId, {key:'ArrowUp', shiftKey:true}).consumed, true);
        assert.deepEqual(Array.from(rail.selectedItemIds), ['b','c']);
        assert.equal(rail.press(rail.focusedId, {key:'ArrowUp', shiftKey:true}).consumed, true);
        assert.deepEqual(Array.from(rail.selectedItemIds), ['b']);
        assert.equal(rail.activeId, 'b');
        assert.equal(rail.focusedId, 'b');
        assert.equal(rail.railSelectionSnapshotRef.current, 'b');
        assert.deepEqual(rail.focusCalls, ['c','d','c','b'].map(id => ({id,preventScroll:true})));
        """)

    def test_escape_and_modified_select_all_clear_without_changing_active_item(self) -> None:
        run_node(NODE_SETUP + r"""
        for (const props of [
          {key:'Escape'},
          {key:'a', ctrlKey:true, shiftKey:true},
          {key:'A', metaKey:true, shiftKey:true},
        ]) {
          const rail = railSandbox(['a','b','c'], ['a','b'], 'b');
          assert.deepEqual(rail.press('b', props), {consumed:true, prevented:1});
          assert.deepEqual(Array.from(rail.selectedItemIds), []);
          assert.equal(rail.selectionAnchorRef.current, null);
          assert.equal(rail.railSelectionSnapshotRef.current, '');
          assert.equal(rail.pendingKeyboardFocusRef.current, null);
          assert.equal(rail.activeId, 'b');
          assert.equal(rail.announcement, '문제 선택을 해제했습니다.');
          assert.ok(!rail.calls.includes('active'));
          assert.deepEqual(rail.focusCalls, []);
        }
        """)

    def test_first_escape_closes_the_bulk_popup_before_clearing_selection(self) -> None:
        run_node(NODE_SETUP + r"""
        const rail = railSandbox(['a','b','c'], ['a','b'], 'b');
        rail.selectionPopupOpen = true;
        rail.selectionKey = '["a","b"]';
        assert.deepEqual(rail.press('b', {key:'Escape'}), {consumed:true, prevented:1});
        assert.equal(rail.dismissedSelection, '["a","b"]');
        assert.deepEqual(Array.from(rail.selectedItemIds), ['a','b']);
        rail.selectionPopupOpen = false;
        assert.deepEqual(rail.press('b', {key:'Escape'}), {consumed:true, prevented:1});
        assert.deepEqual(Array.from(rail.selectedItemIds), []);
        """)

    def test_filtered_selection_uses_only_visible_rows(self) -> None:
        run_node(NODE_SETUP + r"""
        const rail = railSandbox(['a','c'], ['b'], 'b');
        assert.equal(rail.press('a', {key:'a', ctrlKey:true}).consumed, true);
        assert.deepEqual(Array.from(rail.selectedItemIds), ['a','c']);
        assert.equal(rail.selectionAnchorRef.current, 'a');
        assert.equal(rail.activeId, 'a');
        assert.equal(rail.focusedId, 'a');
        const empty = railSandbox([], [], null);
        assert.deepEqual(empty.press('a', {key:'a', ctrlKey:true}),
          {consumed:true, prevented:1});
        assert.deepEqual(Array.from(empty.selectedItemIds), []);
        assert.deepEqual(empty.focusCalls, []);
        """)

    def test_selection_focus_tolerates_missing_row_and_does_not_overwrite_reorder_focus(self) -> None:
        run_node(NODE_SETUP + r"""
        const rail = railSandbox();
        rail.pendingKeyboardFocusRef.current = 'reordered-row';
        delete rail.itemRefs.current.c;
        assert.deepEqual(rail.press('b', {key:'ArrowDown', shiftKey:true}),
          {consumed:true, prevented:1});
        assert.deepEqual(Array.from(rail.selectedItemIds), ['b','c']);
        assert.equal(rail.activeId, 'c');
        assert.equal(rail.pendingKeyboardFocusRef.current, 'reordered-row');
        assert.deepEqual(rail.focusCalls, []);
        """)

    def test_non_selection_keys_pass_through_to_reorder_delete_and_activation(self) -> None:
        run_node(NODE_SETUP + r"""
        for (const props of [
          {key:'ArrowDown', altKey:true},
          {key:'ArrowUp', altKey:true, shiftKey:true},
          {key:'a', altKey:true, ctrlKey:true},
          {key:'ArrowDown'}, {key:'Enter'}, {key:' '},
          {key:'Delete'}, {key:'Backspace'},
        ]) {
          const rail = railSandbox();
          assert.deepEqual(rail.press('b', props), {consumed:false, prevented:0});
          assert.deepEqual(Array.from(rail.selectedItemIds), ['b']);
          assert.deepEqual(rail.calls, []);
          assert.deepEqual(rail.focusCalls, []);
        }
        """)


if __name__ == '__main__':
    unittest.main()
