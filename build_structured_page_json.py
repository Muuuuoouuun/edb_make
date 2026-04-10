#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ocr_backend import ClaudeOCRBackend, NoOcrBackend, create_ocr_backend
from page_repair import AIFallbackConfig, build_ai_fallback_config, repair_page_model
from preprocess import PreparedPage, prepare_source_pages
from segment import crop_block_image, draw_segment_debug, segment_page
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
    ai_config: AIFallbackConfig | None = None,
) -> dict[str, object]:
    fallback_block_count = 0
    text_block_count = 0
    image_block_count = 0
    ai_attempted_pages = 0
    ai_applied_pages = 0

    for page in pages:
        ai_summary = page.metadata.get("ai_fallback")
        if isinstance(ai_summary, dict):
            if ai_summary.get("attempted"):
                ai_attempted_pages += 1
            if ai_summary.get("applied"):
                ai_applied_pages += 1
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
        "ai_fallback": (ai_config or AIFallbackConfig()).to_metadata(),
        "ai_attempted_page_count": ai_attempted_pages,
        "ai_applied_page_count": ai_applied_pages,
        "pages_json_path": str(Path(output_dir) / "pages.json"),
    }


def build_page_model(
    prepared_page: PreparedPage,
    subject: Subject,
    ocr_mode: str,
    *,
    ai_config: AIFallbackConfig | None = None,
) -> PageModel:
    backend = create_ocr_backend(ocr_mode)
    segmented_page = segment_page(prepared_page, page_id=prepared_page.page_id, subject=subject)
    blocks = segmented_page.blocks

    for block in blocks:
        if block.block_type in {BlockType.IMAGE, BlockType.DIAGRAM, BlockType.TABLE}:
            continue

        crop = crop_block_image(prepared_page, block)
        ocr_result = backend.recognize(crop)
        block.metadata["ocr_backend"] = ocr_result.backend_name
        block_type_hint = ocr_result.metadata.get("block_type_hint", "")
        if ocr_result.text.strip():
            block.text = ocr_result.text.strip()
            block.confidence = ocr_result.confidence
            block.ocr_lines = list(ocr_result.lines)
            block.style = TextStyle(
                font_size=max(10.0, block.bbox.height * 0.35),
                math_like=infer_math_like_text(block.text),
            )
            # Prefer Claude's block_type_hint when available; otherwise infer from text
            if block_type_hint and block_type_hint not in {"unknown", "stem"}:
                hint_map = {
                    "choice": BlockType.CHOICE,
                    "figure": BlockType.IMAGE,
                    "formula": BlockType.FORMULA,
                    "title": BlockType.TITLE,
                    "explanation": BlockType.EXPLANATION,
                }
                if block_type_hint in hint_map:
                    block.block_type = hint_map[block_type_hint]
                    block.metadata["block_type_source"] = "claude_hint"
                else:
                    inferred = classify_text_block(block.text)
                    if inferred != BlockType.STEM or block.block_type == BlockType.STEM:
                        block.block_type = inferred
            else:
                inferred = classify_text_block(block.text)
                if inferred != BlockType.STEM or block.block_type == BlockType.STEM:
                    block.block_type = inferred
        elif block_type_hint == "figure":
            block.block_type = BlockType.IMAGE
            block.metadata["fallback_reason"] = "claude_figure_hint"
        elif isinstance(backend, (NoOcrBackend, ClaudeOCRBackend)) and block.block_type == BlockType.STEM:
            block.block_type = BlockType.IMAGE
            block.metadata["fallback_reason"] = "noop_ocr" if isinstance(backend, NoOcrBackend) else "claude_no_text"

    page = PageModel(
        page_id=prepared_page.page_id,
        width_px=prepared_page.image.width,
        height_px=prepared_page.image.height,
        subject=subject,
        source_path=prepared_page.source_path,
        blocks=blocks,
        metadata={
            **dict(prepared_page.metadata),
            **dict(segmented_page.metadata),
            "ocr_mode": ocr_mode,
        },
    )
    return repair_page_model(prepared_page, page, ocr_mode=ocr_mode, config=ai_config)


