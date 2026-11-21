"""Round-2 pipeline-passages chain: column split, passage joins, photo size,
multi-page TIFF, page-cleanup workers and mixed 공통 지문만 추출 lessons."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
import pymupdf
import pytest

import app_server
import build_problem_board_edb as board
import preprocess
from segment import _build_pdf_passage_range_blocks, segment_page
from structured_schema import Subject
from test_large_pdf_batches import make_pdf
from test_ui_placement_details import NODE_SETUP
from test_ui_sidebar_hierarchy import RENDER_SETUP, run_node


class _PdfPage:
    def __init__(self, image, source_type, markers, text_lines):
        self.image = image
        self.metadata = {"source_type": source_type, "pdf_problem_markers": markers}
        if text_lines is not None:
            self.metadata["pdf_text_lines"] = text_lines
        self.source_path = f"synthetic.{source_type}"


def _stems(draw, tops=(90, 330, 570)):
    markers = []
    for number, top in zip((1, 2, 3), tops):
        stem = f"{number}. short stem"
        draw.text((40, top), stem, fill=(20, 20, 20))
        markers.append({"number": number, "text": stem, "bbox": {"left": 40, "top": top - 2, "right": 140, "bottom": top + 14}})
    return markers


# R2
def test_figures_beside_short_stems_keep_one_full_width_column():
    # One-column worksheet: number + short stem on the left, a figure to the
    # right, choices below. No stem crosses the empty strip between text and
    # figures, so the page read as two columns and every crop lost its figure.
    image = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(image)
    markers = _stems(draw)
    text_lines = [{"text": marker["text"], "bbox": marker["bbox"]} for marker in markers]
    for marker in markers:
        top = marker["bbox"]["top"] + 2
        draw.ellipse((380, top + 20, 520, top + 160), outline=(20, 20, 20), width=3)
        draw.text((60, top + 180), "1) a   2) b   3) c", fill=(20, 20, 20))
        text_lines.append({"text": "1) a   2) b   3) c", "bbox": {"left": 60, "top": top + 178, "right": 200, "bottom": top + 194}})

    segmented = segment_page(_PdfPage(image, "pdf", markers, text_lines), page_id="figure-beside-stem", subject=Subject.MATH)

    assert segmented.metadata.get("segmenter") == "pdf-text-markers"
    assert segmented.metadata.get("column_count") == 1
    assert [block.metadata.get("problem_number") for block in segmented.blocks] == [1, 2, 3]
    assert all(block.bbox.right > 540 for block in segmented.blocks)
    assert all(block.metadata.get("segmenter") != "uncovered-region-recovery" for block in segmented.blocks)


@pytest.mark.parametrize("source_type", ["pdf", "hwp"])
def test_unnumbered_right_column_with_text_or_without_text_layer_stays_a_column(source_type):
    # Questions left, a passage continuing right, no centre rule. An HWP page
    # rendered by rhwp has no text layer: "no text there" is missing data.
    image = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(image)
    markers = _stems(draw)
    passage = []
    for top in range(90, 720, 24):
        draw.text((330, top), "passage text line", fill=(20, 20, 20))
        passage.append({"text": "passage text line", "bbox": {"left": 330, "top": top - 2, "right": 440, "bottom": top + 14}})
    text_lines = markers + passage if source_type == "pdf" else None

    segmented = segment_page(_PdfPage(image, source_type, markers, text_lines), page_id=f"right-passage-{source_type}", subject=Subject.KOREAN)

    assert segmented.metadata.get("column_count") == 2
    numbered = [block for block in segmented.blocks if block.metadata.get("problem_number")]
    assert [block.metadata.get("problem_number") for block in numbered] == [1, 2, 3]
    assert all(block.bbox.right < 320 for block in numbered)


# R23
@pytest.mark.parametrize("continues", [True, False])
def test_passage_continuation_starts_below_page_header_rule(continues):
    # 수능/학력평가 page 1: 성명 / 수험 번호 sit in the right half above a
    # page-wide rule. They must never open the right-column fragment.
    image = Image.new("RGB", (1200, 1700), "white")
    ImageDraw.Draw(image).rectangle((70, 172, 1130, 175), fill="black")

    def line(text, left, top, right):
        return {"text": text, "bbox": {"left": left, "top": top, "right": right, "bottom": top + 22}}

    header = [line("성명", 720, 130, 768), line("수험 번호", 900, 130, 990)]
    left = [line("[1~3] 다음 글을 읽고 물음에 답하시오.", 80, 200, 560)]
    left += [line(f"지문 왼쪽 {top}", 90, top, 570) for top in range(240, 1600, 32)]
    right = [line(f"지문 오른쪽 {top}", 640, top, 1120) for top in range(200, 584, 32)] if continues else []
    first_question_top = 624 if continues else 200
    markers = [
        {"number": number, "bbox": {"left": 630, "top": top, "right": 660, "bottom": top + 24}}
        for number, top in ((1, first_question_top), (2, first_question_top + 400))
    ]

    blocks = _build_pdf_passage_range_blocks(
        image, "page-1", header + left + right,
        [(1, [], (80.0, 580.0)), (2, markers, (620.0, 1130.0))],
        page_area=float(image.width * image.height), start_index=1,
    )

    assert len(blocks) == (2 if continues else 1)  # no header-only fragment
    assert all(block.bbox.top > 175.0 for block in blocks)


# R11
def test_page_as_is_photo_is_area_capped_but_tall_screenshot_and_retry_path_are_not(tmp_path, monkeypatch):
    monkeypatch.setattr(preprocess, "MAX_RASTER_PAGE_PIXELS", 300 * 400)
    photo = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[274] = 6  # a portrait phone photo stored landscape
    Image.new("RGB", (800, 600), "white").save(photo, exif=exif)
    shot = tmp_path / "shot.png"
    Image.new("RGB", (117, 800), "white").save(shot)
    kw = dict(subject=Subject.UNKNOWN, ocr_mode="none", ai_fallback_config=None, pdf_dpi=200,
              detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None,
              input_intent="page-as-is")

    assert [page.size for page in board.build_pages(photo, **kw)[0]] == [(300, 400)]
    assert [page.size for page in board.build_pages(shot, **kw)[0]] == [(117, 800)]
    # AI 재인식 reads the page through prepare_source_pages and keeps its coordinates.
    retry = preprocess.prepare_source_pages(photo, detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None)
    assert [page.image.size for page in retry] == [(600, 800)]


# R31
@pytest.mark.parametrize("crop_margins", [False, True])
def test_prepare_pages_reads_every_page_of_a_multipage_tiff(tmp_path, crop_margins):
    frames = []
    for index in range(3):
        frame = Image.new("RGB", (120, 160), "white")
        frame.paste((0, 0, 0), (10 + index * 30, 10, 30 + index * 30, 30))
        frames.append(frame)
    for suffix, expected in ((".tif", 3), (".png", 1)):  # an animated PNG is still one page
        source = tmp_path / f"scan{suffix}"
        frames[0].save(source, save_all=True, append_images=frames[1:])
        pages = preprocess.prepare_pages(source, tmp_path / f"out{suffix}", enable_perspective=False,
                                         enable_deskew=False, enable_margin_crop=crop_margins)
        assert [page.page_index for page in pages] == list(range(expected))
        assert len({page.normalized_path for page in pages}) == expected
        if expected == 3 and not crop_margins:  # page 3 is frame 3, not frame 1 again
            assert pages[2].image.convert("L").getpixel((85, 15)) < 40


# R68
def test_long_pdf_page_cleanup_uses_page_workers_in_page_order(tmp_path, monkeypatch):
    source = tmp_path / "long.pdf"
    make_pdf(source, 12)
    monkeypatch.setattr(preprocess.os, "cpu_count", lambda: 8)
    monkeypatch.delenv("EDB_PREPROCESS_PAGE_WORKERS", raising=False)
    prepared = preprocess.prepare_source_pages(source, pdf_dpi=72)
    assert [page.page_number for page in prepared] == list(range(1, 13))
    assert {page.metadata["pdf_preprocess_page_worker_count"] for page in prepared} == {4}


def test_page_cleanup_memory_failure_redoes_pages_one_at_a_time_and_names_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr(preprocess.os, "cpu_count", lambda: 8)
    monkeypatch.delenv("EDB_PREPROCESS_PAGE_WORKERS", raising=False)
    original = preprocess.normalize_image
    failures = []

    def fails_once_on_page_11(*args, **kwargs):
        if kwargs["page_index"] == 10 and not failures:
            failures.append(1)
            raise MemoryError("fixture allocation failure")
        return original(*args, **kwargs)

    source = tmp_path / "retry.pdf"
    make_pdf(source, 12)
    with patch.object(preprocess, "normalize_image", side_effect=fails_once_on_page_11):
        prepared = preprocess.prepare_source_pages(source, pdf_dpi=72)
    assert failures == [1]
    assert [page.page_number for page in prepared] == list(range(1, 13))
    assert {page.metadata["pdf_preprocess_page_worker_count"] for page in prepared} == {1}

    source = tmp_path / "own-cache" / "too-big.pdf"  # same bytes must not hit the first cache
    source.parent.mkdir()
    make_pdf(source, 12)
    with patch.object(preprocess, "normalize_image", side_effect=MemoryError("fixture allocation failure")):
        with pytest.raises(preprocess.PdfPageProcessingError) as caught:
            preprocess.prepare_source_pages(source, pdf_dpi=72)
    assert caught.value.code == "pdf_resource_exhausted"
    assert (caught.value.source_file, caught.value.page_start, caught.value.page_end) == (source.name, 1, 1)


# R62
def _passage_pdf(path, first, tag):
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 70), f'[{first}~{first + 1}] Read the passage and answer the questions.', fontsize=10)
        for n, y in enumerate(range(110, 300, 22)):
            page.insert_text((40, y), f'{tag} shared printed body line {n}.', fontsize=10)
        page.insert_text((40, 370), f'{first}. What is the title?', fontsize=12)
        page.insert_text((40, 560), f'{first + 1}. Which statement is true?', fontsize=12)
        doc.save(path)


def test_mixed_lesson_publish_checks_missing_questions_per_group(tmp_path):
    # 문항 AI 인식 lesson plus a file added with 공통 지문만 추출: the added
    # passage left 18~19 out on purpose, so 제작 must not report them missing.
    passage_pdf, question_pdf = tmp_path / 'readingA.pdf', tmp_path / 'worksheetB.pdf'
    _passage_pdf(passage_pdf, 18, 'A')
    _passage_pdf(question_pdf, 1, 'B')

    def export(source, target):
        return board.run_problem_export(source, output_dir=tmp_path / f'work-{target}', subject_name='unknown',
            ocr='none', pdf_dpi=144, export_edb=False, record_mode='image-only',
            input_intent='multi-problem', content_target=target)['ui_session']

    shared, questions = export(passage_pdf, 'shared-passages'), export(question_pdf, 'all')
    assert shared['problems'] and all(p['contentTarget'] == 'shared-passages' for p in shared['problems'])
    assert not any('contentTarget' in p for p in questions['problems'])
    session = copy.deepcopy(questions)
    session['problems'] += shared['problems']
    session['pages'] += shared['pages']
    session['input_files'] += shared['input_files']
    session['contentTarget'] = session['content_target'] = 'all'  # what mergeSessions now sends
    responses = []
    server = SimpleNamespace(latest_session=session)
    server.remember_session = lambda saved: setattr(server, 'latest_session', saved)
    handler = object.__new__(app_server.AppRequestHandler)
    handler.server = server
    handler._read_json_body = lambda: {}
    handler._send_json = lambda body, **kwargs: responses.append((body, kwargs))
    handler._handle_session_publish()
    response, options = responses[0]
    assert response['ok'], (response, options)
    final = server.latest_session
    shared_group = next(g for g in final['passageGroups'] if g['childProblemNumbers'] == [18, 19])
    assert shared_group['missingChildProblemNumbers'] == []
    issues = final['publishSummary']['classinPreflight']['issues']
    assert not [issue for issue in issues if issue['type'] == 'passage_missing_child_questions']


def test_mixed_lesson_checks_missing_questions_per_passage_group():
    problems = [
        {'id': 'a-frag', 'passageGroupId': 'a-passage-18-19', 'passageRole': 'passage_fragment',
         'passageChildProblemNumbers': [18, 19], 'contentTarget': 'shared-passages'},
        {'id': 'b-frag', 'passageGroupId': 'b-passage-1-3', 'passageRole': 'passage_fragment',
         'passageChildProblemNumbers': [1, 2, 3]},
        {'id': 'b-q1', 'passageGroupId': 'b-passage-1-3', 'passageRole': 'child_question', 'problemNumber': 1},
        {'id': 'b-q3', 'passageGroupId': 'b-passage-1-3', 'passageRole': 'child_question', 'problemNumber': 3},
    ]
    issues = [issue for issue in board._classin_handoff_preflight({'contentTarget': 'all', 'problems': problems})['issues']
              if issue['type'] == 'passage_missing_child_questions']
    assert [(issue['passageGroupId'], issue['missingChildProblemNumbers']) for issue in issues] == [('b-passage-1-3', [2])]


def test_merge_sessions_keeps_the_content_target_only_when_both_files_agree():
    run_node(NODE_SETUP + r"""
    const sandbox = placementSandbox();
    vm.runInContext(section('function makeUniqueId(', 'function applyItemStateToProblem(')
      + section('function mergeSessions(', 'function sessionReusableSourcePaths('), sandbox);
    const { reviewModeCopy } = require('./ui_prototype/review_filters.js');
    const lesson = (name, target, problems) => ({ session_name: name, contentTarget: target, content_target: target,
      problems, pages: [{ id: `${name}-page-001`, problemIds: problems.map(problem => problem.id) }] });
    const passages = name => lesson(name, 'shared-passages', [1, 2, 3].map(n => ({ id: `${name}-p${n}`,
      title: `지문 ${n}`, actualHeightPages: 0.8, passageRole: 'passage_fragment', passageGroupId: `${name}-${n}` })));
    const questions = lesson('questions', 'all', [1, 2, 3, 4, 5, 6].map(n => ({ id: `q${n}`, title: `${n}번`,
      actualHeightPages: 0.8, problemNumber: n })));
    for (const merged of [sandbox.mergeSessions(passages('a'), questions), sandbox.mergeSessions(questions, passages('a'))]) {
      assert.equal(merged.contentTarget, 'all');
      assert.equal(merged.content_target, 'all');
      assert.equal(reviewModeCopy(merged, { core: 6, supplemental: 3, total: 9 }).title, '문항 검수');
    }
    const both = sandbox.mergeSessions(passages('a'), passages('b'));
    assert.equal(both.contentTarget, 'shared-passages');
    assert.equal(both.content_target, 'shared-passages');
    """)


# R95 (and R62's per-problem stamp)
def test_shared_only_import_reports_no_page_count_warning_or_missing_questions(tmp_path, monkeypatch, capsys):
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
    session = board.run_problem_export(source, output_dir=tmp_path / 'out', subject_name='unknown',
        ocr='none', pdf_dpi=144, export_edb=False, record_mode='image-only',
        input_intent='multi-problem', content_target='shared-passages')['ui_session']
    assert [problem['contentTarget'] for problem in session['problems']] == ['shared-passages']
    assert not [message for message in session['warning_messages'] if '원본 페이지 수와 비슷' in message]
    assert session['passageGroups']
    assert all(not group['missingChildProblemNumbers'] for group in session['passageGroups'])
    assert all(not item['missingChildProblemNumbers'] for item in session['passageReviewItems'])
    monkeypatch.setattr('sys.argv', ['build_problem_board_edb.py', str(source), '--output-dir', str(tmp_path / 'cli'),
        '--passages-only', '--ocr', 'none', '--pdf-dpi', '144', '--record-mode', 'image-only'])
    assert board.main() == 0
    capsys.readouterr()
    cli_session = json.loads((tmp_path / 'cli' / 'ui_session.json').read_text(encoding='utf-8'))
    assert not [message for message in cli_session['warning_messages'] if '원본 페이지 수와 비슷' in message]
    assert all(not group['missingChildProblemNumbers'] for group in cli_session['passageGroups'])


# R88
def test_rail_names_a_shared_passage_by_its_range():
    run_node(RENDER_SETUP + r"""
    const sandbox = renderSandbox();
    const reorder = require('./ui_prototype/reorder.js');
    Object.assign(sandbox, {
      originalProblemNumberInfo: reorder.originalProblemNumberInfo,
      problemDisplayName: reorder.problemDisplayName, problemSourceLabel: reorder.problemSourceLabel,
      reviewStatusClass: () => 'status-normal', TileImage: () => null,
      railThumbClass: () => 'is-board', RAIL_THUMB_PREVIEW_MAX_DIMENSION: 256,
    });
    compile(sandbox, section('function passageRangeLabelFor(', 'function passageGroupIdFor(')
      + section('function RailItemDetails(', 'const MemoRailItemDetails'));
    const label = it => text(findClass(sandbox.RailItemDetails({ it, i: 0, hasPageChrome: false }), 'original-number-label'));
    const passage = { id: 'p', name: '지문 2~4', passageRole: 'passage_fragment' };
    assert.equal(label({ ...passage, passageRange: { start: 2, end: 4 } }), '지문 2~4');
    assert.equal(label({ ...passage, metadata: { passage_range: { start: 12, end: 15 } } }), '지문 12~15');
    assert.equal(label(passage), '공통 지문');
    assert.equal(label({ id: 'q', problemNumber: 3, problemNumberSource: 'ocr_line',
      passageRange: { start: 2, end: 4 } }), '3번', 'a question in the passage group keeps its own number');
    """)
