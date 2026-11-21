"""Round-2 integration fixes (server): problems that only show once the round-2 branches run together.

- G1: a lesson saved on two PCs that spell the synced data folder differently is listed once.
- G2/G11: a running server never writes its old copy back over another PC's 새 수업 or save.
- G5: Tesseract OCR text (one word per line) never becomes a problem name.
- G7: renaming a lesson another PC closed gets the Korean session_conflict.
- G9: Windows PowerShell 5.1 parses the installer script on English Windows.
- G15: a data folder moved off a network share keeps its lessons.
- G18/G19: error cards name the teacher's file, and an empty PDF is reported in Korean.
- G24: the packaging doc says where API keys really live.
- G25: a browser that leaves mid-reply leaves no traceback in app.log.
- G27: a source run counts rhwp-python as an HWP renderer.
"""
from __future__ import annotations

import json
import ntpath
import os
import time
from contextlib import ExitStack
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

import app_server
import build_problem_board_edb as board
import preprocess
from test_dev2_storage_sync import _history_handler
from test_dev_srv_storage import _age_tree, _runtime_patches, _write
from test_stabilize_server_errors import _handler

ROOT = Path(__file__).resolve().parent


def _titles(session: dict) -> list[str]:
    return [problem["title"] for problem in session["problems"]]


# --- G1: per-PC folder spellings x sync conflict copies ------------------------------------------

def test_lesson_saved_on_two_pcs_is_listed_once():
    # R21 x R59: each PC spells the synced data folder differently (user name, 문서/Documents, a Mac),
    # so the 최근 수업 id must come from the stored folder, not from this PC's decoded one.
    with TemporaryDirectory() as raw, ExitStack() as stack:
        here = Path(raw) / "teacher" / "Documents" / "ClassInEDBMVP" / ".app_runtime"
        there = Path(raw) / "kim" / "OneDrive" / "문서" / "ClassInEDBMVP" / ".app_runtime"  # absent here
        lesson = {"session_name": "3월 모의고사", "output_dir": str(there / "outputs" / "march"),
                  "problems": [{"id": "p1"}]}
        with ExitStack() as home_pc:
            _runtime_patches(home_pc, there)
            home = app_server._session_history_with_session([], lesson, updated_at="2026-11-19T21:00:00+09:00")
        _runtime_patches(stack, here)
        here.mkdir(parents=True)
        (here / "session_history-HOME-PC.json").write_text(json.dumps(home, ensure_ascii=False), encoding="utf-8")
        newer = dict(lesson, problems=[{"id": "p1"}, {"id": "p2"}])
        app_server.persist_latest_session_with_history(newer, updated_at="2026-11-20T10:00:00+09:00")

        listed = app_server.load_session_history()
        assert [2] == [len(entry["session"]["problems"]) for entry in listed], \
            "one row, the newer one: the older row must not be openable"
        assert home[0]["id"] == listed[0]["id"], "same id on both PCs (R87 skips on either)"


# --- G2 / G11: a running server and the other PC's 새 수업 or save --------------------------------

@pytest.mark.parametrize("commit", ["import", "open recent"])
def test_first_lesson_after_no_lesson_file_is_still_closed_by_another_pcs_new_lesson(commit):
    with TemporaryDirectory() as raw, ExitStack() as stack:
        runtime = Path(raw)
        _runtime_patches(stack, runtime)
        server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)  # no lesson file
        handler, replies = _history_handler(server), []
        handler._send_json = lambda payload, **kwargs: replies.append(payload)
        try:
            v1 = {"session_name": "3월 모의고사", "output_dir": str(runtime / "outputs" / "quiz"),
                  "problems": [{"id": "p1", "title": "학교 v1"}]}
            app_server._refresh_session_problem_counts(v1)  # as the import and 최근 수업 → 열기 commit it
            # One commit, then the tab is closed: no later request looks at latest_session.json.
            if commit == "import":
                staging = runtime / "staging"
                staging.mkdir()
                committed = server.adopt_staged_publish_if_current(
                    None, v1, staging_dir=staging, final_dir=runtime / "outputs" / "quiz" / "g1",
                    expected_revision=0)
            else:
                committed = server.remember_session_if_current(None, v1, expected_revision=0)
            assert committed is not None
            # The other PC edits the lesson and clicks 새 수업: its 최근 수업 entry syncs, latest_session.json goes.
            v2 = dict(v1, problems=[{"id": "p1", "title": "집 v2"}])
            app_server.save_session_history(
                app_server._session_history_with_session(app_server.load_session_history(), v2))
            app_server.LATEST_SESSION_JSON.unlink()

            handler._handle_latest_session()  # next tab load
            assert replies[-1]["session"] is None
            assert not app_server.LATEST_SESSION_JSON.exists(), "the old copy must not be written back"
            assert ["집 v2"] == _titles(app_server.load_session_history()[0]["session"])
        finally:
            server.server_close()


