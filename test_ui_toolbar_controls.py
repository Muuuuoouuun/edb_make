from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


NODE_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Babel = require('./scripts/vendor/babel.min.js');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
const from = source.indexOf('function toolbarMenuFocusIndex(');
const to = source.indexOf('function ReviewToolbarGroup(', from);
assert.ok(from >= 0 && to > from);
const hooks = { slots: [], index: 0, effects: [], cleanup: [] };
const listeners = new Map();
const sandbox = {
  React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
  Icon: new Proxy({}, { get: (_object, key) => key }),
  BrandMark: () => null,
  PRIMARY_MODIFIER_LABEL: 'Ctrl',
  useState: initial => {
    const index = hooks.index++;
    if (!(index in hooks.slots)) hooks.slots[index] = initial;
    return [hooks.slots[index], update => {
      hooks.slots[index] = typeof update === 'function' ? update(hooks.slots[index]) : update;
    }];
  },
  useRef: initial => {
    const index = hooks.index++;
    if (!(index in hooks.slots)) hooks.slots[index] = { current: initial };
    return hooks.slots[index];
  },
  useCallback: callback => callback,
  useEffect: effect => { hooks.effects.push(effect); },
  window: {
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type, fn) => { if (listeners.get(type) === fn) listeners.delete(type); },
  },
};
vm.createContext(sandbox);
vm.runInContext(Babel.transform(source.slice(from, to), { presets: ['react'] }).code, sandbox);
const walk = value => !value ? [] : Array.isArray(value) ? value.flatMap(walk)
  : typeof value === 'object' ? [value, ...walk(value.props?.children)] : [];
const text = value => !value ? '' : Array.isArray(value) ? value.map(text).join(' ')
  : typeof value === 'object' ? text(value.props?.children) : String(value);
