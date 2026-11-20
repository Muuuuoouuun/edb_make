"""Regression checks for the dev2 paths-publish chain.

- R21: a moved data folder (OneDrive folder backup, new PC or user name) keeps lessons and files.
- R12: another site cannot call the API, and /api/file never opens an unlisted network path.
- R34: the lesson's paths are resolved once across requests.
- R9: a publish remap resolves its two roots once.
- R10: a publish keeps no per-question placements in the session.
- R40: a write that moves the lesson to outputs/recovered keeps one 최근 수업 entry.
- R33: the page-decoration scan decodes each crop file once.
"""
from __future__ import annotations

import http.client
import json
import ntpath
import os
import shutil
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

import pytest
from PIL import Image, ImageDraw

import app_server
import build_problem_board_edb as board
from test_dev_srv_storage import _age_tree, _runtime_patches, _write


# --- R21: moved data folder ---------------------------------------------------

def test_moved_data_folder_keeps_lessons_and_their_files(tmp_path):
    old_home = tmp_path.resolve() / "Documents" / "ClassInEDBMVP"
    new_home = tmp_path.resolve() / "OneDrive" / "문서" / "ClassInEDBMVP"
    old_runtime, new_runtime = old_home / ".app_runtime", new_home / ".app_runtime"
    draft = old_runtime / "outputs" / "previews" / "quiz1_run"
    done = old_runtime / "outputs" / "previews" / "quiz2_run"
    edb = _write(done / "published" / "gen1" / "quiz2.edb")
    lesson = {"session_name": "1단원", "output_dir": str(draft),
              "problems": [{"id": "p1", "imagePath": _write(draft / "crops" / "p1.png").as_uri()}]}
    built = {"session_name": "2단원", "output_dir": str(done),
             "problems": [{"id": "p1", "imagePath": str(_write(done / "crops" / "p1.png"))}],
             "publishSummary": {"edbPath": str(edb), "edbParts": [{"edbPath": str(edb)}]}}
    with ExitStack() as stack:  # the app as it ran before the move
        _runtime_patches(stack, old_runtime)
        stack.enter_context(patch.object(app_server, "BASE_DIR", old_home))
        app_server.save_session_history(app_server._session_history_with_session(
            app_server._session_history_with_session([], built), lesson))
        app_server.save_latest_session(lesson)
    _age_tree(old_home)
    new_home.parent.mkdir(parents=True)
    shutil.move(str(old_home), str(new_home))  # Known Folder Move: same files, new root, old mtimes

    with ExitStack() as stack:
        _runtime_patches(stack, new_runtime)
        stack.enter_context(patch.object(app_server, "BASE_DIR", new_home))
        latest = app_server.load_latest_session()
        history = app_server.load_session_history(strict=True)
        assert app_server.plan_stale_work_folders(
            output_root=new_runtime / "outputs", active_session=latest, history=history, now=time.time(),
        ) == [], "startup cleanup must not delete the lessons' folders"
        image = app_server.decode_file_reference(app_server.rewrite_session_for_http(latest)["problems"][0]["imagePath"])
        assert image.is_file() and image.is_relative_to(new_runtime), image
        public = app_server._public_session_history(history)
        assert public[1]["publishSummary"]["edbFileExists"], "EDB 제작 완료 chip and ⋯ menu stay"
        root, _recovered = app_server._select_session_write_root(latest, operation="publish", reserved_descendants=())
        assert root.is_relative_to(new_runtime), root
        # The next save stores the new-root output_dir: still one 최근 수업 entry per lesson.
        after = app_server._session_history_with_session(history, dict(latest, output_dir=str(root)))
        assert [entry["sessionName"] for entry in after] == ["1단원", "2단원"]


# --- R12: other sites and network paths ----------------------------------------

