import json
import tempfile
import unittest
from pathlib import Path
from http import HTTPStatus
from unittest.mock import Mock, patch

import app_server
import bug_reporting


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class BugReportingTests(unittest.TestCase):
    def test_redaction_covers_cross_platform_paths_without_damaging_web_urls(self):
        web_url = "https://docs.classin.cloud/help/retry.pdf?next=/guide&name=sample.pdf"
        sensitive_text = (
            "D:\\School\\2026\\학생A\\중간고사.pdf | "
            "\\\\NAS01\\Academy\\학생B\\시험지.hwp | "
            "file:///Volumes/Academy/학생C/기말고사.pdf | "
            "file://NAS02/Shared/학생D/모의고사.hwpx | "
            "/opt/academy/private/학생E/진단.png | "
            "'/Volumes/Academy/학생 이름/띄어쓰기 시험.pdf' | "
            "학생F-수행평가.pdf | "
            f"{web_url}"
        )

        redacted = bug_reporting.redact_sensitive_text(sensitive_text)

        for private_value in (
            "D:\\School",
            "NAS01",
            "NAS02",
            "/Volumes",
            "/opt/academy",
            "학생A",
            "학생B",
            "학생C",
            "학생D",
            "학생E",
            "학생 이름",
            "띄어쓰기 시험.pdf",
            "학생F-수행평가.pdf",
            "중간고사.pdf",
            "시험지.hwp",
        ):
            self.assertNotIn(private_value, redacted)
        self.assertGreaterEqual(redacted.count("[local-path]"), 5)
        self.assertIn("[document]", redacted)
        self.assertIn("https://docs.classin.cloud/help/retry.pdf", redacted)
        self.assertIn("next=/guide", redacted)
        self.assertIn("name=[document]", redacted)

    def test_redaction_removes_spaced_and_url_encoded_local_identifiers(self):
        raw_url = (
            "https://reports.classin.cloud/help/retry.pdf"
            "?source=/Users/Alice/Student Name/시험.pdf&mode=retry"
        )
        encoded_url = (
            "https://reports.classin.cloud/help/retry.pdf"
            "?source=%2FUsers%2FAlice%2FStudent%2520Name%2F%EC%8B%9C%ED%97%98.pdf"
            "#fallback=D%3A%2FSchool%2FStudent%20Name"
        )
        sensitive_text = " | ".join(
            (
                raw_url,
                encoded_url,
                "/Users/Alice/Student Name/시험.pdf",
                "D:/School/Student Name",
                r"\\NAS01\Academy\Student Name\시험지.hwp",
            )
        )

        redacted = bug_reporting.redact_sensitive_text(sensitive_text)

        for private_value in (
            "Alice",
            "Student Name",
            "Student%20Name",
            "Student%2520Name",
            "시험.pdf",
            "%EC%8B%9C%ED%97%98.pdf",
            "D:/School",
            "NAS01",
        ):
            self.assertNotIn(private_value, redacted)
        self.assertEqual(redacted.count("https://reports.classin.cloud/help/retry.pdf"), 2)
        self.assertIn("?source=[local-path]&mode=retry", redacted)
        self.assertIn("#fallback=[local-path]", redacted)
        self.assertGreaterEqual(redacted.count("[local-path]"), 5)

    def test_redaction_handles_path_filename_delimiters_without_hiding_non_paths(self):
        sensitive_text = " | ".join(
            (
                "/Users/Alice/Class/exam?draft#1&copy.pdf",
                "/custom/Alice/exam?draft#1&copy.pdf",
                "D:/School/Student Name/exam?draft#1&copy.pdf",
                r"\\NAS01\Academy\Student Name\exam?draft#1&copy.pdf",
                "/Users/Alice/Student & Parent/cache?draft#1",
                "/api/health",
                "ratio 1 / 2",
            )
        )

        redacted = bug_reporting.redact_sensitive_text(sensitive_text)

        self.assertEqual(redacted.count("[local-path]"), 5)
        self.assertNotIn("Alice", redacted)
        self.assertNotIn("Student Name", redacted)
        self.assertNotIn("Student & Parent", redacted)
        self.assertNotIn("cache?draft#1", redacted)
        self.assertNotIn("draft#1&copy.pdf", redacted)
        self.assertIn("/api/health", redacted)
        self.assertIn("ratio 1 / 2", redacted)

    def test_redaction_decodes_url_secrets_email_and_masks_userinfo(self):
        sensitive_text = " | ".join(
            (
                "https://alice@example.com:secret-value@docs.classin.cloud/api/health",
                (
                    "https://docs.classin.cloud/api/health"
                    "?payload=token%253Dsk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"
                ),
                "https://docs.classin.cloud/user/%2561lice%2540example.com",
                (
                    "https://docs.classin.cloud/api/health"
                    "?source=%252FUsers%252FAlice%252FClass%252Fexam.pdf"
                ),
            )
        )

        redacted = bug_reporting.redact_sensitive_text(sensitive_text)

        for private_value in (
            "alice@example.com",
            "secret-value",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234",
            "%2561lice%2540example.com",
            "%252FUsers%252FAlice",
        ):
            self.assertNotIn(private_value, redacted)
        self.assertIn("https://[redacted-userinfo]@docs.classin.cloud/api/health", redacted)
        self.assertIn("payload=[redacted-secret]", redacted)
        self.assertIn("/user/[redacted-email]", redacted)
        self.assertIn("source=[local-path]", redacted)

    def test_redaction_privacy_closes_ambiguous_raw_local_url_suffixes(self):
        ambiguous_ampersand = (
            "https://x.test/?source=/Users/Alice/Student & Parent/cache?draft#1"
        )
        clear_parameter = (
            "https://x.test/?source=/Users/Alice/Student&mode=retry"
        )
        ambiguous_fragment = (
            "https://x.test/?source=/Users/Alice/Student#Parent/cache?draft"
        )
        fragment_parameter = (
            "https://x.test/#file=/Users/Alice/Student & Parent/cache?draft#1"
        )
        clear_fragment_parameter = (
            "https://x.test/#file=/Users/Alice/Student&mode=retry"
        )

        ampersand_redacted = bug_reporting.redact_sensitive_text(ambiguous_ampersand)
        parameter_redacted = bug_reporting.redact_sensitive_text(clear_parameter)
        fragment_redacted = bug_reporting.redact_sensitive_text(ambiguous_fragment)
        fragment_parameter_redacted = bug_reporting.redact_sensitive_text(fragment_parameter)
        clear_fragment_redacted = bug_reporting.redact_sensitive_text(clear_fragment_parameter)

        self.assertEqual(
            "https://x.test/?source=[local-path]#[local-path]",
            ampersand_redacted,
        )
        self.assertEqual(
            "https://x.test/?source=[local-path]&mode=retry",
            parameter_redacted,
        )
        self.assertEqual(
            "https://x.test/?source=[local-path]#[local-path]",
            fragment_redacted,
        )
        self.assertEqual(
            "https://x.test/#file=[local-path]",
            fragment_parameter_redacted,
        )
        self.assertEqual(
            "https://x.test/#file=[local-path]&mode=retry",
            clear_fragment_redacted,
        )
        for private_value in ("Alice", "Student", "Parent", "cache", "draft"):
            self.assertNotIn(
                private_value,
                ampersand_redacted + fragment_redacted + fragment_parameter_redacted,
            )

    def test_redaction_sanitizes_sensitive_url_parameter_keys_and_fragment_keys(self):
        sensitive_url = (
            "https://x.test/api/health"
            "?alice%2540example.com=1&token%253Dsecretvalue=1"
            "#bob%2540example.com=2"
        )

        redacted = bug_reporting.redact_sensitive_text(sensitive_url)

        for private_value in (
            "alice",
            "bob",
            "example.com",
            "secretvalue",
            "%2540",
            "token%253D",
        ):
            self.assertNotIn(private_value, redacted)
        self.assertEqual(
            "https://x.test/api/health"
            "?[redacted-email]=1&[redacted-secret]=1"
            "#[redacted-email]=2",
            redacted,
        )

    def test_report_redacts_secrets_documents_email_and_local_paths(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            log_file = Path(raw_tmp) / "app.log"
            log_file.write_text(
                "API key=AIzaABCDEFGHIJKLMNOPQRSTUVWXYZ1234\n"
                "OpenAI sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234\n"
                "user@example.com\n"
                "/Users/private/Documents/secret-exam.pdf failed\n",
                encoding="utf-8",
            )
            report = bug_reporting.build_bug_report(
                {
                    "description": "문항 인식 중 오류가 발생했습니다.",
                    "includeDiagnostics": True,
                    "context": {
                        "view": "review",
                        "itemCount": 12,
                        "privateField": "must not pass",
                        "runtimeErrors": [
                            {
                                "type": "runtime",
                                "message": "failed /Users/private/input.hwp",
                            }
                        ],
                    },
                },
                app_config={
                    "appId": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "platform": "macos",
                },
                log_file=log_file,
            )

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("AIza", serialized)
        self.assertNotIn("sk-", serialized)
        self.assertNotIn("user@example.com", serialized)
        self.assertNotIn("/Users/private", serialized)
        self.assertNotIn("secret-exam.pdf", serialized)
        self.assertNotIn("privateField", serialized)
        self.assertEqual(report["context"]["itemCount"], 12)
        self.assertIn("logTail", report["diagnostics"])

    def test_report_can_exclude_diagnostics(self):
        report = bug_reporting.build_bug_report(
            {
                "description": "설정 화면 버튼이 작동하지 않습니다.",
                "includeDiagnostics": False,
            },
            app_config={
                "appId": "ClassInEDBMVP",
                "version": "0.1.0",
                "platform": "windows",
            },
        )
        self.assertNotIn("diagnostics", report)

    def test_report_preserves_only_explicitly_consented_contact(self):
        report = bug_reporting.build_bug_report(
            {
                "description": "EDB 제작이 1초 뒤 실패합니다.",
                "contact": "customer@example.com",
                "consentToContact": True,
                "includeDiagnostics": False,
                "context": {
                    "lastOperationError": {
                        "type": "operation",
                        "operation": "session_publish",
                        "code": "edb_write_failed",
                        "status": 500,
                        "retryable": True,
                        "timestamp": "2026-08-21T08:25:48Z",
                        "message": "failed C:\\Users\\customer\\exam.pdf",
                    }
                },
            },
            app_config={"appId": "ClassInEDBMVP", "version": "1.3.5"},
        )

        self.assertEqual(report["reporter"]["contact"], "customer@example.com")
        self.assertTrue(report["reporter"]["consentToContact"])
        operation_error = report["context"]["lastOperationError"]
        self.assertEqual(operation_error["operation"], "session_publish")
        self.assertEqual(operation_error["code"], "edb_write_failed")
        self.assertEqual(operation_error["status"], 500)
        self.assertNotIn("C:\\Users\\customer", operation_error["message"])

    def test_contact_requires_matching_consent(self):
        app_config = {"appId": "ClassInEDBMVP"}
        with self.assertRaisesRegex(bug_reporting.BugReportValidationError, "연락 동의"):
            bug_reporting.build_bug_report(
                {"description": "제작 중 오류가 발생했습니다.", "contact": "customer@example.com"},
                app_config=app_config,
            )
        with self.assertRaisesRegex(bug_reporting.BugReportValidationError, "연락처를 입력"):
            bug_reporting.build_bug_report(
                {"description": "제작 중 오류가 발생했습니다.", "consentToContact": True},
                app_config=app_config,
            )

    def test_short_description_is_rejected(self):
        with self.assertRaisesRegex(bug_reporting.BugReportValidationError, "5자"):
            bug_reporting.build_bug_report(
                {"description": "오류"},
                app_config={"appId": "ClassInEDBMVP"},
            )

    @patch("bug_reporting.urlopen")
    def test_delivery_returns_remote_receipt(self, mocked_urlopen):
        mocked_urlopen.return_value = _Response(
            {
                "ok": True,
                "reportId": "EDB-20260727-ABCDEF0123",
                "receivedAt": "2026-07-27T00:00:00Z",
                "contactAccepted": True,
            }
        )
        receipt = bug_reporting.deliver_bug_report(
            {
                "app": {"id": "ClassInEDBMVP", "version": "0.1.0"},
                "description": "설정 화면 버튼 오류가 있습니다.",
            }
        )
        self.assertEqual(receipt["reportId"], "EDB-20260727-ABCDEF0123")
        self.assertTrue(receipt["contactAccepted"])
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.full_url,
            "https://reports.classin.cloud/v1/edb-reports",
        )

    @patch("app_server.deliver_bug_report")
    def test_app_server_endpoint_builds_and_forwards_report(self, mocked_deliver):
        mocked_deliver.return_value = {
            "ok": True,
            "reportId": "EDB-20260727-ABCDEF0123",
            "receivedAt": "2026-07-27T00:00:00Z",
        }
        handler = app_server.AppRequestHandler.__new__(app_server.AppRequestHandler)
        handler._read_json_body = Mock(
            return_value={
                "description": "설정 탭에서 버그 신고 테스트입니다.",
                "includeDiagnostics": False,
            }
        )
        handler._send_json = Mock()

        handler._handle_bug_report()

        mocked_deliver.assert_called_once()
        forwarded_report = mocked_deliver.call_args.args[0]
        self.assertEqual(forwarded_report["schemaVersion"], 1)
        self.assertNotIn("diagnostics", forwarded_report)
        handler._send_json.assert_called_once_with(
            mocked_deliver.return_value,
            status=HTTPStatus.CREATED,
        )

    def test_app_server_routes_bug_report_post(self):
        source = Path(app_server.__file__).read_text(encoding="utf-8")
        dispatch = source.split("def _dispatch_post", 1)[1].split(
            "def do_DELETE", 1
        )[0]
        self.assertIn('parsed.path == "/api/bug-report"', dispatch)
        self.assertIn("self._handle_bug_report()", dispatch)

    def test_publish_failure_payload_is_actionable_and_machine_readable(self):
        payload = app_server._publish_failure_payload(
            code="edb_write_failed",
            message="EDB 파일 저장에 실패했습니다",
            exc=OSError("access denied"),
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "edb_write_failed")
        self.assertEqual(payload["operation"], "session_publish")
        self.assertTrue(payload["retryable"])
        self.assertGreaterEqual(len(payload["recoverySteps"]), 2)
        self.assertIn("access denied", payload["error"])


if __name__ == "__main__":
    unittest.main()
