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
        loading = loading.split("function RecognitionPageReviewStage", 1)[0]

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

        self.assertIn("if (event.buttons)", tooltip)
        self.assertIn(
            "document.addEventListener('pointermove', onPointerMove, { capture: true, passive: true })",
            tooltip,
        )

    def test_rail_drag_hit_testing_is_coalesced_per_animation_frame(self) -> None:
        items_rail = self.source.split("function ItemsRail({", 1)[1]
        items_rail = items_rail.split("function BoardStage({", 1)[0]
        pointer_move = items_rail.split("const movePointerDrag = (event) => {", 1)[1]
        pointer_move = pointer_move.split("const finishPointerDrag", 1)[0]

        self.assertIn("sourceIdSet: new Set(sourceIds)", items_rail)
        self.assertIn("dragVisualFrameRef.current = window.requestAnimationFrame", items_rail)
        self.assertIn("activeDrag.sourceIdSet", items_rail)
        self.assertNotIn("findPointerDropTarget(", pointer_move)
        self.assertIn("for (const { item } of visibleItemRows)", items_rail)

    def test_tile_images_decode_async_and_gpu_hints_are_drag_scoped(self) -> None:
        board = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        tile_image = self.source.split("function TileImage", 1)[1]
        tile_image = tile_image.split("function canPreviewImageFile", 1)[0]
        base_tile_css = board.split(".stage-tile{", 1)[1].split("}", 1)[0]
        positioning_css = board.split(".stage-tile.positioning{", 1)[1].split("}", 1)[0]

        self.assertIn('loading="lazy"', tile_image)
        self.assertIn('decoding="async"', tile_image)
        self.assertNotIn("will-change", base_tile_css)
        self.assertIn("will-change: transform", positioning_css)

    def test_recognition_preview_scrolls_all_pages_but_lazily_loads_later_images(self) -> None:
        stage = self.source.split("function RecognitionPageReviewStage", 1)[1]
        stage = stage.split("function TileImage", 1)[0]

        self.assertIn("pageRows.map((row, pageIndex) =>", stage)
        self.assertIn("pageRows.map(({ page, problems: pageProblems }, pageIndex)", stage)
        self.assertIn('data-recognition-page-index={pageIndex}', stage)
        self.assertIn("loading={pageIndex < 2 ? 'eager' : 'lazy'}", stage)
        self.assertEqual(stage.count("filePreviewUrl(page.sourceImageUri)"), 1)
        self.assertIn('decoding="async"', stage)
        self.assertIn("fetchPriority={pageIndex < 2 ? 'high' : 'auto'}", stage)

    def test_center_panels_request_display_sized_images_only(self) -> None:
        self.assertIn("const CENTER_PANEL_PREVIEW_MAX_DIMENSION = 1024", self.source)
        self.assertGreaterEqual(
            self.source.count("filePreviewUrl(page.sourceImageUri)"),
            4,
        )
        self.assertIn(
            "<TileImage item={it} previewMaxDimension={CENTER_PANEL_PREVIEW_MAX_DIMENSION} />",
            self.source,
        )
        tile_image = self.source.split("function TileImage", 1)[1]
        tile_image = tile_image.split("function canPreviewImageFile", 1)[0]
        self.assertIn("filePreviewUrl(url, previewMaxDimension)", tile_image)
        self.assertIn("const displayUrl = previewMaxDimension", tile_image)
        self.assertIn("src={displayUrl}", tile_image)

    def test_mobile_sidebar_keeps_scrolling_and_selection_tools_compact(self) -> None:
        board = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("touch-action: pan-y", board)
        self.assertIn(".item .grip{", board)
        self.assertIn(".stage-tile .tile-hd{", board)
        self.assertIn("touch-action: none", board)
        self.assertIn("overscroll-behavior: contain", board)
        self.assertIn("scrollbar-gutter: stable", board)
        self.assertIn("overscroll-behavior-inline: contain", board)
        self.assertIn(".problem-order-status .rail-selection-tools{", board)
        self.assertIn(".items .problem-order-status.is-selection > span{", board)

    def test_smooth_scroll_respects_reduced_motion(self) -> None:
        smooth_scroll = self.source.split("function smoothScrollTo", 1)[1]
        smooth_scroll = smooth_scroll.split("const Icon = {", 1)[0]

        self.assertIn("prefers-reduced-motion: reduce", smooth_scroll)
        self.assertIn("duration = 0", smooth_scroll)

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
