from unittest.mock import patch
from urllib.error import HTTPError, URLError

import app_server


def _handler(path="/api/session/mutate"):
    handler = object.__new__(app_server.AppRequestHandler)
    handler.path = path
    handler.command = "POST"
    responses = []
    handler._send_json = lambda payload, **kwargs: responses.append((payload, kwargs))
    return handler, responses


def _update_status(feed_error, feed_url):
    config = {"appId": "ClassInEDBMVP", "appName": "ClassInEDBMVP", "platform": "macos",
              "version": "0.1.0", "updateFeedUrl": feed_url}
    app_server.clear_app_update_status_cache()
    with patch.object(app_server, "load_app_update_config", return_value=config), \
            patch.object(app_server, "_fetch_update_feed", side_effect=feed_error):
        return app_server.build_app_update_status()


def test_update_check_failures_are_korean_with_raw_text_in_detail():
    missing = _update_status(HTTPError("https://x.test/u.json", 404, "Not Found", None, None), "https://x.test/a.json")
    offline = _update_status(URLError("getaddrinfo failed"), "https://x.test/b.json")
    broken = _update_status(ValueError("update feed must be a JSON object"), "https://x.test/c.json")
    assert missing["error"].startswith("아직 받을 수 있는 업데이트가 없습니다")
    assert "HTTP Error 404" in missing["detail"]
    assert offline["error"].startswith("업데이트 서버에 연결하지 못했습니다")
    assert broken["error"].startswith("업데이트 정보를 확인하지 못했습니다")
    for status in (missing, offline, broken):
        assert status["channelStatus"] == "error"
        assert "Error" not in status["error"]


def test_invalid_feed_keeps_english_only_in_detail():
    feed = {"appId": "OtherApp", "version": "0.2.0"}
    config = {"appId": "ClassInEDBMVP", "appName": "ClassInEDBMVP", "platform": "macos",
              "version": "0.1.0", "updateFeedUrl": "https://x.test/d.json"}
    app_server.clear_app_update_status_cache()
    with patch.object(app_server, "load_app_update_config", return_value=config), \
            patch.object(app_server, "_fetch_update_feed", return_value=feed):
        status = app_server.build_app_update_status()
    assert status["channelStatus"] == "invalid_feed"
    assert status["error"] == "업데이트 정보가 올바르지 않아 확인을 멈췄습니다. 관리자에게 알려 주세요."
    assert "appId mismatch" in status["detail"]


def test_publish_failure_keeps_exception_out_of_main_message():
    missing = app_server._publish_stage_failure_payload(
        "build", FileNotFoundError("problem p1 crop missing at C:/x/problem_crops/p1.png"))
    tall = app_server._publish_stage_failure_payload(
        "build", ValueError("Problem 'p-003' exceeds ClassIn's 50-page limit (61.0 pages)"))
    assert missing["code"] == "publish_asset_missing"
    assert "problem_crops" not in missing["error"] and "problem_crops" in missing["detail"]
    assert tall["code"] == "single_problem_too_tall"


def test_export_errors_map_known_pipeline_messages():
    hwp = app_server._export_error_payload(ValueError("HWP/HWPX conversion failed. Install LibreOffice"))
    empty = app_server._export_error_payload(ValueError("여러 문항이 함께 쓰는 공통 지문을 찾지 못했습니다"))
    io_error = app_server._export_error_payload(OSError(22, "Invalid argument", "C:/x/y.png"))
    assert (hwp["code"], hwp["retryable"]) == ("hwp_conversion_failed", False)
    assert "LibreOffice" not in hwp["error"]
    assert (empty["code"], empty["retryable"]) == ("content_target_empty", False)
    assert io_error["error"] == "파일을 읽거나 저장하지 못했습니다."
    assert "Invalid argument" in io_error["detail"]


def test_disk_full_and_memory_failures_allow_retry():
    assert app_server._export_error_payload(OSError(app_server.errno.ENOSPC, "No space left on device"))["retryable"] is True
    assert app_server._export_error_payload(MemoryError())["retryable"] is True


def test_uncaught_handler_error_returns_classified_json():
    handler, responses = _handler()

    def locked():
        raise PermissionError(13, "locked", "latest_session.json")

    handler._handle_session_mutate = locked
    handler.do_POST()
    payload, kwargs = responses[0]
    assert payload["code"] == "runtime_storage_access_denied"
    assert kwargs["status"] == app_server.HTTPStatus.BAD_REQUEST


def test_problem_image_failure_with_korean_text_returns_json():
    handler, responses = _handler("/api/session/problem-image")
    session = {"problems": [{"id": "p1", "title": "1"}]}
    with patch.object(app_server, "_session_problem_board_png_payload",
                      side_effect=OSError(2, "지정된 파일을 찾을 수 없습니다")):
        handler._send_session_problem_image_response(session, "p1", "board")
    payload, kwargs = responses[0]
    assert payload["code"] == "problem_image_unavailable"
    assert "지정된 파일" in payload["detail"]
    assert kwargs["status"] == app_server.HTTPStatus.INTERNAL_SERVER_ERROR


def test_settings_save_failure_has_code_and_korean_message():
    handler, responses = _handler("/api/user-settings")
    handler._read_json_body = lambda: {"aiEnabled": True}
    with patch.object(app_server, "update_api_keys", side_effect=PermissionError(13, "denied")):
        handler._handle_user_settings_post()
    payload, _kwargs = responses[0]
    assert payload["code"] == "runtime_storage_access_denied"
    assert payload["error"] == "설정을 저장하지 못했습니다."
