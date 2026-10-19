"""A complete raw crop must not acquire a clipped <보기> in derived assets."""
import io
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from PIL import Image, ImageDraw, ImageFont

import app_server
from build_problem_board_edb import _build_image_only_record_image, _extract_problem_cutout
from image_reconstruction_backend import board_render_has_missing_tail


def boxed_question():
    source = Image.new("RGB", (600, 1000), "white")
    draw = ImageDraw.Draw(source)
    font = ImageFont.load_default(size=20)
    draw.text((45, 40), "4. A circuit and its graph", font=font, fill="black")
    draw.rectangle((90, 130, 510, 340), fill=(60, 120, 210), outline="black", width=2)
    draw.text((45, 405), "Choose every correct statement.", font=font, fill="black")
    draw.rectangle((35, 500, 565, 800), outline="black", width=2)
    draw.text((225, 505), "< examples >", font=font, fill="black")
    for y, text in [(555, "a. The resistance is 3 ohms."), (635, "b. The voltage is 50 volts."),
                    (715, "c. Current lags behind voltage."), (755, "   Keep this wrapped sentence.")]:
        draw.text((50, y), text, font=font, fill="black")
    draw.text((50, 855), "(1) a   (2) b   (3) a, b", font=font, fill="black")
    draw.text((50, 920), "(4) b, c   (5) a, b, c", font=font, fill="black")
    return source


@pytest.fixture
def legacy_problem(tmp_path):
    source = boxed_question()
    raw, board = tmp_path / "raw.png", tmp_path / "board.png"
    source.save(raw)
    damaged = _extract_problem_cutout(source)
    damaged.paste((0, 0, 0, 0), (0, 680, damaged.width, damaged.height))
    damaged.save(board)
    problem = {"id": "q4", "title": "4.", "subject": "science", "step": "s2",
               "imagePath": raw.as_uri(), "boardRenderPath": board.as_uri(),
               "sourcePageId": "page1", "sourceFileName": "physics.png",
               "bbox": {"left": 0, "top": 0, "width": 600, "height": 1000},
               "preserveMediaMode": "auto"}
    app_server._build_guarded_board_payload.cache_clear()
    return source, raw, board, problem


@pytest.mark.parametrize("short_canvas", [False, True])
def test_detects_missing_example_tail_on_transparent_or_shortened_canvas(short_canvas):
    source = boxed_question()
    damaged = _extract_problem_cutout(source)
    if short_canvas:
        damaged = damaged.crop((0, 0, 600, 680))
    else:
        damaged.paste((0, 0, 0, 0), (0, 680, 600, 1000))
    assert board_render_has_missing_tail(source, damaged)


def test_complete_downscaled_render_and_removed_page_number_are_not_rejected():
    source = boxed_question()
    complete = _extract_problem_cutout(source)
    assert not board_render_has_missing_tail(source, complete)
    assert not board_render_has_missing_tail(source, complete.resize((300, 500)))
    ImageDraw.Draw(source).text((292, 980), "2", fill="black")
    assert not board_render_has_missing_tail(source, complete)


def assert_complete_tail(image):
    alpha = image.convert("RGBA").getchannel("A")
    # Check the last <보기> row, lower border and both answer rows separately.
    for y0, y1 in [(715, 780), (798, 801), (855, 880), (920, 945)]:
        region = alpha.crop((round(40 * image.width / 600), round(y0 * image.height / 1000),
                             round(555 * image.width / 600), round(y1 * image.height / 1000)))
        assert region.getextrema()[1] > 180


