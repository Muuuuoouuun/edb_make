from __future__ import annotations

import unittest

from test_ui_home_flows import NODE_SETUP, run_node


HOME_SETUP = NODE_SETUP + r"""
const Babel = require('./scripts/vendor/babel.min.js');
const calls = { upload: 0, sample: 0, restore: [], dropped: [] };
const sandbox = {
  React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
  Icon: new Proxy({}, { get: (_object, key) => key }),
  useState: initial => [initial, () => {}],
  ALTERNATE_MODIFIER_LABEL: 'Alt',
  PRIMARY_MODIFIER_LABEL: 'Ctrl',
  recentSessionCountLabel: entry => `${entry.problemCount ?? entry.problem_count ?? 0}개 문항`,
  formatPublishTime: value => `시간 ${value}`,
};
vm.createContext(sandbox);
const components = section('function HomeDashboard({', 'async function downloadPublishSummary(')
  + section('function hangulRuntimeStatusMeta(', 'function hangulRuntimeSummary(');
vm.runInContext(Babel.transform(components, { presets: ['react'] }).code, sandbox);
const nodes = value => !value ? [] : Array.isArray(value) ? value.flatMap(nodes)
  : typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
const text = value => !value ? '' : Array.isArray(value) ? value.map(text).join(' ')
  : typeof value === 'object' ? text(value.props?.children) : String(value);
const base = {
  recentSessions: [],
  runtimeDiagnostics: { hangul: { status: 'ready' } },
  onUpload: () => { calls.upload += 1; },
  onAddMockSample: () => { calls.sample += 1; },
  onRestoreRecentSession: id => calls.restore.push(id),
  onFilesDropped: files => calls.dropped.push(files),
};
const render = extra => sandbox.HomeDashboard({ ...base, ...extra });
const withClass = (tree, name) => nodes(tree).filter(node =>
  (node.props?.className || '').split(/\s+/).includes(name));
"""


