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
        self.assertIn("summary.edbSplit", panel)
        self.assertIn("summary.edbPartCount", panel)
        self.assertIn("EDB files", panel)
        self.assertIn("파일 없음", panel)
        self.assertIn("폴더 없음", panel)
        self.assertIn("ClassIn 열기", panel)
        self.assertIn("openPublishedEdb(summary)", panel)
        self.assertIn("summary.canOpenClassinHandoff", panel)
        self.assertIn("ClassIn 검수", panel)
        self.assertIn("summary.classinReviewStatusLabel", panel)
        self.assertIn("summary.classinHandoffStatusLabel", panel)
        self.assertIn("summary.classinPreflightStatusLabel", panel)
        self.assertIn("summary.classinPreflightIssueSummaryLabel", panel)
        self.assertIn("summary.passageGroupLabel", panel)
        self.assertIn("summary.passageReviewLabel", panel)
        self.assertIn("summary.passageReviewReasonLabel", panel)
        self.assertIn("summary.passageGroupSourceReuseLabel", panel)
        self.assertIn("지문 원본 중복", panel)
        self.assertIn("사전점검", panel)
        self.assertIn("긴 지문", panel)
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
        self.assertIn("edbParts", helper)
        self.assertIn("edbPartCount", helper)
        self.assertIn("edbSplit", helper)
        self.assertIn("summary.edbParts.some", helper)
        self.assertIn("classinHandoffUri", helper)
        self.assertIn("canOpenClassinHandoff", helper)
        self.assertIn("classinReviewStatus", helper)
        self.assertIn("classinHandoffStatus", helper)
        self.assertIn("classinHandoffStatusLabel", helper)
        self.assertIn("classinPreflightStatus", helper)
        self.assertIn("classinPreflightStatusLabel", helper)
        self.assertIn("passageGroupCount", helper)
        self.assertIn("passageGroupLabel", helper)
        self.assertIn("passageReviewItems", helper)
        self.assertIn("passageReviewLabel", helper)
        self.assertIn("passageReviewReasonLabel", helper)
        self.assertIn("passageGroupSourceReuseGroups", helper)
        self.assertIn("passageGroupSourceReuseLabel", helper)
        self.assertIn("canMarkClassinReviewComplete", helper)

    def test_app_posts_classin_review_completion(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")

        self.assertIn("async function postClassinReviewResult", source)
        self.assertIn("fetch('/api/session/classin-review'", source)
        self.assertIn("markClassinReviewComplete", source)
        self.assertIn("status: 'passed'", source)

    def test_publish_result_panel_exposes_png_bundle_action(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        panel = source.split("function PublishResultPanel", 1)[1]
        panel = panel.split("function SidePanel", 1)[0]

        self.assertIn("onExportImages", panel)
        self.assertIn("exportingImages", panel)
        self.assertIn("canExportImages", panel)
        self.assertIn("PNG 묶음", panel)
        self.assertIn("현재 문제 이미지를 PNG 묶음으로 다운로드", panel)

    def test_board_toolbar_exposes_image_download_action(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        topbar = source.split("function TopBar", 1)[1]
        topbar = topbar.split("function ReviewStage", 1)[0]

        self.assertIn("async function postExportImages", source)
        self.assertIn("fetch('/api/session/export-images'", source)
        self.assertIn("const exportSessionImages = useCallback", source)
        self.assertIn("result.downloadUrl", source)
        self.assertIn("result.fileName", source)
        self.assertIn("onExportImages", topbar)
        self.assertIn("exportingImages", topbar)
        self.assertIn("canExportImages", topbar)
        self.assertIn("이미지 다운로드", topbar)
        self.assertIn("PNG ZIP", topbar)

    def test_app_sends_requested_edb_name_for_export_and_publish(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        export_helper = source.split("async function postExport", 1)[1]
        export_helper = export_helper.split("function formatApiError", 1)[0]
        publish_call = source.split("fetch('/api/session/publish'", 1)[1]
        publish_call = publish_call.split("const json = await readJsonResponse", 1)[0]

        self.assertIn("function edbFileNameFromSessionName", source)
        self.assertIn("const edbName = edbFileNameFromSessionName(options.edbName", export_helper)
        self.assertIn("edbName,", export_helper)
        self.assertIn("edbName: edbFileNameFromSessionName(fileName", publish_call)

    def test_app_downloads_each_split_edb_part(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        helper = source.split("function downloadPublishSummary", 1)[1]
        helper = helper.split("async function openPublishedEdb", 1)[0]

        self.assertIn("Array.isArray(target.edbParts)", helper)
        self.assertIn(".forEach((part, index)", helper)
        self.assertIn("window.setTimeout", helper)
        self.assertIn("index * 150", helper)
        self.assertIn("part.edbFileUri || part.edb_file_uri", helper)
        self.assertIn("part.edbFileName || part.edb_file_name", helper)

    def test_served_bundle_uses_split_publish_download_path(self) -> None:
        bundle = (PROJECT_ROOT / "ui_prototype" / "app.bundle.js").read_text(encoding="utf-8")

        self.assertIn("downloadPublishSummary(normalizedPublishSummary)", bundle)
        self.assertNotIn("publishSummary?.edbFileUri", bundle)

    def test_board_preview_no_longer_labels_fifty_pages_as_max(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")

        self.assertNotIn("Math.min(layout.totalPages, 50)", source)
        self.assertNotIn("최대 50", source)

    def test_board_uses_publish_artifact_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("publish_summary.js?v=source-overlap-summary-20260614", html)
        self.assertIn("app.bundle.js?v=frontend-bundle-20260702-edb-split", html)


if __name__ == "__main__":
    unittest.main()
