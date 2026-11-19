"""Server regressions found while integrating the dev branches (stabilize/20261118).

새 수업 vs a skipped history write, 고화질 after 확인, Korean error payloads with raw
detail, retention cleanup of added files, HWP-text number edits, cancel during Gemini,
update download causes, HWP converter notes in bug reports, and the user guide.
"""
from __future__ import annotations

import json
import time
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pytest

import app_server
from test_app_auto_update import update_status_for
from test_dev_srv_jobs import _enhance_session, _fake_ai_result
from test_dev_srv_storage import DAY, _write
from test_dev_support_release import _report
from test_dev_ui_review import guard_issue_counts, overlapping_pair, python_issue_counts

ROOT = Path(__file__).resolve().parent


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


def _handler(server, body=None):
    handler = object.__new__(app_server.AppRequestHandler)
    handler.server = server
    handler.headers = {"Host": "127.0.0.1:8765"}
    handler._read_json_body = lambda: body
    responses = []
    handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
    return handler, responses


def _lock_history_reads(monkeypatch, history: Path):
    real_read_text = Path.read_text

    def locked(self, *args, **kwargs):
        if self == history:
            raise PermissionError(13, "in use")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", locked)
    return lambda: monkeypatch.setattr(Path, "read_text", real_read_text)


LESSON = {"session_name": "오늘 수업", "problems": [{"id": "q1"}], "pages": []}


# F2: 새 수업 records the open lesson first -----------------------------------------

def test_new_lesson_records_a_lesson_whose_save_skipped_the_locked_history(server, tmp_path, monkeypatch):
    history = tmp_path / "session_history.json"
    history.write_text(json.dumps([{"id": "older", "session": {"problems": []}}]), encoding="utf-8")
    lesson = {**LESSON, "output_dir": str(tmp_path / "run")}
    unlock = _lock_history_reads(monkeypatch, history)
    base, revision = server.session_snapshot_with_revision()
    assert server.remember_session_if_current(base, lesson, expected_revision=revision)
    unlock()
    assert [entry["id"] for entry in json.loads(history.read_text(encoding="utf-8"))] == ["older"]

    server.clear_session(keep_history=True)

    kept = json.loads(history.read_text(encoding="utf-8"))
    assert kept[0]["session"]["problems"] == [{"id": "q1"}]
    assert [entry["id"] for entry in kept[1:]] == ["older"]
    assert not (tmp_path / "latest_session.json").exists()


def test_new_lesson_keeps_the_lesson_open_while_history_stays_locked(server, tmp_path, monkeypatch):
    history = tmp_path / "session_history.json"
    server.remember_session({**LESSON, "output_dir": str(tmp_path / "run")})
    before, revision = history.read_bytes(), server.session_revision()
    _lock_history_reads(monkeypatch, history)

    with pytest.raises(app_server.SessionHistoryUnreadable):
        server.clear_session(keep_history=True)

    assert (tmp_path / "latest_session.json").exists()
    assert server.session_snapshot()["problems"] == [{"id": "q1"}]
    assert server.session_revision() == revision
    assert history.read_bytes() == before


# F5: 고화질 keeps the 확인 of the same crop ------------------------------------------

def test_enhance_keeps_the_confirmation_of_the_same_crop(tmp_path):
    with patch.object(app_server, "_global_ai_enabled", return_value=False):
        session = {"problems": overlapping_pair(tmp_path), "pages": [], "output_dir": str(tmp_path)}
        app_server._mutate_confirm(session, ["p1", "p2"])
        session = app_server._mutate_enhance_image(session, {"problemIds": ["p1"], "mode": "preserve"})
        first = session["problems"][0]
        assert first["reviewStatus"] == "normal" and first["step"] == "s3"
        assert first["imagePath"] == first["reviewConfirmedImagePath"]
        assert python_issue_counts(session["problems"])["overlap"] == 0
        assert guard_issue_counts(app_server.rewrite_session_for_http(session)["problems"])["overlap"] == 0

        # A crop that was never confirmed gains nothing from 고화질.
        other = tmp_path / "fresh"
        other.mkdir()
        fresh = {"problems": overlapping_pair(other), "pages": [], "output_dir": str(other)}
        fresh = app_server._mutate_enhance_image(fresh, {"problemIds": ["p1"], "mode": "preserve"})
        assert "reviewConfirmedImagePath" not in fresh["problems"][0]
        assert python_issue_counts(fresh["problems"])["overlap"] == 1


# F9: 새 수업 / 초기화 failures keep the error contract --------------------------------

def _raising(exc):
    def clear(**_kwargs):
        raise exc
    return SimpleNamespace(latest_session=None, allowed_files=set(), clear_session_with_revision=clear)


@pytest.mark.parametrize("query,action", [("keepHistory=1", "새 수업"), ("", "초기화")])
def test_failed_clear_is_korean_with_the_raw_cause_in_detail_and_app_log(query, action):
    handler, responses = _handler(_raising(OSError("locked")))
    with patch.object(app_server, "_log_operation_exception") as log:
        handler._handle_session_clear(SimpleNamespace(query=query))
    payload, kwargs = responses[0]
    assert (payload["code"], kwargs["status"]) == ("reset_failed", HTTPStatus.INTERNAL_SERVER_ERROR)
    assert action in payload["error"]
    assert payload["detail"] == "OSError: locked"
    assert "최근 작업" not in " ".join(payload["recoverySteps"])
    log.assert_called_once()


