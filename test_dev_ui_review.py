"""Regression tests for the review/quality UX fixes (C42-C51)."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import app_server
import build_problem_board_edb as board
from test_ui_processing_step_confirm import CONFIRM_SETUP
from test_ui_placement_details import run_node


PROJECT_ROOT = Path(__file__).resolve().parent


def node_json(script: str, payload: object) -> object:
    """Run a node snippet with PAYLOAD bound to the JSON payload; return its printed JSON."""
    result = subprocess.run(
        ["node", "-e", f"const PAYLOAD = {json.dumps(payload)};\n{script}"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def guard_issue_counts(problems: list[dict]) -> dict:
    return node_json(
        """
        const guard = require('./ui_prototype/publish_guard.js');
        console.log(JSON.stringify({
          overlap: guard.findSourceProblemOverlaps(PAYLOAD).length,
          reuse: guard.findPassageGroupSourceReuse(PAYLOAD).length,
        }));
        """,
        problems,
    )


def overlapping_pair(image_dir: Path, *, passage: bool = False) -> list[dict]:
    problems = []
    for index, top in enumerate((100, 110), start=1):
        path = image_dir / f"p{index}.png"
        Image.new("RGB", (40, 20), "white").save(path)
        problem = {
            "id": f"p{index}",
            "title": f"{index}.",
            "sourcePageId": "page-1",
            "imagePath": path.resolve().as_uri(),
            "bbox": {"left": 40, "top": top, "width": 500, "height": 300},
            "riskFlags": ["source_problem_bbox_overlap"],
            "reviewStatus": "check_needed",
        }
        if passage:
            problem["passageGroupId"] = "passage-1-2"
            problem["passageRole"] = "child_question"
        problems.append(problem)
    return problems


def python_issue_counts(problems: list[dict]) -> dict:
    return {
        "overlap": len(board._classin_source_bbox_overlap_issues(problems)),
        "reuse": len(board._classin_passage_group_source_reuse_issues(problems)),
    }


class TestHwpTextFallbackStaysExemptAfterConfirm(unittest.TestCase):
    def problems(self, source: str) -> list[dict]:
        restored = {
            "id": "p19", "title": "19.", "sourcePageId": "page-1",
            # Full-width band at the top of the page, as _hwp_text_fallback_bbox builds it.
            "bbox": {"left": 0, "top": 0, "width": 640, "height": 400},
            "riskFlags": [], "reviewStatus": "normal",  # 확인 cleared the fallback flag
            "problemNumberSource": source,
        }
        neighbour = {
            "id": "p20", "title": "20.", "sourcePageId": "page-1",
            "bbox": {"left": 40, "top": 60, "width": 500, "height": 200},
            "riskFlags": [], "reviewStatus": "normal",
        }
        return [restored, neighbour]

    def test_confirmed_restored_question_is_not_an_overlap(self) -> None:
        problems = self.problems("hwp_text_snippet")
        self.assertEqual({"overlap": 0, "reuse": 0}, python_issue_counts(problems))
        self.assertEqual({"overlap": 0, "reuse": 0}, guard_issue_counts(problems))

    def test_hand_numbered_question_is_still_checked(self) -> None:
        for source in ("manual", "ocr_top_left"):
            problems = self.problems(source)
            self.assertEqual(1, python_issue_counts(problems)["overlap"], source)
            self.assertEqual(1, guard_issue_counts(problems)["overlap"], source)


class TestConfirmClearsOnlyThatExactCrop(unittest.TestCase):
    def test_confirm_records_crop_and_clears_overlap_until_it_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for passage, key in ((False, "overlap"), (True, "reuse")):
                session = {"problems": overlapping_pair(Path(tmp), passage=passage), "pages": []}
                self.assertEqual(1, python_issue_counts(session["problems"])[key])

                app_server._mutate_confirm(session, ["p1"])
                self.assertEqual(1, python_issue_counts(session["problems"])[key], "only one side confirmed")

                app_server._mutate_confirm(session, ["p2"])
                first = session["problems"][0]
                self.assertEqual(first["imagePath"], first["reviewConfirmedImagePath"])
                self.assertEqual(first["bbox"], first["reviewConfirmedBBox"])
                self.assertEqual(0, python_issue_counts(session["problems"])[key])

                # The client sees /api/file paths; the JS gate must agree with the server.
                http_session = app_server.rewrite_session_for_http(session)
                self.assertEqual(0, guard_issue_counts(http_session["problems"])[key])
                # ...and the snapshot it sends back on 제작 still counts as confirmed.
                round_trip = app_server._denormalize_session_paths(http_session)
                self.assertEqual(0, python_issue_counts(round_trip["problems"])[key])

                moved = json.loads(json.dumps(session))
                moved["problems"][1]["bbox"] = {"left": 40, "top": 112, "width": 500, "height": 300}
                self.assertEqual(1, python_issue_counts(moved["problems"])[key], "re-crop re-arms the check")
                self.assertEqual(1, guard_issue_counts(moved["problems"])[key])

                recropped = json.loads(json.dumps(session))
                recropped["problems"][1]["imagePath"] = recropped["problems"][1]["imagePath"].replace("p2", "p1")
                self.assertEqual(1, python_issue_counts(recropped["problems"])[key], "new image re-arms the check")
                self.assertEqual(1, guard_issue_counts(recropped["problems"])[key])

                flagged = json.loads(json.dumps(session))
                flagged["problems"][1]["reviewStatus"] = "failed"  # e.g. marked again by a 제작 block
                self.assertEqual(1, python_issue_counts(flagged["problems"])[key], "a re-flagged crop needs 확인 again")
                self.assertEqual(1, guard_issue_counts(flagged["problems"])[key])

    def test_saving_an_already_normal_crop_is_not_a_confirmation(self) -> None:
        # The side panel's 변경 저장 sends the confirm action for items that need no check.
        stats = {"hasArtifact": True, "artifactTypes": ["bottom_page_line"]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            board, "_problem_image_page_chrome_artifact_stats", return_value=stats
        ):
            session = {"problems": overlapping_pair(Path(tmp)), "pages": []}
            for problem in session["problems"]:
                problem.update(riskFlags=[], reviewStatus="normal", processingStep=board.PROCESSING_STEP_CHALK)
            app_server._mutate_confirm(session, ["p1", "p2"])
            self.assertNotIn("reviewConfirmedImagePath", session["problems"][0])
            self.assertEqual(1, python_issue_counts(session["problems"])["overlap"])
            self.assertEqual(
                ["step2_page_chrome_artifact_rate"],
                [issue["type"] for issue in board._classin_page_chrome_artifact_issues(session["problems"])],
            )

    def test_publish_rebuild_keeps_the_confirmation_record(self) -> None:
        prior = {"reviewConfirmedImagePath": "file:///tmp/a.png", "reviewConfirmedBBox": {"left": 1, "top": 2, "width": 3, "height": 4}}
        rebuilt: dict = {}
        app_server._copy_publish_problem_metadata(rebuilt, prior)
        self.assertEqual(prior, rebuilt)

    def test_page_decoration_check_skips_only_confirmed_stage2_crop(self) -> None:
        stats = {"hasArtifact": True, "artifactTypes": ["bottom_page_line"]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            board, "_problem_image_page_chrome_artifact_stats", return_value=stats
        ):
            problem = overlapping_pair(Path(tmp))[0]
            problem["processingStep"] = board.PROCESSING_STEP_CHALK
            session = {"problems": [problem], "pages": []}
            types = lambda: [issue["type"] for issue in board._classin_page_chrome_artifact_issues(session["problems"])]
            self.assertEqual(["step2_page_chrome_artifact_rate"], types())
            app_server._mutate_confirm(session, ["p1"])
            self.assertEqual([], types())

            problem["processingStep"] = board.PROCESSING_STEP_RECONSTRUCT
            self.assertEqual(["step3_page_chrome_artifact"], types(), "stage 3 must still be re-cleaned")


class TestMissingPassageQuestionNotice(unittest.TestCase):
    def problems(self, image_dir: Path) -> list[dict]:
        problems = []
        for number in (31, 32, 34):
            path = image_dir / f"p{number}.png"
            Image.new("RGB", (40, 20), "white").save(path)
            problems.append({
                "id": f"p{number}", "title": f"{number}.", "problemNumber": number,
                "imagePath": path.resolve().as_uri(), "sourcePageId": "page-1",
                "bbox": {"left": 40, "top": number * 10, "width": 560, "height": 8},
                "passageGroupId": "passage-31-34", "passageRange": {"start": 31, "end": 34},
                "passageRole": "child_question", "passageChildProblemNumbers": [31, 32, 33, 34],
                "riskFlags": ["passage_missing_child_questions"], "reviewStatus": "check_needed",
            })
        return problems

    def test_notice_stops_once_every_question_is_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = {"problems": self.problems(Path(tmp)), "pages": []}
            issues = board._classin_missing_passage_child_question_issues(session["problems"])
            self.assertEqual([[33]], [issue["missingChildProblemNumbers"] for issue in issues])
            app_server._mutate_confirm(session, ["p31", "p32"])
            self.assertEqual(1, len(board._classin_missing_passage_child_question_issues(session["problems"])))
            app_server._mutate_confirm(session, ["p34"])
            self.assertEqual([], board._classin_missing_passage_child_question_issues(session["problems"]))


class TestStaleHwpCountWarning(unittest.TestCase):
    def session(self, detected: int) -> dict:
        return {
            "warning_messages": [
                "HWP 내부 텍스트 기준 문항 수는 2개인데 최종 감지 문항 수는 3개입니다. 검수 화면에서 분리 상태를 확인해 주세요.",
                "다른 주의 사항",
            ],
            "problems": [{"id": f"p{index}", "riskFlags": [], "reviewStatus": "normal"} for index in range(detected)],
            "pages": [{
                "id": "page-1",
                "problemIds": [f"p{index}" for index in range(detected)],
                "metadata": {"hwp_conversion_quality": {"hwp_text_numbered_problem_count": 2}},
            }],
        }

    def test_builder_count_warning_drops_once_counts_match(self) -> None:
        summary = app_server._session_review_summary(self.session(2))
        self.assertEqual(["다른 주의 사항"], summary["warningMessages"])
        self.assertEqual(1, summary["warningCount"])

    def test_builder_count_warning_stays_while_counts_differ(self) -> None:
        summary = app_server._session_review_summary(self.session(3))
        self.assertTrue(summary["warningMessages"][0].startswith("HWP 내부 텍스트 기준 문항 수는"))


REVIEW_HELPERS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `source bounds missing: ${start}`);
  return source.slice(from, to);
}
const sandbox = { ...require('./ui_prototype/publish_guard.js') };
sandbox.globalThis = sandbox;
vm.runInNewContext(section('const REVIEW_STATUS_META =', 'function normalizePublishSummary'), sandbox);
"""


