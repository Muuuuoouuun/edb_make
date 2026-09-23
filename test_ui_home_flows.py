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
const flush = () => new Promise(resolve => setImmediate(resolve));
"""


class TestUiHomeFlows(unittest.TestCase):
    def test_recent_history_can_find_entries_after_the_first_five(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = { useMemo: fn => fn() };
        vm.createContext(sandbox);
        vm.runInContext(section('function filterRecentSessions(', 'function hasReviewPages('), sandbox);
        const history = Array.from({ length: 10 }, (_, index) => ({
          id: `history-${index}`, sessionName: `수학 ${index + 1}차`,
        }));
        history[8].sessionName = '국어 Café 2026'.normalize('NFD');
        history[9] = { id: 'old-format', session_name: 'Science Final' };
        sandbox.recentSessions = history;
        sandbox.recentSessionQuery = '';
        sandbox.recentSessionLimit = 5;
        const renderSelection = section('  const filteredRecentSessions = useMemo(', '  const hasSessionItems =');
        const renderedIds = () => vm.runInContext('(() => {' + renderSelection
          + 'return visibleRecentSessions.map(entry => entry.id); })()', sandbox);
        assert.equal(renderedIds().length, 5);
        sandbox.recentSessionLimit += 5;
        assert.equal(renderedIds().length, 10, 'show more must reach every stored entry');
        sandbox.recentSessionLimit = 5;
        sandbox.recentSessionQuery = '  CAFÉ  국어  ';
        assert.equal(JSON.stringify(renderedIds()), '["history-8"]');
        sandbox.recentSessionQuery = 'ＦＩＮＡＬ science';
        assert.equal(JSON.stringify(renderedIds()), '["old-format"]');
        sandbox.recentSessionQuery = '없는 수업';
        assert.equal(renderedIds().length, 0);
        sandbox.recentSessionQuery = '';
        assert.equal(renderedIds().length, 5, 'clearing search restores the first page');
        assert.equal(history.length, 10, 'search must not mutate stored history');
        """)

    def test_duplicate_upload_preserves_pending_recognition_and_queue_generation(self) -> None:
        run_node(NODE_SETUP + r"""
        const toasts = [];
        let reviewClears = 0;
        let selected = null;
        const sandbox = {
          useCallback: fn => fn,
          pendingFilesRef: { current: [] },
          pendingFileKeysRef: { current: new Set() },
          queueGenerationRef: { current: 0 },
          fileInputRef: { current: { value: 'picked' } },
          setPendingFiles: files => { sandbox.renderedFiles = files; },
          setRecognitionReview: () => { reviewClears += 1; },
          selectPendingFile: key => { selected = key; },
          showToast: message => toasts.push(message),
        };
        vm.createContext(sandbox);
        vm.runInContext(section('function fileQueueKey(', 'function sourceFileExtension(')
          + section('  const setPendingFilesTracked = useCallback(', '  const queueRequestIsCurrent =')
          + section('  const handleFiles = (fileList) => {', '  const removePendingFile =')
          + '\nglobalThis.handleFiles = handleFiles;\n', sandbox);
        const a = { name: 'A.pdf', size: 20, lastModified: 1 };
        const b = { name: 'B.png', size: 30, lastModified: 2 };
        sandbox.handleFiles([a, a, b]);
        assert.equal(sandbox.renderedFiles.length, 2);
        assert.ok(toasts.at(-1).startsWith('2개 파일'));
        assert.ok(toasts.at(-1).includes('중복 1개 제외'));
        assert.equal(selected, 'A.pdf::20::1');
        assert.equal(sandbox.fileInputRef.current.value, '');
        const generation = sandbox.queueGenerationRef.current;
        const queue = sandbox.pendingFilesRef.current;
        sandbox.handleFiles([{ ...a }, { ...b }]);
        assert.equal(sandbox.pendingFilesRef.current, queue);
        assert.equal(sandbox.queueGenerationRef.current, generation);
        assert.equal(reviewClears, 1, 'duplicate pick must preserve the recognition review');
        assert.equal(toasts.at(-1), '이미 대기열에 있는 파일입니다');
        sandbox.handleFiles([{ name: 'C.hwp', size: 10, lastModified: 3 }]);
        sandbox.handleFiles([{ name: 'D.pdf', size: 40, lastModified: 4 }]);
        assert.equal(sandbox.pendingFilesRef.current.length, 4, 'back-to-back additions must retain both files');
        assert.equal(sandbox.queueGenerationRef.current, generation + 2);
        """)

    def test_queue_keyboard_does_not_cancel_nested_button_activation(self) -> None:
        run_node(NODE_SETUP + r"""
        const row = section('className={`source-queue-row ', 'className="source-queue-actions"');
        const body = row.split('onKeyDown={e => {')[1].split('\n                  }}')[0];
        let selections = 0;
        let prevented = 0;
        const sandbox = { key: 'queued-file', onSelectPendingFile: () => { selections += 1; } };
        vm.createContext(sandbox);
        const onKeyDown = vm.runInContext('(e => {' + body + '})', sandbox);
        const rowTarget = {};
        for (const key of ['Enter', ' ']) {
          onKeyDown({ key, target: {}, currentTarget: rowTarget, preventDefault: () => { prevented += 1; } });
        }
        assert.equal(prevented, 0, 'native button keyboard activation must remain available');
        assert.equal(selections, 0, 'child button keys must not select the parent row');
        for (const key of ['Enter', ' ']) {
          onKeyDown({ key, target: rowTarget, currentTarget: rowTarget, preventDefault: () => { prevented += 1; } });
        }
        assert.equal(selections, 2);
        assert.equal(prevented, 2);
        """)

    def test_initial_load_failure_does_not_mark_an_unknown_session_ready(self) -> None:
        run_node(NODE_SETUP + r"""
        const effect = section('  // initial session fetch', '  useEffect(() => {\n    refreshSessionHistory();');
        async function load(fetchLatestSession) {
          const state = { ready: false, error: null, session: null };
          const sandbox = {
            fetchLatestSession,
            useEffect: fn => { state.cleanup = fn(); },
            applySession: session => { state.session = session; },
            setInitialSessionLoaded: ready => { state.ready = ready; },
            setInitialSessionError: error => { state.error = error; },
            simpleToastErrorMessage: error => error.message,
            console: { warn() {} },
          };
          vm.runInNewContext(effect, sandbox);
          await flush();
          return state;
        }
        (async () => {
          const failed = await load(async () => { throw new Error('offline'); });
          assert.equal(failed.ready, false, 'failed hydration must not unlock upload processing');
          assert.equal(failed.error, 'offline');
          const empty = await load(async () => null);
          assert.equal(empty.ready, true, '404/no stored session is a valid ready state');
          assert.equal(empty.error, null);
          const saved = { problems: [{ id: 'saved' }] };
          const loaded = await load(async () => saved);
          assert.equal(loaded.ready, true);
          assert.equal(loaded.session, saved);
          let resolve;
          const late = await load(() => new Promise(done => { resolve = done; }));
          late.cleanup();
          resolve(saved);
          await flush();
          assert.equal(late.ready, false, 'unmounted initial fetch must not update state');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_retry_load_unlocks_queue_and_refreshes_history_after_an_initial_failure(self) -> None:
        run_node(NODE_SETUP + r"""
        const state = { ready: false, error: 'offline', historyLoads: 0, fetches: 0 };
        let resolveFetch;
        const sandbox = {
          useCallback: fn => fn,
          refreshInFlightRef: { current: false }, restoreInFlightRef: { current: false },
          mutatingRef: { current: false }, loading: null, hasRunningBackgroundJobs: false,
          initialSessionLoaded: false, usingMock: false, LAYOUT_GAP_MODE_GRID: 'grid',
          fetchLatestSession: () => { state.fetches += 1; return new Promise(resolve => { resolveFetch = resolve; }); },
          applySession: () => {}, refreshSessionHistory: () => { state.historyLoads += 1; },
          setInitialSessionLoaded: ready => { state.ready = ready; },
          setInitialSessionError: error => { state.error = error; },
          setRefreshing: value => { state.refreshing = value; },
          setSession() {}, setLayoutGapModeState() {}, setItems() {}, setActiveId() {},
          setPublished() {}, setView() {}, clearOperationRecovery() {}, showToast() {},
          showSimpleErrorToast() {}, simpleToastErrorMessage: error => error.message,
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const refreshSession = useCallback(', '  const restoreRecentSession =')
          + '\nglobalThis.refreshSession = refreshSession;', sandbox);
        (async () => {
          const pending = sandbox.refreshSession();
          await sandbox.refreshSession();
          assert.equal(state.fetches, 1, 'retry clicks must not race multiple loads');
          resolveFetch(null);
          await pending;
          assert.equal(state.ready, true);
          assert.equal(state.error, null);
          assert.equal(state.historyLoads, 1, 'empty latest state must still retry failed history');
          assert.equal(state.refreshing, false);
          assert.equal(sandbox.refreshInFlightRef.current, false);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_recent_restore_waits_for_initial_load_and_other_session_operations(self) -> None:
        run_node(NODE_SETUP + r"""
        let posts = 0;
        const sandbox = {
          useCallback: fn => fn,
          restoringSessionId: null, initialSessionLoaded: true, loading: null,
          hasRunningBackgroundJobs: false, hasPendingSessionConflict: false,
          session: null, items: [], fileName: 'new', boardColumns: 1,
          materializeSessionForItems: () => null,
          postRestoreSessionHistory: async () => { posts += 1; return { session: { problems: [{ id: 'p1' }] }, history: [] }; },
          applySession: () => true, setRecentSessionsAuthoritative() {}, activateOperationRecovery() {},
          clearOperationRecovery() {}, showToast() {}, setRestoringSessionId() {}, setLoading() {},
          setRecognitionReview() {}, setHistoryStack() {}, setReviewFocus() {},
        };
        const refs = ['restoreInFlightRef', 'refreshInFlightRef', 'resetInFlightRef', 'publishInFlightRef',
          'recognitionInFlightRef', 'queueRegistrationInFlightRef', 'sessionRecognitionInFlightRef', 'mutatingRef'];
        refs.forEach(name => { sandbox[name] = { current: false }; });
        vm.createContext(sandbox);
        vm.runInContext(section('  const restoreRecentSession = useCallback(', '  const triggerUpload =')
          + '\nglobalThis.restoreRecentSession = restoreRecentSession;', sandbox);
        (async () => {
          sandbox.initialSessionLoaded = false;
          assert.equal(await sandbox.restoreRecentSession('history-1'), false);
          sandbox.initialSessionLoaded = true;
          for (const name of refs) {
            sandbox[name].current = true;
            assert.equal(await sandbox.restoreRecentSession('history-1'), false, name);
            sandbox[name].current = false;
          }
          for (const name of ['loading', 'hasRunningBackgroundJobs', 'hasPendingSessionConflict']) {
            sandbox[name] = true;
            assert.equal(await sandbox.restoreRecentSession('history-1'), false, name);
            sandbox[name] = false;
          }
          assert.equal(posts, 0, 'busy restore must never mutate the server session');
          assert.equal(await sandbox.restoreRecentSession('history-1'), true);
          assert.equal(posts, 1);
          assert.equal(sandbox.restoreInFlightRef.current, false);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_home_dashboard_renders_for_empty_board_items(self) -> None:
        run_node(NODE_SETUP + r"""
        const boardStageSrc = section('function BoardStage({', 'function HomeDashboard({');
        assert.ok(boardStageSrc.includes('items.length === 0'), 'BoardStage must branch on items.length === 0');
        assert.ok(boardStageSrc.includes('<HomeDashboard'), 'BoardStage must render HomeDashboard when empty');
        assert.ok(boardStageSrc.includes('triggerUpload'), 'BoardStage must pass triggerUpload to HomeDashboard');
        assert.ok(boardStageSrc.includes('addMockSample'), 'BoardStage must pass addMockSample to HomeDashboard');

        const homeDashSrc = section('function HomeDashboard({', 'async function downloadPublishSummary(');
        assert.ok(homeDashSrc.includes('home-hero-dropzone'), 'HomeDashboard must include hero dropzone');
        assert.ok(homeDashSrc.includes('home-quick-cards'), 'HomeDashboard must include quick-action cards');
        assert.ok(homeDashSrc.includes('home-workflow-steps'), 'HomeDashboard must include 3-step workflow guide');
        assert.ok(homeDashSrc.includes('home-status-pills'), 'HomeDashboard must include status indicators');
        assert.ok(homeDashSrc.includes('recentSessions'), 'HomeDashboard must support recent session history');
        assert.ok(homeDashSrc.includes('onUpload'), 'HomeDashboard must support upload trigger');
        assert.ok(homeDashSrc.includes('onAddMockSample'), 'HomeDashboard must support mock sample exploration');
        """)


if __name__ == "__main__":
    unittest.main()

