from __future__ import annotations

import gzip
import io
import json
import struct
import unittest
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_server
from edb_builder import TextRecordSpec, build_edb, build_text_record


class TestGeneratedEdbValidation(unittest.TestCase):
    def _payload(self, *, terminal_eof_plus_one=True):
        records = [build_text_record(TextRecordSpec(index, f"Problem {index + 1}", 0, index / 50, 0.3)) for index in range(2)]
        return build_edb(records, header_flag=3, terminal_eof_plus_one=terminal_eof_plus_one)

    def _validate_inner(self, inner):
        with TemporaryDirectory() as raw:
            target = Path(raw) / "generated.edb"
            payload = self._payload()
            target.write_bytes(payload[:app_server.OUTER_EDB_PREFIX_LEN] + gzip.compress(inner))
            return app_server.validate_edb_file(target)

    def test_rejects_record_count_mismatch_even_above_requested_minimum(self):
        inner = bytearray(gzip.decompress(self._payload()[app_server.OUTER_EDB_PREFIX_LEN:]))
        struct.pack_into(">H", inner, 2, 3)
        with self.assertRaisesRegex(ValueError, "record.*count|record.*hint"):
            self._validate_inner(inner)

    def test_rejects_invalid_later_record_size_instead_of_accepting_prefix(self):
        inner = bytearray(gzip.decompress(self._payload()[app_server.OUTER_EDB_PREFIX_LEN:]))
        record_offset = 17 + inner[16] + 17
        second_offset = record_offset + struct.unpack_from(">I", inner, record_offset)[0]
        struct.pack_into(">I", inner, second_offset, 0)
        with self.assertRaisesRegex(ValueError, "record"):
            self._validate_inner(inner)

    def test_rejects_trailing_fragment_after_complete_records(self):
        inner = gzip.decompress(self._payload(terminal_eof_plus_one=False)[app_server.OUTER_EDB_PREFIX_LEN:])
        with self.assertRaisesRegex(ValueError, "record|trailing"):
            self._validate_inner(inner + b"\x00\x00")

    def test_accepts_exact_and_classin_terminal_size_conventions(self):
        for terminal_eof_plus_one in (False, True):
            with self.subTest(terminal_eof_plus_one=terminal_eof_plus_one):
                inner = gzip.decompress(self._payload(terminal_eof_plus_one=terminal_eof_plus_one)[app_server.OUTER_EDB_PREFIX_LEN:])
                self.assertEqual(2, self._validate_inner(inner)["recordCountActual"])


class TestPreviewFailureCleanup(unittest.TestCase):
    def _handler(self, root):
        handler = object.__new__(app_server.AppRequestHandler)
        handler._current_session_state = lambda: (None, None)
        handler._read_json_body = lambda: {"preview": True, "exportEdb": False, "ocr": "none"}
        handler._resolve_source_paths = lambda _payload, **_kwargs: [root / "source.png"]
        handler._resolve_preview_output_dir = lambda _payload, _sources: root / "preview"
        responses = []
        handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
        return handler, responses

    def test_failed_preview_generation_removes_only_its_partial_output(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            active = root / "active.edb"
            active.write_bytes(b"previous-success")
            handler, responses = self._handler(root)

            def fail_export(_source, **kwargs):
                output = Path(kwargs["output_dir"])
                output.mkdir(parents=True, exist_ok=True)
                (output / "partial.png").write_bytes(b"partial-image")
                raise OSError("disk full")

            with patch.object(app_server, "run_problem_export", side_effect=fail_export):
                handler._handle_export()

            self.assertEqual(HTTPStatus.INSUFFICIENT_STORAGE, responses[0][1]["status"])
            self.assertEqual("runtime_storage_full", responses[0][0]["code"])
            self.assertFalse((root / "preview").exists())
            self.assertEqual(b"previous-success", active.read_bytes())

    def test_invalid_preview_edb_is_removed_before_error_response(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            handler, responses = self._handler(root)

            def invalid_export(_source, **kwargs):
                output = Path(kwargs["output_dir"])
                output.mkdir(parents=True, exist_ok=True)
                invalid_edb = output / "invalid.edb"
                invalid_edb.write_bytes(b"broken")
                return {"ui_session": {"pages": [], "problems": []}, "edb_path": invalid_edb, "summary": {"placements": []}}

            with patch.object(app_server, "run_problem_export", side_effect=invalid_export):
                handler._handle_export()

            self.assertEqual(HTTPStatus.INTERNAL_SERVER_ERROR, responses[0][1]["status"])
            self.assertFalse((root / "preview").exists())

    def test_preview_directory_collision_keeps_existing_artifacts(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            preview = root / "preview"
            preview.mkdir()
            sentinel = preview / "existing.edb"
            sentinel.write_bytes(b"previous-success")
            handler, responses = self._handler(root)

            with patch.object(app_server, "run_problem_export") as generate:
                handler._handle_export()

            generate.assert_not_called()
            self.assertEqual(HTTPStatus.CONFLICT, responses[0][1]["status"])
            self.assertEqual("output_directory_exists", responses[0][0]["code"])
            self.assertEqual(b"previous-success", sentinel.read_bytes())


class TestGenerationRequestCompleteness(unittest.TestCase):
    def test_incomplete_body_cannot_start_an_export_from_valid_json_prefix(self):
        for body in (b"", b"{}", b'{"preview":true}'):
            with self.subTest(body=body):
                handler = object.__new__(app_server.AppRequestHandler)
                handler.headers = {"Content-Length": str(len(body) + 10)}
                handler.rfile = io.BytesIO(body)
                with self.assertRaisesRegex(json.JSONDecodeError, "incomplete|truncated"):
                    handler._read_json_body()

    def test_export_rejects_incomplete_request_before_running_generation(self):
        body = b'{"preview":true}'
        handler = object.__new__(app_server.AppRequestHandler)
        handler.headers = {"Content-Length": str(len(body) + 10)}
        handler.rfile = io.BytesIO(body)
        handler._current_session_state = lambda: (None, None)
        responses = []
        handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

        with patch.object(app_server, "run_problem_export") as generate:
            handler._handle_export()

        generate.assert_not_called()
        self.assertEqual(HTTPStatus.BAD_REQUEST, responses[0][1]["status"])
        self.assertFalse(responses[0][0]["ok"])


if __name__ == "__main__":
    unittest.main()
