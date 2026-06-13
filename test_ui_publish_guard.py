from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiPublishGuard(unittest.TestCase):
    def test_publish_warns_and_returns_to_review_when_actionable_items_remain(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_publish = source.split("const onPublish = async () => {", 1)[1]
        on_publish = on_publish.split("  return (", 1)[0]

        self.assertIn("sessionReviewSummary(session)", on_publish)
        self.assertIn("actionableNeedsReviewCount", on_publish)
        self.assertIn("window.confirm", on_publish)
        self.assertIn("setView('review')", on_publish)
        self.assertIn("검수 화면", on_publish)

    def test_publish_blocks_board_placement_overlap_before_request(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        on_publish = source.split("const onPublish = async () => {", 1)[1]
        on_publish = on_publish.split("  return (", 1)[0]

        self.assertIn("findBoardPlacementOverlaps", on_publish)
        self.assertIn("board_placement_overlap", on_publish)
        self.assertIn("문항 배치가 겹칠 수 있어", on_publish)
        self.assertIn("setView('board')", on_publish)

    def test_board_uses_publish_guard_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("app.jsx?v=passage-continuation-20260614", html)
        self.assertIn("publish_guard.js?v=publish-overlap-20260614", html)


if __name__ == "__main__":
    unittest.main()
