from __future__ import annotations

import os
from pathlib import Path

import pytest

import user_settings


@pytest.mark.parametrize("payload", [b'["settings"]', b'"settings"', b"42", b"\xff"])
def test_invalid_settings_file_does_not_break_startup(tmp_path: Path, payload: bytes) -> None:
    user_settings.settings_path(tmp_path).write_bytes(payload)

    assert user_settings.load_user_settings(tmp_path) == {}
    user_settings.apply_to_env(user_settings.load_user_settings(tmp_path))
    assert user_settings.summarize_for_response(tmp_path)["aiEnabled"] is True


@pytest.mark.parametrize("new_key", ["replacement-test-key", ""])
def test_failed_settings_save_keeps_active_key(tmp_path: Path, monkeypatch, new_key: str) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "previous-test-key")
    user_settings.save_user_settings(tmp_path, {"gemini_api_key": "previous-test-key"})

    def fail_save(*_args, **_kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(user_settings, "save_user_settings", fail_save)
    with pytest.raises(OSError, match="disk full"):
        user_settings.update_api_keys(tmp_path, gemini_api_key=new_key)

    assert os.environ["GEMINI_API_KEY"] == "previous-test-key"
    assert user_settings.load_user_settings(tmp_path)["gemini_api_key"] == "previous-test-key"


def test_failed_atomic_replace_preserves_previous_settings(tmp_path: Path, monkeypatch) -> None:
    previous = {"gemini_api_key": "previous-test-key", "ai_enabled": False}
    user_settings.save_user_settings(tmp_path, previous)

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        user_settings.save_user_settings(tmp_path, {"gemini_api_key": "replacement-test-key"})

    assert user_settings.load_user_settings(tmp_path) == previous
    assert list(tmp_path.iterdir()) == [user_settings.settings_path(tmp_path)]
