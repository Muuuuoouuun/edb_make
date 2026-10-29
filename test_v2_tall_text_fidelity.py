"""Tall joined text keeps punctuation without raising general image limits."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw
import pytest

import build_problem_board_edb as board
from edb_builder import ImageRecordSpec, build_edb, build_image_record
from inspect_edb import extract_images, parse_edb
from structured_schema import Box, Subject


def test_native_tall_text_bypasses_edge_tier_only_inside_fixed_pixel_and_height_budgets():
    # Real Korean joined passage dimensions that previously shrank to4096px.
    source = (1010, 5695)
    assert board._v2_encoded_image_size(source, (301, 1697), preserve_tall_text=True) == source
    ordinary = board._v2_encoded_image_size(source, (301, 1697))
    assert max(ordinary) <= board.V2_ENCODED_IMAGE_MAX_EDGE_PX
    assert ordinary != source
    limit = board.V2_ENCODED_TEXT_MAX_HEIGHT_PX
    assert limit == board.V2_ENCODED_IMAGE_MAX_PIXELS // board.V2_ENCODED_IMAGE_MIN_WIDTH_PX
    assert board._v2_encoded_image_size((1024, limit), (301, 3445), preserve_tall_text=True) == (1024, limit)
    # Width, fixed absolute height, and area are independent guards. In
    # particular a narrow input cannot turn the pixel budget into a huge edge.
    for too_large in ((4097, 5000), (1024, limit + 1), (3000, 5000), (1, 100000)):
        encoded = board._v2_encoded_image_size(too_large, (301, 1700), preserve_tall_text=True)
        assert max(encoded) <= board.V2_ENCODED_IMAGE_MAX_EDGE_PX
        assert encoded[0] <= board.V2_ENCODED_IMAGE_MAX_WIDTH_PX
        assert encoded[0] * encoded[1] <= board.V2_ENCODED_IMAGE_MAX_PIXELS


@pytest.fixture
def tall_entry(tmp_path):
    size = (1010, 5695)
    chalk = board._resolve_chalk_color("charcoal")
    image = Image.new("RGBA", size, chalk + (0,))
    draw = ImageDraw.Draw(image)
    # Text strokes plus an isolated faint punctuation dot. A resize can drop
    # the latter below the final dehalo/visible-alpha cutoff.
    for y in (80, 1800, 3600, 5500):
        draw.text((70, y), "Shared Korean/English source text", fill=chalk + (255,))
    image.putpixel((501, 2848), chalk + (32,))
    image = board._finalize_text_cutout(image, chalk_color=chalk)
    crop = tmp_path / "joined.png"
    image.save(crop)
    image.close()
    entry = board.ProblemEntry(
        problem_id="shared-36-39", title="shared passage", problem_number=None,
        subject=Subject.KOREAN, source_page_id="page1", source_path=str(tmp_path / "source.pdf"),
        prepared_page=None, bounds=Box(0, 0, *size), crop_path=crop, board_render_path=crop,
        blocks=[], actual_height_pages=3, overflow_allowed=False, reading_heavy=True,
        risk_flags=[], processing_step=board.PROCESSING_STEP_CHALK,
        source_segments=[{"sourcePageId": "page1"}, {"sourcePageId": "page2"}],
        board_render_preprocessed=True,
    )
    placement = SimpleNamespace(metadata={"crop_path": str(crop), "board_render_path": str(crop)},
                                start_y_pages=0, snapped_next_start_y_pages=3)
    return entry, placement


def build(entry, placement, *, dark_board=False, generate=True):
    return board._build_image_only_record_image(
        placement, entry, dark_board=dark_board, board_theme="charcoal",
        crop_format=board.CROP_FORMAT_V2, target_image_width_px=0,
        continuous_flow=False, generate_record=generate,
    )


@pytest.mark.parametrize("dark_board", [False, True])
def test_tall_stitched_source_pixels_survive_real_png_and_edb_readback(tall_entry, tmp_path, dark_board):
    entry, placement = tall_entry
    encoded = build(entry, placement, dark_board=dark_board)
    assert (encoded.width_px, encoded.height_px) == (1010, 5695)
    assert encoded.resolution_policy == "source-preserving"
    assert encoded.display_width_px == board.V2_TARGET_IMAGE_WIDTH_PX
    assert encoded.display_height_px == pytest.approx(board.V2_TARGET_IMAGE_WIDTH_PX * 5695 / 1010)
    with Image.open(entry.crop_path) as source, Image.open(io.BytesIO(encoded.image_bytes)) as output:
        assert source.size == output.size
        assert ImageChops.difference(source.convert("RGBA"), output.convert("RGBA")).getbbox() is None
        assert output.getpixel((501, 2848))[3] == 32
        # Reproduce the original failure independently: the unchanged ordinary
        # tier downsamples this dot below the observer's alpha>16 ink threshold.
        ordinary_size = board._v2_encoded_image_size(source.size, (301, 1697))
        ordinary = board._finalize_text_cutout(
            source.resize(ordinary_size, Image.Resampling.LANCZOS),
            chalk_color=board._resolve_chalk_color("charcoal"),
        )
        x, y = round(501 * ordinary.width / source.width), round(2848 * ordinary.height / source.height)
        assert ordinary.getchannel("A").crop((x - 2, y - 2, x + 3, y + 3)).getextrema()[1] <= 16
    record = build_image_record(ImageRecordSpec(
        1, encoded.image_bytes, encoded.secondary_bytes, 0, 0,
        encoded.display_width_px / board.CANVAS_HEIGHT,
        encoded.display_height_px / board.CANVAS_HEIGHT,
    ))
    path = tmp_path / "joined.edb"
    path.write_bytes(build_edb([record], header_flag=0, page_count_hint=50))
    parsed = parse_edb(path)
    assert parsed.record_count_actual == 1 and parsed.page_count_hint == 50
    primary = parsed.records[0].embedded_images[0]
    assert (primary.width, primary.height) == (1010, 5695)
    assert parsed.records[0].width_hint == pytest.approx(encoded.display_width_px / board.CANVAS_HEIGHT)
    assert parsed.records[0].height_hint == pytest.approx(encoded.display_height_px / board.CANVAS_HEIGHT)
    decoded = extract_images(parsed, path, tmp_path / "decoded")
    with Image.open(decoded[0]) as image:
        assert image.getpixel((501, 2848))[3] == 32


def test_header_only_planning_matches_actual_geometry_without_decoding(tall_entry):
    entry, placement = tall_entry
    with patch.object(Image.Image, "load", side_effect=AssertionError("planner decoded a source bitmap")):
        planned = build(entry, placement, generate=False)
    actual = build(entry, placement)
    assert planned.image_bytes == b"" and planned.secondary_bytes == b""
    assert (planned.width_px, planned.height_px) == (actual.width_px, actual.height_px)
    assert (planned.display_width_px, planned.display_height_px) == (actual.display_width_px, actual.display_height_px)


def test_single_fragment_nontext_and_reconstruction_keep_existing_tier_and_display_hints(tall_entry):
    entry, placement = tall_entry
    eligible = build(entry, placement, generate=False)
    exclusions = (
        {"source_segments": entry.source_segments[:1]},
        {"subject": Subject.MATH},
        {"processing_step": board.PROCESSING_STEP_RECONSTRUCT},
    )
    for changed in exclusions:
        with patch.multiple(entry, **changed):
            original_policy = build(entry, placement, generate=False)
        assert original_policy.height_px <= board.V2_ENCODED_IMAGE_MAX_EDGE_PX
        assert original_policy.display_width_px == eligible.display_width_px
        assert original_policy.display_height_px == eligible.display_height_px


def test_ordinary_page_export_policy_is_unchanged():
    assert board._v2_encoded_image_size((2480, 3508), (760, 1075), preserve_tall_text=True) == (2480, 3508)
    assert board._v2_encoded_image_size((3508, 2480), (1075, 760), preserve_tall_text=True) == (3508, 2480)
    assert board._v2_encoded_image_size((5000, 7000), (900, 1260), preserve_tall_text=True) == board._v2_encoded_image_size((5000, 7000), (900, 1260))


@pytest.mark.parametrize("location", ["camel", "snake", "metadata", "metadata-camel"])
def test_actual_ui_entry_conversion_keeps_unknown_neutral_shared_metadata(tall_entry, location):
    import app_server
    existing, _ = tall_entry
    problem = {"id": "neutral-shared", "title": "지문 36~39", "subject": "unknown",
               "sourceFileName": "c42083a412b9453abcde.pdf", "sourcePageId": "upload-page-012",
               "imagePath": existing.crop_path.as_uri(), "boardRenderPath": existing.crop_path.as_uri(),
               "bbox": {"left": 0, "top": 0, "width": 1010, "height": 5695}, "step": "s2",
               "sourceSegments": existing.source_segments}
    if location == "camel":
        problem.update(passageRole="passage_fragment", passageRange={"start": 36, "end": 39})
    elif location == "snake":
        problem.update(passage_role="passage_fragment", passage_range={"start": 36, "end": 39})
    elif location == "metadata":
        problem["metadata"] = {"passage_role": "passage_fragment", "passage_range": {"start": 36, "end": 39}}
    else:
        problem["metadata"] = {"passageRole": "passage_fragment", "passageRange": {"start": 36, "end": 39}}
    entry = app_server._problems_to_entries(json.loads(json.dumps([problem])))[0]
    assert entry.subject == Subject.UNKNOWN
    assert not board._problem_prefers_text_preservation(entry.subject, entry.source_path, entry.title)
    assert entry.passage_role == "passage_fragment" and entry.passage_range == (36, 39)
    # UI conversion must carry provenance, not require a subject/filename guess.
    placement = SimpleNamespace(metadata={"crop_path": str(entry.crop_path), "board_render_path": str(entry.board_render_path)},
                                start_y_pages=0, snapped_next_start_y_pages=10)
    planned = build(entry, placement, generate=False)
    actual = build(entry, placement)
    assert (planned.width_px, planned.height_px) == (1010, 5695)
    assert (actual.width_px, actual.height_px) == (1010, 5695)


@pytest.mark.parametrize("subject", [Subject.MATH, Subject.SCIENCE])
def test_known_graphical_subjects_keep_their_policy_even_with_shared_metadata(tall_entry, subject):
    entry, placement = tall_entry
    entry.subject = subject
    entry.passage_role = "passage_fragment"
    entry.passage_range = (36, 39)
    assert not board._explicit_shared_passage_prefers_text(entry.subject, entry.passage_role, entry.passage_range)
    assert build(entry, placement, generate=False).height_px <= 4096


@pytest.mark.parametrize("role,range_value", [
    (None, {"start": 36, "end": 39}), ("child_question", {"start": 36, "end": 39}),
    ("passage_fragment", {"start": 36, "end": 36}),
    ("passage_fragment", {"start": True, "end": 39}),
    ("passage_fragment", {"start": 36.0, "end": 39}),
    ("passage_fragment", {"start": 36, "end": 39.9}),
    ("passage_fragment", {"start": -1, "end": 39}),
])
def test_untrusted_or_nonshared_ui_ranges_do_not_enable_text_policy(tall_entry, role, range_value):
    import app_server
    existing, _ = tall_entry
    problem = {"id": "ordinary-stitched-graphic", "title": "지문 36~39", "subject": "unknown",
               "sourceFileName": "c42083a412b9453abcde.pdf", "sourcePageId": "upload-page-012",
               "imagePath": existing.crop_path.as_uri(), "boardRenderPath": existing.crop_path.as_uri(),
               "bbox": {"left": 0, "top": 0, "width": 1010, "height": 5695},
               "sourceSegments": existing.source_segments, "passageRole": role, "passageRange": range_value}
    entry = app_server._problems_to_entries([problem])[0]
    assert entry.subject == Subject.UNKNOWN and entry.passage_role is None and entry.passage_range is None
    placement = SimpleNamespace(metadata={"crop_path": str(entry.crop_path), "board_render_path": str(entry.board_render_path)},
                                start_y_pages=0, snapped_next_start_y_pages=10)
    assert build(entry, placement, generate=False).height_px <= board.V2_ENCODED_IMAGE_MAX_EDGE_PX


def test_neutral_unknown_cli_shared_asset_and_session_restore_use_explicit_text_provenance(tmp_path):
    import app_server
    import pymupdf
    # An actual PDF detection→S2 asset→V2 export→saved UI session→entry restore
    # regression. Subject and path deliberately contain no language hints.
    source = tmp_path / "c42083a412b9453abcde.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((40, 70), "[41~42] Read the passage and answer the questions.", fontsize=9)
        for x, rows in ((40, range(110, 731, 26)), (330, range(80, 731, 26))):
            for index, y in enumerate(rows):
                page.insert_text((x, y), f"Body paragraph {x} source line {index:02d}.", fontsize=10)
        page = document.new_page(width=600, height=800)
        for index, y in enumerate(range(80, 251, 26)):
            page.insert_text((40, y), f"Continuation ending source line {index:02d}.", fontsize=10)
        for number, top in ((41, 90), (42, 420)):
            page.insert_text((330, top), f"{number}. Which statement is true?", fontsize=12)
            for choice in range(1, 6):
                page.insert_text((330, top + 40 + choice * 25), f"{choice}) Answer choice", fontsize=10)
        document.save(source)
    with patch.object(board, "_render_problem_asset", wraps=board._render_problem_asset) as render:
        result = board.run_problem_export(
            source, output_dir=tmp_path / "out", subject_name="unknown", ocr="none",
            pdf_dpi=200, skip_crop=True, skip_deskew=True, export_edb=True,
            record_mode="image-only", crop_format=board.CROP_FORMAT_V2,
            content_target="shared-passages", processing_step="s2",
        )
    assert render.call_args_list
    assert all(call.args[0].subject == Subject.UNKNOWN and call.args[0].text_priority
               for call in render.call_args_list)
    session = json.loads(result["ui_session_path"].read_text())
    entries = app_server._problems_to_entries(json.loads(json.dumps(session["problems"])))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.subject == Subject.UNKNOWN and entry.passage_role == "passage_fragment"
    assert entry.passage_range == (41, 42) and len(entry.source_segments) > 1
    assert not board._problem_prefers_text_preservation(entry.subject, entry.source_path, entry.title)
    assert board._explicit_shared_passage_prefers_text(entry.subject, entry.passage_role, entry.passage_range)
    parsed = parse_edb(result["edb_path"])
    assert parsed.record_count_actual == 1
    image = parsed.records[0].embedded_images[0]
    assert 4096 < image.height <= board.V2_ENCODED_TEXT_MAX_HEIGHT_PX
    assert image.width <= 4096 and image.width * image.height <= 12_000_000
