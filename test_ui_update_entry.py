from __future__ import annotations

import unittest

from test_ui_home_flows import NODE_SETUP, run_node


UPDATE_SETUP = NODE_SETUP + r"""
const pendingRequests = [];
const busyStates = [];
const storedInfo = [];
const toasts = [];
const errors = [];
const warnings = [];
let revealed = 0;
let panelRequest = 0;
let installs = 0;
let openedUrls = 0;
const sandbox = {
  useCallback: callback => callback,
  updateCheckInFlightRef: { current: false },
  setUpdateBusy: value => busyStates.push(value),
  setUpdateInfo: value => storedInfo.push(value),
  fetchAppUpdateStatus: () => new Promise((resolve, reject) => pendingRequests.push({ resolve, reject })),
  showToast: message => toasts.push(message),
  showSimpleErrorToast: (error, message) => errors.push({ error, message }),
  isUpdateArchitectureMismatch: () => false,
  updateArchitectureNotice: () => 'architecture mismatch',
  revealSidePanel: () => { revealed += 1; },
  items: [{ id: 'p1' }], pendingFiles: [],
  setHomeUpdateOpen: value => { sandbox.homeUpdateOpen = value; },
  setUpdatePanelRequest: update => { panelRequest = update(panelRequest); },
  postAppUpdateInstall: () => { installs += 1; throw new Error('entry must not install'); },
  installUpdateVersion: () => { installs += 1; throw new Error('entry must not install'); },
  postOpenUrl: () => { openedUrls += 1; throw new Error('entry must not open a URL'); },
  openUpdatePage: () => { openedUrls += 1; throw new Error('entry must not open a URL'); },
  console: { warn: (...args) => warnings.push(args) },
};
vm.createContext(sandbox);
vm.runInContext(section('  const updateStatusToast =', '  const openUpdatePage =')
  + '\nglobalThis.checkForUpdates = checkForUpdates;'
  + '\nglobalThis.openUpdateSettings = openUpdateSettings;', sandbox);
"""


PENDING_PREVIEW_SETUP = NODE_SETUP + r"""
const Babel = require('./scripts/vendor/babel.min.js');
const processed = [];
const sandbox = {
  React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
  Icon: new Proxy({}, { get: (_object, key) => key }),
  useState: initial => [initial, () => {}],
  useEffect: () => {},
  canPreviewImageFile: () => false,
  isPdfFile: () => true,
  isHwpFile: () => false,
  sourceFileExtension: () => 'pdf',
  sourceFileKindLabel: () => 'PDF',
  formatBytes: value => String(value),
  pendingFilePreviewLabel: () => '파일 미리보기',
  SplitModePicker: 'split-mode-picker',
  normalizeSplitMode: value => value,
  splitModeMeta: () => ({ label: '문항 인식', icon: 'scan' }),
  splitActionLabel: () => '분할하기',
};
vm.createContext(sandbox);
vm.runInContext(Babel.transform(
  section('function PendingFilePreview({', '// ─── backend helpers')
  + section('function fileQueueKey(', 'function appendUniquePendingFiles('),
  { presets: ['react'] }).code, sandbox);
const nodes = value => !value ? [] : Array.isArray(value) ? value.flatMap(nodes)
  : typeof value === 'object' ? [value, ...nodes(value.props?.children)] : [];
const text = value => !value ? '' : Array.isArray(value) ? value.map(text).join(' ')
  : typeof value === 'object' ? text(value.props?.children) : String(value);
const file = { name: '공통 지문.pdf', size: 1024, lastModified: 77 };
const render = extra => sandbox.PendingFilePreview({
  file, queueBusy: false, splitMode: 'recognize',
  processQueuedFiles: (...args) => processed.push(args), ...extra,
});
const passageButton = tree => nodes(tree).find(node => node.type === 'button' && text(node).includes('지문만 추출'));
"""


