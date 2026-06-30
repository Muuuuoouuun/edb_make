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

    def test_side_panel_keeps_editor_controls_collapsed_behind_detail_settings(self) -> None:
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
        self.assertIn("이동 · 자르기 · 확대 · 업스케일", detail_block)
        self.assertIn("const [cropPresetsOpen, setCropPresetsOpen] = useState(false)", side_panel)
        self.assertIn("자르기 프리셋", side_panel)
        self.assertIn("showItemConfirmBar", side_panel)
        self.assertIn("view === 'review'", side_panel)

    def test_main_controls_are_compact_by_default(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")
        topbar = source.split("function TopBar", 1)[1]
        topbar = topbar.split("function ReviewStage", 1)[0]
        rail = source.split("function ItemsRail", 1)[1]
        rail = rail.split("function BoardStage", 1)[0]
        board_stage = source.split("function BoardStage", 1)[1]
        board_stage = board_stage.split("function downloadPublishSummary", 1)[0]

        self.assertIn("topbar-actions", topbar)
        self.assertIn("topbar-more-menu", topbar)
        self.assertIn("더보기", topbar)
        self.assertIn("파일 추가", rail)
        self.assertIn("hasSessionItems ? 'is-compact'", rail)
        self.assertIn("stage-fit-btn", board_stage)
        self.assertNotIn("title=\"자동 정렬\"", board_stage)
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
        self.assertIn("위치 이동, 여백 자르기, 확대, 업스케일 상세 설정", side_panel)
        self.assertIn(".ui-tooltip", html)
        self.assertIn("position: fixed", html)

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

        self.assertIn("const reflowedItems = reflowItemsForBoardOrder(items)", materialize)
        self.assertIn("next.startYPages", apply_state)
        self.assertIn("next.snappedNextStartYPages", apply_state)
        self.assertIn("const nextItems = reflowItemsForBoardOrder(options?.resetPlacement ? resetItems : reordered)", reorder_flow)
        self.assertIn("materializeSessionForItems(session, nextItems, fileName)", reorder_flow)
        self.assertIn("setSession(nextSession)", reorder_flow)
        self.assertIn("postRestore(nextSession)", reorder_flow)
        self.assertIn("reflowItemsForBoardOrder(items.filter", remove_flow)

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
        self.assertIn("disabled={updateBusy || !updateDownloadUrl}", side_panel)
        self.assertIn("if (updateBusy)", source)
        self.assertIn("fetch('/api/app/update')", source)
        self.assertIn("fetch('/api/system/open-url'", source)

    def test_shipped_bundle_contains_app_update_controls(self) -> None:
        bundle = (PROJECT_ROOT / "ui_prototype" / "app.bundle.js").read_text(encoding="utf-8")
        compact_bundle = re.sub(r"\s+", "", bundle)

        self.assertIn("updateBusy||!updateDownloadUrl", compact_bundle)
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

        self.assertIn("원본 겹침", review_stage)
        self.assertIn("sourceProblemOverlapGroups", review_stage)
        self.assertIn("sourceProblemOverlapGroups", summary_helper)
        self.assertIn("source_problem_overlap_groups", summary_helper)
        self.assertIn("sourceProblemOverlapLabel", summary_helper)
        self.assertIn("source_problem_bbox_overlap", risk_meta)
        self.assertIn("원본 영역 겹침", risk_meta)

    def test_app_labels_passage_missing_child_preflight_issue(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        risk_meta = source.split("const RISK_FLAG_META = {", 1)[1]
        risk_meta = risk_meta.split("};", 1)[0]
        preflight_meta = source.split("const CLASSIN_PREFLIGHT_ISSUE_LABELS = {", 1)[1]
        preflight_meta = preflight_meta.split("};", 1)[0]

        self.assertIn("passage_missing_child_questions", risk_meta)
        self.assertIn("지문 하위 문항 누락", risk_meta)
        self.assertIn("passage_missing_child_questions", preflight_meta)
        self.assertIn("지문 하위 문항 누락", preflight_meta)

    def test_review_summary_surfaces_passage_group_source_reuse_groups(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        review_stage = source.split("<span className=\"review-summary-title\">검수 요약</span>", 1)[1]
        review_stage = review_stage.split("{reviewSummary.warningPreview &&", 1)[0]
        summary_helper = source.split("function sessionReviewSummary(session)", 1)[1]
        summary_helper = summary_helper.split("function normalizePublishSummary", 1)[0]

        self.assertIn("지문 원본 중복", review_stage)
        self.assertIn("passageGroupSourceReuseGroups", review_stage)
        self.assertIn("passage_group_source_reuse", review_stage)
        self.assertIn("passageGroupSourceReuseGroups", summary_helper)
        self.assertIn("passage_group_source_reuse_groups", summary_helper)
        self.assertIn("passageGroupSourceReuseLabel", summary_helper)

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
            if (!summary.passageGroupSourceReuseLabel.includes('hwp-text-passage-31-34')) {
              throw new Error(`expected passage group label, got ${summary.passageGroupSourceReuseLabel}`);
            }
            if (!summary.passageGroupSourceReuseLabel.includes('92%')) {
              throw new Error(`expected overlap percent label, got ${summary.passageGroupSourceReuseLabel}`);
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

        self.assertIn("'passage', '긴 지문'", review_stage)
        self.assertIn("긴 지문 그룹", review_stage)
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
        self.assertIn("긴 지문 병합 확인", risk_meta)

    def test_board_uses_prebuilt_bundle_without_browser_babel(self) -> None:
        source = (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
        board_html = (PROJECT_ROOT / "ui_prototype" / "board.html").read_text(encoding="utf-8")

        self.assertIn("app.bundle.js?v=frontend-bundle-20260630-undo-order", board_html)
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

        self.assertIn("긴 지문 검수", review_stage)
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
            if (!summary.passageReviewLabel.includes('긴 지문 검수 1')) {
              throw new Error(`expected passage review label, got ${summary.passageReviewLabel}`);
            }
            if (summary.passageReviewReasonLabel !== '페이지 넘김 긴 지문, 지문 하위 문항 누락') {
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
            if (!warning.message.includes('긴 지문 검수 1')) {
              throw new Error(`expected passage warning line, got ${warning.message}`);
            }
            if (!warning.cancelToast.includes('긴 지문 검수 큐')) {
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


if __name__ == "__main__":
    unittest.main()
