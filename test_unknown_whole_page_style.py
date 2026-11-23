"""Whole-page source preservation follows explicit intent, without guessing a subject."""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest

import app_server
import build_problem_board_edb as board
from structured_schema import Subject
from test_figure_glyph_shape import shaded_figure


def page_source():
    source, _holes, _stroke = shaded_figure()
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 2, 2), fill='black')
    draw.rectangle((1021, 597, 1023, 599), fill='black')
    draw.rectangle((300, 300, 380, 360), fill=(30, 90, 190))
    return source


def page_entry(tmp_path, source, **overrides):
    path = tmp_path / 'd230a9b5-page-005.png'
    source.save(path)
    payload = {'id': 'page-005', 'title': '페이지 5', 'subject': 'unknown',
               'sourceFileName': path.name, 'sourcePageId': 'page-005',
               'imagePath': path.as_uri(), 'boardRenderPath': path.as_uri(),
               'bbox': {'left': 0, 'top': 0, 'width': source.width, 'height': source.height},
               'processingStep': 's2', 'preserveMediaMode': 'off',
               'inputIntent': 'page-as-is', 'forceFullPageBounds': True,
               'placementMode': 'continuous-page-as-is', **overrides}
    return app_server._problems_to_entries([payload])[0]


def native_image(entry, *, crop_format='v1'):
    placement = SimpleNamespace(metadata={'crop_path': str(entry.crop_path),
                                         'board_render_path': str(entry.board_render_path)},
                                start_y_pages=0, snapped_next_start_y_pages=3)
    encoded = board._build_image_only_record_image(
        placement, entry, dark_board=True, board_theme='charcoal', crop_format=crop_format,
        target_image_width_px=0, continuous_flow=False)
    with Image.open(BytesIO(encoded.image_bytes)) as image:
        return image.convert('RGBA')


@pytest.mark.parametrize('crop_format', ['v1', 'v2'])
@pytest.mark.parametrize('media', ['off', 'auto'])
def test_unknown_whole_page_matches_declared_text_subject_and_retains_source_components(tmp_path, crop_format, media):
    source = page_source()
    unknown = page_entry(tmp_path, source, preserveMediaMode=media)
    assert unknown.subject == Subject.UNKNOWN
    assert not board._problem_prefers_text_preservation(unknown.subject, unknown.source_path, unknown.title)
    actual = native_image(unknown, crop_format=crop_format)
    known = page_entry(tmp_path, source, subject='korean', preserveMediaMode=media)
    expected = native_image(known, crop_format=crop_format)
    assert actual.size == source.size
    assert actual.tobytes() == expected.tobytes()
    assert unknown.subject == Subject.UNKNOWN
    source_mask = np.any(np.asarray(source) < 200, axis=2).astype(np.uint8)
    supported = cv2.dilate((np.asarray(actual)[..., 3] > 16).astype(np.uint8), np.ones((3, 3), np.uint8))
    count, labels, _stats, _ = cv2.connectedComponentsWithStats(source_mask, connectivity=8)
    for component in range(1, count):
        assert supported[labels == component].mean() >= .9
    assert actual.getpixel((0, 0))[3] == 255
    assert actual.getpixel((1023, 599))[3] == 255


@pytest.mark.parametrize('crop_format', ['v1', 'v2'])
def test_unknown_whole_page_initial_asset_seeds_the_same_publish_cache(tmp_path, crop_format):
    source = page_source()
    entry = page_entry(tmp_path, source)
    task = board._ProblemAssetTask(source_image=source, bounds=entry.bounds,
        crop_path=entry.crop_path, board_render_path=entry.board_render_path,
        chalk_color=board._resolve_chalk_color('charcoal'), subject=Subject.UNKNOWN,
        text_priority=False, preserve_page_content=True)
    preview = board._build_problem_board_asset(source, task)
    with patch.object(board, '_extract_problem_cutout', side_effect=AssertionError('publish missed initial whole-page cache')):
        actual = native_image(entry, crop_format=crop_format)
    assert actual.tobytes() == preview.tobytes()
    assert len(list((tmp_path / board.CHALK_CUTOUT_CACHE_DIRNAME).glob('*.png'))) == 1


@pytest.mark.parametrize('crop_format', ['v1', 'v2'])
def test_unknown_whole_page_keeps_selected_media_overlay(tmp_path, crop_format):
    source = page_source()
    region = {'kind': 'image', 'source': 'pdf_image_block', 'confidence': .99,
              'bbox': {'left': 300, 'top': 300, 'right': 381, 'bottom': 361, 'width': 81, 'height': 61}}
    entry = page_entry(tmp_path, source, preserveMediaMode='auto', preserveMediaRegions=[region])
    assert native_image(entry, crop_format=crop_format).getpixel((340, 330)) == (30, 90, 190, 255)


@pytest.mark.parametrize('overrides', [
    {'subject': 'math', 'inputIntent': 'multi-problem', 'forceFullPageBounds': False},
    {'inputIntent': 'multi-problem', 'forceFullPageBounds': False},
    {'subject': 'math', 'fitWidthConverted': True},
])
def test_ordinary_diagram_rendering_does_not_become_text_priority(tmp_path, overrides):
    entry = page_entry(tmp_path, page_source(), **overrides)
    with patch.object(board, '_cached_chalk_cutout', wraps=board._cached_chalk_cutout) as cutout:
        native_image(entry)
    assert not cutout.call_args.kwargs['text_priority']
    assert not cutout.call_args.kwargs['preserve_page_content']


def test_unknown_whole_page_reconstruction_policy_is_unchanged(tmp_path):
    source = page_source()
    entry = page_entry(tmp_path, source, processingStep='s3')
    with patch.object(board, '_build_transparent_reconstruction_image', return_value=source.convert('RGBA')) as reconstruct:
        native_image(entry)
    assert reconstruct.call_args.kwargs['text_priority'] is False
