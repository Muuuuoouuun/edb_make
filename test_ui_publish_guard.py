from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiPublishGuard(unittest.TestCase):
    def test_publish_warns_and_returns_to_review_when_actionable_items_remain(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_publish = source.split("const onPublish = async () => {", 1)[1]
        on_publish = on_publish.split("  return (", 1)[0]
        warning_helper = source.split("function publishReviewWarningMessage", 1)[1]
        warning_helper = warning_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("sessionReviewSummary(sessionForPublish)", on_publish)
        self.assertIn("publishReviewWarningMessage(sessionForPublish, publishReviewSummary)", on_publish)
        self.assertIn("actionableNeedsReviewCount", warning_helper)
        self.assertIn("window.confirm", on_publish)
        self.assertIn("setView('review')", on_publish)
        self.assertIn("검수 화면", warning_helper)

    def test_publish_warning_mentions_remaining_passage_review_queue(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_publish = source.split("const onPublish = async () => {", 1)[1]
        on_publish = on_publish.split("  return (", 1)[0]
        warning_helper = source.split("function publishReviewWarningMessage", 1)[1]
        warning_helper = warning_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("publishReviewWarningMessage(sessionForPublish, publishReviewSummary)", on_publish)
        self.assertIn("passageReviewItemCount", warning_helper)
        self.assertIn("passageReviewLabel", warning_helper)
        self.assertIn("passageReviewPreview", warning_helper)
        self.assertIn("긴 지문 검수", warning_helper)

    def test_publish_blocks_board_placement_overlap_before_request(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_publish = source.split("const onPublish = async () => {", 1)[1]
        on_publish = on_publish.split("  return (", 1)[0]

        self.assertIn("findBoardPlacementOverlaps", on_publish)
        self.assertIn("board_placement_overlap", on_publish)
        self.assertIn("문항 배치가 겹칠 수 있어", on_publish)
        self.assertIn("setView('board')", on_publish)

    def test_publish_blocks_source_problem_bbox_overlap_before_request(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_publish = source.split("const onPublish = async () => {", 1)[1]
        on_publish = on_publish.split("  return (", 1)[0]

        self.assertIn("findSourceProblemOverlaps", on_publish)
        self.assertIn("source_problem_bbox_overlap", on_publish)
        self.assertIn("문항 원본 영역이 겹칠 수 있어", on_publish)
        self.assertIn("setView('review')", on_publish)

    def test_publish_blocks_duplicate_problem_number_groups_before_request(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_publish = source.split("const onPublish = async () => {", 1)[1]
        on_publish = on_publish.split("  return (", 1)[0]

        self.assertIn("blockingDuplicateProblemNumberGroups", on_publish)
        self.assertIn("중복 문항 번호", on_publish)
        self.assertIn("제작을 멈췄어요", on_publish)
        self.assertIn("setView('review')", on_publish)

    def test_publish_surfaces_server_preflight_block_response(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_publish = source.split("const onPublish = async () => {", 1)[1]
        on_publish = on_publish.split("  return (", 1)[0]
        target_helper = source.split("function publishBlockedTarget", 1)[1]
        target_helper = target_helper.split("const NON_ACTIONABLE_RISK_FLAGS", 1)[0]
        review_stage_signature = source.split("function ReviewStage", 1)[1].split("){", 1)[0]

        self.assertIn("normalizePublishPreflightBlock(json)", on_publish)
        self.assertIn("publishBlockedTarget(blockedPublish)", on_publish)
        self.assertIn("setReviewFocus(blockedTarget.reviewFocus)", on_publish)
        self.assertIn("reviewFocus={reviewFocus}", source)
        self.assertIn("reviewFocus", review_stage_signature)
        self.assertIn("blockedPublish.issueSummaryLabel", on_publish)
        self.assertIn("passage_review_queue_remaining", target_helper)
        self.assertIn("passage-review", target_helper)
        self.assertIn("서버 사전점검", on_publish)
        self.assertIn("setView('review')", on_publish)

    def test_board_uses_publish_guard_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("app.jsx?v=passage-reuse-20260614", html)
        self.assertIn("publish_guard.js?v=passage-reuse-20260614", html)


if __name__ == "__main__":
    unittest.main()
