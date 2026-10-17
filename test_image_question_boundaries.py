"""Workbook-image regressions; synthetic layout based on a reported 2-column page.

The fixture is not the user's original image. It preserves the asymmetric
question starts, colored numbers, outer frame, center rule and distant choices.
"""
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from segment import SegmentOptions, _segment_visual_problem_markers, segment_page
from structured_schema import Subject


def workbook_image(scale=1, *, missing_right_numbers=False):
    image = Image.new("RGB", (800, 1132), "white")
    draw = ImageDraw.Draw(image)
    # Pillow's bundled font keeps the fixture identical on Windows and Mac,
    # without depending on a Linux system font being installed.
    font = ImageFont.load_default(size=22)
    draw.rectangle((38, 57, 762, 1066), outline=(60, 60, 60))
    draw.line((400, 69, 400, 1060), fill=(145, 145, 145))
    starts = [(57, 74, 7), (57, 419, 8), (57, 738, 9),
              (410, 74, 10), (410, 452, 11), (410, 766, 12)]
    for left, top, number in starts:
        if not (missing_right_numbers and left > 400):
            draw.text((left, top), f"{number}.", font=font, fill=(50, 145, 157), anchor="lt")
        for y in (top + 7, top + 29):
            draw.rectangle((left + 45, y, left + 315, y + 3), fill=(45, 45, 45))
        draw.polygon([(left + 100, top + 70), (left + 200, top + 160),
                      (left + 100, top + 160)], outline=(60, 60, 60))
        for y in (top + 195, top + 220, top + 245):
            draw.text((left + 15, y), "(1) a     (2) b", fill=(40, 40, 40))
    if scale != 1:
        image = image.resize((800 * scale, 1132 * scale))
    return image, starts


@pytest.mark.parametrize("scale", [1, 2, 3])
def test_framed_image_splits_six_questions_and_keeps_choices(scale):
    image, starts = workbook_image(scale)
    source = SimpleNamespace(image=image, metadata={"source_type": "image"}, source_path="workbook.png")
    page = segment_page(source, page_id="workbook", subject=Subject.MATH)
    assert page.metadata["column_count"] == 2
    assert page.metadata["segmenter"] == "visual-problem-markers"
    assert len(page.blocks) == 6
    assert [block.metadata["column_index"] for block in page.blocks] == [1, 1, 1, 2, 2, 2]
    for block, (left, top, _number) in zip(page.blocks, starts):
        assert block.bbox.top <= top * scale
        assert block.bbox.bottom >= (top + 251) * scale
        assert block.bbox.left <= left * scale
    # Each column has its own boundaries; row alignment would cut problem 10
    # at the earlier start of 8 or include problem 11 in problem 8.
    assert page.blocks[1].bbox.top < page.blocks[4].bbox.top
    for first, following in ((0, 1), (1, 2), (3, 4), (4, 5)):
        assert page.blocks[first].bbox.bottom < starts[following][1] * scale


def test_partial_marker_detection_never_discards_unmarked_column():
    image, _ = workbook_image(missing_right_numbers=True)
    assert _segment_visual_problem_markers(image, "partial", SegmentOptions()) is None
    source = SimpleNamespace(image=image, metadata={"source_type": "image"}, source_path="partial.png")
    page = segment_page(source, page_id="partial", subject=Subject.MATH)
    assert any(block.bbox.left > 400 for block in page.blocks)
    assert "image_marker_boundaries_unverified" in page.metadata["fallback_reasons"]
    from pipeline_router import decide_page_route
    route = decide_page_route(page, ocr_mode="auto", ai_enabled=True, ai_mode="auto")
    assert route.should_use_ai
    assert "image_marker_boundaries_unverified" in route.trigger_reasons
    assert not decide_page_route(page, ocr_mode="none", ai_enabled=False, ai_mode="off").should_use_ai


def test_monochrome_image_without_verified_numbers_requests_repair():
    from pipeline_router import decide_page_route

    image, _ = workbook_image()
    source = SimpleNamespace(image=image.convert("L").convert("RGB"),
                             metadata={"source_type": "image"}, source_path="gray.png")
    page = segment_page(source, page_id="gray", subject=Subject.MATH)
    route = decide_page_route(page, ocr_mode="auto", ai_enabled=True, ai_mode="auto")
    assert "image_marker_boundaries_unverified" in route.trigger_reasons
    assert route.should_use_ai


def test_six_question_image_keeps_six_grouped_problems_without_ocr(tmp_path, monkeypatch):
    import build_structured_page_json as pipeline
    from page_repair import build_ai_fallback_config
    from preprocess import PreparedPage

    image, _ = workbook_image(2)
    source = tmp_path / "workbook.png"
    image.save(source)
    prepared = PreparedPage(page_id="workbook", source_path=str(source), page_number=1,
                            image=image, original_size=image.size, metadata={"source_type": "image"})

    def unexpected_ocr(*_args, **_kwargs):
        raise AssertionError("verified visual boundaries should not need OCR")

    monkeypatch.setattr(pipeline, "create_ocr_backend", unexpected_ocr)
    page = pipeline.build_page_model(prepared, subject=Subject.MATH, ocr_mode="none",
                                     ai_config=build_ai_fallback_config(mode="off"))
    assert len(page.blocks) == len(page.problems) == 6
    assert page.metadata["ocr_eligible_block_count"] == 0
