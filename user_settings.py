#!/usr/bin/env python3
"""User-editable settings persisted under .app_runtime/user_settings.json.

The local app needs a place to keep secrets (currently the Gemini API key)
between runs without putting them in source control. Settings are written as
JSON next to the runtime cache so they are covered by the existing
``.app_runtime/`` gitignore entry.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

_SETTINGS_FILENAME = "user_settings.json"


def settings_path(runtime_dir: Path) -> Path:
    return runtime_dir / _SETTINGS_FILENAME


def load_user_settings(runtime_dir: Path) -> dict[str, Any]:
    path = settings_path(runtime_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_user_settings(runtime_dir: Path, settings: dict[str, Any]) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = settings_path(runtime_dir)
    payload = json.dumps(settings, ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")
    _restrict_permissions(path)
    return path


def apply_to_env(settings: dict[str, Any], *, overwrite: bool = False) -> dict[str, str]:
    """Promote relevant settings to ``os.environ`` so downstream pipeline code
    that reads env vars (Gemini OCR, page repair) picks them up.

    By default an externally-set env var wins so users who exported
    ``GEMINI_API_KEY`` in their shell are not silently overridden.
    """
    applied: dict[str, str] = {}
    key = str(settings.get("gemini_api_key") or "").strip()
    if key:
        if overwrite or not os.environ.get("GEMINI_API_KEY", "").strip():
            os.environ["GEMINI_API_KEY"] = key
            applied["GEMINI_API_KEY"] = "user_settings"
        else:
            applied["GEMINI_API_KEY"] = "env"
    elif os.environ.get("GEMINI_API_KEY", "").strip():
        applied["GEMINI_API_KEY"] = "env"
    return applied


def mask_key(value: str | None) -> str:
    """Return a short preview (e.g. ``AIza…wxyz``) for display."""
    if not value:
        return ""
    text = str(value).strip()
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}…{text[-4:]}"


def summarize_for_response(
    runtime_dir: Path,
    *,
    env_overwrites: bool = False,
) -> dict[str, Any]:
    """Build a sanitized snapshot of settings for the HTTP API.

    Never returns the raw API key — only a masked preview and a flag for
    whether one is currently active. ``source`` indicates where the active
    value comes from: ``env`` (shell-exported) or ``user_settings`` (saved
    via the UI).
    """
    settings = load_user_settings(runtime_dir)
    stored_key = str(settings.get("gemini_api_key") or "").strip()
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()

    active_key = env_key if (env_key and not env_overwrites) else stored_key or env_key
    if not active_key:
        source = "none"
    elif stored_key and (active_key == stored_key or env_overwrites):
        # If the user saved a key via the UI, treat it as the canonical
        # source even when it has been promoted into os.environ.
        source = "user_settings"
    elif env_key:
        source = "env"
    else:
        source = "user_settings"

    return {
        "geminiApiKey": "",  # never echoed
        "geminiApiKeyPreview": mask_key(active_key),
        "hasGeminiApiKey": bool(active_key),
        "geminiApiKeySource": source,
        "geminiApiKeyStoredPreview": mask_key(stored_key),
        "hasStoredGeminiApiKey": bool(stored_key),
    }


def update_gemini_api_key(runtime_dir: Path, raw_key: str | None) -> dict[str, Any]:
    """Persist (or clear) the Gemini API key and apply it to ``os.environ``.

    Returns the same payload shape as :func:`summarize_for_response` so the
    UI can refresh its status without an extra fetch.
    """
    key = (raw_key or "").strip()
    settings = load_user_settings(runtime_dir)
    if key:
        settings["gemini_api_key"] = key
    else:
        settings.pop("gemini_api_key", None)
    save_user_settings(runtime_dir, settings)

    if key:
        os.environ["GEMINI_API_KEY"] = key
    else:
        os.environ.pop("GEMINI_API_KEY", None)

    return summarize_for_response(runtime_dir, env_overwrites=True)


def _restrict_permissions(path: Path) -> None:
    # On POSIX, mark the file 0600 so it isn't world-readable. No-op on
    # Windows where filesystem ACLs already restrict to the current user.
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
