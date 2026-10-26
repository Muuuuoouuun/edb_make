from __future__ import annotations

import errno
import math
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from PIL import Image, ImageChops, ImageDraw

import preprocess
import build_problem_board_edb as board
from layout_template_schema import LayoutTemplate
from structured_schema import BlockType, Subject


def make_pdf(path: Path, count: int, *, badges: bool = False, layers: bool = False) -> None:
    with pymupdf.open() as document:
        hidden = document.add_ocg("teacher answers", on=False) if layers else 0
        for index in range(count):
            page = document.new_page(width=300, height=420)
            page.insert_text((30, 60), f"{index + 1:02d}" if badges else f"Page {index + 1}", fontsize=12)
            page.draw_rect((30, 90, 260, 180), color=(0, 0, 0))
            if layers:
                page.draw_rect((90, 110, 210, 160), color=(1, 0, 0), fill=(1, 0, 0), oc=hidden)
                if index % 3 == 1:
                    page.set_rotation(90)
        document.save(path)


def test_pdf_batches_keep_all_pages_order_geometry_and_hidden_layers(tmp_path):
    source = tmp_path / "한글 layered.pdf"
    make_pdf(source, 23, layers=True)
    original = preprocess._render_pdf_document_pages
    calls = []

    def render(document, source_path, target, dpi, first, last, watermark_xrefs=frozenset()):
        calls.append((first, last))
        return original(document, source_path, target, dpi, first, last)

    with patch.object(preprocess, "_render_pdf_document_pages", side_effect=render):
        pages = preprocess.render_pdf_pages(source, tmp_path / "out", dpi=72)
    assert calls == [(0, 10), (10, 20), (20, 23)]
    assert [page.page_index for page in pages] == list(range(23))
    assert len({page.page_id for page in pages}) == 23
    with pymupdf.open(source) as document:
        for page, rendered in zip(document, pages):
            pixmap = page.get_pixmap(alpha=False)
            expected = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            with Image.open(rendered.normalized_path) as actual:
                assert ImageChops.difference(expected, actual.convert("RGB")).getbbox() is None
            assert rendered.source_path == str(source)


def test_pdf_memory_failure_halves_ranges_to_single_pages_without_duplicates(tmp_path):
    source = tmp_path / "retry.pdf"
    make_pdf(source, 13)
    original = preprocess._render_pdf_document_pages
    ranges = []

    def render(document, source_path, target, dpi, first, last, watermark_xrefs=frozenset()):
        ranges.append((first, last))
        if last - first > 2:
            raise MemoryError("fixture allocation failure")
        return original(document, source_path, target, dpi, first, last)

    with patch.object(preprocess, "_render_pdf_document_pages", side_effect=render):
        pages = preprocess.render_pdf_pages(source, tmp_path / "out", dpi=72)
    assert ranges[0] == (0, 10)
    assert any(last - first == 1 for first, last in ranges)
    assert [page.page_index for page in pages] == list(range(13))
    assert len({page.normalized_path for page in pages}) == 13


def test_pdf_one_page_memory_failure_names_exact_file_and_page(tmp_path):
    source = tmp_path / "allocation.pdf"
    make_pdf(source, 2)
    with patch.object(preprocess, "_render_pdf_document_pages", side_effect=OSError(errno.ENOMEM, "allocation failed")):
        with pytest.raises(preprocess.PdfPageProcessingError) as caught:
            preprocess.render_pdf_pages(source, tmp_path / "out", dpi=72)
    error = caught.value
    assert error.code == "pdf_resource_exhausted"
    assert (error.source_file, error.page_start, error.page_end) == (source.name, 1, 1)
    assert isinstance(error.cause, OSError)


def test_pdf_program_failure_is_not_retried_as_size_problem(tmp_path):
    source = tmp_path / "broken-renderer.pdf"
    make_pdf(source, 11)
    with patch.object(preprocess, "_render_pdf_document_pages", side_effect=ValueError("renderer bug")) as render:
        with pytest.raises(preprocess.PdfPageProcessingError) as caught:
            preprocess.render_pdf_pages(source, tmp_path / "out", dpi=72)
    assert render.call_count == 1
    assert caught.value.code == "pdf_page_processing_failed"
    assert (caught.value.page_start, caught.value.page_end) == (1, 10)


