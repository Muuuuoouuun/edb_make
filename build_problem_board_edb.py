#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from build_structured_page_json import build_page_model
from edb_builder import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ImageRecordSpec,
    TextRecordSpec,
    build_edb,
    build_image_record,
    build_preview_image_bytes,
    build_text_record,
    normalize_height_px,
    normalize_width_px,
    normalize_x_px,
    normalize_y_px,
    write_edb,
)
from layout_template_schema import LayoutTemplate, ProblemLayoutInput
from placement_engine import place_problems
from preprocess import PreparedPage, prepare_source_pages
from structured_schema import BlockType, Box, ContentBlock, PageModel, ProblemUnit, Subject, save_pages_json


LEFT_MARGIN_PX = 84.0
TOP_PADDING_PX = 20.0
RIGHT_PADDING_PX = 54.0
PROBLEM_PADDING_PX = 18.0
MIN_HEIGHT_PAGES = 0.72
MAX_HEIGHT_PAGES = 4.8
MIN_PROBLEM_AREA_RATIO = 0.12
TEXT_ELIGIBLE_BLOCK_TYPES = {
    BlockType.TITLE,
    BlockType.SECTION,
    BlockType.STEM,
    BlockType.CHOICE,
    BlockType.EXPLANATION,
    BlockType.NOTE,
}
IMAGE_ONLY_BLOCK_TYPES = {
    BlockType.IMAGE,
    BlockType.DIAGRAM,
    BlockType.TABLE,
    BlockType.DECORATION,
    BlockType.FORMULA,
}


@dataclass(slots=True)
class ProblemEntry:
    problem_id: str
    title: str
    subject: Subject
    source_page_id: str
    source_path: str
    prepared_page: PreparedPage
    bounds: Box
    crop_path: Path
    blocks: list[ContentBlock]
    actual_height_pages: float
    overflow_allowed: bool
    reading_heavy: bool


def resolve_subject(name: str | None) -> Subject:
    if not name:
        return Subject.UNKNOWN
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def iter_problem_block_ids(page: PageModel, problem: ProblemUnit) -> list[str]:
    ordered: list[str] = []
    for block_id in (
        *problem.stem_block_ids,
        *problem.choice_block_ids,
        *problem.explanation_block_ids,
        *problem.figure_block_ids,
    ):
        if block_id not in ordered:
            ordered.append(block_id)
    if ordered:
        return ordered
    return [block.block_id for block in page.blocks]


def merge_boxes(boxes: list[Box], *, page_width: int, page_height: int, padding_px: int = PROBLEM_PADDING_PX) -> Box:
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.right for box in boxes)
    bottom = max(box.bottom for box in boxes)
    return Box.from_points(left, top, right, bottom).expanded(
        float(padding_px),
        max_width=float(page_width),
        max_height=float(page_height),
    )


def estimate_height_pages(image_size: tuple[int, int], template: LayoutTemplate) -> float:
    width_px, height_px = image_size
    available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX
    scaled_height_px = available_width_px * (height_px / max(width_px, 1))
    estimated = scaled_height_px / CANVAS_WIDTH
    return max(MIN_HEIGHT_PAGES, min(MAX_HEIGHT_PAGES, estimated))


def build_pages(
    source: str | Path,
    *,
    subject: Subject,
    ocr_mode: str,
    pdf_dpi: int,
    detect_perspective: bool,
    deskew: bool,
    crop_margins: bool,
    max_dimension: int | None,
) -> tuple[list[PreparedPage], list[PageModel]]:
    prepared_pages = prepare_source_pages(
        source,
        pdf_dpi=pdf_dpi,
        detect_perspective=detect_perspective,
        deskew=deskew,
        crop_margins=crop_margins,
        max_dimension=max_dimension,
    )
    page_models = [build_page_model(prepared_page, subject=subject, ocr_mode=ocr_mode) for prepared_page in prepared_pages]
    return prepared_pages, page_models


