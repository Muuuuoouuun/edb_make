#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageDraw

from build_structured_page_json import build_page_model
from edb_builder import ImageRecordSpec, build_edb, build_image_record, write_edb
from layout_template_schema import build_default_template
from placement_engine import build_export_plan
from preprocess import prepare_source_pages
from structured_schema import PageModel, Subject, save_pages_json


def _resolve_subject(name: str | None) -> Subject:
    if not name:
        return Subject.UNKNOWN
    try:
        return Subject(name.lower())
    except ValueError:
        return Subject.UNKNOWN


def _encode_jpeg(image: Image.Image, quality: int = 92) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _make_thumbnail(image: Image.Image, max_size: tuple[int, int] = (640, 640)) -> Image.Image:
    thumb = image.copy()
    thumb.thumbnail(max_size, Image.Resampling.LANCZOS)
    return thumb


def render_board_page(page_model: PageModel, prepared_image: Image.Image, left_zone_ratio: float) -> Image.Image:
    canvas_width = 2160
    canvas_height = 3840
    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
    draw = ImageDraw.Draw(canvas)

    divider_x = int(round(canvas_width * left_zone_ratio))
    margin = 96
    max_width = max(1, divider_x - margin * 2)
    max_height = max(1, canvas_height - margin * 2)

    scaled = prepared_image.convert("RGB")
    scale = min(max_width / scaled.width, max_height / scaled.height)
    target_size = (max(1, int(round(scaled.width * scale))), max(1, int(round(scaled.height * scale))))
    scaled = scaled.resize(target_size, Image.Resampling.LANCZOS)

    canvas.paste(scaled, (margin, margin))
    draw.line([(divider_x, 0), (divider_x, canvas_height)], fill=(210, 210, 210), width=4)
    draw.text((24, 24), page_model.page_id, fill=(70, 70, 70))
    return canvas


def export_board_edb(board_images: list[Image.Image], output_path: Path, template_name: str) -> None:
    records: list[bytes] = []
    for index, image in enumerate(board_images):
        primary = _encode_jpeg(image, quality=92)
        secondary = _encode_jpeg(_make_thumbnail(image), quality=86)
        records.append(
            build_image_record(
                ImageRecordSpec(
                    record_id=index,
                    image_primary=primary,
                    image_secondary=secondary,
                    x=0.0,
                    y=round(index * 0.024, 6),
                    width_hint=0.52,
                    height_hint=0.024,
                )
            )
        )
    payload = build_edb(records, header_flag=4, version="6.0.5.3911")
    write_edb(output_path, payload)


def page_model_to_board_plan_dict(page_models: list[PageModel], export_plan) -> dict[str, object]:
    placements = []
    for placement in export_plan.placements:
        placements.append(
            {
                "problem_id": placement.problem_id,
                "subject": placement.subject,
                "start_y_pages": placement.start_y_pages,
                "nominal_slot_height_pages": placement.nominal_slot_height_pages,
                "actual_content_height_pages": placement.actual_content_height_pages,
                "actual_bottom_y_pages": placement.actual_bottom_y_pages,
                "snapped_next_start_y_pages": placement.snapped_next_start_y_pages,
                "overflow_allowed": placement.overflow_allowed,
                "overflow_amount_pages": placement.overflow_amount_pages,
                "overflow_violation": placement.overflow_violation,
                "slot_span_count": placement.slot_span_count,
                "board_capacity_exceeded": placement.board_capacity_exceeded,
                "metadata": placement.metadata,
            }
        )
    return {
        "template": {
            "name": export_plan.template.name,
            "board_page_count": export_plan.template.board_page_count,
            "base_slot_height_pages": export_plan.template.base_slot_height_pages,
            "fixed_left_zone_ratio": export_plan.template.fixed_left_zone_ratio,
            "preserve_right_writing_zone": export_plan.template.preserve_right_writing_zone,
            "default_overflow_subjects": [subject.value for subject in export_plan.template.default_overflow_subjects],
            "metadata": export_plan.template.metadata,
        },
        "placements": placements,
        "page_count": len(page_models),
        "problem_count": len(placements),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the first MVP export for ClassIn EDB.")
    parser.add_argument("source", type=Path, help="Input image or PDF")
    parser.add_argument("--output-dir", type=Path, default=Path("mvp_export"), help="Output directory")
    parser.add_argument("--subject", default="unknown", help="Subject override: math/science/korean/english/social")
    parser.add_argument("--ocr", default="auto", help="OCR backend: auto/paddle/tesseract/none")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="PDF render DPI")
    parser.add_argument("--detect-perspective", action="store_true", help="Try perspective correction for photographed sources")
    parser.add_argument("--skip-deskew", action="store_true", help="Disable deskew")
    parser.add_argument("--skip-crop", action="store_true", help="Disable margin crop")
    parser.add_argument("--max-dimension", type=int, default=None, help="Resize long edge to this many pixels")
    parser.add_argument("--export-edb", action="store_true", help="Also export a board-image .edb")
    parser.add_argument("--edb-name", default="mvp_board.edb", help="Output .edb filename")
    args = parser.parse_args()

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "board_pages").mkdir(parents=True, exist_ok=True)

    subject = _resolve_subject(args.subject)
    prepared_pages = prepare_source_pages(
        args.source,
        pdf_dpi=args.pdf_dpi,
        detect_perspective=args.detect_perspective,
        deskew=not args.skip_deskew,
        crop_margins=not args.skip_crop,
        max_dimension=args.max_dimension,
    )

    page_models: list[PageModel] = [
        build_page_model(prepared_page, subject=subject, ocr_mode=args.ocr)
        for prepared_page in prepared_pages
    ]
    save_pages_json(page_models, out_dir / "pages.json")

    export_plan = build_export_plan(page_models, template=build_default_template())
    board_plan_dict = page_model_to_board_plan_dict(page_models, export_plan)
    (out_dir / "placements.json").write_text(json.dumps(board_plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    rendered_board_paths: list[str] = []
    board_images: list[Image.Image] = []
    for index, prepared_page in enumerate(prepared_pages):
        board_image = render_board_page(page_models[index], prepared_page.image, export_plan.template.fixed_left_zone_ratio)
        board_path = out_dir / "board_pages" / f"{prepared_page.page_id}.png"
        board_image.save(board_path)
        rendered_board_paths.append(str(board_path))
        board_images.append(board_image)

    board_plan_dict["rendered_page_paths"] = rendered_board_paths
    (out_dir / "placements.json").write_text(json.dumps(board_plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.export_edb:
        edb_path = out_dir / args.edb_name
        export_board_edb(board_images, edb_path, export_plan.template.name)
        board_plan_dict["edb_path"] = str(edb_path)
        (out_dir / "placements.json").write_text(json.dumps(board_plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote pages.json, placements.json, and {len(board_images)} board page renders -> {out_dir}")
    if args.export_edb:
        print(f"exported EDB -> {out_dir / args.edb_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
