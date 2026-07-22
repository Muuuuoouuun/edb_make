from __future__ import annotations

import subprocess
import unittest
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def run_node(script: str) -> None:
    subprocess.run(["node", "-e", script], cwd=PROJECT_ROOT, check=True)


class TestUiRuntimeDiagnostics(unittest.TestCase):
    def test_side_panel_exposes_four_edge_manual_crop_controls(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        side_panel = source.split("function SidePanel", 1)[1]
        side_panel = side_panel.split("function LoadingOverlay", 1)[0]

        self.assertIn("여백 자르기", side_panel)
        self.assertIn("mutateSession?.('crop'", side_panel)
        self.assertIn("cropDraft.leftRatio", side_panel)
        self.assertIn("cropDraft.rightRatio", side_panel)
        self.assertIn("cropDraft.topRatio", side_panel)
        self.assertIn("cropDraft.bottomRatio", side_panel)

    def test_side_panel_splits_material_details_from_placement_controls(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        side_panel = source.split("function SidePanel", 1)[1]
        side_panel = side_panel.split("function LoadingOverlay", 1)[0]
        preview_block = side_panel.split("<div className=\"item-preview\" ref={wrapRef}>", 1)[1]
        preview_block = preview_block.split("<div className=\"panel-section-hd\">", 1)[0]
        detail_block = side_panel.split("detail-settings-toggle", 1)[1]

        self.assertIn("const [advancedSettingsOpen, setAdvancedSettingsOpen] = useState(false)", side_panel)
        self.assertIn("setAdvancedSettingsOpen(true)", side_panel)
        self.assertIn("aria-expanded={advancedSettingsOpen}", side_panel)
        self.assertIn("상세 설정", side_panel)
        self.assertNotIn("className=\"ptools\"", preview_block)
        self.assertIn("className=\"ptools detail-tools\"", detail_block)
        self.assertIn("자르기 · 업스케일", detail_block)
        self.assertIn("tab === 'placement'", side_panel)
        self.assertIn("placement-position-control", side_panel)
        self.assertIn("const [cropPresetsOpen, setCropPresetsOpen] = useState(false)", side_panel)
        self.assertIn("자르기 프리셋", side_panel)
        self.assertIn("showItemConfirmBar", side_panel)
        self.assertIn("view === 'review'", side_panel)

    def test_side_panel_exposes_three_tabs_and_group_placement_rules(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        side_panel = source.split("function SidePanel", 1)[1]
        side_panel = side_panel.split("function LoadingOverlay", 1)[0]

        self.assertIn('role="tablist"', side_panel)
        self.assertEqual(side_panel.count('role="tab"'), 3)
        self.assertIn("자료 <span", side_panel)
        self.assertIn("자료 위치, 크기, 지문 묶음 배치", side_panel)
        self.assertIn("레이아웃, 색상, AI 인식 설정", side_panel)
        self.assertIn("지문 한 번만 배치", side_panel)
        self.assertIn("문항 함께 이동", side_panel)
        self.assertIn("공간 부족 시 다음 칠판", side_panel)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", html)

    def test_main_controls_are_compact_by_default(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        topbar = source.split("function TopBar", 1)[1]
        topbar = topbar.split("function ReviewStage", 1)[0]
        rail = source.split("function ItemsRail", 1)[1]
        rail = rail.split("function BoardStage", 1)[0]
        board_stage = source.split("function BoardStage", 1)[1]
        board_stage = board_stage.split("function downloadPublishSummary", 1)[0]
        side_panel = source.split("function SidePanel", 1)[1]
        side_panel = side_panel.split("function LoadingOverlay", 1)[0]

        self.assertIn("topbar-actions", topbar)
        self.assertIn("topbar-more-menu", topbar)
        self.assertIn("더보기", topbar)
        self.assertIn("파일 추가", rail)
        self.assertIn("hasSessionItems ? 'is-compact'", rail)
        self.assertIn("stage-fit-btn", board_stage)
        self.assertNotIn("title=\"자동 정렬\"", board_stage)
        self.assertIn("한 줄 ${columnCount}개", board_stage)
        self.assertIn("연속 이어붙임", board_stage)
        self.assertIn("칸 {p.columnIndex + 1}", board_stage)
        self.assertIn("한 줄 자료 수", side_panel)
        self.assertIn("너비 맞춤 아님 · 한 줄 배치 개수", side_panel)
        self.assertIn("배치 칸 가이드에 자동 정렬", side_panel)
        self.assertNotIn("열 수", side_panel)
        self.assertNotIn("열 가이드 자동 정렬", side_panel)
        self.assertIn(".drop-zone.is-compact", html)
        self.assertIn(".topbar-more-menu", html)

    def test_compact_controls_expose_hover_tooltips(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        topbar = source.split("function TopBar", 1)[1]
        topbar = topbar.split("function ReviewStage", 1)[0]
        side_panel = source.split("function SidePanel", 1)[1]
        side_panel = side_panel.split("function LoadingOverlay", 1)[0]

        self.assertIn("function TooltipLayer", source)
        self.assertIn("<TooltipLayer />", source)
        self.assertIn("document.addEventListener('pointerover'", source)
        self.assertIn("document.addEventListener('pointermove'", source)
        self.assertIn("document.addEventListener('focusin'", source)
        self.assertIn("data-tooltip", topbar)
        self.assertIn("보드 배치 화면으로 이동", topbar)
        self.assertIn("현재 배치로 EDB 파일 제작", topbar)
        self.assertIn("선택한 자료의 처리 방식과 세부 편집", side_panel)
        self.assertIn("여백 자르기와 업스케일 상세 설정", side_panel)
        self.assertIn("자료 위치, 크기, 지문 묶음 배치", side_panel)
        self.assertIn(".ui-tooltip", html)
        self.assertIn("position: fixed", html)

    def test_review_zoom_only_scales_problem_canvas(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("function ItemsRail", 1)[0]

        self.assertIn("function ReviewCanvasZoomShell", source)
        self.assertIn("centerReviewZoomScrollers", review_stage)
        self.assertIn("reviewWrapRef.current?.querySelectorAll?.('.review-canvas-scroll')", review_stage)
        self.assertIn("className=\"review-zoom-range\"", review_stage)
        self.assertIn("문제 이미지만 확대/축소", review_stage)
        self.assertIn("<ReviewCanvasZoomShell>", review_stage)
        self.assertIn(".review-page{\n    width: 100%;", html)
        self.assertIn(".review-canvas-zoom-shell", html)
        self.assertIn("width: calc(100% * var(--review-zoom, 1))", html)

    def test_placement_tab_exposes_scoped_dynamic_scale_controls(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        side_panel = source.split("function SidePanel", 1)[1]
        side_panel = side_panel.split("function LoadingOverlay", 1)[0]
        publish_panel = source.split("function PublishResultPanel", 1)[1]
        publish_panel = publish_panel.split("function SidePanel", 1)[0]

        self.assertIn("const maxScale = maxPlacementScaleRatio(item)", side_panel)
        self.assertIn("placementScalePercent", side_panel)
        self.assertIn("className=\"scale-range\"", side_panel)
        self.assertIn("placement-width-options", side_panel)
        self.assertIn("placementScope", side_panel)
        self.assertIn("--range-progress", side_panel)
        self.assertIn("너비 맞춤 이어붙임", side_panel)
        fit_width_flow = source.split("if (wantsFitWidth) {", 1)[1]
        fit_width_flow = fit_width_flow.split("} else if (Object.prototype.hasOwnProperty.call(patch || {}, 'scaleRatio'))", 1)[0]
        fit_width_helper = source.split("function fitWidthContinuousPageItem", 1)[1]
        fit_width_helper = fit_width_helper.split("function isContinuousPlacementItem", 1)[0]
        self.assertIn("return fitWidthContinuousPageItem(next, patch);", fit_width_flow)
        self.assertIn("PLACEMENT_FIT_WIDTH_SCALE_RATIO", source)
        board_stage = source.split("function BoardStage", 1)[1]
        board_stage = board_stage.split("function downloadPublishSummary", 1)[0]
        self.assertIn("contentW / PLACEMENT_FIT_WIDTH_SCALE_RATIO", board_stage)
        self.assertIn("next.inputIntent = 'page-as-is';", fit_width_helper)
        self.assertIn("next.placementMode = 'continuous-page-as-is';", fit_width_helper)
        self.assertIn("PLACEMENT_FIT_WIDTH_SCALE_RATIO", fit_width_helper)
        self.assertIn("heightPages * targetScale", fit_width_helper)
        self.assertIn("next.snappedNextStartYPages = Number((startPages + slotSpanPages).toFixed(6));", fit_width_helper)
        self.assertNotIn("snapUpPages(startPages + slotSpanPages)", fit_width_helper)
        self.assertIn("publish-result-panel ${open ? 'open' : 'is-collapsed'}", publish_panel)
        self.assertIn("제작 결과 펼치기", publish_panel)
        self.assertIn(".placement-scale-row input.scale-range", html)
        self.assertIn(".placement-width-options", html)
        self.assertIn(".placement-position-control", html)
        self.assertIn(".publish-result-panel.is-collapsed", html)

    def test_page_png_queue_register_applies_fit_width_page_flow(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        queue_source = source.split("const processQueuedFiles = useCallback(async (mode, targetKey = null) => {", 1)[1]
        queue_source = queue_source.split("const cancelRecognitionReview = useCallback", 1)[0]
        session_helper = source.split("function fitWidthPageAsIsSession", 1)[1]
        session_helper = session_helper.split("function mergeSessions", 1)[0]

        self.assertIn("const registeredSession = isManualSplit ? s : fitWidthPageAsIsSession(s, { fileName, boardColumns });", queue_source)
        self.assertIn("let sessionToApply = registeredSession;", queue_source)
        self.assertIn("const merged = mergeSessions(currentSnapshot, registeredSession, fileName, boardColumns);", queue_source)
        self.assertIn("} else if (!isManualSplit && sessionToApply !== s) {", queue_source)
        self.assertIn("sessionToApply = await postRestore(sessionToApply);", queue_source)
        self.assertIn("return fitWidthContinuousPageItem", session_helper)

    def test_reorder_reflows_saved_board_page_positions(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        self.assertIn("function reflowItemsForBoardOrder", source)
        materialize = source.split("function materializeSessionForItems", 1)[1]
        materialize = materialize.split("function mergeSessions", 1)[0]
        apply_state = source.split("function applyItemStateToProblem", 1)[1]
        apply_state = apply_state.split("function confirmedItemState", 1)[0]
        reorder_flow = source.split("const reorder = (fromId, toId", 1)[1]
        reorder_flow = reorder_flow.split("const removeItem", 1)[0]
        remove_flow = source.split("const removeItem = (id) =>", 1)[1]
        remove_flow = remove_flow.split("const addMockSample", 1)[0]

        self.assertIn("const reflowedItems = reflowItemsForBoardOrder(items, DEFAULT_SLOT_HEIGHT_PAGES, boardColumns);", materialize)
        self.assertIn("next.startYPages", apply_state)
        self.assertIn("next.snappedNextStartYPages", apply_state)
        self.assertIn("const nextItems = reflowItemsForBoardOrder(options?.resetPlacement ? resetItems : reordered, DEFAULT_SLOT_HEIGHT_PAGES, boardColumns);", reorder_flow)
        self.assertIn("materializeSessionForItems(session, nextItems, fileName, boardColumns)", reorder_flow)
        self.assertIn("setSession(nextSession)", reorder_flow)
        self.assertIn("postRestore(nextSession)", reorder_flow)
        self.assertIn("appendBoundedHistory(prev, snapshotBefore, UNDO_HISTORY_LIMIT)", reorder_flow)
        self.assertIn("setActiveId(fromId)", reorder_flow)
        self.assertIn("if (session)", remove_flow)
        self.assertIn("void mutateSession('exclude', { problemId: id });", remove_flow)
        self.assertIn("reflowItemsForBoardOrder(items.filter", remove_flow)

    def test_undo_keeps_the_current_workspace_and_active_problem(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        undo_flow = source.split("const undoMutation = useCallback(async () => {", 1)[1]
        undo_flow = undo_flow.split("// Ctrl/Cmd+Z", 1)[0]
        self.assertIn("const viewBeforeUndo = view;", undo_flow)
        self.assertIn("const activeBeforeUndo = activeId;", undo_flow)
        self.assertIn("setView(viewBeforeUndo);", undo_flow)
        self.assertIn("setActiveId(activeBeforeUndo);", undo_flow)

    def test_reorder_motion_avoids_drag_frame_wide_rerenders(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        items_rail = source.split("function ItemsRail({", 1)[1].split("function BoardStage({", 1)[0]
        board_stage = source.split("function BoardStage({", 1)[1].split("// ─── RIGHT:", 1)[0]

        self.assertIn("}, [itemOrderSignature]);", items_rail)
        self.assertIn("}, [boardOrderSignature, pageH, contentW, columnCount]);", board_stage)
        self.assertIn("setCurrentPage(prev => prev === nextPage ? prev : nextPage);", board_stage)
        self.assertIn("nearestPlacementIndex(layout.positions, c.scrollTop + 24)", board_stage)
        self.assertNotIn("setScrollTop(scroll.scrollTop)", board_stage)
        self.assertNotIn("layout.positions\n      .map", board_stage)
        self.assertIn("top: el.offsetTop, height: el.offsetHeight", items_rail)
        self.assertNotIn("rail.querySelectorAll('.item[data-item-id]')", items_rail)

    def test_left_rail_filters_questions_and_only_separate_shared_passages(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        board = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        items_rail = source.split("function ItemsRail({", 1)[1].split("function BoardStage({", 1)[0]

        self.assertIn('aria-label="자료 모아보기"', items_rail)
        self.assertIn("['questions', '문항', materialCounts.questions]", items_rail)
        self.assertIn("['passages', '공통 지문', materialCounts.passages]", items_rail)
        self.assertIn("items.filter(item => !isPassageFragmentProblem(item)).length", items_rail)
        self.assertIn("items.filter(isPassageFragmentProblem).length", items_rail)
        self.assertIn("모아보기 중 · 순서 변경은 전체 보기에서", items_rail)
        self.assertIn("if (filterActive)", items_rail)
        self.assertIn(".material-filter", board)

    def test_item_editor_can_correct_question_and_shared_passage_classification(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        side_panel = source.split("function SidePanel", 1)[1].split("function LoadingOverlay", 1)[0]
        mutation = source.split("const mutateSession = useCallback(async (action, args) => {", 1)[1]
        mutation = mutation.split("const retryAiSession", 1)[0]

        self.assertIn('className="item-classification"', side_panel)
        self.assertIn('role="radiogroup" aria-label="선택 자료 분류"', side_panel)
        self.assertIn("classification: 'question'", side_panel)
        self.assertIn("classification: 'shared-passage'", side_panel)
        self.assertIn("공통 지문은 여러 문항이 함께 쓰는 별도 지문만 지정", side_panel)
        self.assertIn("action === 'classify' ? '자료 분류를 저장하는 중…'", mutation)
        self.assertIn("action === 'classify' ? '자료 분류를 변경했어요'", mutation)

    def test_hangul_runtime_helpers_include_hwp_renderer(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        summary_helper = source.split("function hangulRuntimeSummary", 1)[1]
        summary_helper = summary_helper.split("function hangulRuntimeToolRows", 1)[0]
        tool_rows = source.split("function hangulRuntimeToolRows", 1)[1]
        tool_rows = tool_rows.split("function listUnique", 1)[0]

        self.assertIn("hangul.hwpRenderers", summary_helper)
        self.assertIn("렌더 ${rendererCount}", summary_helper)
        self.assertIn("HWP 렌더", tool_rows)
        self.assertIn("hangul.hwpRenderers || []", tool_rows)

    def test_board_settings_exposes_app_update_controls(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        side_panel = source.split("function SidePanel", 1)[1]
        side_panel = side_panel.split("function LoadingOverlay", 1)[0]

        self.assertIn("앱 업데이트", side_panel)
        self.assertIn("업데이트 확인", side_panel)
        self.assertIn("다운로드 열기", side_panel)
        self.assertIn("const updateDownloadUrl = updateInfo?.downloadUrl || updateInfo?.latest?.downloadUrl || ''", side_panel)
        self.assertIn("updateStatus === 'invalid_feed'", side_panel)
        self.assertIn("피드 오류", side_panel)
        self.assertIn("disabled={updateBusy || !updateDownloadUrl}", side_panel)
        self.assertIn("if (updateBusy)", source)
        self.assertIn("fetch('/api/app/update')", source)
        self.assertIn("fetch('/api/system/open-url'", source)

    def test_shipped_bundle_contains_app_update_controls(self) -> None:
        bundle = (PROJECT_ROOT / "ui_prototype" / "app.bundle.js").read_text(encoding="utf-8")
        compact_bundle = re.sub(r"\s+", "", bundle)

        self.assertIn("updateBusy||!updateDownloadUrl", compact_bundle)
        self.assertIn("invalid_feed", bundle)
        self.assertIn("피드 오류", bundle)
        self.assertIn("fetch('/api/app/update')", bundle)
        self.assertIn("fetch('/api/system/open-url'", bundle)

    def test_review_summary_surfaces_hwp_cache_hits(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("HWP 캐시", review_stage)
        self.assertIn("hwpCacheHitPageCount", review_stage)
        self.assertIn("hwpCacheHitPageCount", summary_helper)
        self.assertIn("hwpRendererCacheHitCount", summary_helper)
        self.assertIn("hwpNormalizedCacheHitCount", summary_helper)

    def test_review_summary_surfaces_ai_stage_counts(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("reviewSummary.aiStages.map", review_stage)
        self.assertIn("aiStageChipText(stage)", review_stage)
        self.assertIn("aiStageTooltip(stage)", review_stage)
        self.assertIn("normalizeAiStageSummaries(session)", summary_helper)
        self.assertIn("aiStages", summary_helper)

    def test_review_summary_surfaces_hwp_segmentation_risk_counts(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("HWP 과분할", review_stage)
        self.assertIn("hwpOversegmentationCount", review_stage)
        self.assertIn("hwpProblemCountMismatchCount", summary_helper)
        self.assertIn("hwp_problem_count_mismatch", summary_helper)
        self.assertIn("hwpOversegmentationCount", summary_helper)
        self.assertIn("hwp_oversegmentation", summary_helper)

    def test_review_summary_surfaces_duplicate_problem_number_groups(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]
        risk_meta = source.split("const RISK_FLAG_META = {", 1)[1]
        risk_meta = risk_meta.split("};", 1)[0]

        self.assertIn("중복 번호", review_stage)
        self.assertIn("duplicateProblemNumberGroups", review_stage)
        self.assertIn("duplicateProblemNumberGroups", summary_helper)
        self.assertIn("duplicate_problem_number_groups", summary_helper)
        self.assertIn("duplicateProblemNumberLabel", summary_helper)
        self.assertIn("duplicate_problem_number", risk_meta)
        self.assertIn("중복 번호", risk_meta)

    def test_review_summary_surfaces_source_problem_overlap_groups(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]
        risk_meta = source.split("const RISK_FLAG_META = {", 1)[1]
        risk_meta = risk_meta.split("};", 1)[0]

        self.assertIn("문항 영역 겹침", review_stage)
        self.assertIn("sourceProblemOverlapGroups", review_stage)
        self.assertIn("sourceProblemOverlapGroups", summary_helper)
        self.assertIn("source_problem_overlap_groups", summary_helper)
        self.assertIn("sourceProblemOverlapLabel", summary_helper)
        self.assertIn("source_problem_bbox_overlap", risk_meta)
        self.assertIn("문항 영역 겹침", risk_meta)

    def test_app_labels_passage_missing_child_preflight_issue(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        risk_meta = source.split("const RISK_FLAG_META = {", 1)[1]
        risk_meta = risk_meta.split("};", 1)[0]
        preflight_meta = source.split("const CLASSIN_PREFLIGHT_ISSUE_LABELS = {", 1)[1]
        preflight_meta = preflight_meta.split("};", 1)[0]

        self.assertIn("passage_missing_child_questions", risk_meta)
        self.assertIn("문항 누락", risk_meta)
        self.assertIn("passage_missing_child_questions", preflight_meta)
        self.assertIn("문항 누락", preflight_meta)

    def test_review_summary_surfaces_passage_group_source_reuse_groups(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("지문 겹침", review_stage)
        self.assertIn("passageGroupSourceReuseGroups", review_stage)
        self.assertIn("passageGroupSourceReuseDetailLabel", review_stage)
        self.assertIn("passage_group_source_reuse", review_stage)
        self.assertIn("passageGroupSourceReuseGroups", summary_helper)
        self.assertIn("passage_group_source_reuse_groups", summary_helper)
        self.assertIn("passageGroupSourceReuseLabel", summary_helper)
        self.assertIn("passageGroupSourceReuseDetailLabel", summary_helper)

        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('function normalizePublishSummary');
            if (start < 0 || end < 0) throw new Error('review summary helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            const summary = sandbox.sessionReviewSummary({
              passageGroupSourceReuseGroups: [
                {
                  passageGroupId: 'hwp-text-passage-31-34',
                  sourcePageId: 'page-004',
                  problemIds: ['p31', 'p32'],
                  overlapAreaRatio: 0.92,
                },
              ],
              passageGroupSourceReuseGroupCount: 1,
              problems: [
                {
                  id: 'p31',
                  reviewStatus: 'check_needed',
                  riskFlags: ['passage_group_source_reuse'],
                  bbox: { width: 10, height: 10 },
                },
                {
                  id: 'p32',
                  reviewStatus: 'check_needed',
                  riskFlags: ['passage_group_source_reuse'],
                  bbox: { width: 10, height: 10 },
                },
              ],
              pages: [{ id: 'page-004', problemIds: ['p31', 'p32'], riskFlags: [] }],
            });
            if (summary.passageGroupSourceReuseGroups.length !== 1) {
              throw new Error(`expected one source reuse group, got ${summary.passageGroupSourceReuseGroups.length}`);
            }
            if (summary.passageGroupSourceReuseGroupCount !== 1) {
              throw new Error(`expected source reuse count 1, got ${summary.passageGroupSourceReuseGroupCount}`);
            }
            if (summary.passageGroupSourceReuseLabel !== '지문 겹침 1건') {
              throw new Error(`expected short passage group label, got ${summary.passageGroupSourceReuseLabel}`);
            }
            if (!summary.passageGroupSourceReuseDetailLabel.includes('hwp-text-passage-31-34')) {
              throw new Error(`expected passage group detail, got ${summary.passageGroupSourceReuseDetailLabel}`);
            }
            if (!summary.passageGroupSourceReuseDetailLabel.includes('92%')) {
              throw new Error(`expected overlap percent detail, got ${summary.passageGroupSourceReuseDetailLabel}`);
            }
            """
        )

    def test_review_summary_surfaces_passage_groups(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        board_html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]
        passage_id_helper = source.split("function passageGroupIdFor(problem)", 1)[1]
        passage_id_helper = passage_id_helper.split("function isPassageProblem(problem)", 1)[0]
        passage_helper = source.split("function collectPassageGroupSummary(session)", 1)[1]
        passage_helper = passage_helper.split("function sessionReviewSummary(session)", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("'passage', '지문'", review_stage)
        self.assertIn("지문 묶음", review_stage)
        self.assertIn("이어짐", review_stage)
        self.assertIn("passageGroupCount", review_stage)
        self.assertIn("passageContinuationBlockCount", review_stage)
        self.assertIn("review-bbox-passage", review_stage)
        self.assertIn("review-bbox-passage-tag", review_stage)
        self.assertIn(".review-bbox-passage", board_html)
        self.assertIn("passageGroupCount", summary_helper)
        self.assertIn("passageProblemCount", summary_helper)
        self.assertIn("passageContinuationBlockCount", summary_helper)
        self.assertIn("fragmentProblemCount", passage_helper)
        self.assertIn("problemNumbers", passage_helper)
        self.assertIn("passageRole", passage_helper)
        self.assertIn("passageGroupIdFor(problem)", passage_helper)
        self.assertIn("passagePreQuestionContinuationBlockIds", passage_helper)
        self.assertIn("passage_pre_question_continuation_block_ids", passage_helper)
        self.assertIn("passageGroupId", passage_id_helper)
        self.assertIn("passage_group_id", passage_id_helper)
        risk_meta = source.split("const RISK_FLAG_META = {", 1)[1]
        risk_meta = risk_meta.split("};", 1)[0]
        self.assertIn("passage_cross_page_merge_check", risk_meta)
        self.assertIn("병합 확인", risk_meta)

    def test_board_uses_prebuilt_bundle_without_browser_babel(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        board_html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("app.bundle.js?v=frontend-bundle-", board_html)
        self.assertNotIn("app.js?v=", board_html)
        self.assertNotIn("vendor/babel.min.js", board_html)
        self.assertNotIn('type="text/babel"', board_html)
        self.assertIn("EDB_REPORT_RUNTIME_ERROR", board_html)
        self.assertIn("class AppErrorBoundary", source)
        self.assertIn("requiredWindowHelper(PUBLISH_GUARD", source)

    def test_review_summary_surfaces_passage_review_queue(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        queue_helper = source.split("function collectPassageReviewSummary(session", 1)[1]
        queue_helper = queue_helper.split("function sessionReviewSummary(session)", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("지문 확인", review_stage)
        self.assertIn("passageReviewLabel", review_stage)
        self.assertIn("passageReviewItems", queue_helper)
        self.assertIn("passage_review_items", queue_helper)
        self.assertIn("actionableProblemIds", queue_helper)
        self.assertIn("unresolvedPassageReviewItems", queue_helper)
        self.assertIn("passageReviewItemCount", queue_helper)
        self.assertIn("crossPagePassageReviewItemCount", queue_helper)
        self.assertIn("passageReviewItems", summary_helper)
        self.assertIn("passageReviewLabel", summary_helper)
        self.assertIn("passageReviewReasonLabel", review_stage)
        self.assertIn("passageReviewReasonLabel", queue_helper)
        self.assertIn("passageReviewReasonLabel", summary_helper)

    def test_review_summary_passage_review_chip_filters_only_queue_items(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("function ReviewStage", 1)[1]
        review_stage = review_stage.split("// ─── LEFT:", 1)[0]
        queue_helper = source.split("function collectPassageReviewSummary(session", 1)[1]
        queue_helper = queue_helper.split("function sessionReviewSummary(session)", 1)[0]

        self.assertIn("passageReviewProblemIds", queue_helper)
        self.assertIn("passageReviewItemProblemIds", queue_helper)
        self.assertIn("reviewFilter === 'passage-review'", review_stage)
        self.assertIn("'passage-review' ? 'all' : 'passage-review'", review_stage)
        self.assertIn("passageReviewProblemIds", review_stage)
        self.assertIn("problemMatchesReviewFilter(problem, reviewFilter, { passageReviewProblemIds })", review_stage)

    def test_review_summary_removes_confirmed_passage_review_queue_items(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('function normalizePublishSummary');
            if (start < 0 || end < 0) throw new Error('review summary helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            const session = {
              review_summary: {
                actionableNeedsReviewCount: 2,
                reviewStatusCounts: { all: 2, normal: 0, check_needed: 2, failed: 0 },
                needsReviewCount: 2,
                riskFlagCounts: { passage_cross_page_merge_check: 2 },
              },
              passageReviewItemCount: 1,
              crossPagePassageReviewItemCount: 1,
              passageReviewItems: [
                {
                  numberLabel: '31-32',
                  problemIds: ['p31', 'p32'],
                  continuesAcrossPages: true,
                  reviewReasonCodes: ['cross_page_passage_group', 'passage_missing_child_questions'],
                },
              ],
              problems: [
                {
                  id: 'p31',
                  reviewStatus: 'normal',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
                {
                  id: 'p32',
                  reviewStatus: 'normal',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
              ],
              pages: [{ id: 'page-1', problemIds: ['p31', 'p32'], riskFlags: [] }],
            };
            const summary = sandbox.sessionReviewSummary(session);
            if (summary.reviewStatusCounts.check_needed !== 0) {
              throw new Error(`expected confirmed check_needed count 0, got ${summary.reviewStatusCounts.check_needed}`);
            }
            if (summary.needsReviewCount !== 0) {
              throw new Error(`expected confirmed needs review count 0, got ${summary.needsReviewCount}`);
            }
            if (summary.actionableNeedsReviewCount !== 0) {
              throw new Error(`expected confirmed actionable count 0, got ${summary.actionableNeedsReviewCount}`);
            }
            if (summary.passageReviewItemCount !== 0) {
              throw new Error(`expected confirmed passage review count 0, got ${summary.passageReviewItemCount}`);
            }
            if (summary.crossPagePassageReviewItemCount !== 0) {
              throw new Error(`expected confirmed cross-page passage review count 0, got ${summary.crossPagePassageReviewItemCount}`);
            }
            if (summary.passageReviewProblemIds.length !== 0) {
              throw new Error(`expected no confirmed passage review problem ids, got ${summary.passageReviewProblemIds.join(',')}`);
            }
            if (summary.passageReviewLabel !== '') {
              throw new Error(`expected empty confirmed passage review label, got ${summary.passageReviewLabel}`);
            }
            """
        )

    def test_review_summary_keeps_check_needed_passage_review_queue_without_flags(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('function normalizePublishSummary');
            if (start < 0 || end < 0) throw new Error('review summary helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            const session = {
              review_summary: {
                actionableNeedsReviewCount: 0,
                reviewStatusCounts: { all: 2, normal: 1, check_needed: 1, failed: 0 },
                needsReviewCount: 1,
                riskFlagCounts: {},
              },
              passageReviewItemCount: 1,
              crossPagePassageReviewItemCount: 1,
              passageReviewItems: [
                {
                  numberLabel: '31-32',
                  problemIds: ['p31', 'p32'],
                  continuesAcrossPages: true,
                  reviewReasonCodes: ['cross_page_passage_group', 'passage_missing_child_questions'],
                },
              ],
              problems: [
                {
                  id: 'p31',
                  reviewStatus: 'check_needed',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
                {
                  id: 'p32',
                  reviewStatus: 'normal',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
              ],
              pages: [{ id: 'page-1', problemIds: ['p31', 'p32'], riskFlags: [] }],
            };
            const summary = sandbox.sessionReviewSummary(session);
            if (summary.passageReviewItemCount !== 1) {
              throw new Error(`expected unresolved passage review count 1, got ${summary.passageReviewItemCount}`);
            }
            if (summary.crossPagePassageReviewItemCount !== 1) {
              throw new Error(`expected unresolved cross-page count 1, got ${summary.crossPagePassageReviewItemCount}`);
            }
            if (summary.passageReviewProblemIds.join(',') !== 'p31,p32') {
              throw new Error(`expected passage ids p31,p32, got ${summary.passageReviewProblemIds.join(',')}`);
            }
            if (!summary.passageReviewLabel.includes('지문 확인 1')) {
              throw new Error(`expected passage review label, got ${summary.passageReviewLabel}`);
            }
            if (summary.passageReviewReasonLabel !== '페이지 이어짐, 문항 누락') {
              throw new Error(`expected passage reason label, got ${summary.passageReviewReasonLabel}`);
            }
            """
        )

    def test_publish_guard_warns_for_unresolved_passage_review_queue_without_flags(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        publish_flow = source.split("const onPublish = async () =>", 1)[1]
        publish_flow = publish_flow.split("const placements = Object.fromEntries", 1)[0]
        self.assertIn("publishReviewWarningMessage(sessionForPublish, publishReviewSummary)", publish_flow)

        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('function normalizePublishSummary');
            if (start < 0 || end < 0) throw new Error('review summary helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            if (typeof sandbox.publishReviewWarningMessage !== 'function') {
              throw new Error('publishReviewWarningMessage missing');
            }
            const session = {
              passageReviewItemCount: 1,
              crossPagePassageReviewItemCount: 1,
              passageReviewItems: [
                {
                  numberLabel: '31-32',
                  problemIds: ['p31', 'p32'],
                  continuesAcrossPages: true,
                },
              ],
              problems: [
                {
                  id: 'p31',
                  reviewStatus: 'check_needed',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
                {
                  id: 'p32',
                  reviewStatus: 'normal',
                  riskFlags: [],
                  bbox: { width: 10, height: 10 },
                },
              ],
              pages: [{ id: 'page-1', problemIds: ['p31', 'p32'], riskFlags: [] }],
            };
            const summary = sandbox.sessionReviewSummary(session);
            const warning = sandbox.publishReviewWarningMessage(session, summary);
            if (!warning) throw new Error('expected unresolved passage review warning');
            if (!warning.message.includes('지문 확인 1')) {
              throw new Error(`expected passage warning line, got ${warning.message}`);
            }
            if (!warning.cancelToast.includes('지문 확인 항목')) {
              throw new Error(`expected passage cancel toast, got ${warning.cancelToast}`);
            }
            if (warning.reviewFilter !== 'passage-review') {
              throw new Error(`expected passage-review focus filter, got ${warning.reviewFilter}`);
            }
            """
        )

    def test_publish_blocked_target_focuses_preflight_problem_ids(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('const NON_ACTIONABLE_RISK_FLAGS');
            if (start < 0 || end < 0) throw new Error('publish preflight helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            if (typeof sandbox.publishBlockedTarget !== 'function') {
              throw new Error('publishBlockedTarget missing');
            }
            const overlapTarget = sandbox.publishBlockedTarget({
              issueTypes: ['source_problem_bbox_overlap'],
              classinPreflight: {
                issues: [
                  {
                    type: 'source_problem_bbox_overlap',
                    problemId: 'p21',
                    nextProblemId: 'p22',
                  },
                ],
              },
            });
            if (overlapTarget.view !== 'review') {
              throw new Error(`expected review target, got ${overlapTarget.view}`);
            }
            if (overlapTarget.reviewFocus?.filter !== 'all') {
              throw new Error(`expected all filter, got ${overlapTarget.reviewFocus?.filter}`);
            }
            if (overlapTarget.reviewFocus?.problemIds?.join(',') !== 'p21,p22') {
              throw new Error(`expected p21,p22 focus, got ${overlapTarget.reviewFocus?.problemIds}`);
            }
            const duplicateTarget = sandbox.publishBlockedTarget({
              issueTypes: ['duplicate_problem_number'],
              classinPreflight: {
                issues: [
                  {
                    type: 'duplicate_problem_number',
                    problemIds: ['p7-a', 'p7-b'],
                  },
                ],
              },
            });
            if (duplicateTarget.reviewFocus?.problemIds?.join(',') !== 'p7-a,p7-b') {
              throw new Error(`expected duplicate ids focus, got ${duplicateTarget.reviewFocus?.problemIds}`);
            }
            const passageTarget = sandbox.publishBlockedTarget({
              issueTypes: ['passage_review_queue_remaining'],
              blockingProblemIds: ['p31', 'p32', 'p31-fragment'],
              classinPreflight: {
                issues: [
                  {
                    type: 'passage_review_queue_remaining',
                    problemIds: ['p31', 'p32', 'p31-fragment'],
                  },
                ],
              },
            });
            if (passageTarget.reviewFocus?.filter !== 'passage-review') {
              throw new Error(`expected passage-review filter, got ${passageTarget.reviewFocus?.filter}`);
            }
            if (passageTarget.reviewFocus?.problemIds?.join(',') !== 'p31,p32,p31-fragment') {
              throw new Error(`expected passage ids focus, got ${passageTarget.reviewFocus?.problemIds}`);
            }
            const missingChildTarget = sandbox.publishBlockedTarget({
              issueTypes: ['passage_missing_child_questions'],
              classinPreflight: {
                issues: [
                  {
                    type: 'passage_missing_child_questions',
                    problemIds: ['p31', 'p32', 'p34'],
                    missingChildProblemNumbers: [33],
                  },
                ],
              },
            });
            if (missingChildTarget.reviewFocus?.filter !== 'passage-review') {
              throw new Error(`expected missing child passage-review filter, got ${missingChildTarget.reviewFocus?.filter}`);
            }
            if (missingChildTarget.reviewFocus?.problemIds?.join(',') !== 'p31,p32,p34') {
              throw new Error(`expected missing child focus ids, got ${missingChildTarget.reviewFocus?.problemIds}`);
            }
            const missingChildBlock = sandbox.normalizePublishPreflightBlock({
              errorKind: 'publish_preflight_blocked',
              classinPreflight: {
                status: 'blocked',
                passed: false,
                issueCount: 1,
                issues: [{ type: 'passage_missing_child_questions', problemIds: ['p31', 'p32', 'p34'] }],
              },
            });
            if (!missingChildBlock.toastLabel.includes('제작 전 확인')) {
              throw new Error(`expected generic fallback message, got ${missingChildBlock.toastLabel}`);
            }
            if (missingChildBlock.toastLabel.includes('겹침/중복')) {
              throw new Error(`fallback message should not narrow missing child blocks: ${missingChildBlock.toastLabel}`);
            }
            const boardTarget = sandbox.publishBlockedTarget({ issueTypes: ['board_placement_overlap'] });
            if (boardTarget.view !== 'board' || boardTarget.reviewFocus !== null) {
              throw new Error(`expected board-only target, got ${JSON.stringify(boardTarget)}`);
            }
            """
        )

    def test_publish_preflight_page_chrome_issues_mark_problem_flags(self) -> None:
        run_node(
            r"""
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync('./ui_prototype/app.jsx', 'utf8');
            const start = source.indexOf('const REVIEW_STATUS_META =');
            const end = source.indexOf('const NON_ACTIONABLE_RISK_FLAGS');
            if (start < 0 || end < 0) throw new Error('publish preflight helper bounds not found');
            const sandbox = {};
            sandbox.globalThis = sandbox;
            vm.runInNewContext(source.slice(start, end), sandbox);
            if (typeof sandbox.applyPublishPreflightIssuesToSession !== 'function') {
              throw new Error('applyPublishPreflightIssuesToSession missing');
            }
            const session = {
              problems: [
                { id: 'p1', reviewStatus: 'normal', riskFlags: [], bbox: { width: 10, height: 10 } },
                { id: 'p2', reviewStatus: 'normal', riskFlags: [], bbox: { width: 10, height: 10 } },
                { id: 'p3', reviewStatus: 'normal', riskFlags: [], bbox: { width: 10, height: 10 } },
              ],
              pages: [{ id: 'page-1', problemIds: ['p1', 'p2', 'p3'], riskFlags: [] }],
            };
            const blocked = {
              issueTypes: ['step3_page_chrome_artifact'],
              classinPreflight: {
                issues: [
                  { type: 'step3_page_chrome_artifact', problemIds: ['p2'] },
                ],
              },
            };
            const marked = sandbox.applyPublishPreflightIssuesToSession(session, blocked);
            const p1 = marked.problems.find(problem => problem.id === 'p1');
            const p2 = marked.problems.find(problem => problem.id === 'p2');
            if (p1.reviewStatus !== 'normal' || p1.riskFlags.length !== 0) {
              throw new Error(`unexpected p1 mutation: ${JSON.stringify(p1)}`);
            }
            if (p2.reviewStatus !== 'failed') {
              throw new Error(`expected p2 failed, got ${p2.reviewStatus}`);
            }
            if (!p2.riskFlags.includes('step3_page_chrome_artifact')) {
              throw new Error(`expected p2 page chrome flag, got ${p2.riskFlags}`);
            }
            if (!p2.publishPreflightIssues?.some(issue => issue.type === 'step3_page_chrome_artifact')) {
              throw new Error(`expected p2 publish preflight details, got ${JSON.stringify(p2.publishPreflightIssues)}`);
            }
            if (!sandbox.hasPageChromeArtifactFlag(p2)) {
              throw new Error('expected page chrome helper to recognize marked problem');
            }
            const target = sandbox.publishBlockedTarget(blocked);
            if (target.view !== 'review') {
              throw new Error(`expected review target, got ${target.view}`);
            }
            if (target.reviewFocus?.problemIds?.join(',') !== 'p2') {
              throw new Error(`expected p2 focus, got ${target.reviewFocus?.problemIds}`);
            }
            const toast = sandbox.pageChromePreflightBlockToast(blocked);
            if (!toast.includes('3단계 이미지') || !toast.includes('빨간 문제')) {
              throw new Error(`expected page chrome toast, got ${toast}`);
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
