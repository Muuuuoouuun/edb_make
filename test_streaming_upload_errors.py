from __future__ import annotations

import errno
import hashlib
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

import pytest

import app_server


def test_single_problem_classin_limit_has_specific_recovery():
    payload = app_server._export_error_payload(ValueError("Problem 'p1' exceeds ClassIn's 50-page limit (61.0 pages)"))
    assert payload["code"] == "single_problem_too_tall"
    assert "50페이지" in payload["error"]
    assert any("페이지 전체 넣기" in step for step in payload["recoverySteps"])
    assert payload["retryable"] is False


@pytest.mark.parametrize("stage", ["prepare", "build", "write", "commit"])
def test_publish_error_retains_actual_resource_or_program_cause(stage):
    memory = app_server._publish_stage_failure_payload(stage, MemoryError("allocation failed"))
    assert memory["code"] == "processing_memory_exhausted"
    assert memory["operation"] == "session_publish"
    assert "MemoryError" in memory["detail"]
    program = app_server._publish_stage_failure_payload(stage, RuntimeError("renderer invariant failed"))
    assert program["code"] == "app_internal_error"
    assert "renderer invariant failed" in program["detail"]


class GeneratedBody:
    def __init__(self, size: int) -> None:
        self.remaining = size
        self.largest_read = 0

    def read(self, size: int = -1) -> bytes:
        assert 0 <= size <= app_server.UPLOAD_STREAM_CHUNK_BYTES
        self.largest_read = max(size, self.largest_read)
        count = min(size, self.remaining)
        self.remaining -= count
        return b"x" * count


def handler_for(data: bytes = b"pdf contents", *, length: int | str | None = None, filename: str = "한글 자료 with spaces.pdf"):
    handler = object.__new__(app_server.AppRequestHandler)
    handler.path = "/api/uploads"
    handler.headers = {
        "Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/octet-stream", "X-File-Name": quote(filename),
        "Content-Length": str(len(data) if length is None else length),
    }
    handler.rfile = io.BytesIO(data)
    handler.close_connection = False
    responses = []
    handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
    return handler, responses


def call_upload(handler):
    handler.do_POST()


def test_streaming_accepts_binary_larger_than_legacy_json_limit_with_bounded_reads(tmp_path):
    size = app_server.MAX_JSON_BODY_BYTES + 1
    handler, responses = handler_for(length=size)
    stream = GeneratedBody(size)
    handler.rfile = stream
    with (
        patch.object(app_server, "UPLOAD_DIR", tmp_path),
        patch.object(app_server.base64, "b64decode", side_effect=AssertionError("base64 used")),
        patch.object(handler, "_read_json_body", side_effect=AssertionError("JSON body read")),
    ):
        call_upload(handler)
    payload, status = responses[0]
    assert status["status"] == app_server.HTTPStatus.CREATED
    path = Path(payload["sourcePath"])
    assert path.stat().st_size == size
    assert stream.remaining == 0
    assert stream.largest_read <= 1024 * 1024
    assert payload["sizeBytes"] == size
    assert payload["fileName"] == "한글 자료 with spaces.pdf"
    assert path.parent == tmp_path
    assert not list(tmp_path.glob(".*.tmp"))


def test_streamed_upload_digest_deduplicates_and_never_adds_session_allowed_files(tmp_path):
    first, responses = handler_for()
    first.server = SimpleNamespace(allowed_files=set(), latest_session={"id": "preserve"})
    second, second_responses = handler_for(filename="another.pdf")
    second.server = first.server
    with patch.object(app_server, "UPLOAD_DIR", tmp_path):
        call_upload(first)
        call_upload(second)
    assert responses[0][0]["sourcePath"] == second_responses[0][0]["sourcePath"]
    assert responses[0][0]["sha256"] == hashlib.sha256(b"pdf contents").hexdigest()
    assert first.server.allowed_files == set()
    assert first.server.latest_session == {"id": "preserve"}
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("value", ["", "-1", "invalid", "9" * 100])
def test_invalid_length_is_rejected_before_reading_or_creating_file(tmp_path, value):
    handler, responses = handler_for(length=value)
    handler.rfile = SimpleNamespace(read=lambda *_: pytest.fail("body was read"))
    with patch.object(app_server, "UPLOAD_DIR", tmp_path):
        call_upload(handler)
    assert responses[0][0]["code"] == "upload_invalid_length"
    assert handler.close_connection
    assert not list(tmp_path.iterdir())