def build_pages_from_source(
    source: str | Path,
    *,
    subject: Subject = Subject.UNKNOWN,
    ocr_mode: str = "auto",
    ai_config: AIFallbackConfig | None = None,
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
    return [
        build_page_model(
            prepared_page,
            subject=subject,
            ocr_mode=ocr_mode,
            ai_config=ai_config,
        )
        for prepared_page in prepared_pages
    ]


def process_source(
    source: str | Path,
    output_dir: str | Path,
    *,
    subject: Subject = Subject.UNKNOWN,
    ocr_mode: str = "auto",
    ai_config: AIFallbackConfig | None = None,
    pdf_dpi: int = 200,
    detect_perspective: bool = False,
    deskew: bool = True,
    crop_margins: bool = True,
    max_dimension: int | None = None,
    debug_segments: bool = False,
) -> list[PageModel]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared_pages = prepare_source_pages(
        source,
        pdf_dpi=pdf_dpi,
        detect_perspective=detect_perspective,
        deskew=deskew,
        crop_margins=crop_margins,
        max_dimension=max_dimension,
    )
    pages = [
        build_page_model(
            prepared_page,
            subject=subject,
            ocr_mode=ocr_mode,
            ai_config=ai_config,
        )
        for prepared_page in prepared_pages
    ]
    for page in pages:
        page.metadata["schema_version"] = "v0.2"
        page.metadata["ocr_mode"] = ocr_mode
        page.metadata["ai_config"] = (ai_config or AIFallbackConfig()).to_metadata()

    if debug_segments:
        debug_dir = out_dir / "debug_segments"
        for prepared_page, page in zip(prepared_pages, pages):
            debug_path = debug_dir / f"{page.page_id}_segments.png"
            draw_segment_debug(prepared_page, page.blocks, debug_path)

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
    parser.add_argument("--ai-fallback", default="off", help="AI fallback mode: off, auto, force")
    parser.add_argument("--ai-provider", default="openai", help="AI fallback provider: openai, claude (ANTHROPIC_API_KEY required)")
    parser.add_argument("--ai-model", default="", help="AI model override (default: claude-sonnet-4-6 for Claude, gpt-4o-mini for OpenAI)")
    parser.add_argument("--ai-threshold", type=float, default=0.72, help="Low-confidence trigger threshold for AI fallback")
    parser.add_argument("--ai-max-regions", type=int, default=18, help="Maximum number of blocks to send to AI fallback")
    parser.add_argument("--ai-timeout-ms", type=int, default=12000, help="Timeout in milliseconds for AI fallback requests")
    parser.add_argument("--ai-save-debug", action="store_true", help="Write AI fallback debug artifacts under .pipeline_cache/ai_debug")
    parser.add_argument("--fail-on-ai-error", action="store_true", help="Raise an error instead of silently skipping on AI fallback failures")
    parser.add_argument("--debug-segments", action="store_true", help="Save block overlay images to <output-dir>/debug_segments/ for segmentation inspection")
    args = parser.parse_args()
    ai_config = build_ai_fallback_config(
        mode=args.ai_fallback,
        provider=args.ai_provider,
        model=args.ai_model,
        threshold=args.ai_threshold,
        max_regions=args.ai_max_regions,
        timeout_ms=args.ai_timeout_ms,
        save_debug=args.ai_save_debug,
        fail_on_error=args.fail_on_ai_error,
    )

    pages = process_source(
        args.source,
        args.output_dir,
        subject=_resolve_subject(args.subject),
        ocr_mode=args.ocr,
        ai_config=ai_config,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        deskew=not args.skip_deskew,
        crop_margins=not args.skip_crop,
        max_dimension=args.max_dimension,
        debug_segments=args.debug_segments,
    )
    run_summary = build_run_summary(
        pages,
        output_dir=args.output_dir,
        source=args.source,
        ocr_mode=args.ocr,
        ai_config=ai_config,
    )
    summary_path = Path(args.output_dir) / "run_summary.json"
    summary_path.write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
