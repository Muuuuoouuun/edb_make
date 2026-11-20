"""dev2 storage-sync: lesson files kept in step with sync, cheap 최근 수업 reads and writes,
a sturdier 문제 추출 manifest, and bug-report redaction of encoded paths and quoted names."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from unittest.mock import patch
from urllib.parse import quote

import app_server
import bug_reporting
from problem_gallery import GalleryStore
from test_dev_srv_storage import OLD, _age_tree, _runtime_patches, _session, _write
from test_problem_gallery import fake_parse, pdf_bytes


def _history_handler(server: app_server.AppHTTPServer) -> app_server.AppRequestHandler:
    """GET /api/session/history without a socket."""
    handler = object.__new__(app_server.AppRequestHandler)
    handler.server = server
    handler._send_json = lambda *args, **kwargs: None
    return handler


class TestRunningServerTakesOverSyncedLesson(unittest.TestCase):
    """R22: OneDrive/iCloud replace latest_session.json while the server keeps running."""

    def test_lesson_saved_on_another_pc_is_served_and_never_overwritten(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            v1 = {"session_name": "3월 모의고사", "output_dir": str(runtime / "outputs" / "quiz"),
                  "problems": [{"id": "p1", "title": "학교 v1"}]}
            app_server.persist_latest_session_with_history(v1)
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            try:
                stale, stale_revision = server.session_snapshot_with_revision()
                v2 = dict(v1, problems=[{"id": "p1", "title": "집 v2"}, {"id": "p2", "title": "집에서 추가"}])
                # A sync tool replaces the file (new inode), as _atomic_write_text does; NTFS can repeat an mtime.
                app_server._atomic_write_text(app_server.LATEST_SESSION_JSON, json.dumps(v2, ensure_ascii=False))

                served, revision = server.session_snapshot_with_revision()
                self.assertEqual(["집 v2", "집에서 추가"], [p["title"] for p in served["problems"]])
                self.assertGreater(revision, stale_revision)
                app_server._refresh_session_problem_counts(served)  # as GET /api/session/latest does
                self.assertTrue(server.register_current_session(served), "tab load still records 최근 수업")

                edited = dict(stale, session_name="수정")
                self.assertIsNone(server.remember_session_if_current(stale, edited, expected_revision=stale_revision))
                on_disk = json.loads(app_server.LATEST_SESSION_JSON.read_text(encoding="utf-8"))
                self.assertEqual(["집 v2", "집에서 추가"], [p["title"] for p in on_disk["problems"]])

                committed = server.remember_session_if_current(
                    served, dict(served, session_name="수정"), expected_revision=revision)
                self.assertIsNotNone(committed)
                self.assertEqual(committed, server.session_snapshot_with_revision()[1],
                                 "re-reading its own save must not bump the revision")

                app_server.LATEST_SESSION_JSON.write_text('{"problems": [', encoding="utf-8")  # mid-sync
                self.assertEqual(committed, server.session_snapshot_with_revision()[1])
            finally:
                server.server_close()

    def test_files_the_other_pc_added_to_a_recent_lesson_are_allowed(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            old_crop = _write(runtime / "outputs" / "old.png", mtime=None)
            new_crop = _write(runtime / "outputs" / "new.png", mtime=None)
            app_server.persist_latest_session_with_history(_session("다른 수업", old_crop))
            app_server.persist_latest_session_with_history(_session("수업"))
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            handler = _history_handler(server)
            try:
                handler._handle_session_history()
                # The other PC adds a crop to '다른 수업' and then opens another lesson; both files sync here.
                app_server.persist_latest_session_with_history(_session("다른 수업", old_crop, new_crop))
                app_server.persist_latest_session_with_history(_session("집에서 연 수업"))
                served, _revision = server.session_snapshot_with_revision()
                self.assertEqual("집에서 연 수업", served["session_name"])
                handler._handle_session_history()
                self.assertTrue(server.is_file_allowed(str(new_crop.resolve())))
            finally:
                server.server_close()


class _GalleryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = GalleryStore(Path(self.temporary.name) / "gallery")

    def upload_pdf(self, pages: int) -> dict:
        data = pdf_bytes(pages)
        return self.store.upload(io.BytesIO(data), len(data), "교재.pdf")


class TestGalleryBatchCommitsLast(_GalleryCase):
    """R19: a locked scratch folder must never fail a batch after its manifest was saved."""

    def test_cleanup_failure_after_a_batch_never_leaves_listed_crops_missing(self) -> None:
        job = self.upload_pdf(25)
        real_cleanup = tempfile.TemporaryDirectory.cleanup
        calls = []

        def flaky_cleanup(directory):
            calls.append(directory.name)
            if len(calls) == 1:
                raise PermissionError(32, "used by another process", directory.name)
            return real_cleanup(directory)

        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse), \
                mock.patch.object(tempfile.TemporaryDirectory, "cleanup", flaky_cleanup):
            with self.assertRaises(PermissionError):
                self.store.parse_next(job["id"])
            job = self.store.load(job["id"])
            self.assertEqual(0, job["processedPages"], "a failed batch must stay retryable")
            while job["processedPages"] < 25:
                job = self.store.parse_next(job["id"])
        root = self.store.job_dir(job["id"])
        self.assertEqual(list(range(1, 26)), [problem["number"] for problem in job["problems"]])
        self.assertTrue(all((root / problem["imageFile"]).is_file() for problem in job["problems"]))
        self.store.export_images([problem["id"] for problem in job["problems"]])

    def test_file_held_open_in_batch_folder_does_not_fail_a_finished_batch(self) -> None:
        # On Windows an open handle blocks deletion, as antivirus or OneDrive reading the page copy does.
        job = self.upload_pdf(25)
        held = []

        def holding_parse(source, **kwargs):
            held.append(open(source, "rb"))
            return fake_parse(source, **kwargs)

        try:
            with mock.patch("problem_parser.parse_problems", side_effect=holding_parse):
                job = self.store.parse_next(job["id"])
        finally:
            for handle in held:
                handle.close()
        self.assertEqual(("partial", 12), (job["status"], job["processedPages"]))
        with mock.patch("problem_parser.parse_problems", side_effect=fake_parse):
            while job["processedPages"] < 25:
                job = self.store.parse_next(job["id"])
        root = self.store.job_dir(job["id"])
        self.assertTrue(all((root / problem["imageFile"]).is_file() for problem in job["problems"]))
        self.assertFalse(list(self.store.root.rglob("batch-*")), "the next batch removes the leftover folder")


class TestSyncConflictCopiesOfHistory(unittest.TestCase):
    """R59: OneDrive keeps the other PC's history as 'session_history-PC명.json', iCloud as 'session_history 2.json'."""

    def _lesson(self, runtime: Path, run: str, name: str, updated_at: str) -> dict:
        out = runtime / "outputs" / "previews" / run
        _write(out / "crops" / "p1.png")
        return app_server._session_history_entry(
            {"session_name": name, "output_dir": str(out), "problems": []}, updated_at=updated_at)

    def test_copy_lessons_stay_listed_protected_and_kept_by_writers(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            older = self._lesson(runtime, "older", "지난주", "2026-11-10T09:00:00+09:00")
            home = self._lesson(runtime, "home", "집", "2026-11-17T21:00:00+09:00")
            school = self._lesson(runtime, "school", "학교", "2026-11-17T15:00:00+09:00")
            (runtime / "session_history.json").write_text(json.dumps([home, older]), encoding="utf-8")
            (runtime / "session_history-SCHOOL-PC.json").write_text(json.dumps([school, older]), encoding="utf-8")
            (runtime / "session_history 2.json").write_text("{truncated", encoding="utf-8")
            _age_tree(runtime / "outputs")

            history = app_server.load_session_history(strict=True)
            self.assertEqual(["집", "학교", "지난주"], [entry["sessionName"] for entry in history])
            self.assertEqual([], app_server.plan_stale_work_folders(
                output_root=runtime / "outputs", active_session={}, history=history))
            # A corrupt copy never makes writers treat the list as corrupt and drop it.
            self.assertEqual(3, len(app_server._history_for_write()))

            real_read = Path.read_text

            def offline(path, *args, **kwargs):
                if "SCHOOL-PC" in path.name:
                    raise OSError(22, "cloud file not available offline", str(path))
                return real_read(path, *args, **kwargs)

            with patch.object(Path, "read_text", offline):
                with self.assertRaises(app_server.SessionHistoryUnreadable):  # cleanup deletes nothing
                    app_server.load_session_history(strict=True)
                self.assertEqual(2, len(app_server._history_for_write()), "an unreadable copy never blocks saving")

            app_server._atomically_clear_persisted_session_state(keep_history=False)  # 초기화
            self.assertEqual([], list(runtime.glob("session_history*.json")))
            self.assertEqual([], app_server.load_session_history())


class TestOpeningTheAppKeepsRecentLessons(unittest.TestCase):
    """R87: a tab load or 새로고침 must not rewrite 최근 수업 or re-date the open lesson."""

    def test_load_keeps_history_until_the_open_lesson_is_missing_or_not_first(self) -> None:
        stamp = "2026-11-10T09:00:00+09:00"
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            history_path = runtime / "session_history.json"
            other = {"session_name": "다른 수업", "output_dir": str(runtime / "outputs" / "other"), "problems": [{"id": "o1"}]}
            lesson = {"session_name": "수업", "output_dir": str(runtime / "outputs" / "lesson"), "problems": [{"id": "p1"}]}
            for session in (other, lesson):
                app_server._refresh_session_problem_counts(session)
                app_server.persist_latest_session_with_history(session, updated_at=stamp)
            os.utime(history_path, (OLD, OLD))
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)

            def tab_load() -> bool:  # what GET /api/session/latest does
                snapshot = server.session_snapshot()
                app_server._refresh_session_problem_counts(snapshot)
                return server.register_current_session(snapshot)

            try:
                self.assertTrue(tab_load())
                self.assertEqual(OLD, history_path.stat().st_mtime)
                self.assertEqual(stamp, app_server.load_session_history()[0]["updatedAt"])

                stored = json.loads(history_path.read_text(encoding="utf-8"))
                history_path.write_text(json.dumps(stored[1:]), encoding="utf-8")  # an earlier history write was skipped
                self.assertTrue(tab_load())
                self.assertEqual(["수업", "다른 수업"], [e["sessionName"] for e in app_server.load_session_history()])

                history_path.write_text(json.dumps(stored[::-1]), encoding="utf-8")  # open lesson listed, but not first
                self.assertTrue(tab_load())
                self.assertEqual(["수업", "다른 수업"], [e["sessionName"] for e in app_server.load_session_history()])
            finally:
                server.server_close()


