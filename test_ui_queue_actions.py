from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiQueueActions(unittest.TestCase):
    def test_upload_queue_exposes_page_png_and_recognize_actions(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")

        self.assertIn("페이지 PNG", source)
        self.assertIn("문제 파싱 없음", source)
        self.assertIn("문항 AI 인식", source)
        self.assertIn("문제별 자동 분리", source)
        self.assertIn("onClick={() => processQueuedFiles('register')}", source)
        self.assertIn("onClick={() => processQueuedFiles('recognize')}", source)
        self.assertIn("const resolvedInputIntent = isRecognition ? 'multi-problem' : 'page-as-is';", source)

    def test_board_uses_queue_bulk_actions_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("app.bundle.js?v=frontend-bundle-20260702-edb-split", html)

    def test_ai_recognition_application_opens_review_stage(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_branch = source.split("if (review.kind === 'queue-recognition') {", 1)[1]
        queue_branch = queue_branch.split("} else if (review.kind === 'retry-ai') {", 1)[0]

        self.assertIn("setView('review');", queue_branch)
        self.assertIn("검수로 이동", queue_branch)

    def test_queue_recognition_ignores_stale_queue_results(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        recognition_branch = source.split("if (isRecognition) {", 1)[1]
        recognition_branch = recognition_branch.split("setLoading({", 1)[0]
        confirm_branch = source.split("if (review.kind === 'queue-recognition') {", 1)[1]
        confirm_branch = confirm_branch.split("} else if (review.kind === 'retry-ai') {", 1)[0]

        self.assertIn("const pendingFileKeysRef = useRef(new Set());", source)
        self.assertIn("const queueGenerationRef = useRef(0);", source)
        self.assertIn("const queueGeneration = queueGenerationRef.current;", recognition_branch)
        self.assertIn("if (!queueRequestIsCurrent(queueGeneration, fileKeys))", recognition_branch)
        self.assertIn("queueGeneration,", recognition_branch)
        self.assertIn("if (!queueRequestIsCurrent(review.queueGeneration, review.fileKeys || []))", confirm_branch)

    def test_session_history_refresh_ignores_stale_responses(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        refresh_source = source.split("const refreshSessionHistory = useCallback(async () => {", 1)[1]
        refresh_source = refresh_source.split("const dismissBackgroundJob", 1)[0]

        self.assertIn("const sessionHistoryRequestRef = useRef(0);", source)
        self.assertIn("setRecentSessionsAuthoritative", source)
        self.assertIn("const requestId = sessionHistoryRequestRef.current + 1;", refresh_source)
        self.assertIn("if (requestId === sessionHistoryRequestRef.current)", refresh_source)

    def test_reset_aborts_background_jobs_and_clears_stale_review_state(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        reset_source = source.split("const resetSession = useCallback(async () => {", 1)[1]
        reset_source = reset_source.split("const shutdownApp", 1)[0]

        self.assertIn("jobControllersRef.current.forEach(controller => controller.abort());", reset_source)
        self.assertIn("setBackgroundJobs([]);", reset_source)
        self.assertIn("setRecognitionReview(null);", reset_source)
        self.assertIn("setPendingFilesTracked([]);", reset_source)

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
        self.assertIn("바깥을 클릭해 바로 적용", source)
        self.assertIn("onManualCropOutsideMouseDown", source)
        self.assertIn("window.addEventListener('mousedown', onManualCropOutsideMouseDown, true)", source)
        self.assertIn("target?.closest?.('.review-bbox.editing')", source)
        self.assertIn("void applyBoxEdit();", source)
        self.assertIn("crop-frame-handle", html)
        self.assertIn("manual-crop-presets", html)

    def test_review_crop_apply_is_primary_rightmost_and_preserves_current_steps(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        box_edit_branch = source.split("const actionBar = boxEdit ? (", 1)[1]
        box_edit_branch = box_edit_branch.split(") : splitTarget ? (", 1)[0]
        retry_index = box_edit_branch.index("주변 영역 AI 재인식")
        apply_index = box_edit_branch.index("자르기 적용")

        self.assertLess(retry_index, apply_index)
        self.assertIn('<button className="btn primary" type="button" onClick={applyBoxEdit}', box_edit_branch)

        mutation_source = source.split("const mutateSession = useCallback(async (action, args) => {", 1)[1]
        mutation_source = mutation_source.split("const retryAiSession = useCallback", 1)[0]
        self.assertIn("materializeSessionForItems(session, items, fileName) || session", mutation_source)
        self.assertLess(mutation_source.index("await postRestore(snapshotBefore);"), mutation_source.index("await postMutate(action, args);"))

    def test_review_stage_exposes_manual_split_bulk_crop_apply(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        bundle = (PROJECT_ROOT / "ui_prototype" / "app.bundle.js").read_text(encoding="utf-8")

        self.assertIn("function ManualSplitEditor", source)
        self.assertIn("수동 쪼개기", source)
        self.assertIn("분할 적용", source)
        self.assertIn("mutateSession?.('bulk-crop'", source)
        self.assertIn("serializeManualSplitRegions", source)
        self.assertIn("manualSplitStampBoxFromPoint", source)
        self.assertIn("stampManualSplitRegion", source)
        self.assertIn("manual-split-tool", source)
        self.assertIn("manual-stamp-card", source)
        self.assertIn("focusShadeRegionId", source)
        self.assertIn("focus-shade", source)
        self.assertIn("Esc로 스탬프 종료", source)
        self.assertIn("aria-keyshortcuts=\"Escape\"", source)
        self.assertIn("aria-keyshortcuts=\"Enter\"", source)
        self.assertIn("manualSplit.mode === 'stamp'", source)
        self.assertIn("setManualSplitMode('draw')", source)
        self.assertIn("manual-split-panel-actions", source)
        self.assertIn("onApply={applyManualPageSplit}", source)
        self.assertIn("스탬프 크기 조절", source)
        self.assertIn("manual-stamp-field", source)
        self.assertIn("manual-stamp-scale-actions", source)
        self.assertIn("스탬프 10% 확대", source)
        self.assertIn("onStampSizeChange={updateManualSplitStampSize}", source)
        self.assertIn("clampManualSplitStampBox", source)
        self.assertIn("const nextSession = await mutateSession?.('bulk-crop', payload);", source)
        self.assertIn("if (!nextSession) return;", source)
        self.assertIn("const [reviewZoom, setReviewZoom] = useState(1)", source)
        self.assertIn("onWheel={handleReviewWheel}", source)
        self.assertIn("review-zoom-controls", source)
        key_handler = source.split("const onKeyDown = (evt) => {", 1)[1]
        key_handler = key_handler.split("if (evt.key === 'Delete'", 1)[0]
        self.assertLess(key_handler.index("manualSplit.mode === 'stamp'"), key_handler.index("if (isFormControl) return;"))
        self.assertIn("0 0 0 9999px rgba(13,18,30,.20)", html)
        self.assertIn("이 크기로 계속", source)
        self.assertIn("onManualSplitOutsideMouseDown", source)
        self.assertIn("window.addEventListener('mousedown', onManualSplitOutsideMouseDown, true)", source)
        self.assertIn("target?.closest?.('.manual-split-layout')", source)
        self.assertIn("void applyManualPageSplit();", source)
        self.assertIn("manual-split-box", bundle)
        self.assertIn("스탬프", bundle)

    def test_undo_restores_server_snapshot_order_directly(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        undo_source = source.split("const undoMutation = useCallback(async () => {", 1)[1]
        undo_source = undo_source.split("  // Ctrl/Cmd+Z", 1)[0]

        self.assertIn("const restored = await postRestore(snapshot);", undo_source)
        self.assertIn("applySession(restored);", undo_source)
        self.assertNotIn("adoptMutatedSession(restored", undo_source)

    def test_items_rail_keeps_step_and_source_on_one_line_without_status_text_chip(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        rail_item = source.split("{items.map((it, i) => {", 1)[1]
        rail_item = rail_item.split("</div>\n        );})}", 1)[0]

        self.assertIn('className="source-label"', rail_item)
        self.assertIn('className="icon-btn item-download-action"', rail_item)
        self.assertIn("이 자료 PNG 다운로드", rail_item)
        self.assertIn("onDownloadItemImage?.(it)", rail_item)
        self.assertNotIn("statusShortLabel", rail_item)
        self.assertNotIn("status-tag", rail_item)
        self.assertIn(".item .meta .sub .source-label", html)
        self.assertIn(".item .actions .item-download-action", html)
        self.assertIn("word-break: keep-all", html)


if __name__ == "__main__":
    unittest.main()
