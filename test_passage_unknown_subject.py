from __future__ import annotations

import copy
from pathlib import Path

import pymupdf
import pytest

import build_problem_board_edb as board
from layout_template_schema import LayoutTemplate
from structured_schema import Subject


def _make_shared_continuation_pdf(source: Path) -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.draw_line((300, 45), (300, 755), color=(0, 0, 0), width=0.5)
        page.insert_text((40, 70), "[41~42] Read the passage and answer the questions.", fontsize=9)
        for x, rows in ((40, range(110, 731, 26)), (330, range(80, 731, 26))):
            for index, y in enumerate(rows):
                page.insert_text((x, y), f"Body paragraph {x} line {index:02d}.", fontsize=10)
        page = document.new_page(width=600, height=800)
        page.draw_line((300, 45), (300, 755), color=(0, 0, 0), width=0.5)
        for index, y in enumerate(range(80, 251, 26)):
            page.insert_text((40, y), f"CONTINUATION ending line {index:02d}.", fontsize=10)
        page.insert_text((330, 90), "41. What is the title?", fontsize=12)
        page.insert_text((330, 420), "42. Which statement is true?", fontsize=12)
        for top in (130, 460):
            for index, y in enumerate(range(top, top + 121, 25)):
                page.insert_text((330, y), f"{index + 1}) Choice {index + 1}", fontsize=10)
        document.save(source)


def _models(tmp_path: Path, subject: Subject = Subject.UNKNOWN):
    source = tmp_path / "shared.pdf"
    _make_shared_continuation_pdf(source)
    prepared, models = board.build_pages(source, subject=subject, ocr_mode="none", ai_fallback_config=None,
        pdf_dpi=144, detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None)
    return source, prepared, models


def _active_group(models):
    groups = {}
    fragment = next(problem for problem in models[0].problems if problem.metadata.get("passage_role") == "passage_fragment")
    board._seed_cross_page_passage_group(groups, page_id=models[0].page_id, problem=fragment)
    return groups


@pytest.mark.parametrize("subject", [Subject.UNKNOWN, Subject.ENGLISH, Subject.KOREAN])
def test_shared_continuation_keeps_tail_with_ui_default_or_explicit_subject(tmp_path, subject):
    _source, prepared, models = _models(tmp_path, subject)
    entries = board.build_problem_entries(prepared, models, tmp_path / "out", LayoutTemplate(name="test"), content_target="shared-passages")
    assert len(entries) == 1
    assert [segment["source_page_id"] for segment in entries[0].source_segments] == [models[0].page_id, models[0].page_id, models[1].page_id]
    continuation = entries[0].source_segments[-1]["bbox"]
    lines = [line for line in prepared[1].metadata["pdf_text_lines"] if "CONTINUATION" in line["text"]]
    assert len(lines) == 7
    assert continuation["top"] <= min(line["bbox"]["top"] for line in lines)
    assert continuation["top"] + continuation["height"] >= max(line["bbox"]["bottom"] for line in lines)
    child_ids = {problem.unit_id for page in models for problem in page.problems if problem.metadata.get("passage_role") == "child_question"}
    assert entries[0].problem_id not in child_ids


def test_shared_continuation_never_joins_different_original_documents(tmp_path):
    _source, prepared, models = _models(tmp_path)
    models[0].metadata["original_source_path"] = str(tmp_path / "first" / "same-name.pdf")
    models[1].metadata["original_source_path"] = str(tmp_path / "second" / "same-name.pdf")
    models[0].metadata["source_pdf_path"] = models[0].metadata["original_source_path"]
    models[1].metadata["source_pdf_path"] = models[1].metadata["original_source_path"]
    entries = board.build_problem_entries(prepared, models, tmp_path / "out", LayoutTemplate(name="test"), content_target="shared-passages")
    assert len(entries) == 1
    assert all(segment["source_page_id"] == models[0].page_id for segment in entries[0].source_segments)
    assert not any(problem.metadata.get("passage_group_id") for problem in models[1].problems)


