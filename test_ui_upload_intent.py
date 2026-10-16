from __future__ import annotations

import unittest

from test_ui_placement_details import NODE_SETUP, run_node


QUEUE_SETUP = NODE_SETUP + r"""
async function runQueue(mode, selectedIntent) {
  const sandbox = placementSandbox();
  const requests = [], failures = [];
  Object.assign(sandbox, {
    useCallback: fn => fn, initialSessionLoaded: true, operationRecovery: null,
    listUnique: values => [...new Set(values)],
    pendingFiles: [{ name: '수학.pdf', size: 10, lastModified: 1 }],
    inputIntent: selectedIntent, aiEnabled: false, userSettings: {}, session: null, usingMock: false,
    items: [], fileName: '수학 수업', boardColumns: 1, layoutGapMode: 'classin-grid',
    restoreInFlightRef: { current: false }, refreshInFlightRef: { current: false },
    resetInFlightRef: { current: false }, publishInFlightRef: { current: false },
    recognitionInFlightRef: { current: false }, queueRegistrationInFlightRef: { current: false },
    queueGenerationRef: { current: 1 }, fileInputRef: { current: { value: 'picked' } },
    AI_FALLBACK_OFF: { enabled: false }, AI_FALLBACK_ON: { enabled: true },
    isImageOnlyFileBatch: () => false,
    startBackgroundJob: () => ({ id: 'job', controller: { signal: { aborted: false } } }),
    settleBackgroundJob() {}, clearOperationRecovery() {}, showToast() {},
    queueRequestIsCurrent: () => true,
    postExport: async (files, fallback, intent, options) => {
      requests.push({ files, fallback, intent, options });
      return { session_name: '수학 수업', inputIntent: intent, input_intent: intent,
        problems: [{ id: 'p1', title: '문항 1', actualHeightPages: 0.8, inputIntent: intent, input_intent: intent,
          sourcePageId: 'page-1' }], pages: [{ id: 'page-1', problemIds: ['p1'] }] };
    },
    postRestore: async session => session,
    applySession: session => { sandbox.applied = session; return true; },
    setActiveId() {}, reviewFocusForNewSession: () => ({ scopePageIds: ['page-1'] }),
    setReviewFocus() {}, setView: view => { sandbox.view = view; },
    refreshSessionHistory() {}, setPendingFilesTracked() {}, setLoading() {},
    aiModelFallbackToast: () => '', summarizeRecognitionSession: () => ({ problems: 1, problemLabel: '문항 1개' }),
    setRecognitionReview: review => { sandbox.review = review; },
    sessionProblemCounts: () => ({}), formatProblemCount: () => '문항 1개',
    classifyNetworkRequestError: async error => error,
    activateOperationRecovery: error => { failures.push(error); },
  });
  vm.runInContext(section('function fileQueueKey(', 'function sourceFileExtension(')
    + section('function fitWidthPageAsIsSession(', 'function mergeSessions(')
    + section('  const processQueuedFiles = useCallback(', '  const applyRecognitionResult =')
    + '\nglobalThis.processQueuedFiles = processQueuedFiles;', sandbox);
  await sandbox.processQueuedFiles(mode);
  assert.deepEqual(failures, [], 'the queue handler should complete its actual success path');
  assert.equal(requests.length, 1);
  assert.equal(sandbox.recognitionInFlightRef.current, false);
  assert.equal(sandbox.queueRegistrationInFlightRef.current, false);
  return { sandbox, request: requests[0] };
}
"""


class TestUiUploadIntent(unittest.TestCase):
    def test_register_ignores_legacy_single_problem_intent_and_keeps_full_page(self) -> None:
        run_node(QUEUE_SETUP + r"""
        (async () => {
          const { sandbox, request } = await runQueue('register', 'single-problem');
          assert.equal(request.intent, 'page-as-is');
          assert.equal(request.options.exportEdb, false);
          assert.equal(sandbox.applied.input_intent, 'page-as-is');
          assert.equal(sandbox.applied.problems[0].input_intent, 'page-as-is');
          assert.equal(sandbox.applied.problems[0].placement_mode, 'continuous-page-as-is');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_page_register_and_manual_split_keep_the_full_page_workflow(self) -> None:
        run_node(QUEUE_SETUP + r"""
        (async () => {
          for (const selected of ['multi-problem', 'page-as-is']) {
            const { sandbox, request } = await runQueue('register', selected);
            assert.equal(request.intent, 'page-as-is');
            assert.equal(sandbox.applied.input_intent, 'page-as-is');
            assert.equal(sandbox.applied.problems[0].placement_mode, 'continuous-page-as-is');
          }
          const { sandbox, request } = await runQueue('manual-split', 'single-problem');
          assert.equal(request.intent, 'page-as-is');
          assert.equal(sandbox.view, 'review');
          assert.equal(sandbox.applied.problems[0].placement_mode, undefined,
            'manual split opens original pages before width fitting');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_recognition_modes_ignore_legacy_intent_and_queue_transactional_auto_apply(self) -> None:
        run_node(QUEUE_SETUP + r"""
        (async () => {
          for (const mode of ['recognize', 'passage-only']) {
            const { sandbox, request } = await runQueue(mode, 'single-problem');
            assert.equal(request.intent, 'multi-problem');
            assert.equal(request.options.contentTarget, mode === 'passage-only' ? 'shared-passages' : 'all');
            assert.equal(sandbox.review.incomingSession.input_intent, 'multi-problem');
            assert.equal(sandbox.applied, undefined, 'the auto-apply effect must persist the candidate before replacing the session');
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


if __name__ == '__main__':
    unittest.main()