def build_problem_entries(
    prepared_pages: list[PreparedPage],
    pages: list[PageModel],
    output_dir: Path,
    template: LayoutTemplate,
) -> list[ProblemEntry]:
    crop_dir = output_dir / "problem_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    prepared_by_page_id = {page.page_id: page for page in prepared_pages}
    entries: list[ProblemEntry] = []

    for page in pages:
        prepared_page = prepared_by_page_id.get(page.page_id)
        if prepared_page is None:
            continue
        block_by_id = {block.block_id: block for block in page.blocks}

        for index, problem in enumerate(page.problems):
            problem_block_ids = iter_problem_block_ids(page, problem)
            blocks = [block_by_id[block_id] for block_id in problem_block_ids if block_id in block_by_id]
            boxes = [block.bbox for block in blocks]
            if not boxes:
                boxes = [Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))]
            merged_box = merge_boxes(boxes, page_width=page.width_px, page_height=page.height_px)
            if merged_box.area < float(page.width_px * page.height_px) * MIN_PROBLEM_AREA_RATIO:
                merged_box = Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))
                blocks = list(page.sorted_blocks())

            crop = prepared_page.image.crop(
                (
                    int(merged_box.left),
                    int(merged_box.top),
                    int(merged_box.right),
                    int(merged_box.bottom),
                )
            )
            crop_path = crop_dir / f"{problem.unit_id}.png"
            crop.save(crop_path)
            reading_heavy = problem.subject in {Subject.KOREAN, Subject.ENGLISH}
            entries.append(
                ProblemEntry(
                    problem_id=problem.unit_id,
                    title=problem.title or f"{page.page_id} problem {index + 1}",
                    subject=problem.subject,
                    source_page_id=page.page_id,
                    source_path=prepared_page.source_path,
                    prepared_page=prepared_page,
                    bounds=merged_box,
                    crop_path=crop_path,
                    blocks=sorted(blocks, key=lambda block: (block.reading_order, block.bbox.top, block.bbox.left)),
                    actual_height_pages=estimate_height_pages(crop.size, template),
                    overflow_allowed=reading_heavy,
                    reading_heavy=reading_heavy,
                )
            )

    return entries


def normalize_text_payload(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned)


def choose_block_record_mode(block: ContentBlock, *, text_confidence_threshold: float) -> str:
    if block.block_type in IMAGE_ONLY_BLOCK_TYPES:
        return "image"
    text = normalize_text_payload(block.text)
    if not text:
        return "image"
    if block.block_type not in TEXT_ELIGIBLE_BLOCK_TYPES:
        return "image"
    confidence = block.confidence if block.confidence is not None else 0.0
    if confidence < text_confidence_threshold:
        return "image"
    return "text"


def resolve_font_size(block: ContentBlock, scale: float) -> int:
    base_size = block.style.font_size if block.style and block.style.font_size else max(14.0, block.bbox.height * 0.32)
    scaled = base_size * max(scale, 0.25) * 0.9
    return int(max(10, min(40, round(scaled))))


def placement_inputs(problem_entries: list[ProblemEntry]) -> list[ProblemLayoutInput]:
    return [
        ProblemLayoutInput(
            problem_id=entry.problem_id,
            subject=entry.subject,
            actual_content_height_pages=entry.actual_height_pages,
            overflow_allowed=entry.overflow_allowed,
            reading_heavy=entry.reading_heavy,
            metadata={
                "title": entry.title,
                "crop_path": str(entry.crop_path),
                "source_page_id": entry.source_page_id,
                "source_path": entry.source_path,
                "bbox": {
                    "left": entry.bounds.left,
                    "top": entry.bounds.top,
                    "width": entry.bounds.width,
                    "height": entry.bounds.height,
                },
            },
        )
        for entry in problem_entries
    ]


