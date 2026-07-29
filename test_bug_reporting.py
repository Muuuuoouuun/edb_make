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
            }
        )
        receipt = bug_reporting.deliver_bug_report(
            {
                "app": {"id": "ClassInEDBMVP", "version": "0.1.0"},
                "description": "설정 화면 버튼 오류가 있습니다.",
            }
        )
        self.assertEqual(receipt["reportId"], "EDB-20260727-ABCDEF0123")
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


if __name__ == "__main__":
    unittest.main()
