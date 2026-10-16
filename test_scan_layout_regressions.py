"""Regressions for bordered boxes, scanned two-column pages and leftovers.

Synthetic two-column Korean exam pages are rendered with PyMuPDF.  Each test
covers one failure seen on real-looking layouts:

* <보기> boxes: "ㄱ." list items inside a box were read as problem numbers
  and split the question at the box; the fixed column gutter clipped the
  box's right border; the minAreaRect deskew tilted clean pages.
* Scanned pages: a passage / choice line continuing at the top of a column
  (from the previous column or page) was silently dropped.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

import cv2
from build_structured_page_json import build_page_model
from page_repair import build_ai_fallback_config
from preprocess import _estimate_skew_angle_by_projection, prepare_source_pages
from structured_schema import Subject

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/sand-box/google/Nanum Gothic/NanumGothic-Regular.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",
)
FONT = next((path for path in FONT_CANDIDATES if Path(path).exists()), None)
W, H = 595, 842
COLS = ((40, 285), (310, 555))
LINE = "이 글은 사회 변화에 대한 필자의 관점을 서술한다."


class _ExamWriter:
    def __init__(self) -> None:
        self.doc = fitz.open()
        self._new_page()

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=W, height=H)
        self.page.insert_font(fontname="ng", fontfile=FONT)
        self.column = 0
        self.y = 60
        self.page.draw_line((297, 50), (297, 800), width=0.6)
        self.page.insert_text((260, 825), f"- {len(self.doc)} -", fontname="ng", fontsize=8)

    def _advance(self, needed: float) -> None:
        if self.y + needed > 790:
            if self.column == 0:
                self.column, self.y = 1, 60
            else:
                self._new_page()

    def text(self, value: str, indent: float = 0) -> None:
        self._advance(14)
        self.page.insert_text((COLS[self.column][0] + indent, self.y), value, fontname="ng", fontsize=9)
        self.y += 14

    def box(self, lines: list[str]) -> None:
        height = 14 * len(lines) + 16
        self._advance(height)
        x0, x1 = COLS[self.column]
        self.page.draw_rect(fitz.Rect(x0, self.y - 4, x1, self.y - 4 + height), width=0.8)
        self.page.insert_text(((x0 + x1) / 2 - 12, self.y + 6), "<보 기>", fontname="ng", fontsize=9)
        self.y += 20
        for value in lines:
            self.page.insert_text((x0 + 8, self.y), value, fontname="ng", fontsize=9)
            self.y += 14
        self.y += 6

    def question(self, number: int, *, boxed: bool = False) -> None:
        self._advance(40)
        self.text(f"{number}. 윗글에 대한 설명으로 가장 적절한 것은?")
        self.text("  조건을 고려하여 답을 고르시오.")
        if boxed:
            self.box(["ㄱ. 첫째 진술은 옳다.", "ㄴ. 둘째 진술은 옳지 않다.", "ㄷ. 셋째 진술은 옳다."])
        for index, mark in enumerate("①②③④⑤"):
            self.text(f"{mark} 선택지 {index + 1}번 내용입니다.", indent=6)
        self.y += 16


def _box_exam(writer: _ExamWriter) -> None:
    for number in range(1, 9):
        writer.question(number, boxed=number in (2, 3, 6))


def _long_passage_exam(writer: _ExamWriter) -> None:
    writer.text("[1~3] 다음 글을 읽고 물음에 답하시오.")
    for _ in range(140):
        writer.text(LINE)
    writer.y += 10
    for number in range(1, 6):
        writer.question(number)


def _save(builder, path: Path, *, scanned: bool) -> Path:
    writer = _ExamWriter()
    builder(writer)
    if not scanned:
        writer.doc.save(path)
        return path
    scan = fitz.open()
    for page in writer.doc:
        pixmap = page.get_pixmap(dpi=200)
        target = scan.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, pixmap=pixmap)
    scan.save(path)
    return path


def _models(path: Path) -> list:
    pages = prepare_source_pages(path, pdf_dpi=200, detect_perspective=False, deskew=True, crop_margins=True)
    return [
        build_page_model(page, subject=Subject.KOREAN, ocr_mode="none", ai_config=build_ai_fallback_config(mode="off"))
        for page in pages
    ]


def _all_ids(problem) -> list[str]:
    return [
        *problem.stem_block_ids,
        *problem.choice_block_ids,
        *problem.explanation_block_ids,
        *problem.figure_block_ids,
    ]


def _problem_boxes(model) -> list[list]:
    by_id = {block.block_id: block for block in model.blocks}
    result = []
    for problem in model.problems:
        ids = [*problem.stem_block_ids, *problem.choice_block_ids, *problem.figure_block_ids]
        result.append([by_id[block_id].bbox for block_id in ids if block_id in by_id])
    return result


def _uncovered_ink_components(model, image: Image.Image) -> list[tuple[int, int, int, int]]:
    ink = np.array(image.convert("L")) < 200
    ink[:, ink.sum(axis=0) >= ink.shape[0] * 0.42] = False  # centre rule
    covered = np.zeros_like(ink)
    for block in model.blocks:
        box = block.bbox
        covered[int(box.top) : int(box.bottom) + 1, int(box.left) : int(box.right) + 1] = True
    leftover = cv2.dilate((ink & ~covered).astype("uint8"), np.ones((15, 15), np.uint8))
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(leftover)
    return [tuple(int(v) for v in stats[i][:4]) for i in range(1, count) if stats[i][4] > 1500]


@unittest.skipIf(FONT is None, "Korean font required for synthetic exam pages")
class TestScanLayoutRegressions(unittest.TestCase):
    def test_projection_deskew_keeps_straight_pages_and_fixes_real_skew(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _save(_box_exam, Path(temp_dir) / "box.pdf", scanned=False)
            pixmap = fitz.open(path)[0].get_pixmap(dpi=150)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            for applied in (0.0, 1.5, -2.0):
                rotated = image.rotate(applied, fillcolor="white", resample=Image.BICUBIC)
                gray = np.array(rotated.convert("L"))
                _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
                estimate = _estimate_skew_angle_by_projection(thresh)
                self.assertAlmostEqual(-applied, estimate, delta=0.15)

    def test_scanned_boxed_questions_are_not_split_or_dropped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _save(_box_exam, Path(temp_dir) / "box_scan.pdf", scanned=True)
            [model] = _models(path)
            self.assertEqual("visual-problem-markers", model.metadata.get("segmenter"))
            boxes = _problem_boxes(model)
            self.assertEqual(8, len(boxes))
            heights = sorted(round(group[0].height) for group in boxes)
            # three boxed questions are taller than the five plain ones
            self.assertGreater(heights[-3], heights[0] * 1.4)
            self.assertLess(heights[-1], heights[0] * 2.0)

    def test_pdf_boxed_question_crop_keeps_box_right_border(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _save(_box_exam, Path(temp_dir) / "box.pdf", scanned=False)
            pages = prepare_source_pages(path, pdf_dpi=200, detect_perspective=False, deskew=True, crop_margins=True)
            model = build_page_model(pages[0], subject=Subject.KOREAN, ocr_mode="none", ai_config=build_ai_fallback_config(mode="off"))
            self.assertEqual([], _uncovered_ink_components(model, pages[0].image))

    def test_scanned_cross_page_passage_and_column_top_continuations_are_kept(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _save(_long_passage_exam, Path(temp_dir) / "long_scan.pdf", scanned=True)
            pages = prepare_source_pages(path, pdf_dpi=200, detect_perspective=False, deskew=True, crop_margins=True)
            self.assertEqual(2, len(pages))
            second = build_page_model(pages[1], subject=Subject.KOREAN, ocr_mode="none", ai_config=build_ai_fallback_config(mode="off"))
            self.assertEqual([], _uncovered_ink_components(second, pages[1].image))
            leading = sorted(
                (b for b in second.blocks if b.metadata.get("visual_leading_continuation")),
                key=lambda b: b.bbox.left,
            )
            # left column: passage continued from page 1; right column: the
            # last choice line of question 2 wrapped to the next column.
            self.assertEqual(2, len(leading))
            passage, wrapped_choice = leading
            self.assertLess(passage.bbox.top, second.height_px * 0.05)
            self.assertGreater(passage.bbox.height, second.height_px * 0.5)
            self.assertLess(wrapped_choice.bbox.height, second.height_px * 0.05)
            owner = next(p for p in second.problems if wrapped_choice.block_id in _all_ids(p))
            by_id = {b.block_id: b for b in second.blocks}
            owner_tops = [by_id[i].bbox.top for i in _all_ids(owner) if i != wrapped_choice.block_id]
            # attached to the last question of the LEFT column (question 2)
            self.assertTrue(all(top > second.height_px * 0.7 for top in owner_tops))

    def test_pdf_column_top_choice_continuation_joins_previous_column_question(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _save(_long_passage_exam, Path(temp_dir) / "long.pdf", scanned=False)
            pages = prepare_source_pages(path, pdf_dpi=200, detect_perspective=False, deskew=True, crop_margins=True)
            second = build_page_model(pages[1], subject=Subject.KOREAN, ocr_mode="none", ai_config=build_ai_fallback_config(mode="off"))
            recovered = [b for b in second.blocks if b.metadata.get("recovered_uncovered_region")]
            self.assertEqual(1, len(recovered))
            owner = next(p for p in second.problems if recovered[0].block_id in _all_ids(p))
            self.assertEqual(2, owner.metadata.get("problem_number"))


if __name__ == "__main__":
    unittest.main()
