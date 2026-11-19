"""Regression tests for the teacher journey fixes: new lesson, build-done next step,
import notice, handoff removal, quiet recent restore, sample lesson and update reload."""
from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import app_server
from test_ui_home_flows import NODE_SETUP, run_node
from test_ui_home_redesign import HOME_SETUP
from test_ui_publish_response_validation import PUBLISH_SETUP
from test_ui_recognition_auto_apply import SETUP as RECOGNITION_SETUP
from test_ui_sidebar_hierarchy import RENDER_SETUP
from test_ui_toolbar_controls import run_node as run_toolbar_node


PROJECT_ROOT = Path(__file__).resolve().parent
APP_SOURCE = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")


class TestNewLessonKeepsHistoryServer(unittest.TestCase):
    def _files(self, tmpdir: Path):
        latest = tmpdir / "latest.json"
        history = tmpdir / "history.json"
        generated = tmpdir / "generated_session.js"
        edb = tmpdir / "old.edb"
        edb.write_bytes(b"edb")
        latest.write_text('{"problems": [{"id": "p1"}]}', encoding="utf-8")
        history.write_text(json.dumps([{"id": "old", "edbPath": str(edb), "session": {"problems": []}}]), encoding="utf-8")
        generated.write_text("window.EDB_UI_SESSION = { problems: [] };\n", encoding="utf-8")
        return latest, history, generated, edb

    def test_keep_history_clear_keeps_recent_list_and_returns_it(self):
        with TemporaryDirectory() as raw_tmp:
            latest, history, generated, edb = self._files(Path(raw_tmp))
            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = type("FakeServer", (), {"latest_session": {"problems": [{"id": "p1"}]}, "allowed_files": {"x"}})()
            responses = []
            handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
            with (
                patch.object(app_server, "LATEST_SESSION_JSON", latest),
                patch.object(app_server, "SESSION_HISTORY_JSON", history),
                patch.object(app_server, "GENERATED_SESSION_JS", generated),
                patch.object(app_server, "cleanup_runtime_artifacts", side_effect=AssertionError("must not prune")),
            ):
                handler._handle_session_clear(SimpleNamespace(query="keepHistory=1&cleanupArtifacts=true&dryRun=false"))

            self.assertFalse(latest.exists())
            self.assertTrue(history.exists())
            self.assertEqual("window.EDB_UI_SESSION = null;\n", generated.read_text(encoding="utf-8"))
            payload = responses[0][0]
            self.assertTrue(payload["ok"])
            self.assertEqual(["old"], [entry["id"] for entry in payload["history"]])
            self.assertNotIn("session", payload["history"][0])
            self.assertFalse(payload["artifactCleanupRequested"])
            self.assertEqual({str(edb.resolve())}, handler.server.allowed_files)

    def test_server_clear_with_kept_history_never_prunes_and_keeps_files_openable(self):
        with TemporaryDirectory() as raw_tmp:
            latest, history, generated, edb = self._files(Path(raw_tmp))
            server = object.__new__(app_server.AppHTTPServer)
            server._state_lock = threading.RLock()
            server._session_revision = 4
            server._active_artifact_jobs = 0
            server.latest_session = {"problems": [{"id": "p1"}]}
            server.allowed_files = {"stale"}
            with (
                patch.object(app_server, "LATEST_SESSION_JSON", latest),
                patch.object(app_server, "SESSION_HISTORY_JSON", history),
                patch.object(app_server, "GENERATED_SESSION_JS", generated),
                patch.object(app_server, "cleanup_runtime_artifacts", side_effect=AssertionError("must not prune")),
            ):
                result, revision = server.clear_session_with_revision(keep_history=True, cleanup_artifacts=True)
            self.assertIsNone(result)
            self.assertEqual(5, revision)
            self.assertIsNone(server.latest_session)
            self.assertFalse(latest.exists())
            self.assertTrue(history.exists())
            self.assertEqual({str(edb.resolve())}, server.allowed_files)

    def test_failed_clear_suggests_retrying_what_the_teacher_asked_for(self):
        def fail(**_kwargs):
            raise OSError("locked")

        handler = object.__new__(app_server.AppRequestHandler)
        handler.server = type("FakeServer", (), {
            "latest_session": None, "allowed_files": set(), "clear_session_with_revision": staticmethod(fail),
        })()
        responses = []
        handler._send_json = lambda payload, **kwargs: responses.append(payload)
        for query, expected in (("keepHistory=1", "새 수업을 다시 시작"), ("", "초기화를 다시 시도")):
            handler._handle_session_clear(SimpleNamespace(query=query))
            payload = responses.pop()
            self.assertEqual("reset_failed", payload["code"])
            self.assertIn(expected, payload["recoverySteps"][0])

    def test_default_clear_still_removes_history(self):
        with TemporaryDirectory() as raw_tmp:
            latest, history, generated, _edb = self._files(Path(raw_tmp))
            with (
                patch.object(app_server, "LATEST_SESSION_JSON", latest),
                patch.object(app_server, "SESSION_HISTORY_JSON", history),
                patch.object(app_server, "GENERATED_SESSION_JS", generated),
            ):
                app_server._atomically_clear_persisted_session_state()
            self.assertFalse(latest.exists())
            self.assertFalse(history.exists())