def test_a_sync_during_a_tab_load_or_new_lesson_never_writes_the_old_copy_back():
    with TemporaryDirectory() as raw, ExitStack() as stack:
        runtime = Path(raw)
        _runtime_patches(stack, runtime)
        v1 = {"session_name": "3월 모의고사", "output_dir": str(runtime / "outputs" / "quiz"),
              "problems": [{"id": "p1", "title": "학교 v1"}]}
        app_server.persist_latest_session_with_history(v1)

        def other_pc_saves(lesson: dict, *, new_lesson: bool) -> None:
            app_server.save_session_history(
                app_server._session_history_with_session(app_server.load_session_history(), lesson))
            if new_lesson:
                app_server.LATEST_SESSION_JSON.unlink()
            else:
                app_server._atomic_write_text(app_server.LATEST_SESSION_JSON, json.dumps(lesson, ensure_ascii=False))

        server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
        handler, replies = _history_handler(server), []
        handler._send_json = lambda payload, **kwargs: replies.append(payload)
        try:
            # Tab load: the other PC's 새 수업 lands while this request walks the open lesson's files.
            real_walk, landed = app_server.collect_session_file_paths, []

            def walk(session):
                if not landed:
                    landed.append(True)
                    other_pc_saves(dict(v1, problems=[{"id": "p1", "title": "집 v2"}]), new_lesson=True)
                return real_walk(session)

            with patch.object(app_server, "collect_session_file_paths", side_effect=walk):
                handler._handle_latest_session()
            assert landed
            assert replies[-1]["session"] is None
            assert not app_server.LATEST_SESSION_JSON.exists(), "the old copy must not be written back"
            assert ["집 v2"] == _titles(app_server.load_session_history()[0]["session"])

            # 새 수업: the other PC's save lands after the handler checked the revision.
            reopened = dict(v1, session_name="다시 연 수업")
            revision = server.remember_session_if_current(
                None, reopened, expected_revision=replies[-1]["sessionRevision"])
            other_pc_saves(dict(reopened, problems=[{"id": "p1", "title": "집 v3"}]), new_lesson=False)
            with pytest.raises(app_server.SessionRevisionConflict):
                server.clear_session_with_revision(expected_revision=revision, keep_history=True)
            assert ["집 v3"] == _titles(json.loads(app_server.LATEST_SESSION_JSON.read_text(encoding="utf-8")))
            assert ["집 v3"] == _titles(app_server.load_session_history()[0]["session"])
        finally:
            server.server_close()


# --- G7: renaming a lesson that another PC's 새 수업 closed ---------------------------------------

def test_renaming_a_lesson_closed_on_another_pc_gets_the_korean_conflict():
    with TemporaryDirectory() as raw, ExitStack() as stack:
        runtime = Path(raw)
        _runtime_patches(stack, runtime)
        app_server.persist_latest_session_with_history(
            {"session_name": "3월 모의고사", "output_dir": str(runtime / "outputs" / "quiz"),
             "problems": [{"id": "p1", "title": "학교 v1"}]})
        server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
        try:
            _stale, stale_revision = server.session_snapshot_with_revision()
            app_server.LATEST_SESSION_JSON.unlink()  # the other PC's 새 수업, synced
            handler, replies = object.__new__(app_server.AppRequestHandler), []
            handler.server = server
            handler._read_json_body = lambda: {
                "action": "session-name", "sessionName": "새 이름",
                "expectedSessionRevision": stale_revision, "expectedSessionEpoch": server.session_epoch(),
            }
            handler._send_json = lambda payload, **kwargs: replies.append((kwargs.get("status", HTTPStatus.OK), payload))
            handler._handle_session_mutate()
            status, payload = replies[-1]
            assert status == HTTPStatus.CONFLICT
            assert payload["code"] == "session_conflict" and payload["session"] is None
            assert any("가" <= character <= "힣" for character in payload["error"])
            assert not app_server.LATEST_SESSION_JSON.exists()
        finally:
            server.server_close()


