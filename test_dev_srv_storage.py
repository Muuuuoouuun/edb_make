"""Storage growth and per-edit cost: cleanup protection, garbage folders, data-folder note, path caches."""
from __future__ import annotations

import json
import os
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_server
from test_ui_placement_details import run_node

OLD = 1.0  # mtime far in the past
DAY = 86_400.0


def _write(path: Path, data: bytes = b"x", *, mtime: float | None = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _age_tree(folder: Path, mtime: float = OLD) -> None:
    for path in sorted(folder.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.utime(path, (mtime, mtime))
    os.utime(folder, (mtime, mtime))


def _runtime_patches(stack: ExitStack, runtime_dir: Path) -> None:
    for name, value in (
        ("RUNTIME_DIR", runtime_dir),
        ("LATEST_SESSION_JSON", runtime_dir / "latest_session.json"),
        ("SESSION_HISTORY_JSON", runtime_dir / "session_history.json"),
        ("GENERATED_SESSION_JS", runtime_dir / "generated_session.js"),
    ):
        stack.enter_context(patch.object(app_server, name, value))


class TestRetentionCleanupNeverTakesFinishedFiles(unittest.TestCase):
    def test_old_unreferenced_crop_selected_but_finished_and_referenced_files_kept(self) -> None:
        with TemporaryDirectory() as raw:
            runtime = Path(raw)
            outputs = runtime / "outputs"
            crop = _write(outputs / "lesson" / "crops" / "p1.png")
            upload = _write(runtime / "uploads" / "old.pdf")
            kept = [
                _write(outputs / "lesson" / "lesson.edb"),
                _write(outputs / "lesson" / "bundle.ZIP"),
                _write(outputs / "lesson" / "published" / "gen1" / "classin_handoff.json"),
                _write(outputs / "lesson" / "published" / "gen1" / "board.png"),
                _write(runtime / "exports" / "images_20260101.zip"),
                _write(runtime / "exports" / "manifest.json"),
                _write(outputs / "history-lesson" / "crops" / "p9.png"),
            ]
            history = [{"id": "h1", "session": {"problems": [{"imagePath": str(kept[-1])}]}}]

            result = app_server.cleanup_runtime_artifacts(
                runtime_dir=runtime, active_session={}, history=history,
                dry_run=False, min_age_seconds=30 * DAY, now=time.time(),
            )

            self.assertEqual(
                sorted([str(crop.resolve()), str(upload.resolve())]), sorted(result["deletedPaths"]),
            )
            self.assertFalse(crop.exists())
            for path in kept:
                self.assertTrue(path.exists(), path)

    def test_unreadable_history_deletes_nothing(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            crop = _write(runtime / "outputs" / "lesson" / "p1.png")
            (runtime / "session_history.json").write_text("{not json", encoding="utf-8")

            with self.assertRaises(app_server.SessionHistoryUnreadable):
                app_server.cleanup_runtime_artifacts(
                    runtime_dir=runtime, active_session={}, dry_run=False, min_age_seconds=0,
                )
            self.assertTrue(crop.exists())
            self.assertEqual([], app_server.load_session_history(), "lenient callers keep the old behaviour")

            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            try:
                with self.assertRaises(app_server.SessionHistoryUnreadable):
                    server.cleanup_artifacts(dry_run=False, min_age_seconds=0)
                with self.assertRaises(app_server.SessionHistoryUnreadable):
                    server.cleanup_stale_work_folders(min_age_seconds=0)
                handler = object.__new__(app_server.AppRequestHandler)
                handler.server = server
                handler.headers = {}
                handler._read_json_body = lambda: {"dryRun": False}
                responses = []
                handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
                with patch.object(app_server, "_request_is_same_origin", return_value=True):
                    handler._handle_runtime_artifact_cleanup()
            finally:
                server.server_close()
            payload, kwargs = responses[0]
            self.assertEqual("session_history_unreadable", payload["code"])
            self.assertRegex(payload["error"], "[가-힣]")
            self.assertTrue(payload["detail"])
            self.assertEqual(app_server.HTTPStatus.CONFLICT, kwargs["status"])
            self.assertTrue(crop.exists())

    def test_endpoint_dry_run_reports_old_work_bytes_without_deleting(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            crop = _write(runtime / "outputs" / "lesson" / "p1.png", b"12345")
            _write(runtime / "outputs" / "lesson" / "lesson.edb", b"edb-bytes")
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            try:
                result = server.cleanup_artifacts(dry_run=True)
            finally:
                server.server_close()
            self.assertEqual(5, result["selectedBytes"])
            self.assertTrue(crop.exists())

    def test_partial_cleanup_answers_with_korean_error_and_locked_paths(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            freed = _write(runtime / "outputs" / "lesson" / "p1.png")
            locked = _write(runtime / "outputs" / "lesson" / "locked.png")
            real_unlink = Path.unlink

            def unlink(path, *args, **kwargs):
                if path.name == locked.name:
                    raise PermissionError(13, "file is in use", str(path))
                return real_unlink(path, *args, **kwargs)

            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            try:
                handler = object.__new__(app_server.AppRequestHandler)
                handler.server = server
                handler.headers = {}
                handler._read_json_body = lambda: {"dryRun": False}
                responses = []
                handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
                with patch.object(app_server, "_request_is_same_origin", return_value=True), \
                        patch.object(Path, "unlink", unlink):
                    handler._handle_runtime_artifact_cleanup()
            finally:
                server.server_close()
            payload, kwargs = responses[0]
            self.assertFalse(payload["ok"])
            self.assertEqual("artifact_cleanup_partial", payload["code"])
            self.assertRegex(payload["error"], "[가-힣]")
            self.assertIn("locked.png", payload["detail"])
            self.assertNotIn("status", kwargs, "partial success stays HTTP 200")
            self.assertEqual(1, payload["deletedFileCount"])
            self.assertFalse(freed.exists())
            self.assertTrue(locked.exists())


class TestStaleWorkFolders(unittest.TestCase):
    def test_plan_selects_only_old_unreferenced_preview_and_staging_folders(self) -> None:
        with TemporaryDirectory() as raw:
            outputs = Path(raw) / "outputs"
            stale_preview = outputs / "previews" / "quiz_1"
            _write(stale_preview / "pages" / "page1.png", b"abc")
            adopted_preview = outputs / "previews" / "quiz_2"
            adopted_crop = _write(adopted_preview / "crops" / "p1.png")
            _write(adopted_preview / "pages" / "page1.png")
            history_preview = outputs / "previews" / "quiz_3"
            history_crop = _write(history_preview / "crops" / "p1.png")
            fresh_preview = outputs / "previews" / "quiz_4"
            _write(fresh_preview / "pages" / "page1.png", mtime=None)
            touched_preview = outputs / "previews" / "quiz_5"
            _write(touched_preview / "pages" / "page1.png")
            _write(touched_preview / "crops" / "new.png", mtime=None)
            leftover_staging = outputs / "lesson" / ".publish-staging" / "gen1"
            _write(leftover_staging / "classin_handoff.json", b"halfway")
            export_staging = outputs / "previews" / "quiz_2" / ".export-staging" / "gen2"
            _write(export_staging / "crops" / "p1.png")
            published = outputs / "lesson" / "published" / "gen0"
            _write(published / "lesson.edb")
            # Finished files keep the whole folder, referenced or not: an adopted preview run
            # is the lesson's output_dir, so publish writes the teacher's EDB inside it.
            published_preview = outputs / "previews" / "quiz_6"
            _write(published_preview / "pages" / "page1.png")
            _write(published_preview / "published" / "gen1" / "lesson.edb")
            edb_preview = outputs / "previews" / "quiz_7"
            _write(edb_preview / "lesson.edb")
            edb_staging = outputs / "lesson" / ".publish-staging" / "gen2"
            _write(edb_staging / "lesson.edb")
            for folder in (stale_preview, adopted_preview, history_preview, leftover_staging, export_staging, published,
                           published_preview, edb_preview, edb_staging):
                _age_tree(folder)
            _age_tree(touched_preview / "pages")

            active = {"output_dir": str(outputs / "lesson"), "problems": [{"imagePath": adopted_crop.as_uri()}]}
            history = [{"id": "h", "session": {"problems": [{"boardRenderPath": str(history_crop)}]}}]
            planned = app_server.plan_stale_work_folders(
                output_root=outputs, active_session=active, history=history, now=time.time(),
            )

            self.assertEqual(
                sorted([stale_preview.resolve(), leftover_staging.resolve(), export_staging.resolve()]),
                sorted(path for path, _size in planned),
            )
            self.assertEqual(3, dict(planned)[stale_preview.resolve()])

    def test_unreadable_subfolder_keeps_the_whole_folder(self) -> None:
        with TemporaryDirectory() as raw:
            outputs = Path(raw) / "outputs"
            run = outputs / "previews" / "locked_run"
            _write(run / "pages" / "page1.png")
            _write(run / "locked" / "lesson.edb")
            _age_tree(run)
            real_scandir = os.scandir

            def scandir(path):
                if os.path.basename(path) == "locked":
                    raise PermissionError(13, "Access is denied", str(path))
                return real_scandir(path)

            with patch.object(app_server.os, "scandir", scandir):
                planned = app_server.plan_stale_work_folders(
                    output_root=outputs, active_session={}, history=[], now=time.time(),
                )
            self.assertEqual([], planned, "a finished file hidden in an unreadable folder must not be missed")

    def test_server_pass_deletes_garbage_and_logs_counts(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            stale = runtime / "outputs" / "previews" / "old_run"
            _write(stale / "pages" / "page1.png", b"1234")
            _age_tree(stale)
            kept_edb = _write(runtime / "outputs" / "previews" / "adopted_run" / "published" / "g" / "lesson.edb")
            _age_tree(runtime / "outputs" / "previews" / "adopted_run")
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            try:
                server.begin_artifact_job()
                with self.assertRaises(app_server.ArtifactCleanupBusy):
                    server.cleanup_stale_work_folders()
                server.end_artifact_job()
                with patch.dict(os.environ, {"EDB_AUTO_ARTIFACT_CLEANUP": ""}), \
                        patch.object(app_server, "_run_startup_artifact_cleanup") as audit:
                    app_server._run_startup_maintenance(server)
                audit.assert_called_once_with(server)
            finally:
                server.server_close()
            self.assertFalse(stale.exists())
            self.assertTrue(kept_edb.exists())

    def test_opt_out_env_skips_garbage_pass(self) -> None:
        class Server:
            def cleanup_stale_work_folders(self):
                raise AssertionError("opted out")

        with patch.dict(os.environ, {"EDB_AUTO_ARTIFACT_CLEANUP": "0"}), \
                patch.object(app_server, "_run_startup_artifact_cleanup") as audit:
            app_server._run_startup_maintenance(Server())
        audit.assert_called_once()


class TestDataFolderFallbackFlag(unittest.TestCase):
    def test_settings_response_carries_flag_and_folder_only_on_fallback(self) -> None:
        for fallback in (False, True):
            with self.subTest(fallback=fallback), \
                    patch.object(app_server, "DATA_FOLDER_FALLBACK", fallback), \
                    patch.object(app_server, "BASE_DIR", Path("C:/Users/t/AppData/Local/ClassInEDBMVP")), \
                    patch.object(app_server, "summarize_user_settings", return_value={"aiEnabled": True}):
                handler = object.__new__(app_server.AppRequestHandler)
                responses = []
                handler._send_json = lambda payload, **kwargs: responses.append(payload)
                handler._handle_user_settings_get()
                settings = responses[0]["settings"]
                self.assertTrue(settings["aiEnabled"])
                self.assertIs(fallback, settings["dataFolderFallback"])
                self.assertEqual(bool(fallback), bool(settings["dataFolder"]))

    def test_flag_matches_app_root_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.write_text("not a folder", encoding="utf-8")
            with patch.object(app_server, "is_frozen_app", return_value=True), \
                    patch.object(app_server.sys, "platform", "win32"), \
                    patch.dict(app_server.os.environ, {"EDB_APP_HOME": "", "LOCALAPPDATA": tmp}):
                for home, expected in ((blocked, True), (Path(tmp) / "Documents" / "ClassInEDBMVP", False)):
                    with self.subTest(expected=expected), \
                            patch.object(app_server, "default_frozen_app_home", return_value=home.resolve()):
                        self.assertIs(expected, app_server.app_root() != app_server.default_frozen_app_home())


def _session(name: str, *paths: Path) -> dict:
    return {"session_name": name, "generated_at": name,
            "problems": [{"id": path.stem, "imagePath": str(path)} for path in paths]}


class TestHistoryPathsAreWalkedOnce(unittest.TestCase):
    def test_edits_never_rewalk_history_but_every_path_stays_allowed(self) -> None:
        with TemporaryDirectory() as raw, ExitStack() as stack:
            runtime = Path(raw)
            _runtime_patches(stack, runtime)
            crops = [_write(runtime / "outputs" / f"c{index}.png", mtime=None) for index in range(6)]
            server = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
            walked = []
            real = app_server.collect_session_history_file_paths
            stack.enter_context(patch.object(
                app_server, "collect_session_history_file_paths",
                side_effect=lambda history: walked.append([entry.get("id") for entry in history]) or real(history),
            ))
            try:
                server.remember_session(_session("A", crops[0]))
                server.remember_session(_session("B", crops[1]))
                server.remember_session(_session("B", crops[1], crops[2]))
                # Same id within the same second: the new crop still comes from the committed snapshot.
                current = server.session_snapshot()
                server.remember_session_if_current(current, _session("B", crops[1], crops[2], crops[3]))
                self.assertEqual([], walked, "board edits must not re-resolve saved history")
                for crop in crops[:4]:
                    self.assertTrue(server.is_file_allowed(str(crop.resolve())), crop)

                # An entry written by someone else is walked once, then remembered.
                history_path = runtime / "session_history.json"
                history = json.loads(history_path.read_text(encoding="utf-8"))
                outside = app_server._session_history_entry(_session("X", crops[4]))
                history_path.write_text(json.dumps(history + [outside]), encoding="utf-8")
                server.remember_session(_session("B", crops[1], crops[2], crops[3]))
                server.remember_session(_session("B", crops[1], crops[2], crops[3], crops[5]))
                self.assertEqual([[outside["id"]]], walked)
                self.assertTrue(server.is_file_allowed(str(crops[4].resolve())))
                self.assertTrue(server.is_file_allowed(str(crops[5].resolve())))

                server.clear_session(expected_revision=None)
                self.assertFalse(server.is_file_allowed(str(crops[0].resolve())))
                self.assertEqual(set(), server._history_paths_seen)
            finally:
                server.server_close()

    def test_server_built_without_init_still_collects(self) -> None:
        server = object.__new__(app_server.AppHTTPServer)
        server.allowed_files = set()
        with TemporaryDirectory() as raw:
            crop = _write(Path(raw) / "c.png", mtime=None)
            older = app_server._session_history_entry(_session("old", crop))
            server._allow_new_history_paths([{"id": "now"}, older])
        self.assertIn(str(crop.resolve()), server.allowed_files)


UI_SETUP = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Babel = require('./scripts/vendor/babel.min.js');
const source = fs.readFileSync('ui_prototype/app.jsx', 'utf8');
function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `source bounds missing: ${start}`);
  return source.slice(from, to);
}
const createElement = (type, props, ...children) => ({ type, props: props || {},
  children: children.flat(Infinity).filter(child => child != null && child !== false) });
const walk = node => node && typeof node === 'object' ? [node, ...node.children.flatMap(walk)] : [];
const text = node => typeof node === 'string' || typeof node === 'number' ? String(node)
  : node?.children?.map(text).join('') || '';
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
function load(code, extra = {}) {
  const sandbox = { React: { createElement }, ...extra };
  vm.createContext(sandbox);
  vm.runInContext(Babel.transform(code, { presets: ['react'] }).code, sandbox);
  return sandbox;
}
"""


class TestStorageSettingsUi(unittest.TestCase):
    def test_storage_card_shows_old_work_size_and_confirms_before_cleaning(self) -> None:
        run_node(UI_SETUP + r"""
        const requests = [], confirms = [];
        let confirmAnswer = false;
        const replies = [
          { ok: true, selectedBytes: 2 * 1024 * 1024 },
          { ok: true, deletedBytes: 2 * 1024 * 1024 },
          { ok: true, selectedBytes: 0 },
          { ok: false, code: 'artifact_cleanup_partial', error: '다른 프로그램이 쓰고 있는 파일은 지우지 못했어요. 나머지는 정리했어요.' },
          { ok: true, selectedBytes: 3 * 1024 },
        ];
        const store = [], effects = [];
        let cursor = 0;
        const sandbox = load(section('async function requestArtifactCleanup(', 'function BoardLayoutControls(')
          + section('function formatBytes(', 'function cloneSession('), {
          useState: initial => { const i = cursor++; if (!(i in store)) store[i] = initial;
            return [store[i], value => { store[i] = typeof value === 'function' ? value(store[i]) : value; }]; },
          useEffect: effect => { if (!effects.length) effects.push(effect); },
          fetch: async (url, init) => { requests.push([url, JSON.parse(init.body)]); const reply = replies.shift();
            return { ok: reply.ok, json: async () => reply }; },
          expectOkJson: async (resp, fallback) => { const json = await resp.json();
            if (!resp.ok || !json.ok) throw new Error(json.error || fallback); return json; },
          userErrorText: error => error.message,
          window: { confirm: message => { confirms.push(message); return confirmAnswer; } },
        });
        const render = () => { cursor = 0; return sandbox.StorageCleanupCard({}); };
        const button = () => walk(render()).find(node => node.type === 'button');
        (async () => {
          assert.match(text(render()), /확인하는 중/);
          assert.equal(button().props.disabled, true);
          effects[0](); await flush(); await flush();
          assert.deepEqual(requests, [['/api/runtime/artifacts/cleanup', { dryRun: true }]]);
          assert.match(text(render()), /2\.0MB/);
          assert.equal(button().props.disabled, false);

          button().props.onClick(); await flush();
          assert.equal(requests.length, 1, 'cancelling the confirm deletes nothing');
          assert.match(confirms[0], /\.edb/);
          assert.match(confirms[0], /그대로/);

          confirmAnswer = true;
          button().props.onClick(); await flush(); await flush(); await flush();
          assert.deepEqual(requests.slice(1).map(([, body]) => body), [{ dryRun: false }, { dryRun: true }]);
          assert.match(text(render()), /2\.0MB를 정리했어요/);
          assert.match(text(render()), /정리할 오래된 작업 파일이 없어요/);
          assert.equal(button().props.disabled, true);

          // Partial cleanup: the size is measured again and the reason stays visible as a warning.
          store[0] = { status: 'ready', bytes: 10 * 1024, message: '' };
          button().props.onClick(); await flush(); await flush(); await flush();
          assert.deepEqual(requests.slice(3).map(([, body]) => body), [{ dryRun: false }, { dryRun: true }]);
          assert.match(text(render()), /다른 프로그램이 쓰고 있는 파일은 지우지 못했어요/);
          assert.match(text(render()), /3KB 있어요/);
          assert.ok(walk(render()).some(node => String(node.props.className || '').includes('warn')));
          assert.equal(button().props.disabled, false, 'the teacher can try again');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """)

    def test_data_folder_note_is_os_aware_and_hidden_without_fallback(self) -> None:
        run_node(UI_SETUP + r"""
        const code = section('function DataFolderFallbackNote(', 'async function downloadPublishSummary(');
        for (const mac of [true, false]) {
          const sandbox = load(code, { IS_MAC_PLATFORM: mac });
          assert.equal(sandbox.DataFolderFallbackNote({ folder: '' }), null);
          const note = text(sandbox.DataFolderFallbackNote({ folder: 'D:/Other/ClassInEDBMVP' }));
          assert.match(note, /문서 폴더/);
          assert.match(note, /D:\/Other\/ClassInEDBMVP/);
          assert.match(note, mac ? /파일 및 폴더/ : /제어된 폴더 액세스/);
          assert.doesNotMatch(note, mac ? /Windows/ : /시스템 설정/);
        }
        const home = section('function HomeDashboard({', 'function DataFolderFallbackNote(');
        assert.match(home, /<DataFolderFallbackNote folder=\{dataFolder\} \/>/);
        assert.match(source, /dataFolder=\{userSettings\?\.dataFolderFallback \? userSettings\.dataFolder : ''\}/);
        """)


if __name__ == "__main__":
    unittest.main()
