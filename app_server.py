#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
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
from build_problem_board_edb import (
    DEFAULT_BOARD_THEME,
    ProblemEntry,
    build_records,
    build_ui_session,
    resolve_subject,
    run_problem_export,
)
from edb_builder import (
    DEFAULT_CROP_FORMAT,
    build_edb,
    version_string_for_crop_format,
    write_edb,
)
from layout_template_schema import LayoutTemplate
from structured_schema import Box, Subject
from user_settings import (
    apply_to_env as apply_user_settings_to_env,
    load_user_settings,
    summarize_for_response as summarize_user_settings,
    update_gemini_api_key,
)


def load_env_local() -> None:
    # edb_make 전용 .env.local 만 읽어옵니다. (Classin_Home 프로젝트와 완전히 분리)
    env_path = Path(__file__).resolve().parent / ".env.local"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

load_env_local()

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


def hydrate_user_settings_env() -> None:
    """Load persisted user settings and promote secrets into ``os.environ``
    so pipeline modules pick them up via the usual env-var path."""
    apply_user_settings_to_env(load_user_settings(RUNTIME_DIR))


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


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _coerce_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _extract_ai_fallback_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("aiFallback")
    if not isinstance(nested, dict):
        nested = payload.get("ai_fallback")
    if not isinstance(nested, dict):
        nested = {}

    def _field(*names: str, default: Any = None) -> Any:
        for name in names:
            if name in payload and payload[name] is not None:
                return payload[name]
        for name in names:
            if name in nested and nested[name] is not None:
                return nested[name]
        return default

    return {
        "ai_fallback_enabled": _coerce_bool(_field("aiFallbackEnabled", "ai_fallback_enabled", "enabled"), default=False),
        "ai_fallback": _field("aiFallbackMode", "ai_fallback_mode", "mode"),
        "ai_fallback_provider": str(_field("aiFallbackProvider", "ai_fallback_provider", "provider", default="openai")),
        "ai_fallback_model": str(_field("aiFallbackModel", "ai_fallback_model", "model", default="")),
        "ai_fallback_prompt": str(_field("aiFallbackPrompt", "ai_fallback_prompt", "prompt", default="")),
        "ai_fallback_max_tokens": _coerce_optional_int(_field("aiFallbackMaxTokens", "ai_fallback_max_tokens", "maxTokens", "max_tokens")),
        "ai_fallback_temperature": _coerce_optional_float(_field("aiFallbackTemperature", "ai_fallback_temperature", "temperature")),
        "ai_fallback_threshold": _coerce_optional_float(_field("aiFallbackThreshold", "ai_fallback_threshold", "threshold")),
        "ai_fallback_max_regions": _coerce_optional_int(_field("aiFallbackMaxRegions", "ai_fallback_max_regions", "maxRegions", "max_regions")),
        "ai_fallback_timeout_ms": _coerce_optional_int(_field("aiFallbackTimeoutMs", "ai_fallback_timeout_ms", "timeoutMs", "timeout_ms")),
        "ai_fallback_save_debug": _coerce_bool(_field("aiFallbackSaveDebug", "ai_fallback_save_debug", "saveDebug", "save_debug"), default=False),
        "fail_on_ai_error": _coerce_bool(_field("failOnAiError", "fail_on_ai_error"), default=False),
    }