def build_image_only_records(problem_entries: list[ProblemEntry], template: LayoutTemplate) -> tuple[list[bytes], list[dict[str, object]]]:
    placements = place_problems(placement_inputs(problem_entries), template=template)
    available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    for record_id, placement in enumerate(placements):
        crop_path = Path(str(placement.metadata["crop_path"]))
        image_bytes = crop_path.read_bytes()
        preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768), quality=88)
        height_px = placement.actual_content_height_pages * CANVAS_WIDTH
        y_px = placement.start_y_pages * CANVAS_WIDTH + TOP_PADDING_PX
        records.append(
            build_image_record(
                ImageRecordSpec(
                    record_id=record_id,
                    image_primary=image_bytes,
                    image_secondary=preview_bytes,
                    x=normalize_x_px(LEFT_MARGIN_PX),
                    y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                    width_hint=normalize_width_px(available_width_px),
                    height_hint=normalize_height_px(height_px, page_count_hint=template.board_page_count),
                )
            )
        )
        placement_summaries.append(
            {
                "problem_id": placement.problem_id,
                "title": placement.metadata["title"],
                "subject": str(placement.subject),
                "crop_path": str(crop_path),
                "source_page_id": placement.metadata["source_page_id"],
                "source_path": placement.metadata["source_path"],
                "start_y_pages": placement.start_y_pages,
                "actual_content_height_pages": placement.actual_content_height_pages,
                "actual_bottom_y_pages": placement.actual_bottom_y_pages,
                "snapped_next_start_y_pages": placement.snapped_next_start_y_pages,
                "overflow_allowed": placement.overflow_allowed,
                "overflow_amount_pages": placement.overflow_amount_pages,
                "overflow_violation": placement.overflow_violation,
                "slot_span_count": placement.slot_span_count,
                "bbox": placement.metadata["bbox"],
                "record_mode": "image-only",
                "text_record_count": 0,
                "image_record_count": 1,
            }
        )

    return records, placement_summaries


def build_mixed_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    output_dir: Path,
    text_confidence_threshold: float,
) -> tuple[list[bytes], list[dict[str, object]]]:
    placements = place_problems(placement_inputs(problem_entries), template=template)
    entries_by_problem_id = {entry.problem_id: entry for entry in problem_entries}
    available_width_px = CANVAS_HEIGHT * template.fixed_left_zone_ratio - LEFT_MARGIN_PX - RIGHT_PADDING_PX
    block_crop_dir = output_dir / "block_crops"
    block_crop_dir.mkdir(parents=True, exist_ok=True)

    records: list[bytes] = []
    placement_summaries: list[dict[str, object]] = []
    next_record_id = 0

    for placement in placements:
        entry = entries_by_problem_id[placement.problem_id]
        scale = available_width_px / max(entry.bounds.width, 1.0)
        problem_origin_x_px = LEFT_MARGIN_PX
        problem_origin_y_px = placement.start_y_pages * CANVAS_WIDTH + TOP_PADDING_PX
        block_summaries: list[dict[str, object]] = []
        text_record_count = 0
        image_record_count = 0

        for block in entry.blocks:
            x_px = problem_origin_x_px + max(0.0, block.bbox.left - entry.bounds.left) * scale
            y_px = problem_origin_y_px + max(0.0, block.bbox.top - entry.bounds.top) * scale
            width_px = max(40.0, min(available_width_px, block.bbox.width * scale))
            height_px = max(22.0, block.bbox.height * scale)
            record_mode = choose_block_record_mode(block, text_confidence_threshold=text_confidence_threshold)

            if record_mode == "text":
                text_payload = normalize_text_payload(block.text)
                records.append(
                    build_text_record(
                        TextRecordSpec(
                            record_id=next_record_id,
                            text=text_payload,
                            x=normalize_x_px(x_px),
                            y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                            width_hint=normalize_width_px(width_px),
                            font_size=resolve_font_size(block, scale),
                        )
                    )
                )
                text_record_count += 1
            else:
                crop = entry.prepared_page.image.crop(
                    (
                        int(block.bbox.left),
                        int(block.bbox.top),
                        int(block.bbox.right),
                        int(block.bbox.bottom),
                    )
                )
                crop_path = block_crop_dir / f"{entry.problem_id}_{block.block_id}.png"
                crop.save(crop_path)
                image_bytes = crop_path.read_bytes()
                preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768), format_hint="PNG", quality=88)
                records.append(
                    build_image_record(
                        ImageRecordSpec(
                            record_id=next_record_id,
                            image_primary=image_bytes,
                            image_secondary=preview_bytes,
                            x=normalize_x_px(x_px),
                            y=normalize_y_px(y_px, page_count_hint=template.board_page_count),
                            width_hint=normalize_width_px(width_px),
                            height_hint=normalize_height_px(height_px, page_count_hint=template.board_page_count),
                        )
                    )
                )
                image_record_count += 1

            block_summaries.append(
                {
                    "block_id": block.block_id,
                    "block_type": str(block.block_type),
                    "record_mode": record_mode,
                    "text_present": bool(normalize_text_payload(block.text)),
                    "confidence": block.confidence,
                    "bbox": {
                        "left": block.bbox.left,
                        "top": block.bbox.top,
                        "width": block.bbox.width,
                        "height": block.bbox.height,
                    },
                }
            )
            next_record_id += 1

        if not block_summaries:
            image_bytes = entry.crop_path.read_bytes()
            preview_bytes = build_preview_image_bytes(image_bytes, max_size=(768, 768), quality=88)
            records.append(
                build_image_record(
                    ImageRecordSpec(
                        record_id=next_record_id,
                        image_primary=image_bytes,
                        image_secondary=preview_bytes,
                        x=normalize_x_px(LEFT_MARGIN_PX),
                        y=normalize_y_px(problem_origin_y_px, page_count_hint=template.board_page_count),
                        width_hint=normalize_width_px(available_width_px),
                        height_hint=normalize_height_px(
                            placement.actual_content_height_pages * CANVAS_WIDTH,
                            page_count_hint=template.board_page_count,
                        ),
                    )
                )
            )
            image_record_count += 1
            next_record_id += 1

        placement_summaries.append(
            {
                "problem_id": placement.problem_id,
                "title": entry.title,
                "subject": str(entry.subject),
                "crop_path": str(entry.crop_path),
                "source_page_id": entry.source_page_id,
                "source_path": entry.source_path,
                "start_y_pages": placement.start_y_pages,
                "actual_content_height_pages": placement.actual_content_height_pages,
                "actual_bottom_y_pages": placement.actual_bottom_y_pages,
                "snapped_next_start_y_pages": placement.snapped_next_start_y_pages,
                "overflow_allowed": placement.overflow_allowed,
                "overflow_amount_pages": placement.overflow_amount_pages,
                "overflow_violation": placement.overflow_violation,
                "slot_span_count": placement.slot_span_count,
                "bbox": {
                    "left": entry.bounds.left,
                    "top": entry.bounds.top,
                    "width": entry.bounds.width,
                    "height": entry.bounds.height,
                },
                "record_mode": "mixed",
                "text_record_count": text_record_count,
                "image_record_count": image_record_count,
                "blocks": block_summaries,
            }
        )

    return records, placement_summaries


