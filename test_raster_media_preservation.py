from __future__ import annotations

import io
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

import raster_media
from build_problem_board_edb import (
    ProblemEntry, _ProblemAssetTask, _build_image_only_record_image,
    _render_problem_asset, run_problem_export,
)
from edb_builder import CROP_FORMAT_V1
from structured_schema import Box, Subject


def color_worksheet():
    image = Image.new("RGB", (1100, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.text((45, 20), "7.", font=ImageFont.load_default(size=24), fill=(20, 135, 155))
    draw.text((120, 30), "A problem with a colored figure", fill="black")
    draw.rectangle((340, 155, 719, 444), fill=(35, 140, 215), outline="black", width=3)
    draw.ellipse((400, 210, 560, 370), fill=(210, 65, 75), outline="black", width=3)
    draw.rectangle((590, 200, 675, 390), fill=(220, 165, 35))
    draw.line((830, 250, 980, 400), fill="black", width=4)
    draw.text((80, 560), "(1) choice a      (2) choice b", fill="black")
    return image


@pytest.mark.parametrize("scale", [1, 2, 3])
def test_color_figure_detected_with_crop_coordinates_at_multiple_resolutions(scale):
    image = color_worksheet()
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale))
    original = image.tobytes()
    regions = raster_media.detect_color_media_regions(image)
    assert len(regions) == 1
    box = regions[0]["bbox"]
    assert box["left"] <= 340 * scale and box["right"] >= 720 * scale
    assert box["top"] <= 155 * scale and box["bottom"] >= 445 * scale
    assert box["top"] > 100 * scale and box["bottom"] < 500 * scale
    assert image.tobytes() == original


@pytest.mark.parametrize("decoration", ["numbers", "highlight", "frame", "paper", "monochrome"])
def test_labels_highlights_and_page_decoration_are_not_media(decoration):
    image = Image.new("RGB", (900, 650), "white")
    draw = ImageDraw.Draw(image)
    if decoration == "numbers":
        for y in (20, 220, 450):
            draw.text((35, y), "10.", font=ImageFont.load_default(size=26), fill=(20, 140, 160))
    elif decoration == "highlight":
        draw.rectangle((80, 260, 800, 282), fill="yellow")
    elif decoration == "frame":
        draw.rectangle((15, 15, 885, 635), outline="blue", width=5)
    elif decoration == "paper":
        draw.rectangle((0, 0, 899, 649), fill=(240, 230, 100))
    else:
        draw.ellipse((260, 150, 640, 450), outline="black", width=5)
    assert raster_media.detect_color_media_regions(image) == []


def test_transparent_color_pixels_do_not_create_a_picture():
    image = Image.new("RGBA", (900, 650), (255, 255, 255, 255))
    ImageDraw.Draw(image).rectangle((250, 160, 600, 430), fill=(200, 20, 30, 0))
    assert raster_media.detect_color_media_regions(image) == []


def test_colored_body_text_is_not_restored_as_an_opaque_figure():
    image = Image.new("RGB", (900, 650), "white")
    ImageDraw.Draw(image).multiline_text((240, 220), "READ THIS\nCOLOR TEXT\nQUESTION", spacing=0,
        font=ImageFont.load_default(size=28), fill=(25, 125, 195),
        stroke_width=1, stroke_fill=(25, 125, 195))
    assert raster_media.detect_color_media_regions(image) == []


def test_optional_cv_dependency_missing_keeps_legacy_behavior(monkeypatch):
    monkeypatch.setattr(raster_media, "cv2", None)
    assert raster_media.detect_color_media_regions(color_worksheet()) == []


