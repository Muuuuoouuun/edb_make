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
        on_confirm = source.split("const onConfirm = async (id, options = {}) => {", 1)[1]
        on_confirm = on_confirm.split("  const onPublish = async () => {", 1)[0]

        self.assertIn("options.problemIds", on_confirm)
        self.assertIn("전체 ${confirmedIds.size}개 확인 완료", on_confirm)
        self.assertIn("await mutateSession('confirm', { problemIds: [...confirmedIds] })", on_confirm)
        self.assertNotIn("postRestore(nextSession)", on_confirm)
        self.assertIn("return nextSession;", on_confirm)
        self.assertIn("return false;", on_confirm)

    def test_final_and_bulk_confirmation_move_directly_to_board_preview(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_confirm = source.split("const onConfirm = async (id, options = {}) => {", 1)[1]
        on_confirm = on_confirm.split("  const onPublish = async () => {", 1)[0]

        self.assertIn("const beforeFlow = reviewFlowState(sessionReviewSummary(session));", on_confirm)
        self.assertIn("const afterFlow = reviewFlowState(sessionReviewSummary(nextSession));", on_confirm)
        self.assertIn("if (afterFlow.complete && (beforeFlow.remaining > 0 || options.bulk))", on_confirm)
        self.assertIn("setView('board');", on_confirm)
        self.assertIn("검수 완료 · 칠판 미리보기로 이동했어요", on_confirm)
        self.assertIn("일괄 확인 완료 · 칠판 미리보기로 이동했어요", on_confirm)

    def test_bulk_confirmation_moves_mock_session_to_board_preview(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_confirm = source.split("const onConfirm = async (id, options = {}) => {", 1)[1]
        on_confirm = on_confirm.split("  const onPublish = async () => {", 1)[0]

        self.assertIn("const allItemsConfirmedByBulk = options.bulk", on_confirm)
        self.assertIn("items.every(item => confirmedIds.has(item.id))", on_confirm)
        self.assertIn("if (allItemsConfirmedByBulk)", on_confirm)
        self.assertIn("setReviewFocus(null);", on_confirm)

    def test_completed_restored_session_opens_on_board_preview(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        should_open_review = source.split("function shouldOpenReview(session){", 1)[1]
        should_open_review = should_open_review.split("\n}", 1)[0]

        self.assertIn(
            "return reviewFlowState(sessionReviewSummary(session)).remaining > 0;",
            should_open_review,
        )
        self.assertNotIn("problemCount > 1", should_open_review)

    def test_board_image_columns_default_to_one_in_source_and_bundle(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        bundle = (PROJECT_ROOT / "ui_prototype" / "app.bundle.js").read_text(encoding="utf-8")

        self.assertIn('"boardColumns": 1', source)
        self.assertIn('"boardColumns":1', bundle)

    def test_review_stage_exposes_persistent_completion_bar(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]

        self.assertIn("review-completion-bar", review_stage)
        self.assertIn("확인하면 다음 항목이 자동으로 선택됩니다.", review_stage)
        self.assertIn("마지막 확인 · 칠판 보기", review_stage)
        self.assertIn("확인하고 다음", review_stage)
        self.assertIn("칠판 미리보기", review_stage)
        self.assertIn("onOpenBoard", review_stage)
        self.assertIn(".review-completion-bar", html)
        self.assertIn(".review-completion-primary", html)
        self.assertIn(".review-completion-shortcut", html)

    def test_review_confirmation_advances_and_has_a_keyboard_shortcut(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]

        self.assertIn("const confirmSelectedAndAdvance = useCallback(async () => {", review_stage)
        self.assertIn("sessionReviewSummary(confirmedSession).unresolvedReviewProblemIds", review_stage)
        self.assertIn("focusReviewProblem(nextProblemId)", review_stage)
        self.assertIn("normalizedProblemId.startsWith('page:')", review_stage)
        self.assertIn("다음 확인 페이지", review_stage)
        self.assertIn("evt.key !== 'Enter' || (!evt.metaKey && !evt.ctrlKey)", review_stage)
        self.assertIn('aria-keyshortcuts="Meta+Enter Control+Enter"', review_stage)
        self.assertIn("review-completion-shortcut", review_stage)
        self.assertNotIn("그대로 확인", review_stage)

    def test_review_top_toolbars_use_grouped_reusable_components(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("function ReviewToolbarGroup", source)
        self.assertIn("function ReviewFilterTabs", source)
        self.assertIn('label="상태 보기"', source)
        self.assertIn('label="일괄 작업"', source)
        self.assertIn('aria-pressed={value === filterValue}', source)
        self.assertIn('className="stage-toolbar review-stage-toolbar"', source)
        self.assertIn('className="review-view-control-group"', source)
        self.assertIn(".review-toolbar-group", html)
        self.assertIn(".review-toolbar-action:focus-visible", html)
        self.assertIn(".review-stage-heading", html)

    def test_board_uses_review_bulk_confirm_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("review_filters.js?v=passage-filter-20260614", html)
        self.assertIn("app.bundle.js?v=frontend-bundle-", html)
        self.assertNotIn("app.js?v=", html)


if __name__ == "__main__":
    unittest.main()
