#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import concurrent.futures
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
from datetime import datetime
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import url2pathname

import preprocess
from build_mvp_export import run_export
from build_problem_board_edb import (
    DEFAULT_BOARD_THEME,
    ONE_PROBLEM_SLOT_HEIGHT_PAGES,
    ProblemEntry,
    _classin_board_placement_overlap_issues,
    _classin_passage_group_source_reuse_issues,
    _classin_source_bbox_overlap_issues,
    _normalize_processing_step,
    _session_duplicate_problem_number_groups,
    _session_problem_count_payload,
    build_records,
    build_ui_session,
    recrop_problem,
    resolve_subject,
    run_problem_export,
    write_classin_handoff_manifest,
)
from build_structured_page_json import resolve_recognition_worker_count
from edb_builder import (
    CROP_FORMAT_V1,
    CROP_FORMAT_V2,
    build_edb,
    version_string_for_crop_format,
    write_edb,
)
from layout_template_schema import LayoutTemplate
from image_reconstruction_backend import (
    DEFAULT_IMAGE_RECONSTRUCTION_PROVIDER,
    DEFAULT_RECONSTRUCTION_PROMPT,
    default_image_model,
    normalize_image_model,
    normalize_image_provider,
    reconstruct_problem_image,
)
from structured_schema import Box, Subject
from user_settings import (
    apply_to_env as apply_user_settings_to_env,
    load_user_settings,
    summarize_for_response as summarize_user_settings,
    update_api_keys,
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
SESSION_HISTORY_JSON = RUNTIME_DIR / "session_history.json"
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


APP_DEFAULT_CROP_FORMAT = CROP_FORMAT_V1


def _normalize_crop_format(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {CROP_FORMAT_V1, CROP_FORMAT_V2}:
        return normalized
    return APP_DEFAULT_CROP_FORMAT


def _extract_crop_format(payload: dict[str, Any]) -> str:
    return _normalize_crop_format(payload.get("cropFormat") or payload.get("crop_format"))


def _command_info(command: list[str]) -> dict[str, Any]:
    executable = str(command[0]) if command else ""
    name = Path(executable).name
    command_args = [str(part) for part in command[1:]]
    if any("unhwp.extract_text" in part for part in command_args):
        name = "unhwp"
    if any("hwp_hwpx_parser" in part for part in command_args):
        name = "hwp-hwpx-parser"
    if any("hwpilot" in part and part.endswith("main.js") for part in command_args):
        name = "hwpilot"
    if any("render_hwp_with_rhwp_core.mjs" in part for part in command_args):
        name = "rhwp-core"
    return {
        "name": name,
        "path": executable,
        "args": command_args,
    }


def describe_runtime_diagnostics() -> dict[str, Any]:
    pdf_converters = [_command_info(command) for command in preprocess._iter_hwp_pdf_converter_commands()]
    hwp_to_hwpx_converters = [_command_info(command) for command in preprocess._iter_hwp_hwpx_converter_commands()]
    html_converters = [_command_info(command) for command in preprocess._iter_pyhwp_html_converter_commands()]
    text_extractors = [_command_info(command) for command in preprocess._iter_hwp_text_converter_commands()]
    chrome_pdf_converters = [_command_info(command) for command in preprocess._iter_chrome_pdf_commands()]
    hwp_renderers = [_command_info(command) for command in preprocess._iter_rhwp_core_renderer_commands()]

    pdf_ready = bool(pdf_converters)
    html_pdf_ready = bool(html_converters and chrome_pdf_converters)
    hwp_renderer_ready = bool(hwp_renderers)
    hwp_ready = bool(pdf_ready or html_pdf_ready or hwp_renderer_ready)
    hwpx_ready = bool(pdf_ready or hwp_renderer_ready)
    warnings: list[str] = []
    recommended_actions: list[str] = []

    if not pdf_ready and not hwp_renderer_ready:
        warnings.append("LibreOffice, rhwp, hwp5pdf, airun-hwp, or rhwp-core renderer was not found.")
        recommended_actions.append("LibreOffice/rhwp/HWP PDF 변환기, airun-hwp, 또는 rhwp-core 렌더러를 설치하거나, HWP/HWPX를 PDF로 내보낸 뒤 업로드해 주세요.")
    if html_converters and not chrome_pdf_converters:
        warnings.append("pyhwp HTML fallback is available, but Chrome PDF printing was not found.")
        recommended_actions.append("Chrome을 설치하거나 EDB_CHROME 환경 변수로 Chrome 실행 파일 경로를 지정해 주세요.")
    if not text_extractors:
        warnings.append("hwp5txt/unhwp/rhwp/hwpilot/kordoc text extractor was not found; HWP 문항 수 사전 점검이 약해집니다.")
        recommended_actions.append("pyhwp/hwp5txt, unhwp, rhwp, HWPilot, 또는 kordoc를 설치하면 HWP 내부 텍스트 기반 문항 수 QA가 더 정확해집니다.")
    if not hwp_to_hwpx_converters:
        recommended_actions.append("선택 사항: HWPilot을 설치하면 HWP→HWPX 정규화 경로를 추가로 사용할 수 있습니다.")

    if hwp_ready and hwpx_ready:
        status = "ready"
        label = "준비됨"
    elif hwp_ready or hwpx_ready:
        status = "partial"
        label = "부분 준비"
    else:
        status = "blocked"
        label = "확인 필요"

    tool_counts = {
        "pdfConverters": len(pdf_converters),
        "hwpToHwpxConverters": len(hwp_to_hwpx_converters),
        "htmlConverters": len(html_converters),
        "textExtractors": len(text_extractors),
        "chromePdfConverters": len(chrome_pdf_converters),
        "hwpRenderers": len(hwp_renderers),
    }
    summary_parts = [
        f"PDF {tool_counts['pdfConverters']}",
        f"텍스트 {tool_counts['textExtractors']}",
        f"브리지 {tool_counts['hwpToHwpxConverters']}",
    ]
    if hwp_renderers:
        summary_parts.append(f"렌더 {tool_counts['hwpRenderers']}")
    if html_pdf_ready:
        summary_parts.append("HTML fallback")
    if warnings:
        summary_parts.append(f"주의 {len(warnings)}")

    return {
        "ok": True,
        "hangul": {
            "status": status,
            "label": label,
            "summary": " · ".join(summary_parts),
            "toolCounts": tool_counts,
            "pdfReady": pdf_ready,
            "hwpReady": hwp_ready,
            "hwpxReady": hwpx_ready,
            "hwpRendererReady": hwp_renderer_ready,
            "htmlPdfFallbackReady": html_pdf_ready,
            "pdfConverters": pdf_converters,
            "hwpToHwpxConverters": hwp_to_hwpx_converters,
            "htmlConverters": html_converters,
            "textExtractors": text_extractors,
            "chromePdfConverters": chrome_pdf_converters,
            "hwpRenderers": hwp_renderers,
            "warnings": warnings,
            "recommendedActions": recommended_actions,
        },
    }


def _export_error_payload(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    payload: dict[str, Any] = {
        "ok": False,
        "error": message,
        "errorKind": "export_failed",
    }
    if (
        "HWP/HWPX" in message
        or "valid HWP" in message
        or "valid HWPX" in message
        or "한컴오피스" in message
    ):
        payload["errorKind"] = "hangul_conversion_failed"
        payload["recoverySteps"] = [
            "한컴오피스에서 원본 HWP/HWPX를 PDF로 내보낸 뒤 PDF를 다시 업로드해 주세요.",
            "또는 HWP/HWPX를 PDF로 변환할 수 있는 로컬 변환기를 설치한 뒤 다시 실행해 주세요.",
            "암호, 배포용, DRM, 복사 방지 문서라면 보호를 해제하거나 권한 있는 PDF 내보내기를 사용해 주세요.",
        ]
    return payload


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


def _classin_handoff_readiness(path: Path | None) -> tuple[str, bool | None]:
    if path is None or not path.is_file():
        return "", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", None
    if not isinstance(payload, dict):
        return "", None
    status = str(payload.get("status") or payload.get("classinHandoffStatus") or "").strip()
    ready_raw = payload.get("readyForClassIn", payload.get("ready_for_classin"))
    ready = None if ready_raw is None else bool(ready_raw)
    return status, ready


def _passage_group_problem_count(group: dict[str, Any]) -> int:
    for key in ("problemNumbers", "problem_numbers", "childProblemNumbers", "child_problem_numbers"):
        value = group.get(key)
        if isinstance(value, list) and value:
            return len({str(item).strip() for item in value if str(item).strip()})
    raw_count = int(group.get("problemCount") or group.get("problem_count") or 0)
    fragment_count = int(group.get("fragmentProblemCount") or group.get("fragment_problem_count") or 0)
    return max(0, raw_count - fragment_count)


def _path_exists(value: Any, *, directory: bool = False) -> bool:
    path = decode_file_reference(str(value)) if value else None
    if path is None:
        return False
    return path.is_dir() if directory else path.is_file()


def _publish_artifact_state(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return summary
    annotated = dict(summary)
    edb_path = annotated.get("edbPath") or annotated.get("edb_path")
    output_dir = annotated.get("outputDir") or annotated.get("output_dir")
    classin_handoff_path = annotated.get("classinHandoffPath") or annotated.get("classin_handoff_path")
    classin_handoff_markdown_path = (
        annotated.get("classinHandoffMarkdownPath")
        or annotated.get("classin_handoff_markdown_path")
    )
    edb_exists = _path_exists(edb_path)
    output_exists = _path_exists(output_dir, directory=True)
    annotated["edbFileExists"] = edb_exists
    annotated["outputDirExists"] = output_exists
    annotated["edb_file_exists"] = edb_exists
    annotated["output_dir_exists"] = output_exists
    annotated["classinHandoffUri"] = (
        annotated.get("classinHandoffUri")
        or annotated.get("classin_handoff_uri")
        or path_to_api_url(classin_handoff_path)
    )
    annotated["classinHandoffMarkdownUri"] = (
        annotated.get("classinHandoffMarkdownUri")
        or annotated.get("classin_handoff_markdown_uri")
        or path_to_api_url(classin_handoff_markdown_path)
    )
    handoff_status, ready_for_classin = _classin_handoff_readiness(
        decode_file_reference(str(classin_handoff_path)) if classin_handoff_path else None
    )
    annotated["classinHandoffStatus"] = (
        annotated.get("classinHandoffStatus")
        or annotated.get("classin_handoff_status")
        or handoff_status
    )
    if "readyForClassIn" in annotated:
        ready_value = bool(annotated["readyForClassIn"])
    elif "ready_for_classin" in annotated:
        ready_value = bool(annotated["ready_for_classin"])
    elif ready_for_classin is not None:
        ready_value = ready_for_classin
    else:
        ready_value = annotated["classinHandoffStatus"] == "ready_for_classin_review"
    annotated["readyForClassIn"] = ready_value
    annotated["classin_handoff_uri"] = annotated["classinHandoffUri"]
    annotated["classin_handoff_markdown_uri"] = annotated["classinHandoffMarkdownUri"]
    annotated["classin_handoff_status"] = annotated["classinHandoffStatus"]
    annotated["ready_for_classin"] = annotated["readyForClassIn"]
    return annotated


def _duplicate_problem_number_group_issue(group: dict[str, Any]) -> dict[str, Any]:
    problem_ids = [str(value) for value in (group.get("problemIds") or []) if str(value or "")]
    source_page_ids = [str(value) for value in (group.get("sourcePageIds") or []) if str(value or "")]
    number_label = str(group.get("numberLabel") or "")
    message = str(group.get("message") or "").strip()
    if not message:
        message = f"문항 번호 {number_label}가 중복되었습니다. EDB publish 전에 분리/병합 상태를 확인해 주세요."
    return {
        "type": "duplicate_problem_number",
        "severity": "warning",
        "message": message,
        "problemId": problem_ids[0] if problem_ids else "",
        "problemTitle": number_label,
        "numberLabel": number_label,
        "problemNumbers": list(group.get("problemNumbers") or []),
        "problemIds": problem_ids,
        "sourcePageIds": source_page_ids,
        "classification": str(group.get("classification") or "duplicate"),
        "blocking": True,
    }


def _passage_review_queue_issue(item: dict[str, Any]) -> dict[str, Any]:
    problem_ids = _passage_review_item_problem_ids(item)
    source_page_ids: list[str] = []
    for key in ("sourcePageIds", "source_page_ids"):
        values = item.get(key)
        if isinstance(values, list):
            source_page_ids.extend(str(value or "").strip() for value in values)
    source_page_ids = [value for value in source_page_ids if value]
    number_label = str(item.get("numberLabel") or item.get("number_label") or "").strip()
    group_id = str(item.get("groupId") or item.get("group_id") or "").strip()
    title = number_label or group_id or "긴 지문"
    return {
        "type": "passage_review_queue_remaining",
        "severity": "warning",
        "message": f"{title} 긴 지문 검수 큐가 남아 있습니다. EDB publish 전에 지문 병합/하위 문제 상태를 확인해 주세요.",
        "problemId": problem_ids[0] if problem_ids else "",
        "problemTitle": title,
        "numberLabel": number_label,
        "groupId": group_id,
        "problemIds": problem_ids,
        "sourcePageIds": source_page_ids,
        "continuesAcrossPages": bool(item.get("continuesAcrossPages") or item.get("continues_across_pages")),
        "blocking": True,
    }


def _session_passage_review_queue_issues(
    session: dict[str, Any] | None,
    *,
    problems: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    actionable_flags: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(session, dict):
        return []
    raw_items = session.get("passageReviewItems")
    if not isinstance(raw_items, list):
        raw_items = session.get("passage_review_items")
    if not isinstance(raw_items, list):
        return []
    unresolved_problem_ids = _session_unresolved_review_problem_ids(
        problems=problems,
        pages=pages,
        actionable_flags=actionable_flags,
    )
    publish_problem_ids = {
        str(problem.get("id") or problem.get("problem_id") or "").strip()
        for problem in problems
        if str(problem.get("id") or problem.get("problem_id") or "").strip()
    }
    issues: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        problem_ids = _passage_review_item_problem_ids(item)
        if problem_ids:
            if not any(problem_id in publish_problem_ids for problem_id in problem_ids):
                continue
            if not any(problem_id in unresolved_problem_ids for problem_id in problem_ids):
                continue
        issues.append(_passage_review_queue_issue(item))
    return issues


def _session_publish_blocking_preflight(
    problems: list[dict[str, Any]],
    session: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checked_problems = [
        problem
        for problem in problems
        if isinstance(problem, dict) and not _session_problem_is_supplemental(problem)
    ]
    pages = [page for page in ((session or {}).get("pages") or []) if isinstance(page, dict)]
    review_session = dict(session or {})
    review_session["problems"] = checked_problems
    review_session["pages"] = pages
    review_summary = _session_review_summary(review_session)
    actionable_flags = set(review_summary.get("actionableRiskFlagCounts") or {})
    duplicate_groups = [
        dict(group)
        for group in _session_duplicate_problem_number_groups(checked_problems)
        if isinstance(group, dict) and group.get("blocking") is not False
    ]
    issues: list[dict[str, Any]] = [
        _duplicate_problem_number_group_issue(group)
        for group in duplicate_groups
    ]
    issues.extend(dict(issue) for issue in _classin_passage_group_source_reuse_issues(checked_problems))
    issues.extend(dict(issue) for issue in _classin_source_bbox_overlap_issues(checked_problems))
    issues.extend(dict(issue) for issue in _classin_board_placement_overlap_issues(checked_problems))
    issues.extend(
        _session_passage_review_queue_issues(
            session,
            problems=checked_problems,
            pages=pages,
            actionable_flags=actionable_flags,
        )
    )

    blocking_issues: list[dict[str, Any]] = []
    for issue in issues:
        issue_copy = dict(issue)
        issue_copy["blocking"] = True
        blocking_issues.append(issue_copy)

    status = "passed" if not blocking_issues else "blocked"
    preflight = {
        "status": status,
        "passed": not blocking_issues,
        "checkedProblemCount": len(checked_problems),
        "checked_problem_count": len(checked_problems),
        "issueCount": len(blocking_issues),
        "issue_count": len(blocking_issues),
        "issues": blocking_issues,
        "gate": "session_publish",
        "gateLabel": "EDB publish",
        "gate_label": "EDB publish",
    }
    return preflight, duplicate_groups


def _session_publish_preflight_blocked_payload(
    preflight: dict[str, Any],
    duplicate_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    issues = preflight.get("issues") if isinstance(preflight.get("issues"), list) else []
    issue_types = sorted(
        {
            str(issue.get("type") or "")
            for issue in issues
            if isinstance(issue, dict) and str(issue.get("type") or "")
        }
    )
    blocking_problem_ids: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        raw_ids = issue.get("problemIds") or issue.get("problem_ids") or []
        if not isinstance(raw_ids, list):
            raw_ids = []
        for value in [
            *raw_ids,
            issue.get("problemId") or issue.get("problem_id"),
            issue.get("nextProblemId") or issue.get("next_problem_id"),
        ]:
            problem_id = str(value or "").strip()
            if problem_id and problem_id not in blocking_problem_ids:
                blocking_problem_ids.append(problem_id)
    return {
        "ok": False,
        "error": "ClassIn 사전점검에서 겹침/중복 문제가 발견되어 EDB publish를 중단했습니다.",
        "errorKind": "publish_preflight_blocked",
        "error_kind": "publish_preflight_blocked",
        "classinPreflight": preflight,
        "classin_preflight": preflight,
        "classinPreflightStatus": preflight.get("status"),
        "classin_preflight_status": preflight.get("status"),
        "classinPreflightPassed": False,
        "classin_preflight_passed": False,
        "classinPreflightIssueCount": int(preflight.get("issueCount") or 0),
        "classin_preflight_issue_count": int(preflight.get("issueCount") or 0),
        "blockingDuplicateProblemNumberGroups": duplicate_groups,
        "blocking_duplicate_problem_number_groups": duplicate_groups,
        "blockingIssueTypes": issue_types,
        "blocking_issue_types": issue_types,
        "blockingProblemIds": blocking_problem_ids,
        "blocking_problem_ids": blocking_problem_ids,
    }


def _session_publish_summary(
    *,
    edb_path: str | Path,
    output_dir: str | Path,
    edb_validation: dict[str, Any],
    record_count: int,
    core_problem_count: int | None = None,
    supplemental_item_count: int | None = None,
    classin_handoff_path: str | Path | None = None,
    classin_handoff_markdown_path: str | Path | None = None,
    classin_preflight: dict[str, Any] | None = None,
    passage_groups: list[dict[str, Any]] | None = None,
    passage_group_count: int | None = None,
    passage_problem_count: int | None = None,
    cross_page_passage_group_count: int | None = None,
    passage_review_items: list[dict[str, Any]] | None = None,
    passage_review_item_count: int | None = None,
    cross_page_passage_review_item_count: int | None = None,
    passage_group_source_reuse_groups: list[dict[str, Any]] | None = None,
    passage_group_source_reuse_group_count: int | None = None,
    published_at: str | None = None,
) -> dict[str, Any]:
    resolved_edb_path = Path(edb_path).resolve()
    resolved_output_dir = Path(output_dir).resolve()
    resolved_classin_handoff_path = Path(classin_handoff_path).resolve() if classin_handoff_path else None
    resolved_classin_handoff_markdown_path = (
        Path(classin_handoff_markdown_path).resolve()
        if classin_handoff_markdown_path
        else None
    )
    record_count_actual = int(edb_validation.get("recordCountActual") or record_count or 0)
    record_count_hint = int(edb_validation.get("recordCountHint") or record_count_actual or 0)
    page_count_hint = int(edb_validation.get("pageCountHint") or 0)
    supplemental_count = max(0, int(supplemental_item_count or 0))
    if core_problem_count is None:
        core_count = max(0, int(record_count or record_count_actual) - supplemental_count)
    else:
        core_count = max(0, int(core_problem_count or 0))
    record_count_label = (
        f"{core_count}문항 + 자료 {supplemental_count}"
        if supplemental_count
        else f"{int(record_count or record_count_actual)}개 자료"
    )
    preflight = dict(classin_preflight or {})
    preflight_status = str(preflight.get("status") or "")
    preflight_issue_count = int(preflight.get("issueCount") or preflight.get("issue_count") or 0)
    preflight_passed = bool(preflight.get("passed")) if preflight else False
    normalized_passage_groups = [
        dict(group)
        for group in (passage_groups or [])
        if isinstance(group, dict)
    ]
    if passage_group_count is None:
        passage_group_count = len(normalized_passage_groups)
    if passage_problem_count is None:
        passage_problem_count = sum(_passage_group_problem_count(group) for group in normalized_passage_groups)
    if cross_page_passage_group_count is None:
        cross_page_passage_group_count = sum(
            1
            for group in normalized_passage_groups
            if group.get("continuesAcrossPages") or group.get("continues_across_pages")
        )
    normalized_passage_review_items = [
        dict(item)
        for item in (passage_review_items or [])
        if isinstance(item, dict)
    ]
    if passage_review_item_count is None:
        passage_review_item_count = len(normalized_passage_review_items)
    if cross_page_passage_review_item_count is None:
        cross_page_passage_review_item_count = sum(
            1
            for item in normalized_passage_review_items
            if item.get("continuesAcrossPages") or item.get("continues_across_pages")
        )
    normalized_passage_group_source_reuse_groups = [
        dict(group)
        for group in (passage_group_source_reuse_groups or [])
        if isinstance(group, dict)
    ]
    if passage_group_source_reuse_group_count is None:
        passage_group_source_reuse_group_count = len(normalized_passage_group_source_reuse_groups)
    handoff_status, ready_for_classin = _classin_handoff_readiness(resolved_classin_handoff_path)
    if ready_for_classin is None and handoff_status:
        ready_for_classin = handoff_status == "ready_for_classin_review"
    published_at = published_at or datetime.now().astimezone().isoformat(timespec="seconds")
    summary = {
        "validated": True,
        "statusLabel": "검증 완료",
        "edbFileName": resolved_edb_path.name,
        "edbPath": str(resolved_edb_path),
        "edbFileUri": path_to_api_url(resolved_edb_path),
        "outputDir": str(resolved_output_dir),
        "classinHandoffPath": str(resolved_classin_handoff_path) if resolved_classin_handoff_path else None,
        "classinHandoffUri": path_to_api_url(resolved_classin_handoff_path),
        "classinHandoffMarkdownPath": (
            str(resolved_classin_handoff_markdown_path)
            if resolved_classin_handoff_markdown_path
            else None
        ),
        "classinHandoffMarkdownUri": path_to_api_url(resolved_classin_handoff_markdown_path),
        "classinHandoffStatus": handoff_status,
        "readyForClassIn": bool(ready_for_classin) if ready_for_classin is not None else False,
        "classinPreflight": preflight,
        "classinPreflightStatus": preflight_status,
        "classinPreflightPassed": preflight_passed,
        "classinPreflightIssueCount": preflight_issue_count,
        "passageGroups": normalized_passage_groups,
        "passageGroupCount": max(0, int(passage_group_count or 0)),
        "passageProblemCount": max(0, int(passage_problem_count or 0)),
        "crossPagePassageGroupCount": max(0, int(cross_page_passage_group_count or 0)),
        "passageReviewItems": normalized_passage_review_items,
        "passageReviewItemCount": max(0, int(passage_review_item_count or 0)),
        "crossPagePassageReviewItemCount": max(0, int(cross_page_passage_review_item_count or 0)),
        "passageGroupSourceReuseGroups": normalized_passage_group_source_reuse_groups,
        "passageGroupSourceReuseGroupCount": max(0, int(passage_group_source_reuse_group_count or 0)),
        "edbFileExists": resolved_edb_path.is_file(),
        "outputDirExists": resolved_output_dir.is_dir(),
        "recordCount": int(record_count or record_count_actual),
        "recordCountActual": record_count_actual,
        "recordCountHint": record_count_hint,
        "coreProblemCount": core_count,
        "supplementalItemCount": supplemental_count,
        "recordCountLabel": record_count_label,
        "pageCountHint": page_count_hint,
        "outerSize": int(edb_validation.get("outerSize") or 0),
        "innerSize": int(edb_validation.get("innerSize") or 0),
        "publishedAt": published_at,
        "edbValidation": dict(edb_validation),
    }
    summary.update({
        "status_label": summary["statusLabel"],
        "edb_file_name": summary["edbFileName"],
        "edb_path": summary["edbPath"],
        "edb_file_uri": summary["edbFileUri"],
        "output_dir": summary["outputDir"],
        "classin_handoff_path": summary["classinHandoffPath"],
        "classin_handoff_uri": summary["classinHandoffUri"],
        "classin_handoff_markdown_path": summary["classinHandoffMarkdownPath"],
        "classin_handoff_markdown_uri": summary["classinHandoffMarkdownUri"],
        "classin_handoff_status": summary["classinHandoffStatus"],
        "ready_for_classin": summary["readyForClassIn"],
        "classin_preflight": summary["classinPreflight"],
        "classin_preflight_status": summary["classinPreflightStatus"],
        "classin_preflight_passed": summary["classinPreflightPassed"],
        "classin_preflight_issue_count": summary["classinPreflightIssueCount"],
        "passage_groups": summary["passageGroups"],
        "passage_group_count": summary["passageGroupCount"],
        "passage_problem_count": summary["passageProblemCount"],
        "cross_page_passage_group_count": summary["crossPagePassageGroupCount"],
        "passage_review_items": summary["passageReviewItems"],
        "passage_review_item_count": summary["passageReviewItemCount"],
        "cross_page_passage_review_item_count": summary["crossPagePassageReviewItemCount"],
        "passage_group_source_reuse_groups": summary["passageGroupSourceReuseGroups"],
        "passage_group_source_reuse_group_count": summary["passageGroupSourceReuseGroupCount"],
        "edb_file_exists": summary["edbFileExists"],
        "output_dir_exists": summary["outputDirExists"],
        "record_count": summary["recordCount"],
        "record_count_actual": summary["recordCountActual"],
        "record_count_hint": summary["recordCountHint"],
        "core_problem_count": summary["coreProblemCount"],
        "supplemental_item_count": summary["supplementalItemCount"],
        "record_count_label": summary["recordCountLabel"],
        "page_count_hint": summary["pageCountHint"],
        "outer_size": summary["outerSize"],
        "inner_size": summary["innerSize"],
        "published_at": summary["publishedAt"],
        "edb_validation": summary["edbValidation"],
    })
    return summary


def _session_publish_history(
    source_session: dict[str, Any] | None,
    current_summary: dict[str, Any],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    source_session = source_session or {}
    existing = source_session.get("publish_history")
    if not isinstance(existing, list):
        existing = source_session.get("publishHistory")
    if not isinstance(existing, list):
        previous_summary = source_session.get("publish_summary")
        if not isinstance(previous_summary, dict):
            previous_summary = source_session.get("publishSummary")
        existing = [previous_summary] if isinstance(previous_summary, dict) else []
    history: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw in [current_summary, *existing]:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = str(item.get("edbPath") or item.get("edb_path") or item.get("edbFileName") or item.get("edb_file_name") or "")
        if key and key in seen_paths:
            continue
        if key:
            seen_paths.add(key)
        history.append(item)
        if len(history) >= limit:
            break
    return history


def _coerce_review_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "ok", "passed", "완료"}
    return default


def _classin_review_payload(
    payload: dict[str, Any],
    *,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    requested_status = str(payload.get("status") or payload.get("classinReviewStatus") or "").strip().lower()
    if requested_status not in {"passed", "needs_fix", "pending"}:
        requested_status = "passed" if payload.get("passed", True) is not False else "needs_fix"
    passed = requested_status == "passed"
    status_labels = {
        "passed": "ClassIn 확인 완료",
        "needs_fix": "ClassIn 재검수 필요",
        "pending": "ClassIn 검수 대기",
    }
    reviewed_at = reviewed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    review = {
        "status": requested_status,
        "statusLabel": status_labels[requested_status],
        "status_label": status_labels[requested_status],
        "manualReviewRequired": not passed,
        "manual_review_required": not passed,
        "classinOpened": _coerce_review_bool(payload, "classinOpened", default=passed),
        "recordCountOk": _coerce_review_bool(payload, "recordCountOk", default=passed),
        "orderOk": _coerce_review_bool(payload, "orderOk", default=passed),
        "readabilityOk": _coerce_review_bool(payload, "readabilityOk", default=passed),
        "supplementalItemsOk": _coerce_review_bool(payload, "supplementalItemsOk", default=passed),
        "notes": str(payload.get("notes") or "").strip(),
        "reviewedAt": reviewed_at,
        "reviewed_at": reviewed_at,
    }
    return review


def _attach_classin_review_to_publish_summary(summary: dict[str, Any], review: dict[str, Any]) -> None:
    summary["classinReview"] = dict(review)
    summary["classin_review"] = dict(review)
    summary["classinReviewStatus"] = review["status"]
    summary["classinReviewStatusLabel"] = review["statusLabel"]
    summary["classinReviewPassed"] = review["status"] == "passed"
    summary["classin_review_status"] = review["status"]
    summary["classin_review_status_label"] = review["statusLabel"]
    summary["classin_review_passed"] = review["status"] == "passed"


def _apply_classin_review_result(
    session: dict[str, Any],
    payload: dict[str, Any],
    *,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    review = _classin_review_payload(payload, reviewed_at=reviewed_at)
    session["classinReview"] = dict(review)
    session["classin_review"] = dict(review)
    for key in ("publishSummary", "publish_summary"):
        if isinstance(session.get(key), dict):
            _attach_classin_review_to_publish_summary(session[key], review)
    for history_key in ("publishHistory", "publish_history"):
        history = session.get(history_key)
        if isinstance(history, list) and history and isinstance(history[0], dict):
            _attach_classin_review_to_publish_summary(history[0], review)
    return review


def _session_history_key(session: dict[str, Any]) -> str:
    for key in ("output_dir", "outputDir", "pages_json_path", "pagesJsonPath"):
        value = str(session.get(key) or "").strip()
        if value:
            return value
    name = str(session.get("session_name") or session.get("sessionName") or "session")
    generated_at = str(session.get("generated_at") or session.get("generatedAt") or "")
    source_files = "|".join(str(path) for path in (session.get("input_files") or session.get("inputFiles") or []))
    return f"{name}|{generated_at}|{source_files}"


def _session_history_entry(session: dict[str, Any], *, updated_at: str | None = None) -> dict[str, Any]:
    session_snapshot = json.loads(json.dumps(session))
    problems = [problem for problem in (session_snapshot.get("problems") or []) if isinstance(problem, dict)]
    counts = _session_problem_count_payload(problems)
    output_dir = str(session_snapshot.get("output_dir") or session_snapshot.get("outputDir") or "")
    generated_at = str(session_snapshot.get("generated_at") or session_snapshot.get("generatedAt") or "")
    session_name = str(session_snapshot.get("session_name") or session_snapshot.get("sessionName") or "새 세션")
    history_key = _session_history_key(session_snapshot)
    entry_id = hashlib.sha1(history_key.encode("utf-8", errors="ignore")).hexdigest()[:16]
    publish_summary = session_snapshot.get("publishSummary")
    if not isinstance(publish_summary, dict):
        publish_summary = session_snapshot.get("publish_summary")
    review_summary = session_snapshot.get("reviewSummary")
    if not isinstance(review_summary, dict):
        review_summary = session_snapshot.get("review_summary")
    updated_value = updated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "id": entry_id,
        "sessionName": session_name,
        "session_name": session_name,
        "outputDir": output_dir,
        "output_dir": output_dir,
        "generatedAt": generated_at,
        "generated_at": generated_at,
        "updatedAt": updated_value,
        "updated_at": updated_value,
        "detectedProblemCount": counts["detected_problem_count"],
        "coreProblemCount": counts["core_problem_count"],
        "supplementalItemCount": counts["supplemental_item_count"],
        "publishSummary": publish_summary or None,
        "reviewSummary": review_summary or None,
        "inputFileCount": int(session_snapshot.get("input_file_count") or len(session_snapshot.get("input_files") or [])),
        "session": session_snapshot,
    }


def _session_history_with_session(
    history: list[dict[str, Any]],
    session: dict[str, Any],
    *,
    updated_at: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    entry = _session_history_entry(session, updated_at=updated_at)
    seen = {entry["id"]}
    merged = [entry]
    for raw in history:
        if not isinstance(raw, dict):
            continue
        entry_id = str(raw.get("id") or "")
        if not entry_id or entry_id in seen:
            continue
        seen.add(entry_id)
        merged.append(raw)
        if len(merged) >= limit:
            break
    return merged


def _public_session_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_entries: list[dict[str, Any]] = []
    for raw in history:
        if not isinstance(raw, dict):
            continue
        item = {key: value for key, value in raw.items() if key != "session"}
        if isinstance(item.get("publishSummary"), dict):
            item["publishSummary"] = _publish_artifact_state(item["publishSummary"])
        if isinstance(item.get("publish_summary"), dict):
            item["publish_summary"] = _publish_artifact_state(item["publish_summary"])
        public_entries.append(item)
    return public_entries


def content_disposition_attachment(filename: str) -> str:
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\", ";"} else "_"
        for ch in filename
    ).strip()
    if not fallback or fallback in {".", ".."}:
        fallback = "download.edb"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


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


def load_session_history(path: Path = SESSION_HISTORY_JSON) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict)]


def save_session_history(history: list[dict[str, Any]], path: Path = SESSION_HISTORY_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_session_history(
    session: dict[str, Any],
    *,
    path: Path = SESSION_HISTORY_JSON,
    updated_at: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    history = _session_history_with_session(load_session_history(path), session, updated_at=updated_at, limit=limit)
    save_session_history(history, path)
    return history


def collect_session_file_paths(session: dict[str, Any]) -> set[str]:
    paths: set[str] = set()

    def add_path(value: Any) -> None:
        if not value:
            return
        resolved = decode_file_reference(str(value))
        if resolved and resolved.exists():
            paths.add(str(resolved))

    for key in (
        "edb_path",
        "pages_json_path",
        "placements_json_path",
        "classin_handoff_path",
        "classin_handoff_markdown_path",
    ):
        add_path(session.get(key))

    for value in session.get("rendered_page_paths", []):
        add_path(value)
    for value in session.get("rendered_page_file_uris", []):
        add_path(value)

    for problem in session.get("problems", []):
        for key in ("imagePath", "sourceImagePath", "boardRenderPath", "originalImagePath"):
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


def _target_within_allowed_roots(target: Path) -> bool:
    roots = [BASE_DIR.resolve(), RUNTIME_DIR.resolve()]
    return any(str(target) == str(root) or str(target).startswith(str(root) + os.sep) for root in roots)


def _resolve_open_target(raw_path: Any, *, kind: str) -> Path:
    if not raw_path:
        raise ValueError("path is required")
    target = _file_uri_to_path(raw_path)
    if target is None:
        raise ValueError("path is required")
    try:
        target = target.resolve() if target.is_absolute() else (BASE_DIR / target).resolve()
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid path: {exc}") from exc
    if not _target_within_allowed_roots(target):
        raise ValueError("path outside allowed roots")
    if kind == "folder":
        if target.is_file():
            target = target.parent
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(f"folder not found: {target}")
        return target
    if kind == "file":
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"file not found: {target}")
        return target
    raise ValueError(f"unknown open target kind: {kind}")


def _open_system_target(target: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def _problems_to_entries(problems: list[dict[str, Any]]) -> list[ProblemEntry]:
    entries: list[ProblemEntry] = []
    for problem in problems:
        if _session_problem_is_supplemental(problem):
            continue
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
                processing_step=_normalize_processing_step(
                    problem.get("processingStep")
                    or problem.get("processing_step")
                    or problem.get("step")
                ),
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
    template = LayoutTemplate(**kwargs)
    template.base_slot_height_pages = ONE_PROBLEM_SLOT_HEIGHT_PAGES
    template.metadata["placement_mode"] = "one-problem-per-page"
    return template


def rewrite_session_for_http(session: dict[str, Any]) -> dict[str, Any]:
    rewritten = json.loads(json.dumps(session))
    rewritten["edb_file_uri"] = path_to_api_url(session.get("edb_path") or session.get("edb_file_uri"))
    rewritten["classin_handoff_uri"] = path_to_api_url(session.get("classin_handoff_path"))
    rewritten["classin_handoff_markdown_uri"] = path_to_api_url(session.get("classin_handoff_markdown_path"))
    rewritten["rendered_page_file_uris"] = [path_to_api_url(value) for value in session.get("rendered_page_paths", [])]

    for problem in rewritten.get("problems", []):
        problem["imagePath"] = path_to_api_url(problem.get("imagePath"))
        problem["sourceImagePath"] = path_to_api_url(problem.get("sourceImagePath"))
        problem["boardRenderPath"] = path_to_api_url(problem.get("boardRenderPath"))
        problem["originalImagePath"] = path_to_api_url(problem.get("originalImagePath"))

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
        "step": _normalize_processing_step(
            parent.get("processingStep")
            or parent.get("processing_step")
            or parent.get("step")
        ),
        "processingStep": _normalize_processing_step(
            parent.get("processingStep")
            or parent.get("processing_step")
            or parent.get("step")
        ),
        "textRecordCount": parent.get("textRecordCount", 0),
        "imageRecordCount": parent.get("imageRecordCount", 1),
        "placementXRatio": _coerce_placement_x_ratio(parent),
        "placementYRatio": _coerce_placement_y_ratio(parent),
        "placementScaleRatio": _coerce_placement_scale_ratio(parent),
        "riskFlags": [],  # mutated entries lose the auto-detected risk
    }
    return skeleton


def _refresh_session_problem_counts(session: dict[str, Any]) -> None:
    problems = [problem for problem in (session.get("problems") or []) if isinstance(problem, dict)]
    pages = [page for page in (session.get("pages") or []) if isinstance(page, dict)]
    counts = _session_problem_count_payload(problems)
    session.update(counts)
    session["detectedProblemCount"] = counts["detected_problem_count"]
    session["coreProblemCount"] = counts["core_problem_count"]
    session["supplementalItemCount"] = counts["supplemental_item_count"]
    summary = _session_review_summary(session)
    session["review_summary"] = summary
    session["reviewSummary"] = summary
    _normalize_session_passage_review_queue(
        session,
        unresolved_problem_ids=_session_unresolved_review_problem_ids(
            problems=problems,
            pages=pages,
            actionable_flags=set(summary.get("actionableRiskFlagCounts") or {}),
        ),
    )


def _metadata_from_page(page: dict[str, Any]) -> dict[str, Any]:
    metadata = page.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    return page


def _metadata_list_count(metadata: dict[str, Any], key: str) -> int:
    value = metadata.get(key)
    if isinstance(value, list):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _hwp_quality_from_page(page: dict[str, Any]) -> dict[str, Any] | None:
    metadata = _metadata_from_page(page)
    quality = metadata.get("hwp_conversion_quality")
    return quality if isinstance(quality, dict) else None


def _session_pages_json_pages(session: dict[str, Any]) -> list[dict[str, Any]]:
    pages_json_value = session.get("pages_json_path") or session.get("pagesJsonPath")
    pages_json_path = decode_file_reference(str(pages_json_value)) if pages_json_value else None
    if pages_json_path is None or not pages_json_path.exists():
        return []
    try:
        payload = json.loads(pages_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [page for page in payload if isinstance(page, dict)]


def _session_hwp_quality_pages(session: dict[str, Any]) -> list[dict[str, Any]]:
    inline_pages = [page for page in (session.get("pages") or []) if isinstance(page, dict)]
    if any(_hwp_quality_from_page(page) for page in inline_pages):
        return inline_pages
    return _session_pages_json_pages(session)


def _metadata_flag_is_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _page_has_hwp_cache_metadata(page: dict[str, Any]) -> bool:
    metadata = _metadata_from_page(page)
    return "hwp_renderer_cache_hit" in metadata or "hwp_normalized_cache_hit" in metadata


def _session_hwp_cache_pages(session: dict[str, Any]) -> list[dict[str, Any]]:
    inline_pages = [page for page in (session.get("pages") or []) if isinstance(page, dict)]
    if any(_page_has_hwp_cache_metadata(page) for page in inline_pages):
        return inline_pages
    return _session_pages_json_pages(session)


def _session_warning_messages(session: dict[str, Any]) -> list[str]:
    warnings = session.get("warning_messages")
    if not isinstance(warnings, list):
        warnings = session.get("warningMessages")
    if not isinstance(warnings, list):
        return []
    return [str(message) for message in warnings if str(message or "").strip()]


NON_ACTIONABLE_REVIEW_RISK_FLAGS = {
    "marker_document_continuation",
    "ocr_disabled",
}

HWP_COUNT_MATCH_DISMISSIBLE_REVIEW_RISK_FLAGS = {
    "fallback_grouping",
    "large_block_dominance",
    "no_problem_markers",
    "problem_per_block",
    "sparse_segmentation",
}

PUBLISH_PRESERVED_PROBLEM_METADATA_KEYS = (
    ("passageGroupId", "passage_group_id"),
    ("passageRange", "passage_range"),
    ("passageRole", "passage_role"),
    ("sharedPassageBlockIds", "shared_passage_block_ids"),
    ("passageChildProblemNumbers", "passage_child_problem_numbers"),
    ("passageSourcePageIds", "passage_source_page_ids"),
    ("passageContinuesAcrossPages", "passage_continues_across_pages"),
    ("passagePreQuestionContinuationBlockIds", "passage_pre_question_continuation_block_ids"),
)


def _session_problem_is_supplemental(problem: dict[str, Any]) -> bool:
    risk_flags = problem.get("riskFlags") or problem.get("risk_flags") or []
    if isinstance(risk_flags, list) and "marker_document_continuation" in {str(flag) for flag in risk_flags}:
        return True
    metadata = problem.get("metadata")
    if isinstance(metadata, dict) and metadata.get("marker_document_continuation"):
        return True
    problem_id = str(problem.get("id") or problem.get("problem_id") or "")
    return problem_id.endswith("-continuation")


def _has_session_metadata_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, set, tuple)):
        return bool(value)
    return True


def _clone_session_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def _copy_session_metadata_aliases(
    target: dict[str, Any],
    source: dict[str, Any],
    aliases: tuple[str, ...],
) -> None:
    if any(_has_session_metadata_value(target.get(key)) for key in aliases):
        return
    source_metadata = source.get("metadata")
    if not isinstance(source_metadata, dict):
        source_metadata = {}
    value: Any = None
    found = False
    for key in aliases:
        candidate = source.get(key)
        if _has_session_metadata_value(candidate):
            value = candidate
            found = True
            break
    if not found:
        for key in aliases:
            candidate = source_metadata.get(key)
            if _has_session_metadata_value(candidate):
                value = candidate
                found = True
                break
    if not found:
        return
    for key in aliases:
        target[key] = _clone_session_metadata_value(value)


def _copy_publish_problem_metadata(target: dict[str, Any], source: dict[str, Any]) -> None:
    for aliases in PUBLISH_PRESERVED_PROBLEM_METADATA_KEYS:
        _copy_session_metadata_aliases(target, source, aliases)


def _session_actionable_problem_ids(
    *,
    problems: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    actionable_flags: set[str],
) -> set[str]:
    actionable_problem_ids: set[str] = set()
    for index, problem in enumerate(problems):
        problem_id = str(problem.get("id") or problem.get("problem_id") or f"problem-index-{index}")
        problem_flags = {
            str(flag or "").strip()
            for flag in (problem.get("riskFlags") or problem.get("risk_flags") or [])
            if str(flag or "").strip()
        }
        if _problem_review_status(problem) == "failed" or problem_flags.intersection(actionable_flags):
            actionable_problem_ids.add(problem_id)
    for page in pages:
        page_flags = {
            str(flag or "").strip()
            for flag in (page.get("riskFlags") or page.get("risk_flags") or [])
            if str(flag or "").strip()
        }
        if not page_flags.intersection(actionable_flags):
            continue
        page_problem_ids = [str(pid) for pid in (page.get("problemIds") or page.get("problem_ids") or []) if pid]
        actionable_problem_ids.update(page_problem_ids)
    return actionable_problem_ids


def _session_unresolved_review_problem_ids(
    *,
    problems: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    actionable_flags: set[str],
) -> set[str]:
    unresolved_problem_ids = _session_actionable_problem_ids(
        problems=problems,
        pages=pages,
        actionable_flags=actionable_flags,
    )
    for index, problem in enumerate(problems):
        problem_id = str(problem.get("id") or problem.get("problem_id") or f"problem-index-{index}")
        if _problem_review_status(problem) != "normal":
            unresolved_problem_ids.add(problem_id)
    for page in pages:
        page_status = str(page.get("reviewStatus") or page.get("review_status") or "").strip().lower()
        page_flags = {
            str(flag or "").strip()
            for flag in (page.get("riskFlags") or page.get("risk_flags") or [])
            if str(flag or "").strip()
        }
        if page_status not in {"check_needed", "failed"} and not page_flags.intersection(actionable_flags):
            continue
        page_problem_ids = [str(pid) for pid in (page.get("problemIds") or page.get("problem_ids") or []) if pid]
        unresolved_problem_ids.update(page_problem_ids)
    return unresolved_problem_ids


def _passage_review_item_problem_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("problemIds", "problem_ids", "fragmentProblemIds", "fragment_problem_ids"):
        values = item.get(key)
        if isinstance(values, list):
            ids.extend(str(value or "").strip() for value in values)
    return [value for value in ids if value]


def _normalize_session_passage_review_queue(
    session: dict[str, Any],
    *,
    unresolved_problem_ids: set[str],
) -> None:
    raw_items = session.get("passageReviewItems")
    if not isinstance(raw_items, list):
        raw_items = session.get("passage_review_items")
    if not isinstance(raw_items, list):
        has_count_only_metadata = any(
            key in session
            for key in (
                "passageReviewItemCount",
                "passage_review_item_count",
                "crossPagePassageReviewItemCount",
                "cross_page_passage_review_item_count",
            )
        )
        if not has_count_only_metadata:
            return
        raw_items = []

    unresolved_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        problem_ids = _passage_review_item_problem_ids(item)
        if not problem_ids or any(problem_id in unresolved_problem_ids for problem_id in problem_ids):
            unresolved_items.append(dict(item))

    cross_page_count = sum(
        1
        for item in unresolved_items
        if item.get("continuesAcrossPages") or item.get("continues_across_pages")
    )
    session["passageReviewItems"] = unresolved_items
    session["passage_review_items"] = unresolved_items
    session["passageReviewItemCount"] = len(unresolved_items)
    session["passage_review_item_count"] = len(unresolved_items)
    session["crossPagePassageReviewItemCount"] = cross_page_count
    session["cross_page_passage_review_item_count"] = cross_page_count


def _session_review_summary(session: dict[str, Any]) -> dict[str, Any]:
    problems = [problem for problem in (session.get("problems") or []) if isinstance(problem, dict)]
    pages = [page for page in (session.get("pages") or []) if isinstance(page, dict)]
    counts = _session_problem_count_payload(problems)
    review_status_counts = {"all": 0, "normal": 0, "check_needed": 0, "failed": 0}
    supplemental_review_status_counts = {"all": 0, "normal": 0, "check_needed": 0, "failed": 0}
    core_review_status_counts = {"all": 0, "normal": 0, "check_needed": 0, "failed": 0}
    risk_flag_counts: dict[str, int] = {}
    for problem in problems:
        status = _problem_review_status(problem)
        target_counts = (
            supplemental_review_status_counts
            if _session_problem_is_supplemental(problem)
            else core_review_status_counts
        )
        review_status_counts["all"] += 1
        review_status_counts[status] = review_status_counts.get(status, 0) + 1
        target_counts["all"] += 1
        target_counts[status] = target_counts.get(status, 0) + 1
        for flag in problem.get("riskFlags") or problem.get("risk_flags") or []:
            flag_text = str(flag or "").strip()
            if flag_text:
                risk_flag_counts[flag_text] = risk_flag_counts.get(flag_text, 0) + 1

    for page in pages:
        for flag in page.get("riskFlags") or page.get("risk_flags") or []:
            flag_text = str(flag or "").strip()
            if flag_text:
                risk_flag_counts[flag_text] = risk_flag_counts.get(flag_text, 0) + 1

    top_risk_flags = [
        {"flag": flag, "count": count}
        for flag, count in sorted(risk_flag_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    ]
    hwp_problem_count_mismatch_count = int(risk_flag_counts.get("hwp_problem_count_mismatch") or 0)
    hwp_oversegmentation_count = int(risk_flag_counts.get("hwp_oversegmentation") or 0)
    needs_review_count = int(review_status_counts.get("check_needed", 0)) + int(review_status_counts.get("failed", 0))
    hwp_text_extractors: dict[str, int] = {}
    hwp_text_problem_signal_count = 0
    hwp_layout_extractors: dict[str, int] = {}
    hwp_layout_problem_signal_count = 0
    hwp_layout_duplicate_skip_count = 0
    hwp_layout_page_count = 0
    hwp_layout_text_line_count = 0
    hwp_renderer_cache_hit_count = 0
    hwp_normalized_cache_hit_count = 0
    hwp_cache_hit_page_count = 0

    for page in _session_hwp_cache_pages(session):
        metadata = _metadata_from_page(page)
        renderer_cache_hit = _metadata_flag_is_truthy(metadata.get("hwp_renderer_cache_hit"))
        normalized_cache_hit = _metadata_flag_is_truthy(metadata.get("hwp_normalized_cache_hit"))
        if renderer_cache_hit:
            hwp_renderer_cache_hit_count += 1
        if normalized_cache_hit:
            hwp_normalized_cache_hit_count += 1
        if renderer_cache_hit or normalized_cache_hit:
            hwp_cache_hit_page_count += 1

    for page in _session_hwp_quality_pages(session):
        metadata = _metadata_from_page(page)
        quality = _hwp_quality_from_page(page)
        if quality is None:
            continue
        extractor = str(quality.get("hwp_text_extractor") or "").strip()
        if extractor:
            hwp_text_extractors[extractor] = hwp_text_extractors.get(extractor, 0) + 1
        try:
            numbered = int(quality.get("hwp_text_numbered_problem_count") or 0)
            stem = int(quality.get("hwp_text_stem_problem_count") or 0)
        except (TypeError, ValueError):
            numbered = 0
            stem = 0
        hwp_text_problem_signal_count = max(hwp_text_problem_signal_count, numbered, stem)
        layout_extractor = str(quality.get("hwp_layout_extractor") or "").strip()
        if layout_extractor:
            hwp_layout_extractors[layout_extractor] = hwp_layout_extractors.get(layout_extractor, 0) + 1
        try:
            layout_markers = int(quality.get("hwp_layout_problem_marker_count") or 0)
            layout_pages = int(quality.get("hwp_layout_page_count") or 0)
            layout_lines = int(quality.get("hwp_layout_text_line_count") or 0)
        except (TypeError, ValueError):
            layout_markers = 0
            layout_pages = 0
            layout_lines = 0
        hwp_layout_problem_signal_count = max(hwp_layout_problem_signal_count, layout_markers)
        hwp_layout_duplicate_skip_count += _metadata_list_count(metadata, "duplicate_problem_numbers_skipped")
        hwp_layout_page_count = max(hwp_layout_page_count, layout_pages)
        hwp_layout_text_line_count = max(hwp_layout_text_line_count, layout_lines)
    if hwp_layout_problem_signal_count > 0 and hwp_layout_duplicate_skip_count > 0:
        hwp_layout_problem_signal_count = max(0, hwp_layout_problem_signal_count - hwp_layout_duplicate_skip_count)

    warning_messages = _session_warning_messages(session)
    hwp_text_problem_delta = 0
    hwp_text_problem_count_status = "unknown"
    hwp_text_problem_count_message = ""
    hwp_text_problem_count_matches = False
    hwp_layout_problem_delta = 0
    hwp_layout_problem_count_status = "unknown"
    hwp_layout_problem_count_message = ""
    hwp_layout_problem_count_matches = False
    if hwp_text_problem_signal_count > 0:
        core_count = int(counts["core_problem_count"])
        hwp_text_problem_delta = core_count - hwp_text_problem_signal_count
        hwp_text_problem_count_matches = hwp_text_problem_delta == 0
        if hwp_text_problem_count_matches:
            hwp_text_problem_count_status = "match"
            hwp_text_problem_count_message = "HWP 텍스트 문항 수와 검출 문항 수가 일치합니다."
        else:
            hwp_text_problem_count_status = "mismatch"
            if hwp_text_problem_delta > 0:
                hwp_text_problem_count_message = (
                    f"검출 문항이 HWP 텍스트 기준보다 {hwp_text_problem_delta}개 많습니다. "
                    "표지·안내문·보충 자료를 확인하세요."
                )
            else:
                hwp_text_problem_count_message = (
                    f"HWP 텍스트 기준 문항이 검출보다 {abs(hwp_text_problem_delta)}개 많습니다. "
                    "누락 문항을 확인하세요."
                )
            warning_messages = [*warning_messages, hwp_text_problem_count_message]
    if hwp_layout_problem_signal_count > 0:
        core_count = int(counts["core_problem_count"])
        hwp_layout_problem_delta = core_count - hwp_layout_problem_signal_count
        hwp_layout_problem_count_matches = hwp_layout_problem_delta == 0
        if hwp_layout_problem_count_matches:
            hwp_layout_problem_count_status = "match"
            hwp_layout_problem_count_message = "HWP 레이아웃 문항 수와 검출 문항 수가 일치합니다."
        else:
            hwp_layout_problem_count_status = "mismatch"
            if hwp_layout_problem_delta > 0:
                hwp_layout_problem_count_message = (
                    f"검출 문항이 HWP 레이아웃 기준보다 {hwp_layout_problem_delta}개 많습니다. "
                    "표지·안내문·보충 자료를 확인하세요."
                )
            else:
                hwp_layout_problem_count_message = (
                    f"HWP 레이아웃 기준 문항이 검출보다 {abs(hwp_layout_problem_delta)}개 많습니다. "
                    "누락 문항을 확인하세요."
                )
            if hwp_text_problem_signal_count <= 0:
                warning_messages = [*warning_messages, hwp_layout_problem_count_message]
    non_actionable_risk_flags = set(NON_ACTIONABLE_REVIEW_RISK_FLAGS)
    if hwp_text_problem_count_matches or hwp_layout_problem_count_matches:
        non_actionable_risk_flags.update(HWP_COUNT_MATCH_DISMISSIBLE_REVIEW_RISK_FLAGS)
    actionable_risk_flag_counts = {
        flag: count
        for flag, count in sorted(risk_flag_counts.items())
        if flag not in non_actionable_risk_flags
    }
    top_actionable_risk_flags = [
        {"flag": flag, "count": count}
        for flag, count in sorted(actionable_risk_flag_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    ]
    actionable_flags = set(actionable_risk_flag_counts)
    actionable_problem_ids: set[str] = set()
    for index, problem in enumerate(problems):
        problem_id = str(problem.get("id") or problem.get("problem_id") or f"problem-index-{index}")
        problem_flags = {
            str(flag or "").strip()
            for flag in (problem.get("riskFlags") or problem.get("risk_flags") or [])
            if str(flag or "").strip()
        }
        if _problem_review_status(problem) == "failed" or problem_flags.intersection(actionable_flags):
            actionable_problem_ids.add(problem_id)
    actionable_page_count = 0
    for page in pages:
        page_flags = {
            str(flag or "").strip()
            for flag in (page.get("riskFlags") or page.get("risk_flags") or [])
            if str(flag or "").strip()
        }
        if not page_flags.intersection(actionable_flags):
            continue
        page_problem_ids = [str(pid) for pid in (page.get("problemIds") or page.get("problem_ids") or []) if pid]
        if page_problem_ids:
            actionable_problem_ids.update(page_problem_ids)
        else:
            actionable_page_count += 1
    actionable_needs_review_count = len(actionable_problem_ids) + actionable_page_count
    return {
        "detectedProblemCount": counts["detected_problem_count"],
        "coreProblemCount": counts["core_problem_count"],
        "supplementalItemCount": counts["supplemental_item_count"],
        "reviewStatusCounts": review_status_counts,
        "coreReviewStatusCounts": core_review_status_counts,
        "supplementalReviewStatusCounts": supplemental_review_status_counts,
        "needsReviewCount": needs_review_count,
        "actionableNeedsReviewCount": actionable_needs_review_count,
        "riskFlagCounts": risk_flag_counts,
        "topRiskFlags": top_risk_flags,
        "hwpProblemCountMismatchCount": hwp_problem_count_mismatch_count,
        "hwpOversegmentationCount": hwp_oversegmentation_count,
        "actionableRiskFlagCounts": actionable_risk_flag_counts,
        "topActionableRiskFlags": top_actionable_risk_flags,
        "warningCount": len(warning_messages),
        "warningMessages": warning_messages,
        "hwpTextExtractors": hwp_text_extractors,
        "hwpTextProblemSignalCount": hwp_text_problem_signal_count,
        "hwpTextProblemCountStatus": hwp_text_problem_count_status,
        "hwpTextProblemCountMatches": hwp_text_problem_count_matches,
        "hwpTextProblemDelta": hwp_text_problem_delta,
        "hwpTextProblemCountMessage": hwp_text_problem_count_message,
        "hwpLayoutExtractors": hwp_layout_extractors,
        "hwpLayoutProblemSignalCount": hwp_layout_problem_signal_count,
        "hwpLayoutPageCount": hwp_layout_page_count,
        "hwpLayoutTextLineCount": hwp_layout_text_line_count,
        "hwpLayoutProblemCountStatus": hwp_layout_problem_count_status,
        "hwpLayoutProblemCountMatches": hwp_layout_problem_count_matches,
        "hwpLayoutProblemDelta": hwp_layout_problem_delta,
        "hwpLayoutProblemCountMessage": hwp_layout_problem_count_message,
        "hwpCacheHitPageCount": hwp_cache_hit_page_count,
        "hwpRendererCacheHitCount": hwp_renderer_cache_hit_count,
        "hwpNormalizedCacheHitCount": hwp_normalized_cache_hit_count,
    }


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
    _refresh_session_problem_counts(session)


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
    _refresh_session_problem_counts(session)
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
    _refresh_session_problem_counts(session)
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


def _coerce_problem_ids(problem_ids: Any) -> list[str]:
    if isinstance(problem_ids, str):
        raw_ids = [problem_ids]
    else:
        raw_ids = list(problem_ids or [])
    ids: list[str] = []
    seen: set[str] = set()
    for raw_id in raw_ids:
        problem_id = str(raw_id or "").strip()
        if not problem_id or problem_id in seen:
            continue
        ids.append(problem_id)
        seen.add(problem_id)
    return ids


def _mutate_exclude_many(session: dict[str, Any], problem_ids: Any) -> dict[str, Any]:
    ids = _coerce_problem_ids(problem_ids)
    if not ids:
        raise ValueError("problemIds is required")
    for problem_id in ids:
        _find_problem(session, problem_id)  # raises if missing
    _remove_problems(session, set(ids))
    return session


def _mutate_exclude(session: dict[str, Any], problem_id: str) -> dict[str, Any]:
    return _mutate_exclude_many(session, [problem_id])


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


def _enhance_target_problem_ids(session: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    raw_problem_ids = payload.get("problemIds") or payload.get("problem_ids") or []
    if payload.get("problemId") or payload.get("problem_id"):
        raw_problem_ids = [payload.get("problemId") or payload.get("problem_id")]
    ids = [str(pid) for pid in raw_problem_ids if pid]

    raw_page_ids = payload.get("pageIds") or payload.get("page_ids") or []
    if payload.get("pageId") or payload.get("page_id"):
        raw_page_ids = [payload.get("pageId") or payload.get("page_id")]
    page_ids = {str(pid) for pid in raw_page_ids if pid}
    if page_ids:
        for problem in session.get("problems", []):
            if not isinstance(problem, dict):
                continue
            if str(problem.get("sourcePageId") or "") in page_ids and problem.get("id"):
                ids.append(str(problem["id"]))

    if not ids:
        for problem in session.get("problems", []):
            if not isinstance(problem, dict) or not problem.get("id"):
                continue
            if _problem_review_status(problem) != "normal":
                ids.append(str(problem["id"]))

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
    _refresh_session_problem_counts(session)


def _image_reconstruction_dir(session: dict[str, Any]) -> Path:
    if session.get("output_dir"):
        target = Path(str(session["output_dir"])).resolve() / "ai_image_reconstructions"
    else:
        target = RUNTIME_DIR / "ai_image_reconstructions"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _payload_bool(payload: dict[str, Any], camel_key: str, snake_key: str, default: bool) -> bool:
    raw = payload.get(camel_key)
    if raw is None:
        raw = payload.get(snake_key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool(raw)


def _mutate_enhance_image(session: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    provider = normalize_image_provider(
        payload.get("provider")
        or payload.get("imageProvider")
        or payload.get("image_provider")
        or DEFAULT_IMAGE_RECONSTRUCTION_PROVIDER
    )
    env_key = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
    provider_label = "Gemini" if provider == "gemini" else "OpenAI"
    api_key = os.environ.get(env_key, "").strip()
    if not api_key:
        raise ValueError(f"{provider_label} API 키가 필요합니다. 칠판 설정에서 {env_key}를 저장한 뒤 다시 시도해 주세요.")

    problem_ids = _enhance_target_problem_ids(session, payload)
    if not problem_ids:
        raise ValueError("AI 업스케일할 문항이 없습니다.")

    model = normalize_image_model(provider, str(payload.get("model") or payload.get("imageModel") or default_image_model(provider)))
    prompt = str(payload.get("prompt") or payload.get("imagePrompt") or DEFAULT_RECONSTRUCTION_PROMPT)
    quality = str(payload.get("quality") or "high")
    size = str(payload.get("size") or "auto")
    timeout_ms = int(payload.get("timeoutMs") or payload.get("timeout_ms") or 120000)
    transparent_background = _payload_bool(payload, "transparentBackground", "transparent_background", True)
    sharpen = _payload_bool(payload, "sharpen", "sharpen", True)
    output_dir = _image_reconstruction_dir(session)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    summaries: list[dict[str, Any]] = []

    for problem_id in problem_ids:
        _index, problem = _find_problem(session, problem_id)
        source_path = _resolve_session_path(problem.get("imagePath") or problem.get("boardRenderPath"))
        if source_path is None or not source_path.exists():
            flags = list(problem.get("riskFlags") or [])
            flags.append("ai_image_missing_source")
            problem["riskFlags"] = list(dict.fromkeys(str(flag) for flag in flags if flag))
            problem["reviewStatus"] = "failed"
            problem["aiImageReconstruction"] = {
                "status": "missing_source",
                "error": f"problem image missing: {source_path}",
                "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            summaries.append({
                "problemId": problem_id,
                "status": "missing_source",
                "error": f"problem image missing: {source_path}",
            })
            continue

        safe_problem = sanitize_output_dir_name(problem_id) or "problem"
        safe_model = sanitize_output_dir_name(model) or provider
        output_path = output_dir / f"{safe_problem}_{stamp}_{safe_model}.png"
        try:
            result = reconstruct_problem_image(
                source_path,
                output_path,
                api_key=api_key,
                provider=provider,
                model=model,
                prompt=prompt,
                quality=quality,
                size=size,
                timeout_ms=timeout_ms,
                transparent_background=transparent_background,
                sharpen=sharpen,
            )
        except Exception as exc:  # noqa: BLE001 - surface the provider message to the UI
            flags = list(problem.get("riskFlags") or [])
            flags.append("ai_image_reconstruction_failed")
            problem["riskFlags"] = list(dict.fromkeys(str(flag) for flag in flags if flag))
            problem["reviewStatus"] = "check_needed"
            problem["aiImageReconstruction"] = {
                "status": "failed",
                "provider": provider,
                "model": model,
                "error": str(exc),
                "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            summaries.append({
                "problemId": problem_id,
                "status": "failed",
                "provider": provider,
                "model": model,
                "error": str(exc),
            })
            continue

        if not problem.get("originalImagePath"):
            problem["originalImagePath"] = problem.get("imagePath")
        uri = result.output_path.resolve().as_uri()
        problem["imagePath"] = uri
        problem["boardRenderPath"] = uri
        problem["step"] = "s3"
        problem["processingStep"] = "s3"
        problem["processing_step"] = "s3"
        flags = [str(flag) for flag in (problem.get("riskFlags") or []) if flag]
        flags.append("ai_image_reconstructed_check_text")
        problem["riskFlags"] = list(dict.fromkeys(flags))
        problem["reviewStatus"] = "check_needed"
        problem["aiImageReconstruction"] = {
            **result.to_metadata(),
            "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "quality": quality,
            "size": size,
            "transparent_background": transparent_background,
            "sharpen": sharpen,
        }
        summaries.append({
            "problemId": problem_id,
            "status": "applied",
            "provider": result.provider,
            "model": result.model,
            "outputPath": uri,
            "latencyMs": result.latency_ms,
            "postprocess": result.postprocess or {},
        })

    session["ai_image_reconstruction_summary"] = summaries
    return session


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
    session_output_dir = session.get("output_dir")
    if session_output_dir:
        output_root = Path(session_output_dir).resolve() / "ai_retries"
    else:
        output_root = (RUNTIME_DIR / "ai_retries").resolve()
    ai_config = session.get("ai_fallback") if isinstance(session.get("ai_fallback"), dict) else {}
    summaries: list[dict[str, Any]] = []
    retry_jobs: list[dict[str, Any]] = []
    retry_results_by_page_id: dict[str, dict[str, Any]] = {}

    for page_id in page_ids:
        page = _find_page(session, page_id)
        source_path = _resolve_session_path(page.get("sourceImagePath") or page.get("sourceImageUri"))
        if source_path is None or not source_path.exists():
            retry_results_by_page_id[page_id] = {
                "pageId": page_id,
                "status": "missing_source",
                "error": f"source page image missing for retry: {page_id}",
            }
        else:
            retry_jobs.append({
                "pageId": page_id,
                "sourcePath": source_path,
                "retryDir": output_root / sanitize_output_dir_name(f"{page_id}_{stamp}"),
            })

    def _run_retry_job(job: dict[str, Any]) -> dict[str, Any]:
        page_id = str(job["pageId"])
        try:
            result = run_problem_export(
                job["sourcePath"],
                output_dir=job["retryDir"],
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
                ai_fallback_timeout_ms=int(ai_config.get("timeout_ms") or 30000),
                ai_fallback_save_debug=bool(ai_config.get("save_debug")),
            )
        except Exception as exc:  # noqa: BLE001 - show the actionable pipeline message to the UI
            return {"pageId": page_id, "status": "failed", "error": str(exc)}
        return {"pageId": page_id, "status": "ok", "result": result}

    retry_worker_count = resolve_recognition_worker_count(
        len(retry_jobs),
        ocr_mode=str(payload.get("ocr") or "auto"),
        ai_config=None,
    )
    if retry_worker_count <= 1:
        retry_job_results = [_run_retry_job(job) for job in retry_jobs]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=retry_worker_count) as executor:
            retry_job_results = list(executor.map(_run_retry_job, retry_jobs))
    for retry_result in retry_job_results:
        retry_results_by_page_id[str(retry_result["pageId"])] = retry_result

    for page_id in page_ids:
        page = _find_page(session, page_id)
        retry_result = retry_results_by_page_id.get(page_id) or {
            "pageId": page_id,
            "status": "failed",
            "error": "retry did not produce a result",
        }
        if retry_result.get("status") == "missing_source":
            page["reviewStatus"] = "failed"
            flags = list(page.get("riskFlags") or [])
            flags.append("ai_retry_missing_source")
            page["riskFlags"] = list(dict.fromkeys(str(flag) for flag in flags if flag))
            page["aiRetry"] = {
                "status": "missing_source",
                "error": str(retry_result.get("error") or f"source page image missing for retry: {page_id}"),
                "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            summaries.append({
                "pageId": page_id,
                "status": "missing_source",
                "error": str(retry_result.get("error") or f"source page image missing for retry: {page_id}"),
            })
            continue
        if retry_result.get("status") != "ok":
            page["reviewStatus"] = "failed"
            flags = list(page.get("riskFlags") or [])
            flags.append("ai_retry_failed")
            page["riskFlags"] = list(dict.fromkeys(str(flag) for flag in flags if flag))
            page["aiRetry"] = {
                "status": "failed",
                "error": str(retry_result.get("error") or "AI retry failed"),
                "attemptedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            summaries.append({
                "pageId": page_id,
                "status": "failed",
                "error": str(retry_result.get("error") or "AI retry failed"),
            })
            continue

        result = retry_result.get("result") or {}
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
    _refresh_session_problem_counts(session)
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
        for key in ("imagePath", "sourceImagePath", "boardRenderPath", "originalImagePath"):
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
        remember_session_history(session)


class AppRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    @property
    def app_server(self) -> AppHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args) -> None:
        print(f"[app-server] {self.address_string()} - {format % args}")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "app": APP_NAME})
            return
        if parsed.path == "/api/runtime-diagnostics":
            self._send_json(describe_runtime_diagnostics())
            return
        if parsed.path == "/api/session/latest":
            self._handle_latest_session()
            return
        if parsed.path == "/api/session/history":
            self._handle_session_history()
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
        if parsed.path == "/api/session/classin-review":
            self._handle_session_classin_review()
            return
        if parsed.path == "/api/system/open-folder":
            self._handle_open_folder()
            return
        if parsed.path == "/api/system/open-file":
            self._handle_open_file()
            return
        if parsed.path == "/api/session/mutate":
            self._handle_session_mutate()
            return
        if parsed.path == "/api/session/retry-ai":
            self._handle_session_retry_ai()
            return
        if parsed.path == "/api/session/enhance-image":
            self._handle_session_enhance_image()
            return
        if parsed.path == "/api/session/restore":
            self._handle_session_restore()
            return
        if parsed.path == "/api/session/history/restore":
            self._handle_session_history_restore()
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

        payload_session = payload.get("session")
        if isinstance(payload_session, dict) and isinstance(payload_session.get("problems"), list):
            session = _denormalize_session_paths(payload_session)

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
                problem_copy["placementScaleRatio"] = max(1.0, scale_ratio)
            sequence_with_placements.append(problem_copy)
        sequence = sequence_with_placements

        publish_preflight, duplicate_groups = _session_publish_blocking_preflight(sequence, session=session)
        if not publish_preflight.get("passed"):
            self._send_json(
                _session_publish_preflight_blocked_payload(publish_preflight, duplicate_groups),
                status=HTTPStatus.CONFLICT,
            )
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
        # Resize the logical canvas to match the actual problem count after the
        # user may have excluded items in the review UI. Mirrors the formula in
        # run_problem_export so mvp_board.edb and the published EDB agree.
        template.board_page_count = max(50, len(entries) * 2)
        output_dir = Path(session.get("output_dir") or RUNTIME_DIR / "publish_output").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        crop_format = _normalize_crop_format(session.get("crop_format"))

        try:
            records, placements, header_flag = build_records(
                entries,
                template,
                record_mode="image-only",
                output_dir=output_dir,
                text_confidence_threshold=0.78,
                dark_board=True,
                board_theme=session.get("board_theme") or DEFAULT_BOARD_THEME,
                crop_format=crop_format,
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
                    version=version_string_for_crop_format(crop_format),
                    page_count_hint=template.board_page_count,
                ),
            )
            edb_validation = validate_edb_file(edb_path, expected_min_records=len(records))
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": f"failed to write edb: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        core_problem_count = sum(1 for problem in sequence if not _session_problem_is_supplemental(problem))
        supplemental_item_count = sum(1 for problem in sequence if _session_problem_is_supplemental(problem))

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
            board_theme=session.get("board_theme") or DEFAULT_BOARD_THEME,
            crop_format=crop_format,
        )
        # carry over user-facing labels so the rename doesn't get lost
        if session.get("session_name"):
            new_session["session_name"] = session["session_name"]
        new_session["crop_format"] = crop_format
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
            _copy_publish_problem_metadata(problem, prior)
            if "bbox" not in problem or not problem["bbox"]:
                problem["bbox"] = prior.get("bbox") or {}
            problem["riskFlags"] = [
                str(flag)
                for flag in (prior.get("riskFlags") or [])
                if str(flag) != "fallback_grouping"
            ]
            problem["reviewStatus"] = "check_needed" if problem["riskFlags"] else "normal"
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
        _refresh_session_problem_counts(new_session)

        source_paths_for_handoff = [
            path
            for path in (_file_uri_to_path(value) for value in (session.get("input_files") or session.get("inputFiles") or []))
            if path is not None
        ]
        try:
            classin_handoff_path, classin_handoff_markdown_path = write_classin_handoff_manifest(
                output_dir,
                source_paths=source_paths_for_handoff,
                edb_path=edb_path,
                ui_session=new_session,
                summary={
                    "record_count": len(records),
                    "record_mode": "image-only",
                    "crop_format": crop_format,
                    "board_theme": session.get("board_theme") or DEFAULT_BOARD_THEME,
                    "placements": placements,
                },
                template=template,
            )
        except Exception as exc:  # noqa: BLE001 — keep publish failures explicit for the UI.
            self._send_json({"ok": False, "error": f"failed to write ClassIn handoff: {exc}"},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        classin_preflight: dict[str, Any] = {}
        handoff_payload: dict[str, Any] = {}
        try:
            raw_handoff_payload = json.loads(classin_handoff_path.read_text(encoding="utf-8"))
            handoff_payload = raw_handoff_payload if isinstance(raw_handoff_payload, dict) else {}
            if isinstance(handoff_payload.get("classinPreflight"), dict):
                classin_preflight = dict(handoff_payload["classinPreflight"])
        except (OSError, json.JSONDecodeError):
            classin_preflight = {}

        new_session["classin_handoff_path"] = str(classin_handoff_path)
        new_session["classinHandoffPath"] = str(classin_handoff_path)
        new_session["classin_handoff_markdown_path"] = str(classin_handoff_markdown_path)
        new_session["classinHandoffMarkdownPath"] = str(classin_handoff_markdown_path)
        new_session["classin_preflight"] = classin_preflight
        new_session["classinPreflight"] = classin_preflight
        if isinstance(handoff_payload.get("passageReviewItems"), list):
            new_session["passageReviewItems"] = [
                dict(item)
                for item in handoff_payload.get("passageReviewItems", [])
                if isinstance(item, dict)
            ]
            new_session["passage_review_items"] = new_session["passageReviewItems"]
            new_session["passageReviewItemCount"] = (
                int(handoff_payload.get("passageReviewItemCount"))
                if isinstance(handoff_payload.get("passageReviewItemCount"), (int, float, str))
                and str(handoff_payload.get("passageReviewItemCount")).isdigit()
                else len(new_session["passageReviewItems"])
            )
            new_session["passage_review_item_count"] = new_session["passageReviewItemCount"]
            new_session["crossPagePassageReviewItemCount"] = (
                int(handoff_payload.get("crossPagePassageReviewItemCount"))
                if isinstance(handoff_payload.get("crossPagePassageReviewItemCount"), (int, float, str))
                and str(handoff_payload.get("crossPagePassageReviewItemCount")).isdigit()
                else sum(
                    1
                    for item in new_session["passageReviewItems"]
                    if item.get("continuesAcrossPages") or item.get("continues_across_pages")
                )
            )
            new_session["cross_page_passage_review_item_count"] = new_session["crossPagePassageReviewItemCount"]
            publish_problems = [
                problem
                for problem in (new_session.get("problems") or [])
                if isinstance(problem, dict)
            ]
            publish_pages = [
                page
                for page in (new_session.get("pages") or [])
                if isinstance(page, dict)
            ]
            _normalize_session_passage_review_queue(
                new_session,
                unresolved_problem_ids=_session_unresolved_review_problem_ids(
                    problems=publish_problems,
                    pages=publish_pages,
                    actionable_flags=set((new_session.get("reviewSummary") or {}).get("actionableRiskFlagCounts") or {}),
                ),
            )

        passage_group_source_reuse_groups = None
        for source in (handoff_payload, new_session):
            for key in ("passageGroupSourceReuseGroups", "passage_group_source_reuse_groups"):
                value = source.get(key)
                if isinstance(value, list):
                    passage_group_source_reuse_groups = value
                    break
            if passage_group_source_reuse_groups is not None:
                break
        passage_group_source_reuse_group_count = None
        for source in (handoff_payload, new_session):
            for key in ("passageGroupSourceReuseGroupCount", "passage_group_source_reuse_group_count"):
                value = source.get(key)
                if isinstance(value, (int, float, str)) and str(value).isdigit():
                    passage_group_source_reuse_group_count = int(value)
                    break
            if passage_group_source_reuse_group_count is not None:
                break

        publish_summary = _session_publish_summary(
            edb_path=edb_path,
            output_dir=output_dir,
            edb_validation=edb_validation,
            record_count=len(records),
            core_problem_count=core_problem_count,
            supplemental_item_count=supplemental_item_count,
            classin_handoff_path=classin_handoff_path,
            classin_handoff_markdown_path=classin_handoff_markdown_path,
            classin_preflight=classin_preflight,
            passage_groups=(
                handoff_payload.get("passageGroups")
                if isinstance(handoff_payload.get("passageGroups"), list)
                else None
            ),
            passage_group_count=(
                int(handoff_payload.get("passageGroupCount"))
                if isinstance(handoff_payload.get("passageGroupCount"), (int, float, str))
                and str(handoff_payload.get("passageGroupCount")).isdigit()
                else None
            ),
            passage_problem_count=(
                int(handoff_payload.get("passageProblemCount"))
                if isinstance(handoff_payload.get("passageProblemCount"), (int, float, str))
                and str(handoff_payload.get("passageProblemCount")).isdigit()
                else None
            ),
            cross_page_passage_group_count=(
                int(handoff_payload.get("crossPagePassageGroupCount"))
                if isinstance(handoff_payload.get("crossPagePassageGroupCount"), (int, float, str))
                and str(handoff_payload.get("crossPagePassageGroupCount")).isdigit()
                else None
            ),
            passage_review_items=(
                new_session.get("passageReviewItems")
                if isinstance(new_session.get("passageReviewItems"), list)
                else None
            ),
            passage_review_item_count=(
                int(new_session.get("passageReviewItemCount"))
                if isinstance(new_session.get("passageReviewItemCount"), (int, float, str))
                and str(new_session.get("passageReviewItemCount")).isdigit()
                else None
            ),
            cross_page_passage_review_item_count=(
                int(new_session.get("crossPagePassageReviewItemCount"))
                if isinstance(new_session.get("crossPagePassageReviewItemCount"), (int, float, str))
                and str(new_session.get("crossPagePassageReviewItemCount")).isdigit()
                else None
            ),
            passage_group_source_reuse_groups=passage_group_source_reuse_groups,
            passage_group_source_reuse_group_count=passage_group_source_reuse_group_count,
        )
        publish_history = _session_publish_history(session, publish_summary)
        new_session["publish_summary"] = publish_summary
        new_session["publishSummary"] = publish_summary
        new_session["publish_history"] = publish_history
        new_session["publishHistory"] = publish_history
        self.app_server.remember_session(new_session)
        self._send_json({
            "ok": True,
            "session": rewrite_session_for_http(new_session),
            "edbValidation": edb_validation,
            "edb_validation": edb_validation,
            "publishSummary": publish_summary,
            "publish_summary": publish_summary,
            "publishHistory": publish_history,
            "publish_history": publish_history,
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
                ids_raw = payload.get("problemIds", payload.get("problem_ids"))
                if ids_raw is not None:
                    new_session = _mutate_exclude_many(session, ids_raw)
                else:
                    problem_id = str(payload.get("problemId") or payload.get("problem_id") or "")
                    new_session = _mutate_exclude(session, problem_id)
            elif action in {"retry-ai", "retry_ai"}:
                new_session = _mutate_retry_ai(session, payload)
            elif action in {"enhance-image", "enhance_image"}:
                new_session = _mutate_enhance_image(session, payload)
            else:
                self._send_json(
                    {"ok": False, "error": f"unknown action: {action!r} (expected split|merge|exclude|retry-ai|enhance-image)"},
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
            preview_only = _coerce_bool(
                payload.get("preview")
                if "preview" in payload
                else payload.get("previewOnly")
                if "previewOnly" in payload
                else payload.get("dryRun"),
                default=False,
            )
            working_session = json.loads(json.dumps(session)) if preview_only else session
            new_session = _mutate_retry_ai(working_session, payload)
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.CONFLICT)
            return

        if preview_only:
            self.app_server.allowed_files |= collect_session_file_paths(new_session)
        else:
            self.app_server.remember_session(new_session)
        self._send_json({
            "ok": True,
            "session": rewrite_session_for_http(new_session),
            "retry": new_session.get("ai_retry_summary") or [],
            "preview": preview_only,
        })

    def _handle_session_classin_review(self) -> None:
        session = self.app_server.latest_session or load_latest_session()
        if session is None:
            self._send_json({"ok": False, "error": "no session available"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        review = _apply_classin_review_result(session, payload)
        self.app_server.remember_session(session)
        self._send_json({
            "ok": True,
            "session": rewrite_session_for_http(session),
            "review": review,
            "classinReview": review,
            "classin_review": review,
            "history": _public_session_history(load_session_history()),
        })

    def _handle_session_enhance_image(self) -> None:
        session = self.app_server.latest_session or load_latest_session()
        if session is None:
            self._send_json({"ok": False, "error": "no session available"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body()
            new_session = _mutate_enhance_image(session, payload)
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
            "enhance": new_session.get("ai_image_reconstruction_summary") or [],
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
        _refresh_session_problem_counts(restored)
        self.app_server.remember_session(restored)
        self._send_json({"ok": True, "session": rewrite_session_for_http(restored)})

    def _handle_session_history(self) -> None:
        self._send_json({
            "ok": True,
            "history": _public_session_history(load_session_history()),
        })

    def _handle_session_history_restore(self) -> None:
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        session_id = str(payload.get("id") or payload.get("sessionId") or payload.get("session_id") or "").strip()
        if not session_id:
            self._send_json({"ok": False, "error": "session history id is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        entry = next((item for item in load_session_history() if str(item.get("id")) == session_id), None)
        if not isinstance(entry, dict) or not isinstance(entry.get("session"), dict):
            self._send_json({"ok": False, "error": "session history entry not found"}, status=HTTPStatus.NOT_FOUND)
            return
        restored = _denormalize_session_paths(entry["session"])
        _refresh_session_problem_counts(restored)
        self.app_server.remember_session(restored)
        self._send_json({
            "ok": True,
            "session": rewrite_session_for_http(restored),
            "history": _public_session_history(load_session_history()),
        })

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
        try:
            target = _resolve_open_target(raw_path, kind="folder")
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        except ValueError as exc:
            status = HTTPStatus.FORBIDDEN if "outside allowed roots" in str(exc) else HTTPStatus.BAD_REQUEST
            self._send_json({"ok": False, "error": str(exc)}, status=status)
            return
        try:
            _open_system_target(target)
        except OSError as exc:
            self._send_json({"ok": False, "error": f"failed to open: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"ok": True, "path": str(target)})

    def _handle_open_file(self) -> None:
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return
        raw_path = payload.get("path") or payload.get("file") or ""
        try:
            target = _resolve_open_target(raw_path, kind="file")
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        except ValueError as exc:
            status = HTTPStatus.FORBIDDEN if "outside allowed roots" in str(exc) else HTTPStatus.BAD_REQUEST
            self._send_json({"ok": False, "error": str(exc)}, status=status)
            return
        try:
            _open_system_target(target)
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
        raw_key = payload.get("geminiApiKey") if "geminiApiKey" in payload else payload.get("gemini_api_key")
        raw_openai_key = payload.get("openAiApiKey") if "openAiApiKey" in payload else payload.get("openai_api_key")
        try:
            if raw_openai_key is None:
                summary = update_gemini_api_key(RUNTIME_DIR, raw_key if isinstance(raw_key, str) else "")
            else:
                summary = update_api_keys(
                    RUNTIME_DIR,
                    gemini_api_key=raw_key if isinstance(raw_key, str) else None,
                    openai_api_key=raw_openai_key if isinstance(raw_openai_key, str) else "",
                )
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
        _refresh_session_problem_counts(session)
        self.app_server.latest_session = session
        self.app_server.allowed_files |= collect_session_file_paths(session)
        if not LATEST_SESSION_JSON.exists():
            LATEST_SESSION_JSON.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        remember_session_history(session)
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
            self.send_header("Content-Disposition", content_disposition_attachment(path.name))
        self.end_headers()
        self.wfile.write(data)

    def _save_uploaded_file(self, payload: dict[str, Any]) -> Path:
        file_name = payload.get("fileName") or "upload.bin"
        file_data_base64 = payload.get("fileDataBase64")
        if not file_data_base64:
            raise ValueError("fileDataBase64 is required when sourcePath is not provided")
        safe_name = sanitize_upload_file_name(file_name)
        file_bytes = base64.b64decode(file_data_base64)
        content_digest = hashlib.sha1(file_bytes).hexdigest()
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(safe_name).suffix
        for candidate in sorted(UPLOAD_DIR.glob(f"{content_digest}_*{suffix}")):
            try:
                if candidate.read_bytes() == file_bytes:
                    return candidate
            except OSError:
                continue
        target_path = UPLOAD_DIR / f"{content_digest}_{safe_name}"
        if not target_path.exists() or target_path.read_bytes() != file_bytes:
            target_path.write_bytes(file_bytes)
        return target_path

    def _resolve_source_paths(self, payload: dict[str, Any]) -> list[Path]:
        file_payloads = payload.get("files")
        if isinstance(file_payloads, list) and file_payloads:
            resolved_paths: list[Path] = []
            for file_payload in file_payloads:
                if isinstance(file_payload, dict):
                    resolved_paths.append(self._save_uploaded_file(file_payload).resolve())
                    continue
                path = decode_file_reference(str(file_payload))
                if path is None:
                    raise FileNotFoundError(f"sourcePath does not exist: {file_payload}")
                if not path.exists():
                    raise FileNotFoundError(f"sourcePath does not exist: {path}")
                resolved_paths.append(path.resolve())
            return resolved_paths

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
            preview_only = _coerce_bool(
                payload.get("preview")
                if "preview" in payload
                else payload.get("previewOnly")
                if "previewOnly" in payload
                else payload.get("dryRun"),
                default=False,
            )
            export_mode = str(payload.get("exportMode") or payload.get("export_mode") or payload.get("layoutMode") or "question").lower()
            input_intent = _extract_input_intent(payload)
            input_notes = _extract_input_notes(payload)
            crop_format = _extract_crop_format(payload)
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
                    crop_format=crop_format,
                    input_intent=input_intent,
                    input_notes=input_notes,
                    **common_kwargs,
                )
        except Exception as exc:
            self._send_json(_export_error_payload(exc), status=HTTPStatus.BAD_REQUEST)
            return

        session = result["ui_session"]
        session.setdefault("input_intent", input_intent)
        if input_notes:
            session["input_notes"] = input_notes
        _refresh_session_problem_counts(session)
        edb_validation = None
        edb_path = result.get("edb_path")
        if edb_path:
            try:
                expected_records = len((result.get("summary") or {}).get("placements") or [])
                edb_validation = validate_edb_file(edb_path, expected_min_records=max(1, expected_records))
            except Exception as exc:
                self._send_json({"ok": False, "error": f"EDB validation failed: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
        if preview_only:
            self.app_server.allowed_files |= collect_session_file_paths(session)
        else:
            self.app_server.remember_session(session)
        classin_preflight = session.get("classinPreflight")
        if not isinstance(classin_preflight, dict):
            classin_preflight = session.get("classin_preflight")
        if not isinstance(classin_preflight, dict):
            classin_preflight = {}
        classin_preflight_issue_count = int(
            classin_preflight.get("issueCount") or classin_preflight.get("issue_count") or 0
        )
        classin_preflight_passed = bool(classin_preflight.get("passed")) if classin_preflight else False
        classin_preflight_status = str(classin_preflight.get("status") or "")
        self._send_json(
            {
                "ok": True,
                "session": rewrite_session_for_http(session),
                "preview": preview_only,
                "output_dir": str(result["output_dir"]),
                "outputDir": str(result["output_dir"]),
                "ui_session_path": str(result["ui_session_path"]),
                "uiSessionPath": str(result["ui_session_path"]),
                "edb_path": str(result["edb_path"]) if result["edb_path"] else None,
                "edbPath": str(result["edb_path"]) if result["edb_path"] else None,
                "edb_validation": edb_validation,
                "edbValidation": edb_validation,
                "classin_preflight": classin_preflight,
                "classinPreflight": classin_preflight,
                "classin_preflight_status": classin_preflight_status,
                "classinPreflightStatus": classin_preflight_status,
                "classin_preflight_passed": classin_preflight_passed,
                "classinPreflightPassed": classin_preflight_passed,
                "classin_preflight_issue_count": classin_preflight_issue_count,
                "classinPreflightIssueCount": classin_preflight_issue_count,
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
