"""Join trimming must retain glyphs even when adjacent rules resemble chrome."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import build_problem_board_edb as board


def _colored_count(image, color):
    return sum(pixel == color for pixel in image.get_flattened_data())


@pytest.mark.parametrize('use_numpy', [True, False])
@pytest.mark.parametrize('transparent', [False, True])
@pytest.mark.parametrize('end', ['top', 'bottom'])
def test_underlined_body_lines_survive_both_ends_of_join(monkeypatch, use_numpy, transparent, end):
    if not use_numpy:
        monkeypatch.setattr(board, 'np', None)
    mode = 'RGBA' if transparent else 'RGB'
    background = (255, 255, 255, 0) if transparent else 'white'
    color = (30, 50, 150, 255) if transparent else (30, 50, 150)
    black = (0, 0, 0, 255) if transparent else 'black'
    image = Image.new(mode, (400, 600), background)
    draw = ImageDraw.Draw(image)
    if end == 'top':
        draw.text((30, 35), 'FIRST PRINTED READING LINE', fill=color, font_size=20)
        draw.text((30, 70), 'SECOND PRINTED READING LINE', fill=color, font_size=20)
        draw.line((25, 100, 375, 100), fill=black, width=3)
        draw.text((30, 150), 'The reading body continues.', fill=black, font_size=20)
    else:
        draw.text((30, 50), 'Earlier paragraph.', fill=black, font_size=20)
        draw.text((30, 450), 'LAST PRINTED READING LINE', fill=color, font_size=20)
        draw.line((25, 480, 375, 480), fill=black, width=3)
    boxes = []
    trimmed = board._trim_passage_segment_for_join(image, trim_top=end == 'top', trim_bottom=end == 'bottom',
        crop_box_output=boxes)
    assert _colored_count(image, color) > 100
    assert _colored_count(trimmed, color) == _colored_count(image, color)


@pytest.mark.parametrize('use_numpy', [True, False])
@pytest.mark.parametrize('position', ['prefix', 'suffix', 'blank-before-footer'])
def test_single_pixel_dot_and_two_pixel_minus_are_never_treated_as_blank(monkeypatch, use_numpy, position):
    if not use_numpy:
        monkeypatch.setattr(board, 'np', None)
    image = Image.new('RGBA', (400, 600), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.text((30, 150 if position == 'prefix' else 50), 'Reading body.', fill=(0, 0, 0, 255), font_size=20)
    rule_y = 120 if position == 'prefix' else 500
    draw.line((10, rule_y, 390, rule_y), fill=(0, 0, 0, 255), width=2)
    glyph_y = {'prefix': 20, 'suffix': 570, 'blank-before-footer': 300}[position]
    # A faint visible dot and a two-pixel minus are meaningful independent
    # glyph components, even below the existing row-count whitespace threshold.
    dot = (30, 80, 170, 17)
    minus = (30, 80, 170, 32)
    image.putpixel((40, glyph_y), dot)
    image.putpixel((50, glyph_y + 5), minus)
    image.putpixel((51, glyph_y + 5), minus)
    trimmed = board._trim_passage_segment_for_join(image,
        trim_top=position == 'prefix', trim_bottom=position != 'prefix')
    assert _colored_count(trimmed, dot) == 1
    assert _colored_count(trimmed, minus) == 2


@pytest.mark.parametrize('use_numpy', [True, False])
def test_thin_full_height_outer_guides_do_not_impersonate_text_above_bare_rule(monkeypatch, use_numpy):
    if not use_numpy:
        monkeypatch.setattr(board, 'np', None)
    image = Image.new('RGB', (400, 600), 'white')
    draw = ImageDraw.Draw(image)
    draw.line((2, 0, 2, 599), fill='black')
    draw.line((397, 0, 397, 599), fill='black')
    draw.line((10, 120, 390, 120), fill='black', width=2)
    draw.text((30, 170), 'Actual continuation text.', fill='blue', font_size=20)
    assert not board._passage_band_has_substantive_ink(image, top=0, bottom=119)
    trimmed = board._trim_passage_segment_for_join(image, trim_top=True, trim_bottom=False)
    assert trimmed.height < image.height
    assert _colored_count(trimmed, (0, 0, 255)) == _colored_count(image, (0, 0, 255))


def test_bare_bottom_separator_and_blank_padding_can_still_be_removed():
    image = Image.new('RGB', (400, 600), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((30, 300), 'Retained reading body.', fill='blue', font_size=20)
    draw.line((10, 500, 390, 500), fill='black', width=2)
    trimmed = board._trim_passage_segment_for_join(image, trim_top=False, trim_bottom=True)
    assert trimmed.height < 400
    assert _colored_count(trimmed, (0, 0, 255)) == _colored_count(image, (0, 0, 255))


def test_real_june_korean_underlined_continuation_first_two_lines(tmp_path, monkeypatch):
    source = os.environ.get('EDB_REAL_JUNE_KR_PDF')
    if not source:
        pytest.skip('Set EDB_REAL_JUNE_KR_PDF to the external original public June grade11 Korean PDF')
    import hashlib
    import cv2
    import numpy as np
    from structured_schema import Subject
    from layout_template_schema import LayoutTemplate
    source = Path(source)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == '458b8502dfeb0697c5229f317ff4df9f81e66c11c0046750020c04ed29b74152'
    monkeypatch.setenv('EDB_PROBLEM_ASSET_WORKERS', '1')
    prepared, pages = board.build_pages(source, subject=Subject.UNKNOWN, ocr_mode='none',
        ai_fallback_config=None, pdf_dpi=200, detect_perspective=False, deskew=False,
        crop_margins=True, max_dimension=None)
    entries = board.build_problem_entries(prepared, pages, tmp_path / 'out', LayoutTemplate(name='test'),
        content_target='shared-passages', render_board_assets=False)
    passage = next(entry for entry in entries if entry.passage_range == (11, 15))
    right = passage.source_segments[1]
    page = next(p for p in prepared if p.page_id == right['source_page_id'])
    bounds = right['bbox']
    lines = sorted([line for line in page.metadata['pdf_text_lines']
        if line['bbox']['left'] >= bounds['left'] and line['bbox']['top'] >= bounds['top']
        and line['bbox']['bottom'] <= bounds['top'] + bounds['height']], key=lambda line:line['bbox']['top'])
    with Image.open(passage.crop_path) as raw:
        target = cv2.cvtColor(np.asarray(raw.convert('RGB')), cv2.COLOR_RGB2GRAY)
    for line in lines[:2]:
        b = line['bbox']
        patch = page.image.crop((int(b['left']), int(b['top']), int(b['right']) + 1, int(b['bottom']) + 1)).convert('RGB')
        pattern = cv2.cvtColor(np.asarray(patch), cv2.COLOR_RGB2GRAY)
        assert cv2.matchTemplate(target, pattern, cv2.TM_CCOEFF_NORMED).max() > .99, line['text']


@pytest.mark.parametrize('use_numpy', [True, False])
@pytest.mark.parametrize('end', ['top', 'bottom'])
@pytest.mark.parametrize('shape', ['vertical-stroke', 'bracket'])
def test_short_vertical_glyphs_inside_margin_are_not_full_segment_guides(monkeypatch, use_numpy, end, shape):
    if not use_numpy:
        monkeypatch.setattr(board, 'np', None)
    image = Image.new('RGB', (400, 600), 'white')
    draw = ImageDraw.Draw(image)
    blue = (30, 50, 150)
    first, last = (5, 115) if end == 'top' else (485, 595)
    draw.line((40, first, 40, last), fill=blue, width=2)
    if shape == 'bracket':
        draw.line((40, first, 55, first), fill=blue, width=2)
        draw.line((40, last, 55, last), fill=blue, width=2)
    rule = 120 if end == 'top' else 470
    draw.line((10, rule, 390, rule), fill='black', width=2)
    draw.text((30, 170 if end == 'top' else 50), 'Reading body.', fill='black', font_size=20)
    before = _colored_count(image, blue)
    assert before >= 222
    trimmed = board._trim_passage_segment_for_join(image, trim_top=end == 'top', trim_bottom=end == 'bottom')
    assert _colored_count(trimmed, blue) == before


@pytest.mark.parametrize('use_numpy', [True, False])
@pytest.mark.parametrize('end', ['top', 'bottom'])
@pytest.mark.parametrize('position', ['adjacent-column', 'same-column-color', 'same-column-gap'])
def test_dot_on_or_beside_edge_guide_keeps_its_independent_ink(monkeypatch, use_numpy, end, position):
    if not use_numpy:
        monkeypatch.setattr(board, 'np', None)
    image = Image.new('RGB', (400, 600), 'white')
    draw = ImageDraw.Draw(image)
    start = 5 if position == 'same-column-gap' and end == 'top' else 0
    finish = 594 if position == 'same-column-gap' and end == 'bottom' else 599
    draw.line((2, start, 2, finish), fill='black')
    rule = 120 if end == 'top' else 470
    draw.line((10, rule, 390, rule), fill='black', width=2)
    draw.text((30, 170 if end == 'top' else 50), 'Reading body.', fill='black', font_size=20)
    dot_x = 3 if position == 'adjacent-column' else 2
    dot_y = (1 if end == 'top' else 599) if position == 'same-column-gap' else (20 if end == 'top' else 570)
    dot = (30, 80, 170)
    image.putpixel((dot_x, dot_y), dot)
    trimmed = board._trim_passage_segment_for_join(image, trim_top=end == 'top', trim_bottom=end == 'bottom')
    assert _colored_count(trimmed, dot) == 1


def test_numpy_free_large_blank_scan_has_no_python_matrix_allocation(monkeypatch):
    import tracemalloc
    monkeypatch.setattr(board, 'np', None)
    # The former list-of-list reference matrix alone used >8MiB here.
    # Native PIL raster storage is independent of this Python allocation bound.
    image = Image.new('RGB', (800, 1200), 'white')
    tracemalloc.start()
    try:
        assert not board._passage_band_has_substantive_ink(image, top=0, bottom=image.height)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
