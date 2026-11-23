"""Question-only group pages are not evidence of a cross-page reading body."""
from __future__ import annotations

import pymupdf

import build_problem_board_edb as board
from structured_schema import ProblemUnit, Subject


def test_one_page_body_with_questions_elsewhere_has_no_false_merge_review(tmp_path):
    source = tmp_path / 'one-body-page.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.draw_line((300, 45), (300, 755), color=(0, 0, 0), width=.5)
        page.insert_text((40, 70), '[1~2] Read the passage and answer the questions.', fontsize=9)
        for x in (40, 330):
            for n, y in enumerate(range(110, 690, 26)):
                page.insert_text((x, y), f'Shared reading paragraph line {n}.', fontsize=10)
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 100), '1. What is the title?', fontsize=12)
        page.insert_text((40, 400), '2. Which statement is true?', fontsize=12)
        doc.save(source)
    result = board.run_problem_export(source, output_dir=tmp_path / 'out', subject_name='unknown',
        ocr='none', pdf_dpi=144, export_edb=False, record_mode='image-only',
        input_intent='multi-problem', content_target='shared-passages')
    session = result['ui_session']
    assert len(session['problems']) == 1
    passage = session['problems'][0]
    assert len(passage['sourceSegments']) == 2  # Real left/right body columns.
    assert len({s['sourcePageId'] for s in passage['sourceSegments']}) == 1
    assert len(passage['passageSourcePageIds']) == 2  # Body + child question page.
    assert board.PASSAGE_CROSS_PAGE_MERGE_CHECK_RISK_FLAG not in passage['riskFlags']
    assert passage['reviewStatus'] == 'normal'
    assert session['pages'][1]['reviewStatus'] == 'normal'


def test_real_cross_page_body_keeps_merge_review_and_other_risks():
    metadata = {'passage_role': 'passage_fragment', 'passage_continues_across_pages': True,
                'passage_source_page_ids': ['p1', 'p2', 'p3'],
                'passage_fragment_source_page_ids': ['p1', 'p2'],
                'passage_has_cross_page_fragments': True, 'risk_flags': ['real_ocr_risk']}
    unit = ProblemUnit('body', Subject.UNKNOWN, 'shared body', metadata=metadata)
    flags = board._collect_problem_risk_flags(unit)
    assert flags == [board.PASSAGE_CROSS_PAGE_MERGE_CHECK_RISK_FLAG, 'real_ocr_risk']
    assert board._problem_needs_cross_page_passage_merge_review(metadata,
        [{'sourcePageId': 'p1'}, {'sourcePageId': 'p2'}])
    # An unmerged individual fragment has only its own page in its crop;
    # document-wide positive evidence must still require reviewing the merge.
    assert board._problem_needs_cross_page_passage_merge_review(metadata,
        [{'sourcePageId': 'p1'}])
    unit.metadata['passage_fragments_merged'] = True
    assert board._collect_problem_risk_flags(unit) == ['real_ocr_risk']


def test_unknown_legacy_body_remains_reviewable_and_explicit_risks_are_retained():
    metadata = {'passage_role': 'passage_fragment', 'passage_source_page_ids': ['p1', 'p2'],
                'risk_flags': ['real_ocr_risk']}
    unit = ProblemUnit('body', Subject.UNKNOWN, 'shared body', metadata=metadata)
    assert board.PASSAGE_CROSS_PAGE_MERGE_CHECK_RISK_FLAG in board._collect_problem_risk_flags(unit)
    unit.metadata['passage_has_cross_page_fragments'] = False
    assert board._collect_problem_risk_flags(unit) == ['real_ocr_risk']


def test_direct_shared_entry_is_never_whole_page_even_with_malformed_whole_flags(tmp_path):
    from structured_schema import Box
    entry = board.ProblemEntry(problem_id='shared', title='shared', problem_number=None,
        subject=Subject.UNKNOWN, source_page_id='p1', source_path='source.pdf',
        prepared_page=None, bounds=Box(0, 0, 600, 800), crop_path=tmp_path / 'body.png',
        board_render_path=tmp_path / 'body.png', blocks=[], actual_height_pages=1,
        overflow_allowed=True, reading_heavy=True, risk_flags=[], input_intent='page-as-is',
        force_full_page_bounds=True, fit_width_converted=False, passage_role='passage_fragment',
        passage_range=(1, 2))
    assert not board._entry_is_whole_page(entry)
    entry.passage_role = None
    assert board._entry_is_whole_page(entry)
