from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiPerformance(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")

    def test_board_scroll_state_is_coalesced_per_animation_frame(self) -> None:
        board_stage = self.source.split("function BoardStage", 1)[1]
        board_stage = board_stage.split("function downloadPublishSummary", 1)[0]

        self.assertIn("const scrollSyncFrameRef = useRef(null)", board_stage)
        self.assertIn("if (scrollSyncFrameRef.current != null) return", board_stage)
        self.assertIn("scrollSyncFrameRef.current = window.requestAnimationFrame", board_stage)
        self.assertIn("window.cancelAnimationFrame(scrollSyncFrameRef.current)", board_stage)

    def test_elapsed_time_updates_do_not_run_twice_per_second(self) -> None:
        loading = self.source.split("function LoadingOverlay", 1)[1]
        loading = loading.split("function RecognitionReviewModal", 1)[0]

        self.assertNotIn("setInterval(tick, 500)", loading)
        self.assertNotIn("setInterval(() => setNow(Date.now()), 500)", loading)
        self.assertGreaterEqual(loading.count("1000"), 3)

    def test_background_job_interval_depends_on_stable_boolean(self) -> None:
        panel = self.source.split("function BackgroundJobsPanel", 1)[1]
        panel = panel.split("function RecognitionCancelBanner", 1)[0]

        self.assertIn("const hasRunningJob = visibleJobs.some", panel)
        self.assertIn("}, [hasRunningJob]);", panel)
        self.assertNotIn("}, [visibleJobs]);", panel)

    def test_global_pointer_tracking_is_passive(self) -> None:
        tooltip = self.source.split("function TooltipLayer", 1)[1]
        tooltip = tooltip.split("function TopBar", 1)[0]

        self.assertIn(
            "document.addEventListener('pointermove', onPointerMove, { capture: true, passive: true })",
            tooltip,
        )

    def test_large_upload_is_rejected_before_base64_encoding(self) -> None:
        post_export = self.source.split("async function postExport", 1)[1]
        post_export = post_export.split("function formatApiError", 1)[0]

        self.assertIn("const MAX_EXPORT_JSON_BYTES = 64 * 1024 * 1024", self.source)
        self.assertIn("function estimatedExportPayloadBytes", self.source)
        self.assertIn("function assertExportPayloadFits", self.source)
        self.assertLess(
            post_export.index("assertExportPayloadFits(files)"),
            post_export.index("Promise.all(files.map"),
        )
        self.assertIn("파일을 나누어 등록하거나 PDF를 압축해 주세요", self.source)
        self.assertIn("`${fallbackMessage} · 파일을 나누어 등록하세요`", self.source)


if __name__ == "__main__":
    unittest.main()