def build_records(
    problem_entries: list[ProblemEntry],
    template: LayoutTemplate,
    *,
    record_mode: str,
    output_dir: Path,
    text_confidence_threshold: float,
) -> tuple[list[bytes], list[dict[str, object]], int]:
    if record_mode == "image-only":
        return (*build_image_only_records(problem_entries, template), 4)

    records, placement_summaries = build_mixed_records(
        problem_entries,
        template,
        output_dir=output_dir,
        text_confidence_threshold=text_confidence_threshold,
    )
    header_flag = 4 if any(item["image_record_count"] for item in placement_summaries) else 3
    return records, placement_summaries, header_flag


def write_ui_prototype_data(output_path: Path, placements: list[dict[str, object]]) -> None:
    payload = {
        "problems": [
            {
                "id": item["problem_id"],
                "title": item["title"],
                "subject": item["subject"],
                "imagePath": Path(item["crop_path"]).resolve().as_uri(),
                "actualHeightPages": item["actual_content_height_pages"],
                "overflowAllowed": item["overflow_allowed"],
                "readingHeavy": item["overflow_allowed"],
            }
            for item in placements
        ]
    }
    output_path.write_text(
        "window.PROTOTYPE_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )


def build_placement_summary(placements: list[dict[str, object]]) -> dict[str, object]:
    if not placements:
        return {
            "problem_count": 0,
            "overflow_count": 0,
            "overflow_violation_count": 0,
            "max_bottom_y_pages": 0.0,
            "text_record_count": 0,
            "image_record_count": 0,
        }
    return {
        "problem_count": len(placements),
        "overflow_count": sum(1 for item in placements if float(item["overflow_amount_pages"]) > 0),
        "overflow_violation_count": sum(1 for item in placements if bool(item["overflow_violation"])),
        "max_bottom_y_pages": max(float(item["actual_bottom_y_pages"]) for item in placements),
        "text_record_count": sum(int(item.get("text_record_count", 0)) for item in placements),
        "image_record_count": sum(int(item.get("image_record_count", 0)) for item in placements),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a problem-board EDB from a source image or PDF.")
    parser.add_argument("source", help="Path to a PDF or image source")
    parser.add_argument("--output-dir", default="mvp_export", help="Directory for pipeline artifacts and EDB output")
    parser.add_argument("--subject", default="unknown", help="Subject hint: math, science, korean, english, social, unknown")
    parser.add_argument("--ocr", default="noop", help="OCR backend: noop, auto, paddleocr, tesseract")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="PDF render DPI")
    parser.add_argument("--detect-perspective", action="store_true", help="Try perspective correction for photographed sources")
    parser.add_argument("--skip-deskew", action="store_true", help="Disable deskew")
    parser.add_argument("--skip-crop", action="store_true", help="Disable margin crop")
    parser.add_argument("--max-dimension", type=int, default=None, help="Resize long edge to this many pixels")
    parser.add_argument("--template-name", default="academy-default", help="Layout template name")
    parser.add_argument("--board-pages", type=int, default=50, help="Board page count hint")
    parser.add_argument("--slot-height", type=float, default=1.2, help="Base slot height in board pages")
    parser.add_argument("--record-mode", choices=("mixed", "image-only"), default="mixed", help="Record generation strategy")
    parser.add_argument("--text-confidence-threshold", type=float, default=0.78, help="Minimum OCR confidence for text records in mixed mode")
    parser.add_argument("--prototype-data-out", default="ui_prototype\\prototype_data.js", help="Path to write UI prototype data JS")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    subject = resolve_subject(args.subject)
    prepared_pages, pages = build_pages(
        args.source,
        subject=subject,
        ocr_mode=args.ocr,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        deskew=not args.skip_deskew,
        crop_margins=not args.skip_crop,
        max_dimension=args.max_dimension,
    )
    save_pages_json(pages, output_dir / "pages.json")

    template = LayoutTemplate(
        name=args.template_name,
        board_page_count=args.board_pages,
        base_slot_height_pages=args.slot_height,
    )
    problem_entries = build_problem_entries(prepared_pages, pages, output_dir, template)
    records, placements, header_flag = build_records(
        problem_entries,
        template,
        record_mode=args.record_mode,
        output_dir=output_dir,
        text_confidence_threshold=args.text_confidence_threshold,
    )

    edb_path = output_dir / f"{Path(args.source).stem}.edb"
    write_edb(
        edb_path,
        build_edb(records, header_flag=header_flag, page_count_hint=template.board_page_count),
    )

    summary = {
        "source": str(args.source),
        "output_dir": str(output_dir),
        "edb_path": str(edb_path),
        "pages_json_path": str(output_dir / "pages.json"),
        "problem_crop_dir": str(output_dir / "problem_crops"),
        "block_crop_dir": str(output_dir / "block_crops"),
        "record_count": len(records),
        "record_mode": args.record_mode,
        "header_flag": header_flag,
        "text_confidence_threshold": args.text_confidence_threshold,
        "placement_summary": build_placement_summary(placements),
        "placements": placements,
        "ocr_backend_requested": args.ocr,
    }
    summary_path = output_dir / "board_run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    prototype_path = Path(args.prototype_data_out)
    prototype_path.parent.mkdir(parents=True, exist_ok=True)
    write_ui_prototype_data(prototype_path, placements)

    print(
        json.dumps(
            {
                "edb_path": str(edb_path),
                "pages_json_path": str(output_dir / "pages.json"),
                "board_run_summary_path": str(summary_path),
                "ui_prototype_data_path": str(prototype_path),
                "problem_count": len(placements),
                "record_mode": args.record_mode,
                "text_record_count": summary["placement_summary"]["text_record_count"],
                "image_record_count": summary["placement_summary"]["image_record_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
