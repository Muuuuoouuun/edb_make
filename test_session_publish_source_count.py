"""Final publishing retains original page provenance without re-segmenting."""
from __future__ import annotations

import copy
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

import app_server
import build_problem_board_edb as board
from inspect_edb import parse_edb


@pytest.mark.parametrize('count_mode', ['original', 'tiled', 'missing', 'invalid-bool', 'invalid-fraction'])
def test_actual_publish_preserves_original_page_count_or_unique_inventory_fallback(tmp_path, count_mode):
    source = tmp_path / 'source.png'
    image = Image.new('RGB', (160, 220), 'white')
    draw = ImageDraw.Draw(image)
    for y in range(30, 180, 25):
        draw.text((15, y), 'Whole printed page.', fill='black')
    image.save(source)
    preview = board.run_problem_export(source, output_dir=tmp_path / 'work', subject_name='unknown',
        ocr='none', export_edb=False, input_intent='page-as-is', record_mode='image-only')['ui_session']
    assert preview['source_page_count'] == 1
    expected_count = preview['source_page_count']
    if count_mode != 'original':
        # A single original page can have multiple inventory tiles. Duplicate
        # inventory entries are also not separate original source pages.
        first = preview['pages'][0]
        first['pageTileParentPageId'] = 'original-page-1'
        second = copy.deepcopy(first)
        second.update(id='second-tile', problemIds=[])
        preview['pages'].extend([second, copy.deepcopy(second)])
    if count_mode == 'missing': preview.pop('source_page_count')
    elif count_mode == 'invalid-bool': preview['source_page_count'] = True
    elif count_mode == 'invalid-fraction': preview['source_page_count'] = 8.5
    responses = []
    server = SimpleNamespace(latest_session=preview)
    server.remember_session = lambda session: setattr(server, 'latest_session', session)
    handler = object.__new__(app_server.AppRequestHandler)
    handler.server = server
    handler._read_json_body = lambda: {}
    handler._send_json = lambda body, **kwargs: responses.append((body, kwargs))
    handler._handle_session_publish()
    assert len(responses) == 1
    response, options = responses[0]
    assert response['ok'], (response, options)
    final = server.latest_session
    assert response['session']['source_page_count'] == expected_count
    assert final['source_page_count'] == expected_count
    assert len(final['pages']) == len(preview['pages'])
    assert final['pages'][0]['id'] == preview['pages'][0]['id']
    assert final['publishSummary']['recordCount'] == 1
    from pathlib import Path
    assert parse_edb(Path(final['edb_path'])).record_count_actual == 1


@pytest.mark.parametrize('remove_group', [False, True])
def test_actual_shared_publish_rebuilds_groups_from_retained_units(tmp_path, remove_group):
    import pymupdf
    source = tmp_path / 'two-shared-groups.pdf'
    with pymupdf.open() as doc:
        for first in (1, 3):
            page = doc.new_page(width=600, height=800)
            page.insert_text((40, 70), f'[{first}~{first + 1}] Read the passage and answer the questions.', fontsize=10)
            for n, y in enumerate(range(110, 300, 22)):
                page.insert_text((40, y), f'Shared printed body {first} line {n}.', fontsize=10)
            page.insert_text((40, 370), f'{first}. What is the title?', fontsize=12)
            page.insert_text((40, 560), f'{first + 1}. Which statement is true?', fontsize=12)
        doc.save(source)
    preview = board.run_problem_export(source, output_dir=tmp_path / 'work', subject_name='unknown',
        pdf_dpi=144, ocr='none', export_edb=False, record_mode='image-only',
        input_intent='multi-problem', content_target='shared-passages')['ui_session']
    assert preview['source_page_count'] == 2
    assert preview['passageGroupCount'] == 2
    expected_groups = 1 if remove_group else 2
    retained_ids = {p['id'] for p in preview['problems'][:expected_groups]}
    excluded_id = preview['problems'][-1]['id']
    responses = []
    server = SimpleNamespace(latest_session=preview)
    server.remember_session = lambda session: setattr(server, 'latest_session', session)
    handler = object.__new__(app_server.AppRequestHandler)
    handler.server = server
    handler._read_json_body = lambda: {'excluded': [excluded_id]} if remove_group else {}
    handler._send_json = lambda body, **kwargs: responses.append((body, kwargs))
    handler._handle_session_publish()
    response, options = responses[0]
    assert response['ok'], (response, options)
    final = server.latest_session
    assert final['source_page_count'] == preview['source_page_count']
    assert final['passage_group_count'] == final['passageGroupCount'] == expected_groups
    assert final['passage_groups'] == final['passageGroups']
    assert {pid for group in final['passageGroups'] for pid in group['fragmentProblemIds']} == retained_ids
    assert all(group['missingChildProblemNumbers'] == [] for group in final['passageGroups'])
    assert all(group['missingChildProblemCount'] == 0 for group in final['passageGroups'])
    for snake, camel in [('passage_problem_count', 'passageProblemCount'),
                         ('cross_page_passage_group_count', 'crossPagePassageGroupCount'),
                         ('passage_review_item_count', 'passageReviewItemCount'),
                         ('cross_page_passage_review_item_count', 'crossPagePassageReviewItemCount')]:
        assert final[snake] == final[camel]
    assert final['passageReviewItemCount'] == 0
    assert all('passage_missing_child_questions' not in p['riskFlags'] for p in final['problems'])
    assert response['session']['passageGroupCount'] == expected_groups
    assert final['publishSummary']['passageGroupCount'] == expected_groups
    from pathlib import Path
    assert parse_edb(Path(final['edb_path'])).record_count_actual == expected_groups
