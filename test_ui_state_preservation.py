from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


STATE_SETUP = NODE_SETUP + r"""
function stateSandbox() {
  const sandbox = placementSandbox();
  assert.equal(typeof sandbox.sessionEditorDraftSignature, 'function');
  const { session, items } = savedFixture(sandbox);
  const state = { confirms: 0, fetches: 0, historyFetches: 0, historyPosts: 0, restores: 0, errors: [] };
  Object.assign(sandbox, {
    ...{ session, items }, fileName: session.session_name, boardColumns: 1, layoutGapMode: 'classin-grid',
    usingMock: false, initialSessionLoaded: true, initialSessionError: null, loading: null,
    hasRunningBackgroundJobs: false, hasPendingSessionConflict: false, restoringSessionId: null,
    view: 'board', activeId: 'p2', reviewEditorActive: false, mutating: false, published: true, refreshing: false,
    historyStack: [], historyStackRef: { current: [] },
    useCallback: fn => fn, LAYOUT_GAP_MODE_GRID: 'classin-grid', UNDO_HISTORY_LIMIT: 20,
    appendBoundedHistory: require('./ui_prototype/reorder.js').appendBoundedHistory,
    window: { confirm() { state.confirms += 1; return state.confirmed !== false; } },
    fetchLatestSession: async () => { state.fetches += 1; if (state.fetchError) throw state.fetchError; return state.latestSession || null; },
    refreshSessionHistory: () => { state.historyFetches += 1; },
    postRestoreSessionHistory: async () => { state.historyPosts += 1; return { session: state.latestSession, history: [] }; },
    postRestore: async snapshot => { state.restores += 1; if (state.restoreError) throw state.restoreError; return state.restoreResponse === undefined ? snapshot : state.restoreResponse; },
    applySession: next => {
      if (!next?.problems?.length) return false;
      sandbox.session = next;
      sandbox.items = sandbox.reflowItemsForBoardOrder(next.problems.map(sandbox.mapProblemToItem), 1.2, 1,
        next.layoutGapMode || 'classin-grid');
      sandbox.fileName = next.session_name || sandbox.fileName;
      sandbox.activeId = next.problems[0].id;
      sandbox.view = 'review';
      return true;
    },
    clearOperationRecovery() {}, setRecentSessionsAuthoritative() {}, setRecognitionReview() {}, setReviewFocus() {},
    activateOperationRecovery() {}, showToast() {},
    showSimpleErrorToast: error => state.errors.push(error), simpleToastErrorMessage: error => error.message,
    formatProblemCount: counts => String(counts.total), sessionProblemCounts: s => ({ total: s.problems.length }),
  });
  for (const name of ['refreshInFlightRef', 'restoreInFlightRef', 'resetInFlightRef', 'publishInFlightRef',
    'recognitionInFlightRef', 'queueRegistrationInFlightRef', 'sessionRecognitionInFlightRef', 'mutatingRef']) {
    sandbox[name] = { current: false };
  }
  const update = (name, value) => { sandbox[name] = typeof value === 'function' ? value(sandbox[name]) : value; };
  for (const [setter, name] of Object.entries({ setSession: 'session', setItems: 'items', setFileName: 'fileName',
    setActiveId: 'activeId', setLayoutGapModeState: 'layoutGapMode', setPublished: 'published', setView: 'view',
    setRefreshing: 'refreshing', setInitialSessionLoaded: 'initialSessionLoaded', setInitialSessionError: 'initialSessionError',
    setLoading: 'loading', setMutating: 'mutating', setRestoringSessionId: 'restoringSessionId' })) {
    sandbox[setter] = value => update(name, value);
  }
  sandbox.setHistoryStack = value => {
    update('historyStack', value);
    sandbox.historyStackRef.current = sandbox.historyStack;
  };
  vm.runInContext(section('function assertPlacementSaveSession(', 'async function postRestoreBoardLayoutWithConflictRetry('), sandbox);
  const load = (start, end, name) => vm.runInContext(section(start, end) + `\nglobalThis.${name} = ${name};`, sandbox);
  load('  const refreshSession = useCallback(', '  const restoreRecentSession =', 'refreshSession');
  load('  const restoreRecentSession = useCallback(', '  const triggerUpload =', 'restoreRecentSession');
  load('  const undoMutation = useCallback(', '  // Ctrl/Cmd+Z', 'undoMutation');
  return { sandbox, state };
}
"""