const base = {
  total: 2, fileName: 'lesson', view: 'board', reviewAvailable: true, canReset: true,
  canExportImages: true, canUndo: true, setFileName() {},
};
function keyEvent(key, target, currentTarget) {
  return { key, target, currentTarget, prevented: false, stopped: false,
    preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
}
"""


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", NODE_SETUP + script], cwd=PROJECT_ROOT, check=True)


class TestUiToolbarControls(unittest.TestCase):
    def test_topbar_hides_empty_output_and_preserves_busy_guards(self) -> None:
        run_node(r"""
        const empty = sandbox.TopBar({ ...base, total: 0 });
        assert.equal(walk(empty).some(node => node.props.id === 'topbar-export'), false);
        assert.equal(walk(empty).some(node => node.props.onClick === base.onPublish && text(node).includes('EDB 제작')), false);
        const busy = sandbox.TopBar({ ...base, operationBusy: true });
        assert.equal(walk(busy).find(node => node.type === sandbox.LessonNameEditor).props.disabled, true);
        const more = walk(busy).find(node => node.props.id === 'topbar-more');
        assert.equal(more.props.items.find(item => item.id === 'review').disabled, true);
        const resetButton = tree => walk(tree).find(node => String(node.props?.className || '').includes('topbar-reset'));
        assert.equal(resetButton(busy).props.disabled, true);
        assert.equal(more.props.items.some(item => item.id === 'reset'), false, 'reset is a visible toolbar button');
        assert.equal(more.props.items.find(item => item.id === 'refresh').disabled, true);
        const resetBlocked = sandbox.TopBar({ ...base, resetBlocked: true });
        assert.equal(resetButton(resetBlocked).props.disabled, true);
        assert.equal(resetButton(sandbox.TopBar(base)).props.disabled, false);
        const reviewingPages = sandbox.TopBar({ ...base, pageReviewActive: true });
        assert.equal(walk(reviewingPages).find(node => node.props.id === 'topbar-more').props.items.find(item => item.id === 'review').disabled, true);
        const reviewBusy = sandbox.TopBar({ ...base, view: 'review', operationBusy: true });
        assert.equal(walk(reviewBusy).find(node => node.props.id === 'topbar-more').props.items.find(item => item.id === 'board').disabled, true);
        """)

    def test_topbar_orders_reset_before_material_controls_and_update(self) -> None:
        run_node(r"""
        const order = tree => {
          const actions = walk(tree).find(node => node.props?.className === 'topbar-actions');
          return walk(actions.props.children).filter(node => node && node.props && (
            node.props.className === 'editor-mode-control' || node.props['aria-label'] === '되돌리기'
            || /(^|\s)topbar-(reset|update)(\s|$)/.test(String(node.props.className || '')) || node.props.id === 'topbar-more'
          )).map(node => node.props.id || node.props['aria-label']
            || String(node.props.className).match(/topbar-(reset|update)(?=\s|$)|editor-mode-control/)[0]);
        };
        assert.deepEqual(order(sandbox.TopBar({ ...base, canChangeFocusMode: true })),
          ['초기화', '편집기 보기 모드', '되돌리기', '업데이트', 'topbar-more']);
        // Empty home: no material controls, reset still leads.
        assert.deepEqual(order(sandbox.TopBar({ ...base, total: 0, hasMaterials: false, canUndo: false })),
          ['초기화', '업데이트', 'topbar-more']);
        """)

    def test_primary_output_and_recognition_state_follow_the_current_work(self) -> None:
        run_node(r"""
        const onPublish = () => {};
        for (const [view, editingActive, primary] of [['board', false, true], ['board', true, false], ['review', false, false]]) {
          const tree = sandbox.TopBar({ ...base, onPublish, view, editingActive });
          const publish = walk(tree).find(node => node.props.onClick === onPublish);
          assert.equal(publish.props.className.split(' ').includes('primary'), primary);
          assert.equal(text(tree).includes('문항 인식 중'), false);
        }
        const working = sandbox.TopBar({ ...base, recognitionBusy: true });
        assert.equal(text(working).includes('문항 인식 중'), true);
        const currentSteps = walk(working).filter(node => node.props['aria-current'] === 'step');
        assert.equal(currentSteps.length, 0, 'background processing is not a mandatory workflow stage');
        assert.equal(sandbox.SessionRecognitionAction({ sourcePageCount: 0, onRecognizeSession() {} }), null);
        const recognition = sandbox.SessionRecognitionAction({ sourcePageCount: 3, canRecognizeSession: false,
          onRecognizeSession() {}, recognitionDisabledReason: '연결 설정 필요', sessionEditState: { dirty: true } });
        assert.equal(walk(recognition).find(node => node.type === 'button').props.disabled, true);
        assert.ok(text(recognition).includes('연결 설정 필요'));
        assert.ok(text(recognition).includes('변경한 수업 내용'));
        """)

    def test_topbar_removes_stepper_but_keeps_optional_review_and_return_actions(self) -> None:
        run_node(r"""
        const destinations = [];
        const render = extra => sandbox.TopBar({ ...base, setView: view => destinations.push(view), ...extra });
        const items = tree => walk(tree).find(node => node.props.id === 'topbar-more').props.items;
        for (const view of ['board', 'review']) {
          const tree = render({ view });
          assert.equal(walk(tree).some(n => /workflow-(progress|step)/.test(n.props.className || '')), false);
          assert.equal(walk(tree).some(n => n.props['aria-label'] === '자료 제작 진행 단계'), false);
          assert.equal(text(tree).includes('칠판·내보내기'), false);
        }
        const review = items(render({ reviewAttentionCount: 3 })).find(item => item.id === 'review');
        assert.equal(review.label, '문항 검수 열기');
        assert.ok(review.description.includes('확인 필요 3개'));
        review.onSelect();
        const board = items(render({ view: 'review', total: 0 })).find(item => item.id === 'board');
        assert.equal(board.label, '칠판으로 돌아가기');
        board.onSelect();
        assert.deepEqual(destinations, ['review', 'board']);
        assert.equal(items(render({ reviewAvailable: false })).some(item => item.id === 'review'), false);
        assert.ok(items(render({ reviewAttentionCount: 0 })).some(item => item.id === 'review'),
          'manual review remains reachable even when there are no warnings');
        """)

    def test_action_menu_keyboard_focus_disabled_actions_and_dismissal(self) -> None:
        run_node(r"""
        let focused = null;
        let calls = 0;
        const props = { id: 'test', label: '작업', items: [
          { id: 'first', label: '첫째', onSelect: () => { calls++; } },
          { id: 'blocked', label: '실행 불가', disabled: true, onSelect: () => { calls += 100; } },
          { id: 'last', label: '마지막', onSelect: () => { calls++; } },
        ] };
        function render() {
          hooks.cleanup.forEach(cleanup => cleanup?.()); hooks.cleanup = [];
          hooks.index = 0; hooks.effects = [];
          const tree = sandbox.ToolbarActionMenu(props);
          const trigger = walk(tree).find(node => node.props['aria-haspopup'] === 'menu');
          const popup = walk(tree).find(node => node.props.role === 'menu');
          const rows = walk(tree).filter(node => node.props.role === 'menuitem');
          const triggerNode = { kind: 'trigger', focus() { focused = this; } };
          const rowNodes = rows.map((row, index) => ({ index, focus() { focused = this; } }));
          const popupNode = { querySelectorAll: () => rowNodes };
          const allNodes = [triggerNode, popupNode, ...rowNodes];
          tree.props.ref.current = { contains: target => allNodes.includes(target) };
          trigger.props.ref.current = triggerNode;
          if (popup) popup.props.ref.current = popupNode;
          hooks.cleanup = hooks.effects.map(effect => effect());
          return { tree, trigger, popup, rows, rowNodes, popupNode, triggerNode };
        }
        let view = render();
        view.trigger.props.onKeyDown(keyEvent('ArrowDown'));
        view = render();
        assert.equal(focused.index, 0, 'opening must focus the first action');
        for (const [key, expected] of [['ArrowDown', 1], ['End', 2], ['ArrowDown', 0], ['ArrowUp', 2], ['Home', 0]]) {
          const event = keyEvent(key, focused, view.popupNode);
          view.popup.props.onKeyDown(event);
          assert.equal(focused.index, expected);
          assert.equal(event.prevented, true);
        }
        view.rows[1].props.onClick();
        assert.equal(calls, 0, 'disabled menu items remain readable but cannot execute');
        const escape = keyEvent('Escape', focused, view.popupNode);
        view.popup.props.onKeyDown(escape);
        assert.equal(focused.kind, 'trigger');
        assert.equal(escape.stopped, true, 'Escape must not also dismiss the editor');
        view = render(); assert.equal(view.popup, undefined);
        view.trigger.props.onKeyDown(keyEvent('ArrowUp'));
        view = render(); assert.equal(focused.index, 2);
        const tab = keyEvent('Tab', focused, view.popupNode);
        view.popup.props.onKeyDown(tab);
        assert.equal(focused.kind, 'trigger');
        assert.equal(tab.prevented, false, 'native Tab must continue from the trigger');
        view = render(); assert.equal(view.popup, undefined);
        view.trigger.props.onClick(); view = render();
        view.rows[0].props.onClick();
        assert.equal(calls, 1); assert.equal(focused.kind, 'trigger');
        view = render(); assert.equal(view.popup, undefined);
        view.trigger.props.onClick(); view = render();
        listeners.get('pointerdown')({ target: {} });
        view = render(); assert.equal(view.popup, undefined);
        assert.equal(listeners.size, 0);
        """)


if __name__ == "__main__":
    unittest.main()
