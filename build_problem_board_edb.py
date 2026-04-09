#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from build_structured_page_json import process_source
from edb_builder import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ImageRecordSpec,
    build_edb,
    build_image_record,
    build_preview_image_bytes,
    normalize_height_px,
    normalize_width_px,
    normalize_x_px,
    normalize_y_px,
    write_edb,
)
from layout_template_schema import LayoutTemplate, ProblemLayoutInput
from placement_engine import place_problems
from structured_schema import Box, PageModel, Subject


LEFT_MARGIN_PX = 84.0
TOP_PADDING_PX = 20.0
RIGHT_PADDING_PX = 54.0
PROBLEM_PADDING_PX = 18.0
MIN_HEIGHT_PAGES = 0.72
MAX_HEIGHT_PAGES = 4.8
MIN_PROBLEM_AREA_RATIO = 0.12


def resolve_subject(name: str | None) -> Subject:
    if not name:
        return Subject.UNKNOWN
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def iter_problem_block_ids(page: PageModel, problem) -> list[str]:
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


def build_problem_entries(pages: list[PageModel], output_dir: Path, template: LayoutTemplate) -> list[dict[str, object]]:
    crop_dir = output_dir / "problem_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []

    for page in pages:
        source_path = page.source_path or ""
        image = Image.open(source_path).convert("RGB")
        block_by_id = {block.block_id: block for block in page.blocks}

        for index, problem in enumerate(page.problems):
            problem_block_ids = iter_problem_block_ids(page, problem)
            boxes = [block_by_id[block_id].bbox for block_id in problem_block_ids if block_id in block_by_id]
            if not boxes:
                boxes = [Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))]
            merged_box = merge_boxes(boxes, page_width=page.width_px, page_height=page.height_px)
            if merged_box.area < float(page.width_px * page.height_px) * MIN_PROBLEM_AREA_RATIO:
                merged_box = Box(left=0.0, top=0.0, width=float(page.width_px), height=float(page.height_px))
            crop = image.crop(
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
                {
                    "problem_id": problem.unit_id,
                    "title": problem.title or f"{page.page_id} problem {index + 1}",
                    "subject": str(problem.subject),
                    "crop_path": str(crop_path),
                    "source_page_id": page.page_id,
                    "source_path": source_path,
                    "actual_height_pages": estimate_height_pages(crop.size, template),
                    "overflow_allowed": reading_heavy,
                    "reading_heavy": reading_heavy,
                    "bbox": {
                        "left": merged_box.left,
                        "top": merged_box.top,
                        "width": merged_box.width,
                        "height": merged_box.height,
                    },
                }
            )

    return entries


def build_records(problem_entries: list[dict[str, object]], template: LayoutTemplate) -> tuple[list[bytes], list[dict[str, object]]]:
    layout_inputs = [
        ProblemLayoutInput(
            problem_id=str(entry["problem_id"]),
            subject=resolve_subject(str(entry["subject"])),
            actual_content_height_pages=float(entry["actual_height_pages"]),
            overflow_allowed=bool(entry["overflow_allowed"]),
            reading_heavy=bool(entry["reading_heavy"]),
            metadata={
                "title": entry["title"],
                "crop_path": entry["crop_path"],
                "source_page_id": entry["source_page_id"],
                "source_path": entry["source_path"],
                "bbox": entry["bbox"],
            },
        )
        for entry in problem_entries
    ]
    placements = place_problems(layout_inputs, template=template)
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
            }
        )

    return records, placement_summaries


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
        }
    return {
        "problem_count": len(placements),
        "overflow_count": sum(1 for item in placements if float(item["overflow_amount_pages"]) > 0),
        "overflow_violation_count": sum(1 for item in placements if bool(item["overflow_violation"])),
        "max_bottom_y_pages": max(float(item["actual_bottom_y_pages"]) for item in placements),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an image-based EDB board from a source image or PDF.")
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
    parser.add_argument("--prototype-data-out", default="ui_prototype\\prototype_data.js", help="Path to write UI prototype data JS")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = process_source(
        args.source,
        output_dir,
        subject=resolve_subject(args.subject),
        ocr_mode=args.ocr,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        deskew=not args.skip_deskew,
        crop_margins=not args.skip_crop,
        max_dimension=args.max_dimension,
    )
    template = LayoutTemplate(
        name=args.template_name,
        board_page_count=args.board_pages,
        base_slot_height_pages=args.slot_height,
    )
    problem_entries = build_problem_entries(pages, output_dir, template)
    records, placements = build_records(problem_entries, template)

    edb_path = output_dir / f"{Path(args.source).stem}.edb"
    write_edb(
        edb_path,
        build_edb(records, header_flag=4, page_count_hint=template.board_page_count),
    )

    summary = {
        "source": str(args.source),
        "output_dir": str(output_dir),
        "edb_path": str(edb_path),
        "pages_json_path": str(output_dir / "pages.json"),
        "problem_crop_dir": str(output_dir / "problem_crops"),
        "record_count": len(records),
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
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
