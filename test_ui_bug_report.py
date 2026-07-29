import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
APP_SOURCE = PROJECT_ROOT / "ui_prototype" / "app.jsx"
BOARD_SOURCE = PROJECT_ROOT / "ui_prototype" / "board.html"


class BugReportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = APP_SOURCE.read_text(encoding="utf-8")
        cls.board = BOARD_SOURCE.read_text(encoding="utf-8")

    def test_settings_panel_has_accessible_bug_report_form(self):
        settings = self.app.split("{tab === 'board' && (", 1)[1].split(
            "function LoadingOverlay", 1
        )[0]
        self.assertIn('aria-label="버그 리포트"', settings)
        self.assertIn("사용 중 문제가 있었나요?", settings)
        self.assertIn('htmlFor="bug-report-description"', settings)
        self.assertIn('id="bug-report-description"', settings)
        self.assertIn("진단 정보 포함", settings)
        self.assertIn("리포트 보내기", settings)
        self.assertIn("원본 시험지, 세션 내용, API 키, 전체 로컬 경로", settings)

    def test_submit_posts_only_allowlisted_context_to_local_endpoint(self):
        helper = self.app.split("async function submitBugReport", 1)[1].split(
            "async function fetchAppUpdateStatus", 1
        )[0]
        handler = self.app.split("const handleBugReportSubmit", 1)[1].split(
            "const savedCrop", 1
        )[0]
        self.assertIn("fetch('/api/bug-report'", helper)
        self.assertIn("method: 'POST'", helper)
        self.assertIn("'Content-Type': 'application/json'", helper)
        for field in (
            "view",
            "settingsTab",
            "inputIntent",
            "reviewStatus",
            "itemCount",
            "pendingCount",
            "hangul",
            "runtimeErrors",
        ):
            self.assertIn(field, handler)
        self.assertNotIn("session,", handler)
        self.assertNotIn("pendingFile,", handler)

    def test_runtime_errors_are_reduced_to_bounded_primitives(self):
        helper = self.app.split("function runtimeErrorsForBugReport", 1)[1].split(
            "async function submitBugReport", 1
        )[0]
        self.assertIn("entries.slice(-10)", helper)
        self.assertIn("message: String(message).slice(0, 1500)", helper)
        self.assertIn("safe.filename", helper)
        self.assertNotIn("error: rawError", helper)

    def test_report_card_styles_match_existing_settings_panel(self):
        for selector in (
            ".bug-report-card",
            ".bug-report-summary",
            ".bug-report-form",
            ".bug-report-diagnostics",
            ".bug-report-privacy",
            ".bug-report-status.success",
            ".bug-report-actions",
        ):
            self.assertIn(selector, self.board)


if __name__ == "__main__":
    unittest.main()
