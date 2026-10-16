from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiReviewBulkConfirm(unittest.TestCase):
    def test_review_stage_has_one_contextual_confirm_action(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]

        self.assertIn("onConfirm", review_stage)
        self.assertNotIn("확인 필요 전체 확인", review_stage)
        self.assertNotIn("표시 항목 확인 완료", review_stage)
        self.assertEqual(2, review_stage.count("{Icon.check} 확인 완료 {"))
        self.assertIn("collectUnresolvedReviewProblemIds(session, reviewSummary.actionableRiskFlagCounts)", review_stage)
        self.assertIn("const confirmTargetIds = activeReviewFilter", review_stage)
        self.assertIn("visibleReviewScope.problemIds.filter(id => unresolvedProblemIdSet.has(String(id)))", review_stage)
        self.assertIn("onConfirm?.(null, { problemIds: confirmTargetIds, bulk: true })", review_stage)
        self.assertIn("onConfirm?.(null, { problemIds: selectedList, bulk: true })", review_stage)

    def test_side_panel_no_longer_confirms_or_clears_flags(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        side_panel = source.split("function SidePanel", 1)[1].split("function LoadingOverlay", 1)[0]

        self.assertNotIn("onConfirm", side_panel)
        self.assertNotIn("건너뛰기", side_panel)
        self.assertNotIn("일괄 확인", side_panel)
        self.assertNotIn("setBulk", source)
        self.assertIn("onClick={() => applyToAll(item.step)}", side_panel)
        self.assertIn("이 단계를 전체에 적용", side_panel)

    def test_app_passes_confirm_handler_to_review_stage(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_usage = source.split("<ReviewStage", 1)[1]
        review_usage = review_usage.split("/>", 1)[0]

        self.assertIn("onConfirm={onConfirm}", review_usage)

    def test_on_confirm_accepts_explicit_problem_ids(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_confirm = source.split("const onConfirm = (id, options = {}) => {", 1)[1]
        on_confirm = on_confirm.split("  const onPublish = async () => {", 1)[0]

        self.assertIn("options.problemIds", on_confirm)
        self.assertIn("const targetIds = explicitProblemIds?.length ? explicitProblemIds : [id];", on_confirm)
        self.assertNotIn("items.map(item => item.id)", on_confirm)
        self.assertIn("${confirmedIds.size}개 확인 완료", on_confirm)

    def test_board_uses_review_bulk_confirm_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("review_filters.js?v=review-unresolved-20261016", html)
        self.assertIn("app.bundle.js?v=frontend-bundle-", html)
        self.assertNotIn("app.js?v=", html)


if __name__ == "__main__":
    unittest.main()
