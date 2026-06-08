#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import mimetypes
import os
import struct
import subprocess
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
    recrop_problem,
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
                os.environ[key.strip()] = val.strip()

load_env_local()

APP_NAME = "ClassIn EDB MVP Local App"
INPUT_INTENTS = {"auto", "single-problem", "multi-problem", "page-as-is"}
OUTER_EDB_PREFIX_LEN = 11


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


def _coerce_placement_x_ratio(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("xRatio", "placementXRatio", "placement_x_ratio"):
            if value.get(key) is not None:
                value = value.get(key)
                break
        else:
            return None
    try:
        ratio = _coerce_optional_float(value)
    except (TypeError, ValueError):
        return None
    if ratio is None:
        return None
    return max(0.0, min(1.0, ratio))


def _coerce_placement_y_ratio(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("yRatio", "placementYRatio", "placement_y_ratio"):
            if value.get(key) is not None:
                value = value.get(key)
                break
        else:
            return None
    try:
        ratio = _coerce_optional_float(value)
    except (TypeError, ValueError):
        return None
    if ratio is None:
        return None
    return max(0.0, min(1.0, ratio))


def _coerce_placement_scale_ratio(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("scaleRatio", "placementScaleRatio", "placement_scale_ratio"):
            if value.get(key) is not None:
                value = value.get(key)
                break
        else:
            return None
    try:
        ratio = _coerce_optional_float(value)
    except (TypeError, ValueError):
        return None
    if ratio is None:
        return None
    return max(0.6, min(1.6, ratio))


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
        "ai_fallback_provider": str(_field("aiFallbackProvider", "ai_fallback_provider", "provider", default="gemini")),
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


def _extract_input_intent(payload: dict[str, Any]) -> str:
    raw = payload.get("inputIntent") or payload.get("input_intent") or "auto"
    normalized = str(raw).strip().lower().replace("_", "-")
    return normalized if normalized in INPUT_INTENTS else "auto"


def _extract_input_notes(payload: dict[str, Any]) -> str:
    raw = payload.get("inputNotes")
    if raw is None:
        raw = payload.get("input_notes")
    if raw is None:
        raw = payload.get("pastedText")
    return str(raw or "").strip()


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


def validate_edb_file(path: str | Path, *, expected_min_records: int = 1) -> dict[str, Any]:
    """Fast structural check for the EDB envelope we write.

    This does not attempt to fully emulate ClassIn, but it catches the common
    failure modes that make a file unreadable: missing outer marker, corrupt
    gzip payload, truncated inner header, or a record hint that is impossible
    for the requested publish.
    """
    edb_path = Path(path)
    data = edb_path.read_bytes()
    if len(data) <= OUTER_EDB_PREFIX_LEN:
        raise ValueError("EDB is too small")
    if data[4:7] != b"edb":
        raise ValueError("EDB outer marker is missing")

    inner = gzip.decompress(data[OUTER_EDB_PREFIX_LEN:])
    if len(inner) < 30:
        raise ValueError("EDB inner payload is truncated")
    version_len = inner[16]
    version_end = 17 + version_len
    if version_end + 17 > len(inner):
        raise ValueError("EDB header is truncated")

    page_count_hint = struct.unpack_from(">H", inner, 0)[0]
    record_count_hint = struct.unpack_from(">H", inner, 2)[0]
    if record_count_hint < expected_min_records:
        raise ValueError(
            f"EDB record hint {record_count_hint} is below expected {expected_min_records}"
        )
    if page_count_hint < 1:
        raise ValueError("EDB page hint must be positive")

    record_offset = version_end + 17
    current = record_offset
    actual_records = 0
    while current + 4 <= len(inner):
        size = struct.unpack_from(">I", inner, current)[0]
        if size < 5:
            break
        max_end = current + size
        if max_end > len(inner) + 1:
            raise ValueError("EDB record extends past payload")
        actual_records += 1
        current = max_end
        if current >= len(inner):
            break
    if actual_records < expected_min_records:
        raise ValueError(
            f"EDB contains {actual_records} records, expected at least {expected_min_records}"
        )
    return {
        "outerSize": len(data),
        "innerSize": len(inner),
        "pageCountHint": page_count_hint,
        "recordCountHint": record_count_hint,
        "recordCountActual": actual_records,
    }


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

    for page in session.get("pages", []):
        for key in ("sourceImageUri", "sourceImagePath"):
            add_path(page.get(key))

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
                risk_flags=[str(flag) for flag in (problem.get("riskFlags") or []) if flag],
                placement_x_ratio=_coerce_placement_x_ratio(problem),
                placement_y_ratio=_coerce_placement_y_ratio(problem),
                placement_scale_ratio=_coerce_placement_scale_ratio(problem),
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

    for page in rewritten.get("pages", []):
        # Front-end loads page images through /api/file; the original
        # sourceImagePath is kept (server-side absolute path) for mutation
        # endpoints to re-open with PIL.
        page["sourceImageUri"] = path_to_api_url(page.get("sourceImagePath") or page.get("sourceImageUri"))
    return rewritten


def _find_problem(session: dict[str, Any], problem_id: str) -> tuple[int, dict[str, Any]]:
    for index, problem in enumerate(session.get("problems", [])):
        if isinstance(problem, dict) and str(problem.get("id")) == problem_id:
            return index, problem
    raise ValueError(f"problem not found: {problem_id}")


def _find_page(session: dict[str, Any], page_id: str) -> dict[str, Any]:
    for page in session.get("pages", []):
        if isinstance(page, dict) and str(page.get("id")) == page_id:
            return page
    raise ValueError(f"page not found: {page_id}")


def _resolve_session_path(value: Any) -> Path | None:
    """Coerce a session-stored value (file URI, /api/file URL, raw path) to a
    real filesystem path. Returns None if the value cannot be resolved."""
    if not value:
        return None
    text = str(value)
    if text.startswith("file://"):
        parsed = urlparse(text)
        return Path(url2pathname(parsed.path))
    if text.startswith("/api/file"):
        parsed = urlparse(text)
        params = parse_qs(parsed.query)
        raw = params.get("path", [None])[0]
        return Path(unquote(raw)) if raw else None
    return Path(text)


def _next_problem_id(session: dict[str, Any], base: str, suffix: str) -> str:
    """Generate a problem id that does not collide with any existing problem.

    Splits and merges produce children whose ids reflect the parent so the
    UI can keep stable mappings across mutations; we append an integer if
    needed to avoid collisions when the same parent is split twice."""
    candidate = f"{base}-{suffix}"
    existing = {str(p.get("id")) for p in session.get("problems", []) if isinstance(p, dict) and p.get("id")}
    if candidate not in existing:
        return candidate
    counter = 2
    while f"{candidate}-{counter}" in existing:
        counter += 1
    return f"{candidate}-{counter}"


def _crop_dir_for_session(session: dict[str, Any]) -> Path:
    out = session.get("output_dir")
    if out:
        target = Path(str(out)) / "problem_crops"
    else:
        target = RUNTIME_DIR / "mutated_crops"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _make_crop_filename(problem_id: str, suffix: str) -> str:
    digest = hashlib.sha1(f"{problem_id}|{suffix}|{time.time_ns()}".encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"mutated_{digest}.png"


def _problem_skeleton_from_parent(parent: dict[str, Any]) -> dict[str, Any]:
    """Carry over the fields that survive a split/merge unchanged — the
    surgical fields (id, bbox, image paths, title) are filled in by caller."""
    skeleton = {
        "title": parent.get("title"),
        "problemNumber": parent.get("problemNumber"),
        "subject": parent.get("subject"),
        "sourceFileName": parent.get("sourceFileName"),
        "sourceImagePath": parent.get("sourceImagePath"),
        "actualHeightPages": parent.get("actualHeightPages"),
        "overflowAllowed": parent.get("overflowAllowed"),
        "readingHeavy": parent.get("readingHeavy"),
        "sourcePageId": parent.get("sourcePageId"),
        "startYPages": parent.get("startYPages"),
        "snappedNextStartYPages": parent.get("snappedNextStartYPages"),
        "overflowAmountPages": parent.get("overflowAmountPages"),
        "overflowViolation": parent.get("overflowViolation"),
        "slotSpanCount": parent.get("slotSpanCount"),
        "recordMode": parent.get("recordMode"),
        "textRecordCount": parent.get("textRecordCount", 0),
        "imageRecordCount": parent.get("imageRecordCount", 1),
        "placementXRatio": _coerce_placement_x_ratio(parent),
        "placementYRatio": _coerce_placement_y_ratio(parent),
        "placementScaleRatio": _coerce_placement_scale_ratio(parent),
        "riskFlags": [],  # mutated entries lose the auto-detected risk
    }
    return skeleton


def _replace_problem(session: dict[str, Any], original_index: int, replacements: list[dict[str, Any]]) -> None:
    """Replace the problem at original_index with one or more replacements,
    keeping the rest of the list intact. Also updates the page's problemIds
    array to match the new ordering."""
    problems = list(session.get("problems") or [])
    original = problems[original_index]
    page_id = str(original.get("sourcePageId") or "")
    new_ids = [str(r["id"]) for r in replacements]

    problems[original_index : original_index + 1] = replacements
    session["problems"] = problems

    for page in session.get("pages", []):
        if not isinstance(page, dict):
            continue
        if str(page.get("id")) != page_id:
            continue
        ids = list(page.get("problemIds") or [])
        if str(original.get("id")) in ids:
            pos = ids.index(str(original.get("id")))
            ids[pos : pos + 1] = new_ids
        else:
            ids.extend(new_ids)
        page["problemIds"] = ids
        break
    session["detected_problem_count"] = len(problems)


def _remove_problems(session: dict[str, Any], problem_ids: set[str]) -> list[dict[str, Any]]:
    """Drop matching problems from session.problems and from each page's
    problemIds list. Returns the removed entries (in original order)."""
    removed: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for problem in session.get("problems") or []:
        if isinstance(problem, dict) and str(problem.get("id")) in problem_ids:
            removed.append(problem)
        else:
            kept.append(problem)
    session["problems"] = kept
    for page in session.get("pages", []):
        if not isinstance(page, dict):
            continue
        page["problemIds"] = [pid for pid in (page.get("problemIds") or []) if pid not in problem_ids]
    session["detected_problem_count"] = len(kept)
    return removed


def _mutate_split(session: dict[str, Any], problem_id: str, split_y_ratio: float) -> dict[str, Any]:
    if not (0.05 < split_y_ratio < 0.95):
        raise ValueError("splitYRatio must be between 0.05 and 0.95")
    index, problem = _find_problem(session, problem_id)
    page_id = str(problem.get("sourcePageId") or "")
    page = _find_page(session, page_id)
    page_image_path = _resolve_session_path(page.get("sourceImagePath") or page.get("sourceImageUri"))
    if page_image_path is None or not page_image_path.exists():
        raise FileNotFoundError(f"page image missing for {page_id}: {page_image_path}")

    bbox = problem.get("bbox") or {}
    left = float(bbox.get("left", 0.0))
    top = float(bbox.get("top", 0.0))
    width = float(bbox.get("width", 0.0))
    height = float(bbox.get("height", 0.0))
    if width <= 0 or height <= 0:
        raise ValueError("problem bbox is empty — cannot split")
    cut = height * split_y_ratio
    upper = Box(left=left, top=top, width=width, height=cut)
    lower = Box(left=left, top=top + cut, width=width, height=height - cut)

    crop_dir = _crop_dir_for_session(session)
    from PIL import Image  # local import: PIL is already in build_problem_board_edb's deps

    page_image = Image.open(page_image_path).convert("RGB")
    upper_id = _next_problem_id(session, str(problem.get("id")), "u")
    upper_crop_path = crop_dir / _make_crop_filename(upper_id, "u")
    recrop_problem(page_image, upper, upper_crop_path)
    lower_id = _next_problem_id({**session, "problems": session.get("problems", []) + [{"id": upper_id}]}, str(problem.get("id")), "l")
    lower_crop_path = crop_dir / _make_crop_filename(lower_id, "l")
    recrop_problem(page_image, lower, lower_crop_path)

    parent_title = str(problem.get("title") or problem_id)

    def make_entry(new_id: str, new_bbox: Box, crop_path: Path, suffix: str) -> dict[str, Any]:
        entry = _problem_skeleton_from_parent(problem)
        entry["id"] = new_id
        entry["title"] = f"{parent_title} ({suffix})"
        entry["imagePath"] = crop_path.resolve().as_uri()
        # cutout regeneration is reserved for the AI workflow — for now the
        # board render path mirrors the rectangular crop. EDB build composites
        # onto the dark theme using the same source.
        entry["boardRenderPath"] = crop_path.resolve().as_uri()
        entry["bbox"] = {
            "left": new_bbox.left,
            "top": new_bbox.top,
            "width": new_bbox.width,
            "height": new_bbox.height,
        }
        return entry

    upper_entry = make_entry(upper_id, upper, upper_crop_path, "위")
    lower_entry = make_entry(lower_id, lower, lower_crop_path, "아래")
    _replace_problem(session, index, [upper_entry, lower_entry])
    return session


def _mutate_merge(session: dict[str, Any], problem_ids: list[str]) -> dict[str, Any]:
    if len(problem_ids) < 2:
        raise ValueError("merge requires at least 2 problems")
    targets: list[tuple[int, dict[str, Any]]] = []
    for pid in problem_ids:
        index, problem = _find_problem(session, pid)
        targets.append((index, problem))
    page_ids = {str(p.get("sourcePageId")) for _, p in targets}
    if len(page_ids) != 1:
        raise ValueError("merge requires all problems on the same source page")
    page_id = next(iter(page_ids))
    page = _find_page(session, page_id)
    page_image_path = _resolve_session_path(page.get("sourceImagePath") or page.get("sourceImageUri"))
    if page_image_path is None or not page_image_path.exists():
        raise FileNotFoundError(f"page image missing for {page_id}: {page_image_path}")

    lefts, tops, rights, bottoms = [], [], [], []
    for _, problem in targets:
        bbox = problem.get("bbox") or {}
        left = float(bbox.get("left", 0.0))
        top = float(bbox.get("top", 0.0))
        width = float(bbox.get("width", 0.0))
        height = float(bbox.get("height", 0.0))
        if width <= 0 or height <= 0:
            raise ValueError(f"problem {problem.get('id')} has an empty bbox — cannot merge")
        lefts.append(left)
        tops.append(top)
        rights.append(left + width)
        bottoms.append(top + height)
    merged = Box(left=min(lefts), top=min(tops), width=max(rights) - min(lefts), height=max(bottoms) - min(tops))

    first_index = min(idx for idx, _ in targets)
    primary = targets[0][1]  # take the first listed problem as the metadata source
    crop_dir = _crop_dir_for_session(session)
    new_id = _next_problem_id(session, str(primary.get("id")), "m")
    new_crop_path = crop_dir / _make_crop_filename(new_id, "m")

    from PIL import Image
    page_image = Image.open(page_image_path).convert("RGB")
    recrop_problem(page_image, merged, new_crop_path)

    merged_entry = _problem_skeleton_from_parent(primary)
    merged_entry["id"] = new_id
    parent_title = str(primary.get("title") or new_id)
    merged_entry["title"] = f"{parent_title} 외 {len(targets) - 1}건"
    merged_entry["imagePath"] = new_crop_path.resolve().as_uri()
    merged_entry["boardRenderPath"] = new_crop_path.resolve().as_uri()
    merged_entry["bbox"] = {
        "left": merged.left,
        "top": merged.top,
        "width": merged.width,
        "height": merged.height,
    }
    # remove all originals; the page's problemIds will be cleaned up too.
    _remove_problems(session, {str(pid) for pid in problem_ids})
    # insert the merged entry at the position of the first removed problem
    # (relative to the post-removal list — adjust because earlier entries
    # may have been removed too).
    problems = session.get("problems") or []
    insert_at = min(first_index, len(problems))
    problems.insert(insert_at, merged_entry)
    session["problems"] = problems
    session["detected_problem_count"] = len(problems)
    # also slot the new id into the page's problemIds
    for p in session.get("pages", []):
        if not isinstance(p, dict):
            continue
        if str(p.get("id")) != page_id:
            continue
        ids = list(p.get("problemIds") or [])
        # find a reasonable insertion point — right after the first id that
        # already appears in the new problems list, or at the end.
        existing_ids_in_problems = [str(prob.get("id")) for prob in problems if isinstance(prob, dict)]
        insertion_index = len(ids)
        for idx, pid_in_page in enumerate(ids):
            if pid_in_page in existing_ids_in_problems:
                position_in_problems = existing_ids_in_problems.index(pid_in_page)
                if position_in_problems >= insert_at:
                    insertion_index = idx
                    break
        ids.insert(insertion_index, new_id)
        p["problemIds"] = ids
        break
    return session


def _mutate_exclude(session: dict[str, Any], problem_id: str) -> dict[str, Any]:
    if not problem_id:
        raise ValueError("problemId is required")
    _find_problem(session, problem_id)  # raises if missing
    _remove_problems(session, {problem_id})
    return session


def _problem_review_status(problem: dict[str, Any]) -> str:
    explicit = str(problem.get("reviewStatus") or problem.get("review_status") or "").strip().lower()
    if explicit in {"normal", "check_needed", "failed"}:
        return explicit
    bbox = problem.get("bbox") or {}
    try:
        has_bbox = (
            isinstance(bbox, dict)
            and float(bbox.get("width") or 0) > 0
            and float(bbox.get("height") or 0) > 0
        )
    except (TypeError, ValueError):
        has_bbox = False
    if not has_bbox or problem.get("parseFailed") or problem.get("parse_failed"):
        return "failed"
    if problem.get("riskFlags") or problem.get("risk_flags"):
        return "check_needed"
    return "normal"


def _page_needs_ai_retry(session: dict[str, Any], page: dict[str, Any]) -> bool:
    page_flags = page.get("riskFlags") or page.get("risk_flags") or []
    if isinstance(page_flags, list) and page_flags:
        return True
    problem_ids = [str(pid) for pid in (page.get("problemIds") or []) if pid]
    if not problem_ids:
        return True
    by_id = {
        str(problem.get("id")): problem
        for problem in session.get("problems", [])
        if isinstance(problem, dict) and problem.get("id")
    }
    return any(_problem_review_status(by_id.get(pid, {})) != "normal" for pid in problem_ids)


def _retry_target_page_ids(session: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    raw_page_ids = payload.get("pageIds") or payload.get("page_ids") or []
    if payload.get("pageId") or payload.get("page_id"):
        raw_page_ids = [payload.get("pageId") or payload.get("page_id")]

    ids: list[str] = [str(pid) for pid in raw_page_ids if pid]
    raw_problem_ids = payload.get("problemIds") or payload.get("problem_ids") or []
    if payload.get("problemId") or payload.get("problem_id"):
        raw_problem_ids = [payload.get("problemId") or payload.get("problem_id")]
    for problem_id in raw_problem_ids:
        _, problem = _find_problem(session, str(problem_id))
        page_id = str(problem.get("sourcePageId") or "")
        if page_id:
            ids.append(page_id)

    if not ids:
        ids.extend(
            str(page.get("id"))
            for page in session.get("pages", [])
            if isinstance(page, dict) and page.get("id") and _page_needs_ai_retry(session, page)
        )

    return list(dict.fromkeys(ids))


def _replace_page_problems(session: dict[str, Any], page_id: str, replacements: list[dict[str, Any]]) -> None:
    page = _find_page(session, page_id)
    old_ids = {str(pid) for pid in (page.get("problemIds") or []) if pid}
    if not old_ids:
        old_ids = {
            str(problem.get("id"))
            for problem in session.get("problems", [])
            if isinstance(problem, dict) and str(problem.get("sourcePageId") or "") == page_id
        }

    inserted = False
    next_problems: list[dict[str, Any]] = []
    for problem in session.get("problems", []) or []:
        if isinstance(problem, dict) and str(problem.get("id")) in old_ids:
            if not inserted:
                next_problems.extend(replacements)
                inserted = True
            continue
        next_problems.append(problem)

    if not inserted:
        next_problems.extend(replacements)

    session["problems"] = next_problems
    page["problemIds"] = [str(problem.get("id")) for problem in replacements if problem.get("id")]
    session["detected_problem_count"] = len(next_problems)


def _normalized_retry_problem(
    session: dict[str, Any],
    source_page: dict[str, Any],
    retry_problem: dict[str, Any],
    *,
    stamp: str,
    index: int,
    replacements_so_far: list[dict[str, Any]],
) -> dict[str, Any]:
    page_id = str(source_page.get("id") or "")
    source_path = _resolve_session_path(source_page.get("sourceImagePath") or source_page.get("sourceImageUri"))
    candidate_session = {**session, "problems": list(session.get("problems") or []) + replacements_so_far}
    new_id = _next_problem_id(candidate_session, f"{page_id}-ai", f"{stamp}-{index}")
    problem = dict(retry_problem)
    problem["id"] = new_id
    problem["sourcePageId"] = page_id
    problem["sourceFileName"] = source_path.name if source_path else str(source_page.get("id") or "AI retry")
    if source_path:
        problem["sourceImagePath"] = source_path.resolve().as_uri()
    risk_flags = [str(flag) for flag in (problem.get("riskFlags") or problem.get("risk_flags") or []) if flag]
    problem["riskFlags"] = list(dict.fromkeys(risk_flags))
    problem["reviewStatus"] = _problem_review_status(problem)
    problem["aiRetry"] = {
        "status": "applied",
        "sourcePageId": page_id,
        "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return problem


def _mutate_retry_ai(session: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise ValueError("Gemini API 키가 필요합니다. 칠판 설정에서 키를 저장한 뒤 다시 시도해 주세요.")

    page_ids = _retry_target_page_ids(session, payload)
    if not page_ids:
        raise ValueError("AI 재인식할 페이지가 없습니다")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    output_root = Path(session.get("output_dir") or RUNTIME_DIR / "ai_retries").resolve()
    ai_config = session.get("ai_fallback") if isinstance(session.get("ai_fallback"), dict) else {}
    summaries: list[dict[str, Any]] = []

    for page_id in page_ids:
        page = _find_page(session, page_id)
        source_path = _resolve_session_path(page.get("sourceImagePath") or page.get("sourceImageUri"))
        if source_path is None or not source_path.exists():
            raise FileNotFoundError(f"source page image missing for retry: {page_id}")

        retry_dir = output_root / "ai_retries" / sanitize_output_dir_name(f"{page_id}_{stamp}")
        try:
            result = run_problem_export(
                source_path,
                output_dir=retry_dir,
                subject_name=str(payload.get("subject") or "unknown"),
                ocr=str(payload.get("ocr") or "auto"),
                pdf_dpi=int(payload.get("pdfDpi") or payload.get("pdf_dpi") or 200),
                detect_perspective=False,
                skip_deskew=True,
                skip_crop=True,
                max_dimension=None,
                export_edb=False,
                edb_name="ai_retry.edb",
                sync_ui=False,
                record_mode=str(session.get("record_mode") or "image-only"),
                text_confidence_threshold=float(session.get("text_confidence_threshold") or 0.78),
                input_intent="multi-problem",
                ai_fallback_enabled=True,
                ai_fallback="force",
                ai_fallback_provider=str(ai_config.get("provider") or "gemini"),
                ai_fallback_model=str(ai_config.get("model") or ""),
                ai_fallback_max_tokens=ai_config.get("max_tokens"),
                ai_fallback_temperature=ai_config.get("temperature"),
                ai_fallback_threshold=float(ai_config.get("threshold") or 0.72),
                ai_fallback_max_regions=int(ai_config.get("max_regions") or 30),
                ai_fallback_timeout_ms=int(ai_config.get("timeout_ms") or 18000),
                ai_fallback_save_debug=bool(ai_config.get("save_debug")),
            )
        except Exception as exc:  # noqa: BLE001 - show the actionable pipeline message to the UI
            page["reviewStatus"] = "failed"
            flags = list(page.get("riskFlags") or [])
            flags.append("ai_retry_failed")
            page["riskFlags"] = list(dict.fromkeys(str(flag) for flag in flags if flag))
            page["aiRetry"] = {
                "status": "failed",
                "error": str(exc),
                "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            summaries.append({"pageId": page_id, "status": "failed", "error": str(exc)})
            continue

        retry_session = result.get("ui_session") or {}
        retry_problems = [p for p in retry_session.get("problems", []) if isinstance(p, dict)]
        retry_pages = [p for p in retry_session.get("pages", []) if isinstance(p, dict)]
        retry_page = retry_pages[0] if retry_pages else {}

        if not retry_problems:
            page["reviewStatus"] = "failed"
            flags = list(page.get("riskFlags") or [])
            flags.append("ai_retry_empty")
            page["riskFlags"] = list(dict.fromkeys(str(flag) for flag in flags if flag))
            page["aiRetry"] = {
                "status": "empty",
                "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            summaries.append({"pageId": page_id, "status": "empty", "replacedProblemCount": 0})
            continue

        previous_problem_count = len(page.get("problemIds") or [])
        replacements: list[dict[str, Any]] = []
        for index, retry_problem in enumerate(retry_problems, start=1):
            replacements.append(
                _normalized_retry_problem(
                    session,
                    page,
                    retry_problem,
                    stamp=stamp,
                    index=index,
                    replacements_so_far=replacements,
                )
            )

        _replace_page_problems(session, page_id, replacements)
        page["riskFlags"] = [str(flag) for flag in (retry_page.get("riskFlags") or []) if flag]
        page["reviewStatus"] = "check_needed" if page["riskFlags"] else "normal"
        page["aiRetry"] = {
            "status": "applied",
            "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "previousProblemCount": previous_problem_count,
            "replacedProblemCount": len(replacements),
            "aiSummary": retry_session.get("ai_summary"),
        }
        summaries.append({"pageId": page_id, "status": "applied", "replacedProblemCount": len(replacements)})

    session["ai_retry_summary"] = summaries
    session["detected_problem_count"] = len(session.get("problems") or [])
    return session


def _denormalize_session_paths(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Rewrite any /api/file?path=... values in a session snapshot back to
    file:// URIs so the server's "latest_session" stays canonical regardless
    of whether the snapshot came from JS (where everything is /api/file URLs)
    or from a fresh build (where everything is file://)."""
    cloned = json.loads(json.dumps(snapshot))

    def fix(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if value.startswith("/api/file"):
            parsed = urlparse(value)
            params = parse_qs(parsed.query)
            raw = params.get("path", [None])[0]
            if raw:
                return Path(unquote(raw)).resolve().as_uri()
        return value

    for problem in cloned.get("problems", []) or []:
        if not isinstance(problem, dict):
            continue
        for key in ("imagePath", "sourceImagePath", "boardRenderPath"):
            problem[key] = fix(problem.get(key))
    for page in cloned.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        page["sourceImageUri"] = fix(page.get("sourceImageUri"))
    cloned["edb_file_uri"] = fix(cloned.get("edb_file_uri"))
    cloned["rendered_page_file_uris"] = [fix(v) for v in cloned.get("rendered_page_file_uris", [])]
    return cloned


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
        if parsed.path == "/api/system/open-folder":
            self._handle_open_folder()
            return
        if parsed.path == "/api/session/mutate":
            self._handle_session_mutate()
            return
        if parsed.path == "/api/session/retry-ai":
            self._handle_session_retry_ai()
            return
        if parsed.path == "/api/session/restore":
            self._handle_session_restore()
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
        placement_payload = payload.get("placements")
        if not isinstance(placement_payload, dict):
            placement_payload = {}

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

        sequence_with_placements: list[dict[str, Any]] = []
        for problem in sequence:
            problem_copy = dict(problem)
            problem_id = str(problem_copy.get("id") or "")
            x_ratio = _coerce_placement_x_ratio(placement_payload.get(problem_id))
            if x_ratio is None:
                x_ratio = _coerce_placement_x_ratio(problem_copy)
            if x_ratio is not None:
                problem_copy["placementXRatio"] = x_ratio
            y_ratio = _coerce_placement_y_ratio(placement_payload.get(problem_id))
            if y_ratio is None:
                y_ratio = _coerce_placement_y_ratio(problem_copy)
            if y_ratio is not None:
                problem_copy["placementYRatio"] = y_ratio
            scale_ratio = _coerce_placement_scale_ratio(placement_payload.get(problem_id))
            if scale_ratio is None:
                scale_ratio = _coerce_placement_scale_ratio(problem_copy)
            if scale_ratio is not None:
                problem_copy["placementScaleRatio"] = scale_ratio
            sequence_with_placements.append(problem_copy)
        sequence = sequence_with_placements

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
        # Resize the logical canvas to match the actual problem count after the
        # user may have excluded items in the review UI. Mirrors the formula in
        # run_problem_export so mvp_board.edb and the published EDB agree.
        template.board_page_count = max(50, len(entries) * 2)
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
            edb_validation = validate_edb_file(edb_path, expected_min_records=len(records))
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
            template=template,
        )
        # carry over user-facing labels so the rename doesn't get lost
        if session.get("session_name"):
            new_session["session_name"] = session["session_name"]
        # publish only re-renders records; page-level review metadata
        # (sourceImageUri, dimensions, riskFlags) is still meaningful for the
        # caller, so preserve it across the publish hop.
        if session.get("pages"):
            preserved_pages: list[dict[str, Any]] = []
            problem_ids_remaining = {str(problem.get("id")) for problem in new_session.get("problems", []) if problem.get("id")}
            for page in session["pages"]:
                page_copy = dict(page)
                page_copy["problemIds"] = [pid for pid in page.get("problemIds", []) if pid in problem_ids_remaining]
                preserved_pages.append(page_copy)
            new_session["pages"] = preserved_pages
        # propagate per-problem bbox/riskFlags from the prior session — they
        # are derived from segmentation, which publish does not re-run.
        prior_problems_by_id = {
            str(problem.get("id")): problem
            for problem in session.get("problems", [])
            if isinstance(problem, dict) and problem.get("id")
        }
        for problem in new_session.get("problems", []):
            prior = prior_problems_by_id.get(str(problem.get("id")))
            if not prior:
                continue
            if "bbox" not in problem or not problem["bbox"]:
                problem["bbox"] = prior.get("bbox") or {}
            problem["riskFlags"] = list(prior.get("riskFlags") or [])
            if "placementXRatio" not in problem:
                x_ratio = _coerce_placement_x_ratio(prior)
                if x_ratio is not None:
                    problem["placementXRatio"] = x_ratio
            if "placementYRatio" not in problem:
                y_ratio = _coerce_placement_y_ratio(prior)
                if y_ratio is not None:
                    problem["placementYRatio"] = y_ratio
            if "placementScaleRatio" not in problem:
                scale_ratio = _coerce_placement_scale_ratio(prior)
                if scale_ratio is not None:
                    problem["placementScaleRatio"] = scale_ratio
        self.app_server.remember_session(new_session)
        self._send_json({
            "ok": True,
            "session": rewrite_session_for_http(new_session),
            "edbValidation": edb_validation,
            "edb_validation": edb_validation,
        })

    # ── /api/session/mutate ──────────────────────────────────────────────
    # Body: { "action": "split" | "merge" | "exclude", ...args }
    # Returns the updated session (rewritten for HTTP).
    def _handle_session_mutate(self) -> None:
        session = self.app_server.latest_session or load_latest_session()
        if session is None:
            self._send_json({"ok": False, "error": "no session available"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        action = str(payload.get("action") or "").strip().lower()
        try:
            if action == "split":
                problem_id = str(payload.get("problemId") or payload.get("problem_id") or "")
                raw_ratio = payload.get("splitYRatio")
                if raw_ratio is None:
                    raw_ratio = payload.get("split_y_ratio")
                if raw_ratio is None:
                    raw_ratio = 0.5
                split_ratio = float(raw_ratio)
                new_session = _mutate_split(session, problem_id, split_ratio)
            elif action == "merge":
                ids_raw = payload.get("problemIds") or payload.get("problem_ids") or []
                problem_ids = [str(pid) for pid in ids_raw if pid]
                new_session = _mutate_merge(session, problem_ids)
            elif action == "exclude":
                problem_id = str(payload.get("problemId") or payload.get("problem_id") or "")
                new_session = _mutate_exclude(session, problem_id)
            elif action in {"retry-ai", "retry_ai"}:
                new_session = _mutate_retry_ai(session, payload)
            else:
                self._send_json(
                    {"ok": False, "error": f"unknown action: {action!r} (expected split|merge|exclude|retry-ai)"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.CONFLICT)
            return

        self.app_server.remember_session(new_session)
        self._send_json({"ok": True, "session": rewrite_session_for_http(new_session)})

    def _handle_session_retry_ai(self) -> None:
        session = self.app_server.latest_session or load_latest_session()
        if session is None:
            self._send_json({"ok": False, "error": "no session available"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body()
            new_session = _mutate_retry_ai(session, payload)
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.CONFLICT)
            return

        self.app_server.remember_session(new_session)
        self._send_json({
            "ok": True,
            "session": rewrite_session_for_http(new_session),
            "retry": new_session.get("ai_retry_summary") or [],
        })

    # ── /api/session/restore ────────────────────────────────────────────
    # Body: { "session": { ... full session JSON ... } }
    # Replaces the server's "latest" session with the provided snapshot. Used
    # by the front-end Undo stack so a single round-trip is enough to revert.
    def _handle_session_restore(self) -> None:
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        snapshot = payload.get("session")
        if not isinstance(snapshot, dict) or "problems" not in snapshot:
            self._send_json(
                {"ok": False, "error": "session payload must be a dict containing 'problems'"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        # Strip the HTTP-friendly URLs that may already be in the snapshot —
        # the server stores file-system paths and re-derives /api/file URLs on
        # the way out. Snapshots that round-trip through rewrite_session_for_http
        # would otherwise drift over time.
        restored = _denormalize_session_paths(snapshot)
        self.app_server.remember_session(restored)
        self._send_json({"ok": True, "session": rewrite_session_for_http(restored)})

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

    def _handle_open_folder(self) -> None:
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        raw_path = payload.get("path") or payload.get("folder") or ""
        if not raw_path:
            self._send_json({"ok": False, "error": "path is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            target = Path(str(raw_path)).resolve()
        except (OSError, ValueError) as exc:
            self._send_json({"ok": False, "error": f"invalid path: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        # confine the open to paths under BASE_DIR / RUNTIME_DIR so the API
        # can't be abused to reveal arbitrary locations on the user's machine.
        roots = [BASE_DIR.resolve(), RUNTIME_DIR.resolve()]
        if not any(str(target) == str(root) or str(target).startswith(str(root) + os.sep) for root in roots):
            self._send_json({"ok": False, "error": "path outside allowed roots"}, status=HTTPStatus.FORBIDDEN)
            return
        if target.is_file():
            target = target.parent
        if not target.exists() or not target.is_dir():
            self._send_json({"ok": False, "error": f"folder not found: {target}"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            self._send_json({"ok": False, "error": f"failed to open: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"ok": True, "path": str(target)})

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
            input_intent = _extract_input_intent(payload)
            input_notes = _extract_input_notes(payload)
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
                    input_intent=input_intent,
                    input_notes=input_notes,
                    **common_kwargs,
                )
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        session = result["ui_session"]
        session.setdefault("input_intent", input_intent)
        if input_notes:
            session["input_notes"] = input_notes
        edb_validation = None
        edb_path = result.get("edb_path")
        if edb_path:
            try:
                expected_records = len((result.get("summary") or {}).get("placements") or [])
                edb_validation = validate_edb_file(edb_path, expected_min_records=max(1, expected_records))
            except Exception as exc:
                self._send_json({"ok": False, "error": f"EDB validation failed: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
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
                "edb_validation": edb_validation,
                "edbValidation": edb_validation,
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