@pytest.mark.parametrize("mode", ["auto", "grayscale", "off"])
def test_preview_repairs_tail_preserving_figure_policy_without_changing_files(legacy_problem, mode):
    source, raw, board, problem = legacy_problem
    problem["preserveMediaMode"] = mode
    before = raw.read_bytes(), board.read_bytes()
    payload = app_server._guarded_board_payload(problem, board)
    assert payload is not None
    with Image.open(io.BytesIO(payload)) as image:
        assert abs(image.width / image.height - source.width / source.height) < 0.001
        assert_complete_tail(image)
        pixel = image.getpixel((round(image.width / 2), round(image.height * 0.23)))
        if mode == "auto":
            assert pixel == (60, 120, 210, 255)
        elif mode == "grayscale":
            assert pixel[0] == pixel[1] == pixel[2] and pixel[3] == 255
    assert (raw.read_bytes(), board.read_bytes()) == before
    assert app_server._guarded_board_payload(problem, board) == payload
    assert app_server._build_guarded_board_payload.cache_info().hits == 1
    downloaded = app_server._session_problem_board_png_payload(problem, board_theme="green")
    with Image.open(io.BytesIO(downloaded)) as image:
        assert_complete_tail(image)


def test_preview_cache_rechecks_replaced_derived_asset_and_leaves_healthy_render_alone(legacy_problem):
    source, raw, board, problem = legacy_problem
    assert app_server._guarded_board_payload(problem, board) is not None
    _extract_problem_cutout(source).save(board)
    assert app_server._guarded_board_payload(problem, board) is None
    assert app_server._build_guarded_board_payload.cache_info().misses == 2


def test_s3_and_explicit_original_variant_are_not_replaced(legacy_problem):
    _source, raw, board, problem = legacy_problem
    problem["step"] = "s3"
    assert app_server._guarded_board_payload(problem, board) is None
    problem["step"] = "s2"
    problem["boardRenderPath"] = raw.as_uri()
    assert app_server._guarded_board_payload(problem, raw) is None


@pytest.mark.parametrize("max_dimension", [None, 384])
def test_actual_file_route_repairs_only_allowed_board_variant(legacy_problem, max_dimension):
    _source, raw, board, problem = legacy_problem
    handler = app_server.AppRequestHandler.__new__(app_server.AppRequestHandler)
    handler.server = SimpleNamespace(allowed_files={str(raw), str(board)},
        board_file_context=lambda path: (problem, "green") if path == str(board) else None)
    handler.wfile = io.BytesIO()
    statuses, headers = [], []
    handler.send_response = statuses.append
    handler.send_header = lambda k, v: headers.append((k, v))
    handler.end_headers = lambda: None
    handler.send_error = lambda status, *args: statuses.append(status)
    url = app_server.path_to_api_url(board)
    if max_dimension:
        url += f"&previewMax={max_dimension}"
    handler._handle_file(urlparse(url))
    assert statuses == [200]
    assert dict(headers)["Content-Type"] == "image/png"
    with Image.open(io.BytesIO(handler.wfile.getvalue())) as image:
        assert_complete_tail(image)
        assert max_dimension is None or max(image.size) == max_dimension
    handler.wfile = io.BytesIO()
    handler.server.allowed_files.remove(str(raw))
    handler._handle_file(urlparse(url))
    # The allowed original board can still be served, but an unapproved source
    # is never read to synthesize a response.
    with Image.open(io.BytesIO(handler.wfile.getvalue())) as image:
        assert image.convert("RGBA").getpixel((image.width // 2, image.height - 20))[3] == 0


@pytest.mark.parametrize("crop_format", ["v1", "v2"])
@pytest.mark.parametrize("trusted", [False, True])
def test_final_encoded_edb_also_recovers_tail_from_legacy_render(legacy_problem, crop_format, trusted):
    _source, raw, board, problem = legacy_problem
    entry = app_server._problems_to_entries([problem])[0]
    entry.board_render_preprocessed = trusted
    placement = SimpleNamespace(metadata={"crop_path": str(raw), "board_render_path": str(board)})
    encoded = _build_image_only_record_image(placement, entry, dark_board=True,
        board_theme="green", crop_format=crop_format, target_image_width_px=0, continuous_flow=False)
    with Image.open(io.BytesIO(encoded.image_bytes)) as image:
        assert_complete_tail(image)
