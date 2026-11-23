from __future__ import annotations

import unittest

from test_ui_home_flows import NODE_SETUP, run_node


class TestStabilizeUiFeedback(unittest.TestCase):
    def test_error_and_long_toasts_stay_readable(self) -> None:
        run_node(NODE_SETUP + r"""
        const timers = [];
        const sandbox = {
          useCallback: fn => fn, toastTimerRef: { current: null }, actionToastTimerRef: { current: null },
          setToast() {}, setActionToast() {},
          window: { setTimeout: (_fn, ms) => timers.push(ms), clearTimeout() {} },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const showToast = useCallback(', '  const showActionToast =') + '\nglobalThis.showToast = showToast;', sandbox);
        sandbox.showToast('저장했어요');
        sandbox.showToast('실패', 'error');
        sandbox.showToast('가'.repeat(100));
        sandbox.showToast('가'.repeat(400), 'error');
        sandbox.showToast('직접 지정', { duration: 4500 });
        assert.deepEqual(timers, [2500, 6000, 8000, 10000, 4500]);
        """)

    def test_non_json_and_network_failures_keep_jargon_out_of_the_message(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = {};
        vm.createContext(sandbox);
        vm.runInContext(section('async function readJsonResponse(', 'function assertOkJson(')
          + section('function isNetworkRequestError(', 'async function probeLocalServerHealth('), sandbox);
        (async () => {
          const error = await sandbox.readJsonResponse({ status: 502, text: async () => '<!DOCTYPE html><p>Bad gateway</p>' }, '문제 추출 요청에 실패했습니다.')
            .then(() => null, err => err);
          assert.equal(error.message, '문제 추출 요청에 실패했습니다 · 앱을 다시 실행한 뒤 다시 시도해 주세요');
          assert.match(error.detail, /HTTP 502 · JSON 응답 아님: <!DOCTYPE html>/);
          assert.equal(sandbox.userErrorText(new TypeError('Failed to fetch')), '앱과 연결이 끊겼습니다. 앱을 다시 실행한 뒤 다시 시도해 주세요.');
          assert.equal(sandbox.userErrorText(new Error('파일이 손상됐습니다.')), '파일이 손상됐습니다.');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_render_crash_screen_offers_reload_and_hides_raw_error_in_details(self) -> None:
        run_node(NODE_SETUP + r"""
        let reloads = 0;
        const sandbox = {
          React: { Component: class { constructor(props) { this.props = props; } },
            createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
          window: { location: { reload: () => { reloads += 1; } } },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('class AppErrorBoundary', 'function requiredWindowHelper(') + '\nglobalThis.Boundary = AppErrorBoundary;', sandbox);
        const boundary = new sandbox.Boundary({});
        boundary.state = { error: new Error("Cannot read properties of undefined (reading 'id')") };
        const tree = boundary.render();
        const [title, guidance, button, details] = tree.props.children;
        assert.equal(tree.props.role, 'alert');
        assert.ok(!/Cannot read/.test(`${title.props.children} ${guidance.props.children}`));
        button.props.onClick();
        assert.equal(reloads, 1);
        assert.equal(details.type, 'details');
        assert.match(details.props.children[1], /Cannot read/);
        """)


if __name__ == "__main__":
    unittest.main()