def test_large_pdf_prepared_images_stay_disk_backed_and_badge_tail_is_promoted(tmp_path):
    source = tmp_path / "badges.pdf"
    make_pdf(source, 12, badges=True)
    prepared = preprocess.prepare_source_pages(source, pdf_dpi=72, deskew=False, crop_margins=False)
    assert len(prepared) == 12
    assert all(page.disk_backed and page._image is None for page in prepared)
    assert [page.metadata["pdf_problem_markers"][0]["number"] for page in prepared] == list(range(1, 13))
    assert all(page.metadata["pdf_problem_marker_source"] == "badge" for page in prepared)
    assert [page.page_number for page in prepared] == list(range(1, 13))
    assert all(page.metadata["original_source_path"] == str(source) for page in prepared)
    assert prepared[0].size == (300, 420)
    assert prepared[0]._image is None
    image = prepared[0].image
    assert image.size == (300, 420)
    prepared[0].release_image()
    assert prepared[0]._image is None


def test_large_pdf_segmentation_runs_bounded_batches_and_releases_images(tmp_path):
    source = tmp_path / "recognition.pdf"
    make_pdf(source, 23, badges=True)
    prepared = preprocess.prepare_source_pages(source, pdf_dpi=72, deskew=False, crop_margins=False)
    batch_sizes = []
    original = board.build_page_models_for_prepared_pages

    def segment(batch, **kwargs):
        assert sum(page._image is not None for page in prepared) == 0
        batch_sizes.append(len(batch))
        return original(batch, **kwargs)

    with patch.object(board, "prepare_source_pages", return_value=prepared), patch.object(board, "build_page_models_for_prepared_pages", side_effect=segment):
        actual_prepared, models = board.build_pages(source, subject=Subject.MATH, ocr_mode="none", ai_fallback_config=None, pdf_dpi=72, detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None)
    assert batch_sizes == [10, 10, 3]
    assert [model.page_id for model in models] == [page.page_id for page in prepared]
    assert all(page._image is None for page in actual_prepared)


def test_full_page_vertical_slices_are_lossless_and_every_edb_stays_below_50(tmp_path):
    template = LayoutTemplate(name="academy-default")
    width = 32
    max_height = math.floor(49 * board.CANVAS_WIDTH * width / max(board._v1_default_display_width_px(template), board.V2_TARGET_IMAGE_WIDTH_PX, board.CANVAS_HEIGHT - board.LEFT_MARGIN_PX - board.RIGHT_PADDING_PX))
    height = max_height * 2 + 17
    source = tmp_path / "long-page.png"
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for row in range(height):
        draw.line((0, row, width - 1, row), fill=(row % 251, (row * 3) % 251, (row * 7) % 251))
    image.save(source)
    prepared = preprocess.PreparedPage("long", str(source), 7, None, image.size, {"original_source_path": str(source)})
    slices = board._split_tall_page_as_is_prepared_pages([prepared])
    assert len(slices) == 3
    combined = Image.new("RGB", image.size)
    cursor = 0
    for fragment in slices:
        assert fragment.page_number == 7
        assert fragment.metadata["original_source_path"] == str(source)
        combined.paste(fragment.image, (0, cursor))
        cursor += fragment.size[1]
        fragment.release_image()
    assert cursor == height
    assert ImageChops.difference(image, combined).getbbox() is None
    models = board._build_page_as_is_models(slices, subject=Subject.UNKNOWN)
    entries = board.build_problem_entries(slices, models, tmp_path / "entries", template, render_board_assets=False)
    chunks = board.split_problem_entries_for_classin_page_limit(entries, template)
    assert [entry.problem_id for chunk in chunks for entry in chunk] == [entry.problem_id for entry in entries]
    for crop_format in (board.CROP_FORMAT_V1, board.CROP_FORMAT_V2):
        parts = board.plan_classin_limited_edb_parts(entries, template, tmp_path / f"parts-{crop_format}", record_mode="image-only", text_confidence_threshold=0.78, dark_board=False, board_theme=board.DEFAULT_BOARD_THEME, crop_format=crop_format)
        assert sum(part["problemCount"] for part in parts) == len(entries)
        for part in parts:
            assert part["estimatedFlowEndPages"] <= 50


