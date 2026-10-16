from __future__ import annotations

from pathlib import Path

import ocr_backend
import app_server
from user_settings import (
    ai_enabled_from_settings,
    load_user_settings,
    summarize_for_response,
    update_api_keys,
)


def test_ai_enabled_defaults_on_for_existing_installations() -> None:
    assert ai_enabled_from_settings({}) is True
    assert ai_enabled_from_settings({"ai_enabled": False}) is False
    assert ai_enabled_from_settings({"ai_enabled": "off"}) is False


def test_ai_toggle_persists_without_clearing_saved_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    update_api_keys(tmp_path, gemini_api_key="saved-gemini-key")

    off_summary = update_api_keys(tmp_path, ai_enabled=False)
    stored = load_user_settings(tmp_path)

    assert stored["gemini_api_key"] == "saved-gemini-key"
    assert stored["ai_enabled"] is False
    assert off_summary["aiEnabled"] is False
    assert off_summary["hasGeminiApiKey"] is True
    assert summarize_for_response(tmp_path)["geminiApiKey"] == ""


def test_local_ocr_mode_never_falls_through_to_cloud(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "configured-cloud-key")
    monkeypatch.setattr(ocr_backend, "PaddleOCR", None)
    monkeypatch.setattr(ocr_backend, "_tesseract_binary_available", lambda **_kwargs: False)

    assert ocr_backend.preferred_ocr_backend_name("local") == "none"
    assert isinstance(ocr_backend.build_ocr_backend("local"), ocr_backend.NoOcrBackend)


def test_economy_ocr_profile_resolves_to_flash_lite(
    monkeypatch,
) -> None:
    monkeypatch.delenv(ocr_backend.GEMINI_OCR_MODEL_ENV, raising=False)
    monkeypatch.setenv(ocr_backend.GEMINI_OCR_PROFILE_ENV, "economy")

    model = ocr_backend.resolve_gemini_ocr_model()

    assert model == "gemini-3.5-flash-lite"
    assert ocr_backend.resolve_gemini_ocr_thinking_level(model) == "minimal"


def test_server_blocks_ai_mutations_when_global_switch_is_off(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(app_server, "RUNTIME_DIR", tmp_path)
    update_api_keys(tmp_path, ai_enabled=False)

    try:
        app_server._mutate_retry_ai({"pages": [], "problems": []}, {})
    except ValueError as exc:
        assert "AI 기능이 꺼져" in str(exc)
    else:
        raise AssertionError("AI retry should be rejected while global AI is off")

    try:
        app_server._mutate_enhance_image(
            {"pages": [], "problems": []},
            {"problemIds": ["problem-1"], "mode": "ai"},
        )
    except ValueError as exc:
        assert "AI 기능이 꺼져" in str(exc)
    else:
        raise AssertionError("AI image generation should be rejected while global AI is off")
