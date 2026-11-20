"""Round-2 board-undo chain: conflict adopt with undo (R1), unnumbered titles (R3),
passage re-extract merge (R24), plain board drag feedback (R35), 지문 전체 너비
preset (R61), failed reorder rollback (R66) and undo during session loads (R76).
The quit prompt (R72) is covered in test_dev_srv_jobs.py, whose sandbox follows the quit code."""
from __future__ import annotations

import unittest
from pathlib import Path

import build_problem_board_edb as board
from test_dev_ui_undo import UNDO_SETUP
from test_ui_placement_details import NODE_SETUP, run_node
from test_ui_processing_step_confirm import CONFIRM_SETUP


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")


class TestMutationConflictAdopt(unittest.TestCase):
    def test_mutation_conflict_keeps_the_board_one_undo_away_and_takes_the_latest_name(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        (async () => {
          for (const stale of [false, true]) {
            const { sandbox, state, render } = controller();
            Object.assign(sandbox, {
              setFileName: value => { sandbox.fileName = value; },
              sessionResponseRevisionIsStale: () => stale, captureSessionRevision() {},
            });
            sandbox.items = sandbox.items.map((item, index) => index ? item : { ...item, placementScaleRatio: 2.5 });
            assert.equal(sandbox.deriveSessionEditState(sandbox.session, sandbox.items, sandbox.fileName, 1, 'compact').dirty,
              true, 'an unsaved board edit is on screen');
            // Another window (or a restarted app) saved a different lesson first.
            const other = JSON.parse(JSON.stringify(sandbox.session));
            other.session_name = '새 수업';
            other.problems = [{ ...other.problems[1], id: 'y1', title: '문항 1' }];
            other.pages = [{ id: 'page-y', problemIds: ['y1'] }];
            sandbox.postRestore = async () => {
              throw Object.assign(new Error('conflict'), { code: 'session_conflict', latestSession: other,
                hasLatestSessionState: true, sessionRevision: 3, sessionEpoch: 'e' });
            };
            render();
            assert.equal(await sandbox.confirm('p1', { problemIds: ['p1'], processingStep: 's2' }), false);
            if (stale) {
              assert.equal(sandbox.history.length, 0, 'an older reply changes nothing');
              assert.equal(sandbox.items.map(item => item.id).join(','), 'p1,p2');
              assert.equal(sandbox.items[0].placementScaleRatio, 2.5);
              assert.equal(sandbox.fileName, '처리 방식과 자르기 보존');
              assert.equal(state.errors.length, 1);
              assert.equal(state.toasts.length, 0);
              continue;
            }
            assert.equal(sandbox.history.length, 1, 'Ctrl+Z brings the replaced board back');
            assert.equal(sandbox.history[0].problems[0].placementScaleRatio, 2.5, 'with its unsaved edit');
            assert.equal(sandbox.fileName, '새 수업', 'the shown name follows the adopted lesson');
            assert.equal(sandbox.items.map(item => item.id).join(','), 'y1');
            assert.equal(sandbox.deriveSessionEditState(sandbox.session, sandbox.items, sandbox.fileName, 1,
              sandbox.layoutGapMode).dirty, false, '변경 저장 has no stale rename to write onto it');
            assert.equal(state.errors.length, 0);
            assert.equal(state.toasts.length, 1);
            assert.match(state.toasts[0], /Ctrl\+Z/);
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestUnnumberedItemTitles(unittest.TestCase):
    def test_unnumbered_items_are_not_titled_with_the_upload_digest(self) -> None:
        page_id = "a" * 64 + "_휴대폰_사진_0123456789-page-001"
        self.assertEqual("문항 01", board._normalize_problem_title("", 0, page_id))
        self.assertEqual("문항 01", board._normalize_problem_title("문항 1", 0, page_id))
        # A lesson saved before the fix is renamed on its next EDB 제작.
        self.assertEqual("문항 10", board._normalize_problem_title(f"문항 01 · {page_id}", 9, page_id))
        self.assertEqual("문항 7", board._normalize_problem_title("", 0, page_id, 7))
        self.assertEqual("이차함수", board._normalize_problem_title("이차함수", 0, page_id))
        run_node(f"""
const assert = require('node:assert/strict');
const {{ problemDisplayName: name }} = require('./ui_prototype/reorder.js');
assert.equal(name({{ name: '문항 01 · {page_id}' }}, 9), '문제 10');
assert.equal(name({{ name: '문항 01 · {page_id} (위)' }}, 9), '문제 10 · 위쪽');
assert.equal(name({{ name: '문항 01 · {page_id} 외 1건' }}, 9), '문제 10');
assert.equal(name({{ name: '문항 01' }}, 9), '문항 01');
""")


class TestPassageReextractMerge(unittest.TestCase):
    def test_reextract_keeps_hand_edited_and_unfound_passages(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = {
          cloneSession: value => (value == null ? value : JSON.parse(JSON.stringify(value))),
          listUnique: values => Array.from(new Set(values)),
          isPassageFragmentProblem: problem => problem?.passageRole === 'passage_fragment',
          passageGroupIdFor: problem => String(problem?.passageGroupId || ''),
          makeUniqueId(value, used) {
            let next = String(value || 'item');
            for (let suffix = 2; used.has(next); suffix += 1) next = `${value}-${suffix}`;
            used.add(next);
            return next;
          },
          applyProblemCounts(session, problems) { session.problem_count = problems.length; },
          manualCropIsActive: crop => Object.values(crop || {}).some(value => Math.abs(Number(value) || 0) > 0.0001),
        };
        vm.createContext(sandbox);
        vm.runInContext(section('function sessionReusableSourcePaths', 'function reviewScopeForNewSession'), sandbox);
        const passage = (id, group, extra = {}) => ({
          id, passageRole: 'passage_fragment', passageGroupId: group, sourcePageId: 'page-1', ...extra,
        });
        const child = (id, group) => ({ id, passageRole: 'child_question', passageGroupId: group, sourcePageId: 'page-1' });
        const base = {
          problems: [
            passage('stitched', 'g-2-4', { manualStitch: true, imagePath: 'stitched.png', placementScaleRatio: 1, step: 's3' }),
            child('q2', 'g-2-4'),
            passage('cropped', 'g-5-6', { manualCrop: { leftRatio: 0, rightRatio: 0, topRatio: 0.1, bottomRatio: 0 }, imagePath: 'cropped.png' }),
            child('q5', 'g-5-6'),
            passage('missed', 'g-7-8', { imagePath: 'missed.png' }),
            child('q7', 'g-7-8'),
            passage('auto', 'g-9-10', { imagePath: 'old-auto.png' }),
            child('q9', 'g-9-10'),
            child('q11', 'g-11-12'),
          ],
          pages: [{ id: 'page-1', sourceImagePath: 'page-1.png',
            problemIds: ['stitched', 'q2', 'cropped', 'q5', 'missed', 'q7', 'auto', 'q9', 'q11'] }],
        };
        const incoming = {
          problems: ['g-2-4', 'g-5-6', 'g-9-10', 'g-11-12'].map(group => (
            passage(`new-${group}`, group, { imagePath: `${group}.png`, placementScaleRatio: 3 })
          )),
          pages: [{ id: 'page-1', sourceImagePath: 'page-1.png', problemIds: [] }],
        };
        const merged = sandbox.mergeReextractedSharedPassages(base, incoming, 'lesson');
        assert.deepEqual(Array.from(merged.problems, problem => problem.id),
          ['stitched', 'q2', 'cropped', 'q5', 'missed', 'q7', 'new-g-9-10', 'q9', 'new-g-11-12', 'q11']);
        const byId = id => merged.problems.find(problem => problem.id === id);
        assert.equal(byId('stitched').imagePath, 'stitched.png', 'a stitched passage is not replaced');
        assert.equal(byId('stitched').placementScaleRatio, 1);
        assert.equal(byId('stitched').step, 's3');
        assert.equal(byId('cropped').imagePath, 'cropped.png', 'a re-cropped passage is not replaced');
        assert.equal(byId('missed').imagePath, 'missed.png', 'a passage the re-run missed stays');
        assert.deepEqual(Array.from(merged.pages[0].problemIds), Array.from(merged.problems, problem => problem.id));
        """)


class TestPlainBoardDragFeedback(unittest.TestCase):
    def test_plain_drag_that_adds_blank_pages_says_so(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const shown = [null];
        Object.assign(sandbox, {
          boardDropTargetRef: { current: null },
          positionDragRef: { current: { reorderMode: false, smartMode: false, item: { id: 'p1', heightFrac: .8 },
            pendingPlacement: { extraSlots: 0 } } },
          setBoardDropTarget: update => shown.push(update(shown[shown.length - 1])),
        });
        vm.runInContext(section('  const setCurrentBoardDropTarget = (target) => {', '  const findBoardDropTarget =')
          + section('  const updateBoardDropTarget =', '  const stopBoardAutoScroll =')
          + '\nglobalThis.update = updateBoardDropTarget;', sandbox);
        const drag = sandbox.positionDragRef.current;
        assert.equal(sandbox.update(0, 0, 'p1'), null, 'a plain drag never picks a reorder target');
        assert.equal(shown.at(-1), null, 'moving inside its own page shows nothing');
        drag.pendingPlacement = { extraSlots: 1 };
        sandbox.update(0, 0, 'p1');
        assert.deepEqual({ ...shown.at(-1) }, { mode: 'space', pages: 1 });
        drag.pendingPlacement = { extraSlots: 2 };
        sandbox.update(0, 0, 'p1');
        assert.equal(shown.at(-1).pages, 2, 'the count follows the pointer');
        drag.item.placementExtraSlots = 2;
        sandbox.update(0, 0, 'p1');
        assert.equal(shown.at(-1), null, 'space the problem already had is not new');
        """)
        board_stage = SOURCE.split("function BoardStage({", 1)[1].split("function HomeDashboard(", 1)[0]
        self.assertIn("{boardDropTarget?.mode === 'space' && <div className=\"stage-drag-feedback\" role=\"status\"", board_stage)
        self.assertIn("`빈 쪽 ${boardDropTarget.pages}개 추가 · 순서는 그대로 · 순서 이동은 ${ALTERNATE_MODIFIER_LABEL}+드래그`",
                      board_stage)


class TestPassageWidthPreset(unittest.TestCase):
    def test_passage_only_preset_widens_a_recognised_passage_to_the_reading_width(self) -> None:
        run_node(NODE_SETUP + r"""
        const sandbox = placementSandbox();
        const updates = [];
        const passage = sandbox.reflowItemsForBoardOrder([{ id: 'p', heightFrac: 2.4, inputIntent: 'multi-problem',
          passageRole: 'passage_fragment', imageRecordGeometry: { baseDisplayWidthPx: 301, baseHeightPages: 2.4,
            reservedHeightPages: 2.4, maxDisplayWidthPx: 1142 } }], 1.2, 2, 'classin-grid')[0];
        Object.assign(sandbox, {
          panelBusy: false, selectedPassageFragments: [passage], selectedPassageGroupId: '',
          setPlacementPreset() {}, setBoardColumns() {}, setPlacementScope() {},
          setPlacements: batch => updates.push(...batch),
        });
        vm.runInContext(section('  const applyPlacementPreset = (preset) => {', '  const commitPlacement = async () => {')
          + '\nglobalThis.applyPlacementPreset = applyPlacementPreset;', sandbox);
        sandbox.applyPlacementPreset('passage-only');
        assert.equal(updates.length, 1);
        const widened = sandbox.applyPlacementPatchToItem(passage, updates[0].patch);
        assert.equal(widened.placementScaleRatio, 3, '지문 전체 너비 uses the same 3x as 너비 맞춤');
        assert.equal(Math.round(sandbox.itemRenderedScaleRatio(widened) * 301), 903);
        """)


class TestFailedReorderRollback(unittest.TestCase):
    def test_failed_reorder_keeps_unsaved_board_edits_unsaved(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          const { sandbox, state } = undoSandbox();
          const helpers = require('./ui_prototype/reorder.js');
          Object.assign(sandbox, {
            PRIMARY_MODIFIER_LABEL: 'Ctrl', moveSequenceRef: { current: 0 },
            moveFeedbackTimerRef: { current: null }, setMoveFeedback() {},
            reorderItemsForDrop: helpers.reorderItemsForDrop, reorderItemGroupForDrop: helpers.reorderItemGroupForDrop,
          });
          Object.assign(sandbox.window, { setTimeout: () => 1, clearTimeout() {} });
          vm.runInContext(section('  const reorder = (fromId, toId', '  const removeItem = async')
            + '\nglobalThis.reorder = reorder;', sandbox);
          const saved = sandbox.session;
          sandbox.setPlacements([{ id: 'p1', patch: { xRatio: 0.3 } }]);
          state.restoreError = new Error('Failed to fetch');
          assert.equal(sandbox.reorder('p1', 'p2', 'after'), true);
          for (let index = 0; index < 5; index++) await new Promise(resolve => setImmediate(resolve));
          assert.equal(sandbox.session, saved, 'the saved lesson stays what the app holds');
          assert.equal(sandbox.items.map(item => item.id).join(','), 'p1,p2', 'the order rolls back');
          assert.equal(sandbox.items[0].placementXRatio, 0.3, 'the unsaved move stays on the board');
          const edit = sandbox.deriveSessionEditState(sandbox.session, sandbox.items, sandbox.fileName, 1, 'classin-grid');
          assert.equal(edit.dirty, true, 'still unsaved: exit warnings and the crash draft stay on');
          assert.equal(edit.label, '수업 변경 있음 · 저장 필요');
          assert.equal(sandbox.historyStack.length, 0);
          assert.equal(state.errors.length, 1);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


class TestUndoWaitsForSessionLoads(unittest.TestCase):
    def test_undo_and_redo_are_ignored_while_a_lesson_opens_or_refreshes(self) -> None:
        run_node(UNDO_SETUP + r"""
        (async () => {
          for (const kind of ['open', 'refresh']) {
            for (const redo of [false, true]) {
              const { sandbox, state } = undoSandbox();
              withServerStep(sandbox);
              if (redo) await sandbox.undoMutation();
              const restoresBefore = state.restores;
              let finish;
              sandbox.postRestoreSessionHistory = sandbox.fetchLatestSession = () => new Promise(resolve => { finish = resolve; });
              const running = kind === 'open' ? sandbox.restoreRecentSession('h1') : sandbox.refreshSession();
              assert.equal(await (redo ? sandbox.redoMutation() : sandbox.undoMutation()), false, `${kind}: shortcut ignored`);
              assert.equal(state.restores, restoresBefore, `${kind}: no restore races the load`);
              const next = { ...JSON.parse(JSON.stringify(sandbox.session)), session_name: '열린 수업' };
              finish(kind === 'open' ? { session: next, history: [] } : next);
              await running;
              if (redo) continue;
              await sandbox.undoMutation();
              assert.equal(state.restores, restoresBefore + 1, `${kind}: Ctrl+Z works again once it finished`);
            }
          }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


if __name__ == "__main__":
    unittest.main()