def test_unknown_continuation_requires_proven_source_identity_and_real_text(tmp_path):
    _source, _prepared, models = _models(tmp_path)
    groups = _active_group(models)
    anonymous = copy.deepcopy(models)
    for index, page in enumerate(anonymous):
        page.metadata.pop("original_source_path", None)
        page.metadata.pop("source_pdf_path", None)
        page.source_path = str(tmp_path / f"anonymous-{index}.png")
    assert board._infer_pdf_cross_page_passage_continuation(anonymous, 1, groups) is None
    empty = copy.deepcopy(models)
    for region in empty[1].metadata["pdf_pre_question_text_regions"]:
        region["line_count"] = 0
        region["char_count"] = 0
    assert board._infer_pdf_cross_page_passage_continuation(empty, 1, groups) is None
    math = copy.deepcopy(models)
    math[1].subject = Subject.MATH
    assert board._infer_pdf_cross_page_passage_continuation(math, 1, groups) is None


def test_legacy_normalized_page_ids_recover_without_subject_guessing(tmp_path):
    _source, _prepared, models = _models(tmp_path)
    groups = _active_group(models)
    for page in models:
        page.metadata.pop("original_source_path", None)
        page.metadata.pop("source_pdf_path", None)
    assert board._passage_document_identity(models[0]) == board._passage_document_identity(models[1])
    assert board._infer_pdf_cross_page_passage_continuation(models, 1, groups) is not None


def test_intervening_page_cannot_revive_previous_fragment(tmp_path):
    _source, _prepared, models = _models(tmp_path)
    groups = _active_group(models)
    intervening = copy.deepcopy(models[0])
    intervening.page_id = "unrelated-middle-page"
    pages = [models[0], intervening, models[1]]
    assert board._infer_pdf_cross_page_passage_continuation(pages, 2, groups) is None


def test_cross_document_preannotators_do_not_promote_unrelated_marker_tail(tmp_path):
    from structured_schema import PageModel, ProblemUnit
    first = PageModel("first-page-1", 900, 1200, Subject.KOREAN, str(tmp_path / "a" / "first.png"),
        problems=[ProblemUnit("tail", Subject.KOREAN, "unrelated tail", metadata={"marker_document_continuation": True})],
        metadata={"original_source_path": str(tmp_path / "a" / "first.hwp")})
    second = PageModel("second-page-1", 900, 1200, Subject.KOREAN, str(tmp_path / "b" / "second.png"),
        problems=[ProblemUnit(f"q{number}", Subject.KOREAN, str(number), metadata={"problem_number": number}) for number in (41, 42)],
        metadata={"original_source_path": str(tmp_path / "b" / "second.hwp")})
    board._annotate_cross_page_passage_groups([first, second])
    assert not first.problems[0].metadata.get("passage_role")
    assert all(not problem.metadata.get("passage_group_id") for problem in second.problems)


@pytest.mark.parametrize("has_questions", [False, True])
def test_reading_directive_without_visible_shared_body_is_not_passage(tmp_path, has_questions):
    source = tmp_path / "empty-reading.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((40, 70), "[7~9] Read the passage and answer the questions.", fontsize=10)
        if has_questions:
            page.insert_text((40, 160), "7. What is the title?", fontsize=12)
            page.insert_text((40, 360), "8. Which statement is true?", fontsize=12)
            page.insert_text((330, 160), "9. Which word fits?", fontsize=12)
        document.save(source)
    prepared, models = board.build_pages(source, subject=Subject.UNKNOWN, ocr_mode="none", ai_fallback_config=None,
        pdf_dpi=144, detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None)
    entries = board.build_problem_entries(prepared, models, tmp_path / "out", LayoutTemplate(name="test"), content_target="shared-passages")
    assert entries == []


@pytest.mark.parametrize("body_type", ["vector", "raster"])
def test_low_text_shared_graphic_body_is_retained(tmp_path, body_type):
    import io
    from PIL import Image, ImageDraw
    source = tmp_path / f"shared-{body_type}.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((40, 70), "[7~9] Read the chart and answer the questions.", fontsize=10)
        if body_type == "vector":
            page.draw_rect((40, 100, 270, 260), color=(0, 0, 0))
            for x, top in ((70, 180), (120, 140), (170, 120)):
                page.draw_rect((x, top, x + 25, 240), color=(0, 0, 0), fill=(0, 0, 0))
        else:
            image = Image.new("RGB", (230, 160), "white")
            draw = ImageDraw.Draw(image)
            for x, top in ((30, 80), (80, 40), (130, 20)):
                draw.rectangle((x, top, x + 25, 140), fill="black")
            stream = io.BytesIO(); image.save(stream, format="PNG")
            page.insert_image((40, 100, 270, 260), stream=stream.getvalue())
        for number, y in ((7, 330), (8, 480), (9, 630)):
            page.insert_text((40, y), f"{number}. Which value is correct?", fontsize=12)
        document.save(source)
    prepared, models = board.build_pages(source, subject=Subject.UNKNOWN, ocr_mode="none", ai_fallback_config=None,
        pdf_dpi=144, detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None)
    entries = board.build_problem_entries(prepared, models, tmp_path / "out", LayoutTemplate(name="test"), content_target="shared-passages")
    assert len(entries) == 1
    assert entries[0].bounds.bottom >= 260 * 2


