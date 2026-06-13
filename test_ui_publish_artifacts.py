from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiPublishArtifacts(unittest.TestCase):
    def test_publish_result_panel_disables_missing_artifact_actions(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        panel = source.split("function PublishResultPanel", 1)[1]
        panel = panel.split("async function postRestore", 1)[0]

        self.assertIn("summary.canDownload", panel)
        self.assertIn("summary.canOpenEdbFile", panel)
        self.assertIn("summary.canOpenOutputDir", panel)
        self.assertIn("파일 없음", panel)
        self.assertIn("폴더 없음", panel)
        self.assertIn("ClassIn 열기", panel)
        self.assertIn("openPublishedEdb(summary)", panel)
        self.assertIn("summary.canOpenClassinHandoff", panel)
        self.assertIn("ClassIn 검수", panel)
        self.assertIn("summary.classinReviewStatusLabel", panel)
        self.assertIn("summary.classinHandoffStatusLabel", panel)
        self.assertIn("summary.classinPreflightStatusLabel", panel)
        self.assertIn("사전점검", panel)
        self.assertIn("summary.canMarkClassinReviewComplete", panel)
        self.assertIn("검수 완료", panel)

    def test_recent_sessions_expose_direct_publish_artifact_actions(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        rail = source.split("function ItemsRail", 1)[1]
        rail = rail.split("function StageBoard", 1)[0]

        self.assertIn("normalizePublishSummary(entry.publishSummary || entry.publish_summary, entry)", rail)
        self.assertIn("최근 제작본 다운로드", rail)
        self.assertIn("publish.canDownload", rail)
        self.assertIn("publish.canOpenEdbFile", rail)
        self.assertIn("publish.canOpenOutputDir", rail)
        self.assertIn("publish.canOpenClassinHandoff", rail)
        self.assertIn("downloadPublishSummary(publish)", rail)
        self.assertIn("openPublishedEdb(publish)", rail)
        self.assertIn("openOutputFolder(publish.outputDir)", rail)
        self.assertIn("openClassinHandoff(publish)", rail)
        self.assertIn("publish.classinReviewStatusLabel", rail)

    def test_app_fallback_publish_summary_normalizes_artifact_availability(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        helper = source.split("function normalizePublishSummary", 1)[1]
        helper = helper.split("function sessionPublishSummary", 1)[0]

        self.assertIn("edbFileExists", helper)
        self.assertIn("outputDirExists", helper)
        self.assertIn("canDownload", helper)
        self.assertIn("canOpenEdbFile", helper)
        self.assertIn("canOpenOutputDir", helper)
        self.assertIn("classinHandoffUri", helper)
        self.assertIn("canOpenClassinHandoff", helper)
        self.assertIn("classinReviewStatus", helper)
        self.assertIn("classinHandoffStatus", helper)
        self.assertIn("classinHandoffStatusLabel", helper)
        self.assertIn("classinPreflightStatus", helper)
        self.assertIn("classinPreflightStatusLabel", helper)
        self.assertIn("canMarkClassinReviewComplete", helper)

    def test_app_posts_classin_review_completion(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")

        self.assertIn("async function postClassinReviewResult", source)
        self.assertIn("fetch('/api/session/classin-review'", source)
        self.assertIn("markClassinReviewComplete", source)
        self.assertIn("status: 'passed'", source)

    def test_board_uses_publish_artifact_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("publish_summary.js?v=handoff-readiness-20260614", html)
        self.assertIn("app.jsx?v=passage-continuation-20260614", html)


if __name__ == "__main__":
    unittest.main()