class TestNewLessonUi(unittest.TestCase):
    def test_clear_session_sends_keep_history_and_keeps_list_on_404(self):
        run_node(NODE_SETUP + r"""
        const calls = [];
        const sandbox = { latestServerSessionRevision: 3, latestServerSessionEpoch: 'e1', URLSearchParams,
          fetch: async (url, init) => { calls.push([url, init.method]); return sandbox.response; },
          expectOkJson: async resp => resp.body };
        vm.createContext(sandbox);
        vm.runInContext(section('async function clearSession(', 'async function postShutdown(')
          + '\nglobalThis.clearSession = clearSession;', sandbox);
        (async () => {
          sandbox.response = { status: 200, body: { ok: true, history: [{ id: 'kept' }] } };
          assert.deepEqual(await sandbox.clearSession({ keepHistory: true }), { ok: true, history: [{ id: 'kept' }] });
          assert.match(calls[0][0], /keepHistory=1/);
          assert.equal(calls[0][1], 'DELETE');
          await sandbox.clearSession();
          assert.doesNotMatch(calls[1][0], /keepHistory/);
          sandbox.response = { status: 404 };
          assert.equal((await sandbox.clearSession({ keepHistory: true })).history, null, '404 must not empty the recent list');
          assert.equal(JSON.stringify((await sandbox.clearSession()).history), '[]');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_new_lesson_confirms_only_for_unsaved_work_and_keeps_history(self):
        run_node(NODE_SETUP + r"""
        function resetSandbox(overrides = {}) {
          const state = { confirms: [], clears: [], recent: [], hidden: [], recovery: null, toasts: [] };
          const sandbox = {
            useCallback: fn => fn,
            recognitionInFlightRef: { current: false }, queueRegistrationInFlightRef: { current: false },
            downloadInFlightRef: { current: false }, publishInFlightRef: { current: false }, resetInFlightRef: { current: false },
            jobControllersRef: { current: new Map() },
            downloadBusy: false, publishBusy: false, loading: null, backgroundJobs: [],
            session: { problems: [{ id: 'p1' }] }, items: [{ id: 'p1' }], pendingFiles: [], recentSessions: [{ id: 'old' }],
            sessionEditState: { dirty: false }, reviewEditorActive: false,
            fileName: '수업', boardColumns: 1, layoutGapMode: 'classin-grid',
            window: { confirm: message => { state.confirms.push(message); return state.answer ?? true; } },
            materializeSessionForItems: s => s,
            clearSession: async options => { state.clears.push(options); return state.clearResult ?? { history: [{ id: 'old' }] }; },
            setRecentSessionsAuthoritative: history => state.recent.push(history),
            hideMockItems: message => state.hidden.push(message),
            activateOperationRecovery: (_error, title, _detail, recovery) => { state.recovery = { title, ...recovery }; },
            isNetworkRequestError: () => false,
            showToast: message => state.toasts.push(message),
            setResetBusy() {}, setLoading() {}, setBackgroundJobs() {}, setRecognitionReview() {},
            setConfirmingRecognition() {}, setPendingFilesTracked() {}, setReviewFocus() {}, clearOperationRecovery() {},
            ...overrides,
          };
          vm.createContext(sandbox);
          vm.runInContext(section('  const resetSession = useCallback(', '  // Warn before the tab closes')
            + '\nglobalThis.resetSession = resetSession;', sandbox);
          return { sandbox, state };
        }
        (async () => {
          let { sandbox, state } = resetSandbox();
          assert.equal(await sandbox.resetSession({ keepHistory: true }), true);
          assert.equal(state.confirms.length, 0, 'saved lessons start a new one without a scary prompt');
          assert.equal(JSON.stringify(state.clears), JSON.stringify([{ keepHistory: true }]));
          assert.equal(JSON.stringify(state.recent), JSON.stringify([[{ id: 'old' }]]));
          assert.ok(state.hidden[0].includes('최근 수업 목록'));

          for (const unsaved of [{ sessionEditState: { dirty: true } }, { pendingFiles: [{ name: 'a.pdf' }] }, { reviewEditorActive: true }]) {
            ({ sandbox, state } = resetSandbox(unsaved));
            state.answer = false;
            assert.equal(await sandbox.resetSession({ keepHistory: true }), false);
            assert.equal(state.confirms.length, 1);
            assert.ok(state.confirms[0].includes('최근 수업 목록에 남아'));
            assert.equal(state.clears.length, 0);
          }

          ({ sandbox, state } = resetSandbox());
          state.clearResult = { history: null };
          await sandbox.resetSession({ keepHistory: true });
          assert.equal(state.recent.length, 0, 'an unknown kept history leaves the list alone');

          ({ sandbox, state } = resetSandbox({ session: null }));
          await sandbox.resetSession({ keepHistory: true });
          assert.equal(state.clears.length, 0, 'the sample has nothing on the server to clear');
          assert.equal(state.hidden.length, 1);

          ({ sandbox, state } = resetSandbox({ clearSession: async () => { throw new Error('offline'); } }));
          assert.equal(await sandbox.resetSession({ keepHistory: true }), false);
          assert.equal(state.recovery.keepHistory, true);
          assert.equal(state.recovery.retryLabel, '새 수업 다시 시작');

          ({ sandbox, state } = resetSandbox({ loading: { label: 'busy' } }));
          await sandbox.resetSession({ keepHistory: true });
          await sandbox.resetSession();
          assert.deepEqual(state.toasts, ['작업 중에는 새 수업을 시작할 수 없습니다', '작업 중에는 초기화할 수 없습니다']);

          ({ sandbox, state } = resetSandbox());
          await sandbox.resetSession({ type: 'click' });
          assert.equal(state.confirms.length, 1, '초기화 still asks every time');
          assert.ok(state.confirms[0].includes('최근 수업 목록이 사라집니다'));
          assert.equal(JSON.stringify(state.clears), JSON.stringify([{ keepHistory: false }]));
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_failed_new_lesson_banner_says_new_lesson_not_reset(self):
        run_node(NODE_SETUP + r"""
        const Babel = require('./scripts/vendor/babel.min.js');
        const sandbox = {
          React: { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
            Fragment: 'fragment' },
          useState: initial => [initial, () => {}], useEffect() {},
          Icon: new Proxy({}, { get: (_, name) => String(name) }), IS_MAC_PLATFORM: false,
          probeLocalServerHealth: async () => true,
        };
        vm.createContext(sandbox);
        vm.runInContext(Babel.transform(section('const OPERATION_RECOVERY_SUMMARIES', 'function operationRecoveryStateTransition(')
          + section('function OperationRecoveryBanner(', 'function LoadingOverlay('), { presets: ['react'] }).code, sandbox);
        const text = node => typeof node === 'string' ? node : (node?.children || []).map(text).join(' ');
        const render = recovery => text(sandbox.OperationRecoveryBanner({ recovery, onRetry() {} }));
        const error = code => ({ code, message: 'failed to clear: locked', retryable: true });

        for (const recovery of [
          { kind: 'reset', keepHistory: true, error: error('reset_failed') },
          { kind: 'reset', keepHistory: true, error: error('artifact_cleanup_busy') },
          { kind: 'reset', keepHistory: true, error: error('session_conflict'), conflict: { latestSession: { problems: [] } } },
          { kind: 'reset', keepHistory: true, error: error('session_conflict'), conflict: { latestSession: null } },
        ]) {
          const banner = render(recovery);
          assert.ok(banner.includes('새 수업을 시작하지 못했습니다'), banner);
          assert.ok(banner.includes('새 수업 다시 시작'), banner);
          assert.equal(banner.includes('초기화'), false, `a kept-history failure must not read like 초기화: ${banner}`);
        }
        assert.ok(render({ kind: 'reset', keepHistory: true, error: error('reset_failed') }).includes('최근 수업 목록은 그대로 있어요'));

        const reset = render({ kind: 'reset', error: error('reset_failed') });
        assert.ok(reset.includes('초기화를 마치지 못했습니다'));
        assert.ok(reset.includes('초기화 다시 시도'));
        """)

    def test_retry_of_a_failed_new_lesson_keeps_history(self):
        retry = APP_SOURCE.split("const retryRecoveryOperation", 1)[1].split("const resetAfterOperationError", 1)[0]
        self.assertIn("void resetSession({ keepHistory: Boolean(recovery.keepHistory) })", retry)
        self.assertIn("onNewLesson={() => resetSession({ keepHistory: true })}", APP_SOURCE)


class TestTopBarJourney(unittest.TestCase):
    def test_new_lesson_item_and_built_board_opens_classin_instead_of_rebuilding(self):
        run_toolbar_node(r"""
        const onPublish = () => {}, onOpenPublished = () => {}, onNewLesson = () => {};
        const more = tree => walk(tree).find(node => node.props.id === 'topbar-more').props.items;
        const exportItems = tree => walk(tree).find(node => node.props.id === 'topbar-export').props.items;

        const item = more(sandbox.TopBar({ ...base, onNewLesson })).find(entry => entry.id === 'new-lesson');
        assert.equal(item.label, '새 수업 시작');
        assert.equal(item.onSelect, onNewLesson);
        assert.ok(!item.disabled);
        assert.equal(more(sandbox.TopBar({ ...base, onNewLesson, operationBusy: true })).find(entry => entry.id === 'new-lesson').disabled, true);
        assert.equal(more(sandbox.TopBar({ ...base, total: 0, onNewLesson })).some(entry => entry.id === 'new-lesson'), false);

        const built = sandbox.TopBar({ ...base, onPublish, published: true, onOpenPublished });
        const main = walk(built).find(node => node.props.onClick === onOpenPublished);
        assert.ok(text(main).includes('ClassIn에서 열기'));
        assert.equal(walk(built).some(node => node.props.onClick === onPublish), false, 'the done button must not rebuild');
        assert.equal(exportItems(built).find(entry => entry.id === 'republish').onSelect, onPublish);

        const missingFile = sandbox.TopBar({ ...base, onPublish, published: true, onOpenPublished: null });
        assert.ok(text(walk(missingFile).find(node => node.props.onClick === onPublish)).includes('EDB 제작'));
        assert.equal(exportItems(missingFile).some(entry => entry.id === 'republish'), false);
        """)

    def test_main_button_label_matches_what_a_click_does(self):
        run_toolbar_node(r"""
        const calls = [];
        const onPublish = () => calls.push('build'), onOpenPublished = () => calls.push('open');
        const main = props => walk(sandbox.TopBar({ ...base, onPublish, ...props })).find(node => node.type === 'button'
          && (node.props.onClick === onPublish || node.props.onClick === onOpenPublished));
        const click = button => { calls.length = 0; button.props.onClick(); return calls.join(); };

        const built = main({ published: true, onOpenPublished });
        assert.ok(text(built).includes('ClassIn에서 열기'));
        assert.equal(click(built), 'open');

        const split = main({ published: true, onOpenPublished, openPublishedFolder: true });
        assert.ok(text(split).includes('폴더 열기'));
        assert.equal(text(split).includes('ClassIn에서 열기'), false);
        assert.match(split.props['data-tooltip'], /폴더/);
        assert.equal(click(split), 'open');

        for (const props of [{ published: true, onOpenPublished: null }, { published: false, onOpenPublished }]) {
          const button = main(props);
          assert.ok(text(button).includes('EDB 제작'), JSON.stringify(Object.keys(props)));
          assert.equal(click(button), 'build');
        }
        """)

    def test_top_bar_opens_only_a_build_made_from_the_current_board(self):
        run_node(NODE_SETUP + r"""
        const gate = section('  // Open only a build made from this exact board', '  return (');
        function wiring(overrides = {}) {
          const calls = [];
          const summary = { canOpenEdbFile: true, canOpenOutputDir: true, edbSplit: false, outputDir: 'C:/out',
            ...overrides.summary };
          const sandbox = {
            published: true, sessionEditState: { dirty: false },
            session: { problems: [{ id: 'p1' }] }, builtBoardRef: { current: '["p1"]' },
            sessionEditorDraftSignature: s => JSON.stringify((s?.problems || []).map(problem => problem.id)),
            sessionPublishSummary: () => summary,
            openPublishedEdb: value => calls.push(['edb', value]), openOutputFolder: path => calls.push(['folder', path]),
            ...overrides,
          };
          vm.createContext(sandbox);
          vm.runInContext(gate + '\nglobalThis.result = { open: openLatestPublish, folder: openPublishedFolder };', sandbox);
          return { open: sandbox.result.open, folder: sandbox.result.folder, calls };
        }
        let gateResult = wiring();
        gateResult.open();
        assert.equal(gateResult.calls[0][0], 'edb');
        assert.equal(gateResult.folder, false);

        gateResult = wiring({ summary: { edbSplit: true } });
        gateResult.open();
        assert.deepEqual(gateResult.calls, [['folder', 'C:/out']], 'a split build opens its folder, not part 1');
        assert.equal(gateResult.folder, true);

        gateResult = wiring({ summary: { edbSplit: true, canOpenOutputDir: false } });
        gateResult.open();
        assert.equal(gateResult.calls[0][0], 'edb');
        assert.equal(gateResult.folder, false);

        assert.equal(wiring({ summary: { canOpenEdbFile: false, canOpenOutputDir: false } }).open, null);

        for (const stale of [
          { session: { problems: [{ id: 'p1' }, { id: 'p2' }] } },
          { builtBoardRef: { current: '' } },
          { sessionEditState: { dirty: true } },
          { published: false },
        ]) {
          gateResult = wiring(stale);
          assert.equal(gateResult.open, null, `an older build must not be opened as current: ${JSON.stringify(stale)}`);
          assert.equal(gateResult.folder, false);
        }
        """)

    def test_a_finished_build_records_the_board_it_was_made_from(self):
        run_node(PUBLISH_SETUP + r"""
        (async () => {
          const { sandbox, events } = publishSandbox({ ok: true, session: { problems: [{ id: 'p1' }, { id: 'p2' }] } });
          await sandbox.publish();
          assert.equal(events.errors.length, 0);
          assert.equal(sandbox.builtBoardRef.current, 'sig:p1,p2');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestPublishResultJourney(unittest.TestCase):
    def test_open_and_folder_are_visible_while_collapsed_and_handoff_is_gone(self):
        run_node(RENDER_SETUP + r"""
        const sandbox = renderSandbox();
        const opened = [], folders = [];
        const summary = { statusLabel: '제작 완료', validated: true, recordCount: 2, edbFileName: '수업.edb',
          edbParts: [], edbSplit: false, edbPartCount: 1, canDownload: true, canOpenEdbFile: true,
          canOpenOutputDir: true, outputDir: 'C:/out', canOpenClassinHandoff: true };
        Object.assign(sandbox, {
          publishOpen: false, useMemo: fn => fn(), useEffect() {},
          useState: () => [sandbox.publishOpen, update => { sandbox.publishOpen = update(sandbox.publishOpen); }],
          sessionPublishSummary: () => summary, sessionPublishHistory: () => [summary, summary],
          openPublishedEdb: value => opened.push(value), openOutputFolder: value => folders.push(value),
          formatPublishHistoryMeta: () => '',
        });
        compile(sandbox, section('function PublishResultPanel(', 'function SidebarDisclosure('));
        const props = { session: {}, visible: true, onDownloadPublish() {}, publishDownloadBusy: false };
        let tree = sandbox.PublishResultPanel(props);
        const buttons = walk(findClass(tree, 'publish-result-primary-actions')).filter(node => node.type === 'button');
        assert.equal(buttons.length, 3);
        buttons.find(node => text(node).includes('ClassIn 열기')).props.onClick();
        buttons.find(node => text(node).includes('폴더')).props.onClick();
        assert.deepEqual(opened, [summary]);
        assert.deepEqual(folders, ['C:/out']);
        sandbox.publishOpen = true;
        tree = sandbox.PublishResultPanel(props);
        assert.equal(text(tree).includes('ClassIn 검수 파일'), false);
        assert.equal(walk(tree).filter(node => node.type === 'button' && text(node) === 'check').length, 0,
          'history rows no longer offer the developer handoff report');
        """)

    def test_publish_success_offers_the_next_step(self):
        publish = APP_SOURCE.split("  const onPublish = async () => {", 1)[1].split("  const handlePublishDownload", 1)[0]
        self.assertIn("'ClassIn에서 열기', () => openPublishedEdb(normalizedPublishSummary)", publish)
        self.assertIn("'폴더 열기', () => openOutputFolder(normalizedPublishSummary.outputDir)", publish)
        self.assertIn("normalizedPublishSummary.edbSplit && normalizedPublishSummary.canOpenOutputDir", publish)

    def test_developer_handoff_report_is_not_reachable_from_the_ui(self):
        self.assertNotIn("openClassinHandoff", APP_SOURCE)
        self.assertNotIn("ClassIn 검수 열기", APP_SOURCE)
        self.assertNotIn("ClassIn 검수 파일 열기", APP_SOURCE)
        self.assertIn("검수 완료", APP_SOURCE)


class TestImportNotice(unittest.TestCase):
    def test_notice_counts_only_the_new_import(self):
        run_node(NODE_SETUP + r"""
        const sandbox = {
          sessionReviewSummary: () => ({ reviewTargets: [
            { id: 'p1', pageId: 'pg1', status: 'check_needed' },
            { id: 'p4', pageId: 'pg2', status: 'check_needed' },
            { id: 'p3', pageId: 'pg2', status: 'normal' },
            { id: 'page-pg3', pageId: 'pg3', status: 'check_needed' },
          ] }),
          countSessionProblems: list => ({ total: list.length, core: list.length, supplemental: 0 }),
          reviewStageCopyFor: (_session, counts) => ({ countLabel: `${counts.core}문항` }),
        };
        vm.createContext(sandbox);
        vm.runInContext(section('function importResultNotice(', 'function mergeRetryCandidateIntoCurrent('), sandbox);
        const restored = { problems: [{ id: 'p1' }, { id: 'p2' }, { id: 'p3' }, { id: 'p4' }] };
        const merged = sandbox.importResultNotice({ scopeProblemIds: ['p3', 'p4'], scopePageIds: ['pg2', 'pg3'] }, {}, restored, 'AI 재시도');
        assert.equal(merged.checkCount, 2, 'old checks are not counted again');
        assert.equal(merged.firstNewProblemId, 'p3');
        assert.equal(merged.message, 'AI 재시도 · 2문항 칠판에 넣었어요 · 확인 필요 2개');
        sandbox.sessionReviewSummary = () => ({ reviewTargets: [] });
        const clean = sandbox.importResultNotice({ scopeProblemIds: ['p1'], scopePageIds: [] }, {}, restored);
        assert.equal(clean.checkCount, 0);
        assert.equal(clean.message, '1문항 칠판에 넣었어요 · 순서를 확인하고 EDB 제작을 누르세요');
        assert.equal(sandbox.importResultNotice(null, {}, restored).firstNewProblemId, null);
        """)

    def test_apply_selects_new_items_on_merge_and_announces_the_result(self):
        run_node(RECOGNITION_SETUP + r"""
        (async () => {
          const notice = { checkCount: 2, firstNewProblemId: 'new', message: '1문항 칠판에 넣었어요 · 확인 필요 2개' };
          let opened = 0;
          let { sandbox: s, events } = transaction({ importResultNotice: () => notice,
            openPendingReviewRef: { current: () => { opened += 1; } } });
          await s.applyResult();
          assert.deepEqual(events.filter(e => e[0] === 'active'), [['active', 'new']]);
          const action = events.find(e => e[0] === 'actionToast');
          assert.equal(action[1], notice.message);
          assert.equal(action[2], '확인하기');
          action[3]();
          assert.equal(opened, 1, 'the action reads the latest review target through the ref');

          ({ sandbox: s, events } = transaction({ session: null,
            importResultNotice: () => ({ checkCount: 0, firstNewProblemId: 'new', message: 'done' }) }));
          await s.applyResult();
          assert.equal(events.filter(e => e[0] === 'active').length, 0, 'a first import keeps item 1 selected');
          assert.deepEqual(events.filter(e => e[0] === 'toast').map(e => e[1]), ['done']);

          ({ sandbox: s, events } = transaction({
            recognitionReview: { kind: 'queue-recognition', queueGeneration: 1, fileKeys: ['picked'], manualSplit: true,
              incomingSession: { problems: [{ id: 'new' }], pages: [{ id: 'pg' }] } },
            importResultNotice: () => { throw new Error('manual split opens review instead'); } }));
          await s.applyResult();
          assert.equal(events.filter(e => ['toast', 'actionToast', 'error'].includes(e[0])).length, 0);
        })().catch(e => { console.error(e); process.exitCode = 1; });
        """)


class TestRecentRestoreAndHome(unittest.TestCase):
    def test_restore_asks_only_when_work_is_unsaved(self):
        run_node(NODE_SETUP + r"""
        function restoreSandbox(overrides = {}) {
          const state = { confirms: 0, posts: 0, history: null };
          const sandbox = {
            useCallback: fn => fn,
            restoringSessionId: null, initialSessionLoaded: true, loading: null,
            hasRunningBackgroundJobs: false, hasPendingSessionConflict: false,
            session: { problems: [{ id: 'p1' }] }, items: [], fileName: 'a', boardColumns: 1, layoutGapMode: 'classin-grid',
            reviewEditorActive: false, recognitionReview: null, edited: false,
            materializeSessionForItems: s => ({ ...s, ...(sandbox.edited ? { edited: true } : {}) }),
            sessionEditorDraftSignature: s => JSON.stringify(s),
            window: { confirm: () => { state.confirms += 1; return false; } },
            postRestoreSessionHistory: async () => { state.posts += 1; return { session: { problems: [{ id: 'p9' }] }, history: [] }; },
            setHistoryStack: value => { state.history = value; },
            applySession: () => true, setRecentSessionsAuthoritative() {}, activateOperationRecovery() {},
            clearOperationRecovery() {}, showToast() {}, setRestoringSessionId() {}, setLoading() {},
            setRecognitionReview() {}, setReviewFocus() {},
            ...overrides,
          };
          ['restoreInFlightRef', 'refreshInFlightRef', 'resetInFlightRef', 'publishInFlightRef', 'recognitionInFlightRef',
            'queueRegistrationInFlightRef', 'sessionRecognitionInFlightRef', 'mutatingRef'].forEach(name => { sandbox[name] = { current: false }; });
          vm.createContext(sandbox);
          vm.runInContext(section('  const restoreRecentSession = useCallback(', '  const triggerUpload =')
            + '\nglobalThis.restoreRecentSession = restoreRecentSession;', sandbox);
          return { sandbox, state };
        }
        (async () => {
          let { sandbox, state } = restoreSandbox();
          assert.equal(await sandbox.restoreRecentSession('h1'), true);
          assert.equal(state.confirms, 0, 'a saved lesson switches without the overwrite warning');
          assert.equal(typeof state.history, 'function', 'the current lesson still goes on the undo stack');
          for (const unsaved of [{ edited: true }, { reviewEditorActive: true }, { recognitionReview: { kind: 'queue-recognition' } }]) {
            ({ sandbox, state } = restoreSandbox(unsaved));
            assert.equal(await sandbox.restoreRecentSession('h1'), false);
            assert.equal(state.confirms, 1);
            assert.equal(state.posts, 0);
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_home_cards_mark_lessons_that_already_have_an_edb(self):
        run_node(HOME_SETUP + r"""
        const recentSessions = [
          { id: 'a', sessionName: '수학', problemCount: 2, publishSummary: { edbPath: 'C:/a.edb' } },
          { id: 'b', sessionName: '국어', problemCount: 3 },
          { id: 'c', sessionName: '영어', problemCount: 1, publish_summary: { edbFileExists: false } },
        ];
        const cards = withClass(render({ recentSessions }), 'home-recent-card');
        assert.deepEqual(cards.map(card => text(card).includes('EDB 제작 완료')), [true, false, false]);
        assert.ok(cards.every(card => card.type === 'button'), 'the card stays one native button');
        """)


class TestSampleLesson(unittest.TestCase):
    def test_sample_uses_the_full_sample_board_without_dummy_wording(self):
        self.assertNotIn("더미", APP_SOURCE)
        mock = APP_SOURCE.split("  const showMockItems = ", 1)[1].split("  const hideMockItems", 1)[0]
        self.assertIn("setFileName('샘플 수업')", mock)
        run_node(NODE_SETUP + r"""
        const calls = [];
        const sandbox = { session: null, loading: null, pendingFiles: [],
          showMockItems: message => calls.push(['sample', message]), showToast: message => calls.push(['toast', message]) };
        vm.createContext(sandbox);
        vm.runInContext(section('  const addMockSample = () => {', '  // when backend is reachable')
          + '\nglobalThis.addMockSample = addMockSample;', sandbox);
        sandbox.addMockSample();
        assert.equal(calls.length, 1);
        assert.equal(calls[0][0], 'sample');
        assert.ok(calls[0][1].includes('샘플 수업'));
        sandbox.pendingFiles = [{ name: 'a.pdf' }];
        sandbox.addMockSample();
        assert.deepEqual(calls[1][0], 'toast');
        """)

    def test_build_on_the_sample_points_to_a_real_file(self):
        run_node(NODE_SETUP + r"""
        (async () => {
          for (const usingMock of [true, false]) {
            const state = { toasts: [], uploads: 0 };
            const sandbox = { session: null, usingMock, proceeded: false,
              showToast: message => state.toasts.push(message), triggerUpload: () => { state.uploads += 1; } };
            vm.createContext(sandbox);
            vm.runInContext(section('  const onPublish = async () => {', '    if (hasRunningQueueRecognition')
              + 'proceeded = true; }; globalThis.publish = onPublish;', sandbox);
            await sandbox.publish();
            assert.equal(sandbox.proceeded, false);
            assert.equal(state.uploads, usingMock ? 1 : 0);
            assert.ok(state.toasts[0].includes(usingMock ? '파일을 올리면' : '먼저 파일을 추가'));
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestUpdateRelaunch(unittest.TestCase):
    def test_tab_reloads_after_restart_and_lifts_overlay_when_server_never_stopped(self):
        run_node(NODE_SETUP + r"""
        function effect(startedAt, health) {
          const state = { reloads: 0, cleared: [], checks: 0 };
          let tick = null;
          const sandbox = {
            useEffect: fn => { fn(); },
            updateRestarting: { version: '0.3.0', startedAt },
            probeLocalServerHealth: async () => health.shift(),
            window: { setInterval: fn => { tick = fn; return 1; }, clearInterval() {}, location: { reload: () => { state.reloads += 1; } } },
            setUpdateRestarting: value => state.cleared.push(value),
            checkForUpdates: () => { state.checks += 1; },
          };
          vm.createContext(sandbox);
          vm.runInContext(section('  // Once a server answers again after going down', '  // The PIN install is one long request'), sandbox);
          return { state, tick: () => tick() };
        }
        (async () => {
          let run = effect(Date.now(), [false, true]);
          await run.tick(); await run.tick();
          assert.equal(run.state.reloads, 1, 'the old tab must load the new screen code');
          assert.equal(run.state.cleared.length, 0);
          run = effect(Date.now() - 151000, [true]);
          await run.tick();
          assert.equal(run.state.reloads, 0, 'same version, no reload');
          assert.deepEqual(run.state.cleared, [null]);
          assert.equal(run.state.checks, 1);
          run = effect(Date.now(), [true]);
          await run.tick();
          assert.equal(run.state.reloads + run.state.cleared.length, 0);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_overlay_ends_with_a_message_and_recheck_button_when_the_app_never_returns(self):
        run_node(NODE_SETUP + r"""
        const Babel = require('./scripts/vendor/babel.min.js');
        const slots = []; let index = 0;
        const sandbox = {
          React: { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }) },
          useState: initial => { const i = index++; if (!(i in slots)) slots[i] = initial;
            return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
          useEffect: fn => { fn(); }, setInterval: () => 1, clearInterval() {},
        };
        vm.createContext(sandbox);
        vm.runInContext(Babel.transform(section('function LoadingOverlay(', 'function BackgroundJobsPanel('), { presets: ['react'] }).code, sandbox);
        const walk = node => node && typeof node === 'object' ? [node, ...(node.children || []).flatMap(walk)] : [];
        const text = node => typeof node === 'string' ? node : (node?.children || []).map(text).join('');
        let checks = 0;
        const stuck = { afterSec: 240, label: '업데이트 후 앱이 아직 다시 열리지 않았어요', hint: '안내', actionLabel: '다시 확인',
          onAction: async () => { checks += 1; } };
        const render = startedAt => { index = 0; return sandbox.LoadingOverlay({ label: '설치 중', startedAt, stuck }); };
        render(Date.now() - 10000);
        let tree = render(Date.now() - 10000);
        assert.ok(walk(tree).some(node => node.props?.className === 'loading-spinner'));
        assert.equal(walk(tree).some(node => node.type === 'button'), false);
        slots.length = 0;
        render(Date.now() - 241000);
        tree = render(Date.now() - 241000);
        assert.equal(walk(tree).some(node => node.props?.className === 'loading-spinner'), false, 'no endless spinner');
        assert.ok(text(tree).includes('아직 다시 열리지 않았어요'));
        const button = walk(tree).find(node => node.type === 'button');
        assert.equal(text(button), '다시 확인');
        button.props.onClick().then(() => assert.equal(checks, 1));
        """)

    def test_stuck_copy_is_os_aware_and_rechecks_before_reloading(self):
        overlay = APP_SOURCE.split("{updateRestarting && (", 1)[1].split("<TweaksPanel", 1)[0]
        self.assertIn("afterSec: 240", overlay)
        self.assertIn("IS_MAC_PLATFORM ? '응용 프로그램 폴더에서 ClassInEDBMVP' : '시작 메뉴에서 ClassIn EDB'", overlay)
        self.assertIn("if (await probeLocalServerHealth(1500)) window.location.reload();", overlay)


if __name__ == "__main__":
    unittest.main()
