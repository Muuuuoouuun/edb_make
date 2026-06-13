import json
import os
import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_server


class TestStaticAssetCaching(unittest.TestCase):
    def test_static_responses_disable_browser_cache(self):
        handler = object.__new__(app_server.AppRequestHandler)
        headers = []
        handler.send_header = lambda name, value: headers.append((name, value))

        with patch.object(app_server.SimpleHTTPRequestHandler, "end_headers", lambda _self: headers.append(("END", ""))):
            handler.end_headers()

        self.assertIn(("Cache-Control", "no-store, max-age=0"), headers)
        self.assertIn(("Pragma", "no-cache"), headers)


def _build_session(tmpdir: Path, *, present_page_ids: set[str]) -> dict:
    pages = []
    problems = []
    for page_id in ("page-1", "page-2"):
        if page_id in present_page_ids:
            image_path = tmpdir / f"{page_id}.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            source_uri = image_path.resolve().as_uri()
        else:
            source_uri = (tmpdir / f"missing_{page_id}.png").resolve().as_uri()
        problem_id = f"{page_id}-p1"
        pages.append({
            "id": page_id,
            "sourceImageUri": source_uri,
            "problemIds": [problem_id],
            "riskFlags": ["needs_review"],
        })
        problems.append({
            "id": problem_id,
            "sourcePageId": page_id,
            "bbox": {"left": 0, "top": 0, "width": 100, "height": 100},
            "riskFlags": ["needs_review"],
        })
    return {
        "pages": pages,
        "problems": problems,
        "ai_fallback": {"provider": "gemini"},
    }


def _fake_run_problem_export(source_path, **_kwargs):
    page_id = Path(source_path).stem
    return {
        "ui_session": {
            "pages": [{
                "id": page_id,
                "riskFlags": [],
            }],
            "problems": [{
                "id": f"{page_id}-new",
                "sourcePageId": page_id,
                "bbox": {"left": 0, "top": 0, "width": 80, "height": 80},
                "riskFlags": [],
            }],
            "ai_summary": {"applied": True},
        }
    }