class TestUiHomeRedesign(unittest.TestCase):
    def test_one_c_layout_has_three_steps_and_one_b_method_icons_without_auto_execution(self) -> None:
        run_node(HOME_SETUP + r"""
        const tree = render();
        assert.equal(withClass(tree, 'home-start-intro').length, 1);
        assert.equal(withClass(tree, 'home-start-launch').length, 1);
        const steps = withClass(tree, 'home-workflow-steps')[0];
        assert.equal(steps.type, 'ol');
        assert.equal(withClass(steps, 'home-step-card').length, 3);
        assert.ok(text(steps).includes('한 행에 한 문제씩'));
        const methods = withClass(tree, 'home-method-cards')[0];
        assert.equal(methods.type, 'ul');
        assert.equal(withClass(methods, 'home-method-card').length, 3);
        assert.ok(text(methods).includes('sparkle'));
        assert.ok(text(methods).includes('fileText'));
        const scissors = nodes(methods).find(node => node.props?.children?.includes('pen'));
        assert.ok(text(methods).includes('pen'));
        assert.equal(nodes(methods).filter(node => node.type === 'button').length, 3, 'method selection is explicit before upload');
        assert.deepEqual(calls, { upload: 0, sample: 0, restore: [], dropped: [] });
        """)

    def test_upload_and_sample_are_single_native_buttons(self) -> None:
        run_node(HOME_SETUP + r"""
        const tree = render({ canAddDummy: true });
        const upload = withClass(tree, 'home-hero-dropzone')[0];
        assert.equal(upload.type, 'button');
        assert.equal(upload.props.type, 'button');
        assert.equal(upload.props['aria-label'], '자료 파일 선택');
        assert.equal(nodes(upload).filter(node => node.type === 'button').length, 1,
          'the upload card must not contain nested buttons');
        assert.ok(nodes(tree).some(node => node.props?.id === upload.props['aria-describedby']));
        upload.props.onClick();
        assert.equal(calls.upload, 1);
        assert.ok(text(tree).includes('바로 처리해 칠판에 배치합니다'));
        const sample = withClass(tree, 'home-quick-card').find(node => node.type === 'button');
        assert.ok(sample);
        sample.props.onClick();
        assert.equal(calls.sample, 1);
        assert.equal(withClass(render({ canAddDummy: false }), 'home-quick-card')
          .filter(node => node.type === 'button').length, 0,
          'sample permission must still control the sample action');
        """)

    def test_recent_session_card_is_one_button_and_blocks_all_restore_while_busy(self) -> None:
        run_node(HOME_SETUP + r"""
        const recentSessions = [
          { id: 'a', sessionName: '수학', problemCount: 2 },
          { id: 'b', session_name: '예전 국어', problem_count: 3, updated_at: '2026-10-16' },
        ];
        const cards = withClass(render({ recentSessions }), 'home-recent-card');
        assert.equal(cards.length, 2);
        for (const card of cards) {
          assert.equal(card.type, 'button');
          assert.equal(card.props.type, 'button');
          assert.equal(card.props.disabled, false);
          assert.equal(nodes(card).filter(node => node.type === 'button').length, 1);
        }
        assert.equal(cards[1].props['aria-label'], '예전 국어 열기');
        assert.ok(text(cards[1]).includes('시간 2026-10-16'));
        cards[1].props.onClick();
        assert.deepEqual(calls.restore, ['b']);
        for (const busyState of [{ restoringSessionId: 'a' }, { recentSessionRestoreBlocked: true }]) {
          const blocked = withClass(render({ recentSessions, ...busyState }), 'home-recent-card');
          for (const card of blocked) {
            assert.equal(card.props.disabled, true);
            card.props.onClick();
          }
        }
        assert.deepEqual(calls.restore, ['b']);
        const restoring = withClass(render({ recentSessions, restoringSessionId: 'a' }), 'home-recent-card')[0];
        assert.equal(restoring.props['aria-busy'], true);
        assert.ok(text(restoring).includes('여는 중'));
        """)

    def test_file_drop_is_queued_once_and_non_file_drop_does_nothing(self) -> None:
        run_node(HOME_SETUP + r"""
        const tree = render();
        let prevented = 0;
        const files = [{ name: '수학.pdf' }, { name: '국어.png' }];
        tree.props.onDrop({ preventDefault: () => { prevented += 1; }, dataTransfer: { files } });
        assert.equal(prevented, 1);
        assert.equal(calls.dropped.length, 1);
        assert.equal(calls.dropped[0].length, 2);
        assert.equal(calls.dropped[0][0], files[0]);
        tree.props.onDrop({ preventDefault() {}, dataTransfer: { files: [] } });
        tree.props.onDrop({ preventDefault() {} });
        assert.equal(calls.dropped.length, 1);
        tree.props.onDragOver({ preventDefault() { throw new Error('not a file'); }, dataTransfer: { types: ['text/plain'] } });
        assert.equal(calls.upload, 0, 'dropping must not open a second file picker');
        """)

    def test_recent_history_can_reach_every_entry_from_home(self) -> None:
        run_node(HOME_SETUP + r"""
        let limit = 6;
        sandbox.useState = initial => initial === 6
          ? [limit, update => { limit = typeof update === 'function' ? update(limit) : update; }]
          : [initial, () => {}];
        const recentSessions = Array.from({ length: 14 }, (_, index) => ({
          id: `saved-${index}`, sessionName: `수업 ${index + 1}`, problemCount: index + 1,
        }));
        let tree = render({ recentSessions });
        assert.equal(withClass(tree, 'home-recent-card').length, 6);
        withClass(tree, 'home-recent-more')[0].props.onClick();
        tree = render({ recentSessions });
        assert.equal(withClass(tree, 'home-recent-card').length, 12);
        withClass(tree, 'home-recent-more')[0].props.onClick();
        tree = render({ recentSessions });
        const cards = withClass(tree, 'home-recent-card');
        assert.equal(cards.length, 14);
        assert.equal(withClass(tree, 'home-recent-more').length, 0);
        cards.at(-1).props.onClick();
        assert.deepEqual(calls.restore, ['saved-13']);
        assert.equal(recentSessions.length, 14, 'home pagination must not mutate stored history');
        """)


if __name__ == '__main__':
    unittest.main()
