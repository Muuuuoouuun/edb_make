from __future__ import annotations

import copy
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

import app_server
from build_problem_board_edb import _build_image_only_record_image, run_problem_export
from edb_builder import CROP_FORMAT_V1
from test_raster_media_preservation import color_worksheet


@pytest.fixture
def media_session(tmp_path):
    source = tmp_path / "math.png"
    image = color_worksheet()
    ImageDraw.Draw(image).rectangle((850, 250, 960, 400), fill=(90, 90, 90))
    image.save(source)
    return {"output_dir": str(tmp_path / "out"),
        "pages": [{"id": "page1", "sourceImageUri": source.as_uri(), "problemIds": ["p7"]}],
        "problems": [{"id": "p7", "title": "7", "subject": "math", "step": "s2",
            "sourcePageId": "page1", "sourceFileName": "math.png", "imagePath": source.as_uri(),
            "boardRenderPath": source.as_uri(),
            "bbox": {"left": 0, "top": 0, "width": 1100, "height": 700}}]}


def pixel(problem, point, *, board=True):
    path = app_server.decode_file_reference(problem["boardRenderPath" if board else "imagePath"])
    with Image.open(path) as image:
        if board:
            with Image.open(app_server.decode_file_reference(problem["imagePath"])) as raw:
                point = (round(point[0] * image.width / raw.width), round(point[1] * image.height / raw.height))
        return image.convert("RGBA").getpixel(point)


def assert_encoded_pixel(session, point, expected):
    entry = app_server._problems_to_entries(json.loads(json.dumps(session))["problems"])[0]
    placement = SimpleNamespace(metadata={"crop_path": str(entry.crop_path),
                                         "board_render_path": str(entry.board_render_path)})
    encoded = _build_image_only_record_image(placement, entry, dark_board=True,
        board_theme="charcoal", crop_format=CROP_FORMAT_V1, target_image_width_px=0,
        continuous_flow=False)
    with Image.open(io.BytesIO(encoded.image_bytes)) as image:
        with Image.open(entry.crop_path) as raw:
            point = (round(point[0] * image.width / raw.width), round(point[1] * image.height / raw.height))
        assert image.convert("RGBA").getpixel(point) == expected


def test_auto_redetects_after_crop_and_reset_without_pasting_old_coordinates(media_session):
    app_server._mutate_crop(media_session, "p7", {"cropBox": {"left": 100, "top": 60, "width": 950, "height": 600}})
    problem = media_session["problems"][0]
    assert len(problem["preserveMediaRegions"]) == 1
    assert pixel(problem, (380, 220)) == (210, 65, 75, 255)
    assert pixel(problem, (805, 265))[:3] != (90, 90, 90)
    assert_encoded_pixel(media_session, (380, 220), (210, 65, 75, 255))
    app_server._mutate_crop(media_session, "p7", {})
    assert pixel(problem, (480, 280)) == (210, 65, 75, 255)
    assert problem["preserveMediaRegions"][0]["bbox"]["left"] > 300


def test_manual_monochrome_selection_survives_crop_json_reset_and_final_encoding(media_session):
    app_server._mutate_preserve_media(media_session, "p7", "manual", [
        {"left": 850, "top": 250, "width": 111, "height": 151}])
    assert pixel(media_session["problems"][0], (905, 325)) == (90, 90, 90, 255)
    media_session = json.loads(json.dumps(media_session))
    app_server._mutate_crop(media_session, "p7", {"cropBox": {"left": 100, "top": 100, "width": 950, "height": 500}})
    problem = media_session["problems"][0]
    assert problem["preserveMediaRegions"][0]["bbox"] == {"left": 750, "top": 150, "width": 111, "height": 151}
    assert pixel(problem, (805, 225)) == (90, 90, 90, 255)
    assert_encoded_pixel(media_session, (805, 225), (90, 90, 90, 255))
    app_server._mutate_crop(media_session, "p7", {})
    assert pixel(problem, (905, 325)) == (90, 90, 90, 255)
    assert pixel(problem, (480, 280))[:3] != (210, 65, 75)