class TestUiRefreshDraftPreservation(unittest.TestCase):
    def test_refresh_preserves_view_and_existing_selection_and_falls_back_when_it_was_deleted(self) -> None:
        run_node(STATE_SETUP + r"""
        (async () => {
          for (const view of ['board', 'review']) {
            for (const selectionExists of [true, false]) {
              const { sandbox, state } = stateSandbox();
              sandbox.view = view;
              sandbox.activeId = 'p2';
              state.latestSession = JSON.parse(JSON.stringify(sandbox.session));
              if (!selectionExists) state.latestSession.problems = [state.latestSession.problems[0]];
              await sandbox.refreshSession();
              assert.equal(sandbox.view, view, 'refresh must preserve the current working view');
              assert.equal(sandbox.activeId, selectionExists ? 'p2' : 'p1');
              assert.equal(state.confirms, 0);
              assert.equal(sandbox.historyStack.length, 0);
            }
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_cancel_dirty_refresh_never_fetches_or_changes_preview_or_history(self) -> None:
        run_node(STATE_SETUP + r"""
        (async () => {
          const { sandbox, state } = stateSandbox();
          sandbox.items = [sandbox.applyPlacementPatchToItem(sandbox.items[0], { xRatio: 0.4 }), sandbox.items[1]];
          sandbox.fileName = '아직 저장하지 않은 수업 이름';
          const before = JSON.stringify({ session: sandbox.session, items: sandbox.items, fileName: sandbox.fileName,
            published: sandbox.published, activeId: sandbox.activeId, view: sandbox.view, history: sandbox.historyStack });
          state.confirmed = false;
          assert.equal(await sandbox.refreshSession(), false);
          assert.equal(state.confirms, 1);
          assert.equal(state.fetches, 0);
          assert.equal(state.historyFetches, 0);
          assert.equal(JSON.stringify({ session: sandbox.session, items: sandbox.items, fileName: sandbox.fileName,
            published: sandbox.published, activeId: sandbox.activeId, view: sandbox.view, history: sandbox.historyStack }), before);
          assert.equal(sandbox.refreshInFlightRef.current, false);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_confirmed_dirty_refresh_saves_current_draft_as_an_undo_snapshot(self) -> None:
        run_node(STATE_SETUP + r"""
        (async () => {
          const { sandbox, state } = stateSandbox();
          const oldSession = sandbox.session;
          sandbox.items = [sandbox.applyPlacementPatchToItem(sandbox.items[0], { xRatio: 0.4 }), sandbox.items[1]];
          sandbox.fileName = '미저장 수업';
          state.latestSession = JSON.parse(JSON.stringify(oldSession));
          await sandbox.refreshSession();
          assert.equal(state.confirms, 1);
          assert.equal(state.fetches, 1);
          assert.equal(sandbox.session, state.latestSession);
          assert.equal(sandbox.historyStack.length, 1);
          assert.equal(sandbox.historyStack[0].session_name, '미저장 수업');
          assert.equal(sandbox.historyStack[0].problems[0].placementXRatio, 0.4);
          assert.equal(sandbox.historyStackRef.current, sandbox.historyStack);
          assert.equal(sandbox.refreshing, false);
          await sandbox.undoMutation();
          assert.equal(sandbox.fileName, '미저장 수업');
          assert.equal(sandbox.items[0].placementXRatio, 0.4);
          assert.equal(sandbox.historyStack.length, 0);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_failed_refresh_preserves_draft_and_does_not_add_an_undo_entry(self) -> None:
        run_node(STATE_SETUP + r"""
        (async () => {
          const { sandbox, state } = stateSandbox();
          sandbox.layoutGapMode = 'compact';
          const originalItems = sandbox.items, originalSession = sandbox.session;
          state.fetchError = new Error('offline');
          await sandbox.refreshSession();
          assert.equal(sandbox.items, originalItems);
          assert.equal(sandbox.session, originalSession);
          assert.equal(sandbox.layoutGapMode, 'compact');
          assert.equal(sandbox.historyStack.length, 0);
          assert.equal(sandbox.refreshInFlightRef.current, false);
          assert.equal(sandbox.refreshing, false);
          assert.equal(state.errors.length, 1);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_clean_refresh_does_not_ask_or_create_history_for_server_metadata_changes(self) -> None:
        run_node(STATE_SETUP + r"""
        (async () => {
          const { sandbox, state } = stateSandbox();
          state.latestSession = { ...sandbox.session, sessionRevision: 20, edbPath: '/tmp/result.edb' };
          await sandbox.refreshSession();
          assert.equal(state.confirms, 0);
          assert.equal(sandbox.historyStack.length, 0);
          assert.equal(state.historyFetches, 1);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_name_step_and_gap_only_edits_each_trigger_the_refresh_guard(self) -> None:
        run_node(STATE_SETUP + r"""
        (async () => {
          for (const change of [
            sandbox => { sandbox.fileName = '새 수업 이름'; },
            sandbox => { sandbox.items[0] = { ...sandbox.items[0], name: '새 문항 제목' }; },
            sandbox => { sandbox.items[0] = { ...sandbox.items[0], step: 's3' }; },
            sandbox => { sandbox.layoutGapMode = 'compact'; },
          ]) {
            const { sandbox, state } = stateSandbox();
            change(sandbox);
            state.confirmed = false;
            assert.equal(await sandbox.refreshSession(), false);
            assert.equal(state.confirms, 1);
            assert.equal(state.fetches, 0);
          }
          const { sandbox } = stateSandbox();
          const signature = sandbox.sessionEditorDraftSignature(sandbox.session);
          const artifacts = { ...sandbox.session, edb_path: '/tmp/old.edb', sessionRevision: 99,
            sessionEpoch: 'server-generation', publishSummary: { edbPath: '/tmp/old.edb' } };
          assert.equal(sandbox.sessionEditorDraftSignature(artifacts), signature,
            'publication artifacts and server revision metadata are not unsaved user edits');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestUiHistoryUndoSafety(unittest.TestCase):
    def test_history_restore_requires_confirmation_and_keeps_current_draft_for_undo(self) -> None:
        run_node(STATE_SETUP + r"""
        (async () => {
          const { sandbox, state } = stateSandbox();
          sandbox.items = [sandbox.applyPlacementPatchToItem(sandbox.items[0], { xRatio: 0.35 }), sandbox.items[1]];
          state.latestSession = { ...sandbox.session, session_name: '최근 작업' };
          state.confirmed = false;
          assert.equal(await sandbox.restoreRecentSession('history-1'), false);
          assert.equal(state.historyPosts, 0);
          assert.equal(sandbox.items[0].placementXRatio, 0.35);
          state.confirmed = true;
          assert.equal(await sandbox.restoreRecentSession('history-1'), true);
          assert.equal(state.historyPosts, 1);
          assert.equal(sandbox.historyStack.at(-1).problems[0].placementXRatio, 0.35);
          sandbox.view = 'board';
          sandbox.activeId = 'p2';
          await sandbox.undoMutation();
          assert.equal(sandbox.items[0].placementXRatio, 0.35);
          assert.equal(sandbox.view, 'board');
          assert.equal(sandbox.activeId, 'p2');
          assert.equal(sandbox.historyStack.length, 0);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_busy_failed_or_invalid_undo_never_consumes_the_history_snapshot(self) -> None:
        run_node(STATE_SETUP + r"""
        (async () => {
          const { sandbox, state } = stateSandbox();
          sandbox.setHistoryStack([sandbox.session]);
          const originalHistory = sandbox.historyStack;
          sandbox.reviewEditorActive = true;
          await sandbox.undoMutation();
          assert.equal(state.restores, 0, 'session undo must not discard an open crop draft');
          assert.equal(sandbox.historyStack, originalHistory);
          sandbox.reviewEditorActive = false;
          sandbox.mutatingRef.current = true;
          await sandbox.undoMutation();
          assert.equal(state.restores, 0, 'undo must not race an in-flight mutation');
          sandbox.mutatingRef.current = false;
          state.restoreError = new Error('offline');
          await sandbox.undoMutation();
          assert.equal(sandbox.historyStack, originalHistory);
          state.restoreError = null;
          for (const response of [null, {}, { problems: [] }]) {
            state.restoreResponse = response;
            await sandbox.undoMutation();
            assert.equal(sandbox.historyStack, originalHistory, 'invalid success payload must preserve undo history');
          }
          assert.equal(sandbox.mutatingRef.current, false);
          assert.equal(sandbox.loading, null);
          assert.equal(state.errors.length, 4);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestUiBulkStepInvalidatesPublish(unittest.TestCase):
    def test_bulk_step_marks_publish_stale_even_when_the_success_toast_is_silent(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        Object.assign(sandbox, {
          items: [{ id: 'a', step: 'raw' }, { id: 'b', step: 's1' }], published: true,
          setItems: update => { sandbox.items = update(sandbox.items); },
          setPublished: value => { sandbox.published = value; },
          stepLabel: step => step, showToast: message => { sandbox.toasts.push(message); }, toasts: [],
        });
        vm.runInContext(section('  const applyToAll =', '  const setPlacements =') + '\nglobalThis.applyToAll = applyToAll;', sandbox);
        sandbox.applyToAll('s2', { silent: true });
        assert.equal(sandbox.items.map(item => item.step).join(','), 's2,s2');
        assert.equal(sandbox.published, false, 'image processing changes invalidate previously published content');
        assert.equal(sandbox.toasts.length, 0);
        sandbox.published = true;
        sandbox.applyToAll('s3');
        assert.equal(sandbox.published, false);
        assert.equal(sandbox.toasts.length, 1);
        """)

    def test_api_key_save_reports_success_only_after_the_settings_request_succeeds(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = { useCallback: fn => fn, showToast() {}, showSimpleErrorToast() {},
          setUserSettings: settings => { sandbox.userSettings = settings; },
          setAiEnabled: enabled => { sandbox.aiEnabled = enabled; },
          saveUserSettings: async () => ({ hasGeminiApiKey: true, aiEnabled: true }),
        };
        vm.createContext(sandbox);
        vm.runInContext(section('  const onSaveGeminiKey = useCallback(', '  const onToggleAi = useCallback(')
          + '\nglobalThis.onSaveGeminiKey = onSaveGeminiKey;', sandbox);
        (async () => {
          assert.equal(await sandbox.onSaveGeminiKey('test-placeholder'), true);
          const savedSettings = sandbox.userSettings;
          sandbox.saveUserSettings = async () => { throw new Error('simulated settings failure'); };
          assert.equal(await sandbox.onSaveGeminiKey('replacement-placeholder'), false);
          assert.equal(sandbox.userSettings, savedSettings, 'failed API key save must keep active settings intact');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


if __name__ == '__main__':
    unittest.main()