class TestUiUpdateEntry(unittest.TestCase):
    def test_update_check_deduplicates_in_flight_and_reports_unconfigured_truthfully(self) -> None:
        run_node(UPDATE_SETUP + r"""
        (async () => {
          const first = sandbox.checkForUpdates();
          assert.equal(sandbox.updateCheckInFlightRef.current, true);
          assert.deepEqual(busyStates, [true]);
          assert.equal(pendingRequests.length, 1);
          assert.equal(await sandbox.checkForUpdates(), null);
          assert.equal(await sandbox.checkForUpdates({ silent: true }), null);
          assert.equal(pendingRequests.length, 1, 'repeat clicks must share the in-flight guard');
          assert.deepEqual(busyStates, [true], 'a rejected duplicate must not clear the active busy state');
          const info = { channelStatus: 'not_configured', currentVersion: '0.2.3', updateAvailable: false };
          pendingRequests[0].resolve(info);
          assert.equal(await first, info);
          assert.equal(storedInfo[0], info);
          assert.deepEqual(toasts, ['업데이트 채널이 아직 설정되지 않았습니다']);
          assert.equal(toasts.some(message => message.includes('최신 버전')), false);
          assert.equal(sandbox.updateCheckInFlightRef.current, false);
          assert.deepEqual(busyStates, [true, false]);
          assert.equal(errors.length, 0);
          const second = sandbox.checkForUpdates({ silent: true });
          assert.equal(pendingRequests.length, 2, 'a completed check must permit a fresh check');
          pendingRequests[1].resolve({ channelStatus: 'up_to_date' });
          await second;
          assert.equal(toasts.length, 1, 'silent refresh must not add a toast');
          assert.deepEqual(busyStates, [true, false, true, false]);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_update_check_error_and_silent_error_always_release_busy_state(self) -> None:
        run_node(UPDATE_SETUP + r"""
        (async () => {
          const failure = new Error('feed unavailable');
          const first = sandbox.checkForUpdates();
          pendingRequests[0].reject(failure);
          assert.equal(await first, null);
          assert.equal(errors.length, 1);
          assert.equal(errors[0].error, failure);
          assert.equal(errors[0].message, '업데이트 확인 실패');
          assert.equal(storedInfo.length, 0);
          assert.equal(sandbox.updateCheckInFlightRef.current, false);
          assert.deepEqual(busyStates, [true, false]);
          const silent = sandbox.checkForUpdates({ silent: true });
          pendingRequests[1].reject(new Error('still unavailable'));
          assert.equal(await silent, null);
          assert.equal(errors.length, 1, 'silent checks must not display failure toasts');
          assert.equal(toasts.length, 0);
          assert.equal(warnings.length, 2);
          assert.equal(sandbox.updateCheckInFlightRef.current, false);
          assert.deepEqual(busyStates, [true, false, true, false]);
          const retry = sandbox.checkForUpdates();
          pendingRequests[2].resolve({ channelStatus: 'update_available', updateAvailable: true, latest: { version: '0.2.4' } });
          assert.equal((await retry).latest.version, '0.2.4');
          assert.deepEqual(toasts, ['업데이트 가능 · 0.2.4']);
          assert.equal(sandbox.updateCheckInFlightRef.current, false);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_empty_home_update_entry_opens_only_the_dialog_and_checks_silently(self) -> None:
        run_node(UPDATE_SETUP + r"""
        (async () => {
          sandbox.items = [];
          sandbox.openUpdateSettings();
          sandbox.openUpdateSettings();
          assert.equal(sandbox.homeUpdateOpen, true);
          assert.equal(revealed, 0, 'file-free home must never reveal the editing side panel');
          assert.equal(panelRequest, 0);
          assert.equal(pendingRequests.length, 1, 'modal entry shares the existing update request guard');
          pendingRequests[0].resolve({ channelStatus: 'not_configured' });
          await flush();
          assert.equal(installs, 0); assert.equal(openedUrls, 0); assert.equal(toasts.length, 0);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_loaded_update_entry_uses_the_same_modal_without_changing_editor_tabs(self) -> None:
        run_node(UPDATE_SETUP + r"""
        (async () => {
          sandbox.openUpdateSettings();
          sandbox.openUpdateSettings();
          assert.equal(revealed, 0);
          assert.equal(sandbox.homeUpdateOpen, true);
          assert.equal(panelRequest, 0, 'update entry must preserve the current editor tab');
          assert.equal(pendingRequests.length, 1);
          pendingRequests[0].resolve({
            channelStatus: 'update_available', updateAvailable: true,
            automaticUpdateEnabled: true, automaticUpdateReady: true,
            downloadUrl: 'https://example.invalid/setup.exe', latest: { version: '0.2.4' },
          });
          await flush();
          assert.equal(storedInfo.length, 1);
          assert.equal(toasts.length, 0);
          assert.equal(errors.length, 0);
          assert.equal(installs, 0, 'opening update settings must never authorize installation');
          assert.equal(openedUrls, 0, 'opening update settings must never launch an external download');
          assert.equal(sandbox.updateCheckInFlightRef.current, false);
          const topbar = section('function TopBar({', 'function ReviewToolbarGroup(');
          assert.match(topbar, /className="btn ghost topbar-update"[\s\S]*?onClick=\{onOpenUpdateSettings\}/);
          assert.match(source, /onOpenUpdateSettings=\{openUpdateSettings\}/);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_passage_button_processes_only_the_exact_previewed_file_key(self) -> None:
        run_node(PENDING_PREVIEW_SETUP + r"""
        const first = passageButton(render());
        assert.ok(first, 'the passage-only control must remain rendered');
        assert.equal(typeof first.props.onClick, 'function', 'button must reference a defined handler');
        assert.equal(first.props.disabled, false);
        first.props.onClick();
        assert.deepEqual(processed, [['passage-only', '공통 지문.pdf::1024::77']]);
        const sameNameDifferentFile = { ...file, size: 2048, lastModified: 78 };
        passageButton(render({ file: sameNameDifferentFile })).props.onClick();
        assert.deepEqual(processed[1], ['passage-only', '공통 지문.pdf::2048::78']);
        assert.equal(processed.every(args => args.length === 2), true,
          'target key must be provided, never process the entire pending queue');
        """)

    def test_passage_button_respects_queue_busy_and_missing_handler(self) -> None:
        run_node(PENDING_PREVIEW_SETUP + r"""
        const busy = passageButton(render({ queueBusy: true }));
        assert.equal(busy.props.disabled, true);
        busy.props.onClick();
        assert.equal(processed.length, 0, 'the handler must also guard queued/busy invocation');
        const missing = passageButton(render({ processQueuedFiles: undefined }));
        assert.doesNotThrow(() => missing.props.onClick());
        assert.equal(processed.length, 0);
        assert.equal(render({ file: null }), null);
        """)

class TestUnifiedUpdateDialog(unittest.TestCase):
    def test_modal_restores_focus_traps_tab_and_does_not_send_edit_shortcuts_to_canvas(self):
        run_node(NODE_SETUP + r"""
        const Babel = require('./scripts/vendor/babel.min.js');
        let effect, opened=0, closed=0, restored=0, dismissed=0, stopped=0;
        const first = { focus() { sandbox.document.activeElement=first; } };
        const last = { focus() { sandbox.document.activeElement=last; } };
        const trigger = {isConnected:true, focus(){restored++}};
        const dialog = {showModal(){opened++},close(){closed++},querySelectorAll(){return [first,last]}};
        const sandbox = {
          React:{createElement:(type,props,...children)=>({type,props:{...props,children}})},
          useRef:()=>({current:dialog}),useLayoutEffect:fn=>{effect=fn},
          document:{activeElement:trigger}, Icon:{close:'close'},
        };
        vm.createContext(sandbox);
        vm.runInContext(Babel.transform(section('function HomeUpdateDialog(', 'function BugReportDialog('),{presets:['react']}).code,sandbox);
        const render = busy => sandbox.HomeUpdateDialog({open:true,busy,onClose:()=>dismissed++});
        let tree=render(false), cleanup=effect();
        assert.equal(opened,1);
        let prevented=0;
        for (const key of ['Delete','s','Enter','Escape']) tree.props.onKeyDown({key,stopPropagation(){stopped++}});
        assert.equal(stopped,4);
        sandbox.document.activeElement=last;
        tree.props.onKeyDown({key:'Tab',stopPropagation(){},preventDefault(){prevented++}});
        assert.equal(sandbox.document.activeElement,first);
        tree.props.onKeyDown({key:'Tab',shiftKey:true,stopPropagation(){},preventDefault(){prevented++}});
        assert.equal(sandbox.document.activeElement,last);
        assert.equal(prevented,2);
        tree.props.onCancel({preventDefault(){}}); assert.equal(dismissed,1);
        tree=render(true);tree.props.onCancel({preventDefault(){}});assert.equal(dismissed,1);
        cleanup();assert.equal(closed,1);assert.equal(restored,1);
        """)


if __name__ == '__main__':
    unittest.main()