def test_oversized_file_is_explicit_and_rejected_before_read(tmp_path):
    handler, responses = handler_for(length=app_server.MAX_UPLOAD_FILE_BYTES + 1)
    handler.rfile = SimpleNamespace(read=lambda *_: pytest.fail("oversize body read"))
    with patch.object(app_server, "UPLOAD_DIR", tmp_path):
        call_upload(handler)
    payload, response = responses[0]
    assert response["status"] == app_server.HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert payload["code"] == "upload_file_too_large"
    assert payload["receivedBytes"] > payload["maxBytes"]
    assert "512.0 MB" in payload["error"]
    assert payload["sourceFile"] == "한글 자료 with spaces.pdf"
    assert not list(tmp_path.iterdir())


def test_truncated_upload_cleans_partial_file_and_preserves_existing_files(tmp_path):
    sentinel = tmp_path / "existing.pdf"
    sentinel.write_bytes(b"preserve")
    handler, responses = handler_for(data=b"short", length=100)
    with patch.object(app_server, "UPLOAD_DIR", tmp_path):
        call_upload(handler)
    assert responses[0][0]["code"] == "upload_incomplete"
    assert responses[0][0]["receivedBytes"] == 5
    assert sentinel.read_bytes() == b"preserve"
    assert list(tmp_path.iterdir()) == [sentinel]


def test_stream_rejects_cross_origin_and_invalid_type_before_body_read(tmp_path):
    for updates, expected in (
        ({"Origin": "https://example.com"}, "cross_origin_rejected"),
        ({"X-File-Name": "unsafe.exe"}, "upload_unsupported_type"),
        ({"Transfer-Encoding": "chunked"}, "upload_invalid_length"),
        ({"Content-Type": "text/plain"}, "upload_invalid_content_type"),
    ):
        handler, responses = handler_for()
        handler.headers.update(updates)
        handler.rfile = SimpleNamespace(read=lambda *_: pytest.fail("untrusted body read"))
        with patch.object(app_server, "UPLOAD_DIR", tmp_path):
            call_upload(handler)
        assert responses[0][0]["code"] == expected
        assert not list(tmp_path.iterdir())