def test_header_only_page_seeds_actual_shared_body_on_next_page(tmp_path):
    source = tmp_path / "header-then-body.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((40, 710), "[41~42] Read the passage and answer the questions.", fontsize=10)
        page = document.new_page(width=600, height=800)
        page.draw_line((300, 45), (300, 755), color=(0, 0, 0), width=0.5)
        for index, y in enumerate(range(80, 251, 26)):
            page.insert_text((40, y), f"Actual common reading body line {index:02d}.", fontsize=10)
        page.insert_text((330, 90), "41. What is the title?", fontsize=12)
        page.insert_text((330, 420), "42. Which statement is true?", fontsize=12)
        document.save(source)
    prepared, models = board.build_pages(source, subject=Subject.UNKNOWN, ocr_mode="none", ai_fallback_config=None,
        pdf_dpi=144, detect_perspective=False, deskew=False, crop_margins=False, max_dimension=None)
    entries = board.build_problem_entries(prepared, models, tmp_path / "out", LayoutTemplate(name="test"), content_target="shared-passages")
    assert len(entries) == 1
    assert models[1].page_id in [segment["source_page_id"] for segment in entries[0].source_segments]


@pytest.mark.parametrize('same_document', [True, False])
def test_cached_hwp_pages_use_original_hwp_provenance_for_continuation(tmp_path, monkeypatch, same_document):
    from PIL import Image
    import preprocess
    from preprocess import NormalizedPageImage
    from structured_schema import PageModel, ProblemUnit
    hwp = tmp_path / 'one-original.hwp'
    hwp.write_bytes(b'cached HWP fixture')
    normalized = []
    for index in range(2):
        path = tmp_path / f'cached-{index}.png'
        Image.new('RGB', (600, 800), 'white').save(path)
        normalized.append(NormalizedPageImage(page_id=f'one-original-page-{index + 1:03d}',
            source_path=str(path), normalized_path=str(path), page_index=index,
            width_px=600, height_px=800,
            metadata={'source_type': 'hwp', 'source_hwp_path': str(hwp)}))
    monkeypatch.setattr(preprocess, 'prepare_pages', lambda *_args, **_kwargs: normalized)
    prepared = preprocess.prepare_source_pages(hwp)
    pages = [PageModel(p.page_id, 600, 800, Subject.KOREAN, p.source_path, metadata=dict(p.metadata)) for p in prepared]
    assert pages[0].metadata['original_source_path'] != pages[1].metadata['original_source_path']
    pages[0].problems = [ProblemUnit('tail', Subject.KOREAN, 'HWP continuation',
        metadata={'marker_document_continuation': True})]
    pages[1].problems = [ProblemUnit(f'q{n}', Subject.KOREAN, str(n), metadata={'problem_number': n}) for n in (41, 42)]
    if not same_document:
        pages[1].metadata['source_hwp_path'] = str(tmp_path / 'different-original.hwp')
    board._annotate_cross_page_passage_groups(pages)
    if same_document:
        assert board._passage_document_identity(pages[0]) == board._passage_document_identity(pages[1])
        assert pages[0].problems[0].metadata['passage_role'] == 'passage_fragment'
        assert all(p.metadata['passage_role'] == 'child_question' for p in pages[1].problems)
    else:
        assert board._passage_document_identity(pages[0]) != board._passage_document_identity(pages[1])
        assert not pages[0].problems[0].metadata.get('passage_role')
        assert all(not p.metadata.get('passage_group_id') for p in pages[1].problems)