@pytest.mark.parametrize("regions_from_pdf", [False, True])
@pytest.mark.parametrize("target_width", [0, 550])
def test_math_preview_and_encoded_image_retain_color_with_chalk_outside(tmp_path, regions_from_pdf, target_width):
    image = color_worksheet()
    regions = [{"kind": "image", "confidence": .99, "source": "pdf_image_block",
                "bbox": {"left": 335, "top": 150, "right": 725, "bottom": 450}}] if regions_from_pdf else []
    task = _ProblemAssetTask(
        source_image=image, bounds=Box(left=0, top=0, width=1100, height=700),
        crop_path=tmp_path / "crop.png", board_render_path=tmp_path / "board.png",
        chalk_color=(238, 238, 226), trim_edge_guides=False, pad_edges=False,
        subject=Subject.MATH, source_media_regions=tuple(regions), detect_color_media=True,
    )
    _render_problem_asset(task)
    assert len(task.rendered_media_regions) == 1
    with Image.open(task.board_render_path) as preview:
        assert preview.getpixel((480, 280)) == image.getpixel((480, 280)) + (255,)
        assert preview.getpixel((905, 325))[:3] == (238, 238, 226)
        assert preview.getpixel((100, 100))[3] == 0
    # The persisted metadata must survive JSON round-trip and the export's
    # final chalk normalization, which used to erase math figures' colors.
    entry = ProblemEntry(
        problem_id="p7", title="7", problem_number=7, subject=Subject.MATH,
        source_page_id="page1", source_path="math.png", prepared_page=None,
        bounds=task.bounds, crop_path=task.crop_path, board_render_path=task.board_render_path,
        blocks=[], actual_height_pages=1, overflow_allowed=False, reading_heavy=False,
        risk_flags=[], processing_step="s2", board_render_preprocessed=True,
        preserve_media_regions=json.loads(json.dumps(task.rendered_media_regions)),
    )
    placement = SimpleNamespace(metadata={"crop_path": str(task.crop_path),
                                          "board_render_path": str(task.board_render_path)})
    payload = _build_image_only_record_image(placement, entry, dark_board=True,
        board_theme="charcoal", crop_format=CROP_FORMAT_V1, target_image_width_px=target_width,
        continuous_flow=False)
    with Image.open(io.BytesIO(payload.image_bytes)) as output:
        ratio = output.width / image.width
        assert output.convert("RGBA").getpixel((round(480 * ratio), round(280 * ratio))) == image.getpixel((480, 280)) + (255,)
        assert output.convert("RGBA").getpixel((round(100 * ratio), round(100 * ratio)))[3] == 0


def test_new_image_recognition_carries_detected_media_to_saved_session(tmp_path):
    source = tmp_path / "math.png"
    color_worksheet().save(source)
    result = run_problem_export(source, output_dir=tmp_path / "output", subject_name="math",
        ocr="none", ai_fallback="off", input_intent="single-problem", processing_step="s2",
        skip_deskew=True, skip_crop=True, export_edb=False, sync_ui=False)
    session = json.loads(result["ui_session_path"].read_text())
    assert len(session["problems"]) == 1
    regions = session["problems"][0]["preserveMediaRegions"]
    assert len(regions) == 1
    assert regions[0]["source"] == "raster_color_candidate_v1"


def test_restoration_changes_no_pixels_outside_the_detected_region(tmp_path):
    import numpy as np

    source = color_worksheet()
    enabled = _ProblemAssetTask(
        source_image=source, bounds=Box(left=0, top=0, width=1100, height=700),
        crop_path=tmp_path / "enabled-crop.png", board_render_path=tmp_path / "enabled.png",
        chalk_color=(238, 238, 226), trim_edge_guides=False, pad_edges=False,
        subject=Subject.MATH, detect_color_media=True,
    )
    disabled = replace(enabled, crop_path=tmp_path / "disabled-crop.png",
                       board_render_path=tmp_path / "disabled.png", detect_color_media=False,
                       rendered_media_regions=[])
    _render_problem_asset(enabled)
    _render_problem_asset(disabled)
    with Image.open(enabled.board_render_path) as after, Image.open(disabled.board_render_path) as before:
        assert after.size == before.size == source.size
        outside = np.ones((source.height, source.width), dtype=bool)
        for region in enabled.rendered_media_regions:
            box = region["bbox"]
            outside[box["top"]:box["bottom"], box["left"]:box["right"]] = False
        assert np.array_equal(np.asarray(after)[outside], np.asarray(before)[outside])


def test_tiny_color_label_does_not_restore_an_entire_monochrome_math_region():
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((100, 80, 500, 330), outline="black", width=4)
    draw.rectangle((200, 120, 205, 125), fill="red")
    region = {"kind": "image", "bbox": {"left": 50, "top": 50, "right": 550, "bottom": 350}}
    assert raster_media.stage2_media_regions(image, [region], chalk_math=True) == []