# --- G5: Tesseract OCR text as a name -------------------------------------------------------------

def test_ocr_text_is_never_a_problem_name():
    page_id = "a" * 64 + "_스캔_0123456789-page-001"
    ocr_text = "다음\n글\n을\n읽\n고\n.\n13."  # TesseractOCRBackend puts one word on each line
    assert board._normalize_problem_title(ocr_text, 4, page_id) == "문항 05"
    assert board._normalize_problem_title(ocr_text, 4, page_id, 13) == "문항 13"
    assert board._normalize_problem_title("이차함수", 0, page_id) == "이차함수"


# --- G9: Windows PowerShell 5.1 reads a BOM-less script in the ANSI code page ----------------------

def test_non_ascii_powershell_scripts_start_with_a_utf8_bom():
    # On English Windows (cp1252) Korean bytes turn into quote characters and the script stops parsing.
    scripts = [*ROOT.glob("*.ps1"), *(ROOT / "scripts").glob("*.ps1")]
    assert ROOT / "package_windows_installer.ps1" in scripts
    for script in scripts:
        data = script.read_bytes()
        assert data.isascii() or data.startswith(b"\xef\xbb\xbf"), script.name


# --- G15: data folder moved off a network share -----------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="only Windows reads \\\\host\\share paths as network paths")
def test_data_folder_moved_off_a_network_share_keeps_its_lessons(tmp_path):
    # Documents used to be redirected to a school server; the data folder now lives on this PC.
    home = tmp_path.resolve() / "Documents" / "ClassInEDBMVP"
    runtime = home / ".app_runtime"
    crop = _write(runtime / "outputs" / "previews" / "quiz1_run" / "crops" / "p1.png")
    _age_tree(home)
    old_run = r"\\fileserver.invalid\home\teacher\Documents\ClassInEDBMVP\.app_runtime\outputs\previews\quiz1_run"
    lesson = {"session_name": "1단원", "output_dir": old_run,
              "problems": [{"id": "p1", "imagePath": "file:" + old_run.replace("\\", "/") + "/crops/p1.png"}]}
    touched = []

    def recording(real):
        def fake(path, *args, **kwargs):
            if str(path).startswith("\\\\"):
                touched.append(str(path))
                raise OSError(0, "network path stubbed", str(path), 53)
            return real(path, *args, **kwargs)
        return fake

    with ExitStack() as stack:
        _runtime_patches(stack, runtime)
        stack.enter_context(patch.object(app_server, "BASE_DIR", home))
        for module, name in ((os, "stat"), (os.path, "exists"), (ntpath, "_getfinalpathname")):
            stack.enter_context(patch.object(module, name, recording(getattr(module, name))))
        assert app_server.plan_stale_work_folders(
            output_root=runtime / "outputs", active_session=lesson,
            history=[{"id": "x", "session": lesson}], now=time.time(),
        ) == [], "startup cleanup must not delete the open lesson's folder"
        assert app_server.decode_file_reference(lesson["problems"][0]["imagePath"]) == crop
        # A share path with no copy here stays a network path.
        missing = old_run.replace("quiz1_run", "gone_run")
        assert str(app_server.decode_file_reference(missing)) == missing
    assert touched == [], "the old share is never opened"


# --- G18 / G19: error cards -----------------------------------------------------------------------

