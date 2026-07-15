from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class TestUiQueueActions(unittest.TestCase):
    def test_upload_queue_exposes_page_png_and_recognize_actions(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")

        self.assertIn("페이지 PNG", source)
        self.assertIn("문제 파싱 없음", source)
        self.assertIn("수동 쪼개기", source)
        self.assertIn("인식 없이 직접 분할", source)
        self.assertIn("문항 AI 인식", source)
        self.assertIn("문제별 자동 분리", source)
        self.assertIn("onClick={() => processQueuedFiles('register')}", source)
        self.assertIn("onClick={() => processQueuedFiles('manual-split')}", source)
        self.assertIn("onClick={() => processQueuedFiles('recognize')}", source)
        self.assertIn("const resolvedInputIntent = isRecognition ? 'multi-problem' : 'page-as-is';", source)

    def test_image_only_recognition_uses_fast_non_ai_path(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback(async (mode, targetKey = null) => {", 1)[1]
        queue_source = queue_source.split("const cancelRecognitionReview = useCallback", 1)[0]

        self.assertIn("function isImageOnlyFileBatch(files)", source)
        self.assertIn("const fastImageRecognition = isRecognition && isImageOnlyFileBatch(files);", queue_source)
        self.assertIn("!fastImageRecognition", queue_source)
        self.assertIn("const recognitionOcr = fastImageRecognition ? 'none' : 'auto';", queue_source)
        self.assertIn("ocr: recognitionOcr", queue_source)
        self.assertIn("detectPerspective: !fastImageRecognition", queue_source)
        self.assertIn("skipDeskew: fastImageRecognition", queue_source)
        self.assertIn("이미지는 AI 보정 없이 원본 경계 중심으로 빠르게 나눕니다", queue_source)

    def test_upload_queue_row_selects_pending_file_preview(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        rail = source.split("function ItemsRail", 1)[1].split("function SidePanel", 1)[0]

        self.assertIn("const [selectedPendingFileKey, setSelectedPendingFileKey] = useState(null);", source)
        self.assertIn("const selectedPendingFile = useMemo", source)
        self.assertIn("const selectPendingFile = useCallback((key) => {", source)
        self.assertIn("setActiveId(null);", source)
        self.assertIn("className={`source-queue-row ${selected ? 'is-selected' : ''}`}", rail)
        self.assertIn("aria-pressed={selected ? 'true' : 'false'}", rail)
        self.assertIn("onClick={() => onSelectPendingFile?.(key)}", rail)
        self.assertIn("onKeyDown={e =>", rail)
        self.assertIn("onClick={e => { e.stopPropagation(); processQueuedFiles('register', key); }}", rail)
        self.assertIn("onClick={e => { e.stopPropagation(); processQueuedFiles('manual-split', key); }}", rail)
        self.assertIn("onClick={e => { e.stopPropagation(); processQueuedFiles('recognize', key); }}", rail)
        self.assertIn("<PendingFilePreview", source)
        self.assertIn("pendingFile={selectedPendingFile}", source)

    def test_queue_and_preview_errors_use_short_toast_helper(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback(async (mode, targetKey = null) => {", 1)[1]
        queue_source = queue_source.split("const cancelRecognitionReview = useCallback", 1)[0]

        self.assertIn("function simpleToastErrorMessage", source)
        self.assertIn("const showSimpleErrorToast = useCallback((error, fallbackMessage) => {", source)
        self.assertIn("showSimpleErrorToast(error, '미리보기 실패')", source)
        self.assertIn("showSimpleErrorToast(e, '문제 인식 실패')", queue_source)
        self.assertIn("showSimpleErrorToast(e, isManualSplit ? '수동 쪼개기 실패' : '등록 실패')", queue_source)
        self.assertNotIn("문제 인식 실패: ${e.message}", queue_source)
        self.assertNotIn("등록'} 실패: ${e.message}", queue_source)

    def test_manual_split_queue_registers_without_recognition_and_opens_editor(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback(async (mode, targetKey = null) => {", 1)[1]
        queue_source = queue_source.split("const cancelRecognitionReview = useCallback", 1)[0]
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("const centerReviewZoomScrollers", 1)[0]

        self.assertIn("const isManualSplit = mode === 'manual-split';", queue_source)
        self.assertIn("isManualSplit ? 'queue-manual-split' : 'queue-register'", queue_source)
        self.assertIn("manualSplitPageId,", queue_source)
        self.assertIn("setView('review');", queue_source)
        self.assertIn("const pageId = String(reviewFocus?.manualSplitPageId || '').trim();", review_stage)
        self.assertIn("beginManualPageSplit(page, replacementIds);", review_stage)

    def test_board_uses_queue_bulk_actions_cache_bust(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("app.bundle.js?v=frontend-bundle-", html)
        self.assertNotIn("app.js?v=", html)

    def test_ai_recognition_application_opens_review_stage(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_branch = source.split("if (review.kind === 'queue-recognition') {", 1)[1]
        queue_branch = queue_branch.split("} else if (review.kind === 'retry-ai') {", 1)[0]

        self.assertIn("setView('review');", queue_branch)
        self.assertIn("검수로 이동", queue_branch)
        self.assertIn("reviewFocusForNewSession(currentSnapshot, restored, 'queue-recognition')", queue_branch)
        self.assertNotIn("openOutputFolder(", queue_branch)

    def test_page_png_registration_does_not_auto_open_output_folder(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        register_branch = source.split("const s = await postExport(files, aiFallback, resolvedInputIntent,", 1)[1]
        register_branch = register_branch.split("} catch (e) {", 1)[0]

        self.assertIn("페이지 PNG 등록", register_branch)
        self.assertIn("const nextReviewFocus = reviewFocusForNewSession", register_branch)
        self.assertIn("isManualSplit ? 'queue-manual-split' : 'queue-register'", register_branch)
        self.assertIn("setReviewFocus(nextReviewFocus);", register_branch)
        self.assertIn("preview: true,", register_branch)
        self.assertIn("exportEdb: false,", register_branch)
        self.assertNotIn("openOutputFolder(", register_branch)

    def test_review_scope_limits_all_tab_to_recently_added_batch(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("function ItemsRail", 1)[0]

        self.assertIn("reviewScopeProblemIds", review_stage)
        self.assertIn("reviewScopePageIds", review_stage)
        self.assertIn("const scopedProblems = useMemo", review_stage)
        self.assertIn("countReviewFilters?.(scopedProblems)", review_stage)
        self.assertIn(".filter(problemInReviewScope)", review_stage)
        self.assertIn("최근 추가 묶음", review_stage)
        self.assertIn("전체 세션 보기", review_stage)

    def test_review_filter_counts_match_the_problems_the_filter_will_show(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        filter_source = source.split("const filterOptions = [", 1)[1].split("];", 1)[0]

        self.assertIn("['check_needed', '확인 필요', statusCounts.check_needed]", filter_source)
        self.assertNotIn("['check_needed', '확인 필요', actionableStatusCount]", filter_source)

    def test_topbar_exposes_reset_icon_outside_more_menu(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        topbar = source.split("function TopBar", 1)[1]
        topbar = topbar.split("function ReviewStage", 1)[0]
        actions = topbar.split('<div className="topbar-actions"', 1)[1]
        actions = actions.split('<div className="topbar-more"', 1)[0]

        self.assertIn('aria-label="초기화"', actions)
        self.assertIn("onClick={onReset}", actions)
        self.assertIn("{Icon.reset}", actions)
        self.assertLess(actions.index('aria-label="초기화"'), actions.index("aria-label={refreshing ? '세션 새로고침 중' : '세션 새로고침'}"))
        self.assertIn("저장된 최신 세션 다시 읽기", topbar)
        self.assertNotIn("현재 세션 다시 불러오기", topbar)

    def test_review_selected_boxes_delete_key_excludes_and_can_undo(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("function ItemsRail", 1)[0]
        do_exclude = review_stage.split("const doExclude = useCallback(async () => {", 1)[1]
        do_exclude = do_exclude.split("const doRetryAi", 1)[0]
        delete_key_handler = review_stage.split("const onReviewDeleteKey = (evt) => {", 1)[1]
        delete_key_handler = delete_key_handler.split("window.addEventListener('keydown', onReviewDeleteKey)", 1)[0]

        self.assertIn("if (selectedActionIds.length === 0 || mutating) return;", do_exclude)
        self.assertIn("mutateSession?.('exclude', { problemId: selectedActionIds[0] })", do_exclude)
        self.assertIn("mutateSession?.('exclude', { problemIds: selectedActionIds })", do_exclude)
        self.assertIn("evt.key !== 'Delete' && evt.key !== 'Backspace'", delete_key_handler)
        self.assertIn("isEditableKeyboardTarget(evt.target)", delete_key_handler)
        self.assertIn("void doExclude();", delete_key_handler)

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

    def test_running_recognition_exposes_prominent_cancel_banner(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("function RecognitionCancelBanner", source)
        self.assertIn("const runningRecognitionJob = backgroundJobs.find", source)
        self.assertIn("String(job.scope || '').includes('recognition')", source)
        self.assertIn("<RecognitionCancelBanner", source)
        self.assertIn("job={runningRecognitionJob}", source)
        self.assertIn("onCancel={cancelBackgroundJob}", source)
        self.assertIn("인식 취소", source)
        self.assertIn("잘못 눌렀다면 지금 취소할 수 있습니다", source)
        self.assertIn(".recognition-cancel-banner", html)
        self.assertIn(".recognition-cancel-action", html)

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

    def test_review_stage_exposes_crop_frame_fast_surrounding_crop_and_continuation(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("틀 조정/자르기", source)
        self.assertIn("주변 포함 빠른 자르기", source)
        self.assertIn("applyExpandedCrop", source)
        self.assertIn("pendingBoxEditProblemIdRef", source)
        self.assertIn("다른 문제를 클릭하면 현재 자르기를 적용하고 다음 틀을 이어 조정합니다", source)
        self.assertIn("MANUAL_CROP_OUTSET_MAX", source)
        self.assertIn("인식 중단", source)
        self.assertIn("onManualCropOutsideMouseDown", source)
        self.assertIn("window.addEventListener('mousedown', onManualCropOutsideMouseDown, true)", source)
        self.assertIn("target?.closest?.('.review-bbox.editing')", source)
        self.assertIn("target?.closest?.('.review-bbox')", source)
        self.assertIn("void applyBoxEdit();", source)
        self.assertIn("crop-frame-handle", html)
        self.assertIn("manual-crop-presets", html)

    def test_review_crop_apply_is_primary_rightmost_and_preserves_current_steps(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        box_edit_branch = source.split("const actionBar = boxEdit ? (", 1)[1]
        box_edit_branch = box_edit_branch.split(") : splitTarget ? (", 1)[0]
        retry_index = box_edit_branch.index("주변 포함 빠른 자르기")
        apply_index = box_edit_branch.index("자르기 적용")

        self.assertLess(retry_index, apply_index)
        self.assertIn('<button className="btn primary" type="button" onClick={applyBoxEdit}', box_edit_branch)

        mutation_source = source.split("const mutateSession = useCallback(async (action, args) => {", 1)[1]
        mutation_source = mutation_source.split("const retryAiSession = useCallback", 1)[0]
        retry_source = source.split("const retryAiSession = useCallback(async (args) => {", 1)[1]
        retry_source = retry_source.split("const recognizeCurrentSession = useCallback", 1)[0]
        self.assertIn("materializeSessionForItems(session, items, fileName, boardColumns) || session", mutation_source)
        self.assertLess(mutation_source.index("await postRestore(snapshotBefore);"), mutation_source.index("await postMutate(action, args);"))
        self.assertIn("materializeSessionForItems(session, items, fileName, boardColumns) || cloneSession(session)", retry_source)
        self.assertLess(retry_source.index("await postRestore(snapshotBefore);"), retry_source.index("const result = await postRetryAi(args"))

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

    def test_input_intent_choices_use_readable_single_column_layout(self) -> None:
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        intent_control = html.split(".intent-control{", 1)[1].split("}", 1)[0]
        intent_title = html.split(".intent-choice-head strong{", 1)[1].split("}", 1)[0]

        self.assertIn("grid-template-columns: 1fr", intent_control)
        self.assertIn("word-break: keep-all", intent_title)

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
