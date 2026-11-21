"""Internal section numbering must not truncate an explicit shared range."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pymupdf
import pytest

import build_problem_board_edb as board
from layout_template_schema import LayoutTemplate
from structured_schema import Subject


def _render_pdf(source, tmp_path):
    prepared, pages = board.build_pages(source, subject=Subject.UNKNOWN, ocr_mode='none',
        ai_fallback_config=None, pdf_dpi=144, detect_perspective=False, deskew=False,
        crop_margins=False, max_dimension=None)
    entries = board.build_problem_entries(prepared, pages, tmp_path / 'out', LayoutTemplate(name='test'),
        content_target='shared-passages', render_board_assets=False)
    return prepared, pages, entries


@pytest.mark.parametrize('first', [7, 28])
def test_numbered_report_sections_before_true_child_do_not_cut_either_body_column(tmp_path, first):
    source = tmp_path / 'numbered-report.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.draw_line((300, 40), (300, 760), color=(0, 0, 0), width=.5)
        page.insert_text((40, 65), f'[{first}~{first+2}] Read the passage and answer the questions.', fontsize=9)
        for number, y in enumerate(range(105, 731, 25)):
            text = {3: '1. Survey methods and legal definitions.',
                    10: '2. Results and requested remedies.',
                    18: '1. Supplementary investigation results.'}.get(number, f'LEFT_BODY required reading line {number}.')
            page.insert_text((40, y), text, fontsize=10)
        for number, y in enumerate(range(80, 256, 25)):
            page.insert_text((330, y), f'RIGHT_BODY required reading line {number}.', fontsize=10)
        for number, y in zip(range(first, first+3), (330, 490, 650)):
            page.insert_text((330, y), f'{number}. Choose the correct answer.', fontsize=11)
            page.insert_text((340, y+35), '1) A question choice, never shared body.', fontsize=9)
        doc.save(source)
    prepared, pages, entries = _render_pdf(source, tmp_path)
    assert len(entries) == 1
    passage = entries[0]
    assert len(passage.source_segments) == 2
    left, right = passage.source_segments
    assert left['bbox']['top'] + left['bbox']['height'] >= 1450
    markers = pages[0].metadata['pdf_problem_markers']
    first_child = next(m for m in markers if m['number'] == first)['bbox']
    assert right['bbox']['top'] + right['bbox']['height'] < first_child['top']
    lines = [line for line in prepared[0].metadata['pdf_text_lines']
             if any(s in line['text'] for s in ('LEFT_BODY', 'RIGHT_BODY', 'Survey methods', 'Results and requested', 'Supplementary investigation'))]
    assert len(lines) >= 30
    for line in lines:
        box = line['bbox']
        assert any(seg['bbox']['left'] <= box['left']
                   and seg['bbox']['top'] <= box['top']
                   and seg['bbox']['left']+seg['bbox']['width'] >= box['right']
                   and seg['bbox']['top']+seg['bbox']['height'] >= box['bottom']
                   for seg in passage.source_segments), line['text']


@pytest.mark.parametrize('first, actual_child', [(1, 1), (7, 10)])
def test_real_first_question_or_next_range_stem_still_bounds_shared_body(tmp_path, first, actual_child):
    source = tmp_path / 'true-question-boundary.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 65), f'[{first}~{first+2}] Read the passage and answer the questions.', fontsize=9)
        for n, y in enumerate(range(105, 281, 25)):
            page.insert_text((40, y), f'Printed shared reading line {n}.', fontsize=10)
        page.insert_text((40, 360), f'{actual_child}. A real numbered question starts here.', fontsize=12)
        page.insert_text((40, 400), 'Question choice or prompt, never shared body.', fontsize=10)
        doc.save(source)
    _prepared, pages, entries = _render_pdf(source, tmp_path)
    assert len(entries) == 1
    marker = next(m for m in pages[0].metadata['pdf_problem_markers'] if m['number'] == actual_child)['bbox']
    for segment in entries[0].source_segments:
        assert segment['bbox']['top']+segment['bbox']['height'] < marker['top']


def test_real_march_grade11_numbered_report_and_underlined_other_passages(tmp_path, monkeypatch):
    source = os.environ.get('EDB_REAL_MARCH_G11_KR_PDF')
    if not source:
        pytest.skip('Set EDB_REAL_MARCH_G11_KR_PDF to the external original public March grade11 Korean PDF')
    import cv2
    import numpy as np
    from PIL import Image
    source = Path(source)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == 'ea1c5f215237ee95ed26483543f5e6837a7486b09c6bb5e2b9cbac1a96d07a4d'
    monkeypatch.setenv('EDB_PROBLEM_ASSET_WORKERS', '1')
    prepared, pages = board.build_pages(source, subject=Subject.UNKNOWN, ocr_mode='none',
        ai_fallback_config=None, pdf_dpi=200, detect_perspective=False, deskew=False,
        crop_margins=True, max_dimension=None)
    entries = board.build_problem_entries(prepared, pages, tmp_path / 'out', LayoutTemplate(name='test'),
        content_target='shared-passages', render_board_assets=False)
    report = next(e for e in entries if e.passage_range == (28, 30))
    assert len(report.source_segments) == 2
    left, right = report.source_segments
    assert left['bbox']['top']+left['bbox']['height'] >= 2600
    assert right['bbox']['top']+right['bbox']['height'] >= 800
    for target_range in ((11, 13), (28, 30), (39, 42)):
        entry = next(e for e in entries if e.passage_range == target_range)
        fragment = entry.source_segments[1]
        page = next(p for p in prepared if p.page_id == fragment['source_page_id'])
        box = fragment['bbox']
        lines = sorted([line for line in page.metadata['pdf_text_lines']
            if line['bbox']['left'] >= box['left'] and line['bbox']['top'] >= box['top']
            and line['bbox']['right'] <= box['left']+box['width']
            and line['bbox']['bottom'] <= box['top']+box['height']], key=lambda l:l['bbox']['top'])
        with Image.open(entry.crop_path) as raw:
            target = cv2.cvtColor(np.asarray(raw.convert('RGB')), cv2.COLOR_RGB2GRAY)
        required = lines[:2] + (lines[-2:] if target_range == (28, 30) else [])
        for line in required:
            b = line['bbox']
            pattern = cv2.cvtColor(np.asarray(page.image.crop((int(b['left']),int(b['top']),int(b['right'])+1,int(b['bottom'])+1)).convert('RGB')),cv2.COLOR_RGB2GRAY)
            assert cv2.matchTemplate(target,pattern,cv2.TM_CCOEFF_NORMED).max() > .99, line['text']