def stitch_two_pages(session, tmp_path):
    other = tmp_path / "other.png"
    image = Image.new("RGB", (1100, 250), "white")
    ImageDraw.Draw(image).text((100, 80), "A second page", fill="black")
    image.save(other)
    session["pages"].append({"id": "page2", "sourceImageUri": other.as_uri(), "problemIds": []})
    app_server._mutate_stitch_crop(session, "p7", [
        {"pageId": "page2", "bbox": {"left": 0, "top": 0, "width": 1100, "height": 250}},
        {"pageId": "page1", "bbox": {"left": 50, "top": 10, "width": 1000, "height": 650}}])


@pytest.mark.parametrize("mode", ["auto", "manual", "off"])
def test_stitch_preserves_mode_and_uses_actual_join_coordinates(media_session, tmp_path, mode):
    app_server._mutate_preserve_media(media_session, "p7", mode, [
        {"left": 850, "top": 250, "width": 111, "height": 151}])
    stitch_two_pages(media_session, tmp_path)
    problem = media_session["problems"][0]
    assert problem["preserveMediaMode"] == mode
    assert all(segment.get("cropBbox") for segment in problem["sourceSegments"])
    regions = problem["preserveMediaRegions"]
    if mode == "off":
        assert regions == []
        return
    assert len(regions) == 1
    box = regions[0]["bbox"]
    point = (round(box["left"] + box["width"] / 2), round(box["top"] + box["height"] / 2))
    assert pixel(problem, point) == pixel(problem, point, board=False)
    assert_encoded_pixel(media_session, point, pixel(problem, point, board=False))
    if mode == "manual":
        assert pixel(problem, point) == (90, 90, 90, 255)
        # A manual edit of an already stitched crop can be anchored again.
        app_server._mutate_preserve_media(media_session, "p7", "manual", regions)
        assert media_session["problems"][0]["preserveMediaSourceRegions"]


def test_off_survives_crop_json_export_and_publish_metadata(media_session):
    app_server._mutate_preserve_media(media_session, "p7", "off", [])
    media_session = json.loads(json.dumps(media_session))
    app_server._mutate_crop(media_session, "p7", {"leftRatio": .1})
    problem = media_session["problems"][0]
    assert problem["preserveMediaRegions"] == []
    assert pixel(problem, (370, 280))[:3] != (210, 65, 75)
    target = {}
    app_server._copy_publish_problem_layout_metadata(target, problem)
    assert target["preserveMediaMode"] == target["preserve_media_mode"] == "off"
    assert_encoded_pixel(media_session, (370, 280), pixel(problem, (370, 280)))


@pytest.mark.parametrize("mode", ["auto", "manual", "off"])
def test_cropping_stitched_material_uses_stitched_pixels_and_preserves_settings(media_session, tmp_path, mode):
    app_server._mutate_preserve_media(media_session, "p7", mode, [
        {"left": 850, "top": 250, "width": 111, "height": 151}])
    stitch_two_pages(media_session, tmp_path)
    problem = media_session["problems"][0]
    before = copy.deepcopy(problem)
    with Image.open(app_server.decode_file_reference(before["imagePath"])) as image:
        original = image.copy()
    app_server._mutate_crop(media_session, "p7", {"leftRatio": .02, "topRatio": .01})
    with Image.open(app_server.decode_file_reference(problem["imagePath"])) as cropped:
        expected = original.crop((round(original.width * .02), round(original.height * .01), original.width, original.height))
        assert cropped.size == expected.size
        assert cropped.tobytes() == expected.tobytes()
    assert problem["preserveMediaMode"] == mode
    if mode == "off":
        assert problem["preserveMediaRegions"] == []
    else:
        box = problem["preserveMediaRegions"][0]["bbox"]
        point = (round(box["left"] + box["width"] / 2), round(box["top"] + box["height"] / 2))
        assert pixel(problem, point) == pixel(problem, point, board=False)
        assert_encoded_pixel(media_session, point, pixel(problem, point, board=False))
    app_server._mutate_crop(media_session, "p7", {})
    with Image.open(app_server.decode_file_reference(problem["imagePath"])) as reset:
        assert reset.size == original.size and reset.tobytes() == original.tobytes()
    assert problem["sourceSegments"] == before["sourceSegments"]
    target = {}
    app_server._copy_publish_problem_layout_metadata(target, problem)
    assert target["sourceSegments"] == problem["sourceSegments"]
    assert target["manualStitch"] is True