@pytest.mark.parametrize("query,action", [("keepHistory=1", "새 수업"), ("", "초기화")])
def test_busy_clear_says_what_to_wait_for_in_korean(query, action):
    busy = app_server.ArtifactCleanupBusy("session reset paused while 1 request(s) are using files")
    handler, responses = _handler(_raising(busy))
    handler._handle_session_clear(SimpleNamespace(query=query))
    payload, kwargs = responses[0]
    assert (payload["code"], kwargs["status"]) == ("artifact_cleanup_busy", HTTPStatus.CONFLICT)
    assert action in payload["error"]
    assert payload["detail"] == str(busy)
    assert "방금 취소한" in payload["recoverySteps"][0]


def test_publish_steps_use_the_lists_screen_name():
    assert "최근 작업" not in " ".join(app_server.PUBLISH_RECOVERY_STEPS)


# F10: session_conflict copy ------------------------------------------------------------

def test_session_conflict_is_plain_korean_with_raw_detail():
    handler, responses = _handler(SimpleNamespace(session_snapshot_with_revision=lambda: (None, 7)))
    handler._send_session_conflict()
    payload, kwargs = responses[0]
    assert (payload["code"], kwargs["status"]) == ("session_conflict", HTTPStatus.CONFLICT)
    assert payload["sessionRevision"] == 7 and "revision 7" in payload["detail"]
    for text in (payload["error"], *payload["recoverySteps"]):
        assert "세션" not in text and "서버" not in text, text


# F14: retention cleanup keeps a preview run a lesson still uses ------------------------

def test_preview_run_holding_a_lesson_file_is_kept_whole(tmp_path):
    # A file added to an open lesson keeps its own preview run (output_dir stays the first run);
    # a crop that delete/split/merge dropped is still reachable through 실행 취소.
    previews = tmp_path / "outputs" / "previews"
    page = _write(previews / "added_run" / "pages" / "p2.png")
    dropped_crop = _write(previews / "added_run" / "problem_crops" / "q2.png")
    abandoned = _write(previews / "abandoned_run" / "problem_crops" / "q9.png")
    lesson = {"output_dir": str(previews / "lesson_run"), "problems": [],
              "pages": [{"id": "p2", "sourceImagePath": str(page)}]}
    options = {"runtime_dir": tmp_path, "min_age_seconds": 30 * DAY, "now": time.time()}
    for active, history in ((lesson, []), ({}, [{"id": "h1", "session": lesson}])):
        planned = app_server.cleanup_runtime_artifacts(active_session=active, history=history, dry_run=True, **options)
        assert planned["selectedPaths"] == [str(abandoned.resolve())]

    result = app_server.cleanup_runtime_artifacts(active_session=lesson, history=[], dry_run=False, **options)
    assert result["deletedPaths"] == [str(abandoned.resolve())]
    assert dropped_crop.exists() and page.exists()


# F17: 원문 번호 on a question restored from HWP text ------------------------------------

def test_saving_the_number_keeps_the_restored_question_exempt():
    # 원문 번호 수정 -> 번호 저장 edits the label only; the card is still drawn from HWP text.
    for number in ("19", "18"):
        restored = {
            "id": "p19", "title": "19.", "sourcePageId": "page-1",
            "bbox": {"left": 0, "top": 0, "width": 640, "height": 400},  # the full-width text band
            "riskFlags": [], "reviewStatus": "normal",  # 확인 cleared the fallback flag
            "problemNumberSource": "hwp_text_snippet",
        }
        neighbour = {"id": "p20", "title": "20.", "sourcePageId": "page-1",
                     "bbox": {"left": 40, "top": 60, "width": 500, "height": 200},
                     "riskFlags": [], "reviewStatus": "normal"}
        session = {"problems": [restored, neighbour], "pages": []}
        app_server._mutate_problem_number(session, "p19", number)
        assert restored["problemNumber"] == int(number)
        assert restored["problemNumberSource"] == "hwp_text_snippet", number
        assert restored["metadata"]["problem_number_source"] == "hwp_text_snippet", number
        assert python_issue_counts(session["problems"]) == {"overlap": 0, "reuse": 0}, number
        assert guard_issue_counts(session["problems"]) == {"overlap": 0, "reuse": 0}, number


# F20: a cancel during the first Gemini call skips the content retry ----------------------

def test_cancel_during_gemini_skips_the_content_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(app_server, "_global_ai_enabled", lambda: True)
    cancelled = []

    def rejected_while_cancelled(source_path, output_path, **kwargs):
        cancelled.append(True)  # the teacher presses 취소 while Gemini works
        result = _fake_ai_result(source_path, output_path, **kwargs)
        result.postprocess["content_preservation"] = {"status": "review_required", "review_required": True}
        return result

    with patch.object(app_server, "reconstruct_problem_image", side_effect=rejected_while_cancelled) as reconstruct:
        with pytest.raises(app_server.JobCancelled):
            app_server._mutate_enhance_image(
                _enhance_session(tmp_path, 1), {"problemIds": ["p1"], "mode": "ai"},
                should_cancel=lambda: bool(cancelled),
            )
    assert reconstruct.call_count == 1, "no content-retry Gemini call after the cancel"


