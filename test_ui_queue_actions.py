from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiQueueActions(unittest.TestCase):
    def test_upload_queue_exposes_bulk_register_and_recognize_actions(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")

        self.assertIn("전체 그대로 등록", source)
        self.assertIn("전체 AI 인식", source)
        self.assertIn("onClick={() => processQueuedFiles('register')}", source)
        self.assertIn("onClick={() => processQueuedFiles('recognize')}", source)

    def test_board_uses_queue_bulk_actions_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("app.bundle.js?v=frontend-bundle-20260617", html)

    def test_ai_recognition_application_opens_review_stage(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_branch = source.split("if (review.kind === 'queue-recognition') {", 1)[1]
        queue_branch = queue_branch.split("} else if (review.kind === 'retry-ai') {", 1)[0]

        self.assertIn("setView('review');", queue_branch)
        self.assertIn("검수로 이동", queue_branch)

    def test_queue_recognition_review_copy_points_to_review_stage(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_review_setup = source.split("kind: 'queue-recognition',", 1)[1]
        queue_review_setup = queue_review_setup.split("session: incomingSession,", 1)[0]
        modal_source = source.split("function RecognitionReviewModal", 1)[1]
        modal_source = modal_source.split("function TileImage", 1)[0]

        self.assertIn("검수 화면", queue_review_setup)
        self.assertNotIn("칠판에", queue_review_setup)
        self.assertIn("review?.kind === 'queue-recognition'", modal_source)
        self.assertIn("맞아요, 검수로 이동", modal_source)

    def test_review_stage_exposes_crop_frame_and_partial_retry(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("틀 조정/자르기", source)
        self.assertIn("주변 영역 AI 재인식", source)
        self.assertIn("partial: true", source)
        self.assertIn("cropBoxes", source)
        self.assertIn("MANUAL_CROP_OUTSET_MAX", source)
        self.assertIn("인식 중단", source)
        self.assertIn("crop-frame-handle", html)
        self.assertIn("manual-crop-presets", html)


if __name__ == "__main__":
    unittest.main()
