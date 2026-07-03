import json
import os
import base64
import io
import threading
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_server
from structured_schema import Box


VALID_MANIFEST_SHA256 = "a" * 64
VALID_ARTIFACT_SHA256 = "b" * 64


class TestStaticAssetCaching(unittest.TestCase):
    def setUp(self):
        app_server.clear_app_update_status_cache()

    def test_app_version_comparison_handles_semver_like_versions(self):
        self.assertEqual(0, app_server.compare_app_versions("v0.1.0", "0.1"))
        self.assertGreater(app_server.compare_app_versions("0.1.0", "0.1.1"), 0)
        self.assertLess(app_server.compare_app_versions("0.2.0", "0.1.9"), 0)
        self.assertGreater(app_server.compare_app_versions("1.0.0-beta.1", "1.0.0"), 0)

    def test_update_urls_require_https_except_loopback(self):
        self.assertEqual("", app_server._normalize_update_url("http://example.test/update.json"))
        self.assertEqual("https://example.test/update.json", app_server._normalize_update_url("https://example.test/update.json"))
        self.assertEqual("http://127.0.0.1:9999/update.json", app_server._normalize_update_url("http://127.0.0.1:9999/update.json"))

    def test_sanitize_edb_file_name_normalizes_requested_name(self):
        self.assertEqual("Lesson_1.edb", app_server.sanitize_edb_file_name("Lesson 1"))
        self.assertEqual("고1_샘플.edb", app_server.sanitize_edb_file_name("../고1 샘플.edb"))
        self.assertEqual(
            "fallback_name.edb",
            app_server.sanitize_edb_file_name("", fallback_stem="fallback name"),
        )

    def test_export_default_output_dir_lives_under_runtime_outputs(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            runtime_dir = tmpdir / "runtime"
            source = tmpdir / "Lesson 1.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            handler = object.__new__(app_server.AppRequestHandler)

            with patch.object(app_server, "RUNTIME_DIR", runtime_dir):
                default_output = handler._resolve_output_dir({}, [source])
                relative_output = handler._resolve_output_dir({"outputDir": "../Old Session?"}, [source])
                absolute_output = handler._resolve_output_dir({"outputDir": str(tmpdir / "custom out")}, [source])

            self.assertEqual((runtime_dir / "outputs" / "Lesson_1").resolve(), default_output)
            self.assertEqual((runtime_dir / "outputs" / "___Old_Session_").resolve(), relative_output)
            self.assertEqual((tmpdir / "custom out").resolve(), absolute_output)

    def test_ensure_runtime_dirs_creates_default_output_root(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            base_dir = tmpdir / "base"
            runtime_dir = tmpdir / "runtime"
            upload_dir = runtime_dir / "uploads"

            with (
                patch.object(app_server, "BASE_DIR", base_dir),
                patch.object(app_server, "RUNTIME_DIR", runtime_dir),
                patch.object(app_server, "UPLOAD_DIR", upload_dir),
            ):
                app_server.ensure_runtime_dirs()

            self.assertTrue(base_dir.is_dir())
            self.assertTrue(upload_dir.is_dir())
            self.assertTrue((runtime_dir / "outputs").is_dir())

    def test_same_origin_guard_rejects_cross_site_browser_posts(self):
        self.assertTrue(app_server._request_is_same_origin({
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
        }))
        self.assertFalse(app_server._request_is_same_origin({
            "Host": "127.0.0.1:8765",
            "Origin": "https://example.test",
        }))

    def test_update_status_reports_platform_release_from_feed(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({
                    "appName": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "updateFeedUrl": "https://example.test/classin-edb/update.json",
                }),
                encoding="utf-8",
            )
            feed = {
                "schemaVersion": 1,
                "appId": "ClassInEDBMVP",
                "channel": "stable",
                "version": "0.1.1",
                "publishedAt": "2026-06-19T00:00:00+00:00",
                "manifestUrl": "https://example.test/releases/0.1.2/manifest.json",
                "manifestSha256": VALID_MANIFEST_SHA256,
                "platforms": {
                    "macos": {
                        "version": "0.1.2",
                        "downloadUrl": "https://example.test/ClassInEDBMVP-macOS.dmg",
                        "releaseNotesUrl": "https://example.test/releases/0.1.2",
                        "fileName": "ClassInEDBMVP-macOS.dmg",
                        "artifactType": "dmg",
                        "arch": "arm64",
                        "sizeBytes": 12345,
                        "sha256": VALID_ARTIFACT_SHA256,
                    }
                },
            }
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.object(app_server.sys, "platform", "darwin"), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }), \
                    patch.object(app_server, "_fetch_update_feed", return_value=feed):
                status = app_server.build_app_update_status()

            self.assertTrue(status["ok"])
            self.assertTrue(status["configured"])
            self.assertTrue(status["updateAvailable"])
            self.assertEqual("update_available", status["channelStatus"])
            self.assertEqual("stable", status["channel"])
            self.assertEqual("https://example.test/releases/0.1.2/manifest.json", status["manifestUrl"])
            self.assertEqual("0.1.2", status["latest"]["version"])
            self.assertEqual("ClassInEDBMVP-macOS.dmg", status["latest"]["fileName"])
            self.assertEqual("dmg", status["latest"]["artifactType"])
            self.assertEqual(VALID_ARTIFACT_SHA256, status["latest"]["sha256"])
            self.assertEqual(12345, status["latest"]["sizeBytes"])
            self.assertEqual("https://example.test/ClassInEDBMVP-macOS.dmg", status["downloadUrl"])

    def test_update_status_filters_unsafe_manifest_url_from_feed(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({
                    "appName": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "updateFeedUrl": "https://example.test/classin-edb/update.json",
                }),
                encoding="utf-8",
            )
            feed = {
                "version": "0.1.2",
                "manifestUrl": "http://example.test/releases/0.1.2/manifest.json",
                "manifestSha256": VALID_MANIFEST_SHA256,
                "platforms": {
                    "macos": {
                        "version": "0.1.2",
                        "downloadUrl": "https://example.test/ClassInEDBMVP-macOS.dmg",
                    }
                },
            }
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.object(app_server.sys, "platform", "darwin"), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }), \
                    patch.object(app_server, "_fetch_update_feed", return_value=feed):
                status = app_server.build_app_update_status()

            self.assertTrue(status["updateAvailable"])
            self.assertNotIn("manifestUrl", status)
            self.assertNotIn("manifestUrl", status["latest"])
            self.assertEqual(VALID_MANIFEST_SHA256, status["manifestSha256"])

    def test_update_status_rejects_available_update_without_usable_download_url(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({
                    "appName": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "updateFeedUrl": "https://example.test/classin-edb/update.json",
                }),
                encoding="utf-8",
            )
            feed = {
                "version": "0.1.2",
                "platforms": {
                    "macos": {
                        "version": "0.1.2",
                        "downloadUrl": "http://example.test/ClassInEDBMVP-macOS.dmg",
                    }
                },
            }
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.object(app_server.sys, "platform", "darwin"), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }), \
                    patch.object(app_server, "_fetch_update_feed", return_value=feed):
                status = app_server.build_app_update_status()

            self.assertFalse(status["updateAvailable"])
            self.assertEqual("invalid_feed", status["channelStatus"])
            self.assertEqual("update feed does not include a usable download URL", status["error"])
            self.assertEqual("", status["downloadUrl"])

    def test_update_status_rejects_invalid_integrity_digest_from_feed(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({
                    "appName": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "updateFeedUrl": "https://example.test/classin-edb/update.json",
                }),
                encoding="utf-8",
            )
            feed = {
                "version": "0.1.2",
                "manifestSha256": "not-a-real-sha",
                "platforms": {
                    "macos": {
                        "version": "0.1.2",
                        "downloadUrl": "https://example.test/ClassInEDBMVP-macOS.dmg",
                        "sha256": VALID_ARTIFACT_SHA256,
                    }
                },
            }
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.object(app_server.sys, "platform", "darwin"), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }), \
                    patch.object(app_server, "_fetch_update_feed", return_value=feed):
                status = app_server.build_app_update_status()

            self.assertFalse(status["updateAvailable"])
            self.assertEqual("invalid_feed", status["channelStatus"])
            self.assertEqual("update feed has invalid manifestSha256", status["error"])
            self.assertNotIn("manifestSha256", status)

    def test_update_status_rejects_invalid_artifact_size_from_feed(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({
                    "appName": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "updateFeedUrl": "https://example.test/classin-edb/update.json",
                }),
                encoding="utf-8",
            )
            feed = {
                "version": "0.1.2",
                "platforms": {
                    "macos": {
                        "version": "0.1.2",
                        "downloadUrl": "https://example.test/ClassInEDBMVP-macOS.dmg",
                        "sizeBytes": 0,
                    }
                },
            }
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.object(app_server.sys, "platform", "darwin"), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }), \
                    patch.object(app_server, "_fetch_update_feed", return_value=feed):
                status = app_server.build_app_update_status()

            self.assertFalse(status["updateAvailable"])
            self.assertEqual("invalid_feed", status["channelStatus"])
            self.assertEqual("update feed has invalid sizeBytes", status["error"])
            self.assertNotIn("sizeBytes", status["latest"])

    def test_update_status_rejects_platform_artifact_type_mismatch(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({
                    "appName": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "updateFeedUrl": "https://example.test/classin-edb/update.json",
                }),
                encoding="utf-8",
            )
            feed = {
                "version": "0.1.2",
                "platforms": {
                    "macos": {
                        "version": "0.1.2",
                        "downloadUrl": "https://example.test/ClassInEDBMVP-Setup.exe",
                        "fileName": "ClassInEDBMVP-Setup.exe",
                        "artifactType": "setup-exe",
                    }
                },
            }
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.object(app_server.sys, "platform", "darwin"), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }), \
                    patch.object(app_server, "_fetch_update_feed", return_value=feed):
                status = app_server.build_app_update_status()

            self.assertFalse(status["updateAvailable"])
            self.assertEqual("invalid_feed", status["channelStatus"])
            self.assertEqual("update feed artifactType for macos must be one of: dmg, zip", status["error"])

    def test_update_status_rejects_platform_file_name_mismatch_without_artifact_type(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({
                    "appName": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "updateFeedUrl": "https://example.test/classin-edb/update.json",
                }),
                encoding="utf-8",
            )
            feed = {
                "version": "0.1.2",
                "platforms": {
                    "macos": {
                        "version": "0.1.2",
                        "downloadUrl": "https://example.test/ClassInEDBMVP-Setup.exe",
                    }
                },
            }
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.object(app_server.sys, "platform", "darwin"), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }), \
                    patch.object(app_server, "_fetch_update_feed", return_value=feed):
                status = app_server.build_app_update_status()

            self.assertFalse(status["updateAvailable"])
            self.assertEqual("invalid_feed", status["channelStatus"])
            self.assertEqual("update feed fileName for macos must use one of: .dmg, .zip", status["error"])

    def test_update_status_caches_feed_fetches_for_short_ttl(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({
                    "appName": "ClassInEDBMVP",
                    "version": "0.1.0",
                    "updateFeedUrl": "https://example.test/classin-edb/update.json",
                }),
                encoding="utf-8",
            )
            feed = {
                "version": "0.1.1",
                "platforms": {
                    "macos": {
                        "version": "0.1.1",
                        "downloadUrl": "https://example.test/ClassInEDBMVP-macOS.dmg",
                    }
                },
            }
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.object(app_server.sys, "platform", "darwin"), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }), \
                    patch.object(app_server, "_fetch_update_feed", return_value=feed) as fetch_feed:
                first = app_server.build_app_update_status()
                second = app_server.build_app_update_status()
                allowed = app_server._allowed_update_urls()

            self.assertTrue(first["updateAvailable"])
            self.assertEqual(first, second)
            self.assertIn("https://example.test/ClassInEDBMVP-macOS.dmg", allowed)
            self.assertEqual(1, fetch_feed.call_count)

    def test_update_status_is_safe_when_channel_is_unconfigured(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            (tmpdir / "app_update_config.json").write_text(
                json.dumps({"version": "0.3.0"}),
                encoding="utf-8",
            )
            with patch.object(app_server, "RESOURCE_DIR", tmpdir), \
                    patch.object(app_server, "BASE_DIR", tmpdir), \
                    patch.dict(os.environ, {
                        "EDB_APP_VERSION": "",
                        "EDB_UPDATE_FEED_URL": "",
                        "EDB_DOWNLOAD_URL": "",
                        "EDB_RELEASE_NOTES_URL": "",
                    }):
                status = app_server.build_app_update_status()

            self.assertTrue(status["ok"])
            self.assertFalse(status["configured"])
            self.assertFalse(status["updateAvailable"])
            self.assertEqual("not_configured", status["channelStatus"])
            self.assertEqual("0.3.0", status["currentVersion"])

    def test_open_url_rejects_unconfigured_url_before_browser_open(self):
        handler = object.__new__(app_server.AppRequestHandler)
        payload = json.dumps({"url": "https://example.test/not-configured"}).encode("utf-8")
        handler.headers = {
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
            "Content-Length": str(len(payload)),
        }
        handler.rfile = io.BytesIO(payload)
        handler.wfile = io.BytesIO()
        statuses = []
        handler.send_response = lambda status: statuses.append(status)
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None

        with patch.object(app_server, "_allowed_update_urls", return_value=set()), \
                patch.object(app_server.webbrowser, "open", side_effect=AssertionError("browser should not open")):
            handler._handle_open_url()

        self.assertEqual([app_server.HTTPStatus.FORBIDDEN], statuses)
        self.assertIn(b"not in the configured update metadata", handler.wfile.getvalue())

    def test_json_body_rejects_oversized_content_length(self):
        handler = object.__new__(app_server.AppRequestHandler)
        handler.headers = {"Content-Length": str(app_server.MAX_JSON_BODY_BYTES + 1)}
        handler.rfile = io.BytesIO(b"{}")

        with self.assertRaises(json.JSONDecodeError):
            handler._read_json_body()

    def test_static_responses_disable_browser_cache(self):
        handler = object.__new__(app_server.AppRequestHandler)
        headers = []
        handler.send_header = lambda name, value: headers.append((name, value))

        with patch.object(app_server.SimpleHTTPRequestHandler, "end_headers", lambda _self: headers.append(("END", ""))):
            handler.end_headers()

        self.assertIn(("Cache-Control", "no-store, max-age=0"), headers)
        self.assertIn(("Pragma", "no-cache"), headers)

    def test_legacy_app_js_requests_serve_current_bundle(self):
        handler = object.__new__(app_server.AppRequestHandler)
        handler.path = "/app.js?v=old-ui"
        served_paths = []

        def fake_static_get(static_handler):
            served_paths.append(static_handler.path)

        with patch.object(app_server.SimpleHTTPRequestHandler, "do_GET", fake_static_get):
            handler.do_GET()

        self.assertEqual(["/app.bundle.js"], served_paths)

    def test_legacy_app_js_head_requests_serve_current_bundle(self):
        handler = object.__new__(app_server.AppRequestHandler)
        handler.path = "/app.js?v=old-ui"
        served_paths = []

        def fake_static_head(static_handler):
            served_paths.append(static_handler.path)

        with patch.object(app_server.SimpleHTTPRequestHandler, "do_HEAD", fake_static_head):
            handler.do_HEAD()

        self.assertEqual(["/app.bundle.js"], served_paths)

    def test_generated_session_script_is_always_empty_bridge(self):
        with TemporaryDirectory() as raw_tmp:
            generated_path = Path(raw_tmp) / "generated_session.js"
            generated_path.write_text(
                'window.EDB_UI_SESSION = {"problems":[{"id":"stale-session"}]};\n',
                encoding="utf-8",
            )
            handler = object.__new__(app_server.AppRequestHandler)
            handler.path = "/generated_session.js"
            statuses = []
            headers = []
            handler.wfile = io.BytesIO()
            handler.send_response = lambda status: statuses.append(status)
            handler.send_header = lambda name, value: headers.append((name, value))
            handler.end_headers = lambda: None

            with patch.object(app_server, "GENERATED_SESSION_JS", generated_path):
                handler.do_GET()

            self.assertEqual([app_server.HTTPStatus.OK], statuses)
            self.assertIn(("Content-Type", "application/javascript; charset=utf-8"), headers)
            self.assertEqual(b"window.EDB_UI_SESSION = null;\n", handler.wfile.getvalue())

    def test_generated_session_placeholder_overwrites_stale_bridge(self):
        with TemporaryDirectory() as raw_tmp:
            generated_path = Path(raw_tmp) / "generated_session.js"
            generated_path.write_text(
                'window.EDB_UI_SESSION = {"problems":[{"id":"stale-session"}]};\n',
                encoding="utf-8",
            )

            with patch.object(app_server, "GENERATED_SESSION_JS", generated_path):
                app_server.write_placeholder_generated_session()

            self.assertEqual("window.EDB_UI_SESSION = null;\n", generated_path.read_text(encoding="utf-8"))

    def test_latest_session_does_not_fallback_to_generated_session_bridge(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            latest_path = tmpdir / "missing_latest.json"
            generated_path = tmpdir / "generated_session.js"
            generated_path.write_text(
                'window.EDB_UI_SESSION = {"problems":[{"id":"old-session"}]};\n',
                encoding="utf-8",
            )

            with (
                patch.object(app_server, "LATEST_SESSION_JSON", latest_path),
                patch.object(app_server, "GENERATED_SESSION_JS", generated_path),
            ):
                self.assertIsNone(app_server.load_latest_session())

    def test_file_download_streams_without_reading_entire_artifact(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            artifact = tmpdir / "large.edb"
            payload = (b"0123456789abcdef" * 70000) + b"tail"
            artifact.write_bytes(payload)
            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = type("FakeServer", (), {
                "allowed_files": {str(artifact.resolve())},
            })()
            statuses = []
            headers = []
            handler.wfile = io.BytesIO()
            handler.send_response = lambda status: statuses.append(status)
            handler.send_header = lambda name, value: headers.append((name, value))
            handler.end_headers = lambda: None
            handler.send_error = lambda status, message=None: statuses.append(status)

            parsed = app_server.urlparse(app_server.path_to_api_url(artifact))
            with patch.object(Path, "read_bytes", side_effect=AssertionError("read_bytes should not be used for downloads")):
                handler._handle_file(parsed)

            self.assertEqual([app_server.HTTPStatus.OK], statuses)
            self.assertIn(("Content-Length", str(len(payload))), headers)
            self.assertEqual(payload, handler.wfile.getvalue())

    def test_problem_image_download_streams_named_png_attachment(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            crop = tmpdir / "crop.png"
            Image.new("RGB", (12, 8), "white").save(crop)
            payload = crop.read_bytes()
            session = {
                "problems": [{
                    "id": "p1",
                    "title": "문항 1",
                    "imagePath": crop.resolve().as_uri(),
                    "boardRenderPath": crop.resolve().as_uri(),
                }],
            }
            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = type("FakeServer", (), {
                "latest_session": session,
            })()
            statuses = []
            headers = []
            handler.wfile = io.BytesIO()
            handler.send_response = lambda status: statuses.append(status)
            handler.send_header = lambda name, value: headers.append((name, value))
            handler.end_headers = lambda: None
            handler.send_error = lambda status, message=None: statuses.append(status)

            parsed = app_server.urlparse("/api/session/problem-image?problemId=p1")
            handler._handle_session_problem_image(parsed)

            self.assertEqual([app_server.HTTPStatus.OK], statuses)
            self.assertIn(("Content-Type", "image/png"), headers)
            self.assertIn(("Content-Length", str(len(payload))), headers)
            disposition = dict(headers)["Content-Disposition"]
            self.assertIn("filename*=UTF-8''01_%EB%AC%B8%ED%95%AD_1.png", disposition)
            self.assertEqual(payload, handler.wfile.getvalue())

    def test_problem_image_download_returns_404_without_image(self):
        session = {"problems": [{"id": "p1", "title": "문항 1"}]}
        handler = object.__new__(app_server.AppRequestHandler)
        handler.server = type("FakeServer", (), {
            "latest_session": session,
        })()
        statuses = []
        handler.send_error = lambda status, message=None: statuses.append((status, message))

        parsed = app_server.urlparse("/api/session/problem-image?problemId=p1")
        handler._handle_session_problem_image(parsed)

        self.assertEqual([(app_server.HTTPStatus.NOT_FOUND, "problem image not found")], statuses)


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

    def test_retry_ai_clamps_forced_ai_worker_count_from_env(self):
        previous_workers = os.environ.get("EDB_RECOGNITION_WORKERS")
        previous_ai_workers = os.environ.get("EDB_AI_MAX_WORKERS")
        os.environ["EDB_RECOGNITION_WORKERS"] = "8"
        os.environ.pop("EDB_AI_MAX_WORKERS", None)
        try:
            with TemporaryDirectory() as raw_tmp:
                tmpdir = Path(raw_tmp)
                pages = []
                problems = []
                page_ids = []
                for index in range(1, 5):
                    page_id = f"page-{index}"
                    page_ids.append(page_id)
                    image_path = tmpdir / f"{page_id}.png"
                    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
                    problem_id = f"{page_id}-p1"
                    pages.append({
                        "id": page_id,
                        "sourceImageUri": image_path.resolve().as_uri(),
                        "problemIds": [problem_id],
                        "riskFlags": [],
                    })
                    problems.append({
                        "id": problem_id,
                        "sourcePageId": page_id,
                        "bbox": {"left": 0, "top": 0, "width": 100, "height": 100},
                        "riskFlags": ["needs_review"],
                    })
                session = {
                    "pages": pages,
                    "problems": problems,
                    "ai_fallback": {"provider": "gemini"},
                }

                with patch.object(app_server, "run_problem_export", side_effect=_fake_run_problem_export):
                    new_session = app_server._mutate_retry_ai(session, {"pageIds": page_ids})

                summaries = new_session.get("ai_retry_summary") or []
                self.assertEqual(4, len(summaries))
                self.assertEqual({3}, {summary.get("workerCount") for summary in summaries})
        finally:
            if previous_workers is None:
                os.environ.pop("EDB_RECOGNITION_WORKERS", None)
            else:
                os.environ["EDB_RECOGNITION_WORKERS"] = previous_workers
            if previous_ai_workers is None:
                os.environ.pop("EDB_AI_MAX_WORKERS", None)
            else:
                os.environ["EDB_AI_MAX_WORKERS"] = previous_ai_workers

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

    def test_partial_retry_replaces_only_selected_problem_and_offsets_bbox(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            page_path = tmpdir / "page-1.png"
            Image.new("RGB", (240, 180), "white").save(page_path)
            session = {
                "output_dir": str(tmpdir / "out"),
                "pages": [{
                    "id": "page-1",
                    "sourceImageUri": page_path.resolve().as_uri(),
                    "problemIds": ["p1", "p2"],
                }],
                "problems": [
                    {
                        "id": "p1",
                        "sourcePageId": "page-1",
                        "bbox": {"left": 20, "top": 30, "width": 80, "height": 90},
                        "riskFlags": ["needs_review"],
                    },
                    {
                        "id": "p2",
                        "sourcePageId": "page-1",
                        "bbox": {"left": 120, "top": 30, "width": 80, "height": 90},
                        "riskFlags": [],
                    },
                ],
                "ai_fallback": {"provider": "gemini"},
            }

            def fake_partial_export(source_path, **kwargs):
                self.assertEqual("partial_source.png", Path(source_path).name)
                self.assertEqual("single-problem", kwargs["input_intent"])
                return {
                    "ui_session": {
                        "pages": [{"id": "partial", "riskFlags": []}],
                        "problems": [{
                            "id": "partial-p1",
                            "sourcePageId": "partial",
                            "bbox": {"left": 2, "top": 3, "width": 40, "height": 50},
                            "riskFlags": [],
                        }],
                    }
                }

            with patch.object(app_server, "run_problem_export", side_effect=fake_partial_export):
                new_session = app_server._mutate_retry_ai(
                    session,
                    {
                        "partial": True,
                        "problemIds": ["p1"],
                        "cropBox": {"left": 10, "top": 20, "width": 80, "height": 90},
                    },
                )

            problem_ids = [problem["id"] for problem in new_session["problems"]]
            self.assertNotIn("p1", problem_ids)
            self.assertIn("p2", problem_ids)
            replacement = next(problem for problem in new_session["problems"] if problem["id"] != "p2")
            self.assertEqual("page-1", replacement["sourcePageId"])
            self.assertEqual(12.0, replacement["bbox"]["left"])
            self.assertEqual(23.0, replacement["bbox"]["top"])
            self.assertEqual(40.0, replacement["bbox"]["width"])
            self.assertEqual(50.0, replacement["bbox"]["height"])
            self.assertTrue(replacement["aiRetry"]["partial"])
            self.assertEqual([replacement["id"], "p2"], new_session["pages"][0]["problemIds"])
            self.assertEqual("applied", new_session["ai_retry_summary"][0]["status"])
            self.assertTrue(new_session["ai_retry_summary"][0]["partial"])


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


class TestSessionCropMutation(unittest.TestCase):
    def test_bbox_crop_wraps_fractional_edges_outward(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            source_path = tmpdir / "page.png"
            output_path = tmpdir / "crop.png"
            Image.new("RGB", (80, 60), "white").save(source_path)

            size = app_server._crop_image_by_bbox(
                source_path,
                Box(10.2, 20.2, 20.1, 10.1),
                output_path,
            )

            self.assertEqual((21, 11), size)
            with Image.open(output_path) as crop:
                self.assertEqual((21, 11), crop.size)

    def test_manual_crop_updates_bbox_image_and_can_reset(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            source_path = tmpdir / "problem.png"
            Image.new("RGB", (200, 120), "white").save(source_path)
            session = {
                "output_dir": str(tmpdir / "out"),
                "pages": [{"id": "page-1", "problemIds": ["p1"]}],
                "problems": [{
                    "id": "p1",
                    "sourcePageId": "page-1",
                    "imagePath": source_path.resolve().as_uri(),
                    "boardRenderPath": source_path.resolve().as_uri(),
                    "bbox": {"left": 10, "top": 20, "width": 100, "height": 80},
                }],
            }

            cropped_session = app_server._mutate_crop(
                session,
                "p1",
                {"leftRatio": 0.1, "rightRatio": 0.2, "topRatio": 0.25, "bottomRatio": 0.05},
            )

            problem = cropped_session["problems"][0]
            self.assertEqual({"left": 10.0, "top": 20.0, "width": 100.0, "height": 80.0}, problem["cropBaseBbox"])
            self.assertEqual({"leftRatio": 0.1, "rightRatio": 0.2, "topRatio": 0.25, "bottomRatio": 0.05}, problem["manualCrop"])
            self.assertEqual(20.0, problem["bbox"]["left"])
            self.assertEqual(40.0, problem["bbox"]["top"])
            self.assertEqual(70.0, problem["bbox"]["width"])
            self.assertEqual(56.0, problem["bbox"]["height"])
            crop_path = app_server._resolve_session_path(problem["imagePath"])
            self.assertIsNotNone(crop_path)
            self.assertTrue(crop_path.exists())
            self.assertEqual((140, 84), Image.open(crop_path).size)

            reset_session = app_server._mutate_crop(cropped_session, "p1", {"leftRatio": 0})
            reset_problem = reset_session["problems"][0]
            self.assertEqual({"left": 10.0, "top": 20.0, "width": 100.0, "height": 80.0}, reset_problem["bbox"])
            self.assertEqual(source_path.resolve().as_uri(), reset_problem["imagePath"])
            self.assertEqual(
                {"leftRatio": 0.0, "rightRatio": 0.0, "topRatio": 0.0, "bottomRatio": 0.0},
                reset_problem["manualCrop"],
            )

    def test_manual_crop_can_expand_from_source_page(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            page_path = tmpdir / "page.png"
            problem_path = tmpdir / "problem.png"
            Image.new("RGB", (300, 200), "white").save(page_path)
            Image.new("RGB", (100, 80), "white").save(problem_path)
            session = {
                "output_dir": str(tmpdir / "out"),
                "pages": [{
                    "id": "page-1",
                    "sourceImageUri": page_path.resolve().as_uri(),
                    "problemIds": ["p1"],
                }],
                "problems": [{
                    "id": "p1",
                    "sourcePageId": "page-1",
                    "imagePath": problem_path.resolve().as_uri(),
                    "boardRenderPath": problem_path.resolve().as_uri(),
                    "bbox": {"left": 50, "top": 40, "width": 100, "height": 80},
                }],
            }

            expanded_session = app_server._mutate_crop(
                session,
                "p1",
                {"leftRatio": -0.1, "rightRatio": -0.2, "topRatio": -0.25, "bottomRatio": -0.05},
            )

            problem = expanded_session["problems"][0]
            self.assertEqual(40.0, problem["bbox"]["left"])
            self.assertEqual(20.0, problem["bbox"]["top"])
            self.assertEqual(130.0, problem["bbox"]["width"])
            self.assertEqual(104.0, problem["bbox"]["height"])
            self.assertEqual(-0.1, problem["manualCrop"]["leftRatio"])
            self.assertEqual(-0.2, problem["manualCrop"]["rightRatio"])
            crop_path = app_server._resolve_session_path(problem["imagePath"])
            self.assertIsNotNone(crop_path)
            self.assertEqual((130, 104), Image.open(crop_path).size)

    def test_manual_crop_accepts_absolute_crop_box(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            page_path = tmpdir / "page.png"
            problem_path = tmpdir / "problem.png"
            Image.new("RGB", (300, 200), "white").save(page_path)
            Image.new("RGB", (100, 80), "white").save(problem_path)
            session = {
                "output_dir": str(tmpdir / "out"),
                "pages": [{
                    "id": "page-1",
                    "sourceImageUri": page_path.resolve().as_uri(),
                    "problemIds": ["p1"],
                }],
                "problems": [{
                    "id": "p1",
                    "sourcePageId": "page-1",
                    "imagePath": problem_path.resolve().as_uri(),
                    "boardRenderPath": problem_path.resolve().as_uri(),
                    "bbox": {"left": 50, "top": 40, "width": 100, "height": 80},
                }],
            }

            cropped_session = app_server._mutate_crop(
                session,
                "p1",
                {"cropBox": {"left": 35, "top": 30, "width": 150, "height": 120}},
            )

            problem = cropped_session["problems"][0]
            self.assertEqual({"left": 35.0, "top": 30.0, "width": 150.0, "height": 120.0}, problem["bbox"])
            self.assertAlmostEqual(-0.15, problem["manualCrop"]["leftRatio"])
            self.assertAlmostEqual(-0.35, problem["manualCrop"]["rightRatio"])
            self.assertAlmostEqual(-0.125, problem["manualCrop"]["topRatio"])
            self.assertAlmostEqual(-0.375, problem["manualCrop"]["bottomRatio"])
            crop_path = app_server._resolve_session_path(problem["imagePath"])
            self.assertIsNotNone(crop_path)
            self.assertEqual((150, 120), Image.open(crop_path).size)

    def test_manual_crop_refreshes_board_render_for_processed_steps(self):
        from PIL import Image, ImageDraw

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            page_path = tmpdir / "page.png"
            problem_path = tmpdir / "problem.png"
            page = Image.new("RGB", (300, 200), "white")
            draw = ImageDraw.Draw(page)
            draw.rectangle((70, 55, 175, 125), outline="black", width=4)
            page.save(page_path)
            page.crop((50, 40, 150, 120)).save(problem_path)
            session = {
                "output_dir": str(tmpdir / "out"),
                "pages": [{
                    "id": "page-1",
                    "sourceImageUri": page_path.resolve().as_uri(),
                    "problemIds": ["p1"],
                }],
                "problems": [{
                    "id": "p1",
                    "sourcePageId": "page-1",
                    "imagePath": problem_path.resolve().as_uri(),
                    "boardRenderPath": problem_path.resolve().as_uri(),
                    "bbox": {"left": 50, "top": 40, "width": 100, "height": 80},
                    "step": "s2",
                }],
            }

            cropped_session = app_server._mutate_crop(
                session,
                "p1",
                {"cropBox": {"left": 45, "top": 35, "width": 145, "height": 110}},
            )

            problem = cropped_session["problems"][0]
            crop_path = app_server._resolve_session_path(problem["imagePath"])
            board_path = app_server._resolve_session_path(problem["boardRenderPath"])
            self.assertIsNotNone(crop_path)
            self.assertIsNotNone(board_path)
            self.assertTrue(crop_path.exists())
            self.assertTrue(board_path.exists())
            self.assertNotEqual(crop_path, board_path)
            self.assertEqual("s2", problem["step"])
            with Image.open(board_path) as board_image:
                self.assertIn("A", board_image.getbands())

    def test_bulk_crop_replaces_source_problem_with_multiple_png_entries(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            page_path = tmpdir / "page.png"
            Image.new("RGB", (300, 200), "white").save(page_path)
            session = {
                "output_dir": str(tmpdir / "out"),
                "pages": [{
                    "id": "page-1",
                    "sourceImageUri": page_path.resolve().as_uri(),
                    "problemIds": ["p0", "p1", "p2"],
                }],
                "problems": [
                    {
                        "id": "p0",
                        "sourcePageId": "page-1",
                        "bbox": {"left": 0, "top": 0, "width": 10, "height": 10},
                    },
                    {
                        "id": "p1",
                        "title": "원본",
                        "sourcePageId": "page-1",
                        "sourceFileName": "page.png",
                        "sourceImagePath": page_path.resolve().as_uri(),
                        "bbox": {"left": 0, "top": 0, "width": 300, "height": 200},
                        "actualHeightPages": 1.9,
                        "actual_height_pages": 1.9,
                        "startYPages": 3.6,
                        "start_y_pages": 3.6,
                        "snappedNextStartYPages": 6.0,
                        "snapped_next_start_y_pages": 6.0,
                        "slotSpanCount": 2,
                        "slot_span_count": 2,
                        "riskFlags": ["large_block_dominance"],
                        "recordMode": "image-only",
                    },
                    {
                        "id": "p2",
                        "sourcePageId": "page-1",
                        "bbox": {"left": 10, "top": 10, "width": 20, "height": 20},
                    },
                ],
            }

            updated = app_server._mutate_bulk_crop(
                session,
                "page-1",
                [
                    {"bbox": {"left": 10, "top": 20, "width": 50, "height": 40}, "title": "직접 1"},
                    {"bbox": {"left": 250, "top": 180, "width": 100, "height": 50}},
                ],
                ["p1"],
            )

            problem_ids = [problem["id"] for problem in updated["problems"]]
            self.assertEqual("p0", problem_ids[0])
            self.assertEqual("p2", problem_ids[-1])
            created = updated["problems"][1:3]
            created_ids = [problem["id"] for problem in created]
            self.assertEqual(created_ids, updated["pages"][0]["problemIds"][1:3])
            self.assertEqual(["p0", *created_ids, "p2"], updated["pages"][0]["problemIds"])
            self.assertEqual("직접 1", created[0]["title"])
            self.assertEqual("문항 02", created[1]["title"])
            self.assertEqual("p1", created[0]["replacesProblemId"])
            self.assertEqual("p1", created[0]["replaces_problem_id"])
            self.assertEqual("p1", created[1]["replacesProblemId"])
            self.assertEqual("p1", created[1]["replaces_problem_id"])
            self.assertEqual("image-only", created[0]["recordMode"])
            self.assertEqual(1, created[0]["imageRecordCount"])
            self.assertEqual([], created[0]["riskFlags"])
            self.assertEqual("normal", created[0]["reviewStatus"])
            self.assertEqual(page_path.resolve().as_uri(), created[0]["sourceImagePath"])
            self.assertEqual({"left": 250.0, "top": 180.0, "width": 50.0, "height": 20.0}, created[1]["bbox"])
            self.assertAlmostEqual(
                app_server.estimate_height_pages((50, 40), app_server.LayoutTemplate(name="academy-default")),
                created[0]["actualHeightPages"],
            )
            self.assertAlmostEqual(created[0]["actualHeightPages"], created[0]["actual_height_pages"])
            self.assertNotIn("startYPages", created[0])
            self.assertNotIn("snappedNextStartYPages", created[0])
            self.assertNotIn("slotSpanCount", created[0])

            first_crop = app_server._resolve_session_path(created[0]["imagePath"])
            second_crop = app_server._resolve_session_path(created[1]["imagePath"])
            self.assertIsNotNone(first_crop)
            self.assertIsNotNone(second_crop)
            self.assertTrue(first_crop.exists())
            self.assertTrue(second_crop.exists())
            with Image.open(first_crop) as first_image:
                self.assertEqual((50, 40), first_image.size)
            with Image.open(second_crop) as second_image:
                self.assertEqual((50, 20), second_image.size)

    def test_session_image_export_zip_uses_order_fallback_and_safe_names(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            raw_1 = tmpdir / "raw-1.png"
            board_1 = tmpdir / "board-1.png"
            raw_2 = tmpdir / "raw-2.png"
            Image.new("RGB", (20, 10), "white").save(raw_1)
            Image.new("RGB", (24, 12), "black").save(board_1)
            Image.new("RGB", (18, 8), "blue").save(raw_2)
            session = {
                "session_name": "국어 수업",
                "problems": [
                    {
                        "id": "p1",
                        "title": "문항 01/위험:*",
                        "sourcePageId": "page-1",
                        "bbox": {"left": 1, "top": 2, "width": 3, "height": 4},
                        "imagePath": raw_1.resolve().as_uri(),
                        "boardRenderPath": board_1.resolve().as_uri(),
                    },
                    {
                        "id": "p2",
                        "title": "문항 02",
                        "sourcePageId": "page-1",
                        "bbox": {"left": 5, "top": 6, "width": 7, "height": 8},
                        "imagePath": raw_2.resolve().as_uri(),
                        "boardRenderPath": (tmpdir / "missing-board.png").resolve().as_uri(),
                    },
                ],
            }

            with patch.object(app_server, "RUNTIME_DIR", tmpdir / "runtime"):
                result = app_server._write_session_image_export_zip(session, "both", problem_ids=["p2", "p1"])

            self.assertEqual(2, result["count"])
            self.assertEqual([], result["missing"])
            zip_path = Path(result["zipPath"])
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
                self.assertIn("edb_images/001_문항_02.png", names)
                self.assertIn("raw_crops/001_문항_02.png", names)
                self.assertIn("edb_images/002_문항_01_위험.png", names)
                self.assertIn("raw_crops/002_문항_01_위험.png", names)
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

            self.assertEqual("국어 수업", manifest["sessionName"])
            self.assertEqual("both", manifest["mode"])
            self.assertEqual(2, manifest["count"])
            self.assertEqual(["p2", "p1"], [item["problemId"] for item in manifest["items"]])
            self.assertEqual("edb_images/001_문항_02.png", manifest["items"][0]["edbImage"])
            self.assertEqual("raw_crops/001_문항_02.png", manifest["items"][0]["rawCrop"])

    def test_session_export_images_handler_allows_generated_zip_file(self):
        from PIL import Image

        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            crop = tmpdir / "crop.png"
            Image.new("RGB", (12, 8), "white").save(crop)
            session = {
                "session_name": "수업",
                "problems": [{
                    "id": "p1",
                    "title": "문항 1",
                    "sourcePageId": "page-1",
                    "bbox": {"left": 0, "top": 0, "width": 12, "height": 8},
                    "imagePath": crop.resolve().as_uri(),
                    "boardRenderPath": crop.resolve().as_uri(),
                }],
            }
            fake_server = type("FakeServer", (), {
                "latest_session": session,
                "allowed_files": set(),
            })()
            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = fake_server
            handler._read_json_body = lambda: {"mode": "edb"}
            responses = []
            handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

            with patch.object(app_server, "RUNTIME_DIR", tmpdir / "runtime"):
                handler._handle_session_export_images()

            body = responses[0][0]
            self.assertTrue(body["ok"])
            self.assertTrue(body["downloadUrl"].startswith("/api/file?path="))
            self.assertIn(body["zipPath"], fake_server.allowed_files)
            self.assertTrue(Path(body["zipPath"]).exists())


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

    def test_uploaded_files_reuse_digest_path_without_rereading_existing_file(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            upload_dir = tmpdir / "uploads"
            upload_dir.mkdir()
            handler = object.__new__(app_server.AppRequestHandler)
            payload = {
                "fileName": "same.hwp",
                "fileDataBase64": base64.b64encode(b"same hwp bytes").decode("ascii"),
            }

            with patch.object(app_server, "UPLOAD_DIR", upload_dir):
                first = handler._save_uploaded_file(payload)
                with patch.object(Path, "read_bytes", side_effect=AssertionError("should not reread cache hit")):
                    second = handler._save_uploaded_file(payload)

            self.assertEqual(first, second)

    def test_uploaded_file_rejects_malformed_base64(self):
        with TemporaryDirectory() as raw_tmp:
            upload_dir = Path(raw_tmp) / "uploads"
            upload_dir.mkdir()
            handler = object.__new__(app_server.AppRequestHandler)

            with patch.object(app_server, "UPLOAD_DIR", upload_dir):
                with self.assertRaises(ValueError) as ctx:
                    handler._save_uploaded_file({
                        "fileName": "broken.hwp",
                        "fileDataBase64": "not valid base64!!!",
                    })

            self.assertIn("valid base64", str(ctx.exception))

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

    def test_page_as_is_export_forces_source_preserving_preprocessing(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            source = tmpdir / "source.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            output_dir = tmpdir / "out"
            payload = {
                "files": [str(source)],
                "outputDir": str(output_dir),
                "inputIntent": "page-as-is",
                "preview": True,
                "exportEdb": False,
                "detectPerspective": True,
                "skipCrop": False,
                "skipDeskew": False,
            }
            captured_kwargs = {}

            class FakeServer:
                allowed_files = set()

                def remember_session(self, session):
                    self.latest_session = session

            def fake_run_problem_export(_source, **kwargs):
                captured_kwargs.update(kwargs)
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

            with patch.object(app_server, "run_problem_export", side_effect=fake_run_problem_export):
                handler._handle_export()

            self.assertEqual("page-as-is", captured_kwargs["input_intent"])
            self.assertFalse(captured_kwargs["detect_perspective"])
            self.assertTrue(captured_kwargs["skip_crop"])
            self.assertTrue(captured_kwargs["skip_deskew"])
            self.assertTrue(responses[0][0]["ok"])

    def test_export_passes_sanitized_requested_edb_name(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            source = tmpdir / "sample.hwp"
            source.write_bytes(b"hwp")
            output_dir = tmpdir / "out"
            captured_kwargs: dict[str, object] = {}
            payload = {
                "files": [str(source)],
                "outputDir": str(output_dir),
                "edbName": "../Renamed Lesson?.edb",
                "preview": True,
                "exportEdb": False,
            }

            class FakeServer:
                allowed_files = set()

                def remember_session(self, session):
                    self.latest_session = session

            def fake_run_problem_export(_source, **kwargs):
                captured_kwargs.update(kwargs)
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

            with patch.object(app_server, "run_problem_export", side_effect=fake_run_problem_export):
                handler._handle_export()

            self.assertEqual("Renamed_Lesson.edb", captured_kwargs["edb_name"])
            self.assertEqual(1, len(responses))
            self.assertTrue(responses[0][0]["ok"])

    def test_export_response_exposes_classin_preflight_from_session(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            source = tmpdir / "sample.hwp"
            source.write_bytes(b"hwp")
            output_dir = tmpdir / "out"
            preflight = {
                "passed": False,
                "status": "needs_attention",
                "issueCount": 1,
                "issues": [{"type": "board_placement_overlap", "problemId": "p1"}],
            }
            payload = {
                "files": [str(source)],
                "outputDir": str(output_dir),
                "preview": True,
                "exportEdb": False,
            }

            class FakeServer:
                allowed_files = set()

                def remember_session(self, session):
                    self.latest_session = session

            def fake_run_problem_export(_source, **kwargs):
                resolved_output = Path(kwargs.get("output_dir") or output_dir)
                return {
                    "ok": True,
                    "ui_session": {
                        "pages": [],
                        "problems": [],
                        "classinPreflight": preflight,
                        "classin_preflight": preflight,
                    },
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

            with patch.object(app_server, "run_problem_export", side_effect=fake_run_problem_export):
                handler._handle_export()

            body = responses[0][0]
            self.assertEqual(preflight, body["classinPreflight"])
            self.assertEqual(preflight, body["classin_preflight"])
            self.assertEqual("needs_attention", body["classinPreflightStatus"])
            self.assertEqual(1, body["classinPreflightIssueCount"])
            self.assertFalse(body["classinPreflightPassed"])

    def test_export_response_synthesizes_single_edb_part_metadata(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            source = tmpdir / "sample.hwp"
            source.write_bytes(b"hwp")
            output_dir = tmpdir / "out"
            edb_path = output_dir / "lesson.edb"
            payload = {
                "files": [str(source)],
                "outputDir": str(output_dir),
                "preview": True,
            }

            class FakeServer:
                allowed_files = set()

                def remember_session(self, session):
                    self.latest_session = session

            def fake_run_problem_export(_source, **kwargs):
                resolved_output = Path(kwargs.get("output_dir") or output_dir)
                resolved_output.mkdir(parents=True, exist_ok=True)
                edb_path.write_bytes(b"edb")
                return {
                    "ok": True,
                    "ui_session": {"pages": [], "problems": [{"id": "p1"}]},
                    "output_dir": str(resolved_output),
                    "ui_session_path": str(resolved_output / "ui_session.json"),
                    "edb_path": edb_path,
                    "summary": {"placements": [{"problem_id": "p1"}]},
                }

            responses = []
            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = FakeServer()
            handler._read_json_body = lambda: dict(payload)
            handler._send_json = lambda body, **kwargs: responses.append((body, kwargs))

            with (
                patch.object(app_server, "run_problem_export", side_effect=fake_run_problem_export),
                patch.object(app_server, "validate_edb_file", return_value={
                    "outerSize": 10,
                    "innerSize": 8,
                    "pageCountHint": 50,
                    "recordCountHint": 1,
                    "recordCountActual": 1,
                }),
            ):
                handler._handle_export()

            body = responses[0][0]
            self.assertTrue(body["ok"], body)
            self.assertFalse(body["edbSplit"])
            self.assertEqual(1, body["edbPartCount"])
            self.assertEqual("lesson.edb", body["edbParts"][0]["edbFileName"])
            self.assertFalse(body["session"]["edbSplit"])
            self.assertEqual(1, body["session"]["edbPartCount"])

    def test_validate_edb_parts_rejects_page_hint_over_classin_limit(self):
        with TemporaryDirectory() as raw_tmp:
            edb_path = Path(raw_tmp) / "too-long.edb"
            edb_path.write_bytes(b"edb")

            with patch.object(app_server, "validate_edb_file", return_value={
                "outerSize": 10,
                "innerSize": 8,
                "pageCountHint": 51,
                "recordCountHint": 1,
                "recordCountActual": 1,
            }):
                with self.assertRaises(ValueError) as ctx:
                    app_server._validate_edb_parts([
                        {"edbPath": str(edb_path), "edbFileName": edb_path.name, "recordCount": 1}
                    ])

            self.assertIn("exceeds ClassIn limit 50", str(ctx.exception))


class TestSessionPublishPreflightGuard(unittest.TestCase):
    def _publish(self, session: dict, payload: dict | None = None):
        class FakeServer:
            def __init__(self, latest_session):
                self.latest_session = latest_session
                self.remembered_session = None

            def remember_session(self, new_session):
                self.latest_session = new_session
                self.remembered_session = new_session

        responses = []
        handler = object.__new__(app_server.AppRequestHandler)
        handler.server = FakeServer(session)
        handler._read_json_body = lambda: dict(payload or {})
        handler._send_json = lambda body, **kwargs: responses.append((body, kwargs))
        return handler, responses

    def test_session_publish_uses_requested_name_and_splits_over_fifty_pages(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = {
                "session_name": "fallback lesson",
                "output_dir": str(root),
                "input_files": [],
                "pages": [],
                "problems": [
                    {
                        "id": f"p{i}",
                        "title": f"{i + 1}.",
                        "bbox": {},
                        "riskFlags": [],
                    }
                    for i in range(26)
                ],
            }
            handler, responses = self._publish(session, {"edbName": "../Renamed Lesson?.edb"})
            captured: dict[str, object] = {}
            entries = [object() for _ in range(26)]

            def fake_build_records(received_entries, template, **_kwargs):
                captured["entry_count"] = len(received_entries)
                captured["board_page_count"] = template.board_page_count
                return ([b"record"] * len(received_entries), [], 3)

            def fake_write_classin_limited_edb_files(received_entries, template, output_dir, edb_name, **_kwargs):
                captured["split_entry_count"] = len(received_entries)
                captured["split_board_page_count"] = template.board_page_count
                captured["split_edb_name"] = edb_name
                paths = [
                    Path(output_dir) / "Renamed_Lesson_part01.edb",
                    Path(output_dir) / "Renamed_Lesson_part02.edb",
                ]
                for path in paths:
                    path.write_bytes(b"edb")
                return [
                    {
                        "partIndex": 1,
                        "partCount": 2,
                        "edbPath": str(paths[0]),
                        "edbFileName": paths[0].name,
                        "recordCount": 13,
                        "pageCountHint": 50,
                    },
                    {
                        "partIndex": 2,
                        "partCount": 2,
                        "edbPath": str(paths[1]),
                        "edbFileName": paths[1].name,
                        "recordCount": 13,
                        "pageCountHint": 50,
                    },
                ]

            def fake_validate(path, *, expected_min_records=1):
                captured.setdefault("validated_paths", []).append(Path(path).name)
                captured.setdefault("expected_min_records", []).append(expected_min_records)
                return {
                    "outerSize": 10,
                    "innerSize": 8,
                    "pageCountHint": 50,
                    "recordCountHint": expected_min_records,
                    "recordCountActual": expected_min_records,
                }

            def fake_build_ui_session(**_kwargs):
                return {
                    "session_name": "published",
                    "output_dir": str(root),
                    "problems": [
                        {"id": f"p{i}", "title": f"{i + 1}.", "riskFlags": [], "bbox": {}}
                        for i in range(26)
                    ],
                    "pages": [],
                }

            def fake_write_handoff(output_dir, **_kwargs):
                handoff_path = Path(output_dir) / "classin_handoff.json"
                handoff_md_path = Path(output_dir) / "classin_handoff.md"
                handoff_path.write_text(
                    json.dumps({
                        "classinPreflight": {
                            "status": "passed",
                            "passed": True,
                            "issueCount": 0,
                            "issues": [],
                        }
                    }),
                    encoding="utf-8",
                )
                handoff_md_path.write_text("# handoff", encoding="utf-8")
                return handoff_path, handoff_md_path

            with (
                patch.object(app_server, "_problems_to_entries", return_value=entries),
                patch.object(app_server, "build_records", side_effect=fake_build_records),
                patch.object(app_server, "write_classin_limited_edb_files", side_effect=fake_write_classin_limited_edb_files),
                patch.object(app_server, "build_ui_session", side_effect=fake_build_ui_session),
                patch.object(app_server, "validate_edb_file", side_effect=fake_validate),
                patch.object(app_server, "write_classin_handoff_manifest", side_effect=fake_write_handoff),
            ):
                handler._handle_session_publish()

            self.assertEqual(1, len(responses))
            body, _kwargs = responses[0]
            self.assertTrue(body["ok"], body)
            self.assertEqual(26, captured["entry_count"])
            self.assertEqual(52, captured["board_page_count"])
            self.assertEqual(26, captured["split_entry_count"])
            self.assertEqual(52, captured["split_board_page_count"])
            self.assertEqual("Renamed_Lesson.edb", captured["split_edb_name"])
            self.assertEqual(["Renamed_Lesson_part01.edb", "Renamed_Lesson_part02.edb"], captured["validated_paths"])
            self.assertEqual([13, 13], captured["expected_min_records"])
            self.assertEqual("Renamed_Lesson_part01.edb", body["publishSummary"]["edbFileName"])
            self.assertEqual(50, body["publishSummary"]["pageCountHint"])
            self.assertTrue(body["publishSummary"]["edbSplit"])
            self.assertEqual(2, body["publishSummary"]["edbPartCount"])
            self.assertEqual(["Renamed_Lesson_part01.edb", "Renamed_Lesson_part02.edb"], [
                part["edbFileName"] for part in body["publishSummary"]["edbParts"]
            ])
            self.assertEqual(
                "Renamed_Lesson_part01.edb",
                handler.server.remembered_session["publishSummary"]["edbFileName"],
            )

    def test_session_publish_blocks_source_bbox_overlap_before_build(self):
        with TemporaryDirectory() as raw_tmp:
            session = {
                "session_name": "source-overlap",
                "output_dir": raw_tmp,
                "pages": [{"id": "page-1", "problemIds": ["p21", "p22"]}],
                "problems": [
                    {
                        "id": "p21",
                        "title": "21.",
                        "problemNumber": 21,
                        "sourcePageId": "page-1",
                        "bbox": {"left": 40, "top": 100, "width": 520, "height": 320},
                    },
                    {
                        "id": "p22",
                        "title": "22.",
                        "problemNumber": 22,
                        "sourcePageId": "page-1",
                        "bbox": {"left": 60, "top": 125, "width": 500, "height": 300},
                    },
                ],
            }
            handler, responses = self._publish(session)

            with patch.object(app_server, "_problems_to_entries", side_effect=AssertionError("build should be blocked")) as entries:
                handler._handle_session_publish()

            self.assertEqual(1, len(responses))
            body, kwargs = responses[0]
            self.assertFalse(body["ok"])
            self.assertEqual("publish_preflight_blocked", body["errorKind"])
            self.assertEqual(app_server.HTTPStatus.CONFLICT, kwargs.get("status"))
            issue_types = {issue["type"] for issue in body["classinPreflight"]["issues"]}
            self.assertIn("source_problem_bbox_overlap", issue_types)
            self.assertEqual(["p21", "p22"], body["blockingProblemIds"])
            self.assertEqual(["p21", "p22"], body["blocking_problem_ids"])
            entries.assert_not_called()

    def test_session_publish_blocks_passage_group_source_reuse_before_build(self):
        with TemporaryDirectory() as raw_tmp:
            session = {
                "session_name": "passage-source-reuse",
                "output_dir": raw_tmp,
                "pages": [{"id": "page-4", "problemIds": ["p22", "p23"]}],
                "problems": [
                    {
                        "id": "p22",
                        "title": "22.",
                        "problemNumber": 22,
                        "sourcePageId": "page-4",
                        "bbox": {"left": 42, "top": 120, "width": 520, "height": 430},
                        "passageGroupId": "hwp-continuation-passage-22-26",
                        "passageRole": "child_question",
                    },
                    {
                        "id": "p23",
                        "title": "23.",
                        "problemNumber": 23,
                        "sourcePageId": "page-4",
                        "bbox": {"left": 48, "top": 132, "width": 510, "height": 410},
                        "passageGroupId": "hwp-continuation-passage-22-26",
                        "passageRole": "child_question",
                    },
                ],
            }
            handler, responses = self._publish(session)

            with patch.object(app_server, "_problems_to_entries", side_effect=AssertionError("build should be blocked")) as entries:
                handler._handle_session_publish()

            self.assertEqual(1, len(responses))
            body, kwargs = responses[0]
            self.assertFalse(body["ok"])
            self.assertEqual("publish_preflight_blocked", body["errorKind"])
            self.assertEqual(app_server.HTTPStatus.CONFLICT, kwargs.get("status"))
            issue_types = {issue["type"] for issue in body["classinPreflight"]["issues"]}
            self.assertIn("passage_group_source_reuse", issue_types)
            self.assertEqual(["p22", "p23"], body["blockingProblemIds"])
            self.assertEqual(["p22", "p23"], body["blocking_problem_ids"])
            entries.assert_not_called()

    def test_session_publish_blocks_duplicate_problem_numbers_before_build(self):
        with TemporaryDirectory() as raw_tmp:
            session = {
                "session_name": "duplicate-number",
                "output_dir": raw_tmp,
                "pages": [
                    {"id": "page-1", "problemIds": ["p7-a"]},
                    {"id": "page-2", "problemIds": ["p7-b"]},
                ],
                "problems": [
                    {
                        "id": "p7-a",
                        "title": "7.",
                        "problemNumber": 7,
                        "sourcePageId": "page-1",
                        "bbox": {"left": 10, "top": 10, "width": 120, "height": 100},
                    },
                    {
                        "id": "p7-b",
                        "title": "7.",
                        "problemNumber": 7,
                        "sourcePageId": "page-2",
                        "bbox": {"left": 10, "top": 140, "width": 120, "height": 100},
                    },
                ],
            }
            handler, responses = self._publish(session)

            with patch.object(app_server, "_problems_to_entries", side_effect=AssertionError("build should be blocked")) as entries:
                handler._handle_session_publish()

            body, kwargs = responses[0]
            self.assertFalse(body["ok"])
            self.assertEqual("publish_preflight_blocked", body["errorKind"])
            self.assertEqual(app_server.HTTPStatus.CONFLICT, kwargs.get("status"))
            issue_types = {issue["type"] for issue in body["classinPreflight"]["issues"]}
            self.assertIn("duplicate_problem_number", issue_types)
            self.assertEqual("7", body["blockingDuplicateProblemNumberGroups"][0]["numberLabel"])
            self.assertEqual(["p7-a", "p7-b"], body["blockingProblemIds"])
            self.assertEqual(["p7-a", "p7-b"], body["blocking_problem_ids"])
            entries.assert_not_called()

    def test_session_publish_reflows_board_placement_overlap_before_build(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = {
                "session_name": "placement-overlap",
                "output_dir": raw_tmp,
                "pages": [
                    {"id": "page-1", "problemIds": ["p13"]},
                    {"id": "page-2", "problemIds": ["p14"]},
                ],
                "problems": [
                    {
                        "id": "p13",
                        "title": "13.",
                        "problemNumber": 13,
                        "sourcePageId": "page-1",
                        "bbox": {"left": 10, "top": 10, "width": 120, "height": 100},
                        "startYPages": 0.0,
                        "actualHeightPages": 1.1,
                    },
                    {
                        "id": "p14",
                        "title": "14.",
                        "problemNumber": 14,
                        "sourcePageId": "page-2",
                        "bbox": {"left": 10, "top": 140, "width": 120, "height": 100},
                        "startYPages": 1.2,
                        "actualHeightPages": 0.8,
                    },
                ],
            }
            handler, responses = self._publish(
                session,
                {"placements": {"p13": {"placementScaleRatio": 1.4}}},
            )

            def fake_problems_to_entries(problems, **_kwargs):
                self.assertEqual(["p13", "p14"], [problem["id"] for problem in problems])
                self.assertEqual(1.4, problems[0]["placementScaleRatio"])
                return [
                    app_server.ProblemEntry(
                        problem_id=problem["id"],
                        title=problem["title"],
                        problem_number=problem["problemNumber"],
                        subject=app_server.resolve_subject("math"),
                        source_page_id=problem["sourcePageId"],
                        source_path=problem["sourcePageId"],
                        prepared_page=None,
                        bounds=Box(left=0, top=0, width=100, height=100),
                        crop_path=root / f"{problem['id']}.png",
                        board_render_path=root / f"{problem['id']}.png",
                        blocks=[],
                        actual_height_pages=problem["actualHeightPages"],
                        overflow_allowed=True,
                        reading_heavy=False,
                        risk_flags=[],
                        placement_scale_ratio=problem.get("placementScaleRatio"),
                    )
                    for problem in problems
                ]

            def fake_build_records(entries, _template, **_kwargs):
                self.assertEqual(["p13", "p14"], [entry.problem_id for entry in entries])
                return (
                    [{"record": "p13"}, {"record": "p14"}],
                    [
                        {
                            "problem_id": "p13",
                            "title": "13.",
                            "record_index": 0,
                            "crop_path": str(root / "p13.png"),
                            "board_render_path": str(root / "p13.png"),
                            "start_y_pages": 0.0,
                            "snapped_next_start_y_pages": 2.4,
                            "actual_height_pages": 1.1,
                            "placement_scale_ratio": 1.4,
                        },
                        {
                            "problem_id": "p14",
                            "title": "14.",
                            "record_index": 1,
                            "crop_path": str(root / "p14.png"),
                            "board_render_path": str(root / "p14.png"),
                            "start_y_pages": 2.4,
                            "snapped_next_start_y_pages": 3.6,
                            "actual_height_pages": 0.8,
                            "placement_scale_ratio": 1.0,
                        },
                    ],
                    0,
                )

            def fake_build_ui_session(**kwargs):
                placements = kwargs["placements"]
                return {
                    "session_name": "published",
                    "output_dir": str(root),
                    "problems": [
                        {
                            "id": placement["problem_id"],
                            "title": placement["title"],
                            "problemNumber": 13 + index,
                            "sourcePageId": f"page-{index + 1}",
                            "imagePath": (root / f"p{13 + index}.png").resolve().as_uri(),
                            "bbox": {},
                            "riskFlags": [],
                            "startYPages": placement["start_y_pages"],
                            "snappedNextStartYPages": placement["snapped_next_start_y_pages"],
                            "actualHeightPages": placement["actual_height_pages"],
                            "placementScaleRatio": placement["placement_scale_ratio"],
                        }
                        for index, placement in enumerate(placements)
                    ],
                    "pages": [],
                }

            def fake_write_handoff(output_dir, *, ui_session, **_kwargs):
                handoff_path = Path(output_dir) / "classin_handoff.json"
                handoff_md_path = Path(output_dir) / "classin_handoff.md"
                handoff_path.write_text(
                    json.dumps({
                        "status": "ready_for_classin_review",
                        "readyForClassIn": True,
                        "classinPreflight": {"status": "passed", "passed": True, "issueCount": 0, "issues": []},
                    }),
                    encoding="utf-8",
                )
                handoff_md_path.write_text("# handoff", encoding="utf-8")
                return handoff_path, handoff_md_path

            with (
                patch.object(app_server, "_problems_to_entries", side_effect=fake_problems_to_entries) as entries,
                patch.object(app_server, "build_records", side_effect=fake_build_records),
                patch.object(app_server, "build_ui_session", side_effect=fake_build_ui_session),
                patch.object(app_server, "build_edb", return_value=b"edb"),
                patch.object(app_server, "write_edb", side_effect=lambda path, data: Path(path).write_bytes(data)),
                patch.object(app_server, "validate_edb_file", return_value={
                    "outerSize": 10,
                    "innerSize": 8,
                    "pageCountHint": 50,
                    "recordCountHint": 2,
                    "recordCountActual": 2,
                }),
                patch.object(app_server, "write_classin_handoff_manifest", side_effect=fake_write_handoff),
            ):
                handler._handle_session_publish()

            body, kwargs = responses[0]
            self.assertTrue(body["ok"], body)
            self.assertNotEqual(app_server.HTTPStatus.CONFLICT, kwargs.get("status"))
            entries.assert_called_once()
            remembered = handler.server.remembered_session
            self.assertEqual(0.0, remembered["problems"][0]["startYPages"])
            self.assertEqual(2.4, remembered["problems"][1]["startYPages"])
            self.assertEqual(1.4, remembered["problems"][0]["placementScaleRatio"])

    def test_session_publish_blocks_unresolved_passage_review_queue_before_build(self):
        with TemporaryDirectory() as raw_tmp:
            session = {
                "session_name": "passage-review-queue",
                "output_dir": raw_tmp,
                "pages": [
                    {"id": "page-5", "problemIds": ["p31"]},
                    {"id": "page-6", "problemIds": ["p32"]},
                ],
                "passageReviewItems": [
                    {
                        "groupId": "hwp-text-passage-31-32",
                        "numberLabel": "31-32",
                        "problemIds": ["p31", "p32"],
                        "fragmentProblemIds": ["page-5-continuation"],
                        "sourcePageIds": ["page-5", "page-6"],
                        "problemCount": 2,
                        "fragmentProblemCount": 1,
                        "continuesAcrossPages": True,
                        "reviewReasonCodes": ["cross_page_passage_group", "passage_fragment"],
                        "riskFlags": ["passage_cross_page_merge_check"],
                        "message": "31-32 긴 지문 그룹은 2개 페이지와 2개 하위 문항, 이어짐 자료 1개를 확인해야 합니다.",
                    }
                ],
                "passageReviewItemCount": 1,
                "crossPagePassageReviewItemCount": 1,
                "problems": [
                    {
                        "id": "p31",
                        "title": "31.",
                        "problemNumber": 31,
                        "sourcePageId": "page-5",
                        "bbox": {"left": 10, "top": 10, "width": 120, "height": 100},
                        "reviewStatus": "check_needed",
                        "riskFlags": [],
                    },
                    {
                        "id": "p32",
                        "title": "32.",
                        "problemNumber": 32,
                        "sourcePageId": "page-6",
                        "bbox": {"left": 20, "top": 20, "width": 130, "height": 100},
                        "reviewStatus": "normal",
                        "riskFlags": [],
                    },
                ],
            }
            handler, responses = self._publish(session)

            with patch.object(app_server, "_problems_to_entries", side_effect=AssertionError("build should be blocked")) as entries:
                handler._handle_session_publish()

            body, kwargs = responses[0]
            self.assertFalse(body["ok"])
            self.assertEqual("publish_preflight_blocked", body["errorKind"])
            self.assertEqual(app_server.HTTPStatus.CONFLICT, kwargs.get("status"))
            self.assertIn("제작 전 확인", body["error"])
            self.assertNotIn("겹침/중복", body["error"])
            issues = body["classinPreflight"]["issues"]
            issue_types = {issue["type"] for issue in issues}
            self.assertIn("passage_review_queue_remaining", issue_types)
            queue_issue = next(issue for issue in issues if issue["type"] == "passage_review_queue_remaining")
            self.assertEqual(["p31", "p32", "page-5-continuation"], queue_issue["problemIds"])
            self.assertEqual(["page-5-continuation"], queue_issue["fragmentProblemIds"])
            self.assertEqual(["cross_page_passage_group", "passage_fragment"], queue_issue["reviewReasonCodes"])
            self.assertEqual(["passage_cross_page_merge_check"], queue_issue["riskFlags"])
            self.assertEqual(2, queue_issue["problemCount"])
            self.assertEqual(1, queue_issue["fragmentProblemCount"])
            self.assertIn("이어짐 자료 1개", queue_issue["message"])
            entries.assert_not_called()

    def test_session_publish_excludes_supplemental_passage_fragments_from_edb_entries(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            fragment_crop = root / "page-2-continuation.png"
            p22_crop = root / "p22.png"
            p23_crop = root / "p23.png"
            for crop_path in (fragment_crop, p22_crop, p23_crop):
                crop_path.write_bytes(b"fake image")

            session = {
                "session_name": "passage-fragment-publish",
                "output_dir": str(root),
                "input_files": [str(root / "source.hwp")],
                "pages": [
                    {"id": "page-2", "problemIds": ["page-2-continuation", "p22"]},
                    {"id": "page-3", "problemIds": ["p23"]},
                ],
                "problems": [
                    {
                        "id": "page-2-continuation",
                        "title": "지문 계속",
                        "sourcePageId": "page-2",
                        "subject": "korean",
                        "imagePath": fragment_crop.resolve().as_uri(),
                        "bbox": {"left": 10, "top": 20, "width": 520, "height": 420},
                        "riskFlags": ["marker_document_continuation", "passage_cross_page_merge_check"],
                        "passageGroupId": "hwp-continuation-passage-22-23",
                        "passageRole": "passage_fragment",
                        "passageRange": {"start": 22, "end": 23},
                        "passageChildProblemNumbers": [22, 23],
                        "passageSourcePageIds": ["page-2", "page-3"],
                        "passageContinuesAcrossPages": True,
                    },
                    {
                        "id": "p22",
                        "title": "22.",
                        "problemNumber": 22,
                        "sourcePageId": "page-2",
                        "subject": "korean",
                        "imagePath": p22_crop.resolve().as_uri(),
                        "bbox": {"left": 30, "top": 60, "width": 500, "height": 360},
                        "passageGroupId": "hwp-continuation-passage-22-23",
                        "passageRole": "child_question",
                        "passageRange": {"start": 22, "end": 23},
                        "passageChildProblemNumbers": [22, 23],
                        "passageSourcePageIds": ["page-2", "page-3"],
                        "passageContinuesAcrossPages": True,
                    },
                    {
                        "id": "p23",
                        "title": "23.",
                        "problemNumber": 23,
                        "sourcePageId": "page-3",
                        "subject": "korean",
                        "imagePath": p23_crop.resolve().as_uri(),
                        "bbox": {"left": 30, "top": 430, "width": 500, "height": 220},
                        "passageGroupId": "hwp-continuation-passage-22-23",
                        "passageRole": "child_question",
                        "passageRange": {"start": 22, "end": 23},
                        "passageChildProblemNumbers": [22, 23],
                        "passageSourcePageIds": ["page-2", "page-3"],
                        "passageContinuesAcrossPages": True,
                    },
                ],
            }
            handler, responses = self._publish(session)

            def fake_build_records(entries, template, **_kwargs):
                self.assertEqual(["p22", "p23"], [entry.problem_id for entry in entries])
                return (
                    [{"record": "p22"}, {"record": "p23"}],
                    [
                        {
                            "problem_id": "p22",
                            "title": "22.",
                            "record_index": 0,
                            "crop_path": str(p22_crop),
                            "board_render_path": str(p22_crop),
                            "start_y_pages": 0.0,
                            "actual_height_pages": 1.0,
                        },
                        {
                            "problem_id": "p23",
                            "title": "23.",
                            "record_index": 1,
                            "crop_path": str(p23_crop),
                            "board_render_path": str(p23_crop),
                            "start_y_pages": 2.0,
                            "actual_height_pages": 1.0,
                        },
                    ],
                    0,
                )

            def fake_build_ui_session(**_kwargs):
                return {
                    "session_name": "published",
                    "output_dir": str(root),
                    "problems": [
                        {
                            "id": "p22",
                            "title": "22.",
                            "problemNumber": 22,
                            "sourcePageId": "page-2",
                            "imagePath": p22_crop.resolve().as_uri(),
                            "bbox": {},
                            "riskFlags": [],
                        },
                        {
                            "id": "p23",
                            "title": "23.",
                            "problemNumber": 23,
                            "sourcePageId": "page-3",
                            "imagePath": p23_crop.resolve().as_uri(),
                            "bbox": {},
                            "riskFlags": [],
                        },
                    ],
                    "pages": [],
                }

            def fake_write_handoff(output_dir, *, ui_session, **_kwargs):
                handoff_path = Path(output_dir) / "classin_handoff.json"
                handoff_md_path = Path(output_dir) / "classin_handoff.md"
                handoff_path.write_text(
                    json.dumps(
                        {
                            "status": "ready_for_classin_review",
                            "readyForClassIn": True,
                            "classinPreflight": {"status": "passed", "passed": True, "issueCount": 0, "issues": []},
                            "passageGroups": [
                                {
                                    "id": "hwp-continuation-passage-22-23",
                                    "problemCount": 2,
                                    "continuesAcrossPages": True,
                                    "sourcePageIds": ["page-2", "page-3"],
                                }
                            ],
                            "passageGroupCount": 1,
                            "passageProblemCount": 2,
                            "crossPagePassageGroupCount": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                handoff_md_path.write_text("# handoff", encoding="utf-8")
                return handoff_path, handoff_md_path

            with (
                patch.object(app_server, "build_records", side_effect=fake_build_records),
                patch.object(app_server, "build_ui_session", side_effect=fake_build_ui_session),
                patch.object(app_server, "build_edb", return_value=b"edb"),
                patch.object(app_server, "write_edb", side_effect=lambda path, data: Path(path).write_bytes(data)),
                patch.object(app_server, "validate_edb_file", return_value={
                    "outerSize": 10,
                    "innerSize": 8,
                    "pageCountHint": 50,
                    "recordCountHint": 2,
                    "recordCountActual": 2,
                }),
                patch.object(app_server, "write_classin_handoff_manifest", side_effect=fake_write_handoff),
            ):
                handler._handle_session_publish()

            self.assertEqual(1, len(responses))
            body, kwargs = responses[0]
            self.assertTrue(body["ok"], body)
            self.assertNotEqual(app_server.HTTPStatus.CONFLICT, kwargs.get("status"))
            summary = body["publishSummary"]
            self.assertEqual(2, summary["recordCount"])
            self.assertEqual(2, summary["coreProblemCount"])
            self.assertEqual(1, summary["supplementalItemCount"])
            self.assertEqual("2문항 + 자료 1", summary["recordCountLabel"])
            self.assertEqual(["p22", "p23"], [problem["id"] for problem in handler.server.remembered_session["problems"]])

    def test_session_publish_allows_official_alternate_section_duplicate_numbers(self):
        problems = []
        for section_index, source_prefix in enumerate(("speech-writing", "language-media")):
            for number in range(35, 41):
                problems.append({
                    "id": f"{source_prefix}-{number}",
                    "title": f"{number}.",
                    "problemNumber": number,
                    "sourcePageId": f"{source_prefix}-{number}",
                    "bbox": {
                        "left": 10,
                        "top": 20 + section_index * 220,
                        "width": 120,
                        "height": 100,
                    },
                })

        preflight, duplicate_groups = app_server._session_publish_blocking_preflight(problems)

        self.assertTrue(preflight["passed"])
        self.assertEqual("passed", preflight["status"])
        self.assertEqual(0, preflight["issueCount"])
        self.assertEqual([], duplicate_groups)

    def test_session_publish_preserves_passage_groups_in_publish_summary(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            crop_path = root / "p13.png"
            crop_path.write_bytes(b"fake image")
            session = {
                "session_name": "passage-publish",
                "output_dir": str(root),
                "input_files": [str(root / "source.hwp")],
                "pages": [{"id": "page-1", "problemIds": ["p13"]}],
                "problems": [
                    {
                        "id": "p13",
                        "title": "13.",
                        "problemNumber": 13,
                        "sourcePageId": "page-1",
                        "imagePath": crop_path.resolve().as_uri(),
                        "bbox": {"left": 10, "top": 10, "width": 120, "height": 100},
                        "passageGroupId": "page-1-passage-13-16",
                        "passageRange": {"start": 13, "end": 16},
                        "passageSourcePageIds": ["page-1", "page-2"],
                        "passageContinuesAcrossPages": True,
                    }
                ],
            }
            handler, responses = self._publish(session)

            def fake_build_records(entries, template, **_kwargs):
                return (
                    [{"record": "p13"}],
                    [
                        {
                            "problem_id": "p13",
                            "title": "13.",
                            "record_index": 0,
                            "crop_path": str(crop_path),
                            "board_render_path": str(crop_path),
                            "start_y_pages": 0.0,
                            "actual_height_pages": 1.0,
                        }
                    ],
                    0,
                )

            def fake_build_ui_session(**_kwargs):
                return {
                    "session_name": "published",
                    "output_dir": str(root),
                    "problems": [
                        {
                            "id": "p13",
                            "title": "13.",
                            "problemNumber": 13,
                            "sourcePageId": "page-1",
                            "imagePath": crop_path.resolve().as_uri(),
                            "bbox": {},
                            "riskFlags": [],
                        }
                    ],
                    "pages": [],
                }

            def fake_write_handoff(output_dir, *, ui_session, **_kwargs):
                grouped = [
                    problem for problem in ui_session.get("problems", [])
                    if problem.get("passageGroupId") == "page-1-passage-13-16"
                ]
                passage_groups = (
                    [
                        {
                            "id": "page-1-passage-13-16",
                            "problemCount": len(grouped),
                            "continuesAcrossPages": True,
                            "sourcePageIds": ["page-1", "page-2"],
                        }
                    ]
                    if grouped
                    else []
                )
                handoff_path = Path(output_dir) / "classin_handoff.json"
                handoff_md_path = Path(output_dir) / "classin_handoff.md"
                handoff_path.write_text(
                    json.dumps(
                        {
                            "status": "ready_for_classin_review",
                            "readyForClassIn": True,
                            "classinPreflight": {"status": "passed", "passed": True, "issueCount": 0, "issues": []},
                            "passageGroups": passage_groups,
                            "passageGroupCount": len(passage_groups),
                            "passageProblemCount": len(grouped),
                            "crossPagePassageGroupCount": len(passage_groups),
                        }
                    ),
                    encoding="utf-8",
                )
                handoff_md_path.write_text("# handoff", encoding="utf-8")
                return handoff_path, handoff_md_path

            with (
                patch.object(app_server, "build_records", side_effect=fake_build_records),
                patch.object(app_server, "build_ui_session", side_effect=fake_build_ui_session),
                patch.object(app_server, "build_edb", return_value=b"edb"),
                patch.object(app_server, "write_edb", side_effect=lambda path, data: Path(path).write_bytes(data)),
                patch.object(app_server, "validate_edb_file", return_value={
                    "outerSize": 10,
                    "innerSize": 8,
                    "pageCountHint": 50,
                    "recordCountHint": 1,
                    "recordCountActual": 1,
                }),
                patch.object(app_server, "write_classin_handoff_manifest", side_effect=fake_write_handoff),
            ):
                handler._handle_session_publish()

            self.assertEqual(1, len(responses))
            body, _kwargs = responses[0]
            self.assertTrue(body["ok"])
            summary = body["publishSummary"]
            self.assertEqual(1, summary["passageGroupCount"])
            self.assertEqual(1, summary["passageProblemCount"])
            self.assertEqual(1, summary["crossPagePassageGroupCount"])
            self.assertEqual("page-1-passage-13-16", summary["passageGroups"][0]["id"])
            remembered_problem = handler.server.remembered_session["problems"][0]
            self.assertEqual("page-1-passage-13-16", remembered_problem["passageGroupId"])
            self.assertTrue(remembered_problem["passageContinuesAcrossPages"])

    def test_session_publish_preserves_passage_group_source_reuse_in_publish_summary(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            crop_path = root / "p31.png"
            crop_path.write_bytes(b"fake image")
            source_reuse_groups = [
                {
                    "passageGroupId": "hwp-text-passage-31-34",
                    "sourcePageId": "page-004",
                    "problemIds": ["p31", "p32"],
                    "overlapAreaRatio": 0.92,
                }
            ]
            session = {
                "session_name": "passage-reuse-publish",
                "output_dir": str(root),
                "input_files": [str(root / "source.hwp")],
                "pages": [{"id": "page-004", "problemIds": ["p31", "p32"]}],
                "problems": [
                    {
                        "id": "p31",
                        "title": "31.",
                        "problemNumber": 31,
                        "sourcePageId": "page-004",
                        "imagePath": crop_path.resolve().as_uri(),
                        "bbox": {"left": 10, "top": 10, "width": 120, "height": 100},
                    },
                    {
                        "id": "p32",
                        "title": "32.",
                        "problemNumber": 32,
                        "sourcePageId": "page-004",
                        "imagePath": crop_path.resolve().as_uri(),
                        "bbox": {"left": 200, "top": 10, "width": 120, "height": 100},
                    },
                ],
            }
            handler, responses = self._publish(session)

            def fake_build_records(entries, template, **_kwargs):
                return (
                    [{"record": "p31"}, {"record": "p32"}],
                    [
                        {
                            "problem_id": "p31",
                            "title": "31.",
                            "record_index": 0,
                            "crop_path": str(crop_path),
                            "board_render_path": str(crop_path),
                            "start_y_pages": 0.0,
                            "actual_height_pages": 1.0,
                        },
                        {
                            "problem_id": "p32",
                            "title": "32.",
                            "record_index": 1,
                            "crop_path": str(crop_path),
                            "board_render_path": str(crop_path),
                            "start_y_pages": 1.0,
                            "actual_height_pages": 1.0,
                        },
                    ],
                    0,
                )

            def fake_build_ui_session(**_kwargs):
                return {
                    "session_name": "published",
                    "output_dir": str(root),
                    "problems": [
                        {"id": "p31", "title": "31.", "riskFlags": []},
                        {"id": "p32", "title": "32.", "riskFlags": []},
                    ],
                    "pages": [],
                }

            def fake_write_handoff(output_dir, **_kwargs):
                handoff_path = Path(output_dir) / "classin_handoff.json"
                handoff_md_path = Path(output_dir) / "classin_handoff.md"
                handoff_path.write_text(
                    json.dumps(
                        {
                            "status": "ready_for_classin_review",
                            "readyForClassIn": True,
                            "classinPreflight": {"status": "passed", "passed": True, "issueCount": 0, "issues": []},
                            "passageGroupSourceReuseGroups": source_reuse_groups,
                            "passageGroupSourceReuseGroupCount": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                handoff_md_path.write_text("# handoff", encoding="utf-8")
                return handoff_path, handoff_md_path

            with (
                patch.object(app_server, "build_records", side_effect=fake_build_records),
                patch.object(app_server, "build_ui_session", side_effect=fake_build_ui_session),
                patch.object(app_server, "build_edb", return_value=b"edb"),
                patch.object(app_server, "write_edb", side_effect=lambda path, data: Path(path).write_bytes(data)),
                patch.object(app_server, "validate_edb_file", return_value={
                    "outerSize": 10,
                    "innerSize": 8,
                    "pageCountHint": 50,
                    "recordCountHint": 2,
                    "recordCountActual": 2,
                }),
                patch.object(app_server, "write_classin_handoff_manifest", side_effect=fake_write_handoff),
            ):
                handler._handle_session_publish()

            self.assertEqual(1, len(responses))
            body, _kwargs = responses[0]
            self.assertTrue(body["ok"])
            summary = body["publishSummary"]
            self.assertEqual(source_reuse_groups, summary["passageGroupSourceReuseGroups"])
            self.assertEqual(source_reuse_groups, summary["passage_group_source_reuse_groups"])
            self.assertEqual(1, summary["passageGroupSourceReuseGroupCount"])
            self.assertEqual(1, summary["passage_group_source_reuse_group_count"])
            self.assertEqual(source_reuse_groups, handler.server.remembered_session["publishSummary"]["passageGroupSourceReuseGroups"])

    def test_session_publish_preserves_source_problem_overlap_in_publish_summary(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            crop_path = root / "p31.png"
            crop_path.write_bytes(b"fake image")
            source_overlap_groups = [
                {
                    "sourcePageId": "page-004",
                    "problemIds": ["p31", "p32"],
                    "overlapAreaRatio": 0.88,
                }
            ]
            session = {
                "session_name": "source-overlap-publish",
                "output_dir": str(root),
                "input_files": [str(root / "source.hwp")],
                "pages": [{"id": "page-004", "problemIds": ["p31", "p32"]}],
                "problems": [
                    {
                        "id": "p31",
                        "title": "31.",
                        "problemNumber": 31,
                        "sourcePageId": "page-004",
                        "imagePath": crop_path.resolve().as_uri(),
                        "bbox": {"left": 10, "top": 10, "width": 120, "height": 100},
                    },
                    {
                        "id": "p32",
                        "title": "32.",
                        "problemNumber": 32,
                        "sourcePageId": "page-004",
                        "imagePath": crop_path.resolve().as_uri(),
                        "bbox": {"left": 200, "top": 10, "width": 120, "height": 100},
                    },
                ],
            }
            handler, responses = self._publish(session)

            def fake_build_records(entries, template, **_kwargs):
                return (
                    [{"record": "p31"}, {"record": "p32"}],
                    [
                        {
                            "problem_id": "p31",
                            "title": "31.",
                            "record_index": 0,
                            "crop_path": str(crop_path),
                            "board_render_path": str(crop_path),
                            "start_y_pages": 0.0,
                            "actual_height_pages": 1.0,
                        },
                        {
                            "problem_id": "p32",
                            "title": "32.",
                            "record_index": 1,
                            "crop_path": str(crop_path),
                            "board_render_path": str(crop_path),
                            "start_y_pages": 1.0,
                            "actual_height_pages": 1.0,
                        },
                    ],
                    0,
                )

            def fake_build_ui_session(**_kwargs):
                return {
                    "session_name": "published",
                    "output_dir": str(root),
                    "problems": [
                        {"id": "p31", "title": "31.", "riskFlags": []},
                        {"id": "p32", "title": "32.", "riskFlags": []},
                    ],
                    "pages": [],
                }

            def fake_write_handoff(output_dir, **_kwargs):
                handoff_path = Path(output_dir) / "classin_handoff.json"
                handoff_md_path = Path(output_dir) / "classin_handoff.md"
                handoff_path.write_text(
                    json.dumps(
                        {
                            "status": "ready_for_classin_review",
                            "readyForClassIn": True,
                            "classinPreflight": {"status": "passed", "passed": True, "issueCount": 0, "issues": []},
                            "sourceProblemOverlapGroups": source_overlap_groups,
                            "sourceProblemOverlapGroupCount": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                handoff_md_path.write_text("# handoff", encoding="utf-8")
                return handoff_path, handoff_md_path

            with (
                patch.object(app_server, "build_records", side_effect=fake_build_records),
                patch.object(app_server, "build_ui_session", side_effect=fake_build_ui_session),
                patch.object(app_server, "build_edb", return_value=b"edb"),
                patch.object(app_server, "write_edb", side_effect=lambda path, data: Path(path).write_bytes(data)),
                patch.object(app_server, "validate_edb_file", return_value={
                    "outerSize": 10,
                    "innerSize": 8,
                    "pageCountHint": 50,
                    "recordCountHint": 2,
                    "recordCountActual": 2,
                }),
                patch.object(app_server, "write_classin_handoff_manifest", side_effect=fake_write_handoff),
            ):
                handler._handle_session_publish()

            self.assertEqual(1, len(responses))
            body, _kwargs = responses[0]
            self.assertTrue(body["ok"])
            summary = body["publishSummary"]
            self.assertEqual(source_overlap_groups, summary["sourceProblemOverlapGroups"])
            self.assertEqual(source_overlap_groups, summary["source_problem_overlap_groups"])
            self.assertEqual(1, summary["sourceProblemOverlapGroupCount"])
            self.assertEqual(1, summary["source_problem_overlap_group_count"])
            self.assertEqual("원본 겹침 1 · page-004 88%", summary["sourceProblemOverlapLabel"])
            self.assertEqual("원본 겹침 1 · page-004 88%", summary["source_problem_overlap_label"])
            self.assertEqual(source_overlap_groups, handler.server.remembered_session["publishSummary"]["sourceProblemOverlapGroups"])

    def test_session_publish_summary_prefers_resolved_session_passage_review_queue(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            crop_path = root / "p31.png"
            crop_path.write_bytes(b"fake image")
            session = {
                "session_name": "resolved-passage-publish",
                "output_dir": str(root),
                "input_files": [str(root / "source.hwp")],
                "pages": [{"id": "page-5", "problemIds": ["p31"]}],
                "passageReviewItems": [
                    {
                        "groupId": "hwp-text-passage-31-32",
                        "numberLabel": "31-32",
                        "problemIds": ["p31"],
                        "continuesAcrossPages": True,
                    }
                ],
                "passageReviewItemCount": 1,
                "crossPagePassageReviewItemCount": 1,
                "problems": [
                    {
                        "id": "p31",
                        "title": "31.",
                        "problemNumber": 31,
                        "sourcePageId": "page-5",
                        "imagePath": crop_path.resolve().as_uri(),
                        "bbox": {"left": 10, "top": 10, "width": 120, "height": 100},
                        "reviewStatus": "normal",
                        "riskFlags": [],
                    }
                ],
            }
            handler, responses = self._publish(session)

            def fake_build_records(entries, template, **_kwargs):
                return (
                    [{"record": "p31"}],
                    [
                        {
                            "problem_id": "p31",
                            "title": "31.",
                            "record_index": 0,
                            "crop_path": str(crop_path),
                            "board_render_path": str(crop_path),
                            "start_y_pages": 0.0,
                            "actual_height_pages": 1.0,
                        }
                    ],
                    0,
                )

            def fake_build_ui_session(**_kwargs):
                return {
                    "session_name": "published",
                    "output_dir": str(root),
                    "problems": [
                        {
                            "id": "p31",
                            "title": "31.",
                            "problemNumber": 31,
                            "sourcePageId": "page-5",
                            "imagePath": crop_path.resolve().as_uri(),
                            "bbox": {},
                            "riskFlags": [],
                        }
                    ],
                    "pages": [],
                }

            def fake_write_handoff(output_dir, **_kwargs):
                handoff_path = Path(output_dir) / "classin_handoff.json"
                handoff_md_path = Path(output_dir) / "classin_handoff.md"
                handoff_path.write_text(
                    json.dumps(
                        {
                            "status": "ready_for_classin_review",
                            "readyForClassIn": True,
                            "classinPreflight": {"status": "passed", "passed": True, "issueCount": 0, "issues": []},
                            "passageReviewItems": [
                                {
                                    "groupId": "hwp-text-passage-31-32",
                                    "numberLabel": "31-32",
                                    "problemIds": ["p31"],
                                    "continuesAcrossPages": True,
                                }
                            ],
                            "passageReviewItemCount": 1,
                            "crossPagePassageReviewItemCount": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                handoff_md_path.write_text("# handoff", encoding="utf-8")
                return handoff_path, handoff_md_path

            with (
                patch.object(app_server, "build_records", side_effect=fake_build_records),
                patch.object(app_server, "build_ui_session", side_effect=fake_build_ui_session),
                patch.object(app_server, "build_edb", return_value=b"edb"),
                patch.object(app_server, "write_edb", side_effect=lambda path, data: Path(path).write_bytes(data)),
                patch.object(app_server, "validate_edb_file", return_value={
                    "outerSize": 10,
                    "innerSize": 8,
                    "pageCountHint": 50,
                    "recordCountHint": 1,
                    "recordCountActual": 1,
                }),
                patch.object(app_server, "write_classin_handoff_manifest", side_effect=fake_write_handoff),
            ):
                handler._handle_session_publish()

            self.assertEqual(1, len(responses))
            body, _kwargs = responses[0]
            self.assertTrue(body["ok"], body)
            summary = body["publishSummary"]
            self.assertEqual([], summary["passageReviewItems"])
            self.assertEqual(0, summary["passageReviewItemCount"])
            self.assertEqual(0, summary["crossPagePassageReviewItemCount"])
            remembered = handler.server.remembered_session
            self.assertEqual([], remembered["passageReviewItems"])
            self.assertEqual(0, remembered["passageReviewItemCount"])

    def test_session_publish_summary_exposes_passage_review_items(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            edb_path = root / "lesson.edb"
            edb_path.write_bytes(b"placeholder")
            review_items = [
                {
                    "groupId": "hwp-text-passage-31-34",
                    "numberLabel": "31-34",
                    "problemIds": ["p31", "p32"],
                    "sourcePageIds": ["page-5", "page-6"],
                    "problemCount": 2,
                    "continuesAcrossPages": True,
                    "reviewReasonCodes": [
                        "cross_page_passage_group",
                        "passage_missing_child_questions",
                        "cross_page_passage_group",
                    ],
                }
            ]

            summary = app_server._session_publish_summary(
                edb_path=edb_path,
                output_dir=root,
                edb_validation={
                    "outerSize": 1234,
                    "innerSize": 987,
                    "pageCountHint": 50,
                    "recordCountHint": 2,
                    "recordCountActual": 2,
                },
                record_count=2,
                passage_review_items=review_items,
                published_at="2026-06-13T12:00:00+09:00",
            )

            self.assertEqual(review_items, summary["passageReviewItems"])
            self.assertEqual(review_items, summary["passage_review_items"])
            self.assertEqual(1, summary["passageReviewItemCount"])
            self.assertEqual(1, summary["passage_review_item_count"])
            self.assertEqual(1, summary["crossPagePassageReviewItemCount"])
            self.assertEqual(1, summary["cross_page_passage_review_item_count"])
            self.assertEqual("페이지 넘김 긴 지문, 지문 하위 문항 누락", summary["passageReviewReasonLabel"])
            self.assertEqual("페이지 넘김 긴 지문, 지문 하위 문항 누락", summary["passage_review_reason_label"])


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
            publish_handoff_json = tmpdir / "publish_classin_handoff.json"
            publish_handoff_md = tmpdir / "publish_classin_handoff.md"
            handoff_json.write_text("{}", encoding="utf-8")
            handoff_md.write_text("# check", encoding="utf-8")
            publish_handoff_json.write_text("{}", encoding="utf-8")
            publish_handoff_md.write_text("# publish check", encoding="utf-8")
            session = {
                "classin_handoff_path": str(handoff_json),
                "classinHandoffMarkdownPath": str(handoff_md),
                "publishSummary": {
                    "classinHandoffPath": str(publish_handoff_json),
                    "classinHandoffMarkdownUri": publish_handoff_md.resolve().as_uri(),
                },
            }

            paths = app_server.collect_session_file_paths(session)
            rewritten = app_server.rewrite_session_for_http(session)

        self.assertIn(str(handoff_json.resolve()), paths)
        self.assertIn(str(handoff_md.resolve()), paths)
        self.assertIn(str(publish_handoff_json.resolve()), paths)
        self.assertIn(str(publish_handoff_md.resolve()), paths)
        self.assertIn("/api/file?path=", rewritten["classin_handoff_uri"])
        self.assertIn("/api/file?path=", rewritten["classin_handoff_markdown_uri"])

    def test_session_history_endpoint_allows_handoff_files_from_recent_work(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            handoff_json = tmpdir / "classin_handoff.json"
            handoff_md = tmpdir / "classin_handoff.md"
            handoff_json.write_text("{}", encoding="utf-8")
            handoff_md.write_text("# check", encoding="utf-8")
            history = [{
                "id": "recent",
                "publishSummary": {
                    "classinHandoffPath": str(handoff_json),
                    "classinHandoffMarkdownPath": str(handoff_md),
                },
            }]
            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = type("FakeServer", (), {"allowed_files": set()})()
            responses = []
            handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

            with patch.object(app_server, "load_session_history", return_value=history):
                handler._handle_session_history()

            self.assertIn(str(handoff_json.resolve()), handler.server.allowed_files)
            self.assertIn(str(handoff_md.resolve()), handler.server.allowed_files)
            self.assertEqual(1, len(responses))
            self.assertTrue(responses[0][0]["ok"])

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

    def test_public_session_history_exposes_classin_handoff_readiness(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            handoff_path = root / "classin_handoff.json"
            handoff_path.write_text(
                json.dumps(
                    {
                        "status": "needs_attention_before_classin",
                        "readyForClassIn": False,
                    }
                ),
                encoding="utf-8",
            )
            session = {
                "session_name": "주의 필요 제작본",
                "generated_at": "2026-06-13T12:00:00+09:00",
                "output_dir": str(root),
                "problems": [{"id": "p1"}],
                "publishSummary": {
                    "edbFileName": "lesson.edb",
                    "edbPath": str(root / "lesson.edb"),
                    "outputDir": str(root),
                    "classinHandoffPath": str(handoff_path),
                },
            }
            history = app_server._session_history_with_session(
                [],
                session,
                updated_at="2026-06-13T12:00:00+09:00",
            )

            public = app_server._public_session_history(history)

            summary = public[0]["publishSummary"]
            self.assertEqual("needs_attention_before_classin", summary["classinHandoffStatus"])
            self.assertFalse(summary["readyForClassIn"])
            self.assertEqual("needs_attention_before_classin", summary["classin_handoff_status"])
            self.assertFalse(summary["ready_for_classin"])

    def test_public_session_history_backfills_passage_review_reason_label(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            session = {
                "session_name": "긴 지문 제작본",
                "generated_at": "2026-06-13T12:00:00+09:00",
                "output_dir": str(root),
                "problems": [{"id": "p31"}, {"id": "p32"}],
                "publishSummary": {
                    "edbFileName": "lesson.edb",
                    "edbPath": str(root / "lesson.edb"),
                    "outputDir": str(root),
                    "passageReviewItems": [
                        {
                            "numberLabel": "31-32",
                            "problemIds": ["p31", "p32"],
                            "reviewReasonCodes": [
                                "cross_page_passage_group",
                                "passage_missing_child_questions",
                            ],
                        }
                    ],
                },
            }
            history = app_server._session_history_with_session(
                [],
                session,
                updated_at="2026-06-13T12:00:00+09:00",
            )

            public = app_server._public_session_history(history)

            summary = public[0]["publishSummary"]
            self.assertEqual("페이지 넘김 긴 지문, 지문 하위 문항 누락", summary["passageReviewReasonLabel"])
            self.assertEqual("페이지 넘김 긴 지문, 지문 하위 문항 누락", summary["passage_review_reason_label"])

    def test_public_session_history_backfills_source_problem_overlap_label(self):
        with TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            source_overlap_groups = [
                {
                    "sourcePageId": "page-004",
                    "problemIds": ["p31", "p32"],
                    "overlapAreaRatio": 0.88,
                }
            ]
            session = {
                "session_name": "원본 겹침 제작본",
                "generated_at": "2026-06-13T12:00:00+09:00",
                "output_dir": str(root),
                "problems": [{"id": "p31"}, {"id": "p32"}],
                "publishSummary": {
                    "edbFileName": "lesson.edb",
                    "edbPath": str(root / "lesson.edb"),
                    "outputDir": str(root),
                    "sourceProblemOverlapGroups": source_overlap_groups,
                },
            }
            history = app_server._session_history_with_session(
                [],
                session,
                updated_at="2026-06-13T12:00:00+09:00",
            )

            public = app_server._public_session_history(history)

            summary = public[0]["publishSummary"]
            self.assertEqual(source_overlap_groups, summary["sourceProblemOverlapGroups"])
            self.assertEqual(source_overlap_groups, summary["source_problem_overlap_groups"])
            self.assertEqual(1, summary["sourceProblemOverlapGroupCount"])
            self.assertEqual(1, summary["source_problem_overlap_group_count"])
            self.assertEqual("원본 겹침 1 · page-004 88%", summary["sourceProblemOverlapLabel"])
            self.assertEqual("원본 겹침 1 · page-004 88%", summary["source_problem_overlap_label"])


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

    def test_session_history_helpers_use_current_default_path(self):
        with TemporaryDirectory() as raw_tmp:
            history_path = Path(raw_tmp) / "patched-history.json"
            session = {
                "session_name": "임시 런타임",
                "generated_at": "2026-06-13T12:00:00+09:00",
                "output_dir": "/tmp/session-patched",
                "problems": [{"id": "p1"}],
            }

            with patch.object(app_server, "SESSION_HISTORY_JSON", history_path):
                history = app_server.remember_session_history(
                    session,
                    updated_at="2026-06-13T12:00:00+09:00",
                )
                loaded = app_server.load_session_history()

            self.assertTrue(history_path.exists())
            self.assertEqual(history, loaded)
            self.assertEqual("임시 런타임", loaded[0]["sessionName"])

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

    def test_latest_session_empty_state_returns_ok_null(self):
        handler = object.__new__(app_server.AppRequestHandler)
        handler.server = type("FakeServer", (), {
            "latest_session": None,
            "allowed_files": set(),
        })()
        responses = []
        handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

        with (
            patch.object(app_server, "load_latest_session", return_value=None),
            patch.object(app_server, "remember_session_history") as mock_history,
        ):
            handler._handle_latest_session()

        mock_history.assert_not_called()
        self.assertEqual([({"ok": True, "session": None}, {})], responses)

    def test_session_clear_removes_latest_session_and_history_files(self):
        with TemporaryDirectory() as raw_tmp:
            tmpdir = Path(raw_tmp)
            latest_path = tmpdir / "latest.json"
            history_path = tmpdir / "history.json"
            generated_path = tmpdir / "generated_session.js"
            latest_path.write_text('{"problems": [{"id": "p1"}]}', encoding="utf-8")
            history_path.write_text('[{"id": "old"}]', encoding="utf-8")
            generated_path.write_text("window.EDB_UI_SESSION = { problems: [] };\n", encoding="utf-8")

            handler = object.__new__(app_server.AppRequestHandler)
            handler.server = type("FakeServer", (), {
                "latest_session": {"problems": [{"id": "p1"}]},
                "allowed_files": {"some-file"},
            })()
            responses = []
            handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

            with (
                patch.object(app_server, "LATEST_SESSION_JSON", latest_path),
                patch.object(app_server, "SESSION_HISTORY_JSON", history_path),
                patch.object(app_server, "GENERATED_SESSION_JS", generated_path),
            ):
                handler._handle_session_clear()

            self.assertFalse(latest_path.exists())
            self.assertFalse(history_path.exists())
            self.assertEqual("window.EDB_UI_SESSION = null;\n", generated_path.read_text(encoding="utf-8"))
            self.assertIsNone(handler.server.latest_session)
            self.assertEqual(set(), handler.server.allowed_files)
            self.assertEqual({"ok": True, "history": []}, responses[0][0])

    def test_shutdown_endpoint_sends_ok_and_stops_server(self):
        shutdown_called = threading.Event()
        handler = object.__new__(app_server.AppRequestHandler)
        handler.server = type("FakeServer", (), {
            "shutdown": lambda _self: shutdown_called.set(),
        })()
        handler.headers = {
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
        }
        responses = []
        handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))

        handler._handle_shutdown()

        self.assertEqual([({"ok": True}, {})], responses)
        self.assertTrue(shutdown_called.wait(1.0))


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

    def test_session_review_summary_keeps_cross_page_passage_checks_actionable(self):
        session = {
            "problems": [
                {
                    "id": "p15",
                    "bbox": {"width": 120, "height": 80},
                    "riskFlags": ["passage_cross_page_merge_check"],
                    "passageGroupId": "page-1-passage-13-16",
                    "passageContinuesAcrossPages": True,
                },
            ],
            "pages": [],
        }

        summary = app_server._session_review_summary(session)

        self.assertEqual(
            {"passage_cross_page_merge_check": 1},
            summary["actionableRiskFlagCounts"],
        )
        self.assertEqual(1, summary["actionableNeedsReviewCount"])

    def test_refresh_session_counts_removes_resolved_passage_review_queue(self):
        session = {
            "passageReviewItems": [
                {
                    "groupId": "hwp-text-passage-31-32",
                    "numberLabel": "31-32",
                    "problemIds": ["p31", "p32"],
                    "sourcePageIds": ["page-5", "page-6"],
                    "problemCount": 2,
                    "continuesAcrossPages": True,
                }
            ],
            "passage_review_items": [
                {
                    "group_id": "hwp-text-passage-31-32",
                    "number_label": "31-32",
                    "problem_ids": ["p31", "p32"],
                    "source_page_ids": ["page-5", "page-6"],
                    "problem_count": 2,
                    "continues_across_pages": True,
                }
            ],
            "passageReviewItemCount": 1,
            "passage_review_item_count": 1,
            "crossPagePassageReviewItemCount": 1,
            "cross_page_passage_review_item_count": 1,
            "problems": [
                {
                    "id": "p31",
                    "bbox": {"width": 120, "height": 80},
                    "reviewStatus": "normal",
                    "riskFlags": [],
                },
                {
                    "id": "p32",
                    "bbox": {"width": 120, "height": 80},
                    "reviewStatus": "normal",
                    "riskFlags": [],
                },
            ],
            "pages": [
                {
                    "id": "page-5",
                    "problemIds": ["p31"],
                    "riskFlags": [],
                },
                {
                    "id": "page-6",
                    "problemIds": ["p32"],
                    "riskFlags": [],
                },
            ],
        }

        app_server._refresh_session_problem_counts(session)

        self.assertEqual([], session["passageReviewItems"])
        self.assertEqual([], session["passage_review_items"])
        self.assertEqual(0, session["passageReviewItemCount"])
        self.assertEqual(0, session["passage_review_item_count"])
        self.assertEqual(0, session["crossPagePassageReviewItemCount"])
        self.assertEqual(0, session["cross_page_passage_review_item_count"])

    def test_refresh_session_counts_keeps_check_needed_passage_review_queue_without_flags(self):
        session = {
            "passageReviewItems": [
                {
                    "groupId": "hwp-text-passage-31-32",
                    "numberLabel": "31-32",
                    "problemIds": ["p31", "p32"],
                    "sourcePageIds": ["page-5", "page-6"],
                    "problemCount": 2,
                    "continuesAcrossPages": True,
                }
            ],
            "passageReviewItemCount": 1,
            "crossPagePassageReviewItemCount": 1,
            "problems": [
                {
                    "id": "p31",
                    "bbox": {"width": 120, "height": 80},
                    "reviewStatus": "check_needed",
                    "riskFlags": [],
                },
                {
                    "id": "p32",
                    "bbox": {"width": 120, "height": 80},
                    "reviewStatus": "normal",
                    "riskFlags": [],
                },
            ],
            "pages": [
                {
                    "id": "page-5",
                    "problemIds": ["p31"],
                    "riskFlags": [],
                },
                {
                    "id": "page-6",
                    "problemIds": ["p32"],
                    "riskFlags": [],
                },
            ],
        }

        app_server._refresh_session_problem_counts(session)

        self.assertEqual(1, len(session["passageReviewItems"]))
        self.assertEqual("31-32", session["passageReviewItems"][0]["numberLabel"])
        self.assertEqual(1, session["passageReviewItemCount"])
        self.assertEqual(1, session["crossPagePassageReviewItemCount"])

    def test_refresh_session_counts_removes_count_only_passage_review_queue(self):
        session = {
            "passageReviewItemCount": 1,
            "passage_review_item_count": 1,
            "crossPagePassageReviewItemCount": 1,
            "cross_page_passage_review_item_count": 1,
            "problems": [
                {
                    "id": "p31",
                    "bbox": {"width": 120, "height": 80},
                    "reviewStatus": "normal",
                    "riskFlags": [],
                },
            ],
            "pages": [
                {
                    "id": "page-5",
                    "problemIds": ["p31"],
                    "riskFlags": [],
                },
            ],
        }

        app_server._refresh_session_problem_counts(session)

        self.assertEqual([], session["passageReviewItems"])
        self.assertEqual([], session["passage_review_items"])
        self.assertEqual(0, session["passageReviewItemCount"])
        self.assertEqual(0, session["passage_review_item_count"])
        self.assertEqual(0, session["crossPagePassageReviewItemCount"])
        self.assertEqual(0, session["cross_page_passage_review_item_count"])

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