class TestHistoryListWalksOnlyNewLessons(unittest.TestCase):
    """R45: the 최근 수업 refresh after every edit must not re-resolve every saved lesson's files."""

    def test_history_list_walks_only_lessons_the_server_has_not_walked(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            crops = [_write(runtime / "outputs" / f"c{index}.png", mtime=None) for index in range(4)]
            history_path = runtime / "session_history.json"
            entries = [app_server._session_history_entry(_session(name, crop)) for name, crop in zip("ABC", crops)]
            history_path.write_text(json.dumps(entries), encoding="utf-8")
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            handler = _history_handler(server)
            walked = []
            real = app_server.collect_session_history_file_paths
            stack.enter_context(patch.object(
                app_server, "collect_session_history_file_paths",
                side_effect=lambda history: walked.extend(entry.get("id") for entry in history) or real(history),
            ))
            try:
                handler._handle_session_history()
                handler._handle_session_history()
                self.assertEqual([], walked, "the list refresh after each edit must not re-resolve saved lessons")
                # A lesson written by someone else is walked once by the list, then remembered.
                outside = app_server._session_history_entry(_session("X", crops[3]))
                history_path.write_text(json.dumps(entries + [outside]), encoding="utf-8")
                handler._handle_session_history()
                handler._handle_session_history()
                self.assertEqual([outside["id"]], walked)
                for crop in crops:
                    self.assertTrue(server.is_file_allowed(str(crop.resolve())), crop)
                # 새 수업 keeps the list and forgets what was walked: the next list walks each lesson once.
                server.clear_session_with_revision(keep_history=True)
                walked.clear()
                handler._handle_session_history()
                handler._handle_session_history()
                self.assertEqual(sorted(entry["id"] for entry in entries + [outside]), sorted(walked))
            finally:
                server.server_close()


class TestCompactStateFiles(unittest.TestCase):
    """R69: indent=2 forced Python's slow pure-Python JSON encoder on every lesson save."""

    def test_lesson_save_writes_compact_state_files(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            try:
                base, revision = server.session_snapshot_with_revision()
                lesson = {"session_name": "새 수업\n2교시", "problems": []}
                self.assertTrue(server.remember_session_if_current(base, lesson, expected_revision=revision))
                for name in ("latest_session.json", "session_history.json"):
                    text = (runtime / name).read_text(encoding="utf-8")
                    self.assertNotIn("\n", text, name)
                    self.assertIn("새 수업", text, name)  # ensure_ascii=False kept
                    json.loads(text)
            finally:
                server.server_close()


class TestGalleryManifestWrites(_GalleryCase):
    """R65: the 문제 추출 manifest survives a short Windows lock and a power cut, and a hidden job leaves a trail."""

    def test_brief_windows_lock_is_retried_and_manifest_is_flushed(self) -> None:
        job = self.upload_pdf(1)
        real_replace, attempts = os.replace, []

        def locked_once(*args):
            attempts.append(args)
            if len(attempts) == 1:
                error = PermissionError(13, "sharing violation")
                error.winerror = 32  # set by hand, so the check also runs on the ubuntu CI
                raise error
            return real_replace(*args)

        job["sourceName"] = "바뀐 교재.pdf"
        with mock.patch("problem_gallery.os.replace", side_effect=locked_once), \
                mock.patch("problem_gallery.time.sleep"), \
                mock.patch("problem_gallery.os.fsync", wraps=os.fsync) as fsync:
            self.store.save(job)
        self.assertEqual(2, len(attempts))
        self.assertEqual(1, fsync.call_count)
        self.assertEqual(job, self.store.load(job["id"]))
        self.assertFalse(list(self.store.root.rglob("*.tmp")))
        with mock.patch("problem_gallery.os.replace", side_effect=OSError(28, "disk full")) as replace, \
                self.assertRaises(OSError):
            self.store.save({**job, "sourceName": "x.pdf"})
        self.assertEqual(1, replace.call_count, "only a Windows busy lock is retried")
        self.assertEqual(job, self.store.load(job["id"]))

    def test_unreadable_job_is_logged_and_the_others_stay_listed(self) -> None:
        good = self.upload_pdf(1)
        bad = self.upload_pdf(1)
        (self.store.job_dir(bad["id"]) / "manifest.json").write_bytes(b"\x00" * 64)  # power cut before fsync
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual([good], self.store.list_jobs())
        self.assertIn(f"skipped unreadable job {bad['id']}", stderr.getvalue())


class TestBugReportRedactsEncodedAndQuotedNames(unittest.TestCase):
    """R46: app.log keeps failed /api/file requests percent-encoded; error messages quote spaced file names."""

    def test_report_redacts_encoded_file_urls_and_quoted_spaced_names(self) -> None:
        # A literal Windows path: on the ubuntu CI a Path.home() path would pass without the fix.
        edb = (
            "C:\\Users\\홍길동\\Documents\\ClassInEDBMVP\\.app_runtime\\outputs"
            "\\3학년 2반 홍길동 오답\\3학년 2반 홍길동 오답.edb"
        )
        message = "파일 '3학년 2반 홍길동 오답.pdf'의 용량 600.0 MB가 파일당 한도 512.0 MB를 넘었습니다."
        with TemporaryDirectory() as raw:
            log_file = Path(raw) / "app.log"
            log_file.write_text(
                f'[app-server] 127.0.0.1 - "GET /api/file?path={quote(edb)} HTTP/1.1" 404 -\n',
                encoding="utf-8",
            )
            report = bug_reporting.build_bug_report(
                {
                    "description": "파일 용량 오류가 납니다.",
                    "context": {"lastOperationError": {"type": "operation", "message": message}},
                },
                app_config={"appId": "ClassInEDBMVP", "version": "0.1.0", "platform": "windows"},
                log_file=log_file,
            )

        serialized = json.dumps(report, ensure_ascii=False)
        for private_value in ("홍길동", "3학년 2반", quote("홍길동"), "C:\\Users", quote("C:\\Users")):
            self.assertNotIn(private_value, serialized)
        self.assertIn("path=[local-path]", report["diagnostics"]["logTail"])
        self.assertEqual(
            "파일 '[document]'의 용량 600.0 MB가 파일당 한도 512.0 MB를 넘었습니다.",
            report["context"]["lastOperationError"]["message"],
        )


if __name__ == "__main__":
    unittest.main()
