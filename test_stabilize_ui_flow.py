from __future__ import annotations

import unittest

from test_ui_home_flows import NODE_SETUP, run_node


class TestStabilizeUiFlow(unittest.TestCase):
    def test_board_edits_wait_for_running_ai_jobs_instead_of_hanging(self) -> None:
        run_node(NODE_SETUP + r"""
        const toasts = [], restores = [];
        const sandbox = {
          session: { problems: [] }, items: [], fileName: '수업', boardColumns: 1, layoutGapMode: 'compact',
          useCallback: fn => fn, mutatingRef: { current: false }, adoptMutatedSession() {}, refreshSessionHistory() {},
          hasRunningBackgroundJobs: true, showToast: message => toasts.push(message), showSimpleErrorToast() {},
          postRestore: async snapshot => { restores.push(snapshot); },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const mutateSession = useCallback(', '  const retryAiSession = useCallback')
          + '\nglobalThis.mutateSession = mutateSession;', sandbox);
        (async () => {
          assert.equal(await sandbox.mutateSession('exclude', { problemId: 'p1' }), null);
          assert.equal(restores.length, 0, 'the edit must not queue behind the AI job lock');
          assert.equal(sandbox.mutatingRef.current, false);
          assert.match(toasts[0], /진행 중인 작업이 끝난 뒤/);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_startup_update_check_is_silent_but_reports_a_failed_last_update_once(self) -> None:
        run_node(NODE_SETUP + r"""
        const toasts = [];
        const sandbox = {
          useCallback: fn => fn, updateCheckInFlightRef: { current: false }, setUpdateBusy() {}, setUpdateInfo() {},
          fetchAppUpdateStatus: async () => ({ channelStatus: 'up_to_date', lastUpdateFailed: true }),
          showToast: message => toasts.push(message), showSimpleErrorToast() {},
        };
        vm.createContext(sandbox);
        vm.runInContext(section('const UPDATE_CHANNEL_ERROR_COPY', 'function isUpdateArchitectureMismatch(')
          + section('  const updateStatusToast =', '  const openUpdateSettings =')
          + '\nglobalThis.checkForUpdates = checkForUpdates;', sandbox);
        (async () => {
          await sandbox.checkForUpdates({ silent: true });
          assert.equal(toasts.length, 1);
          assert.match(toasts[0], /지난 업데이트를 마치지 못했어요/);
          sandbox.fetchAppUpdateStatus = async () => ({ channelStatus: 'up_to_date' });
          await sandbox.checkForUpdates({ silent: true });
          assert.equal(toasts.length, 1, 'ordinary silent checks stay quiet');
          assert.match(source, /window\.setTimeout\(\(\) => \{ void checkForUpdates\(\{ silent: true \}\); \}, 3000\)/);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_tab_close_and_quit_warn_only_about_real_unsaved_or_running_work(self) -> None:
        run_node(NODE_SETUP + r"""
        const listeners = new Map(), prompts = [];
        const sandbox = {
          useRef: value => ({ current: value }), useCallback: fn => fn,
          useEffect: fn => { sandbox.cleanup = fn(); }, showToast() {}, showSimpleErrorToast() {},
          window: {
            addEventListener: (type, fn) => listeners.set(type, fn),
            removeEventListener: type => listeners.delete(type),
            confirm: message => { prompts.push(message); return false; },
          },
          postShutdown: async () => { throw new Error('declined quits must not stop the app'); },
        };
        vm.createContext(sandbox);
        const code = section('  // Warn before the tab closes', '  const refreshSession = useCallback(')
          + '\nglobalThis.shutdownApp = shutdownApp;';
        const render = state => { listeners.clear(); Object.assign(sandbox, state); vm.runInContext('{' + code + '}', sandbox); };
        render({ sessionEditState: { dirty: false }, workInProgress: false });
        assert.equal(listeners.has('beforeunload'), false, 'a saved idle board closes without a prompt');
        render({ sessionEditState: { dirty: true }, workInProgress: false });
        const event = { preventDefault() { event.prevented = true; } };
        listeners.get('beforeunload')(event);
        assert.equal(event.prevented, true);
        (async () => {
          await sandbox.shutdownApp();
          assert.match(prompts[0], /저장하지 않은 수업 변경/);
          render({ sessionEditState: { dirty: false }, workInProgress: true });
          await sandbox.shutdownApp();
          assert.match(prompts[1], /진행 중인 작업이 있어요/);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_unsupported_dropped_files_stay_out_of_the_queue(self) -> None:
        run_node(NODE_SETUP + r"""
        const toasts = [];
        const sandbox = {
          useCallback: fn => fn, pendingFilesRef: { current: [] }, autoProcessKeysRef: { current: new Set() },
          pendingFileKeysRef: { current: new Set() }, queueGenerationRef: { current: 0 }, items: [],
          fileInputRef: { current: { value: 'picked' } }, setPendingFiles() {}, setRecognitionReview() {},
          selectPendingFile() {}, showToast: message => toasts.push(message), session: null, runtimeDiagnostics: null,
        };
        vm.createContext(sandbox);
        vm.runInContext(section('function fileQueueKey(', 'function sourceFileExtension(')
          + section('  const setPendingFilesTracked = useCallback(', '  const queueRequestIsCurrent =')
          + section('  const handleFiles = (fileList) => {', '  const removePendingFile =')
          + '\nglobalThis.handleFiles = handleFiles;', sandbox);
        assert.equal(sandbox.handleFiles([{ name: '시험지.pdf', size: 1 }, { name: '정답.docx', size: 2 }]), 1);
        assert.equal(sandbox.pendingFilesRef.current.map(file => file.name).join(), '시험지.pdf');
        assert.match(toasts[0], /지원하지 않는 파일 1개는 빼고/);
        assert.equal(sandbox.handleFiles([{ name: '묶음.zip', size: 3 }]), 0);
        assert.equal(sandbox.pendingFilesRef.current.length, 1);
        """)

    def test_failed_open_folder_tells_the_user_instead_of_only_logging(self) -> None:
        run_node(NODE_SETUP + r"""
        const notices = [];
        const sandbox = {
          console: { warn() {} },
          window: { dispatchEvent: event => notices.push(event.detail) },
          CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
          fetch: async () => ({ ok: false, status: 404 }),
          readJsonResponse: async () => ({ ok: false, error: 'folder not found' }),
        };
        vm.createContext(sandbox);
        vm.runInContext(section('async function openOutputFolder(', 'async function saveUserSettings(')
          + section('function notifyOpenFailure(', 'function readSettingsSectionsOpen('), sandbox);
        (async () => {
          await sandbox.openOutputFolder('C:/out');
          assert.equal(JSON.stringify(notices), JSON.stringify([{ message: '폴더를 찾지 못했어요. EDB를 다시 제작해 주세요.', type: 'error' }]));
          assert.match(source, /addEventListener\('edb-notice', onNotice\)/);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


if __name__ == "__main__":
    unittest.main()
