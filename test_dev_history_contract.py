"""One lesson-history read contract for writers (dev/srv-jobs) and cleanup (dev/srv-storage).

Locked file: writers leave it as it is, cleanup deletes nothing.
Corrupt file: writers replace it with a fresh list, cleanup still deletes nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import app_server


@pytest.fixture
def server(tmp_path):
    with (
        patch.object(app_server, "RUNTIME_DIR", tmp_path),
        patch.object(app_server, "LATEST_SESSION_JSON", tmp_path / "latest_session.json"),
        patch.object(app_server, "SESSION_HISTORY_JSON", tmp_path / "session_history.json"),
        patch.object(app_server, "GENERATED_SESSION_JS", tmp_path / "generated_session.js"),
    ):
        srv = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
        srv.latest_session = {"problems": [], "pages": [], "session_name": "수업"}
        srv._session_revision = 1
        try:
            yield srv
        finally:
            srv.server_close()


@pytest.mark.parametrize("content", ["{not json", '{"a": 1}', b"\xff\xfe broken"])
def test_corrupt_history_is_corrupt_for_strict_reads_and_empty_for_lenient_ones(server, tmp_path, content):
    history = tmp_path / "session_history.json"
    history.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    with pytest.raises(app_server.SessionHistoryCorrupt) as caught:
        app_server.load_session_history(strict=True)
    assert isinstance(caught.value, app_server.SessionHistoryUnreadable)
    assert isinstance(caught.value, OSError), "writers catch OSError to keep an unreadable file"
    assert app_server.load_session_history() == []


def test_locked_history_is_unreadable_but_not_corrupt(server, tmp_path, monkeypatch):
    history = tmp_path / "session_history.json"
    history.write_text("[]", encoding="utf-8")
    real_read_text = Path.read_text

    def locked(self, *args, **kwargs):
        if self == history:
            raise PermissionError(13, "in use")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(app_server.SessionHistoryUnreadable) as caught:
        app_server.load_session_history(strict=True)
    assert not isinstance(caught.value, app_server.SessionHistoryCorrupt)


def test_saving_a_lesson_replaces_a_corrupt_history_file(server, tmp_path):
    history = tmp_path / "session_history.json"
    history.write_text("{not json", encoding="utf-8")
    base, revision = server.session_snapshot_with_revision()
    assert server.remember_session_if_current(base, {**base, "session_name": "새 수업"}, expected_revision=revision)
    saved = json.loads(history.read_text(encoding="utf-8"))
    assert isinstance(saved, list) and len(saved) == 1


def test_cleanup_deletes_nothing_while_history_is_corrupt(server, tmp_path):
    (tmp_path / "session_history.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(app_server.SessionHistoryUnreadable):
        server.cleanup_artifacts(dry_run=False, min_age_seconds=0)
    with pytest.raises(app_server.SessionHistoryUnreadable):
        server.cleanup_stale_work_folders(min_age_seconds=0)