def test_large_pdf_exports_encoded_records_one_classin_part_at_a_time(tmp_path):
    source = tmp_path / "large.pdf"
    make_pdf(source, 23)
    original = board.build_records
    generated_sizes = []

    def record_batch(entries, *args, **kwargs):
        if kwargs.get("generate_records", True):
            generated_sizes.append(len(entries))
        return original(entries, *args, **kwargs)

    with patch.object(board, "build_records", side_effect=record_batch):
        result = board.run_problem_export(source, output_dir=tmp_path / "export", ocr="none", pdf_dpi=72,
            skip_crop=True, skip_deskew=True, record_mode="image-only", input_intent="page-as-is", dark_board=False)
    parts = result["edb_parts"]
    assert len(parts) > 1
    assert sum(part["recordCount"] for part in parts) == 23
    assert result["summary"]["record_count"] == 23
    assert max(generated_sizes) < 23
    assert all(part["flowEndPages"] <= 50 and part["pageCountHint"] == 50 for part in parts)
    assert len({problem for part in parts for problem in part["problemIds"]}) == 23
    assert all(Path(part["edbPath"]).is_file() for part in parts)


def test_mixed_geometry_planning_never_encodes_records_or_reloads_pages(tmp_path):
    source = tmp_path / "mixed.pdf"
    make_pdf(source, 2)
    prepared, models = board.build_pages(source, subject=Subject.UNKNOWN, ocr_mode="none", ai_fallback_config=None,
        pdf_dpi=72, detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None, input_intent="page-as-is")
    template = LayoutTemplate(name="academy-default", board_page_count=50)
    entries = board.build_problem_entries(prepared, models, tmp_path / "entries", template, render_board_assets=False)
    # Exercise both record kinds while retaining the same entry geometry.
    entries[0].blocks[0].block_type = BlockType.STEM
    entries[0].blocks[0].text = "Text record content"
    entries[0].blocks[0].confidence = 1.0
    entries[0].blocks[0].metadata.pop("force_image_record", None)
    with patch.object(board, "_encode_image_bytes", side_effect=AssertionError("geometry plan encoded an image")), patch.object(board, "build_text_record", side_effect=AssertionError("geometry plan encoded text")):
        records, placements, _header = board.build_records(entries, template, record_mode="mixed", output_dir=tmp_path,
            text_confidence_threshold=0.78, dark_board=False, generate_records=False, expand_board_capacity=False)
    assert records == []
    assert len(placements) == 2
    assert sum(placement["image_record_count"] for placement in placements) == 1
    assert sum(placement["text_record_count"] for placement in placements) == 1
    assert template.board_page_count == 50


def test_real_session_publish_keeps_encoded_records_in_classin_parts(tmp_path):
    import app_server

    source = tmp_path / "publish-source.pdf"
    make_pdf(source, 23)
    imported = board.run_problem_export(source, output_dir=tmp_path / "session", ocr="none", pdf_dpi=72,
        skip_crop=True, skip_deskew=True, record_mode="image-only", input_intent="page-as-is", export_edb=False)

    class Server:
        def __init__(self, session):
            self.latest_session = session

        def remember_session(self, session):
            self.latest_session = session

    handler = object.__new__(app_server.AppRequestHandler)
    handler.server = Server(imported["ui_session"])
    handler._read_json_body = lambda: {"edbName": "published-large.edb"}
    responses = []
    handler._send_json = lambda payload, **kwargs: responses.append(payload)
    original = board.build_records
    encoded_batches = []
    geometry_batches = []

    def tracked(entries, *args, **kwargs):
        (encoded_batches if kwargs.get("generate_records", True) else geometry_batches).append(len(entries))
        return original(entries, *args, **kwargs)

    with patch.object(board, "build_records", side_effect=tracked):
        handler._handle_session_publish()
    assert len(responses) == 1
    response = responses[0]
    assert response["ok"], response
    assert geometry_batches[0] == 23
    assert max(encoded_batches) < 23
    assert sum(encoded_batches) == 23
    assert response["publishSummary"]["recordCount"] == 23
    parts = response["session"]["edbParts"]
    assert len(parts) > 1
    assert sum(part["recordCount"] for part in parts) == 23
    assert all(part["pageCountHint"] == 50 and part["flowEndPages"] <= 50 for part in parts)
    assert all(Path(part["edbPath"]).is_file() for part in parts)