def sanitize_output_dir_name(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return f"mvp_export_{time.strftime('%Y%m%d_%H%M%S')}"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    return safe or f"mvp_export_{time.strftime('%Y%m%d_%H%M%S')}"


def sanitize_upload_file_name(value: str | None) -> str:
    raw = Path(value or "upload.bin").name
    invalid = '<>:"/\\|?*'
    safe = "".join(ch if ch not in invalid and ord(ch) >= 32 else "_" for ch in raw).strip(" .")
    if not safe:
        return "upload.bin"

    path = Path(safe)
    extension = path.suffix[:12]
    stem = path.stem or "upload"
    digest = hashlib.sha1(safe.encode("utf-8", errors="ignore")).hexdigest()[:10]
    trimmed_stem = stem[:48].rstrip(" ._") or "upload"
    return f"{trimmed_stem}_{digest}{extension}"


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


def _file_uri_to_path(value: Any) -> Path | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("file://"):
        parsed = urlparse(text)
        # url2pathname expects a path with a leading slash for absolute paths;
        # urlparse preserves that on Windows (`/C:/...`).
        return Path(url2pathname(parsed.path))
    if text.startswith("/api/file"):
        # session was already rewritten; pull the underlying path back out.
        parsed = urlparse(text)
        params = parse_qs(parsed.query)
        raw = params.get("path", [None])[0]
        return Path(unquote(raw)) if raw else None
    # bare filesystem path
    return Path(text)


def _problems_to_entries(problems: list[dict[str, Any]]) -> list[ProblemEntry]:
    entries: list[ProblemEntry] = []
    for problem in problems:
        crop_path = _file_uri_to_path(problem.get("imagePath"))
        board_render_path = _file_uri_to_path(problem.get("boardRenderPath")) or crop_path
        if crop_path is None or not crop_path.exists():
            raise FileNotFoundError(f"problem {problem.get('id')} crop missing at {crop_path}")
        if not board_render_path.exists():
            board_render_path = crop_path
        bbox = problem.get("bbox") or {}
        entries.append(
            ProblemEntry(
                problem_id=str(problem.get("id") or ""),
                title=str(problem.get("title") or ""),
                problem_number=(int(problem["problemNumber"])
                                if isinstance(problem.get("problemNumber"), (int, float, str))
                                and str(problem.get("problemNumber")).isdigit()
                                else None),
                subject=resolve_subject(problem.get("subject")),
                source_page_id=str(problem.get("sourcePageId") or ""),
                source_path=str(problem.get("sourceFileName") or problem.get("sourcePageId") or ""),
                prepared_page=None,  # image-only mode never touches this
                bounds=Box(
                    left=float(bbox.get("left", 0.0)),
                    top=float(bbox.get("top", 0.0)),
                    width=float(bbox.get("width", 1000.0)),
                    height=float(bbox.get("height", 1000.0)),
                ),
                crop_path=crop_path,
                board_render_path=board_render_path,
                blocks=[],  # image-only mode doesn't use OCR blocks
                actual_height_pages=float(problem.get("actualHeightPages") or 1.2),
                overflow_allowed=bool(problem.get("overflowAllowed", True)),
                reading_heavy=bool(problem.get("readingHeavy", False)),
            )
        )
    return entries


def _template_from_session(session: dict[str, Any]) -> LayoutTemplate:
    template_data = session.get("template") or {}
    kwargs: dict[str, Any] = {"name": str(template_data.get("name") or "academy-default")}
    for key in ("board_page_count", "base_slot_height_pages", "fixed_left_zone_ratio"):
        if key in template_data and template_data[key] is not None:
            kwargs[key] = template_data[key]
    if "preserve_right_writing_zone" in template_data:
        kwargs["preserve_right_writing_zone"] = bool(template_data["preserve_right_writing_zone"])
    return LayoutTemplate(**kwargs)


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
        if parsed.path == "/api/user-settings":
            self._handle_user_settings_get()
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
        if parsed.path == "/api/user-settings":
            self._handle_user_settings_post()
            return
        if parsed.path == "/api/session/publish":
            self._handle_session_publish()
            return
        self._send_json({"ok": False, "error": "unknown endpoint"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/session/latest":
            self._handle_session_clear()
            return
        self._send_json({"ok": False, "error": "unknown endpoint"}, status=HTTPStatus.NOT_FOUND)

    def _handle_session_publish(self) -> None:
        session = self.app_server.latest_session or load_latest_session()
        if session is None:
            self._send_json({"ok": False, "error": "no session available"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        order = list(payload.get("order") or [])
        excluded = set(payload.get("excluded") or [])

        by_id = {p["id"]: p for p in session.get("problems", []) if isinstance(p, dict) and p.get("id")}
        sequence: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pid in order:
            if pid in by_id and pid not in excluded and pid not in seen:
                sequence.append(by_id[pid])
                seen.add(pid)
        for pid, problem in by_id.items():
            if pid in excluded or pid in seen:
                continue
            sequence.append(problem)
            seen.add(pid)

        if not sequence:
            self._send_json({"ok": False, "error": "after exclusion nothing remains to publish"},
                            status=HTTPStatus.BAD_REQUEST)
            return

        try:
            entries = _problems_to_entries(sequence)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": f"missing asset: {exc}"}, status=HTTPStatus.CONFLICT)
            return
        except (KeyError, ValueError) as exc:
            self._send_json({"ok": False, "error": f"session is missing fields needed for publish: {exc}"},
                            status=HTTPStatus.CONFLICT)
            return

        template = _template_from_session(session)
        output_dir = Path(session.get("output_dir") or RUNTIME_DIR / "publish_output").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            records, placements, header_flag = build_records(
                entries,
                template,
                record_mode="image-only",
                output_dir=output_dir,
                text_confidence_threshold=0.78,
                dark_board=True,
                board_theme=session.get("board_theme") or DEFAULT_BOARD_THEME,
                crop_format=session.get("crop_format") or DEFAULT_CROP_FORMAT,
            )
        except Exception as exc:  # noqa: BLE001 — bubble up pipeline errors verbatim
            self._send_json({"ok": False, "error": f"publish build failed: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        stamp = time.strftime("%Y%m%d-%H%M%S")
        edb_name = (session.get("session_name") or "classin") + f"-published-{stamp}.edb"
        edb_path = output_dir / edb_name
        try:
            write_edb(
                edb_path,
                build_edb(
                    records,
                    header_flag=header_flag,
                    version=version_string_for_crop_format(
                        session.get("crop_format") or DEFAULT_CROP_FORMAT
                    ),
                    page_count_hint=template.board_page_count,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": f"failed to write edb: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        new_session = build_ui_session(
            prepared_pages=[],
            placements=placements,
            output_dir=output_dir,
            edb_path=edb_path,
            source_paths=session.get("input_files") or [],
            record_mode="image-only",
            ai_fallback_config=session.get("ai_fallback"),
            ai_summary=session.get("ai_summary"),
        )
        # carry over user-facing labels so the rename doesn't get lost
        if session.get("session_name"):
            new_session["session_name"] = session["session_name"]
        self.app_server.remember_session(new_session)
        self._send_json({"ok": True, "session": rewrite_session_for_http(new_session)})

    def _handle_session_clear(self) -> None:
        self.app_server.latest_session = None
        self.app_server.allowed_files = set()
        try:
            if LATEST_SESSION_JSON.exists():
                LATEST_SESSION_JSON.unlink()
        except OSError as exc:
            self._send_json({"ok": False, "error": f"failed to clear: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        # also blank out the generated_session.js bridge so a refresh shows empty state
        try:
            GENERATED_SESSION_JS.write_text("window.EDB_UI_SESSION = null;\n", encoding="utf-8")
        except OSError:
            pass
        self._send_json({"ok": True})

    def _handle_user_settings_get(self) -> None:
        self._send_json(
            {"ok": True, "settings": summarize_user_settings(RUNTIME_DIR)}
        )

    def _handle_user_settings_post(self) -> None:
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        raw_key = payload.get("geminiApiKey")
        if raw_key is None:
            raw_key = payload.get("gemini_api_key")
        try:
            summary = update_gemini_api_key(RUNTIME_DIR, raw_key if isinstance(raw_key, str) else "")
        except OSError as exc:
            self._send_json({"ok": False, "error": f"failed to persist settings: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"ok": True, "settings": summary})

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
            common_kwargs.update(_extract_ai_fallback_kwargs(payload))
            if export_mode == "page":
                result = run_export(
                    source_paths[0] if len(source_paths) == 1 else source_paths,
                    **common_kwargs,
                )
            else:
                result = run_problem_export(
                    source_paths[0] if len(source_paths) == 1 else source_paths,
                    record_mode=str(payload.get("recordMode") or payload.get("record_mode") or "image-only"),
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
    hydrate_user_settings_env()
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
