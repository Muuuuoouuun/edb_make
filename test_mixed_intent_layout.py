"""제작 for a lesson that mixes 넣는 방식 (페이지 전체 넣기 / 수동 자르기 and 문항 AI 인식 files).

Every page of a 페이지 전체 넣기 or 수동 자르기 file is marked pageAsIsFastPath, and the
cuts made from it keep that page, so 제작 decides the page-chrome check per question
from its own file. Pages saved before the mark existed keep the lesson-wide value.
The board layout was already per question (each problem's own inputIntent).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import app_server
import build_problem_board_edb as board
from placement_engine import place_problems
from test_ui_placement_details import NODE_SETUP, run_node


def _page_image(path: Path) -> str:
    # A whole page keeps its frame, footer rule and page number.
    image = Image.new("RGB", (500, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, 0, 799), fill="black", width=3)
    draw.line((0, 780, 499, 780), fill="black", width=3)
    draw.text((60, 80), "Source text and original page frame", fill="black")
    draw.text((350, 790), "37", fill="black")
    image.save(path)
    return path.as_uri()


def _crop_image(path: Path, *, guide_line: bool) -> str:
    # A crop that caught the page's left guide line reads as page chrome.
    image = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.text((64, 48), "3. Choose the answer.", fill="black")
    if guide_line:
        draw.line((0, 0, 0, 239), fill="black", width=3)
    image.save(path)
    return path.as_uri()


def _page(page_id: str, problem_ids: list[str], *, page_file: bool | None) -> dict:
    page = {"id": page_id, "problemIds": problem_ids}
    if page_file is not None:  # None: a page saved before the mark existed
        page["pageAsIsFastPath"] = page["page_as_is_fast_path"] = page_file
    return page


def _whole_page(tmp_path: Path, page_id: str) -> dict:
    # What 페이지 전체 넣기 makes for one source page.
    return {"id": f"{page_id}-problem-1", "title": "페이지 1", "sourcePageId": page_id,
            "imagePath": _page_image(tmp_path / f"{page_id}.png"), "processingStep": "s2",
            "inputIntent": "page-as-is", "placementMode": "continuous-page-as-is",
            "forceFullPageBounds": True, "riskFlags": []}


def _crop(tmp_path: Path, problem_id: str, page_id: str, *, guide_line: bool, **stamps) -> dict:
    # A 수동 자르기 cut has no stamp of its own; the UI sends it as multi-problem.
    return {"id": problem_id, "title": problem_id, "sourcePageId": page_id,
            "imagePath": _crop_image(tmp_path / f"{problem_id}.png", guide_line=guide_line),
            "processingStep": "s2", "inputIntent": "multi-problem", "riskFlags": [], **stamps}


def _lesson(intent: str, placement_mode: str, pages: list[dict], problems: list[dict]) -> dict:
    return {"input_intent": intent, "template": {"metadata": {"placement_mode": placement_mode}},
            "pages": pages, "problems": problems}


def _flagged(session: dict) -> list[str]:
    preflight, _groups = app_server._session_publish_blocking_preflight(session["problems"], session=session)
    return sorted({problem_id for issue in preflight["issues"]
                   for problem_id in issue.get("problemIds") or [issue.get("problemId")]})


def test_page_as_is_lesson_plus_a_multi_problem_file_checks_the_added_questions(tmp_path):
    page = _whole_page(tmp_path, "pages-page-001")
    cut = _crop(tmp_path, "pages-page-002-crop-01", "pages-page-002", guide_line=True)
    caught = _crop(tmp_path, "ai-page-001-problem-1", "ai-page-001", guide_line=True,
                   placementMode="one-problem-per-page")
    clean = _crop(tmp_path, "ai-page-001-problem-2", "ai-page-001", guide_line=False,
                  placementMode="one-problem-per-page")
    pages = [_page("pages-page-001", [page["id"]], page_file=True),
             _page("pages-page-002", [cut["id"]], page_file=True),
             _page("ai-page-001", [caught["id"], clean["id"]], page_file=False)]
    # Merged before this fix the lesson kept page-as-is; now it reads multi-problem.
    # Both keep the first file's continuous template label.
    for intent in ("page-as-is", "multi-problem"):
        lesson = _lesson(intent, "continuous-page-as-is", pages, [page, cut, caught, clean])
        assert _flagged(lesson) == [caught["id"]], intent

    # The board already placed each question by its own 넣는 방식: the page flows
    # on and the added question takes a slot, despite the lesson's continuous label.
    template = app_server._template_from_session(lesson)
    entries = app_server._problems_to_entries([page, caught], template=template)
    page_slot, question_slot = place_problems(board.placement_inputs(entries), template=template)
    assert page_slot.snapped_next_start_y_pages == pytest.approx(
        page_slot.start_y_pages + page_slot.actual_content_height_pages)
    assert question_slot.snapped_next_start_y_pages - question_slot.start_y_pages == pytest.approx(
        template.base_slot_height_pages)


def test_multi_problem_lesson_plus_a_page_as_is_file_leaves_its_pages_unchecked(tmp_path):
    caught = _crop(tmp_path, "ai-page-001-problem-1", "ai-page-001", guide_line=True,
                   placementMode="one-problem-per-page")
    clean = _crop(tmp_path, "ai-page-001-problem-2", "ai-page-001", guide_line=False,
                  placementMode="one-problem-per-page")
    cut = _crop(tmp_path, "pages-page-001-crop-01", "pages-page-001", guide_line=True)
    page = {**_whole_page(tmp_path, "pages-page-002"), "processingStep": "s3"}
    lesson = _lesson("multi-problem", "one-problem-per-page",
                     [_page("ai-page-001", [caught["id"], clean["id"]], page_file=False),
                      _page("pages-page-001", [cut["id"]], page_file=True),
                      _page("pages-page-002", [page["id"]], page_file=True)],
                     [caught, clean, cut, page])
    assert _flagged(lesson) == [caught["id"]]


def test_single_method_lessons_keep_todays_checks(tmp_path):
    # 수동 자르기 alone: a page left whole and a cut of the next page.
    page = _whole_page(tmp_path, "manual-page-001")
    cut = _crop(tmp_path, "manual-page-002-crop-01", "manual-page-002", guide_line=True)
    manual = _lesson("page-as-is", "continuous-page-as-is",
                     [_page("manual-page-001", [page["id"]], page_file=True),
                      _page("manual-page-002", [cut["id"]], page_file=True)], [page, cut])
    assert _flagged(manual) == []

    # 문항 AI 인식 alone: a question stretched with 너비 맞춤 is still a question.
    widened = _crop(tmp_path, "ai-page-001-problem-1", "ai-page-001", guide_line=True,
                    inputIntent="page-as-is", placementMode="continuous-page-as-is",
                    forceFullPageBounds=True, fitWidthConverted=True)
    clean = _crop(tmp_path, "ai-page-001-problem-2", "ai-page-001", guide_line=False)
    questions = _lesson("multi-problem", "one-problem-per-page",
                        [_page("ai-page-001", [widened["id"], clean["id"]], page_file=False)], [widened, clean])
    assert _flagged(questions) == [widened["id"]]

    # Pages saved before the mark existed follow the lesson-wide value.
    old_cut = _crop(tmp_path, "old-page-001-crop-01", "old-page-001", guide_line=True)
    old_pages = [_page("old-page-001", [old_cut["id"]], page_file=None)]
    assert _flagged(_lesson("page-as-is", "continuous-page-as-is", old_pages, [old_cut])) == []
    assert _flagged(_lesson("multi-problem", "one-problem-per-page", old_pages, [old_cut])) == [old_cut["id"]]


def test_merged_lesson_keeps_one_method_only_when_both_files_agree() -> None:
    run_node(NODE_SETUP + r"""
    const sandbox = placementSandbox();
    vm.runInContext(section('function makeUniqueId(', 'function applyItemStateToProblem(')
      + section('function mergeSessions(', 'function sessionReusableSourcePaths('), sandbox);
    const { reviewModeCopy } = require('./ui_prototype/review_filters.js');
    const lesson = (name, intent, pageFile) => ({ session_name: name, input_intent: intent,
      pages: [{ id: `${name}-page-001`, pageAsIsFastPath: pageFile, problemIds: [`${name}-p1`] }],
      problems: [{ id: `${name}-p1`, title: `${name} 1`, actualHeightPages: 0.8, inputIntent: intent }] });
    const pages = lesson('pages', 'page-as-is', true);
    const questions = lesson('questions', 'multi-problem', false);
    for (const [base, added] of [[pages, questions], [questions, pages]]) {
      const merged = sandbox.mergeSessions(base, added, 'lesson', 1);
      assert.equal(merged.inputIntent, 'multi-problem');
      assert.equal(merged.input_intent, 'multi-problem');
      assert.equal(reviewModeCopy(merged, { core: 2, total: 2 }).title, '문항 검수');
      // Each question and page keeps its own file's method for 제작.
      assert.deepEqual(Array.from(merged.problems, problem => problem.inputIntent),
        [base.problems[0].inputIntent, added.problems[0].inputIntent]);
      assert.deepEqual(Array.from(merged.pages, page => page.pageAsIsFastPath),
        [base.pages[0].pageAsIsFastPath, added.pages[0].pageAsIsFastPath]);
    }
    const both = sandbox.mergeSessions(pages, lesson('more', 'page-as-is', true), 'lesson', 1);
    assert.equal(both.input_intent, 'page-as-is');
    assert.equal(reviewModeCopy(both, { core: 2, total: 2 }).title, '페이지 검수');
    """)
