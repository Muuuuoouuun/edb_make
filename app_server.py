#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import time
import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import url2pathname

from build_mvp_export import run_export
from build_problem_board_edb import run_problem_export


APP_NAME = "ClassIn EDB MVP Local App"


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


BASE_DIR = app_root()
RESOURCE_DIR = resource_root()
UI_DIR = RESOURCE_DIR / "ui_prototype"
RUNTIME_DIR = BASE_DIR / ".app_runtime"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
LATEST_SESSION_JSON = RUNTIME_DIR / "latest_session.json"
GENERATED_SESSION_JS = UI_DIR / "generated_session.js"


def ensure_runtime_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def write_placeholder_generated_session() -> None:
    if GENERATED_SESSION_JS.exists():
        return
    try:
        GENERATED_SESSION_JS.write_text("window.EDB_UI_SESSION = null;\n", encoding="utf-8")
    except OSError:
        pass


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def sanitize_output_dir_name(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return f"mvp_export_{time.strftime('%Y%m%d_%H%M%S')}"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    return safe or f"mvp_export_{time.strftime('%Y%m%d_%H%M%S')}"


def sanitize_upload_file_name(value: str | None) -> str:
    raw = Path(value or "upload.bin").name
    invalid = '<>:"/\\|?*'
    safe = "".join(ch if ch not in invalid and ord(ch) >= 32 else "_" for ch in raw)
    return safe or "upload.bin"


def decode_file_reference(value: str | None) -> Path | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme == "file":
        path = Path(url2pathname(unquote(parsed.path)))
        return path.resolve()
    path = Path(value)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return path


def path_to_api_url(path: str | Path | None) -> str | None:
    if path is None:
        return None
    resolved = decode_file_reference(str(path))
    if resolved is None:
        return None
    return f"/api/file?path={quote(str(resolved))}"


def load_generated_session() -> dict[str, Any] | None:
    if not GENERATED_SESSION_JS.exists():
        return None
    raw = GENERATED_SESSION_JS.read_text(encoding="utf-8").strip()
    prefix = "window.EDB_UI_SESSION = "
    if not raw.startswith(prefix):
        return None
    payload = raw[len(prefix):].rstrip(";\n ")
    if not payload or payload == "null":
        return None
    return json.loads(payload)


def load_latest_session() -> dict[str, Any] | None:
    if LATEST_SESSION_JSON.exists():
        return json.loads(LATEST_SESSION_JSON.read_text(encoding="utf-8"))
    return load_generated_session()


def collect_session_file_paths(session: dict[str, Any]) -> set[str]:
    paths: set[str] = set()

    def add_path(value: Any) -> None:
        if not value:
            return
        resolved = decode_file_reference(str(value))
        if resolved and resolved.exists():
            paths.add(str(resolved))

    for key in ("edb_path", "pages_json_path", "placements_json_path"):
        add_path(session.get(key))

    for value in session.get("rendered_page_paths", []):
        add_path(value)
    for value in session.get("rendered_page_file_uris", []):
        add_path(value)

    for problem in session.get("problems", []):
        for key in ("imagePath", "sourceImagePath", "boardRenderPath"):
            add_path(problem.get(key))

    return paths


def rewrite_session_for_http(session: dict[str, Any]) -> dict[str, Any]:
    rewritten = json.loads(json.dumps(session))
    rewritten["edb_file_uri"] = path_to_api_url(session.get("edb_path") or session.get("edb_file_uri"))
    rewritten["rendered_page_file_uris"] = [path_to_api_url(value) for value in session.get("rendered_page_paths", [])]

    for problem in rewritten.get("problems", []):
        problem["imagePath"] = path_to_api_url(problem.get("imagePath"))
        problem["sourceImagePath"] = path_to_api_url(problem.get("sourceImagePath"))
        problem["boardRenderPath"] = path_to_api_url(problem.get("boardRenderPath"))
    return rewritten


class AppHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        self.latest_session: dict[str, Any] | None = load_latest_session()
        self.allowed_files: set[str] = collect_session_file_paths(self.latest_session) if self.latest_session else set()

    def remember_session(self, session: dict[str, Any]) -> None:
        self.latest_session = session
        self.allowed_files = collect_session_file_paths(session)
        LATEST_SESSION_JSON.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


class AppRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    @property
    def app_server(self) -> AppHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args) -> None:
        print(f"[app-server] {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "app": APP_NAME})
            return
        if parsed.path == "/api/session/latest":
            self._handle_latest_session()
            return
        if parsed.path == "/api/file":
            self._handle_file(parsed)
            return
        if parsed.path in {"", "/"}:
            self.path = "/index.html"
        else:
            self.path = parsed.path
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/export":
            self._handle_export()
            return
        self._send_json({"ok": False, "error": "unknown endpoint"}, status=HTTPStatus.NOT_FOUND)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        if not raw_body:
            return {}
        return json.loads(raw_body.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_latest_session(self) -> None:
        session = self.app_server.latest_session or load_latest_session()
        if session is None:
            self._send_json({"ok": False, "error": "no session available"}, status=HTTPStatus.NOT_FOUND)
            return
        self.app_server.latest_session = session
        self.app_server.allowed_files |= collect_session_file_paths(session)
        if not LATEST_SESSION_JSON.exists():
            LATEST_SESSION_JSON.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        self._send_json(
            {
                "ok": True,
                "session": rewrite_session_for_http(session),
            }
        )

    def _handle_file(self, parsed) -> None:
        query = parse_qs(parsed.query)
        requested = query.get("path", [None])[0]
        path = decode_file_reference(requested)
        if path is None or not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "file not found")
            return

        normalized = str(path.resolve())
        if normalized not in self.app_server.allowed_files:
            self.send_error(HTTPStatus.FORBIDDEN, "file not allowed")
            return

        mime_type, _ = mimetypes.guess_type(path.name)
        if path.suffix.lower() == ".edb":
            mime_type = "application/octet-stream"

        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        if path.suffix.lower() == ".edb":
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _save_uploaded_file(self, payload: dict[str, Any]) -> Path:
        file_name = payload.get("fileName") or "upload.bin"
        file_data_base64 = payload.get("fileDataBase64")
        if not file_data_base64:
            raise ValueError("fileDataBase64 is required when sourcePath is not provided")
        safe_name = sanitize_upload_file_name(file_name)
        stamped_name = f"{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}_{safe_name}"
        target_path = UPLOAD_DIR / stamped_name
        target_path.write_bytes(base64.b64decode(file_data_base64))
        return target_path

    def _resolve_source_paths(self, payload: dict[str, Any]) -> list[Path]:
        file_payloads = payload.get("files")
        if isinstance(file_payloads, list) and file_payloads:
            return [self._save_uploaded_file(file_payload).resolve() for file_payload in file_payloads]

        source_paths = payload.get("sources") or payload.get("sourcePaths")
        if isinstance(source_paths, list) and source_paths:
            resolved_paths: list[Path] = []
            for source_path in source_paths:
                path = decode_file_reference(str(source_path))
                if path is None:
                    raise FileNotFoundError(f"sourcePath does not exist: {source_path}")
                if not path.exists():
                    raise FileNotFoundError(f"sourcePath does not exist: {path}")
                resolved_paths.append(path.resolve())
            return resolved_paths

        source_path = payload.get("source") or payload.get("sourcePath") or payload.get("source_path")
        if source_path:
            path = decode_file_reference(str(source_path))
            if path is None:
                raise FileNotFoundError(f"sourcePath does not exist: {source_path}")
            if not path.exists():
                raise FileNotFoundError(f"sourcePath does not exist: {path}")
            return [path.resolve()]
        return [self._save_uploaded_file(payload).resolve()]

    def _resolve_output_dir(self, payload: dict[str, Any], source_paths: list[Path]) -> Path:
        requested = payload.get("output_dir") or payload.get("outputDir")
        if requested:
            target = Path(str(requested))
            if not target.is_absolute():
                target = BASE_DIR / sanitize_output_dir_name(str(requested))
            return target.resolve()
        if not source_paths:
            return (BASE_DIR / sanitize_output_dir_name(None)).resolve()
        if len(source_paths) == 1:
            return (BASE_DIR / sanitize_output_dir_name(source_paths[0].stem)).resolve()
        batch_name = f"{source_paths[0].stem}_{len(source_paths)}files"
        return (BASE_DIR / sanitize_output_dir_name(batch_name)).resolve()

    def _handle_export(self) -> None:
        try:
            payload = self._read_json_body()
            source_paths = self._resolve_source_paths(payload)
            output_dir = self._resolve_output_dir(payload, source_paths)
            export_mode = str(payload.get("exportMode") or payload.get("export_mode") or payload.get("layoutMode") or "question").lower()
            common_kwargs = {
                "output_dir": output_dir,
                "subject_name": str(payload.get("subject") or "unknown"),
                "ocr": str(payload.get("ocr") or "auto"),
                "pdf_dpi": int(payload.get("pdfDpi") or payload.get("pdf_dpi") or 200),
                "detect_perspective": _coerce_bool(payload.get("detectPerspective") if "detectPerspective" in payload else payload.get("detect_perspective")),
                "skip_deskew": _coerce_bool(payload.get("skipDeskew") if "skipDeskew" in payload else payload.get("skip_deskew")),
                "skip_crop": _coerce_bool(payload.get("skipCrop") if "skipCrop" in payload else payload.get("skip_crop")),
                "max_dimension": int(payload["maxDimension"]) if payload.get("maxDimension") else int(payload["max_dimension"]) if payload.get("max_dimension") else None,
                "export_edb": _coerce_bool(payload.get("export_edb") if "export_edb" in payload else payload.get("exportEdb"), default=True),
                "edb_name": str(payload.get("edbName") or payload.get("edb_name") or "mvp_board.edb"),
                "sync_ui": False,
            }
            if export_mode == "page":
                result = run_export(
                    source_paths[0] if len(source_paths) == 1 else source_paths,
                    **common_kwargs,
                )
            else:
                result = run_problem_export(
                    source_paths[0] if len(source_paths) == 1 else source_paths,
                    record_mode=str(payload.get("recordMode") or payload.get("record_mode") or "mixed"),
                    text_confidence_threshold=float(payload.get("textConfidenceThreshold") or payload.get("text_confidence_threshold") or 0.78),
                    **common_kwargs,
                )
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        session = result["ui_session"]
        self.app_server.remember_session(session)
        self._send_json(
            {
                "ok": True,
                "session": rewrite_session_for_http(session),
                "output_dir": str(result["output_dir"]),
                "outputDir": str(result["output_dir"]),
                "ui_session_path": str(result["ui_session_path"]),
                "uiSessionPath": str(result["ui_session_path"]),
                "edb_path": str(result["edb_path"]) if result["edb_path"] else None,
                "edbPath": str(result["edb_path"]) if result["edb_path"] else None,
                "export_mode": session.get("export_mode"),
                "exportMode": session.get("export_mode"),
            }
        )


def run_server(*, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    ensure_runtime_dirs()
    write_placeholder_generated_session()
    handler = partial(AppRequestHandler)
    server = AppHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"{APP_NAME} running at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local MVP app server for the ClassIn EDB builder.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8765, help="Bind port")
    parser.add_argument("--open-browser", action="store_true", help="Open the app in the default browser")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, open_browser=args.open_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
