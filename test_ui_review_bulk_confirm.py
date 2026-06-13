from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiReviewBulkConfirm(unittest.TestCase):
    def test_review_stage_exposes_bulk_confirm_actions(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]

        self.assertIn("onConfirm", review_stage)
        self.assertIn("actionableProblemIds", review_stage)
        self.assertIn("확인 필요 전체 확인", review_stage)
        self.assertIn("표시 항목 확인 완료", review_stage)
        self.assertIn("onConfirm?.(null, { problemIds: actionableProblemIds, bulk: true })", review_stage)
        self.assertIn("onConfirm?.(null, { problemIds: visibleReviewScope.problemIds, bulk: true })", review_stage)

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
        self.assertIn("전체 ${confirmedIds.size}개 확인 완료", on_confirm)

    def test_board_uses_review_bulk_confirm_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("review_filters.js?v=passage-filter-20260614", html)
        self.assertIn("app.jsx?v=preflight-focus-20260614", html)


if __name__ == "__main__":
    unittest.main()