# F24: a failed update download keeps its raw cause ---------------------------------------

def test_download_failure_keeps_raw_cause_in_detail_and_app_log(tmp_path, monkeypatch, capsys):
    def blocked(*args, **kwargs):
        raise HTTPError("https://example.invalid/Setup.exe", 403, "Forbidden", {}, None)

    monkeypatch.setattr(app_server, "urlopen", blocked)
    with pytest.raises(app_server.AppUpdateError) as caught:
        app_server._download_update_artifact(update_status_for(b"installer"), runtime_dir=tmp_path)
    assert caught.value.detail == "HTTPError: HTTP Error 403: Forbidden"

    handler, responses = _handler(None, {"pin": "4826", "expectedUpdate": {"version": "9"}})
    monkeypatch.setattr(app_server, "begin_app_update", Mock(side_effect=caught.value))
    app_server.AppRequestHandler._handle_app_update_install(handler)
    payload, _kwargs = responses[0]
    assert (payload["code"], payload["detail"]) == ("update_download_failed", "HTTPError: HTTP Error 403: Forbidden")
    assert "HTTP Error 403: Forbidden" in capsys.readouterr().err  # stderr is app.log in the packaged app


# F25: bug reports say which HWP converters are missing -----------------------------------

def test_hangul_converter_notes_ride_along_bounded_and_only_with_diagnostics(tmp_path):
    notes = ["pyhwp HTML fallback is available, but Chrome PDF printing was not found.", "x" * 500, *["n"] * 10]
    sent = _report(tmp_path, extra={"hangulNotes": notes})["diagnostics"]["hangulNotes"]
    assert sent[0] == notes[0] and len(sent) == 5 and len(sent[1]) == 200
    assert _report(tmp_path, extra={"hangulNotes": []})["diagnostics"]["hangulNotes"] == []
    assert "hangulNotes" not in _report(tmp_path, extra={"hangulNotes": None})["diagnostics"]
    opted_out = _report(tmp_path, include=False, extra={"hangulNotes": notes})
    assert "Chrome PDF" not in json.dumps(opted_out, ensure_ascii=False)


def test_server_reads_hangul_notes_and_a_broken_probe_does_not_block_the_report(tmp_path):
    note = "hwp5txt/unhwp/rhwp/hwpilot/kordoc text extractor was not found."
    with patch.object(app_server, "_update_root", return_value=tmp_path / "updates"):
        with patch.object(app_server, "cached_runtime_diagnostics", return_value={"hangul": {"technicalNotes": [note]}}):
            assert app_server._bug_report_diagnostics()[1]["hangulNotes"] == [note]
        with patch.object(app_server, "cached_runtime_diagnostics", side_effect=ImportError("cv2")):
            assert app_server._bug_report_diagnostics()[1]["hangulNotes"] is None


# F26: the 저장 공간 card names the list the way the screen does ---------------------------

def test_unreadable_history_cleanup_names_the_recent_list(server, tmp_path):
    (tmp_path / "session_history.json").write_text("{not json", encoding="utf-8")
    for dry_run in (False, True):  # the 정리 button, then the card's measurement on mount
        handler, responses = _handler(server, {"dryRun": dry_run})
        handler._handle_runtime_artifact_cleanup()
        payload, kwargs = responses[0]
        assert (payload["code"], kwargs["status"]) == ("session_history_unreadable", HTTPStatus.CONFLICT)
        assert payload["detail"]
        assert "최근 수업 목록" in payload["error"]
        assert "작업 이력" not in payload["error"] and "지우지" not in payload["error"]


# F29: the guide points to labels the app shows -------------------------------------------

def test_guide_points_to_labels_the_app_shows():
    guide = (ROOT / "EDB_MAKE_USER_GUIDE.md").read_text(encoding="utf-8")
    app = (ROOT / "ui_prototype" / "app.jsx").read_text(encoding="utf-8")
    for label in ("새 수업 시작", "다시 실행", "ClassIn에서 열기", "폴더 열기", "다시 제작", "저장 공간",
                  "오래된 작업 파일 정리", "크기·간격", "문항 사이 여백", "칠판 배경", "처리 방식 선택", "2단계 · 칠판용",
                  "선택적 AI 재구성"):
        assert label in guide and label in app, label
    # The update-prune thread clears week-old folders at every launch (dev/srv-update).
    assert "일주일이 지난 폴더는 앱을 켤 때 정리됩니다" in guide
    for gone in ("폴더는 다음 업데이트 때 정리", "`배치` 탭", "`자료` 탭", "`전체 레이아웃`", "`상세 설정`",
                 "`2단계 · AI 변환`", "`AI 업스케일 재구성`"):
        assert gone not in guide, gone