def test_legacy_padded_manual_selection_cannot_be_pasted_at_guessed_coordinates(media_session, tmp_path):
    padded = tmp_path / "padded.png"
    Image.new("RGB", (1100, 740), "white").save(padded)
    media_session["problems"][0]["imagePath"] = padded.as_uri()
    app_server._mutate_preserve_media(media_session, "p7", "manual", [
        {"left": 300, "top": 200, "width": 100, "height": 100}])
    before = copy.deepcopy(media_session)
    with pytest.raises(ValueError, match="자동 감지"):
        app_server._mutate_crop(media_session, "p7", {"leftRatio": .1})
    assert media_session == before


@pytest.mark.parametrize("regions", [None, [{}], [{"left": float("nan"), "top": 0, "width": 20, "height": 20}],
    [{"left": 2000, "top": 0, "width": 20, "height": 20}],
    [{"left": 0, "top": 0, "width": -1, "height": 20}],
    [{"left": 0, "top": 0, "width": 20, "height": 20}] * 17])
def test_invalid_manual_regions_do_not_change_the_session(media_session, regions):
    before = copy.deepcopy(media_session)
    with pytest.raises(ValueError):
        app_server._mutate_preserve_media(media_session, "p7", "manual", regions)
    assert media_session == before


def test_manual_region_is_clipped_without_shifting_and_auto_can_be_restored(media_session):
    app_server._mutate_preserve_media(media_session, "p7", "manual", [
        {"left": -20, "top": -10, "width": 60, "height": 50}])
    assert media_session["problems"][0]["preserveMediaRegions"][0]["bbox"] == {"left": 0, "top": 0, "width": 40, "height": 40}
    app_server._mutate_preserve_media(media_session, "p7", "auto", [])
    assert len(media_session["problems"][0]["preserveMediaRegions"]) == 1
    assert media_session["problems"][0]["preserveMediaSourceRegions"] == []


def test_new_padded_recognition_records_exact_source_offset_for_manual_crop(tmp_path):
    source = tmp_path / "math.png"
    color_worksheet().save(source)
    result = run_problem_export(source, output_dir=tmp_path / "output", subject_name="math",
        ocr="none", ai_fallback="off", input_intent="single-problem", processing_step="s2",
        skip_deskew=True, skip_crop=True, export_edb=False, sync_ui=False)
    session = json.loads(result["ui_session_path"].read_text())
    problem = session["problems"][0]
    # The review UI switches this existing source crop into chalk mode.
    problem["step"] = problem["processingStep"] = "s2"
    offset = problem["cropSourceOffset"]
    assert offset is not None
    left, top = 400 - offset["left"], 210 - offset["top"]
    app_server._mutate_preserve_media(session, problem["id"], "manual", [
        {"left": left, "top": top, "width": 160, "height": 160}])
    anchor = problem["preserveMediaSourceRegions"][0]["bbox"]
    assert anchor == {"left": 400, "top": 210, "width": 160, "height": 160}
    app_server._mutate_crop(session, problem["id"], {"cropBox": {"left": 100, "top": 60, "width": 950, "height": 600}})
    assert pixel(problem, (380, 220)) == (210, 65, 75, 255)


def test_preserve_media_http_action_uses_revision_checked_session_commit(media_session):
    handler = object.__new__(app_server.AppRequestHandler)
    handler._current_session_state = lambda: (copy.deepcopy(media_session), 5)
    handler._read_json_body = lambda: {"action": "preserve-media", "problemId": "p7", "mode": "off", "expectedSessionRevision": 5}
    handler._validate_expected_session_revision = lambda payload, revision: payload["expectedSessionRevision"] == revision
    commits = []
    handler._remember_session_if_current = lambda before, after, **kwargs: (commits.append((before, after, kwargs)) or True, 6)
    responses = []
    handler._send_json = lambda payload, **kwargs: responses.append(payload)
    handler._handle_session_mutate()
    assert responses[0]["ok"] and responses[0]["sessionRevision"] == 6
    assert commits[0][0] == media_session
    assert commits[0][1]["problems"][0]["preserveMediaMode"] == "off"
    assert commits[0][2] == {"expected_revision": 5}
