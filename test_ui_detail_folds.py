from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def app_source() -> str:
    return (PROJECT_ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")


def between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


class TestUiDetailFolds(unittest.TestCase):
    """Only the basic flow is visible by default; the rest sits behind 상세 설정."""

    def test_detail_fold_is_closed_by_default_and_remembered(self) -> None:
        source = app_source()
        hook = between(source, "function useDetailFoldState(id){", "function DetailFold(")
        fold = between(source, "function DetailFold(", "function DetailToggleButton(")

        self.assertIn("const DETAIL_FOLD_STORAGE_PREFIX = 'edb.detailFold.';", source)
        self.assertIn("=== '1';", hook)
        self.assertIn("return false;", hook)
        self.assertIn("next ? '1' : '0'", hook)
        self.assertIn("<strong>상세 설정</strong>", fold)
        self.assertIn("{isOpen && <div className=\"detail-settings-body\">{children}</div>}", fold)

    def test_settings_tab_keeps_only_basics_outside_the_fold(self) -> None:
        source = app_source()
        tab = between(source, "{tab === 'board' && (", "function LoadingOverlay")
        basic, advanced = tab.split('<DetailFold\n              id="settings"', 1)

        for text in ("<SplitModePicker", "한 줄 자료 수", "칠판 배경", "Gemini API 키"):
            self.assertIn(text, basic)
        for text in ("스크롤 모드", "강조색", "일괄 작업", "HWP/HWPX", "AI 보정 사용", "OpenAI API 키", "앱 업데이트", "테스트용 더미 자료 추가"):
            self.assertNotIn(text, basic)
            self.assertIn(text, advanced)

    def test_placement_tab_folds_everything_but_size(self) -> None:
        source = app_source()
        tab = between(source, "{tab === 'placement' && (", "{tab === 'board' && (")
        basic, advanced = tab.split('<DetailFold\n              id="placement"', 1)

        self.assertIn('className="scale-range"', basic)
        self.assertIn("placement-scale-actions", basic)
        self.assertIn("{placementHasOverlap && (", basic)
        for text in ("편집 범위", "레이아웃 프리셋", "콘텐츠 너비", "placement-position-control", "지문 묶음 규칙"):
            self.assertNotIn(text, basic)
            self.assertIn(text, advanced)
        self.assertNotIn("배치 적용", source)
        self.assertNotIn("placementApplied", source)

    def test_review_less_common_tools_are_behind_the_toggle(self) -> None:
        source = app_source()
        review_stage = between(source, "function ReviewStage", "function ItemsRail")

        self.assertIn("const [reviewToolsOpen, toggleReviewTools] = useDetailFoldState('reviewTools');", review_stage)
        self.assertIn("{reviewToolsOpen && (\n                <button\n                  className=\"btn\"\n                  type=\"button\"\n                  onClick={() => applyExpandedCrop()}", review_stage)
        self.assertIn("{reviewToolsOpen && showBulkRetry && (", review_stage)
        self.assertIn("{reviewToolsOpen && selectedList.length >= 2 && (", review_stage)
        self.assertIn("{reviewToolsOpen && activeReviewFilter && visibleReviewScope.problemIds.length > 0 && (", review_stage)
        self.assertEqual(2, review_stage.count("<DetailToggleButton"))
        self.assertIn('id="reviewSummary"', review_stage)
        self.assertIn("forceOpen={Boolean(reviewRiskFilter) || reviewFilter === 'passage' || reviewFilter === 'passage-review'}", review_stage)
        self.assertIn("pageRiskFlags.map(riskFlagLabel).join(' · ')", review_stage)
        self.assertNotIn("`위험 · ${pageRiskFlags.join(' · ')}`", review_stage)

    def test_rail_header_and_board_status_are_minimal(self) -> None:
        source = app_source()
        rail = between(source, "function ItemsRail", "function BoardStage")
        board = between(source, "function BoardStage", "function downloadPublishSummary")

        self.assertNotIn("bulkApply", source)
        self.assertNotIn("addMockSample", rail)
        self.assertIn("const [statusDetailsOpen, toggleStatusDetails] = useDetailFoldState('boardStatus');", board)
        self.assertIn("{statusDetailsOpen && (", board)
        self.assertNotIn("처리됨 ·", board)


if __name__ == "__main__":
    unittest.main()
