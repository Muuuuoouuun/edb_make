"""Read labels from existing gallery crops without changing their pixels.

No cloud calls or sequential-number guessing. A missing number is not evidence
of a passage: shared passages need an explicit range/instruction heading.
"""
from __future__ import annotations

import math
import re

from PIL import Image

from assemble_page import extract_problem_number, extract_problem_number_from_block
from ocr_backend import OCRLine, build_ocr_backend
from passage_detection import parse_shared_passage_range_header
from structured_schema import BlockType, Box, ContentBlock

LABEL_FIELDS = ("problemNumberSource", "passageRole", "passageRange")
LABEL_REVIEW_FLAG = "number_unconfirmed"
MAX_RECOGNITION_BATCH = 8


def label_is_confirmed(record: dict) -> bool:
    if record.get("passageRole") == "passage_fragment":
        return True
    number = record.get("number")
    source = record.get("problemNumberSource", "")
    return type(number) is int and number > 0 and (
        source == "manual" or source.startswith(("ocr_", "text_"))
        or source in {"pdf_text_marker", "hwp_text_snippet"}
    )


def _visual_lines(result):
    """Tesseract returns words; regroup by row before matching range headings."""
    rows = []
    for word in sorted(result.lines, key=lambda line: (line.bbox.top, line.bbox.left)):
        if not word.text.strip():
            continue
        row = next((row for row in reversed(rows) if
            min(row["bottom"], word.bbox.bottom) - max(row["top"], word.bbox.top)
            >= .5 * min(row["bottom"] - row["top"], word.bbox.height)), None)
        if row is None:
            row = {"top": word.bbox.top, "bottom": word.bbox.bottom, "words": []}
            rows.append(row)
        row["top"] = min(row["top"], word.bbox.top)
        row["bottom"] = max(row["bottom"], word.bbox.bottom)
        row["words"].append(word)
    lines = []
    for row in sorted(rows, key=lambda row: row["top"]):
        words = sorted(row["words"], key=lambda word: word.bbox.left)
        left, right = min(word.bbox.left for word in words), max(word.bbox.right for word in words)
        confidences = [word.confidence for word in words if word.confidence is not None and math.isfinite(word.confidence)]
        lines.append(OCRLine(" ".join(word.text.strip() for word in words),
            confidences[0] if confidences else 0,
            Box(left=left, top=row["top"], width=right-left, height=row["bottom"]-row["top"])))
    return lines


def read_image_label(image: Image.Image, backend) -> dict | None:
    # Bound OCR work even for tall stitched passages. The image itself is untouched.
    with image.convert("RGB") as crop:
        crop.thumbnail((1800, 2400), Image.Resampling.LANCZOS)
        result = backend.ocr_image(crop)
        if result.metadata.get("error"):
            raise ValueError("이미지의 번호를 읽지 못했습니다. OCR 엔진과 한국어·영어 언어 데이터를 확인해 주세요.")
        lines = _visual_lines(result)
        if not lines or lines[0].confidence < .65:
            return None
        heading = " ".join(line.text for line in lines[:4])
        # Local OCR frequently inserts spaces between Korean syllables.
        heading = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", heading)
        header = parse_shared_passage_range_header(heading)
        explicit_passage = re.fullmatch(r"\s*(?:(?:공통\s*)?지문|(?:reading\s+)?passage)\s*[:：]?\s*", lines[0].text, re.I)
        if header or explicit_passage:
            # A crop containing both passage AND questions is ambiguous; don't
            # relabel the whole image as a standalone passage.
            if any(extract_problem_number(line.text) is not None for line in lines[1:]):
                return None
            return {"number": None, "problemNumberSource": "ocr_passage_header",
                "passageRole": "passage_fragment",
                "passageRange": {"start": header.start, "end": header.end} if header else None}
        block = ContentBlock("gallery-label", BlockType.STEM,
            Box(left=0, top=0, width=crop.width, height=crop.height), 0,
            text=lines[0].text, ocr_lines=lines[:2])
        number, source = extract_problem_number_from_block(block)
        if number is None:
            return None
        return {"number": number, "problemNumberSource": source or "ocr_top_left",
            "passageRole": "", "passageRange": None}


def local_label_backend():
    backend = build_ocr_backend("local", refresh_capabilities=True)
    if backend.name == "none":
        raise ValueError("사진 속 번호 인식에는 로컬 OCR이 필요합니다. Tesseract와 한국어·영어 언어 데이터 또는 PaddleOCR을 설치해 주세요.")
    if backend.name == "tesseract":
        # A crop is a single text block, unlike a full multi-column exam page.
        backend.config = "--psm 6"
    return backend
