#!/usr/bin/env python3
"""Privacy-preserving bug report construction and delivery.

Reports intentionally exclude source documents, session data, API keys, and
full local paths.  The UI supplies a small allowlisted context object; this
module adds bounded, redacted runtime diagnostics before forwarding it to the
remote report collector.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BUG_REPORT_URL = "https://reports.classin.cloud/v1/edb-reports"
MAX_DESCRIPTION_CHARS = 4_000
MAX_LOG_CHARS = 24_000
MAX_REMOTE_RESPONSE_BYTES = 64_000
MAX_RUNTIME_ERRORS = 10
REPORT_TIMEOUT_SECONDS = 8.0

_SECRET_PATTERNS = (
    re.compile(r"\bAIza[0-9A-Za-z_-]{16,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z_-]{16,}\b"),
    re.compile(
        r"(?i)\b(api[_ -]?key|authorization|bearer|password|secret|token)"
        r"(\s*[:=]\s*)([^\s,;\"']+)"
    ),
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_POSIX_LOCAL_PATH_PATTERN = re.compile(
    r"(?<!https:)(?<!http:)(?:file://)?"
    r"(?:/Users/|/home/|/private/var/|/var/folders/|/tmp/)"
    r"[^\s\"'<>]+"
)
_WINDOWS_LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:\\Users\\|[A-Z]:\\Temp\\)[^\s\"'<>]+"
)
_DOCUMENT_NAME_PATTERN = re.compile(
    r"(?i)(?<![/\\])\b[^\s\"'<>/\\]+"
    r"\.(?:pdf|hwp|hwpx|png|jpe?g|webp|bmp|tiff?)\b"
)


class BugReportValidationError(ValueError):
    """Raised when a local report request is not safe or useful."""


class BugReportDeliveryError(RuntimeError):
    """Raised when the remote report collector cannot accept a report."""


def redact_sensitive_text(value: Any) -> str:
    """Return bounded text with common secrets and local identifiers removed."""

    text = str(value or "")
    for pattern in _SECRET_PATTERNS[:2]:
        text = pattern.sub("[redacted-secret]", text)
    text = _SECRET_PATTERNS[2].sub(
        lambda match: f"{match.group(1)}{match.group(2)}[redacted-secret]",
        text,
    )
    text = _EMAIL_PATTERN.sub("[redacted-email]", text)
    text = _POSIX_LOCAL_PATH_PATTERN.sub("[local-path]", text)
    text = _WINDOWS_LOCAL_PATH_PATTERN.sub("[local-path]", text)
    text = _DOCUMENT_NAME_PATTERN.sub("[document]", text)
    home = str(Path.home())
    if home:
        text = text.replace(home, "[home]")
    return text


def _bounded_text(value: Any, limit: int) -> str:
    return redact_sensitive_text(value).strip()[:limit]


def _safe_runtime_errors(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    safe: list[dict[str, Any]] = []
    for raw in value[:MAX_RUNTIME_ERRORS]:
        if not isinstance(raw, dict):
            continue
        entry = {
            "type": _bounded_text(raw.get("type"), 80) or "runtime",
            "message": _bounded_text(raw.get("message"), 1_500),
        }
        for field, limit in (
            ("filename", 240),
            ("componentStack", 4_000),
        ):
            text = _bounded_text(raw.get(field), limit)
            if text:
                entry[field] = text
        for field in ("lineno", "colno"):
            try:
                number = int(raw.get(field))
            except (TypeError, ValueError):
                continue
            if 0 <= number <= 10_000_000:
                entry[field] = number
        safe.append(entry)
    return safe


def _safe_context(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    safe: dict[str, Any] = {}
    for field in ("view", "settingsTab", "inputIntent", "reviewStatus"):
        text = _bounded_text(raw.get(field), 80)
        if text:
            safe[field] = text
    for field in ("itemCount", "pendingCount"):
        try:
            count = int(raw.get(field))
        except (TypeError, ValueError):
            continue
        safe[field] = max(0, min(count, 100_000))
    hangul = raw.get("hangul")
    if isinstance(hangul, dict):
        safe["hangul"] = {
            "status": _bounded_text(hangul.get("status"), 40),
            "summary": _bounded_text(hangul.get("summary"), 240),
        }
    errors = _safe_runtime_errors(raw.get("runtimeErrors"))
    if errors:
        safe["runtimeErrors"] = errors
    return safe


def _read_log_tail(log_file: Path | None) -> str:
    if log_file is None or not log_file.is_file():
        return ""
    try:
        with log_file.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - (MAX_LOG_CHARS * 4)), os.SEEK_SET)
            tail = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return redact_sensitive_text(tail)[-MAX_LOG_CHARS:]


def build_bug_report(
    request_payload: dict[str, Any],
    *,
    app_config: dict[str, Any],
    log_file: Path | None = None,
) -> dict[str, Any]:
    """Build the remote collector payload from an untrusted local request."""

    description = _bounded_text(request_payload.get("description"), MAX_DESCRIPTION_CHARS)
    if len(description) < 5:
        raise BugReportValidationError("무슨 문제가 있었는지 5자 이상 적어 주세요.")
    include_diagnostics = bool(request_payload.get("includeDiagnostics", True))
    context = _safe_context(request_payload.get("context"))
    app_id = _bounded_text(app_config.get("appId"), 80) or "ClassInEDBMVP"
    version = _bounded_text(app_config.get("version"), 80) or "unknown"
    app_platform = _bounded_text(app_config.get("platform"), 40) or sys.platform

    report: dict[str, Any] = {
        "schemaVersion": 1,
        "submittedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "category": "bug",
        "description": description,
        "app": {
            "id": app_id,
            "version": version,
            "platform": app_platform,
        },
        "context": context,
    }
    if include_diagnostics:
        diagnostics: dict[str, Any] = {
            "system": _bounded_text(platform.system(), 80),
            "systemRelease": _bounded_text(platform.release(), 160),
            "pythonVersion": _bounded_text(platform.python_version(), 40),
        }
        log_tail = _read_log_tail(log_file)
        if log_tail:
            diagnostics["logTail"] = log_tail
        report["diagnostics"] = diagnostics
    return report


def deliver_bug_report(
    report: dict[str, Any],
    *,
    endpoint: str = DEFAULT_BUG_REPORT_URL,
    timeout: float = REPORT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """POST a sanitized report to the remote collector and return its receipt."""

    endpoint = str(endpoint or "").strip()
    if not endpoint.startswith("https://"):
        raise BugReportDeliveryError("버그 리포트 수신 주소가 안전한 HTTPS 주소가 아닙니다.")
    body = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": (
                f"{report.get('app', {}).get('id', 'ClassInEDBMVP')}/"
                f"{report.get('app', {}).get('version', 'unknown')}"
            ),
            "X-EDB-Report-Schema": "1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_REMOTE_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise BugReportDeliveryError(f"수신 서버가 신고를 받지 못했습니다. ({exc.code})") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BugReportDeliveryError("수신 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc
    if len(raw) > MAX_REMOTE_RESPONSE_BYTES:
        raise BugReportDeliveryError("수신 서버 응답이 너무 큽니다.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BugReportDeliveryError("수신 서버 응답을 확인할 수 없습니다.") from exc
    if not isinstance(payload, dict) or not payload.get("ok") or not payload.get("reportId"):
        raise BugReportDeliveryError("수신 서버가 접수 번호를 반환하지 않았습니다.")
    return {
        "ok": True,
        "reportId": _bounded_text(payload.get("reportId"), 100),
        "receivedAt": _bounded_text(payload.get("receivedAt"), 100),
    }