def test_error_cards_name_the_teachers_file_without_the_upload_hash(tmp_path):
    hwp = ValueError("HWP/HWPX conversion failed. Install LibreOffice with HWP support, or convert the HWP file to PDF first.")
    hwp.source_file = app_server._managed_upload_target_name("학습지.hwp", "a" * 64, upload_dir=tmp_path)
    payload = app_server._export_error_payload(hwp)
    assert payload["code"] == "hwp_conversion_failed"
    assert payload["sourceFile"] == payload["fileName"] == "학습지.hwp"

    pdf = tmp_path / app_server._managed_upload_target_name("3월 모의고사.pdf", "c" * 64, upload_dir=tmp_path)
    payload = app_server._export_error_payload(preprocess.PdfPageProcessingError(pdf, 3, 5, MemoryError()))
    assert payload["code"] == "processing_memory_exhausted"
    assert payload["sourceFile"] == payload["fileName"] == "3월 모의고사.pdf"
    assert app_server._lesson_name_from_sources([pdf]) == "3월 모의고사", "the lesson and the card read the same"

    plain = ValueError("HWP/HWPX conversion failed.")
    plain.source_file = "plain_0123456789.hwp"  # no managed prefix: the name is the teacher's own
    assert app_server._export_error_payload(plain)["sourceFile"] == "plain_0123456789.hwp"


@pytest.mark.parametrize("filename", ["a" * 64 + "_빈쪽_91abbf16be.pdf", "out of memory.pdf"])
def test_pdf_with_no_pages_is_reported_as_unreadable_pdf_in_korean(tmp_path, filename):
    source = tmp_path / filename
    source.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                       b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    with pytest.raises(ValueError) as caught:
        preprocess.render_pdf_pages(source, tmp_path / "rendered")
    payload = app_server._export_error_payload(caught.value)
    assert payload["code"] == "pdf_invalid_document"
    assert "no pages" not in payload["error"] and filename not in payload["error"]
    assert "has no pages" in payload["detail"]


# --- G24: where the packaged app keeps API keys ---------------------------------------------------

def test_packaging_doc_never_puts_api_keys_in_the_runtime_folder():
    # R47 moved user_settings.json out of Documents/.app_runtime into the local app folder.
    packaging = (ROOT / "PACKAGING_MVP.md").read_text(encoding="utf-8")
    assert not [line for line in packaging.splitlines() if "API key" in line and "runtime folder" in line]


# --- G25: the browser leaves mid-reply ------------------------------------------------------------

def test_browser_leaving_mid_reply_closes_without_a_traceback(capsys):
    handler, responses = _handler("/api/session/publish-plan")

    def left_mid_reply():
        handler._response_started = True  # the reply had started when the browser closed the socket
        raise ConnectionAbortedError(10053, "aborted by the host")

    handler._handle_session_publish_plan = left_mid_reply
    handler.do_POST()
    assert responses == [] and handler.close_connection is True
    assert "[operation-error]" not in capsys.readouterr().err
    # A ConnectionError before any reply (an outbound call) is still logged.
    handler, responses = _handler("/api/session/publish-plan")
    handler._handle_session_publish_plan = lambda: (_ for _ in ()).throw(ConnectionRefusedError(10061, "refused"))
    handler.do_POST()
    assert responses == [] and handler.close_connection is True
    assert "[operation-error] POST /api/session/publish-plan: ConnectionRefusedError" in capsys.readouterr().err


# --- G27: a source run renders HWP/HWPX with rhwp-python in a subprocess -----------------------------

def test_runtime_diagnostics_treats_source_run_rhwp_python_as_hangul_ready():
    # 'blocked' would make the UI drop HWP files that would open (R39).
    with (
        patch.object(app_server.preprocess, "_iter_hwp_pdf_converter_commands", return_value=[]),
        patch.object(app_server.preprocess, "_iter_hwp_hwpx_converter_commands", return_value=[]),
        patch.object(app_server.preprocess, "_iter_pyhwp_html_converter_commands", return_value=[]),
        patch.object(app_server.preprocess, "_iter_hwp_text_converter_commands", return_value=[]),
        patch.object(app_server.preprocess, "_iter_chrome_pdf_commands", return_value=[]),
        patch.object(app_server.preprocess, "_iter_rhwp_core_renderer_commands", return_value=[]),
        patch.object(app_server.preprocess, "_bundled_rhwp", return_value=None),
        patch.object(app_server.preprocess, "_iter_rhwp_python_renderer_commands",
                     return_value=[["/venv/bin/python", "-c", "script"]]),
    ):
        hangul = app_server.describe_runtime_diagnostics()["hangul"]
    assert hangul["status"] == "ready"
    assert hangul["hwpRendererReady"]
    assert hangul["recommendedActions"] == [], "a working renderer must not tell teachers to save as PDF"
