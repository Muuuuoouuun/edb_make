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
        self.assertIn('htmlFor="bug-report-contact"', settings)
        self.assertIn('id="bug-report-contact"', settings)
        self.assertIn("이 문제에 관한 회신에 동의", settings)
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
            "lastOperationError",
        ):
            self.assertIn(field, handler)
        self.assertIn("contact: bugReportContact.trim()", handler)
        self.assertIn("consentToContact", handler)
        self.assertNotIn("session,", handler)
        self.assertNotIn("pendingFile,", handler)

    def test_runtime_errors_are_reduced_to_bounded_primitives(self):
        helper = self.app.split("function runtimeErrorsForBugReport", 1)[1].split(
            "async function submitBugReport", 1
        )[0]
        self.assertIn("entries.slice(-10)", helper)
        self.assertIn("message: String(message).slice(0, 1500)", helper)
        self.assertIn("safe.filename", helper)
        self.assertIn("safe.operation", helper)
        self.assertIn("safe.code", helper)
        self.assertIn("safe.status", helper)
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
            ".bug-report-contact",
        ):
            self.assertIn(selector, self.board)

    def test_publish_failure_has_persistent_recovery_actions(self):
        banner = self.app.split("function OperationRecoveryBanner", 1)[1].split(
            "function LoadingOverlay", 1
        )[0]
        for label in (
            "EDB 다시 제작",
            "최근 저장본 열기",
            "PNG로 대체 저장",
            "초기화 후 다시 시작",
            "버그 리포트 열기",
            "오류 내용 복사",
        ):
            self.assertIn(label, banner)
        self.assertIn("편집 내용은 안전합니다", banner)
        self.assertIn("초기화 후 원본 PDF를 다시 등록", banner)
        self.assertIn("설정 → 문제 신고 → 버그 리포트", banner)
        self.assertIn("operationRecoverySummary(error)", banner)
        self.assertIn("onReset", banner)
        self.assertIn("onReport", banner)
        publish = self.app.split("const onPublish = async", 1)[1].split(
            "return (", 1
        )[0]
        self.assertIn("operationErrorFromResponse", publish)
        self.assertIn("setLastOperationError(diagnostic)", publish)
        self.assertIn("setLastOperationError(null)", publish)
        self.assertIn("captureRecoverableDiagnostic", self.app)
        self.assertIn("EDB_CAPTURE_RUNTIME_DIAGNOSTIC", self.board)
        self.assertIn(".operation-recovery-banner", self.board)

    def test_recovery_report_action_opens_settings_report_form(self):
        panel = self.app.split("function SidePanel", 1)[1].split(
            "function OperationRecoveryBanner", 1
        )[0]
        self.assertIn("bugReportOpenRequestId", panel)
        self.assertIn("setTab('board')", panel)
        self.assertIn("setBugReportOpen(true)", panel)
        self.assertIn(".bug-report-card", panel)
        self.assertIn("bug-report-description", panel)
        self.assertIn("EDB 제작 중 오류가 발생했습니다", panel)

        app = self.app.split("function App()", 1)[1]
        self.assertIn("setBugReportOpenRequestId(requestId => requestId + 1)", app)
        self.assertIn("setOperationRecoveryDismissed(true)", app)
        self.assertIn("error={operationRecoveryDismissed ? null : lastOperationError}", app)
        self.assertIn("onDismiss={() => setOperationRecoveryDismissed(true)}", app)
        self.assertIn("onReport={openBugReportAfterPublishError}", app)
        self.assertIn("onReset={() => void resetSession()}", app)


if __name__ == "__main__":
    unittest.main()
