"""Intentional shared-only page omissions must not hide recognition failures."""
from __future__ import annotations


from PIL import Image
import pymupdf
import pytest

import build_problem_board_edb as board
from inspect_edb import parse_edb
from preprocess import PreparedPage
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject


def _question_page(tmp_path):
    path = tmp_path / 'page.png'
    image = Image.new('RGB', (600, 800), 'white')
    image.save(path)
    prepared = PreparedPage('page', str(path), 1, image, image.size)
    block = ContentBlock('q-block', BlockType.STEM, Box(20, 20, 500, 200), 0,
                         text='7. Choose the correct answer.', confidence=0.99)
    question = ProblemUnit('q7', Subject.UNKNOWN, '7.', stem_block_ids=['q-block'],
                           metadata={'problem_number': 7})
    page = PageModel('page', 600, 800, Subject.UNKNOWN, str(path), [block], [question])
    return prepared, page


def _session(tmp_path, prepared, page, target='shared-passages'):
    return board.build_ui_session([prepared], [], tmp_path, None, [prepared.source_path],
        record_mode='image-only', pages=[page] if page is not None else [], content_target=target)


def test_shared_only_confident_question_page_is_normal_and_survives_http_restore(tmp_path):
    from app_server import rewrite_session_for_http
    prepared, page = _question_page(tmp_path)
    session = _session(tmp_path, prepared, page)
    assert session['contentTarget'] == 'shared-passages'
    assert session['pages'][0]['problemIds'] == []
    assert session['pages'][0]['reviewStatus'] == 'normal'
    assert session['pages'][0]['intentionallyExcluded'] is True
    assert session['pages'][0]['exclusionReason'] == 'shared_passages_only'
    restored = rewrite_session_for_http(session)
    assert restored['pages'][0]['reviewStatus'] == 'normal'
    assert restored['pages'][0]['intentionallyExcluded'] is True


@pytest.mark.parametrize('failure', ['all-target', 'no-model', 'empty', 'missing-block',
    'unnumbered', 'low-confidence', 'page-risk', 'problem-risk', 'ocr-empty', 'shared-body'])
def test_shared_only_does_not_hide_real_unparsed_or_unexported_content(tmp_path, failure):
    prepared, page = _question_page(tmp_path)
    target = 'shared-passages'
    if failure == 'all-target': target = 'all'
    elif failure == 'no-model': page = None
    elif failure == 'empty': page.problems = []
    elif failure == 'missing-block': page.blocks = []
    elif failure == 'unnumbered': page.problems[0].metadata.clear()
    elif failure == 'low-confidence': page.blocks[0].confidence = 0.3
    elif failure == 'page-risk': page.metadata['route_decision'] = {'profile': {'reasons': ['ocr_empty_text']}}
    elif failure == 'problem-risk': page.problems[0].metadata['risk_flags'] = ['fallback_grouping']
    elif failure == 'ocr-empty': page.blocks[0].metadata['ocr_empty_text'] = True
    elif failure == 'shared-body':
        page.blocks.append(ContentBlock('shared', BlockType.STEM, Box(20, 300, 500, 300), 1,
            text='Unexported shared reading body.', confidence=1,
            metadata={'passage_has_visible_body': True}))
        page.problems.append(ProblemUnit('shared-unit', Subject.UNKNOWN, 'passage',
            stem_block_ids=['shared'], metadata={'passage_role': 'passage_fragment',
            'passage_range': {'start': 7, 'end': 8}, 'passage_group_id': 'group'}))
    status = _session(tmp_path, prepared, page, target)['pages'][0]
    assert status['reviewStatus'] == 'failed'
    assert status['intentionallyExcluded'] is False


def test_explicit_header_only_seed_does_not_block_confident_question_only_omission(tmp_path):
    prepared, page = _question_page(tmp_path)
    page.blocks.append(ContentBlock('header', BlockType.STEM, Box(20, 300, 500, 30), 1,
        text='[7~8] Read the passage.', confidence=1, metadata={'passage_has_visible_body': False}))
    page.problems.append(ProblemUnit('header-unit', Subject.UNKNOWN, 'header', stem_block_ids=['header'],
        metadata={'passage_role': 'passage_fragment', 'passage_range': {'start': 7, 'end': 8},
                  'passage_group_id': 'group'}))
    assert _session(tmp_path, prepared, page)['pages'][0]['reviewStatus'] == 'normal'