def test_disk_space_preflight_has_actual_remaining_and_required_bytes(tmp_path):
    handler, responses = handler_for()
    with patch.object(app_server, "UPLOAD_DIR", tmp_path), patch.object(app_server.shutil, "disk_usage", return_value=SimpleNamespace(free=20)):
        call_upload(handler)
    payload, response = responses[0]
    assert response["status"] == app_server.HTTPStatus.INSUFFICIENT_STORAGE
    assert payload["code"] == "runtime_storage_full"
    assert payload["availableBytes"] == 20
    assert payload["requiredBytes"] > 20
    assert "남은 공간" in payload["error"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("exception,code", [(MemoryError("allocation failed"), "processing_memory_exhausted"), (OSError(errno.ENOSPC, "disk full"), "runtime_storage_full")])
def test_mid_stream_resource_failures_are_classified_and_cleanup_temp(tmp_path, exception, code):
    handler, responses = handler_for()
    handler.rfile = SimpleNamespace(read=lambda *_: (_ for _ in ()).throw(exception))
    with patch.object(app_server, "UPLOAD_DIR", tmp_path):
        call_upload(handler)
    assert responses[0][0]["code"] == code
    assert not list(tmp_path.iterdir())


def test_persistent_windows_lock_uses_fresh_path_without_replacing_protected_file(tmp_path):
    data = b"pdf contents"
    digest = hashlib.sha256(data).hexdigest()
    protected = tmp_path / app_server._managed_upload_target_name("한글 자료 with spaces.pdf", digest, upload_dir=tmp_path)
    protected.write_bytes(b"old protected data")
    handler, responses = handler_for()
    original = app_server._replace_file_with_retry
    error = PermissionError(errno.EACCES, "locked")
    error.winerror = 32
    attempts = []
    def replace(source, target):
        attempts.append(Path(target))
        if Path(target) == protected:
            raise error
        original(source, target)
    with patch.object(app_server, "UPLOAD_DIR", tmp_path), patch.object(app_server, "_replace_file_with_retry", side_effect=replace):
        call_upload(handler)
    payload = responses[0][0]
    assert payload["ok"]
    assert protected.read_bytes() == b"old protected data"
    assert Path(payload["sourcePath"]).read_bytes() == data
    assert Path(payload["sourcePath"]) != protected
    assert not list(tmp_path.glob(".*.tmp"))


def test_export_source_capacity_counts_unique_files_and_rejects_batch_limit(tmp_path):
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    first.write_bytes(b"111")
    second.write_bytes(b"222")
    with patch.object(app_server, "MAX_UPLOAD_FILE_BYTES", 4), patch.object(app_server, "MAX_UPLOAD_BATCH_BYTES", 5):
        app_server._validate_source_capacity([first, first])
        with pytest.raises(app_server.UploadRequestError) as caught:
            app_server._validate_source_capacity([first, second])
    assert caught.value.code == "upload_batch_too_large"
    assert caught.value.context["receivedBytes"] == 6


@pytest.mark.parametrize("cause,expected", [
    (MemoryError("out of memory"), "processing_memory_exhausted"),
    (OSError(errno.ENOMEM, "allocation failed"), "processing_memory_exhausted"),
    (OSError(errno.ENOSPC, "disk full"), "runtime_storage_full"),
    (PermissionError(errno.EACCES, "access denied"), "runtime_storage_access_denied"),
    (RuntimeError("unexpected renderer logic failure"), "pdf_page_processing_failed"),
])
def test_wrapped_pdf_failures_keep_true_cause_and_page_context(cause, expected):
    class PageError(RuntimeError):
        code = "pdf_page_processing_failed"
        source_file = "수업.pdf"
        page_start = 21
        page_end = 23
    error = PageError("pages 21-23 could not render")
    error.cause = cause
    payload = app_server._export_error_payload(error)
    assert payload["code"] == expected
    assert payload["sourceFile"] == "수업.pdf"
    assert payload["pageStart"] == 21 and payload["pageEnd"] == 23
    assert "pages 21-23" in payload["detail"]


@pytest.mark.parametrize("exception,code", [
    (RuntimeError("bundled PyMuPDF: DLL import failed"), "pdf_renderer_unavailable"),
    (ValueError("Failed to open PDF document x.pdf: broken xref"), "pdf_invalid_document"),
    (ValueError("PDF document is password protected: x.pdf"), "pdf_password_protected"),
    (RuntimeError("bug in layout code"), "app_internal_error"),
    (ValueError("unsupported processing option"), "invalid_input"),
])
def test_document_and_app_errors_are_not_misreported_as_file_too_large(exception, code):
    payload = app_server._export_error_payload(exception)
    assert payload["code"] == code
    assert payload["error"]
    assert payload["detail"]
    assert payload["recoverySteps"]
    assert "too_large" not in payload["code"]


@pytest.mark.parametrize("filename", [
    "ordinary.pdf", "out of memory.pdf", "password protected.pdf",
    "bundled PyMuPDF.pdf", "exceeds ClassIn's 50-page limit.pdf",
])
def test_corrupt_pdf_filename_does_not_override_document_error(tmp_path, filename):
    import preprocess

    source = tmp_path / filename
    source.write_bytes(b"not a PDF")
    with pytest.raises(ValueError) as caught:
        preprocess.render_pdf_pages(source, tmp_path / "rendered")
    payload = app_server._export_error_payload(caught.value)
    assert payload["code"] == "pdf_invalid_document"
    assert filename in payload["detail"]


def test_known_source_name_does_not_turn_program_bug_into_capacity_or_password_error():
    class PageError(RuntimeError):
        code = "pdf_page_processing_failed"
        source_file = "out of memory password protected bundled pymupdf.pdf"
        page_start = 2
        page_end = 2

    error = PageError(f"Failed to process {PageError.source_file}: invalid renderer state")
    error.cause = RuntimeError("invalid renderer state")
    payload = app_server._export_error_payload(error)
    assert payload["code"] == "pdf_page_processing_failed"
    assert payload["sourceFile"] == error.source_file


def test_missing_file_with_disk_full_in_filename_is_not_disk_exhaustion(tmp_path):
    error = FileNotFoundError(errno.ENOENT, "No such file or directory", str(tmp_path / "disk full.pdf"))
    payload = app_server._export_error_payload(error)
    assert payload["code"] == "input_file_missing"


def test_true_memory_and_password_errors_still_win_with_misleading_source_names(tmp_path):
    memory = MemoryError("out of memory")
    memory.source_file = "password protected.pdf"
    assert app_server._export_error_payload(memory)["code"] == "processing_memory_exhausted"

    password = ValueError("PDF document is password protected: out of memory.pdf")
    password.source_file = "out of memory.pdf"
    assert app_server._export_error_payload(password)["code"] == "pdf_password_protected"


def test_zero_length_and_exact_file_limit(tmp_path):
    empty, empty_responses = handler_for(data=b"")
    accepted, accepted_responses = handler_for(data=b"1234")
    rejected, rejected_responses = handler_for(data=b"12345")
    with patch.object(app_server, "UPLOAD_DIR", tmp_path), patch.object(app_server, "MAX_UPLOAD_FILE_BYTES", 4):
        call_upload(empty)
        call_upload(accepted)
        call_upload(rejected)
    assert empty_responses[0][0]["code"] == "upload_empty_file"
    assert accepted_responses[0][0]["ok"]
    assert rejected_responses[0][0]["code"] == "upload_file_too_large"


def test_connection_reset_is_transfer_failure_not_storage_error(tmp_path):
    handler, responses = handler_for()
    handler.rfile = SimpleNamespace(read=lambda *_: (_ for _ in ()).throw(ConnectionResetError("client disconnected")))
    with patch.object(app_server, "UPLOAD_DIR", tmp_path):
        call_upload(handler)
    assert responses[0][0]["code"] == "upload_incomplete"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("stage,exception,expected_code", [
    ("mkdir", PermissionError(errno.EACCES, "upload folder denied"), "runtime_storage_access_denied"),
    ("disk_usage", OSError(errno.EIO, "disk statistics unavailable"), "runtime_storage_error"),
    ("disk_usage", MemoryError("allocation failed"), "processing_memory_exhausted"),
    ("read", MemoryError("allocation failed"), "processing_memory_exhausted"),
])
def test_upload_resource_failures_include_only_sanitized_selected_filename(tmp_path, stage, exception, expected_code):
    handler, responses = handler_for(filename="../private/수업 자료.pdf")
    with patch.object(app_server, "UPLOAD_DIR", tmp_path):
        if stage == "mkdir":
            with patch.object(Path, "mkdir", side_effect=exception):
                call_upload(handler)
        elif stage == "disk_usage":
            with patch.object(app_server.shutil, "disk_usage", side_effect=exception):
                call_upload(handler)
        else:
            handler.rfile = SimpleNamespace(read=lambda *_: (_ for _ in ()).throw(exception))
            call_upload(handler)
    payload = responses[0][0]
    assert payload["code"] == expected_code
    assert payload["sourceFile"] == "수업 자료.pdf"
    assert payload["fileName"] == "수업 자료.pdf"
    assert "private" not in payload["sourceFile"]
    assert not list(tmp_path.iterdir())


def test_invalid_upload_name_is_rejected_without_reflecting_unsafe_input(tmp_path):
    handler, responses = handler_for(filename="private\n.pdf")
    handler.rfile = SimpleNamespace(read=lambda *_: pytest.fail("invalid name body was read"))
    with patch.object(app_server, "UPLOAD_DIR", tmp_path):
        call_upload(handler)
    payload = responses[0][0]
    assert payload["code"] == "upload_invalid_name"
    assert "sourceFile" not in payload
    assert "private" not in payload["error"]
    assert not list(tmp_path.iterdir())


def test_real_http_binary_upload_returns_reference_and_large_file_error(tmp_path):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    with patch.object(app_server, "UPLOAD_DIR", tmp_path), patch.object(app_server, "MAX_UPLOAD_FILE_BYTES", 16):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        address = f"127.0.0.1:{server.server_port}"
        try:
            for body, expected_status in ((b"%PDF test", 201), (b"x" * 17, 413)):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    connection.request("POST", "/api/uploads", body=body, headers={
                        "Content-Type": "application/octet-stream",
                        "X-File-Name": quote("테스트 자료.pdf"),
                        "Origin": f"http://{address}",
                    })
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    assert response.status == expected_status
                    if expected_status == 201:
                        assert Path(payload["sourcePath"]).read_bytes() == body
                        assert payload["fileName"] == "테스트 자료.pdf"
                    else:
                        assert payload["code"] == "upload_file_too_large"
                        assert payload["receivedBytes"] == 17
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