@pytest.fixture
def live_server(tmp_path):
    with ExitStack() as stack:
        _runtime_patches(stack, tmp_path / ".app_runtime")  # the real name, so the moved-folder rebase is armed
        srv = app_server.AppHTTPServer(("127.0.0.1", 0), app_server.AppRequestHandler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            yield srv
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(5)


def _get_status(srv, target, site=None):
    port = srv.server_address[1]
    headers = {"Host": f"127.0.0.1:{port}", **({"Sec-Fetch-Site": site} if site else {})}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", target, headers=headers)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_another_site_cannot_call_the_api_but_can_open_the_app(live_server, site):
    assert _get_status(live_server, "/api/session/latest", site) == 403  # would rewrite 최근 수업
    assert _get_status(live_server, "/api/health", site) == 403
    assert _get_status(live_server, "/", site) == 200
    assert _get_status(live_server, "/api/health", "same-origin") == 200
    assert _get_status(live_server, "/api/health") == 200  # the launcher's health check sends no Sec-Fetch headers


@pytest.mark.skipif(os.name != "nt", reason="only Windows opens \\\\host\\share paths over the network")
def test_file_endpoint_never_opens_an_unlisted_network_path(live_server, tmp_path):
    touched = []

    def recording(real):
        def fake(path, *args, **kwargs):
            if str(path).startswith("\\\\"):
                touched.append(str(path))
                raise OSError(0, "network path stubbed", str(path), 53)
            return real(path, *args, **kwargs)
        return fake

    listed = _write(tmp_path / "outputs" / "p1.png", b"png", mtime=None)
    live_server.add_allowed_files({str(listed.resolve())})
    with (
        patch.object(os, "stat", recording(os.stat)),
        patch.object(os.path, "exists", recording(os.path.exists)),
        patch.object(ntpath, "_getfinalpathname", recording(ntpath._getfinalpathname)),
    ):
        for spelling in (
            r"\\attacker.example\s\x.png", "file://attacker.example/s/x.png",
            "file:////attacker.example/s/x.png", r"\\attacker.example",
            r"\\attacker.example\s\.app_runtime\outputs\x.png",  # never rebased onto the data folder either
        ):
            # A link clicked in KakaoTalk or mail opens with Sec-Fetch-Site: none, so only the path check stops it.
            assert _get_status(live_server, "/api/file?path=" + quote(spelling), "none") == 403, spelling
        assert _get_status(live_server, "/api/file?path=" + quote(str(listed.resolve())), "same-origin") == 200
    assert touched == []


# --- R34: cached path resolution -------------------------------------------------

def test_session_paths_resolve_once_across_requests(tmp_path):
    problems = []
    for index in range(30):
        crop = _write(tmp_path / f"crop-{index}.png", b"png", mtime=None)
        board_png = _write(tmp_path / f"board-{index}.png", b"png", mtime=None)
        problems.append({"id": f"p{index}", "imagePath": crop.as_uri(), "boardRenderPath": board_png.as_uri()})
    page = _write(tmp_path / "page.png", b"png", mtime=None)
    session = {"problems": problems, "pages": [{"id": "page-1", "sourceImagePath": str(page)}],
               "rendered_page_paths": [str(page)]}
    server = SimpleNamespace(_state_lock=threading.RLock(), _session_revision=1, latest_session=session)
    board0 = str((tmp_path / "board-0.png").resolve())

    http_session = app_server.rewrite_session_for_http(session)
    allowed = app_server.collect_session_file_paths(session)
    restored = app_server._denormalize_session_paths(http_session)
    assert app_server.AppHTTPServer.board_file_context(server, board0)[0]["id"] == "p0"

    # The next 확인 / undo / save: restore, commit under the lock, then the first thumbnail.
    with patch.object(Path, "resolve", autospec=True, side_effect=Path.resolve) as resolve:
        server._session_revision = 2
        assert app_server.collect_session_file_paths(session) == allowed
        assert app_server._denormalize_session_paths(http_session) == restored
        assert app_server.AppHTTPServer.board_file_context(server, board0)[0]["id"] == "p0"
    assert resolve.call_count == 0

    (tmp_path / "crop-0.png").unlink()
    assert str((tmp_path / "crop-0.png").resolve()) not in app_server.collect_session_file_paths(session)


# --- R9: publish path remap ---------------------------------------------------------

def test_remap_artifact_paths_resolves_roots_once(tmp_path):
    staging = tmp_path / ".publish-staging" / "gen"
    (staging / "crops").mkdir(parents=True)
    final = tmp_path / "published" / "gen"  # not created yet, like a real publish
    crop = staging / "crops" / "q1.png"
    problem = {
        "cropPath": str(crop),
        "cropUri": f"{app_server._path_as_file_uri(staging)}/crops/q1.png",
        "cropUrl": app_server.path_to_api_url(crop),
        "sibling": f"{staging}-old{os.sep}q1.png",
        "score": 3,
        "flags": [None, True],
    }
    payload = {"problems": [dict(problem, id=index) for index in range(500)], "edbPath": crop}
    calls = []
    original = Path.resolve

    def counting(self, *args, **kwargs):
        calls.append(str(self))
        return original(self, *args, **kwargs)

    with patch.object(Path, "resolve", counting):
        remapped = app_server._remap_artifact_paths(payload, staging, final)
    assert len(calls) <= 4, len(calls)
    target = final.resolve() / "crops" / "q1.png"
    assert remapped["edbPath"] == target
    assert remapped["problems"][499] == {
        "cropPath": str(target),
        "cropUri": f"{app_server._path_as_file_uri(final)}/crops/q1.png",
        "cropUrl": f"/api/file?path={quote(str(target))}",
        "sibling": problem["sibling"],
        "score": 3,
        "flags": [None, True],
        "id": 499,
    }


# --- R10 and R40: publish ---------------------------------------------------------

def _page_preview(tmp_path):
    source = tmp_path / "source.png"
    image = Image.new("RGB", (160, 220), "white")
    draw = ImageDraw.Draw(image)
    for y in range(30, 180, 25):
        draw.text((15, y), "Whole printed page.", fill="black")
    image.save(source)
    return board.run_problem_export(source, output_dir=tmp_path / "work", subject_name="unknown", ocr="none",
                                    export_edb=False, input_intent="page-as-is", record_mode="image-only")["ui_session"]


def _publish(session, body=None):
    responses = []
    server = SimpleNamespace(latest_session=session)
    server.remember_session = lambda saved: setattr(server, "latest_session", saved)
    handler = object.__new__(app_server.AppRequestHandler)
    handler.server = server
    handler._read_json_body = lambda: dict(body or {})
    handler._send_json = lambda payload, **kwargs: responses.append(payload)
    handler._handle_session_publish()
    assert len(responses) == 1 and responses[0]["ok"], responses
    return responses[0], server.latest_session


def _has_placements(value):
    if isinstance(value, dict):
        return "placements" in value or any(_has_placements(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_placements(item) for item in value)
    return False


def test_publish_keeps_no_placements_in_the_session(tmp_path):
    preview = _page_preview(tmp_path)
    old_edb = str(tmp_path / "old.edb")
    old_parts = [{"edbPath": old_edb, "placements": [{"problem_id": "p1"}]}]
    preview["publish_history"] = [{"edbPath": old_edb, "edbParts": old_parts, "edbValidation": {"edbParts": old_parts}}]
    response, saved = _publish(preview)
    assert not _has_placements(saved), "no copy in the session, its summary, validation or history"
    assert [item["edbPath"] for item in saved["publishHistory"]][1:] == [old_edb]  # the legacy entry is only slimmed
    parts = response["session"]["edbParts"]
    assert parts and {str(Path(part["edbPath"]).resolve()) for part in parts} <= app_server.collect_session_file_paths(saved)


def test_publish_that_moves_the_lesson_keeps_one_recent_lesson(tmp_path):
    preview = _page_preview(tmp_path)
    recovered = (tmp_path / "outputs" / "recovered" / "session_x").resolve()
    with patch.object(app_server, "_select_session_write_root", return_value=(recovered, True)):
        response, first = _publish(preview)
        # The UI keeps the session it got back and sends it with the next EDB 제작.
        _response, second = _publish(first, {"session": json.loads(json.dumps(response["session"]))})
    history = []
    for session in (preview, first, second):
        history = app_server._session_history_with_session(history, session)
    assert len(history) == 1, [entry["outputDir"] for entry in history]
    assert history[0]["publishSummary"]
    assert history[0]["outputDir"] == str(recovered)


def test_split_that_moves_the_lesson_keeps_one_recent_lesson(tmp_path):
    runtime_dir = tmp_path / ".app_runtime"
    legacy_root = runtime_dir / "outputs" / ("legacy_" + "가" * 20 + "x" * 180)  # too long for new crops
    page = tmp_path / "page.png"
    Image.new("RGB", (160, 200), "white").save(page)
    uri = page.resolve().as_uri()
    session = {
        "output_dir": str(legacy_root), "outputDir": str(legacy_root),
        "pages": [{"id": "page-1", "problemIds": ["p1"], "sourceImagePath": uri}],
        "problems": [{"id": "p1", "title": "1.", "sourcePageId": "page-1", "sourceImagePath": uri,
                      "imagePath": uri, "boardRenderPath": uri, "riskFlags": [],
                      "bbox": {"left": 10, "top": 10, "width": 120, "height": 160}}],
    }
    with patch.object(app_server.sys, "platform", "win32"), patch.object(app_server, "RUNTIME_DIR", runtime_dir):
        before = app_server._session_history_with_session([], session)
        updated = app_server._mutate_split(session, "p1", 0.5)
        after = app_server._session_history_with_session(before, updated)
    assert Path(updated["output_dir"]).parent.name == "recovered"
    assert [entry["outputDir"] for entry in after] == [updated["output_dir"]]
    assert after[0]["id"] == before[0]["id"]


def test_recovered_retry_result_keeps_the_lesson_id():
    session = {"output_dir": str(Path("C:/legacy/long-output")), "outputDir": str(Path("C:/legacy/long-output"))}
    before = app_server._session_history_entry(session)["id"]
    recovered = Path("C:/short/recovered/session_123")
    assert app_server._adopt_recovered_output_dir_after_success(
        session, recovered, recovered=True, summaries=[{"status": "applied"}])
    assert session["output_dir"] == str(recovered)
    assert app_server._session_history_entry(session)["id"] == before


# --- R33: page-decoration scan cache ------------------------------------------------

def test_page_chrome_scan_runs_once_per_crop_file(tmp_path):
    problems = []
    for index in range(3):
        path = tmp_path / f"crop{index}.png"
        image = Image.new("RGB", (300, 200), "white")
        if index == 0:  # a page line left at the bottom of a stage-3 crop blocks 제작
            ImageDraw.Draw(image).line((0, 196, 299, 196), fill="black", width=3)
        image.save(path)
        problems.append({"id": f"p{index}", "imagePath": path.as_uri(),
                         "processingStep": board.PROCESSING_STEP_RECONSTRUCT})

    def issue_ids():
        return [issue["problemId"] for issue in board._classin_page_chrome_artifact_issues(problems)]

    with patch.object(board, "_problem_image_page_chrome_artifact_stats",
                      wraps=board._problem_image_page_chrome_artifact_stats) as scan:
        assert issue_ids() == ["p0"]
        assert issue_ids() == ["p0"]  # every board edit re-runs the 제작 preview check
        assert scan.call_count == 3
        Image.new("RGB", (320, 200), "white").save(tmp_path / "crop0.png")  # cleaned crop, new size
        assert issue_ids() == []
        assert scan.call_count == 4