class TestRetryAiResilience(unittest.TestCase):
    def setUp(self):
        self._prev_key = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-key"

    def tearDown(self):
        if self._prev_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self._prev_key

    def test_missing_source_marks_page_failed_and_continues(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            session = _build_session(tmpdir, present_page_ids={"page-2"})

            with patch.object(app_server, "run_problem_export", side_effect=_fake_run_problem_export):
                new_session = app_server._mutate_retry_ai(
                    session,
                    {"pageIds": ["page-1", "page-2"]},
                )

            summaries = new_session.get("ai_retry_summary") or []
            statuses = {row["pageId"]: row["status"] for row in summaries}
            self.assertEqual(statuses, {"page-1": "missing_source", "page-2": "applied"})

            page_1 = next(p for p in new_session["pages"] if p["id"] == "page-1")
            self.assertEqual(page_1["reviewStatus"], "failed")
            self.assertIn("ai_retry_missing_source", page_1["riskFlags"])
            self.assertEqual(page_1["aiRetry"]["status"], "missing_source")
            # page-1's original problem is untouched (no replacement on missing-source)
            page_1_problem_ids = page_1["problemIds"]
            self.assertEqual(page_1_problem_ids, ["page-1-p1"])

            page_2 = next(p for p in new_session["pages"] if p["id"] == "page-2")
            self.assertEqual(page_2["aiRetry"]["status"], "applied")
            self.assertEqual(page_2["aiRetry"]["replacedProblemCount"], 1)
            # page-2's old problem was replaced
            new_problem_ids = {prob["id"] for prob in new_session["problems"]}
            self.assertNotIn("page-2-p1", new_problem_ids)
            self.assertIn("page-1-p1", new_problem_ids)

    def test_missing_key_rejects_before_any_mutation(self):
        os.environ.pop("GEMINI_API_KEY", None)
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            session = _build_session(tmpdir, present_page_ids={"page-1", "page-2"})
            with self.assertRaises(ValueError) as ctx:
                app_server._mutate_retry_ai(session, {"pageIds": ["page-1"]})
            self.assertIn("Gemini API", str(ctx.exception))
            # No ai_retry_summary, no page-level aiRetry should have been written.
            self.assertNotIn("ai_retry_summary", session)
            for page in session["pages"]:
                self.assertNotIn("aiRetry", page)


class TestSessionExcludeMutation(unittest.TestCase):
    def _session(self) -> dict:
        return {
            "pages": [
                {"id": "page-1", "problemIds": ["p1", "p2"]},
                {"id": "page-2", "problemIds": ["p3"]},
            ],
            "problems": [
                {"id": "p1", "sourcePageId": "page-1", "bbox": {"width": 100, "height": 80}},
                {"id": "p2", "sourcePageId": "page-1", "bbox": {"width": 100, "height": 80}},
                {"id": "p3", "sourcePageId": "page-2", "bbox": {"width": 100, "height": 80}},
            ],
        }

    def test_bulk_exclude_removes_multiple_problems_and_page_links(self):
        session = self._session()

        new_session = app_server._mutate_exclude_many(session, ["p1", "p3"])

        self.assertIs(new_session, session)
        self.assertEqual(["p2"], [problem["id"] for problem in new_session["problems"]])
        self.assertEqual(["p2"], new_session["pages"][0]["problemIds"])
        self.assertEqual([], new_session["pages"][1]["problemIds"])
        self.assertEqual(1, new_session["detected_problem_count"])
        self.assertEqual(1, new_session["detectedProblemCount"])

    def test_session_mutate_exclude_accepts_problem_ids_payload(self):
        session = self._session()

        class FakeServer:
            def __init__(self, latest_session):
                self.latest_session = latest_session
                self.remembered_session = None

            def remember_session(self, new_session):
                self.latest_session = new_session
                self.remembered_session = new_session

        handler = object.__new__(app_server.AppRequestHandler)
        handler.server = FakeServer(session)
        handler._read_json_body = lambda: {"action": "exclude", "problemIds": ["p1", "p3"]}
        responses = []
        handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

        handler._handle_session_mutate()

        self.assertEqual(1, len(responses))
        payload, _kwargs = responses[0]
        self.assertTrue(payload["ok"])
        remembered = handler.server.remembered_session
        self.assertIsNotNone(remembered)
        self.assertEqual(["p2"], [problem["id"] for problem in remembered["problems"]])


class TestExportErrorPayload(unittest.TestCase):
    def test_hangul_conversion_error_includes_recovery_steps(self):
        payload = app_server._export_error_payload(
            ValueError(
                "HWP/HWPX conversion failed. Details: source file could not be loaded "
                "Diagnosis: Input is a valid HWPX ZIP document. "
                "한컴오피스에서 PDF로 내보낸 뒤 다시 업로드하거나 HWPX 지원 변환기를 설치해 주세요."
            )
        )

        self.assertFalse(payload["ok"])
        self.assertEqual("hangul_conversion_failed", payload["errorKind"])
        self.assertIn("한컴오피스", payload["error"])
        self.assertGreaterEqual(len(payload["recoverySteps"]), 2)
        self.assertTrue(any("PDF" in step for step in payload["recoverySteps"]))

    def test_generic_export_error_stays_simple(self):
        payload = app_server._export_error_payload(ValueError("plain failure"))

        self.assertFalse(payload["ok"])
        self.assertEqual("export_failed", payload["errorKind"])
        self.assertEqual("plain failure", payload["error"])
        self.assertNotIn("recoverySteps", payload)


class TestExportSourceResolution(unittest.TestCase):
    def test_files_accepts_source_path_strings_for_automation_clients(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            source = tmpdir / "sample.hwp"
            source.write_bytes(b"fake")
            handler = object.__new__(app_server.AppRequestHandler)

            resolved = handler._resolve_source_paths({"files": [str(source)]})

            self.assertEqual(resolved, [source.resolve()])

    def test_uploaded_files_reuse_same_content_path_for_cache_stability(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            upload_dir = tmpdir / "uploads"
            upload_dir.mkdir()
            handler = object.__new__(app_server.AppRequestHandler)
            payload = {
                "fileName": "평가원 양식.hwp",
                "fileDataBase64": base64.b64encode(b"same hwp bytes").decode("ascii"),
            }

            with patch.object(app_server, "UPLOAD_DIR", upload_dir):
                first = handler._save_uploaded_file(payload)
                second = handler._save_uploaded_file(payload)

            self.assertEqual(first, second)
            self.assertTrue(first.exists())
            self.assertEqual(b"same hwp bytes", first.read_bytes())
            self.assertIn("평가원 양식", first.name)
            self.assertEqual(1, len(list(upload_dir.glob("*.hwp"))))

    def test_uploaded_files_reuse_same_content_path_when_filename_changes(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            upload_dir = tmpdir / "uploads"
            upload_dir.mkdir()
            handler = object.__new__(app_server.AppRequestHandler)
            file_data = base64.b64encode(b"same hwp bytes").decode("ascii")

            with patch.object(app_server, "UPLOAD_DIR", upload_dir):
                first = handler._save_uploaded_file(
                    {"fileName": "download-a.hwp", "fileDataBase64": file_data}
                )
                second = handler._save_uploaded_file(
                    {"fileName": "renamed-by-user.hwp", "fileDataBase64": file_data}
                )

            self.assertEqual(first, second)
            self.assertEqual(1, len(list(upload_dir.glob("*.hwp"))))

    def test_export_uses_stable_upload_path_across_repeated_base64_uploads(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            upload_dir = tmpdir / "uploads"
            upload_dir.mkdir()
            output_dir = tmpdir / "out"
            source_paths: list[Path] = []
            payload = {
                "fileName": "same.hwp",
                "fileDataBase64": base64.b64encode(b"same hwp bytes").decode("ascii"),
                "outputDir": str(output_dir),
                "preview": True,
            }

            class FakeServer:
                allowed_files = set()

                def remember_session(self, session):
                    self.latest_session = session

            def fake_run_problem_export(source, **kwargs):
                source_paths.append(Path(source))
                resolved_output = Path(kwargs.get("output_dir") or output_dir)
                return {
                    "ok": True,
                    "ui_session": {"pages": [], "problems": []},
                    "output_dir": str(resolved_output),
                    "ui_session_path": str(resolved_output / "ui_session.json"),
                    "edb_path": None,
                    "summary": {"placements": []},
                }

            responses = []
            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = FakeServer()
            handler._read_json_body = lambda: dict(payload)
            handler._send_json = lambda body, **kwargs: responses.append((body, kwargs))

            with (
                patch.object(app_server, "UPLOAD_DIR", upload_dir),
                patch.object(app_server, "run_problem_export", side_effect=fake_run_problem_export),
            ):
                handler._handle_export()
                handler._handle_export()

            self.assertEqual(2, len(source_paths))
            self.assertEqual(source_paths[0], source_paths[1])
            self.assertTrue(source_paths[0].exists())
            self.assertEqual(1, len(list(upload_dir.glob("*.hwp"))))
            self.assertEqual(2, len(responses))
            self.assertTrue(all(response[0]["ok"] for response in responses))


class TestRuntimeDiagnostics(unittest.TestCase):
    def test_runtime_diagnostics_reports_hangul_converter_readiness(self):
        with (
            patch.object(app_server.preprocess, "_iter_hwp_pdf_converter_commands", return_value=[["/usr/bin/soffice", "--headless"]]),
            patch.object(app_server.preprocess, "_iter_hwp_hwpx_converter_commands", return_value=[["/usr/local/bin/hwpilot"]]),
            patch.object(app_server.preprocess, "_iter_pyhwp_html_converter_commands", return_value=[["/venv/bin/hwp5html"]]),
            patch.object(app_server.preprocess, "_iter_hwp_text_converter_commands", return_value=[["/venv/bin/hwp5txt"], ["/venv/bin/python", "-c", "import unhwp; unhwp.extract_text('x')"]]),
            patch.object(app_server.preprocess, "_iter_chrome_pdf_commands", return_value=[["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]]),
            patch.object(app_server.preprocess, "_iter_rhwp_core_renderer_commands", return_value=[]),
        ):
            diagnostics = app_server.describe_runtime_diagnostics()

        self.assertTrue(diagnostics["ok"])
        hangul = diagnostics["hangul"]
        self.assertTrue(hangul["pdfReady"])
        self.assertTrue(hangul["hwpReady"])
        self.assertTrue(hangul["hwpxReady"])
        self.assertEqual("ready", hangul["status"])
        self.assertEqual("soffice", hangul["pdfConverters"][0]["name"])
        self.assertEqual("hwpilot", hangul["hwpToHwpxConverters"][0]["name"])
        self.assertEqual("hwp5txt", hangul["textExtractors"][0]["name"])
        self.assertEqual("unhwp", hangul["textExtractors"][1]["name"])
        self.assertEqual("hwp5html", hangul["htmlConverters"][0]["name"])
        self.assertEqual("Google Chrome", hangul["chromePdfConverters"][0]["name"])
        self.assertEqual("준비됨", hangul["label"])
        self.assertEqual(
            {
                "pdfConverters": 1,
                "hwpToHwpxConverters": 1,
                "htmlConverters": 1,
                "textExtractors": 2,
                "chromePdfConverters": 1,
                "hwpRenderers": 0,
            },
            hangul["toolCounts"],
        )
        self.assertIn("PDF 1", hangul["summary"])
        self.assertIn("텍스트 2", hangul["summary"])

    def test_runtime_diagnostics_treats_rhwp_core_renderer_as_hangul_ready(self):
        with (
            patch.object(app_server.preprocess, "_iter_hwp_pdf_converter_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_hwp_hwpx_converter_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_pyhwp_html_converter_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_hwp_text_converter_commands", return_value=[["/app/.bin/kordoc"]]),
            patch.object(app_server.preprocess, "_iter_chrome_pdf_commands", return_value=[]),
            patch.object(
                app_server.preprocess,
                "_iter_rhwp_core_renderer_commands",
                return_value=[["/usr/bin/node", "/app/scripts/render_hwp_with_rhwp_core.mjs", "--node-modules", "/app/node_modules"]],
            ),
        ):
            diagnostics = app_server.describe_runtime_diagnostics()

        hangul = diagnostics["hangul"]
        self.assertFalse(hangul["pdfReady"])
        self.assertTrue(hangul["hwpReady"])
        self.assertTrue(hangul["hwpxReady"])
        self.assertTrue(hangul["hwpRendererReady"])
        self.assertEqual("ready", hangul["status"])
        self.assertEqual("rhwp-core", hangul["hwpRenderers"][0]["name"])
        self.assertIn("렌더 1", hangul["summary"])
        self.assertFalse(any("PDF converter was not found" in warning for warning in hangul["warnings"]))

    def test_runtime_diagnostics_reports_actionable_hangul_warning_when_missing_pdf_converter(self):
        with (
            patch.object(app_server.preprocess, "_iter_hwp_pdf_converter_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_hwp_hwpx_converter_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_pyhwp_html_converter_commands", return_value=[["/venv/bin/hwp5html"]]),
            patch.object(app_server.preprocess, "_iter_pyhwp_text_converter_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_chrome_pdf_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_rhwp_core_renderer_commands", return_value=[]),
        ):
            diagnostics = app_server.describe_runtime_diagnostics()

        hangul = diagnostics["hangul"]
        self.assertFalse(hangul["pdfReady"])
        self.assertFalse(hangul["hwpReady"])
        self.assertFalse(hangul["hwpxReady"])
        self.assertEqual("blocked", hangul["status"])
        self.assertEqual("확인 필요", hangul["label"])
        self.assertIn("주의", hangul["summary"])
        self.assertTrue(any("rhwp" in warning for warning in hangul["warnings"]))
        self.assertTrue(any("PDF" in step for step in hangul["recommendedActions"]))
        self.assertTrue(any("Chrome" in warning or "LibreOffice" in warning for warning in hangul["warnings"]))

    def test_runtime_diagnostics_labels_node_wrapped_hwpilot_bridge(self):
        with (
            patch.object(app_server.preprocess, "_iter_hwp_pdf_converter_commands", return_value=[["/usr/bin/soffice", "--headless"]]),
            patch.object(
                app_server.preprocess,
                "_iter_hwp_hwpx_converter_commands",
                return_value=[["/usr/local/bin/node", "/tmp/hwpilot-src/dist/src/cli/main.js"]],
            ),
            patch.object(app_server.preprocess, "_iter_pyhwp_html_converter_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_hwp_text_converter_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_chrome_pdf_commands", return_value=[]),
            patch.object(app_server.preprocess, "_iter_rhwp_core_renderer_commands", return_value=[]),
        ):
            diagnostics = app_server.describe_runtime_diagnostics()

        hangul = diagnostics["hangul"]
        self.assertEqual("hwpilot", hangul["hwpToHwpxConverters"][0]["name"])


class TestSessionHistory(unittest.TestCase):
    def test_session_file_paths_and_http_rewrite_include_classin_handoff_files(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            handoff_json = tmpdir / "classin_handoff.json"
            handoff_md = tmpdir / "classin_handoff.md"
            handoff_json.write_text("{}", encoding="utf-8")
            handoff_md.write_text("# check", encoding="utf-8")
            session = {
                "classin_handoff_path": str(handoff_json),
                "classin_handoff_markdown_path": str(handoff_md),
            }

            paths = app_server.collect_session_file_paths(session)
            rewritten = app_server.rewrite_session_for_http(session)

        self.assertIn(str(handoff_json), paths)
        self.assertIn(str(handoff_md), paths)
        self.assertIn("/api/file?path=", rewritten["classin_handoff_uri"])
        self.assertIn("/api/file?path=", rewritten["classin_handoff_markdown_uri"])

    def test_session_history_deduplicates_by_output_dir_and_keeps_latest_snapshot(self):
        older = {
            "session_name": "국어 6월",
            "generated_at": "2026-06-13T12:00:00+09:00",
            "output_dir": "/tmp/session-a",
            "problems": [{"id": "old"}],
            "core_problem_count": 1,
            "supplemental_item_count": 0,
        }
        newer = {
            "session_name": "국어 6월",
            "generated_at": "2026-06-13T12:10:00+09:00",
            "output_dir": "/tmp/session-a",
            "problems": [{"id": "new-1"}, {"id": "new-2"}],
            "core_problem_count": 2,
            "supplemental_item_count": 0,
        }

        first = app_server._session_history_with_session([], older, updated_at="2026-06-13T12:00:00+09:00")
        history = app_server._session_history_with_session(first, newer, updated_at="2026-06-13T12:10:00+09:00")

        self.assertEqual(1, len(history))
        self.assertEqual("국어 6월", history[0]["sessionName"])
        self.assertEqual("/tmp/session-a", history[0]["outputDir"])
        self.assertEqual(2, history[0]["coreProblemCount"])
        self.assertEqual(["new-1", "new-2"], [problem["id"] for problem in history[0]["session"]["problems"]])

    def test_public_session_history_omits_full_session_payload(self):
        session = {
            "session_name": "영어 양식",
            "generated_at": "2026-06-13T12:00:00+09:00",
            "output_dir": "/tmp/session-b",
            "problems": [{"id": "p1"}],
            "core_problem_count": 1,
            "supplemental_item_count": 0,
        }
        history = app_server._session_history_with_session([], session, updated_at="2026-06-13T12:00:00+09:00")

        public = app_server._public_session_history(history)

        self.assertEqual(1, len(public))
        self.assertNotIn("session", public[0])
        self.assertEqual(history[0]["id"], public[0]["id"])
        self.assertEqual("영어 양식", public[0]["sessionName"])

    def test_public_session_history_marks_missing_publish_artifacts(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = {
                "session_name": "삭제된 제작본",
                "generated_at": "2026-06-13T12:00:00+09:00",
                "output_dir": str(root),
                "problems": [{"id": "p1"}],
                "publishSummary": {
                    "edbFileName": "missing.edb",
                    "edbPath": str(root / "missing.edb"),
                    "edbFileUri": "/api/file?path=missing",
                    "outputDir": str(root / "missing-output"),
                    "classinHandoffPath": str(root / "classin_handoff.json"),
                    "classinHandoffMarkdownPath": str(root / "classin_handoff.md"),
                },
            }
            history = app_server._session_history_with_session(
                [],
                session,
                updated_at="2026-06-13T12:00:00+09:00",
            )

            public = app_server._public_session_history(history)

            summary = public[0]["publishSummary"]
            self.assertFalse(summary["edbFileExists"])
            self.assertFalse(summary["outputDirExists"])
            self.assertFalse(summary["edb_file_exists"])
            self.assertFalse(summary["output_dir_exists"])
            self.assertIn("/api/file?path=", summary["classinHandoffUri"])
            self.assertIn("/api/file?path=", summary["classinHandoffMarkdownUri"])
            self.assertEqual(summary["classinHandoffUri"], summary["classin_handoff_uri"])
            self.assertEqual(summary["classinHandoffMarkdownUri"], summary["classin_handoff_markdown_uri"])


class TestSystemOpenTargets(unittest.TestCase):
    def test_resolve_open_file_target_accepts_runtime_edb(self):
        app_server.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=app_server.RUNTIME_DIR) as raw_tmp:
            edb_path = Path(raw_tmp) / "lesson.edb"
            edb_path.write_bytes(b"edb")

            target = app_server._resolve_open_target(str(edb_path), kind="file")

            self.assertEqual(edb_path.resolve(), target)

    def test_resolve_open_file_target_rejects_outside_allowed_roots(self):
        with TemporaryDirectory() as raw_tmp:
            edb_path = Path(raw_tmp) / "outside.edb"
            edb_path.write_bytes(b"edb")

            with self.assertRaises(ValueError) as ctx:
                app_server._resolve_open_target(str(edb_path), kind="file")

            self.assertIn("outside allowed roots", str(ctx.exception))

    def test_resolve_open_file_target_rejects_folder_for_file_open(self):
        app_server.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=app_server.RUNTIME_DIR) as raw_tmp:
            with self.assertRaises(FileNotFoundError) as ctx:
                app_server._resolve_open_target(raw_tmp, kind="file")

            self.assertIn("file not found", str(ctx.exception))

    def test_remember_session_history_writes_history_file(self):
        with TemporaryDirectory() as raw_tmp:
            history_path = Path(raw_tmp) / "history.json"
            session = {
                "session_name": "과탐 양식",
                "generated_at": "2026-06-13T12:00:00+09:00",
                "output_dir": "/tmp/session-c",
                "problems": [{"id": "p1"}],
            }

            history = app_server.remember_session_history(
                session,
                path=history_path,
                updated_at="2026-06-13T12:00:00+09:00",
            )

            persisted = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(history, persisted)
            self.assertEqual("과탐 양식", persisted[0]["sessionName"])
            self.assertEqual(["p1"], [problem["id"] for problem in persisted[0]["session"]["problems"]])

    def test_latest_session_registers_history_entry(self):
        with TemporaryDirectory() as raw_tmp:
            latest_path = Path(raw_tmp) / "latest.json"
            session = {
                "session_name": "최근 작업",
                "generated_at": "2026-06-13T12:00:00+09:00",
                "output_dir": "/tmp/session-d",
                "problems": [{"id": "p1"}],
            }
            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = type("FakeServer", (), {
                "latest_session": session,
                "allowed_files": set(),
            })()
            responses = []
            handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

            with (
                patch.object(app_server, "LATEST_SESSION_JSON", latest_path),
                patch.object(app_server, "remember_session_history") as mock_history,
            ):
                handler._handle_latest_session()

            mock_history.assert_called_once_with(session)
            self.assertTrue(responses[0][0]["ok"])


class TestClassInManualReview(unittest.TestCase):
    def test_apply_classin_review_result_marks_current_publish_summary_passed(self):
        session = {
            "publish_summary": {
                "edbFileName": "lesson.edb",
                "edbPath": "/tmp/lesson.edb",
            },
            "publishSummary": {
                "edbFileName": "lesson.edb",
                "edbPath": "/tmp/lesson.edb",
            },
            "publish_history": [
                {"edbFileName": "lesson.edb", "edbPath": "/tmp/lesson.edb"},
                {"edbFileName": "old.edb", "edbPath": "/tmp/old.edb"},
            ],
        }

        review = app_server._apply_classin_review_result(
            session,
            {"status": "passed", "notes": "ClassIn에서 정상 확인"},
            reviewed_at="2026-06-14T00:30:00+09:00",
        )

        self.assertEqual("passed", review["status"])
        self.assertEqual("ClassIn 확인 완료", review["statusLabel"])
        self.assertTrue(review["classinOpened"])
        self.assertTrue(review["recordCountOk"])
        self.assertTrue(review["orderOk"])
        self.assertTrue(review["readabilityOk"])
        self.assertEqual("ClassIn에서 정상 확인", review["notes"])
        self.assertEqual("passed", session["classinReview"]["status"])
        self.assertEqual("passed", session["publishSummary"]["classinReviewStatus"])
        self.assertTrue(session["publishSummary"]["classinReviewPassed"])
        self.assertEqual("ClassIn 확인 완료", session["publish_summary"]["classin_review_status_label"])
        self.assertEqual("passed", session["publish_history"][0]["classinReviewStatus"])
        self.assertNotIn("classinReviewStatus", session["publish_history"][1])

    def test_classin_review_handler_returns_review_alias(self):
        session = {
            "session_name": "수업",
            "publishSummary": {
                "edbFileName": "lesson.edb",
                "edbPath": "/tmp/lesson.edb",
            },
        }
        remembered = []
        fake_server = type("FakeServer", (), {
            "latest_session": session,
            "allowed_files": set(),
            "remember_session": lambda self, next_session: remembered.append(next_session),
        })()
        handler = object.__new__(app_server.AppRequestHandler)
        handler.server = fake_server
        handler._read_json_body = lambda: {"status": "passed"}
        responses = []
        handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

        with patch.object(app_server, "load_session_history", return_value=[]):
            handler._handle_session_classin_review()

        self.assertEqual(1, len(remembered))
        body = responses[0][0]
        self.assertTrue(body["ok"])
        self.assertEqual("passed", body["review"]["status"])
        self.assertEqual(body["classinReview"], body["review"])
        self.assertEqual("passed", body["session"]["publishSummary"]["classinReviewStatus"])


class TestReviewSummary(unittest.TestCase):
    def test_session_review_summary_collects_hwp_text_qa(self):
        session = {
            "warning_messages": ["감지된 문항 수 확인 필요"],
            "problems": [
                {"id": "p1", "riskFlags": []},
                {"id": "p2", "riskFlags": []},
                {"id": "doc-1", "riskFlags": ["marker_document_continuation"]},
            ],
            "pages": [
                {
                    "id": "page-1",
                    "metadata": {
                        "hwp_conversion_quality": {
                            "hwp_text_extractor": "hwp5txt",
                            "hwp_text_numbered_problem_count": 45,
                            "hwp_text_stem_problem_count": 0,
                        }
                    },
                },
                {
                    "id": "page-2",
                    "metadata": {
                        "hwp_conversion_quality": {
                            "hwp_text_extractor": "rhwp",
                            "hwp_text_numbered_problem_count": 45,
                            "hwp_text_stem_problem_count": 3,
                        }
                    },
                },
                {
                    "id": "page-3",
                    "metadata": {
                        "hwp_conversion_quality": {
                            "hwp_text_extractor": "rhwp",
                            "hwp_text_numbered_problem_count": 45,
                            "hwp_text_stem_problem_count": 3,
                        }
                    },
                },
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual(3, summary["detectedProblemCount"])
        self.assertEqual(2, summary["coreProblemCount"])
        self.assertEqual(1, summary["supplementalItemCount"])
        self.assertEqual(2, summary["warningCount"])
        self.assertEqual({"hwp5txt": 1, "rhwp": 2}, summary["hwpTextExtractors"])
        self.assertEqual(45, summary["hwpTextProblemSignalCount"])
        self.assertEqual("mismatch", summary["hwpTextProblemCountStatus"])

    def test_session_review_summary_collects_review_status_and_risk_flags(self):
        session = {
            "problems": [
                {
                    "id": "p1",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": [],
                },
                {
                    "id": "p2",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": ["fallback_grouping", "ocr_disabled"],
                },
                {
                    "id": "p3",
                    "bbox": {"width": 0, "height": 0},
                    "riskFlags": ["ai_retry_missing_source"],
                },
                {
                    "id": "doc-1",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": ["marker_document_continuation", "fallback_grouping"],
                },
            ],
            "pages": [
                {
                    "id": "page-1",
                    "riskFlags": ["large_block_dominance", "ocr_disabled"],
                    "problemIds": ["p1", "p2"],
                }
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual(
            {"all": 4, "normal": 1, "check_needed": 2, "failed": 1},
            summary["reviewStatusCounts"],
        )
        self.assertEqual(3, summary["needsReviewCount"])
        self.assertEqual(
            {
                "ai_retry_missing_source": 1,
                "fallback_grouping": 2,
                "large_block_dominance": 1,
                "marker_document_continuation": 1,
                "ocr_disabled": 2,
            },
            summary["riskFlagCounts"],
        )
        self.assertEqual(
            [
                {"flag": "fallback_grouping", "count": 2},
                {"flag": "ocr_disabled", "count": 2},
                {"flag": "ai_retry_missing_source", "count": 1},
            ],
            summary["topRiskFlags"],
        )
        self.assertEqual(
            {
                "ai_retry_missing_source": 1,
                "fallback_grouping": 2,
                "large_block_dominance": 1,
            },
            summary["actionableRiskFlagCounts"],
        )
        self.assertEqual(
            [
                {"flag": "fallback_grouping", "count": 2},
                {"flag": "ai_retry_missing_source", "count": 1},
                {"flag": "large_block_dominance", "count": 1},
            ],
            summary["topActionableRiskFlags"],
        )
        self.assertEqual({"all": 1, "normal": 0, "check_needed": 1, "failed": 0}, summary["supplementalReviewStatusCounts"])
        self.assertEqual({"all": 3, "normal": 1, "check_needed": 1, "failed": 1}, summary["coreReviewStatusCounts"])

    def test_session_review_summary_demotes_fallback_grouping_when_hwp_counts_match(self):
        session = {
            "problems": [
                {
                    "id": "p1",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": [
                        "fallback_grouping",
                        "large_block_dominance",
                        "ocr_disabled",
                        "problem_per_block",
                    ],
                },
                {
                    "id": "p2",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": [
                        "fallback_grouping",
                        "large_block_dominance",
                        "ocr_disabled",
                        "problem_per_block",
                    ],
                },
            ],
            "pages": [
                {
                    "id": "page-1",
                    "problemIds": ["p1", "p2"],
                    "riskFlags": ["sparse_segmentation", "no_problem_markers"],
                    "metadata": {
                        "hwp_conversion_quality": {
                            "hwp_text_extractor": "rhwp-markdown",
                            "hwp_text_numbered_problem_count": 2,
                            "hwp_layout_extractor": "rhwp-render-tree",
                            "hwp_layout_problem_marker_count": 2,
                        }
                    },
                }
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual(
            {
                "fallback_grouping": 2,
                "large_block_dominance": 2,
                "no_problem_markers": 1,
                "ocr_disabled": 2,
                "problem_per_block": 2,
                "sparse_segmentation": 1,
            },
            summary["riskFlagCounts"],
        )
        self.assertTrue(summary["hwpTextProblemCountMatches"])
        self.assertTrue(summary["hwpLayoutProblemCountMatches"])
        self.assertEqual(2, summary["needsReviewCount"])
        self.assertEqual(0, summary["actionableNeedsReviewCount"])
        self.assertEqual({}, summary["actionableRiskFlagCounts"])
        self.assertEqual([], summary["topActionableRiskFlags"])

    def test_session_review_summary_keeps_sparse_marker_risks_actionable_without_hwp_count_match(self):
        session = {
            "problems": [
                {
                    "id": "p1",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": [],
                },
            ],
            "pages": [
                {
                    "id": "page-1",
                    "problemIds": ["p1"],
                    "riskFlags": ["sparse_segmentation", "no_problem_markers"],
                }
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertFalse(summary["hwpTextProblemCountMatches"])
        self.assertFalse(summary["hwpLayoutProblemCountMatches"])
        self.assertEqual(
            {"no_problem_markers": 1, "sparse_segmentation": 1},
            summary["actionableRiskFlagCounts"],
        )
        self.assertEqual(1, summary["actionableNeedsReviewCount"])

    def test_session_review_summary_counts_hwp_segmentation_risks(self):
        session = {
            "problems": [
                {
                    "id": "p1",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": [],
                },
                {
                    "id": "p2",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": [],
                },
            ],
            "pages": [
                {
                    "id": "page-1",
                    "problemIds": ["p1", "p2"],
                    "riskFlags": ["hwp_problem_count_mismatch", "hwp_oversegmentation"],
                },
                {
                    "id": "page-2",
                    "riskFlags": ["hwp_problem_count_mismatch"],
                },
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual(2, summary["hwpProblemCountMismatchCount"])
        self.assertEqual(1, summary["hwpOversegmentationCount"])
        self.assertEqual(3, summary["actionableNeedsReviewCount"])
        self.assertEqual(
            {
                "hwp_problem_count_mismatch": 2,
                "hwp_oversegmentation": 1,
            },
            summary["actionableRiskFlagCounts"],
        )

    def test_session_review_summary_reads_hwp_text_qa_from_pages_json_path(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            pages_json_path = tmpdir / "pages.json"
            pages_json_path.write_text(
                json.dumps(
                    [
                        {
                            "page_id": "page-1",
                            "metadata": {
                                "hwp_conversion_quality": {
                                    "hwp_text_extractor": "rhwp",
                                    "hwp_text_numbered_problem_count": 45,
                                    "hwp_text_stem_problem_count": 0,
                                }
                            },
                        },
                        {
                            "page_id": "page-2",
                            "metadata": {
                                "hwp_conversion_quality": {
                                    "hwp_text_extractor": "rhwp",
                                    "hwp_text_numbered_problem_count": 45,
                                    "hwp_text_stem_problem_count": 0,
                                }
                            },
                        },
                    ]
                ),
                encoding="utf-8",
            )
            session = {
                "pages_json_path": str(pages_json_path),
                "problems": [{"id": "p1"}, {"id": "doc-1", "riskFlags": ["marker_document_continuation"]}],
                "pages": [{"id": "page-1"}, {"id": "page-2"}],
            }

            summary = app_server._session_review_summary(session)

        self.assertEqual({"rhwp": 2}, summary["hwpTextExtractors"])
        self.assertEqual(45, summary["hwpTextProblemSignalCount"])

    def test_session_review_summary_flags_hwp_text_count_mismatch(self):
        session = {
            "problems": [
                {"id": "p1"},
                {"id": "p2"},
                {"id": "p3"},
                {"id": "doc-1", "riskFlags": ["marker_document_continuation"]},
            ],
            "pages": [
                {
                    "id": "page-1",
                    "metadata": {
                        "hwp_conversion_quality": {
                            "hwp_text_extractor": "rhwp",
                            "hwp_text_numbered_problem_count": 5,
                            "hwp_text_stem_problem_count": 0,
                        }
                    },
                },
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual("mismatch", summary["hwpTextProblemCountStatus"])
        self.assertFalse(summary["hwpTextProblemCountMatches"])
        self.assertEqual(-2, summary["hwpTextProblemDelta"])
        self.assertEqual(1, summary["warningCount"])
        self.assertIn("누락 문항", summary["hwpTextProblemCountMessage"])
        self.assertIn(summary["hwpTextProblemCountMessage"], summary["warningMessages"])

    def test_session_review_summary_collects_hwp_layout_qa(self):
        session = {
            "problems": [{"id": "p1"}, {"id": "p2"}],
            "pages": [
                {
                    "id": "page-1",
                    "metadata": {
                        "hwp_conversion_quality": {
                            "hwp_layout_extractor": "rhwp-render-tree",
                            "hwp_layout_page_count": 2,
                            "hwp_layout_problem_marker_count": 2,
                            "hwp_layout_text_line_count": 12,
                        }
                    },
                },
                {
                    "id": "page-2",
                    "metadata": {
                        "hwp_conversion_quality": {
                            "hwp_layout_extractor": "rhwp-render-tree",
                            "hwp_layout_page_count": 2,
                            "hwp_layout_problem_marker_count": 2,
                            "hwp_layout_text_line_count": 12,
                        }
                    },
                },
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual({"rhwp-render-tree": 2}, summary.get("hwpLayoutExtractors"))
        self.assertEqual(2, summary.get("hwpLayoutProblemSignalCount"))
        self.assertEqual(2, summary.get("hwpLayoutPageCount"))
        self.assertEqual(12, summary.get("hwpLayoutTextLineCount"))
        self.assertEqual("match", summary.get("hwpLayoutProblemCountStatus"))
        self.assertTrue(summary.get("hwpLayoutProblemCountMatches"))

    def test_session_review_summary_adjusts_hwp_layout_count_by_duplicate_marker_skips(self):
        session = {
            "problems": [{"id": f"p{index}"} for index in range(1, 21)],
            "pages": [
                {
                    "id": "page-1",
                    "metadata": {
                        "duplicate_problem_numbers_skipped": list(range(21, 32)),
                        "hwp_conversion_quality": {
                            "hwp_text_extractor": "rhwp-markdown",
                            "hwp_text_numbered_problem_count": 20,
                            "hwp_layout_extractor": "rhwp-render-tree",
                            "hwp_layout_problem_marker_count": 31,
                        },
                    },
                }
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual(20, summary.get("hwpLayoutProblemSignalCount"))
        self.assertEqual("match", summary.get("hwpLayoutProblemCountStatus"))
        self.assertTrue(summary.get("hwpLayoutProblemCountMatches"))
        self.assertEqual(0, summary.get("hwpLayoutProblemDelta"))

    def test_session_review_summary_collects_hwp_cache_hits(self):
        session = {
            "problems": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
            "pages": [
                {
                    "id": "page-1",
                    "metadata": {
                        "hwp_renderer_cache_hit": True,
                        "hwp_normalized_cache_hit": True,
                    },
                },
                {
                    "id": "page-2",
                    "metadata": {
                        "hwp_renderer_cache_hit": True,
                    },
                },
                {
                    "id": "page-3",
                    "metadata": {},
                },
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual(2, summary["hwpCacheHitPageCount"])
        self.assertEqual(2, summary["hwpRendererCacheHitCount"])
        self.assertEqual(1, summary["hwpNormalizedCacheHitCount"])

    def test_session_review_summary_treats_layout_mismatch_as_advisory_when_text_matches(self):
        session = {
            "problems": [{"id": "p1"}, {"id": "p2"}],
            "pages": [
                {
                    "id": "page-1",
                    "metadata": {
                        "hwp_conversion_quality": {
                            "hwp_text_extractor": "rhwp-markdown",
                            "hwp_text_numbered_problem_count": 2,
                            "hwp_layout_extractor": "rhwp-render-tree",
                            "hwp_layout_problem_marker_count": 3,
                        }
                    },
                },
            ],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual("match", summary.get("hwpTextProblemCountStatus"))
        self.assertEqual("mismatch", summary.get("hwpLayoutProblemCountStatus"))
        self.assertEqual(0, summary.get("warningCount"))
        self.assertEqual([], summary.get("warningMessages"))


if __name__ == "__main__":
    unittest.main()
