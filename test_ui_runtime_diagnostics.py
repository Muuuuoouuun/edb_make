from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


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
        self.assertIn("passageGroupCount", review_stage)
        self.assertIn("review-bbox-passage", review_stage)
        self.assertIn("review-bbox-passage-tag", review_stage)
        self.assertIn(".review-bbox-passage", board_html)
        self.assertIn("passageGroupCount", summary_helper)
        self.assertIn("passageProblemCount", summary_helper)
        self.assertIn("passageGroupIdFor(problem)", passage_helper)
        self.assertIn("passageGroupId", passage_id_helper)
        self.assertIn("passage_group_id", passage_id_helper)


if __name__ == "__main__":
    unittest.main()
