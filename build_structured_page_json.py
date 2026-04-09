#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ocr_backend import NoOcrBackend, create_ocr_backend
from preprocess import PreparedPage, prepare_source_pages
from segment import blocks_from_page, crop_block_image
from assemble_page import group_problem_units
from structured_schema import BlockType, PageModel, Subject, classify_text_block, infer_math_like_text, save_pages_json, TextStyle


def _resolve_subject(name: str | None) -> Subject:
    if not name:
        return Subject.UNKNOWN
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def build_run_summary(
    pages: list[PageModel],
    *,
    output_dir: str | Path,
    source: str | Path,
    ocr_mode: str,
) -> dict[str, object]:
    fallback_block_count = 0
    text_block_count = 0
    image_block_count = 0

    for page in pages:
        for block in page.blocks:
            if block.text:
                text_block_count += 1
            if block.block_type == BlockType.IMAGE:
                image_block_count += 1
            if block.metadata.get("fallback_reason"):
                fallback_block_count += 1

    return {
        "source": str(source),
        "output_dir": str(output_dir),
        "ocr_mode": ocr_mode,
        "page_count": len(pages),
        "problem_count": sum(len(page.problems) for page in pages),
        "block_count": sum(len(page.blocks) for page in pages),
        "text_block_count": text_block_count,
        "image_block_count": image_block_count,
        "fallback_block_count": fallback_block_count,
        "pages_json_path": str(Path(output_dir) / "pages.json"),
    }


def build_page_model(prepared_page: PreparedPage, subject: Subject, ocr_mode: str) -> PageModel:
    backend = create_ocr_backend(ocr_mode)
    blocks = blocks_from_page(prepared_page)

    for block in blocks:
        if block.block_type in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
            continue

        crop = crop_block_image(prepared_page, block)
        ocr_result = backend.recognize(crop)
        block.metadata["ocr_backend"] = ocr_result.backend_name
        if ocr_result.text.strip():
            block.text = ocr_result.text.strip()
            block.confidence = ocr_result.confidence
            block.ocr_lines = list(ocr_result.lines)
            block.style = TextStyle(
                font_size=max(10.0, block.bbox.height * 0.35),
                math_like=infer_math_like_text(block.text),
            )
            inferred = classify_text_block(block.text)
            if inferred != BlockType.STEM or block.block_type == BlockType.STEM:
                block.block_type = inferred
        elif isinstance(backend, NoOcrBackend) and block.block_type == BlockType.STEM:
            block.block_type = BlockType.IMAGE
            block.metadata["fallback_reason"] = "noop_ocr"

    page = PageModel(
        page_id=prepared_page.page_id,
        width_px=prepared_page.image.width,
        height_px=prepared_page.image.height,
        subject=subject,
        source_path=prepared_page.source_path,
        blocks=blocks,
        metadata=dict(prepared_page.metadata),
    )
    return group_problem_units(page)


def build_pages_from_source(
    source: str | Path,
    *,
    subject: Subject = Subject.UNKNOWN,
    ocr_mode: str = "auto",
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    deskew: bool = True,
    crop_margins: bool = True,
    max_dimension: int | None = None,
) -> list[PageModel]:
    prepared_pages = prepare_source_pages(
        source,
        pdf_dpi=pdf_dpi,
        detect_perspective=detect_perspective,
        deskew=deskew,
        crop_margins=crop_margins,
        max_dimension=max_dimension,
    )
    return [build_page_model(prepared_page, subject=subject, ocr_mode=ocr_mode) for prepared_page in prepared_pages]


def process_source(
    source: str | Path,
    output_dir: str | Path,
    *,
    subject: Subject = Subject.UNKNOWN,
    ocr_mode: str = "auto",
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    deskew: bool = True,
    crop_margins: bool = True,
    max_dimension: int | None = None,
) -> list[PageModel]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = build_pages_from_source(
        source,
        subject=subject,
        ocr_mode=ocr_mode,
        pdf_dpi=pdf_dpi,
        detect_perspective=detect_perspective,
        deskew=deskew,
        crop_margins=crop_margins,
        max_dimension=max_dimension,
    )
    for page in pages:
        page.metadata["schema_version"] = "v0.2"
        page.metadata["ocr_mode"] = ocr_mode
    save_pages_json(pages, out_dir / "pages.json")
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description="Build structured page JSON from a PDF or image source.")
    parser.add_argument("source", help="Path to a PDF or image file")
    parser.add_argument("--output-dir", default="pipeline_output", help="Directory for generated JSON and assets")
    parser.add_argument("--subject", default="unknown", help="Subject hint: math, science, korean, english, social, unknown")
    parser.add_argument("--ocr", default="auto", help="OCR backend: auto, paddleocr, tesseract, none")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="PDF render DPI")
    parser.add_argument("--detect-perspective", action="store_true", help="Try perspective correction for photographed sources")
    parser.add_argument("--skip-deskew", action="store_true", help="Disable deskew")
    parser.add_argument("--skip-crop", action="store_true", help="Disable margin crop")
    parser.add_argument("--max-dimension", type=int, default=None, help="Resize long edge to this many pixels")
    args = parser.parse_args()

    pages = process_source(
        args.source,
        args.output_dir,
        subject=_resolve_subject(args.subject),
        ocr_mode=args.ocr,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        deskew=not args.skip_deskew,
        crop_margins=not args.skip_crop,
        max_dimension=args.max_dimension,
    )
    run_summary = build_run_summary(
        pages,
        output_dir=args.output_dir,
        source=args.source,
        ocr_mode=args.ocr,
    )
    summary_path = Path(args.output_dir) / "run_summary.json"
    summary_path.write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