@pytest.mark.parametrize('explicit_risk', [False, True])
def test_excluded_child_page_ignores_only_automatic_cross_page_reminder(tmp_path, explicit_risk):
    prepared, page = _question_page(tmp_path)
    page.problems[0].metadata.update(passage_role='child_question', passage_group_id='group',
        passage_range={'start': 7, 'end': 8}, passage_continues_across_pages=True)
    if explicit_risk:
        page.problems[0].metadata['risk_flags'] = [board.PASSAGE_CROSS_PAGE_MERGE_CHECK_RISK_FLAG]
    status = _session(tmp_path, prepared, page)['pages'][0]['reviewStatus']
    assert status == ('failed' if explicit_risk else 'normal')


@pytest.mark.parametrize('orphan_type', ['shared-body', 'ordinary-text', 'image', 'table', 'dangling-reference'])
def test_unassigned_substantive_blocks_cannot_be_silently_excluded(tmp_path, orphan_type):
    prepared, page = _question_page(tmp_path)
    if orphan_type == 'dangling-reference':
        page.problems[0].choice_block_ids.append('missing-choice')
    else:
        metadata = {'passage_role': 'passage_fragment', 'passage_range': {'start': 8, 'end': 9},
                    'passage_has_visible_body': True} if orphan_type == 'shared-body' else {}
        block_type = {'image': BlockType.IMAGE, 'table': BlockType.TABLE}.get(orphan_type, BlockType.STEM)
        page.blocks.append(ContentBlock('orphan', block_type, Box(20, 300, 500, 200), 1,
            text='Actual printed reading content. ' * 8 if block_type == BlockType.STEM else None,
            confidence=.99, metadata=metadata))
    status = _session(tmp_path, prepared, page)['pages'][0]
    assert status['reviewStatus'] == 'failed'
    assert status['intentionallyExcluded'] is False


@pytest.mark.parametrize('chrome', ['header', 'footer', 'decoration'])
def test_known_page_chrome_does_not_become_unparsed_shared_body(tmp_path, chrome):
    prepared, page = _question_page(tmp_path)
    text, box, block_type = {
        'header': ('국어 영역', Box(30, 1, 200, 10), BlockType.TITLE),
        'footer': ('1/16', Box(270, 770, 60, 15), BlockType.FOOTNOTE),
        'decoration': (None, Box(300, 20, 1, 700), BlockType.DECORATION),
    }[chrome]
    page.blocks.append(ContentBlock('chrome', block_type, box, 1, text=text, confidence=1))
    assert _session(tmp_path, prepared, page)['pages'][0]['reviewStatus'] == 'normal'


def test_actual_shared_only_export_marks_question_only_page_as_intentionally_excluded(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'shared-and-questions.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 70), '[1~2] Read the passage and answer the questions.', fontsize=10)
        for n, y in enumerate(range(110, 300, 22)):
            page.insert_text((40, y), f'Shared printed reading body line {n}.', fontsize=10)
        page.insert_text((40, 360), '1. What is the title?', fontsize=12)
        page.insert_text((40, 550), '2. Which statement is true?', fontsize=12)
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 100), '3. Choose the correct answer.', fontsize=12)
        page.insert_text((40, 400), '4. Choose the next answer.', fontsize=12)
        doc.save(source)
    result = board.run_problem_export(source, output_dir=tmp_path / 'out', subject_name='unknown',
        ocr='none', pdf_dpi=144, export_edb=False, record_mode='image-only',
        input_intent='multi-problem', content_target='shared-passages')
    session = result['ui_session']
    assert len(session['problems']) == 1
    assert session['pages'][0]['problemIds']
    assert session['pages'][1]['problemIds'] == []
    assert session['pages'][1]['reviewStatus'] == 'normal'
    assert session['pages'][1]['intentionallyExcluded'] is True
    # Exercise the separate CLI session-building call, not just the API helper.
    import json
    from pathlib import Path
    monkeypatch.setattr('sys.argv', ['build_problem_board_edb.py', str(source), '--output-dir', str(tmp_path / 'cli'),
        '--passages-only', '--ocr', 'none', '--pdf-dpi', '144', '--record-mode', 'image-only'])
    assert board.main() == 0
    capsys.readouterr()
    cli_session = json.loads((tmp_path / 'cli' / 'ui_session.json').read_text())
    assert cli_session['contentTarget'] == 'shared-passages'
    assert cli_session['pages'][1]['intentionallyExcluded'] is True
    assert cli_session['pages'][1]['reviewStatus'] == 'normal'
    assert parse_edb(Path(cli_session['edb_path'])).record_count_actual == 1
