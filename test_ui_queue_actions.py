from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

def board_document() -> str:
    """board.html plus the stylesheet it links.

    The editor theme used to be an inline <style> block; it now lives in
    ui_prototype/board.css. These assertions cover both halves of the document.
    """

    ui_root = PROJECT_ROOT / "ui_prototype"
    return (
        (ui_root / "board.html").read_text(encoding="utf-8")
        + "\n"
        + (ui_root / "board.css").read_text(encoding="utf-8")
    )



def media_rules(css: str, query: str) -> str:
    """Every rule that applies at `query`, across all blocks using it.

    The stylesheet declares the same breakpoint in more than one place, so a
    naive split on the first occurrence reads the wrong block.
    """

    marker = f"@media ({query})" + "{"
    collected = []
    index = css.find(marker)
    while index != -1:
        cursor = index + len(marker)
        depth = 1
        while cursor < len(css) and depth:
            if css[cursor] == "{":
                depth += 1
            elif css[cursor] == "}":
                depth -= 1
            cursor += 1
        collected.append(css[index + len(marker) : cursor - 1])
        index = css.find(marker, cursor)
    return "\n".join(collected)


class TestUiQueueActions(unittest.TestCase):
    def test_upload_queue_uses_one_split_choice_and_one_split_button(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        rail = source.split("function ItemsRail", 1)[1].split("function BoardStage", 1)[0]
        split_options = source.split("const SPLIT_MODE_OPTIONS = [", 1)[1].split("];", 1)[0]

        self.assertIn("value: 'recognize',", split_options)
        self.assertIn("label: '자동 분할',", split_options)
        self.assertIn("value: 'register',", split_options)
        self.assertIn("label: '페이지 그대로',", split_options)
        self.assertIn("value: 'manual-split',", split_options)
        self.assertIn("label: '직접 자르기',", split_options)
        self.assertIn("const SPLIT_MODE_STORAGE_KEY = 'edb.splitMode';", source)
        self.assertIn("useState(readStoredSplitMode)", source)
        self.assertIn("function SplitModePicker", source)
        self.assertIn("<SplitModePicker", rail)
        self.assertEqual(1, rail.count("processQueuedFiles("))
        self.assertIn("onClick={() => processQueuedFiles(normalizeSplitMode(splitMode))}", rail)
        self.assertIn("splitActionLabel(pendingFiles.length)", rail)
        self.assertNotIn("processQueuedFiles('register')", source)
        self.assertNotIn("processQueuedFiles('manual-split')", source)
        self.assertNotIn("processQueuedFiles('recognize')", source)
        self.assertNotIn("queue-action-card", source)
        self.assertIn("const resolvedInputIntent = isRecognition ? 'multi-problem' : 'page-as-is';", source)
    def test_page_as_is_upload_preserves_the_200_dpi_master(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        export_request = source.split("async function postExport(files", 1)[1]
        export_request = export_request.split("function formatApiError", 1)[0]

        self.assertIn("pills: ['원본 해상도 우선', '200 DPI'", source)
        self.assertIn("pdfDpi: options.pdfDpi || 200", export_request)
        self.assertIn("const DEFAULT_RECOGNITION_MAX_DIMENSION = 4096;", source)
        self.assertIn(
            "maxDimension: resolvedInputIntent === 'page-as-is'",
            export_request,
        )
        self.assertIn("(options.maxDimension || DEFAULT_RECOGNITION_MAX_DIMENSION)", export_request)
        self.assertIn(
            "pageTileMode: resolvedInputIntent === 'page-as-is' ? (options.pageTileMode || 'off') : 'off'",
            export_request,
        )

    def test_side_panel_split_choice_is_the_same_state_the_queue_runs(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        side_panel = source.split("function SidePanel", 1)[1].split("function LoadingOverlay", 1)[0]

        self.assertNotIn("setInputIntent", source)
        self.assertNotIn("INPUT_INTENT_OPTIONS.map", side_panel)
        self.assertIn("splitMode, setSplitMode,", side_panel)
        self.assertIn("업로드 대기열의 [분할하기]가 이 방식으로 실행됩니다", side_panel)
        self.assertEqual(2, side_panel.count("<SplitModePicker") + side_panel.count("<PendingFilePreview"))

    def test_image_only_recognition_uses_fast_non_ai_path(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback(async (mode, targetKey = null) => {", 1)[1]
        queue_source = queue_source.split("const applyRecognitionResult = useCallback", 1)[0]

        self.assertIn("function isImageOnlyFileBatch(files)", source)
        self.assertIn(
            "const fastImageRecognition = isRecognition && !isPassageOnly && isImageOnlyFileBatch(files);",
            queue_source,
        )
        self.assertIn("!fastImageRecognition", queue_source)
        self.assertIn(
            "const recognitionOcr = fastImageRecognition ? 'none' : (aiEnabled ? 'auto' : 'local');",
            queue_source,
        )
        self.assertIn("ocr: recognitionOcr", queue_source)
        self.assertIn("detectPerspective: !fastImageRecognition", queue_source)
        self.assertIn("skipDeskew: fastImageRecognition", queue_source)
        self.assertIn("이미지는 AI 보정 없이 원본 경계 중심으로 빠르게 나눕니다", queue_source)

    def test_passage_only_review_surfaces_text_and_image_quality(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        summary_source = source.split("function summarizeRecognitionSession(session, pageIds){", 1)[1]
        summary_source = summary_source.split("function aiModelFallbackToast(session){", 1)[0]

        self.assertIn("problem?.passageQuality || problem?.passage_quality", summary_source)
        self.assertIn("passageQualityScore", summary_source)
        self.assertIn("passageQualityReviewCount", summary_source)
        self.assertIn("passageQualityReviewCount", summary_source)
        self.assertNotIn("function RecognitionPageReviewStage", source)

    def test_passage_only_uses_deterministic_fast_path_without_ai_page_repair(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback(async (mode, targetKey = null) => {", 1)[1]
        queue_source = queue_source.split("const cancelRecognitionReview = useCallback", 1)[0]

        self.assertIn(
            "isRecognition && !isPassageOnly && aiEnabled && userSettings?.hasGeminiApiKey && !fastImageRecognition",
            queue_source,
        )
        self.assertIn("contentTarget: isPassageOnly ? 'shared-passages' : DEFAULT_CONTENT_TARGET", queue_source)

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
        self.assertNotIn("queue-row-action", rail)
        self.assertIn("onClick={e => { e.stopPropagation(); removePendingFile(key); }}", rail)
        self.assertIn("<PendingFilePreview", source)
        self.assertIn("pendingFile={selectedPendingFile}", source)
        preview = source.split("function PendingFilePreview", 1)[1].split("// ─── backend helpers", 1)[0]
        self.assertIn("processQueuedFiles?.(normalizeSplitMode(splitMode));", preview)
        self.assertIn("{splitActionLabel(pendingCount)}", preview)
        self.assertEqual(1, preview.count("className=\"btn primary split-run-action\""))

    def test_queue_errors_use_persistent_recovery_and_preview_keeps_short_toast(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback(async (mode, targetKey = null) => {", 1)[1]
        queue_source = queue_source.split("const applyRecognitionResult = useCallback", 1)[0]

        self.assertIn("function simpleToastErrorMessage", source)
        self.assertIn("const showSimpleErrorToast = useCallback((error, fallbackMessage, detail = {}) => {", source)
        self.assertIn("showSimpleErrorToast(error, '미리보기 실패')", source)
        self.assertIn(
            "activateOperationRecovery(failure, isPassageOnly ? '공통 지문 추출 실패' : '문제 인식 실패'",
            queue_source,
        )
        self.assertIn("await classifyNetworkRequestError(e, 'recognition_connection_failed')", queue_source)
        self.assertIn("await classifyNetworkRequestError(e, 'registration_connection_failed')", queue_source)
        self.assertIn("activateOperationRecovery(failure, isManualSplit ? '수동 자르기 실패' : '등록 실패'", queue_source)
        self.assertIn("kind: 'recognition'", queue_source)
        self.assertIn("kind: 'registration'", queue_source)
        self.assertNotIn("문제 인식 실패: ${e.message}", queue_source)
        self.assertNotIn("등록'} 실패: ${e.message}", queue_source)

    def test_session_recognition_has_a_synchronous_double_submit_guard(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        retry_source = source.split("const retryAiSession = useCallback(async (args) => {", 1)[1]
        retry_source = retry_source.split("const recognizeCurrentSession = useCallback", 1)[0]
        passage_source = source.split("const reextractSharedPassagesFromSession = useCallback(async () => {", 1)[1]
        passage_source = passage_source.split("const exportSessionImages = useCallback", 1)[0]

        self.assertIn("const sessionRecognitionInFlightRef = useRef(false);", source)
        self.assertIn("const sessionRecognitionGuardUntilRef = useRef(0);", source)
        self.assertIn("sessionRecognitionInFlightRef.current", retry_source)
        self.assertIn("sessionRecognitionInFlightRef.current = true;", retry_source)
        self.assertIn("Date.now() < sessionRecognitionGuardUntilRef.current", retry_source)
        self.assertIn("sessionRecognitionGuardUntilRef.current = Date.now() + 1500;", retry_source)
        self.assertIn("sessionRecognitionInFlightRef.current = false;", retry_source)
        self.assertIn("sessionRecognitionInFlightRef.current", passage_source)
        self.assertIn("sessionRecognitionInFlightRef.current = true;", passage_source)
        self.assertIn("Date.now() < sessionRecognitionGuardUntilRef.current", passage_source)
        self.assertIn("sessionRecognitionGuardUntilRef.current = Date.now() + 1500;", passage_source)
        self.assertIn("sessionRecognitionInFlightRef.current = false;", passage_source)
        self.assertIn("앱을 다시 실행한 뒤 공통 지문 추출을 다시 눌러 주세요.", passage_source)

    def test_manual_split_queue_registers_without_recognition_and_opens_editor(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback(async (mode, targetKey = null) => {", 1)[1]
        queue_source = queue_source.split("const applyRecognitionResult = useCallback", 1)[0]
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("const centerReviewZoomScrollers", 1)[0]

        self.assertIn("const isManualSplit = mode === 'manual-split';", queue_source)
        self.assertIn("isManualSplit ? 'queue-manual-split' : 'queue-register'", queue_source)
        self.assertIn("manualSplitPageId,", queue_source)
        self.assertIn("manualSplitPageIds,", queue_source)
        self.assertIn("setView('review');", queue_source)
        self.assertIn("const pageId = String(reviewFocus?.manualSplitPageId || '').trim();", review_stage)
        self.assertIn("setGuidedSplitPageIds(guidedIds);", review_stage)
        self.assertIn("beginManualPageSplit(page, guidedSplitReplacementIds(page), { guided: true });", review_stage)

    def test_queue_manual_split_cuts_each_page_then_returns_to_board(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1].split("function ItemsRail", 1)[0]
        next_page_effect = review_stage.split("// After each applied page, open the next queued page;", 1)[1]
        next_page_effect = next_page_effect.split("const centerReviewZoomScrollers", 1)[0]
        apply_split = review_stage.split("const applyManualPageSplit = useCallback(async () => {", 1)[1]
        apply_split = apply_split.split("const stampManualSplitRegion", 1)[0]
        cancel_split = review_stage.split("const cancelManualPageSplit = () => {", 1)[1]
        cancel_split = cancel_split.split("const setManualSplitMode", 1)[0]

        self.assertIn("guidedSplitCommitRef.current", next_page_effect)
        self.assertIn("if (!rest.length) onManualSplitFlowDone?.();", next_page_effect)
        self.assertIn("guidedSplitCommitRef.current = !!manualSplit.guided;", apply_split)
        self.assertIn("if (manualSplit.guided) {", apply_split)
        self.assertIn("if (!rest.length) onManualSplitFlowDone?.();", apply_split)
        self.assertIn("guidedSplitCommitRef.current = false;", apply_split)
        self.assertIn("if (wasGuided) {", cancel_split)
        self.assertIn("onManualSplitFlowDone?.();", cancel_split)
        self.assertIn("onManualSplitFlowDone={finishManualSplitFlow}", source)
        finish = source.split("const finishManualSplitFlow = useCallback(() => {", 1)[1].split("}, []);", 1)[0]
        self.assertIn("setView('board');", finish)
        strip = source.split("// A queued 직접 자르기 request is one-shot", 1)[1].split("const finishManualSplitFlow", 1)[0]
        self.assertIn("const { manualSplitPageId, manualSplitPageIds, ...rest } = prev;", strip)

    def test_review_crop_editors_expand_the_workspace_and_restore_the_side_panel(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = board_document()
        review_stage = source.split("function ReviewStage", 1)[1].split("function ItemsRail", 1)[0]

        self.assertIn("const reviewEditorActive = Boolean(boxEdit || manualSplit);", review_stage)
        self.assertIn("onEditorModeChange?.(reviewEditorActive);", review_stage)
        self.assertIn("onEditorModeChange?.(false)", review_stage)
        self.assertIn("const [reviewEditorActive, setReviewEditorActive] = useState(false);", source)
        self.assertIn("view === 'review' && reviewEditorActive ? 'review-editor-focus' : ''", source)
        self.assertIn("onEditorModeChange={setReviewEditorActive}", source)
        self.assertIn(".main.review-editor-focus", html)
        self.assertIn(".main.review-editor-focus > .col.right", html)

    def test_manual_split_panel_stays_accessible_above_canvas_in_narrow_windows(self) -> None:
        responsive_block = media_rules(board_document(), "max-width: 700px")

        self.assertIn(".manual-split-layout.panel-left", responsive_block)
        self.assertIn(".manual-split-layout.panel-right", responsive_block)
        self.assertIn("grid-template-columns: 1fr", responsive_block)
        self.assertIn("position: sticky", responsive_block)
        self.assertIn("45vh", responsive_block)
        panel = responsive_block.split('.manual-split-layout.panel-right .manual-split-panel', 1)[1].split('}', 1)[0]
        self.assertIn('grid-row: 1', panel)
        active_page = board_document().split('.review-wrap.manual-split-open > .review-page{', 1)[1].split('}', 1)[0]
        self.assertIn('overflow: clip', active_page, 'rounded page clipping must not steal sticky scrolling')

    def test_manual_split_exposes_productive_keyboard_shortcuts_and_bulk_actions(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1].split("function ItemsRail", 1)[0]

        self.assertIn("const selectAllManualSplitRegions = () => {", review_stage)
        self.assertIn("const duplicateManualSplitSelected = () => {", review_stage)
        self.assertIn("const toggleManualSplitPanelSide = () => {", review_stage)
        self.assertIn("primaryModifier && !evt.altKey && !evt.shiftKey && key === 'a'", review_stage)
        self.assertIn("primaryModifier && !evt.altKey && !evt.shiftKey && key === 'd'", review_stage)
        self.assertIn("key === 'g'", review_stage)
        self.assertIn("key === 's'", review_stage)
        self.assertIn("key === 'p'", review_stage)
        self.assertIn("evt.key === '?'", review_stage)
        self.assertIn('aria-keyshortcuts="Control+A Meta+A"', review_stage)
        self.assertIn('aria-keyshortcuts="Control+D Meta+D"', review_stage)
        self.assertIn("manual-split-shortcut-guide", source)

    def test_review_actionbars_keep_top_actions_in_one_row(self) -> None:
        html = board_document()

        actionbar_css = html.split(".review-actionbar{", 1)[1].split(".review-summary-strip", 1)[0]
        manual_css = html.split(".manual-split-actionbar{", 1)[1].split("@media (max-width: 820px)", 1)[0]
        self.assertIn("flex-wrap: nowrap", actionbar_css)
        self.assertIn(".review-actionbar-actions", actionbar_css)
        self.assertIn("overflow-x: auto", actionbar_css)
        self.assertIn(".review-toolbar-group-content", actionbar_css)
        self.assertIn("flex: 0 0 auto", actionbar_css)
        filters_css = actionbar_css.split(".review-filters button{", 1)[1]
        filters_css = filters_css.split(".review-filters button span", 1)[0]
        self.assertIn("flex: 0 0 auto", filters_css)
        self.assertIn("white-space: nowrap", filters_css)
        self.assertIn("flex-wrap: nowrap", manual_css)
        self.assertIn(".manual-split-toolbar-actions", manual_css)

    def test_board_uses_queue_bulk_actions_cache_bust(self) -> None:
        html = board_document()

        self.assertIn("app.bundle.js?v=frontend-bundle-", html)
        self.assertNotIn("app.js?v=", html)

    def test_ai_recognition_result_lands_on_board_with_undo(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_branch = source.split("if (review.kind === 'queue-recognition') {", 1)[1]
        queue_branch = queue_branch.split("} else if (review.kind === 'retry-ai') {", 1)[0]

        self.assertIn("setView('board');", queue_branch)
        self.assertNotIn("setView('review');", queue_branch)
        self.assertIn("칠판에 배치했어요", queue_branch)
        self.assertIn("sessionReviewSummary(incomingSession).actionableNeedsReviewCount", queue_branch)
        self.assertIn("확인 필요 ${attentionCount}개는 칠판의 검수 버튼에서 확인", queue_branch)
        self.assertIn("appendBoundedHistory(prev, currentSnapshot, UNDO_HISTORY_LIMIT)", queue_branch)
        self.assertIn("reviewFocusForNewSession(currentSnapshot, restored, 'queue-recognition')", queue_branch)
        self.assertNotIn("openOutputFolder(", queue_branch)

    def test_processing_never_auto_opens_review_stage(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        apply_session = source.split("const applySession = useCallback((rawSession) => {", 1)[1]
        apply_session = apply_session.split("const adoptMutatedSession", 1)[0]
        retry_branch = source.split("} else if (review.kind === 'retry-ai') {", 1)[1]
        retry_branch = retry_branch.split("setRecognitionReview(null);", 1)[0]
        topbar = source.split("function TopBar", 1)[1].split("function ReviewStage", 1)[0]

        self.assertNotIn("shouldOpenReview", source)
        self.assertIn("const wantsReview = !initialViewConsumedRef.current && initialViewRef.current === 'review';", apply_session)
        self.assertNotIn("setView(", retry_branch)
        self.assertIn("reviewAttentionCount = 0", topbar)
        self.assertIn("label: '문항 검수 열기'", topbar)
        self.assertNotIn('className="workflow-progress"', topbar)
        self.assertIn("reviewAttentionCount={reviewAttentionCount}", source)
    def test_queue_recognition_uses_a_synchronous_single_flight_guard(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_action = source.split("const processQueuedFiles = useCallback", 1)[1]
        queue_action = queue_action.split("const cancelRecognitionReview", 1)[0]

        self.assertIn("const recognitionInFlightRef = useRef(false)", source)
        self.assertIn("if (recognitionInFlightRef.current)", queue_action)
        self.assertIn("recognitionInFlightRef.current = true", queue_action)
        self.assertIn("recognitionInFlightRef.current = false", queue_action)
        self.assertLess(
            queue_action.index("recognitionInFlightRef.current = true"),
            queue_action.index("startBackgroundJob({"),
        )

    def test_page_png_registration_does_not_auto_open_output_folder(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        register_branch = source.split("const s = await postExport(files, aiFallback, resolvedInputIntent,", 1)[1]
        register_branch = register_branch.split("} catch (e) {", 1)[0]

        self.assertIn("페이지 그대로 넣기", register_branch)
        self.assertIn("const nextReviewFocus = reviewFocusForNewSession", register_branch)
        self.assertIn("isManualSplit ? 'queue-manual-split' : 'queue-register'", register_branch)
        self.assertIn("setReviewFocus(nextReviewFocus);\n        setView('board');", register_branch)
        self.assertIn("appendBoundedHistory(prev, currentSnapshot, UNDO_HISTORY_LIMIT)", register_branch)
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
        self.assertIn("최근 묶음", review_stage)
        self.assertIn("전체 보기", review_stage)

    def test_review_filter_counts_match_the_problems_the_filter_will_show(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        filter_source = source.split("const filterOptions = [", 1)[1].split("];", 1)[0]

        self.assertIn("['check_needed', '확인 필요', statusCounts.check_needed]", filter_source)
        self.assertNotIn("['check_needed', '확인 필요', actionableStatusCount]", filter_source)

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
        html = board_document()

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

    def test_reset_blocks_running_jobs_and_only_clears_job_ui_after_server_success(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        reset_source = source.split("const resetSession = useCallback(async () => {", 1)[1]
        reset_source = reset_source.split("const shutdownApp", 1)[0]

        self.assertIn("jobControllersRef.current.forEach(controller => controller.abort());", reset_source)
        self.assertIn("setBackgroundJobs([]);", reset_source)
        self.assertIn("setRecognitionReview(null);", reset_source)
        self.assertIn("setPendingFilesTracked([]);", reset_source)
        self.assertIn("backgroundJobs.some(job => job.status === 'running')", reset_source)
        self.assertIn("진행 중인 작업을 취소하거나 완료한 뒤 초기화해 주세요", reset_source)
        self.assertIn("이미 만든 EDB·PNG 출력 파일은 보관됩니다", reset_source)
        self.assertLess(reset_source.index("await clearSession()"), reset_source.index("setBackgroundJobs([]);"))
        self.assertIn("resetBlocked={hasRunningBackgroundJobs}", source)
        self.assertIn("진행 중인 작업을 취소하거나 완료한 뒤 초기화할 수 있습니다", source)
        self.assertIn("downloadInFlightRef.current || downloadBusy", reset_source)
        self.assertIn("EDB 다운로드 준비가 끝난 뒤 초기화해 주세요", reset_source)
        self.assertIn(
            "operationBusy={Boolean(loading) || (!initialSessionLoaded && !initialSessionError) || resetBusy || publishBusy || downloadBusy || hasPendingSessionConflict}",
            source,
        )
        self.assertIn(
            "!resetBusy && !publishBusy && !downloadBusy && !hasRunningBackgroundJobs && !hasPendingSessionConflict",
            source,
        )
        self.assertIn("recognitionInFlightRef.current || queueRegistrationInFlightRef.current", reset_source)

    def test_recognition_results_apply_without_confirm_popup(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        apply_result = source.split("const applyRecognitionResult = useCallback(async () => {", 1)[1]
        apply_result = apply_result.split("const setStep = ", 1)[0]
        catch_branch = apply_result.split("} catch (e) {", 1)[1].split("} finally {", 1)[0]

        self.assertNotIn("function RecognitionReviewModal", source)
        self.assertNotIn("<RecognitionReviewModal", source)
        self.assertNotIn("cancelRecognitionReview", source)
        self.assertNotIn("맞아요", source)
        self.assertNotIn("확인 팝업", source)
        self.assertIn("if (!recognitionReview || confirmingRecognition || mutating || loading || hasPendingSessionConflict) return;", apply_result)
        self.assertIn("void applyRecognitionResult();", apply_result)
        self.assertIn("setRecognitionReview(null);", catch_branch)
        self.assertIn("app.bundle.js?v=frontend-bundle-", html)
    def test_queue_registration_and_recognition_share_a_synchronous_single_flight_boundary(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback", 1)[1]
        queue_source = queue_source.split("const cancelRecognitionReview", 1)[0]

        self.assertIn("const queueRegistrationInFlightRef = useRef(false);", source)
        self.assertIn(
            "recognitionInFlightRef.current || queueRegistrationInFlightRef.current",
            queue_source,
        )
        self.assertIn("queueRegistrationInFlightRef.current = true;", queue_source)
        self.assertIn("queueRegistrationInFlightRef.current = false;", queue_source)
        self.assertLess(
            queue_source.index("queueRegistrationInFlightRef.current = true;"),
            queue_source.index("const s = await postExport"),
        )
        self.assertIn("if (operationRecovery?.conflict)", queue_source)

    def test_review_stage_exposes_crop_frame_fast_surrounding_crop_and_continuation(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = board_document()

        self.assertIn("영역 조정", source)
        self.assertIn("영역 다시 잡기", source)
        self.assertIn("applyExpandedCrop", source)
        self.assertIn("pendingReviewSelectionProblemIdRef", source)
        self.assertIn("시험지 위의 테두리를 바로 조정하고 오른쪽 패널에서 적용하세요. ⌘+휠 확대/축소 · Enter 적용 · Esc 취소", source)
        self.assertIn("onBoxEditKeyDown", source)
        self.assertIn("aria-keyshortcuts=\"Enter\"", source)
        self.assertIn("aria-keyshortcuts=\"Escape\"", source)
        self.assertIn("MANUAL_CROP_OUTSET_MAX", source)
        self.assertIn("인식 중단", source)
        self.assertIn("className={`box-edit-panel ${multi ? 'multi-crop-panel' : ''}`}", source)
        self.assertIn("onApply={applyBoxEdit}", source)
        self.assertIn("onCancel={cancelBoxEdit}", source)
        self.assertIn("void applyBoxEdit();", source)
        self.assertIn("crop-frame-handle", html)
        self.assertIn("manual-crop-presets", html)

    def test_review_stage_has_fast_page_navigation(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = board_document()
        review_stage = source.split("function ReviewStage({", 1)[1]
        review_stage = review_stage.split("function ItemsRail({", 1)[0]

        self.assertIn("const [reviewPageNav, setReviewPageNav]", review_stage)
        self.assertIn("syncReviewPageNavigation", review_stage)
        self.assertIn("jumpReviewPage(-1)", review_stage)
        self.assertIn("jumpReviewPage(1)", review_stage)
        self.assertIn('aria-label="이전 검수 페이지"', review_stage)
        self.assertIn('aria-label="다음 검수 페이지"', review_stage)
        self.assertIn("const handleReviewScroll = useCallback((event) =>", review_stage)
        self.assertIn("reviewScrollSyncFrameRef.current = window.requestAnimationFrame", review_stage)
        self.assertIn("syncReviewPageNavigation(wrap)", review_stage)
        self.assertIn("onScroll={handleReviewScroll}", review_stage)
        self.assertIn(".review-page-jump{", html)
        self.assertIn(".review-page-jump-status{", html)

    def test_review_crop_apply_is_primary_rightmost_and_preserves_current_steps(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        box_edit_panel = source.split("className={`box-edit-panel ${multi ? 'multi-crop-panel' : ''}`}", 1)[1]
        box_edit_panel = box_edit_panel.split("function ManualSplitEditor", 1)[0]
        cancel_index = box_edit_panel.index("취소")
        apply_index = box_edit_panel.index("(mutating ? '적용 중…' : '적용')")

        self.assertLess(cancel_index, apply_index)
        self.assertIn('className="btn primary"', box_edit_panel)
        self.assertIn('aria-keyshortcuts="Enter"', box_edit_panel)
        self.assertIn("onClick={onApply}", box_edit_panel)

        mutation_source = source.split("const mutateSession = useCallback(", 1)[1]
        mutation_source = mutation_source.split("const retryAiSession = useCallback", 1)[0]
        retry_source = source.split("const retryAiSession = useCallback(async (args) => {", 1)[1]
        retry_source = retry_source.split("const recognizeCurrentSession = useCallback", 1)[0]
        self.assertIn("materializeSessionForItems(session, items, fileName, boardColumns, layoutGapMode) || session", mutation_source)
        self.assertLess(mutation_source.index("await postRestore("), mutation_source.index("await postMutate(action, args);"))
        self.assertIn("materializeSessionForItems(session, items, fileName, boardColumns, layoutGapMode) || cloneSession(session)", retry_source)
        self.assertLess(retry_source.index("await postRestore(snapshotBefore);"), retry_source.index("const result = await postRetryAi(args"))

    def test_review_area_reset_uses_explicit_panel_apply_and_removes_two_way_split(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = board_document()
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]

        self.assertIn("originalBox: initialBox", review_stage)
        self.assertIn("box: initialBox", review_stage)
        self.assertIn("mode: 'crop'", review_stage)
        self.assertIn("if (!boxEdit.multi && !recognizeMode && reviewBoxesEqual(boxEdit.originalBox, boxEdit.box)) return;", review_stage)
        self.assertIn("pendingReviewSelectionProblemIdRef.current = sessionReviewSummary(nextSession).unresolvedReviewTargets?.[0]?.id || '';", review_stage)
        self.assertIn("preserveProblemIdentity: true", review_stage)
        self.assertIn("collapseToSingle: true", review_stage)
        self.assertIn("partial: true", review_stage)
        self.assertIn("problemIds: [boxEdit.problemId]", review_stage)
        self.assertIn("cropBox: boxEdit.box", review_stage)
        self.assertIn("mode={boxEdit.mode}", review_stage)
        self.assertIn("onModeChange={setBoxEditMode}", review_stage)
        self.assertIn("여러 후보가 나와도 하나의 경계로 합치며 번호와 순서를 유지합니다.", source)
        self.assertIn("AI 인식 적용 · ${summary.problemLabel}", source)
        self.assertIn("mergeRetryCandidateIntoCurrent(currentSnapshot", source)
        self.assertNotIn("onManualCropOutsideMouseDown", review_stage)
        self.assertNotIn("continueWithProblemId", review_stage)
        self.assertNotIn("const [splitTarget", review_stage)
        self.assertNotIn("const beginSplit", review_stage)
        self.assertNotIn("mutateSession?.('split'", review_stage)
        self.assertNotIn("2개로 나누기", review_stage)
        self.assertNotIn("두 문제로 나누기", review_stage)
        self.assertIn("새 영역 추가", review_stage)
        self.assertIn(".box-edit-layout.is-open", html)
        self.assertIn(".box-edit-panel", html)
        self.assertIn(".review-actionbar.is-selection{\n    align-items: flex-start;\n    flex-wrap: wrap;", html)
        self.assertIn(".review-actionbar.is-selection .review-actionbar-actions{\n    flex: 1 1 100%;", html)
        self.assertNotIn(".split-guide", html)

    def test_passage_area_reset_supports_ordered_multi_page_stitching(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = board_document()
        review_stage = source.split("function ReviewStage", 1)[1].split("// ─── LEFT:", 1)[0]

        self.assertIn("editableSourceSegmentsForProblem", source)
        self.assertIn("지문 여러 영역 이어붙이기", source)
        self.assertIn("페이지가 달라도 추가할 수 있습니다", source)
        self.assertIn("합치고 다음 검수", source)
        self.assertIn("serializeBoxEditSegments", source)
        self.assertIn("mutateSession?.('stitch-crop'", review_stage)
        self.assertIn("mode: 'draw-segment'", review_stage)
        self.assertIn("manual-passage-segment-", review_stage)
        self.assertIn("moveBoxEditSegment", review_stage)
        self.assertIn("boxEditSegmentValidation", source)
        self.assertIn("BoxEditStitchedPreview", source)
        self.assertIn("합친 결과", source)
        self.assertIn("reviewSourcePageLabel", source)
        self.assertIn("nudgeBoxEditSelection", review_stage)
        self.assertIn("data-page-id={page.id}", review_stage)
        self.assertIn("smoothScrollTo(wrap, targetTop, 180)", review_stage)
        self.assertIn("const passageOnlyReview = Boolean(", review_stage)
        self.assertIn("? '지문 없음'", review_stage)
        self.assertIn("const pageStatus = pageReviewTarget?.status", review_stage)
        self.assertIn("|| (passageOnlyReview && !pageRiskFlags.length", review_stage)
        self.assertIn("boxEdit?.multi || !reviewScopeActive", review_stage)
        self.assertIn("pageProblems", review_stage)
        self.assertIn(".multi-crop-segment-list", html)
        self.assertIn(".box-edit-stitched-preview", html)
        self.assertIn(".passage-segment-draft", html)
        self.assertNotIn("if (!manualSplit) return;\n    if (!evt.ctrlKey && !evt.metaKey) return;", review_stage)

    def test_review_stage_exposes_manual_split_bulk_crop_apply(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = board_document()
        bundle = (PROJECT_ROOT / "ui_prototype" / "app.bundle.js").read_text(encoding="utf-8")

        self.assertIn("function ManualSplitEditor", source)
        self.assertIn("수동 자르기", source)
        self.assertIn("자르기 적용", source)
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
        self.assertIn("const reviewZoom = clampReviewZoom(reviewUi?.zoom || 1)", source)
        self.assertIn("updateReviewUiField('zoom', value)", source)
        self.assertIn("onWheel={handleReviewWheel}", source)
        self.assertIn("review-zoom-controls", source)
        key_handler = source.split("const onKeyDown = (evt) => {", 1)[1]
        key_handler = key_handler.split("if (evt.key === 'Delete'", 1)[0]
        self.assertLess(key_handler.index("setManualSplitMode('select')"), key_handler.index("if (isFormControl) return;"))
        self.assertIn("0 0 0 9999px rgba(13,18,30,.20)", html)
        self.assertIn("선택 크기로 반복", source)
        self.assertNotIn("onManualSplitOutsideMouseDown", source)
        self.assertIn("panel-${panelSide}", source)
        self.assertIn("영역 패널을 ${panelSide === 'right' ? '왼쪽' : '오른쪽'}으로 이동", source)
        self.assertIn("manual-split-row-grip", source)
        self.assertIn("drop-${listDragState.position}", source)
        self.assertIn("새 문제 ${regionList.length}개", source)
        self.assertIn("클릭마다 새 영역 · Esc로 선택·이동", source)
        manual_actionbar = source.split('className="review-actionbar manual-split-actionbar"', 1)[1]
        manual_actionbar = manual_actionbar.split(") : selectedList.length === 0", 1)[0]
        self.assertNotIn("onClick={applyManualPageSplit}", manual_actionbar)
        self.assertEqual(source.count('aria-keyshortcuts="Enter"'), 2)
        self.assertIn("manual-split-box", bundle)
        self.assertIn("스탬프", bundle)

    def test_upload_method_is_chosen_beside_the_drop_zone(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = board_document()
        rail = source.split("function ItemsRail({", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("<SplitModePicker", rail)
        self.assertEqual(1, rail.count("processQueuedFiles("))
        self.assertNotIn('<label className="upload-intent"', rail)
        self.assertIn(".split-mode-control.compact", html)
        self.assertIn("normalizeSplitMode(splitMode)", rail)

    def test_undo_restores_server_snapshot_order_directly(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        undo_source = source.split("const undoMutation = useCallback(async () => {", 1)[1]
        undo_source = undo_source.split("  // Ctrl/Cmd+Z", 1)[0]

        self.assertIn("const restored = assertPlacementSaveSession(await postRestore(snapshot));", undo_source)
        self.assertLess(undo_source.index("assertPlacementSaveSession"), undo_source.index("setHistoryStack"))
        self.assertIn("applySession(restored);", undo_source)
        self.assertNotIn("adoptMutatedSession(restored", undo_source)

    def test_items_rail_keeps_step_and_source_on_one_line_without_status_text_chip(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = board_document()
        rail_item = source.split("function RailRow({", 1)[1].split("// ─── CENTER:", 1)[0]
        rail_item += source.split("function RailItemDetails({", 1)[1].split("function BoardTile({", 1)[0]

        self.assertIn('className="source-label"', rail_item)
        self.assertIn('className="icon-btn item-download-action"', rail_item)
        self.assertIn("이 자료 PNG 다운로드", rail_item)
        self.assertIn("onDownloadItemImage?.(it)", rail_item)
        self.assertNotIn("statusShortLabel", rail_item)
        self.assertNotIn("status-tag", rail_item)
        self.assertIn(".item .meta .sub .source-label", html)
        self.assertIn(".item .actions .item-download-action", html)
        self.assertIn("word-break: keep-all", html)

    def test_current_session_can_reextract_and_replace_shared_passages(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        request_source = source.split("async function postReextractSharedPassages", 1)[1]
        request_source = request_source.split("function formatApiError", 1)[0]
        confirm_source = source.split("if (review.kind === 'session-passage-reextract') {", 1)[1]
        confirm_source = confirm_source.split("} else if (review.kind === 'queue-recognition') {", 1)[0]
        rail_selection = source.split(
            '<div className="rail-selection-tools" role="group" aria-label="선택 문제 일괄 작업">',
            1,
        )[1].split("</div>", 1)[0]

        self.assertIn("현재 원본에서 공통 지문 다시 추출", source)
        self.assertIn("reuseSessionSources: true", request_source)
        self.assertIn("preview: true", request_source)
        self.assertIn("inputIntent: 'multi-problem'", request_source)
        self.assertIn("contentTarget: 'shared-passages'", request_source)
        self.assertIn("mergeReextractedSharedPassages(", confirm_source)
        self.assertNotIn("mergeSessions(", confirm_source)
        self.assertIn("문항에 잘못 붙인 공통 지문 표시는 문항으로 되돌립니다", source)
        self.assertNotIn("'shared-passage'", rail_selection)
        self.assertIn("독립 지문 이미지", source)


if __name__ == "__main__":
    unittest.main()
