"""Atomic writes must survive Windows cache locks without destroying old data."""
import base64
import ctypes
import errno
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

import app_server


def windows_error(code=5):
    error = PermissionError(errno.EACCES, "Access denied")
    error.winerror = code
    return error


class TestAtomicWindowsWrites(unittest.TestCase):
    def test_transient_lock_retries_without_publishing_partial_bytes(self):
        with TemporaryDirectory() as raw:
            target = Path(raw) / "문서.pdf"
            target.write_bytes(b"old")
            replace = os.replace
            attempts = []

            def locked_once(source, destination):
                attempts.append(Path(source))
                self.assertEqual(b"old", target.read_bytes())
                if len(attempts) == 1:
                    raise windows_error(32)
                replace(source, destination)

            with patch.object(app_server.os, "replace", side_effect=locked_once), patch.object(app_server.time, "sleep") as sleep:
                app_server._atomic_write_bytes(target, b"complete new data")
            self.assertEqual(b"complete new data", target.read_bytes())
            self.assertEqual(attempts[0], attempts[1])
            sleep.assert_called_once_with(0.05)
            self.assertEqual([], list(Path(raw).glob(".*.tmp")))

    def test_persistent_lock_preserves_original_and_cleans_temp(self):
        with TemporaryDirectory() as raw:
            target = Path(raw) / "session.json"
            target.write_text("old", encoding="utf-8")
            with patch.object(app_server.os, "replace", side_effect=windows_error()) as replace, patch.object(app_server.time, "sleep") as sleep:
                with self.assertRaises(PermissionError):
                    app_server._atomic_write_text(target, "new")
            self.assertEqual(6, replace.call_count)
            self.assertEqual(5, sleep.call_count)
            self.assertEqual("old", target.read_text(encoding="utf-8"))
            self.assertEqual([], list(Path(raw).glob(".*.tmp")))

    def test_non_lock_errors_fail_immediately(self):
        with TemporaryDirectory() as raw:
            with patch.object(app_server.os, "replace", side_effect=OSError(errno.ENOSPC, "disk full")) as replace, patch.object(app_server.time, "sleep") as sleep:
                with self.assertRaises(OSError):
                    app_server._atomic_write_bytes(Path(raw) / "upload.pdf", b"data")
            replace.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual([], list(Path(raw).iterdir()))

    def test_zip_replace_also_retries(self):
        with TemporaryDirectory() as raw:
            target = Path(raw) / "export.zip"
            replace = os.replace
            with patch.object(app_server.os, "replace", side_effect=[windows_error(33), None]) as mocked, patch.object(app_server.time, "sleep"):
                # Second attempt performs the actual atomic rename.
                def commit(source, destination):
                    if mocked.call_count == 1:
                        raise windows_error(33)
                    replace(source, destination)
                mocked.side_effect = commit
                app_server._write_zip_atomically(target, compression=zipfile.ZIP_STORED, populate=lambda archive: archive.writestr("ok.txt", "complete"))
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(b"complete", archive.read("ok.txt"))


class TestLockedUploadCache(unittest.TestCase):
    def payload(self):
        return {"fileName": "한글 원문.pdf", "fileDataBase64": base64.b64encode(b"valid new upload").decode("ascii")}

    def test_locked_cache_uses_new_digest_path_and_preserves_old_entry(self):
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            digest = hashlib.sha256(b"valid new upload").hexdigest()
            cached = directory / app_server._managed_upload_target_name("한글 원문.pdf", digest, upload_dir=directory)
            cached.write_bytes(b"old corrupt entry")
            replace = os.replace

            def lock_canonical(source, destination):
                if Path(destination) == cached:
                    raise windows_error()
                replace(source, destination)

            handler = object.__new__(app_server.AppRequestHandler)
            with patch.object(app_server, "UPLOAD_DIR", directory), patch.object(app_server.os, "replace", side_effect=lock_canonical), patch.object(app_server.time, "sleep"):
                result = handler._save_uploaded_file(self.payload())
                reused = handler._save_uploaded_file(self.payload())
            self.assertNotEqual(cached, result)
            self.assertEqual(result, reused)
            self.assertTrue(result.name.startswith(digest + "_"))
            self.assertEqual(".pdf", result.suffix)
            self.assertEqual(b"valid new upload", result.read_bytes())
            self.assertEqual(b"old corrupt entry", cached.read_bytes())
            self.assertEqual([], list(directory.glob(".*.tmp")))

    def test_concurrent_success_is_verified_and_reused(self):
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            handler = object.__new__(app_server.AppRequestHandler)
            def concurrent_write(target, payload):
                target.write_bytes(payload)
                raise windows_error()
            with patch.object(app_server, "UPLOAD_DIR", directory), patch.object(app_server, "_atomic_write_bytes", side_effect=concurrent_write) as writer:
                result = handler._save_uploaded_file(self.payload())
            writer.assert_called_once()
            self.assertEqual(b"valid new upload", result.read_bytes())

    def test_directory_denial_is_bounded_and_not_hidden(self):
        with TemporaryDirectory() as raw:
            with patch.object(app_server, "UPLOAD_DIR", Path(raw)), patch.object(app_server.os, "replace", side_effect=windows_error()) as replace, patch.object(app_server.time, "sleep"):
                with self.assertRaises(PermissionError):
                    object.__new__(app_server.AppRequestHandler)._save_uploaded_file(self.payload())
            self.assertEqual(12, replace.call_count)
            self.assertEqual([], list(Path(raw).iterdir()))

    def test_access_error_has_specific_recovery_code(self):
        result = app_server._export_error_payload(windows_error())
        self.assertEqual("runtime_storage_access_denied", result["code"])
        self.assertEqual("storage_access_denied", result["errorKind"])
        self.assertTrue(result["retryable"])
        self.assertEqual(3, len(result["recoverySteps"]))

    @unittest.skipUnless(os.name == "nt", "real Windows file sharing semantics")
    def test_real_windows_handle_blocks_canonical_replacement_only(self):
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            digest = hashlib.sha256(b"valid new upload").hexdigest()
            cached = directory / app_server._managed_upload_target_name("한글 원문.pdf", digest, upload_dir=directory)
            cached.write_bytes(b"locked old entry")
            handle = create(str(cached), 0x80000000, 1, None, 3, 0x80, None)
            self.assertNotEqual(ctypes.c_void_p(-1).value, handle)
            try:
                with patch.object(app_server, "UPLOAD_DIR", directory), patch.object(app_server.time, "sleep"):
                    result = object.__new__(app_server.AppRequestHandler)._save_uploaded_file(self.payload())
                self.assertNotEqual(cached, result)
                self.assertEqual(b"valid new upload", result.read_bytes())
                self.assertEqual(b"locked old entry", cached.read_bytes())
            finally:
                kernel32.CloseHandle(handle)
