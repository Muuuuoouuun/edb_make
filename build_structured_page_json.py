#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from assemble_page import group_problem_units
from ocr_backend import build_ocr_backend
from preprocess import prepare_pages
from segment import crop_block_images, segment_page
from structured_schema import AssetRef, AssetType, BlockType, Subject, TextStyle, classify_text_block, infer_math_like_text, save_pages_json


def _resolve_subject(name: str) -> Subject:
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def process_source(
    source: str | Path,
    output_dir: str | Path,
    *,
    subject: Subject = Subject.UNKNOWN,
    ocr_name: str = "auto",
    dpi: int = 160,
    low_confidence_threshold: float = 0.55,
):
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    normalized_pages = prepare_pages(source, out_dir / "preprocess", dpi=dpi)
    ocr_backend = build_ocr_backend(ocr_name)
    pages = []

    for normalized in normalized_pages:
        page = segment_page(normalized.normalized_path, page_id=normalized.page_id, subject=subject)
        normalized_image = Image.open(normalized.normalized_path).convert("RGB")
        asset_paths = crop_block_images(normalized.normalized_path, page.blocks, out_dir / "assets" / normalized.page_id)

        for block in page.blocks:
            block.asset = AssetRef(
                asset_id=f"{block.block_id}-crop",
                asset_type=AssetType.CROP,
                source_path=asset_paths.get(block.block_id),
                crop_box=block.bbox,
                width_px=int(block.bbox.width),
                height_px=int(block.bbox.height),
            )

            if block.block_type == BlockType.IMAGE:
                continue

            result = ocr_backend.ocr_box(normalized_image, block.bbox)
            block.confidence = result.confidence
            if result.text.strip():
                block.text = result.text.strip()
                block.ocr_lines = list(result.lines)
                block.style = TextStyle(
                    font_size=max(10.0, block.bbox.height * 0.35),
                    math_like=infer_math_like_text(block.text),
                )
                block.block_type = classify_text_block(block.text)
            elif result.metadata.get("backend") == "noop" and block.block_type == BlockType.STEM:
                block.block_type = BlockType.IMAGE
                block.metadata["fallback_reason"] = "noop_ocr"
            if result.confidence is not None and result.confidence < low_confidence_threshold and block.block_type == BlockType.FORMULA:
                block.block_type = BlockType.IMAGE
                block.metadata["fallback_reason"] = "low_confidence_formula"

        page.source_path = normalized.normalized_path
        page.metadata.update(normalized.metadata)
        pages.append(group_problem_units(page))

    save_pages_json(pages, out_dir / "pages.json")
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description="Build structured page JSON from a PDF or image source.")
    parser.add_argument("source", help="Path to a PDF or image file")
    parser.add_argument("--output-dir", default="pipeline_output", help="Directory for generated JSON and normalized assets")
    parser.add_argument("--subject", default="unknown", help="Subject hint: math, science, korean, english, social, unknown")
    parser.add_argument("--ocr", default="auto", help="OCR backend: auto, paddleocr, tesseract, noop")
    parser.add_argument("--dpi", type=int, default=160, help="PDF render DPI")
    parser.add_argument("--low-confidence-threshold", type=float, default=0.55, help="Threshold for formula fallback to image blocks")
    args = parser.parse_args()

    process_source(
        args.source,
        args.output_dir,
        subject=_resolve_subject(args.subject),
        ocr_name=args.ocr,
        dpi=args.dpi,
        low_confidence_threshold=args.low_confidence_threshold,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