class TestReviewUiCopy(unittest.TestCase):
    def test_page_and_box_risk_codes_are_korean(self) -> None:
        run_node(REVIEW_HELPERS + r"""
        // Page flags written by build_mvp_export.py, app_server.py and pipeline_router.py.
        for (const flag of ['hwp_problem_count_mismatch', 'hwp_oversegmentation', 'ai_retry_missing_source',
          'ai_retry_failed', 'sparse_segmentation', 'large_block_dominance', 'textless_blocks', 'ocr_disabled']) {
          const label = sandbox.riskFlagLabel(flag);
          assert.ok(label && !label.includes('_') && /[^\x00-\x7f]/.test(label), `${flag} -> ${label}`);
        }
        const review = section('function ReviewStage', '// ─── LEFT:');
        assert.ok(!review.includes('위험 ·'));
        assert.ok(!review.includes("pageRiskFlags.join("), 'page header must not print raw codes');
        assert.ok(review.includes('`확인 필요 · ${pageShownRiskLabels.join'));
        assert.ok(review.includes('problemRiskFlags.map(riskFlagLabel)'));
        assert.ok(!review.includes('`지문 ${passageGroupId}`'));
        """)

    def test_overlap_chips_follow_current_problems_and_confirmation(self) -> None:
        run_node(REVIEW_HELPERS + r"""
        const problem = (id, top, extra = {}) => ({ id, title: `${id}.`, sourcePageId: 'page-1',
          imagePath: `/api/file?path=${id}.png`, bbox: { left: 0, top, width: 100, height: 100 },
          reviewStatus: 'check_needed', ...extra });
        const stale = { sourceProblemOverlapGroups: [{ sourcePageId: 'page-1' }], pages: [] };
        let summary = sandbox.sessionReviewSummary({ ...stale, problems: [problem('3', 0), problem('4', 5)] });
        assert.equal(summary.sourceProblemOverlapLabel, '문항 영역 겹침 1건');
        assert.ok(summary.sourceProblemOverlapDetailLabel.startsWith('3. ↔ 4.'), summary.sourceProblemOverlapDetailLabel);
        assert.ok(!summary.sourceProblemOverlapDetailLabel.includes('page-1'));
        summary = sandbox.sessionReviewSummary({ ...stale, problems: [problem('3', 0), problem('4', 500)] });
        assert.equal(summary.sourceProblemOverlapGroups.length, 0, 'moved crop clears the chip');
        const confirmed = (id, top) => problem(id, top, { reviewStatus: 'normal',
          reviewConfirmedImagePath: `/api/file?path=${id}.png`,
          reviewConfirmedBBox: { left: 0, top, width: 100, height: 100 } });
        summary = sandbox.sessionReviewSummary({ ...stale, problems: [confirmed('3', 0), confirmed('4', 5)] });
        assert.equal(summary.sourceProblemOverlapGroups.length, 0, '확인 clears the chip');
        """)

    def test_summary_exposes_all_warnings_and_missing_question_numbers(self) -> None:
        run_node(REVIEW_HELPERS + r"""
        const summary = sandbox.sessionReviewSummary({
          reviewSummary: { warningMessages: ['첫 주의', '둘째 주의'] },
          problems: [{ id: 'p18', reviewStatus: 'check_needed', riskFlags: ['passage_missing_child_questions'] }],
          pages: [],
          passageReviewItems: [{ groupId: 'g', numberLabel: '18-20', problemIds: ['p18'], missingChildProblemNumbers: [19] }],
        });
        assert.deepEqual(Array.from(summary.warningMessages), ['첫 주의', '둘째 주의']);
        assert.equal(summary.passageMissingChildLabel, '19번 문항이 없어요');
        const review = section('function ReviewStage', '// ─── LEFT:');
        assert.ok(review.includes('reviewSummary.warningMessages.map('));
        assert.ok(review.includes('onClick={() => setReviewDiagnosticsOpen(true)}'));
        assert.ok(review.includes('reviewSummary.passageMissingChildLabel'));
        """)

    def test_item_reason_names_missing_question(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        const sandbox = placementSandbox();
        const session = { passageGroups: [{ groupId: 'g18', numberLabel: '18-20', missingChildProblemNumbers: [19] }] };
        const item = sandbox.mapProblemToItem({ id: 'p18', passageGroupId: 'g18',
          riskFlags: ['passage_missing_child_questions'] }, 0, session);
        assert.equal(item.statusReason, '18-20번 지문의 19번 문항이 없어요');
        assert.equal(item.passageGroupId, 'g18');
        const fallback = sandbox.mapProblemToItem({ id: 'p18', riskFlags: ['passage_missing_child_questions'] }, 0, null);
        assert.equal(fallback.statusReason, 'passage_missing_child_questions', 'unknown group keeps the label');
        """)


PREFLIGHT_HELPERS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `source bounds missing: ${start}`);
  return source.slice(from, to);
}
const sandbox = {};
vm.runInNewContext(
  section('const CLASSIN_PREFLIGHT_ISSUE_LABELS = {', 'const PASSAGE_REVIEW_REASON_LABELS')
  + section('function classinPreflightIssueLabel(', 'function passageReviewReasonLabel(')
  + section('function listUnique(', 'function riskFlagsFor(')
  + section('function preflightIssueType(', 'function preflightBlockingProblemIds('),
  sandbox,
);
"""


class TestPreflightNaming(unittest.TestCase):
    def test_publish_result_buttons_name_questions(self) -> None:
        run_node(PREFLIGHT_HELPERS + r"""
        const buttons = sandbox.publishPreflightIssueButtons({ issues: [
          { type: 'horizontal_crop_edge_risk', problemId: 'p3', problemTitle: '3.' },
          { type: 'horizontal_crop_edge_risk', problemId: 'p7', problemTitle: '7.' },
          { type: 'unreadable_problem_image', problemId: 'p9', problemTitle: '9.' },
          { type: 'passage_missing_child_questions', problemIds: ['p31', 'p32'], problemTitle: '31.', missingChildProblemNumbers: [33] },
          { type: 'step2_page_chrome_artifact_rate' },
        ] });
        assert.deepEqual(JSON.parse(JSON.stringify(buttons)), [
          { type: 'horizontal_crop_edge_risk', problemIds: ['p3', 'p7'], text: '글자 잘림 확인 · 3., 7.' },
          { type: 'unreadable_problem_image', problemIds: ['p9'], text: '이미지 파일을 열 수 없음 · 9.' },
          { type: 'passage_missing_child_questions', problemIds: ['p31', 'p32'], text: '문항 누락 · 33번 없음' },
          { type: 'step2_page_chrome_artifact_rate', problemIds: [], text: '쪽번호·머리말 많이 섞임 1' },
        ]);
        assert.equal(sandbox.preflightPairToastLabel([
          { problemTitle: '3.', nextProblemTitle: '4.' }, {}, {},
        ]), '3. ↔ 4. 외 2곳');
        assert.equal(sandbox.preflightPairToastLabel([{ problemId: 'a', nextProblemId: 'b' }]), 'a ↔ b');
        """)

    def test_publish_result_buttons_jump_to_review(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        panel = source.split("function PublishResultPanel(", 1)[1].split("function SidePanel(", 1)[0]
        self.assertIn("publishPreflightIssueButtons(summary.classinPreflight)", panel)
        self.assertIn("onClick={() => onFocusProblems(issue.problemIds)}", panel)
        self.assertIn("onFocusProblems={focusReviewProblems}", source)
        focus = source.split("const focusReviewProblems = useCallback(", 1)[1].split("const markClassinReviewComplete", 1)[0]
        self.assertIn("filter(id => knownIds.has(id))", focus)
        self.assertIn("setReviewFocus({ filter: 'all', problemIds: ids, source: 'publish-result' })", focus)

    def test_label_tables_agree(self) -> None:
        def js_table(path: str, start: str) -> dict:
            text = (PROJECT_ROOT / path).read_text(encoding="utf-8")
            body = text.split(start, 1)[1].split("}", 1)[0]
            return dict(re.findall(r"(\w+): ['\"]([^'\"]+)['\"]", body))

        labels = board.CLASSIN_PREFLIGHT_ISSUE_LABELS
        self.assertEqual(labels, js_table("ui_prototype/app.jsx", "const CLASSIN_PREFLIGHT_ISSUE_LABELS = {"))
        self.assertEqual(labels, js_table("ui_prototype/publish_summary.js", "CLASSIN_PREFLIGHT_ISSUE_LABELS = {"))
        self.assertEqual("글자 잘림 확인", labels["horizontal_crop_edge_risk"])
        self.assertEqual("이미지 파일을 열 수 없음", labels["unreadable_problem_image"])
        # A question marked by a page-decoration block reads the same as the 제작 result.
        risk_labels = js_table("ui_prototype/app.jsx", "const RISK_FLAG_META = {")
        for flag in ("step2_page_chrome_artifact_rate", "step3_page_chrome_artifact"):
            self.assertEqual(labels[flag], risk_labels[flag])

    def test_stage2_decoration_block_names_the_confirm_way_out(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        toast = source.split("function pageChromePreflightBlockToast(", 1)[1].split("function applyPublishPreflightIssuesToSession(", 1)[0]
        stage2 = toast.split("step2_page_chrome_artifact_rate", 1)[1]
        self.assertIn("확인을 누르고", stage2)
        self.assertNotIn("확인을 누르고", toast.split("step2_page_chrome_artifact_rate", 1)[0], "stage 3 is not cleared by 확인")


class TestBoardNextCheckItem(unittest.TestCase):
    def test_next_item_wraps_and_confirm_advances(self) -> None:
        run_node(CONFIRM_SETUP + r"""
        const next = (items, activeIndex) => {
          const sandbox = { items, activeIndex, item: items[activeIndex] };
          vm.runInNewContext('const items = globalThis.items; const item = globalThis.item; const activeIndex = globalThis.activeIndex;'
            + section('  const itemNeedsCheck =', '  const panelBusy =') + 'globalThis.result = nextNeedsCheckItem;', Object.assign(sandbox, { globalThis: sandbox }));
          return sandbox.result?.id || null;
        };
        const items = [{ id: 'A', reviewStatus: 'check_needed' }, { id: 'B', reviewStatus: 'normal' }, { id: 'C', reviewStatus: 'failed' }];
        assert.equal(next(items, 2), 'A', 'wraps to earlier items');
        assert.equal(next(items, 0), 'C');
        assert.equal(next([{ id: 'A', reviewStatus: 'check_needed' }], 0), null);
        (async () => {
          const { sandbox, clickFooter } = controller();
          assert.equal(sandbox.activeId, 'p2');
          await clickFooter(false);
          await sandbox.footerDone;
          assert.equal(sandbox.activeId, 'p1', '확인 완료 moves to the remaining item');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)


if __name__ == "__main__":
    unittest.main()
