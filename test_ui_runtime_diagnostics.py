from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiRuntimeDiagnostics(unittest.TestCase):
    def test_hangul_runtime_helpers_include_hwp_renderer(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        summary_helper = source.split("function hangulRuntimeSummary", 1)[1]
        summary_helper = summary_helper.split("function hangulRuntimeToolRows", 1)[0]
        tool_rows = source.split("function hangulRuntimeToolRows", 1)[1]
        tool_rows = tool_rows.split("function listUnique", 1)[0]

        self.assertIn("hangul.hwpRenderers", summary_helper)
        self.assertIn("렌더 ${rendererCount}", summary_helper)
        self.assertIn("HWP 렌더", tool_rows)
        self.assertIn("hangul.hwpRenderers || []", tool_rows)

    def test_review_summary_surfaces_hwp_cache_hits(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("HWP 캐시", review_stage)
        self.assertIn("hwpCacheHitPageCount", review_stage)
        self.assertIn("hwpCacheHitPageCount", summary_helper)
        self.assertIn("hwpRendererCacheHitCount", summary_helper)
        self.assertIn("hwpNormalizedCacheHitCount", summary_helper)

    def test_review_summary_surfaces_hwp_segmentation_risk_counts(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("HWP 과분할", review_stage)
        self.assertIn("hwpOversegmentationCount", review_stage)
        self.assertIn("hwpProblemCountMismatchCount", summary_helper)
        self.assertIn("hwp_problem_count_mismatch", summary_helper)
        self.assertIn("hwpOversegmentationCount", summary_helper)
        self.assertIn("hwp_oversegmentation", summary_helper)

    def test_review_summary_surfaces_duplicate_problem_number_groups(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]
        risk_meta = source.split("const RISK_FLAG_META = {", 1)[1]
        risk_meta = risk_meta.split("};", 1)[0]

        self.assertIn("중복 번호", review_stage)
        self.assertIn("duplicateProblemNumberGroups", review_stage)
        self.assertIn("duplicateProblemNumberGroups", summary_helper)
        self.assertIn("duplicate_problem_number_groups", summary_helper)
        self.assertIn("duplicateProblemNumberLabel", summary_helper)
        self.assertIn("duplicate_problem_number", risk_meta)
        self.assertIn("중복 번호", risk_meta)

    def test_review_summary_surfaces_source_problem_overlap_groups(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]
        risk_meta = source.split("const RISK_FLAG_META = {", 1)[1]
        risk_meta = risk_meta.split("};", 1)[0]

        self.assertIn("원본 겹침", review_stage)
        self.assertIn("sourceProblemOverlapGroups", review_stage)
        self.assertIn("sourceProblemOverlapGroups", summary_helper)
        self.assertIn("source_problem_overlap_groups", summary_helper)
        self.assertIn("sourceProblemOverlapLabel", summary_helper)
        self.assertIn("source_problem_bbox_overlap", risk_meta)
        self.assertIn("원본 영역 겹침", risk_meta)

    def test_review_summary_surfaces_passage_groups(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        board_html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]
        passage_id_helper = source.split("function passageGroupIdFor(problem)", 1)[1]
        passage_id_helper = passage_id_helper.split("function isPassageProblem(problem)", 1)[0]
        passage_helper = source.split("function collectPassageGroupSummary(session)", 1)[1]
        passage_helper = passage_helper.split("function sessionReviewSummary(session)", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("'passage', '긴 지문'", review_stage)
        self.assertIn("긴 지문 그룹", review_stage)
        self.assertIn("이어짐", review_stage)
        self.assertIn("passageGroupCount", review_stage)
        self.assertIn("passageContinuationBlockCount", review_stage)
        self.assertIn("review-bbox-passage", review_stage)
        self.assertIn("review-bbox-passage-tag", review_stage)
        self.assertIn(".review-bbox-passage", board_html)
        self.assertIn("passageGroupCount", summary_helper)
        self.assertIn("passageProblemCount", summary_helper)
        self.assertIn("passageContinuationBlockCount", summary_helper)
        self.assertIn("fragmentProblemCount", passage_helper)
        self.assertIn("problemNumbers", passage_helper)
        self.assertIn("passageRole", passage_helper)
        self.assertIn("passageGroupIdFor(problem)", passage_helper)
        self.assertIn("passagePreQuestionContinuationBlockIds", passage_helper)
        self.assertIn("passage_pre_question_continuation_block_ids", passage_helper)
        self.assertIn("passageGroupId", passage_id_helper)
        self.assertIn("passage_group_id", passage_id_helper)
        risk_meta = source.split("const RISK_FLAG_META = {", 1)[1]
        risk_meta = risk_meta.split("};", 1)[0]
        self.assertIn("passage_cross_page_merge_check", risk_meta)
        self.assertIn("긴 지문 병합 확인", risk_meta)

    def test_review_summary_surfaces_passage_review_queue(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        queue_helper = source.split("function collectPassageReviewSummary(session", 1)[1]
        queue_helper = queue_helper.split("function sessionReviewSummary(session)", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("긴 지문 검수", review_stage)
        self.assertIn("passageReviewLabel", review_stage)
        self.assertIn("passageReviewItems", queue_helper)
        self.assertIn("passage_review_items", queue_helper)
        self.assertIn("actionableProblemIds", queue_helper)
        self.assertIn("unresolvedPassageReviewItems", queue_helper)
        self.assertIn("passageReviewItemCount", queue_helper)
        self.assertIn("crossPagePassageReviewItemCount", queue_helper)
        self.assertIn("passageReviewItems", summary_helper)
        self.assertIn("passageReviewLabel", summary_helper)

    def test_review_summary_passage_review_chip_filters_only_queue_items(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]
        queue_helper = source.split("function collectPassageReviewSummary(session", 1)[1]
        queue_helper = queue_helper.split("function sessionReviewSummary(session)", 1)[0]

        self.assertIn("passageReviewProblemIds", queue_helper)
        self.assertIn("passageReviewItemProblemIds", queue_helper)
        self.assertIn("reviewFilter === 'passage-review'", review_stage)
        self.assertIn("'passage-review' ? 'all' : 'passage-review'", review_stage)
        self.assertIn("passageReviewProblemIds", review_stage)
        self.assertIn("problemMatchesReviewFilter(problem, reviewFilter, { passageReviewProblemIds })", review_stage)

    def test_review_summary_removes_confirmed_passage_review_queue_items(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('function normalizePublishSummary');
            if (start < 0 || end < 0) throw new Error('review summary helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            const session = {
              review_summary: {
                actionableNeedsReviewCount: 2,
                reviewStatusCounts: { all: 2, normal: 0, check_needed: 2, failed: 0 },
                needsReviewCount: 2,
                riskFlagCounts: { passage_cross_page_merge_check: 2 },
              },
              passageReviewItemCount: 1,
              crossPagePassageReviewItemCount: 1,
              passageReviewItems: [
                {
                  numberLabel: '31-32',
                  problemIds: ['p31', 'p32'],
                  continuesAcrossPages: true,
                },
              ],
              problems: [
                {
                  id: 'p31',
                  reviewStatus: 'normal',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
                {
                  id: 'p32',
                  reviewStatus: 'normal',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
              ],
              pages: [{ id: 'page-1', problemIds: ['p31', 'p32'], riskFlags: [] }],
            };
            const summary = sandbox.sessionReviewSummary(session);
            if (summary.reviewStatusCounts.check_needed !== 0) {
              throw new Error(`expected confirmed check_needed count 0, got ${summary.reviewStatusCounts.check_needed}`);
            }
            if (summary.needsReviewCount !== 0) {
              throw new Error(`expected confirmed needs review count 0, got ${summary.needsReviewCount}`);
            }
            if (summary.actionableNeedsReviewCount !== 0) {
              throw new Error(`expected confirmed actionable count 0, got ${summary.actionableNeedsReviewCount}`);
            }
            if (summary.passageReviewItemCount !== 0) {
              throw new Error(`expected confirmed passage review count 0, got ${summary.passageReviewItemCount}`);
            }
            if (summary.crossPagePassageReviewItemCount !== 0) {
              throw new Error(`expected confirmed cross-page passage review count 0, got ${summary.crossPagePassageReviewItemCount}`);
            }
            if (summary.passageReviewProblemIds.length !== 0) {
              throw new Error(`expected no confirmed passage review problem ids, got ${summary.passageReviewProblemIds.join(',')}`);
            }
            if (summary.passageReviewLabel !== '') {
              throw new Error(`expected empty confirmed passage review label, got ${summary.passageReviewLabel}`);
            }
            """
        )

    def test_review_summary_keeps_check_needed_passage_review_queue_without_flags(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('function normalizePublishSummary');
            if (start < 0 || end < 0) throw new Error('review summary helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            const session = {
              review_summary: {
                actionableNeedsReviewCount: 0,
                reviewStatusCounts: { all: 2, normal: 1, check_needed: 1, failed: 0 },
                needsReviewCount: 1,
                riskFlagCounts: {},
              },
              passageReviewItemCount: 1,
              crossPagePassageReviewItemCount: 1,
              passageReviewItems: [
                {
                  numberLabel: '31-32',
                  problemIds: ['p31', 'p32'],
                  continuesAcrossPages: true,
                },
              ],
              problems: [
                {
                  id: 'p31',
                  reviewStatus: 'check_needed',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
                {
                  id: 'p32',
                  reviewStatus: 'normal',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
              ],
              pages: [{ id: 'page-1', problemIds: ['p31', 'p32'], riskFlags: [] }],
            };
            const summary = sandbox.sessionReviewSummary(session);
            if (summary.passageReviewItemCount !== 1) {
              throw new Error(`expected unresolved passage review count 1, got ${summary.passageReviewItemCount}`);
            }
            if (summary.crossPagePassageReviewItemCount !== 1) {
              throw new Error(`expected unresolved cross-page count 1, got ${summary.crossPagePassageReviewItemCount}`);
            }
            if (summary.passageReviewProblemIds.join(',') !== 'p31,p32') {
              throw new Error(`expected passage ids p31,p32, got ${summary.passageReviewProblemIds.join(',')}`);
            }
            if (!summary.passageReviewLabel.includes('긴 지문 검수 1')) {
              throw new Error(`expected passage review label, got ${summary.passageReviewLabel}`);
            }
            """
        )

    def test_publish_guard_warns_for_unresolved_passage_review_queue_without_flags(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        publish_flow = source.split("const onPublish = async () =>", 1)[1]
        publish_flow = publish_flow.split("const placements = Object.fromEntries", 1)[0]
        self.assertIn("publishReviewWarningMessage(sessionForPublish, publishReviewSummary)", publish_flow)

        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('function normalizePublishSummary');
            if (start < 0 || end < 0) throw new Error('review summary helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            if (typeof sandbox.publishReviewWarningMessage !== 'function') {
              throw new Error('publishReviewWarningMessage missing');
            }
            const session = {
              passageReviewItemCount: 1,
              crossPagePassageReviewItemCount: 1,
              passageReviewItems: [
                {
                  numberLabel: '31-32',
                  problemIds: ['p31', 'p32'],
                  continuesAcrossPages: true,
                },
              ],
              problems: [
                {
                  id: 'p31',
                  reviewStatus: 'check_needed',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
                {
                  id: 'p32',
                  reviewStatus: 'normal',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
              ],
              pages: [{ id: 'page-1', problemIds: ['p31', 'p32'], riskFlags: [] }],
            };
            const summary = sandbox.sessionReviewSummary(session);
            const warning = sandbox.publishReviewWarningMessage(session, summary);
            if (!warning) throw new Error('expected unresolved passage review warning');
            if (!warning.message.includes('긴 지문 검수 1')) {
              throw new Error(`expected passage warning line, got ${warning.message}`);
            }
            if (!warning.cancelToast.includes('긴 지문 검수 큐')) {
              throw new Error(`expected passage cancel toast, got ${warning.cancelToast}`);
            }
            if (warning.reviewFilter !== 'passage-review') {
              throw new Error(`expected passage-review focus filter, got ${warning.reviewFilter}`);
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
