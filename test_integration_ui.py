"""Regression tests for the UI fixes made while stabilizing the merged dev branches
(build gate, blocked 제작, AI jobs vs saves, delete-toast undo, toast wrapping,
AI pre-save, AI conflicts, busy-overlay shortcuts, undo back to the build, 검수 Delete)."""
from __future__ import annotations

import unittest
from pathlib import Path

from test_dev_ui_editor import KEY_SETUP
from test_dev_ui_undo import UNDO_SETUP
from test_ui_home_flows import NODE_SETUP, run_node
from test_ui_publish_response_validation import PUBLISH_SETUP

PROJECT_ROOT = Path(__file__).resolve().parent
CSS = (PROJECT_ROOT / "ui_prototype" / "board.css").read_text(encoding="utf-8")


class TestBuildGate(unittest.TestCase):
    def test_classin_review_mark_keeps_the_current_build_openable(self) -> None:
        # 검수 완료 replaces the session object; the build gate compares the exact object.
        run_node(NODE_SETUP + r"""
        (async () => {
          const built = { problems: [{ id: 'p1' }] };
          const updated = { problems: [{ id: 'p1' }], publishSummary: { classinReview: 'passed' } };
          for (const wasCurrent of [true, false]) {
            const sandbox = {
              useCallback: fn => fn, session: built, builtBoardRef: { current: wasCurrent ? built : null },
              postClassinReviewResult: async () => updated, setSession: value => { sandbox.session = value; },
              refreshSessionHistory() {}, setPublished() {}, showToast() {},
              showSimpleErrorToast: error => { throw error; },
            };
            vm.createContext(sandbox);
            vm.runInContext(section('  const markClassinReviewComplete = useCallback(', '  const onPublish = async () => {')
              + '\nglobalThis.mark = markClassinReviewComplete;', sandbox);
            await sandbox.mark();
            assert.equal(sandbox.session, updated);
            assert.equal(sandbox.builtBoardRef.current === updated, wasCurrent,
              wasCurrent ? 'the build this board made stays openable' : 'an older build never becomes openable');
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestBlockedPublish(unittest.TestCase):
    def test_server_preflight_block_keeps_saved_lesson_as_baseline(self) -> None:
        run_node(PUBLISH_SETUP + r"""
        (async () => {
          const { sandbox, events, session } = publishSandbox({ ok: false, code: 'publish_preflight_blocked' });
          sandbox.publishRequestDraft.sessionForPublish = { ...session, problems: [{ id: 'p2', moved: true }, { id: 'p1' }] };
          Object.assign(sandbox, {
            normalizePublishPreflightBlock: () => ({ blocked: true, issueTypes: ['step2_page_chrome_artifact_rate'], message: '' }),
            applyPublishPreflightIssuesToSession: raw => ({ ...raw, marked: true }),
            publishBlockedTarget: () => ({ view: 'review', reviewFocus: null }),
            pageChromePreflightBlockToast: () => 'blocked', setReviewFocus() {}, setView() {},
          });
          await sandbox.publish();
          assert.equal(events.adopted.length, 1);
          assert.equal(sandbox.session.marked, true, 'the flagged problems still show as 확인 필요');
          assert.equal(sandbox.session.problems, session.problems, 'the server saved nothing: the saved lesson stays the baseline');
          assert.equal(sandbox.items.map(item => item.id).join(','), 'p2,p1');
          assert.equal(sandbox.items[0].moved, true, 'the unsaved move stays on the board, still unsaved');
          assert.equal(sandbox.publishInFlightRef.current, false);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestSavesWaitForRunningJobs(unittest.TestCase):
    def test_saves_and_second_jobs_wait_for_a_running_job(self) -> None:
        # AI jobs no longer hold the server lock: a save or a second job now would make
        # the running job's result be rejected (고화질) or arrive stale (AI 인식).
        run_node(NODE_SETUP + r"""
        const toasts = [], sent = [];
        const spy = name => async () => { sent.push(name); return {}; };
        const sandbox = {
          useCallback: fn => fn, hasRunningBackgroundJobs: true,
          session: { problems: [{ id: 'p1' }, { id: 'p2' }] }, items: [{ id: 'p1' }, { id: 'p2' }],
          usingMock: false, aiEnabled: true, userSettings: { hasGeminiApiKey: true }, mutatingRef: { current: false },
          initialSessionLoaded: true, operationRecovery: null, pendingFiles: [],
          hasRunningQueueRecognition: false, confirmingRecognition: false,
          queueRegistrationInFlightRef: { current: false }, publishInFlightRef: { current: false },
          restoreInFlightRef: { current: false }, refreshInFlightRef: { current: false },
          resetInFlightRef: { current: false }, recognitionInFlightRef: { current: false },
          sessionRecognitionInFlightRef: { current: false }, sessionRecognitionGuardUntilRef: { current: 0 },
          sessionReusableSourcePaths: () => ['수업.pdf'], reusableSessionSourcePaths: ['수업.pdf'],
          fileName: '수업', boardColumns: 1, layoutGapMode: 'classin-grid', inputIntent: 'multi-problem',
          keepAttachedFigures: true, trimPageWhitespace: true, queueRequestIsCurrent: () => true,
          settleBackgroundJob() {}, adoptMutatedSession() {}, applySession() {}, refreshSessionHistory() {},
          setPendingFilesTracked() {}, activateOperationRecovery() {}, clearOperationRecovery() {},
          showToast: message => toasts.push(message), showSimpleErrorToast() {},
          startBackgroundJob() { throw new Error('no second job may start'); },
          postRestore: spy('restore'), postRestoreBoardLayoutWithConflictRetry: spy('save'), fetch: spy('publish'),
          postRetryAi: spy('retry'), postReextractSharedPassages: spy('reextract'),
          postEnhanceImage: spy('enhance'), postExport: spy('export'),
        };
        vm.createContext(sandbox);
        vm.runInContext([
          section('  const reorder = (fromId, toId,', '  const removeItem = async ('),
          section('  const savePlacement = async () => {', '  const reorder = (fromId, toId,'),
          section('  const onPublish = async () => {', '  const handlePublishDownload ='),
          section('  const retryAiSession = useCallback(', '  const recognizeCurrentSession = useCallback('),
          section('  const reextractSharedPassagesFromSession = useCallback(', '  const exportSessionImages = useCallback('),
          section('  const enhanceImageSession = useCallback(', '  const resetSession = useCallback('),
          section('  const processQueuedFiles = useCallback(', '  const applyRecognitionResult ='),
        ].join('\n') + '\nObject.assign(globalThis, { reorder, savePlacement, onPublish, retryAiSession, '
          + 'reextractSharedPassagesFromSession, enhanceImageSession, processQueuedFiles });', sandbox);
        (async () => {
          assert.equal(sandbox.reorder('p1', 'p2', 'after'), false);
          assert.equal(await sandbox.savePlacement(), false);
          await sandbox.onPublish();
          await sandbox.retryAiSession({ pageIds: ['page-1'] });
          await sandbox.reextractSharedPassagesFromSession();
          await sandbox.enhanceImageSession(['p1'], { mode: 'preserve' });
          await sandbox.processQueuedFiles('register');
          assert.deepEqual(sent, [], 'nothing is saved and no second job starts while a job runs');
          assert.equal(toasts.length, 7);
          for (const message of toasts) assert.match(message, /진행 중인 작업이 끝난 뒤/);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestUndoAndRedo(unittest.TestCase):
    def test_delete_toast_undo_leaves_a_redo_that_deletes_again(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox } = undoSandbox();
          // React gives each render its own closures: the toast is made in the
          // Delete render and clicked after the board without the problem rendered.
          let toastAction = null;
          Object.assign(sandbox, {
            undoMutationRef: { current: null }, listUnique: values => [...new Set(values)],
            showActionToast: (_message, _label, action) => { toastAction = action; },
            mutateSession: async (_action, { problemId }) => {
              const before = sandbox.materializeSessionForItems(sandbox.session, sandbox.items, sandbox.fileName, 1, 'classin-grid');
              sandbox.setHistoryStack(prev => sandbox.appendBoundedHistory(prev, before, 20));
              sandbox.applySession({ ...before, problems: before.problems.filter(problem => problem.id !== problemId) });
              return sandbox.session;
            },
          });
          vm.runInContext('globalThis.render = () => {\n  const { session, items, fileName, view, activeId, mutating } = globalThis;\n'
            + section('  const undoMutation = useCallback(', '  const redoMutation = useCallback(')
            + section('  const removeItem = async (id, options = {}) =>', '  const addMockSample')
            + '  return removeItem;\n};', sandbox);
          assert.equal(await sandbox.render()('p1'), true);
          sandbox.render();
          toastAction();
          await new Promise(resolve => setImmediate(resolve));
          assert.equal(sandbox.session.problems.map(problem => problem.id).join(','), 'p1,p2', '실행 취소 brings the problem back');
          assert.equal(await sandbox.redoMutation(), true);
          assert.equal(sandbox.session.problems.map(problem => problem.id).join(','), 'p2', '다시 실행 deletes it again');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_shortcuts_wait_while_the_busy_overlay_is_up(self) -> None:
        # EDB 제작, 새 수업 and 최근 수업 열기 raise the overlay (loading) without
        # mutating; a Ctrl+Z under it was overwritten by the result.
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox } = undoSandbox();
          sandbox.redoCount = 0;
          sandbox.setRedoCount = value => { sandbox.redoCount = value; };
          vm.runInContext('globalThis.shortcutState = () => {' + section('  const canUndo = (historyStack.length', '\n')
            + section('  const redoAnchor = redoAnchorRef.current;', '  const updateInstallBlockedReason')
            + ' return [canUndo, canRedo]; };', sandbox);
          withServerStep(sandbox);
          await sandbox.undoMutation();
          sandbox.localEditUndoCount = 1;
          assert.deepEqual([...sandbox.shortcutState()], [true, true], 'a redo step and an unsaved board edit');
          sandbox.loading = { label: '편집된 .edb 파일 생성 중...' };
          assert.deepEqual([...sandbox.shortcutState()], [false, false], 'Ctrl+Z under the overlay is overwritten by its result');
          sandbox.loading = null;
          assert.deepEqual([...sandbox.shortcutState()], [true, true]);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_undo_back_to_the_built_board_reopens_the_build(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox } = undoSandbox();
          sandbox.published = true;  // this board was just built
          for (const scale of [0.9, 0.8]) sandbox.setPlacements([{ id: 'p1', patch: { scaleRatio: scale } }]);
          sandbox.setPlacements([{ id: 'p2', patch: { xRatio: 0.3 } }]);
          assert.equal(sandbox.published, false);
          await sandbox.undoMutation();
          assert.equal(sandbox.published, false, 'the board still differs from the build');
          await sandbox.undoMutation();
          assert.equal(sandbox.published, true, 'back on the built board, ClassIn에서 열기 needs no rebuild');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestAiJobSaves(unittest.TestCase):
    def test_a_cancelled_ai_job_keeps_the_save_as_the_saved_lesson(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          for (const name of ['enhanceImageSession', 'retryAiSession']) {
            const { sandbox } = undoSandbox();
            const saved = sandbox.session;
            const clone = value => JSON.parse(JSON.stringify(value));
            const sig = value => sandbox.sessionEditorDraftSignature(value);
            let app = clone(saved);
            const cancelled = async () => { const error = new Error('aborted'); error.name = 'AbortError'; throw error; };
            Object.assign(sandbox, {
              aiEnabled: true, userSettings: { hasGeminiApiKey: true }, listUnique: values => [...new Set(values)],
              sessionRecognitionGuardUntilRef: { current: 0 }, settleBackgroundJob() {}, adoptMutatedSession() {},
              startBackgroundJob: () => ({ id: 'job', controller: new AbortController() }),
              postRestore: async snapshot => { app = clone(snapshot); return clone(snapshot); },
              postEnhanceImage: cancelled, postRetryAi: cancelled,
            });
            vm.runInContext(section('  const retryAiSession = useCallback(', '  const recognizeCurrentSession = useCallback(')
              + section('  const enhanceImageSession = useCallback(', '  const resetSession = useCallback(')
              + '\nglobalThis.retryAiSession = retryAiSession; globalThis.enhanceImageSession = enhanceImageSession;', sandbox);
            sandbox.setPlacements([{ id: 'p1', patch: { xRatio: 0.4 } }]);
            await (name === 'enhanceImageSession'
              ? sandbox.enhanceImageSession(['p1'], { mode: 'preserve' })
              : sandbox.retryAiSession({ pageIds: ['page-1'] }));
            assert.notEqual(sig(app), sig(saved), `${name}: the move is saved before the job starts`);
            assert.equal(sig(sandbox.session), sig(app), `${name}: the board takes that save as its saved lesson`);
            assert.equal(sandbox.deriveSessionEditState(sandbox.session, sandbox.items, sandbox.fileName,
              sandbox.boardColumns, sandbox.layoutGapMode).dirty, false, `${name}: no '저장 필요' after a cancel`);
            assert.equal(sandbox.historyStack.at(-1), saved, `${name}: Ctrl+Z can take the saved move back`);
            // React clears local undo when the saved lesson changes ([session] effect).
            if (sandbox.session !== saved) sandbox.localEditHistoryRef.current = { stack: [], burstKey: '', burstAt: 0 };
            await sandbox.undoMutation();
            assert.equal(sig(app), sig(saved), `${name}: Ctrl+Z also takes the move back in the app`);
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_ai_conflict_takes_the_saved_lesson_only_when_this_screen_was_behind(self) -> None:
        run_node(NODE_SETUP + r"""
        const EPOCH = '00000000000000000001-a';
        const latest = { problems: [{ id: 'p1' }] };
        const conflict = (revision, latestSession = latest) => Object.assign(new Error('conflict'), {
          code: 'session_conflict', latestSession, sessionRevision: revision, sessionEpoch: EPOCH,
        });
        const applied = [], toasts = [];
        const sandbox = {
          useCallback: fn => fn, session: { problems: [{ id: 'p1' }] }, items: [], fileName: '수업',
          boardColumns: 1, layoutGapMode: 'compact', aiEnabled: true, userSettings: { hasGeminiApiKey: true },
          hasRunningBackgroundJobs: false, UNDO_HISTORY_LIMIT: 5, listUnique: values => [...new Set(values)],
          materializeSessionForItems: () => ({ problems: [] }), cloneSession: value => value,
          // snapshotBefore is a fresh object: it differs from the saved lesson only while the board is dirty.
          sessionEditorDraftSignature: value => (sandbox.dirty && value !== sandbox.session ? 'edited' : 'saved'),
          setSession() {},
          startBackgroundJob: () => ({ id: 'job', controller: { signal: { aborted: false } } }),
          settleBackgroundJob() {}, setHistoryStack() {}, appendBoundedHistory: list => list,
          adoptMutatedSession: next => applied.push(next), setRecognitionReview() {}, aiModelFallbackToast: () => '',
          sessionRecognitionInFlightRef: { current: false }, sessionRecognitionGuardUntilRef: { current: 0 },
          showToast: (message, type) => toasts.push([message, type]),
          showSimpleErrorToast() { throw new Error('no generic error toast'); },
          postRestore: async () => { if (sandbox.restoreError) throw sandbox.restoreError; return {}; },
          postEnhanceImage: async () => { if (sandbox.jobError) throw sandbox.jobError; return { session: {}, enhance: [] }; },
          postRetryAi: async () => { if (sandbox.jobError) throw sandbox.jobError; return { session: {} }; },
        };
        vm.createContext(sandbox);
        vm.runInContext(section('let latestServerSessionRevision = null;', 'async function fetchLatestSession(')
          + section('const AI_RESULT_BOARD_CHANGED_MESSAGE', 'async function postRestore(')
          + section('  const retryAiSession = useCallback(', '  const recognizeCurrentSession = useCallback(')
          + section('  const enhanceImageSession = useCallback(', '  const resetSession = useCallback(')
          + `\nglobalThis.t = { retryAiSession, enhanceImageSession, captureSessionRevision,
            expected: () => withExpectedSessionRevision({}).expectedSessionRevision };`, sandbox);
        const { t } = sandbox;
        const run = async (kind, { restoreError = null, jobError = null } = {}) => {
          Object.assign(sandbox, { restoreError, jobError });
          sandbox.sessionRecognitionGuardUntilRef.current = 0;
          if (kind === 'retry') await t.retryAiSession({ pageIds: ['page-1'] });
          else await t.enhanceImageSession(['p1'], { mode: 'preserve' });
        };
        (async () => {
          t.captureSessionRevision({ session: {}, sessionRevision: 4, sessionEpoch: EPOCH });
          // Another window saved: the saved lesson replaces this screen, so the re-run can save.
          await run('enhance', { restoreError: conflict(6) });
          assert.deepEqual(applied.splice(0), [latest]);
          assert.equal(t.expected(), 6);
          assert.deepEqual(toasts.pop(), ['작업하는 동안 칠판이 바뀌어서 결과를 적용하지 않았어요. 다시 실행해 주세요.', 'error']);
          // This screen already holds that revision (it saved the board during the job): keep the screen.
          await run('enhance', { jobError: conflict(6) });
          assert.deepEqual(applied, []);
          // An older reply never wins; a reply without a saved lesson leaves the screen and its revision.
          await run('enhance', { jobError: conflict(5) });
          await run('enhance', { jobError: conflict(9, null) });
          assert.deepEqual(applied, []);
          assert.equal(t.expected(), 6);
          // AI 재인식 recovers the same way.
          await run('retry', { restoreError: conflict(8) });
          assert.deepEqual(applied.splice(0), [latest]);
          assert.equal(t.expected(), 8);
          await run('retry', { jobError: conflict(8) });
          assert.deepEqual(applied, []);
          // Unsaved board edits and the pre-job save itself was refused: the edits stay on screen and the
          // revision stays behind, so 수업 배치 변경 저장 merges them instead of overwriting the newer lesson.
          sandbox.dirty = true;
          for (const kind of ['enhance', 'retry']) {
            await run(kind, { restoreError: conflict(9) });
            assert.deepEqual(applied, [], `${kind}: unsaved edits are not replaced`);
            assert.equal(t.expected(), 8, `${kind}: revision not taken over`);
            const [message, type] = toasts.pop();
            assert.equal(type, 'error');
            assert.ok(message.includes('수업 배치 변경 저장'), message);
          }
          // The same edits saved before the job, then the job's result conflicted: the saved lesson wins.
          await run('enhance', { jobError: conflict(9) });
          assert.deepEqual(applied.splice(0), [latest]);
          assert.equal(t.expected(), 9);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestReviewDeleteKey(unittest.TestCase):
    def test_delete_ignores_keys_from_the_source_picker_and_menus(self) -> None:
        run_node(KEY_SETUP + r"""
        let excluded = 0;
        Object.assign(sandbox, { manualSplit: null, boxEdit: null, selectedActionIds: ['p1'], mutating: false,
          doExclude: async () => { excluded += 1; } });
        vm.runInContext(section("  useEffect(() => {\n    // 영역 조정 owns Delete/Backspace", "  useEffect(() => {\n    const onMove"), sandbox);
        // Element.closest over a fake ancestor chain: tag, [name] and [name="value"].
        const inside = (...chain) => ({ tagName: 'BUTTON', closest: selector => chain.find(node =>
          selector.split(',').some(part => {
            const tag = part.trim().match(/^[a-z]+/)?.[0];
            return (!tag || tag === node.tag) && [...part.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)]
              .every(([, name, value]) => value === undefined ? name in node : node[name] === value);
          })) || null });
        const pickerOption = inside({ tag: 'button', role: 'option' }, { tag: 'div', role: 'listbox' },
          { tag: 'div', role: 'dialog' });  // 원본 파일 picker: a popover without aria-modal
        const menuItem = inside({ tag: 'button', role: 'menuitem' }, { tag: 'div', role: 'menu' });
        for (const target of [pickerOption, menuItem]) {
          press('Backspace', target);
          press('Delete', target);
        }
        assert.equal(excluded, 0, 'Backspace in the 원본 파일 picker or a ⋯ menu must not exclude the selected problems');
        press('Delete', inside({ tag: 'button' }, { tag: 'div', role: 'toolbar' }));
        assert.equal(excluded, 1, 'Delete on the 검수 toolbar still excludes the selection');
        """)


class TestActionToastCopy(unittest.TestCase):
    def test_long_action_toasts_wrap(self) -> None:
        # A split EDB's '1번 파일부터 차례로 여세요' and the import notice's count must stay visible.
        copy = CSS.split("  .undo-toast-copy{", 1)[1].split("}", 1)[0]
        self.assertIn("white-space: normal;", copy)
        self.assertIn("word-break: keep-all;", copy)
        self.assertIn("overflow-wrap: anywhere;", copy)
        self.assertNotIn("text-overflow: ellipsis", copy)


if __name__ == "__main__":
    unittest.main()
